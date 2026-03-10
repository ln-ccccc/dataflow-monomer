import os
import time
import logging
import json
import re
import uuid
import tempfile
import csv
from mimetypes import guess_type
from pathlib import Path
from typing import Any, List, Optional, Union, Dict, Literal
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm
from pydantic import BaseModel
import pandas as pd

# --- Dependency: Google Vertex AI SDK ---
try:
    import vertexai
    from vertexai.generative_models import (
        GenerativeModel,
        Part,
        Tool,
        FunctionDeclaration,
        GenerationConfig,
        GenerationResponse,
    )
    from google.api_core import exceptions as google_exceptions
    from google.cloud import bigquery
    
    # For batch processing
    from google import genai
    from google.genai.types import CreateBatchJobConfig

except ImportError:
    raise ImportError(
        "Google Cloud AI Platform library not found or is outdated. "
        "Please run 'pip install \"google-cloud-aiplatform>=1.55\" pydantic tqdm google-cloud-bigquery google-genai'"
    )

from dataflow.core import LLMServingABC

# --- Helpers ---
_BQ_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")

def _normalize_bq_location(location: Optional[str]) -> Optional[str]:
    if not location:
        return None
    loc = str(location).strip()
    if not loc or loc.lower() == "global":
        return None
    return loc

def _validate_bq_id(kind: str, value: str) -> str:
    if not value or not _BQ_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid BigQuery {kind} id: {value!r}")
    return value

def _parse_bq_uri(uri: str) -> tuple[str, str, str]:
    if not uri or not isinstance(uri, str):
        raise ValueError(f"Invalid BigQuery URI: {uri!r}")
    m = re.fullmatch(r"bq://([A-Za-z0-9\-:\.]+)/([A-Za-z_][A-Za-z0-9_]{0,1023})/([A-Za-z_][A-Za-z0-9_]{0,1023})", uri.strip())
    if not m:
        m = re.fullmatch(r"bq://([A-Za-z0-9\-:\.]+)\.([A-Za-z_][A-Za-z0-9_]{0,1023})\.([A-Za-z_][A-Za-z0-9_]{0,1023})", uri.strip())
    if not m:
        raise ValueError(f"Invalid BigQuery URI: {uri!r}")
    return m.group(1), m.group(2), m.group(3)

# --- Singleton Client Registry ---
class ClientRegistry:
    _bq_clients: Dict[tuple[Optional[str], Optional[str]], bigquery.Client] = {}
    _genai_clients: Dict[tuple[Optional[str], str], genai.Client] = {}
    _vertexai_initialized: bool = False

    @classmethod
    def get_bq_client(cls, project: Optional[str], location: Optional[str]) -> bigquery.Client:
        loc = _normalize_bq_location(location)
        key = (project, loc)
        client = cls._bq_clients.get(key)
        if client is None:
            if loc:
                client = bigquery.Client(project=project, location=loc)
            else:
                client = bigquery.Client(project=project)
            cls._bq_clients[key] = client
        return client

    @classmethod
    def get_genai_client(cls, project: Optional[str], location: str) -> genai.Client:
        key = (project, location)
        client = cls._genai_clients.get(key)
        if client is None:
            client = genai.Client(vertexai=True, project=project, location=location)
            cls._genai_clients[key] = client
        return client

    @classmethod
    def init_vertexai(cls, project: str, location: str):
        if not cls._vertexai_initialized:
            vertexai.init(project=project, location=location)
            cls._vertexai_initialized = True

# --- BigQuery Client Wrapper ---
class BigQueryClient:
    def __init__(self, project: Optional[str], location: Optional[str]):
        self.project = project
        self.location = _normalize_bq_location(location)
        self.logger = logging.getLogger(__name__)
        self._client = None

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            self._client = ClientRegistry.get_bq_client(self.project, self.location)
        return self._client

    def create_dataset_if_not_exists(self, dataset_name: str) -> str:
        """Creates a dataset if it doesn't exist. Returns full dataset ID."""
        _validate_bq_id("dataset", dataset_name)
        dataset_id = f"{self.project}.{dataset_name}"
        try:
            self.client.get_dataset(dataset_id)
            self.logger.info(f"Dataset '{dataset_id}' already exists.")
        except google_exceptions.NotFound:
            dataset = bigquery.Dataset(dataset_id)
            if self.location:
                dataset.location = self.location
            self.client.create_dataset(dataset, timeout=30)
            self.logger.info(f"Created dataset '{dataset_id}'.")
        return dataset_id

    def upload_csv_to_table(self, csv_path: str, dataset_name: str, table_name: str) -> str:
        """
        Uploads a CSV file to a BigQuery table.
        The CSV is expected to have 'custom_id' and 'request' columns.
        """
        if not self.project:
            raise ValueError("BigQuery project is not set.")
        _validate_bq_id("dataset", dataset_name)
        _validate_bq_id("table", table_name)
        self.create_dataset_if_not_exists(dataset_name)
        
        table_id = f"{self.project}.{dataset_name}.{table_name}"
        
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            autodetect=False, 
            skip_leading_rows=1,
            allow_quoted_newlines=True,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=[
                bigquery.SchemaField("custom_id", "STRING"),
                bigquery.SchemaField("request", "JSON"),
            ]
        )
        
        try:
            with open(csv_path, "rb") as source_file:
                job = self.client.load_table_from_file(source_file, table_id, job_config=job_config)
            job.result()
        except Exception as e:
            details = getattr(e, "errors", None)
            if details:
                self.logger.error(f"BigQuery load failed for {table_id}. errors={details}")
            else:
                self.logger.error(f"BigQuery load failed for {table_id}: {e}")
            raise
        
        self.logger.info(f"Uploaded {csv_path} to {table_id}. Loaded {job.output_rows} rows.")
        return f"bq://{table_id}"

    def query_to_dataframe(self, query: str) -> pd.DataFrame:
        """Executes a query and returns the result as a DataFrame."""
        return self.client.query(query).to_dataframe()


# --- BigQuery Batch Runner ---
class BigQueryBatchRunner:
    def __init__(self, project: Optional[str], location: str, bq_client: BigQueryClient):
        self.project = project
        self.location = location
        self.bq_client = bq_client
        self.logger = logging.getLogger(__name__)
        self._genai_client = None

    @property
    def genai_client(self) -> genai.Client:
        if self._genai_client is None:
            self._genai_client = ClientRegistry.get_genai_client(self.project, self.location)
        return self._genai_client

    def generate_batch_csv(
        self,
        user_inputs: List[str],
        system_prompt: str,
        csv_filename: str,
        response_schema: Optional[Union[type[BaseModel], Dict]] = None,
        max_tokens: int = 8192,
        temperature: float = 0.0,
        custom_ids: Optional[List[str]] = None
    ) -> str:
        """
        Generates a CSV file formatted for Vertex AI Batch Prediction.
        Uses camelCase for JSON keys (systemInstruction, generationConfig).
        """
        os.makedirs(os.path.dirname(csv_filename) or ".", exist_ok=True)
        with open(csv_filename, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL, doublequote=True, lineterminator="\n")
            writer.writerow(["custom_id", "request"])

            for i, prompt in enumerate(user_inputs):
                # 1. Construct Request JSON (camelCase)
                request_json = {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": str(prompt)}]
                        }
                    ],
                    "systemInstruction": {
                        "parts": [{"text": str(system_prompt)}]
                    },
                    "generationConfig": {
                        "temperature": temperature,
                        "maxOutputTokens": max_tokens,
                        "responseMimeType": "text/plain"
                    }
                }

                # Handle Schema
                if response_schema:
                    request_json["generationConfig"]["responseMimeType"] = "application/json"
                    if hasattr(response_schema, "model_json_schema"):
                        request_json["generationConfig"]["responseSchema"] = response_schema.model_json_schema()
                    elif isinstance(response_schema, dict):
                        request_json["generationConfig"]["responseSchema"] = response_schema

                # 2. Determine Custom ID
                cid = custom_ids[i] if custom_ids and i < len(custom_ids) else f"req-{i}-{uuid.uuid4().hex[:6]}"

                request_str = json.dumps(
                    request_json,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                writer.writerow([cid, request_str])

        self.logger.info(f"Generated batch input CSV at {csv_filename} with {len(user_inputs)} rows.")
        return csv_filename

    def submit_batch_job(self, model_name: str, input_bq_uri: str, output_uri_prefix: str) -> str:
        """Submits a batch job to Vertex AI with exponential backoff for rate limits."""
        import time
        import random
        from google.api_core import exceptions as google_exceptions
        
        max_retries = 100
        base_delay = 5  # seconds
        
        for attempt in range(max_retries):
            try:
                batch_job = self.genai_client.batches.create(
                    model=model_name,
                    src=input_bq_uri,
                    config=CreateBatchJobConfig(dest=output_uri_prefix)
                )
                self.logger.info(f"Batch job submitted: {batch_job.name}")
                return batch_job.name
            except Exception as e:
                # Check for rate limit or connection errors (503, 429)
                error_str = str(e)
                if isinstance(e, google_exceptions.ServiceUnavailable) or \
                   isinstance(e, google_exceptions.TooManyRequests) or \
                   "503" in error_str or "429" in error_str or "Too many open connections" in error_str:
                    if attempt < max_retries - 1:
                        delay = (base_delay * (2 ** attempt)) + random.uniform(0, 5)
                        self.logger.warning(f"Failed to submit batch job (Attempt {attempt+1}/{max_retries}): {e}. Retrying in {delay:.2f}s...")
                        time.sleep(delay)
                        continue
                self.logger.error(f"Failed to submit batch job: {e}")
                raise

    def wait_for_job(self, job_name: str, poll_interval: int = 60) -> str:
        """Waits for the batch job to complete. Returns the output table URI."""
        self.logger.info(f"Waiting for batch job {job_name}...")
        
        # 记录连续失败次数，避免无限循环
        consecutive_failures = 0
        max_consecutive_failures = 20
        
        # 记录上一次打印状态的时间，避免日志过于频繁（每 10 分钟打印一次状态）
        last_status_log_time = 0
        status_log_interval = 600 
        
        while True:
            try:
                job = self.genai_client.batches.get(name=job_name)
                consecutive_failures = 0
                
                if job.state == "JOB_STATE_SUCCEEDED":
                    self.logger.info(f"Batch job {job_name} succeeded.")
                    return job.dest.bigquery_uri 
                elif job.state in ["JOB_STATE_FAILED", "JOB_STATE_CANCELLED"]:
                    raise RuntimeError(f"Batch job failed/cancelled. State: {job.state}, Error: {job.error}")
                
                # 只有间隔达到 status_log_interval 时才打印日志
                current_time = time.time()
                if current_time - last_status_log_time >= status_log_interval:
                    self.logger.info(f"Batch job {job_name} state: {job.state}. Still waiting...")
                    last_status_log_time = current_time
                
                time.sleep(poll_interval)
                
            except Exception as e:
                error_str = str(e)
                if "503" in error_str or "429" in error_str or "Connection" in error_str:
                    consecutive_failures += 1
                    wait_time = min(poll_interval * 2, 120) 
                    self.logger.warning(f"Polling failed (Attempt {consecutive_failures}): {e}. Retrying in {wait_time}s...")
                    
                    if consecutive_failures >= max_consecutive_failures:
                        self.logger.error(f"Too many consecutive polling failures ({max_consecutive_failures}). Giving up.")
                        raise
                        
                    time.sleep(wait_time)
                    continue
                else:
                    self.logger.error(f"Unexpected error during polling: {e}")
                    raise

    def retrieve_results(self, output_bq_uri: str) -> Dict[str, str]:
        """
        Retrieves results from the output BigQuery table.
        Parses the 'response' column (JSON) to extract text/function calls.
        Returns a dictionary mapping custom_id to response string.
        """
        project, dataset, table = _parse_bq_uri(output_bq_uri)
        table_id = f"{project}.{dataset}.{table}"
        
        # 增加重试逻辑
        max_retries = 5
        for attempt in range(max_retries):
            try:
                query = f"SELECT custom_id, response FROM `{table_id}`"
                df = self.bq_client.query_to_dataframe(query)
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    self.logger.warning(f"Failed to retrieve results (Attempt {attempt+1}): {e}. Retrying...")
                    time.sleep(10)
                else:
                    self.logger.error(f"Failed to retrieve results after {max_retries} attempts: {e}")
                    raise
        
        result_map = {}
        
        for _, row in df.iterrows():
            cid = row['custom_id']
            raw_response = row['response']
            
            try:
                resp_json = json.loads(raw_response)
                # Parse candidates
                if 'candidates' in resp_json and resp_json['candidates']:
                    candidate = resp_json['candidates'][0]
                    content = candidate.get('content', {})
                    parts = content.get('parts', [])
                    if parts:
                        if 'text' in parts[0]:
                            val = parts[0]['text']
                        elif 'functionCall' in parts[0]:
                            val = json.dumps(parts[0]['functionCall']['args'])
                        else:
                            val = json.dumps(parts[0])
                    else:
                        val = ""
                else:
                    val = f"Error: No candidates. {raw_response}"
            except Exception:
                val = raw_response
            
            result_map[cid] = val
            
        return result_map


# --- Gemini Client Logic (Real-time) ---
class GeminiVertexAIClient:
    def __init__(self, project: Optional[str] = None, location: str = 'us-central1'):
        """Initialize Gemini client for Vertex AI."""
        self.logger = logging.getLogger(__name__)
        
        # Ensure credentials
        if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
             # Fallback or warning
             pass

        vertexai.init(project=project, location=location)

    def generate(
        self,
        system_prompt: str,
        content: Union[str, Path],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_schema: Optional[Union[type[BaseModel], dict]] = None,
        use_function_call: bool = False,
    ) -> GenerationResponse:
        
        model_instance = GenerativeModel(
            model,
            system_instruction=system_prompt
        )

        config_params = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        
        tools = None
        if response_schema:
            if isinstance(response_schema, dict):
                schema_dict = response_schema
            else:
                schema_dict = response_schema.model_json_schema()

            if use_function_call:
                function_declaration = FunctionDeclaration(
                    name="extract_data",
                    description=f"Extracts structured data according to the provided schema.",
                    parameters=schema_dict,
                )
                tools = [Tool(function_declarations=[function_declaration])]
            else:
                config_params["response_mime_type"] = "application/json"
                config_params["response_schema"] = schema_dict

        generation_config = GenerationConfig(**config_params)
        
        # Prepare content
        if isinstance(content, (str, Path)) and os.path.exists(content) and os.path.isfile(content):
            mime_type, _ = guess_type(str(content))
            parts = [Part.from_uri(uri=str(content), mime_type=mime_type or "application/octet-stream")]
        else:
            parts = [Part.from_text(content)]

        response = model_instance.generate_content(
            contents=parts,
            generation_config=generation_config,
            tools=tools,
        )
        return response


# --- Main Serving Class ---
class APIGoogleVertexAIServing(LLMServingABC):
    """
    LLM Serving class for Google's Gemini models via Vertex AI API.
    Supports both real-time and batch processing.
    """
    def __init__(self, 
                 model_name: str = "gemini-2.5-flash",
                 project: Optional[str] = None,
                 location: str = 'us-central1',
                 max_workers: int = 10,
                 max_retries: int = 5,
                 temperature: float = 0.0,
                 max_tokens: int = 4096,
                 use_function_call: bool = True,
                 use_batch: bool = False,
                 batch_wait: bool = True,
                 batch_dataset: str = "dataflow_batch",
                 csv_filename: Optional[str] = None,
                 bq_csv_filename: Optional[str] = None,
                 ):
        self.logger = logging.getLogger(__name__)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        
        self.model_name = model_name
        self.project = project
        self.location = location
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.use_function_call = use_function_call
        self.use_batch = use_batch
        self.batch_wait = batch_wait
        self.batch_dataset = batch_dataset
        self.csv_filename = csv_filename
        self.bq_csv_filename = bq_csv_filename
        
        # Lazy Initialization
        self.client = None
        if not use_batch:
            try:
                self.client = GeminiVertexAIClient(project=project, location=location)
            except Exception as e:
                self.logger.error(f"Failed to initialize GeminiVertexAIClient: {e}")
        
        # Initialize Batch Components (Lazy)
        self.bq_client_wrapper = None
        self.batch_runner = None
        
        if use_batch:
            try:
                self.bq_client_wrapper = BigQueryClient(project=project, location=location)
                self.batch_runner = BigQueryBatchRunner(project=project, location=location, bq_client=self.bq_client_wrapper)
            except Exception as e:
                self.logger.warning(f"Batch processing components could not be initialized: {e}")

    def start_serving(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    def load_model(self, model_name_or_path: str, **kwargs: Any):
        self.model_name = model_name_or_path

    def generate_from_input(
        self, 
        user_inputs: List[str], 
        system_prompt: str = "", 
        json_schema: Optional[Union[type[BaseModel], dict]] = None,
        use_function_call: Optional[bool] = None,
        use_batch: Optional[bool] = None,
        batch_wait: Optional[bool] = None,
        batch_dataset: Optional[str] = None,
        csv_filename: Optional[str] = None,
        bq_csv_filename: Optional[str] = None,
    ) -> Union[List[str], str]:
        
        # Default overrides
        use_batch = use_batch if use_batch is not None else self.use_batch
        batch_wait = batch_wait if batch_wait is not None else self.batch_wait
        batch_dataset = batch_dataset if batch_dataset is not None else self.batch_dataset
        
        if use_batch:
            if not self.batch_runner:
                raise RuntimeError("Batch runner not initialized. Check credentials/project config.")
            return self._generate_with_batch(
                user_inputs, system_prompt, json_schema, 
                batch_wait, batch_dataset, csv_filename, bq_csv_filename
            )
        else:
            return self._generate_with_parallel(
                user_inputs, system_prompt, json_schema, use_function_call
            )

    def _generate_with_batch(
        self,
        user_inputs: List[str],
        system_prompt: str,
        response_schema: Optional[Any],
        wait_for_completion: bool,
        dataset_name: str,
        csv_filename: Optional[str],
        bq_csv_filename: Optional[str]
    ) -> Union[List[str], str]:
        if not self.project:
            raise ValueError("Vertex AI / BigQuery project is not set.")
        _validate_bq_id("dataset", dataset_name)

        # 分批大小（默认 1000，可通过环境变量调节）
        try:
            chunk_size = int(os.getenv("PROPS_BATCH_CHUNK_SIZE") or "1000")
            if chunk_size <= 0:
                chunk_size = 1000
        except Exception:
            chunk_size = 1000

        total = len(user_inputs)
        if total == 0:
            return []

        # 如果无需等待完成且只有一个分片，沿用单 job 返回 job_name 的语义
        single_shot = (total <= chunk_size)

        result_map: Dict[str, str] = {}
        job_names: List[str] = []
        job_infos: List[tuple[str, list[str]]] = []

        for start in range(0, total, chunk_size):
            end = min(start + chunk_size, total)
            chunk = user_inputs[start:end]

            timestamp = int(time.time())
            unique_id = uuid.uuid4().hex[:8]
            chunk_tag = f"{start}_{end}_{unique_id}"
            csv_name = f"batch_input_{chunk_tag}.csv" if not csv_filename else f"{Path(csv_filename).stem}_{chunk_tag}.csv"
            temp_csv_path = os.path.join(tempfile.gettempdir(), csv_name)
            table_name = f"batch_input_{timestamp}_{chunk_tag}"
            _validate_bq_id("table", table_name)

            # 为分片生成全局索引的 custom_id，便于结果合并定位
            custom_ids = [f"req-{i}" for i in range(start, end)]

            # 生成 CSV（流式写，每行一条）
            self.batch_runner.generate_batch_csv(
                user_inputs=chunk,
                system_prompt=system_prompt,
                csv_filename=temp_csv_path,
                response_schema=response_schema,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                custom_ids=custom_ids
            )

            try:
                # 上传到 BigQuery
                input_bq_uri = self.bq_client_wrapper.upload_csv_to_table(
                    csv_path=temp_csv_path,
                    dataset_name=dataset_name,
                    table_name=table_name
                )
                # 提交 Batch Job
                output_uri_prefix = f"bq://{self.project}.{dataset_name}.{table_name}_results"
                job_name = self.batch_runner.submit_batch_job(
                    model_name=self.model_name,
                    input_bq_uri=input_bq_uri,
                    output_uri_prefix=output_uri_prefix
                )
                job_names.append(job_name)
                job_infos.append((job_name, custom_ids))

            finally:
                if os.path.exists(temp_csv_path):
                    try:
                        os.remove(temp_csv_path)
                    except Exception:
                        pass

        if not wait_for_completion:
            return job_names[-1] if job_names else ""

        try:
            workers_env = os.getenv("PROPS_BATCH_RETRIEVAL_WORKERS")
            retrieval_workers = int(workers_env) if workers_env else 3
        except Exception:
            retrieval_workers = 3
        if retrieval_workers <= 0:
            retrieval_workers = 1

        def _wait_and_fetch(job_name: str) -> Dict[str, str]:
            uri = self.batch_runner.wait_for_job(job_name)
            return self.batch_runner.retrieve_results(uri)

        with ThreadPoolExecutor(max_workers=retrieval_workers) as executor:
            future_map = {executor.submit(_wait_and_fetch, jn): jn for jn, _ in job_infos}
            for fut in as_completed(future_map):
                chunk_result = fut.result()
                result_map.update(chunk_result)

        ordered_results = [result_map.get(f"req-{i}", "Error: Result not found") for i in range(total)]
        return ordered_results

    def _generate_with_parallel(
        self, 
        user_inputs: List[str], 
        system_prompt: str, 
        response_schema: Optional[Union[type[BaseModel], dict]], 
        use_function_call: Optional[bool]
    ) -> List[str]:
        
        if use_function_call is None:
            use_function_call = self.use_function_call
            
        responses = [None] * len(user_inputs)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_index = {
                executor.submit(
                    self._generate_single, i, inp, system_prompt, response_schema, use_function_call
                ): i for i, inp in enumerate(user_inputs)
            }
            
            for future in tqdm(as_completed(future_to_index), total=len(user_inputs), desc="Generating (Parallel)"):
                idx, res = future.result()
                responses[idx] = res
                
        return responses

    def _generate_single(
        self, 
        index: int, 
        user_input: str, 
        system_prompt: str, 
        response_schema: Optional[Union[type[BaseModel], dict]], 
        use_function_call: Optional[bool]
    ) -> tuple[int, Optional[str]]:
        
        for attempt in range(self.max_retries):
            try:
                resp = self.client.generate(
                    system_prompt=system_prompt,
                    content=user_input,
                    model=self.model_name,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    response_schema=response_schema,
                    use_function_call=use_function_call
                )
                
                # Parse Response
                if not resp.candidates:
                    time.sleep(2 ** attempt)
                    continue
                    
                cand = resp.candidates[0]
                
                if cand.content.parts:
                    part = cand.content.parts[0]
                    if part.function_call:
                        # Convert function call to JSON string
                        result_data = {key: val for key, val in part.function_call.args.items()}
                        return index, json.dumps(result_data)
                    return index, part.text
                return index, ""
                
            except Exception as e:
                self.logger.warning(f"Request {index} failed (Attempt {attempt+1}): {e}")
                time.sleep(2 ** attempt)
                
        return index, f"Error: Request failed after {self.max_retries} retries."
