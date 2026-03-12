import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import GenericAlias
from typing import Optional, Union
import pandas as pd
import requests
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.auth import load_credentials_from_file
from google.auth.transport.requests import Request, AuthorizedSession
from google.cloud import bigquery
from google.genai.types import CreateBatchJobConfig, BatchJob
from pydantic import BaseModel, TypeAdapter
from transinfo.config import settings
from transinfo.utils.llm import GeminiClient


# Initialize clients
class BigQueryClient:
    def __init__(self):
        self.service_account_file = settings.GEMINI_SERVICE_ACCOUNT_FILE
        self.scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        self.proxy = settings.LLM_PROXY
        self.refresh_ttl = timedelta(seconds=1800)

        self._client: Optional[bigquery.Client] = None
        self._credentials = None
        self._project_id: Optional[str] = None
        self._last_refresh: Optional[datetime] = None

    def _build_client(self) -> bigquery.Client:
        """
        Lazily create a BigQuery client with periodic refresh.
        """
        now = datetime.now(timezone.utc)
        if (
            self._client
            and self._credentials
            and self._project_id
            and self._last_refresh
        ):
            fresh_enough = now - self._last_refresh < self.refresh_ttl
            not_expired = not getattr(self._credentials, "expired", True)
            if fresh_enough and not_expired:
                return self._client

        credentials, project_id = load_credentials_from_file(
            self.service_account_file,
            scopes=self.scopes,
        )

        session = requests.Session()
        if self.proxy:
            session.proxies.update({"http": self.proxy, "https": self.proxy})
        credentials.refresh(Request(session=session))

        _http = None
        if self.proxy:
            _http = AuthorizedSession(credentials)
            _http.proxies.update({"http": self.proxy, "https": self.proxy})

        self._client = bigquery.Client(
            credentials=credentials,
            project=project_id,
            _http=_http,
        )
        self._credentials = credentials
        self._project_id = project_id
        self._last_refresh = now

        return self._client

    @property
    def client(self) -> bigquery.Client:
        return self._build_client()


class BigQueryBatchRunner:
    """
    A class to handle BigQuery-based Gemini batch processing.
    """

    def __init__(
        self,
        bq_client: BigQueryClient = BigQueryClient(),
        gemini_client: GeminiClient = GeminiClient(),
    ):
        """
        Initialize the BigQueryBatchRunner with optional BigQuery and Gemini clients.

        Args:
            bq_client: Optional BigQuery client. Defaults to a new BigQueryClient.
            gemini_client: Optional Gemini client. Defaults to a new GeminiClient.
        """
        self.bq_client = bq_client
        self.gemini_client = gemini_client

    def create_bq_dataset(self):
        """
        Creates a BigQuery dataset if it does not already exist.
        """
        dataset_ref = self.bq_client.client.dataset(settings.BQ_DATASET_NAME)
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = settings.GEMINI_LOCATION
        try:
            self.bq_client.client.create_dataset(dataset)  # API request
            print(f"Dataset {settings.BQ_DATASET_NAME} created.")
        except Exception as e:
            if "Already Exists" in str(e):
                print(f"Dataset {settings.BQ_DATASET_NAME} already exists.")
            else:
                raise e

    def generate_bq_csv(
        self,
        csv_filename: str,
        system_prompt: Union[str, list[str]],
        user_prompts: list[str],
        custom_id_list: list[str] = None,
        schema_class: type[BaseModel] = None,
        max_token: int = 500,
        thinking_budget: int = 0,
    ) -> str:
        """
        Generates a CSV file for batch processing with the Gemini API.
        """
        # Get current timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        # Insert timestamp into the filename before the extension
        name, ext = os.path.splitext(csv_filename)
        csv_filename = f"{name}_{timestamp}{ext}"

        df = pd.DataFrame({"user_prompt": user_prompts})
        df["system_prompt"] = system_prompt

        schema_json = None
        if isinstance(
            schema_class, GenericAlias
        ):  # Handle generic types like list[Method]
            schema_json = TypeAdapter(schema_class).json_schema()
        elif schema_class and issubclass(schema_class, BaseModel):
            schema_json = schema_class.model_json_schema()

        def create_batch_request_json(row) -> str:
            request_parts = [
                {"text": "{user_prompt}".format(user_prompt=row["user_prompt"])},
            ]

            generation_config = {
                "temperature": 0,
                "thinkingConfig": {"thinkingBudget": thinking_budget},
                "maxOutputTokens": max_token,
                "stopSequences": ["\n\n\n\n"],
            }
            if schema_json:
                generation_config["responseMimeType"] = "application/json"
                generation_config["responseJsonSchema"] = schema_json
            else:
                generation_config["responseMimeType"] = "text/plain"

            return json.dumps(
                {
                    "contents": [
                        {
                            "role": "user",
                            "parts": request_parts,
                        },
                    ],
                    "systemInstruction": {
                        "parts": [
                            {
                                "text": "{system_prompt}".format(
                                    system_prompt=row["system_prompt"]
                                )
                            }
                        ]
                    },
                    "generationConfig": generation_config,
                }
            )

        df["request"] = df.apply(create_batch_request_json, axis=1)
        df["custom_id"] = custom_id_list

        df.to_csv(
            csv_filename,
            index_label="index",
            columns=["custom_id", "request"],
            sep="\t",
            quoting=0,
        )

        return csv_filename

    def create_bq_table(self, csv_path: str):
        """
        Creates a BigQuery table from a CSV file.
        """
        self.create_bq_dataset()  # Ensure dataset exists

        table_name = Path(
            csv_path
        ).stem  # Use pathlib to get the table name from the CSV path
        table_id = f"{settings.BQ_DATASET_NAME}.{table_name}"

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            autodetect=True,
            skip_leading_rows=1,  # Treat the first row as a header
            field_delimiter="\t",
            allow_quoted_newlines=True,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,  # Overwrite table if it exists
        )

        with open(csv_path, "rb") as source_file:
            job = self.bq_client.client.load_table_from_file(
                source_file, table_id, job_config=job_config
            )

        job.result()  # Waits for the job to complete

        print(f"Loaded {job.output_rows} rows into {table_id}")
        return f"bq://{self.bq_client.client.project}.{settings.BQ_DATASET_NAME}.{table_name}"

    def run_batch_prediction(
        self, input_bq_uri: str, output_file_path: str = "", model: str = None
    ):
        """
        Runs a batch prediction job using the Gemini API and returns the results as a DataFrame.

        Args:
            input_bq_uri: BigQuery URI for input data or CSV file path.
            output_file_path: Optional output file path to derive output table name.
            model: Model to use for batch prediction. Defaults to settings.GEMINI_MODEL.
        """
        model = model or settings.GEMINI_MODEL

        # Check if input_bq_uri is a CSV file path
        if input_bq_uri.endswith(".csv") and Path(input_bq_uri).is_file():
            print(f"CSV file detected: {input_bq_uri}. Creating BigQuery table...")
            input_bq_uri = self.create_bq_table(input_bq_uri)
            print(f"BigQuery table created from CSV: {input_bq_uri}")

        # Parse input_bq_uri to get project, dataset, and table name
        match = re.match(r"bq://([^.]+)\.([^.]+)\.(.+)", input_bq_uri)
        if not match:
            raise ValueError(
                f"Invalid input_bq_uri format: {input_bq_uri}. Must be a BQ URI or a valid CSV file path."
            )
        project_id = match.group(1)
        dataset_name = match.group(2)
        input_table_name = match.group(3)

        # Construct the output BigQuery URI
        if output_file_path:
            # Derive table name from output_file_path (assuming it's a file path)
            output_table_name = Path(output_file_path).stem
        else:
            # Default case: output_file_path is empty, append "_result" to input table name
            output_table_name = f"{input_table_name}_result"

        output_uri = f"bq://{project_id}.{dataset_name}.{output_table_name}"

        batch_job = self.gemini_client.client.batches.create(
            model=model,
            src=input_bq_uri,
            config=CreateBatchJobConfig(dest=output_uri),
        )
        print(f"Batch job {batch_job.name} created with model {model}. State: {batch_job.state}")
        return batch_job.name

    @retry(
        retry=retry_if_exception_type((httpx.ConnectError, httpx.ReadError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def get_batch_job(self, job_name: str) -> BatchJob:
        """
        Gets the status of a batch prediction job.

        Automatically retries on transient network errors (ConnectError, ReadError,
        TimeoutException) with exponential backoff.
        """
        return self.gemini_client.client.batches.get(name=job_name)

    def download_bq_table(
        self,
        table_id: str,
        output_file_path: str = "",
        columns: Optional[list[str]] = None,
    ) -> str:
        """
        Downloads data from a BigQuery table to a CSV file.

        Note:
            If `columns` is provided, only those columns will be downloaded (in the
            order given). This is useful for Gemini batch result tables where the
            request/prompt columns can be extremely large; most downstream logic
            only needs `custom_id` and `response`.
        """
        # Parse table_id to get project, dataset, and table name
        parts = table_id.split(".")
        if len(parts) != 3:
            raise ValueError(
                f"Invalid table_id format: {table_id}. Expected 'project.dataset.table'."
            )
        table_name = parts[2]

        if not output_file_path:
            output_file_path = f"{table_name}.csv"

        try:
            timeout_seconds = 86400
            bq_retry = bigquery.DEFAULT_RETRY.with_deadline(timeout_seconds)

            table = self.bq_client.client.get_table(table_id, retry=bq_retry, timeout=timeout_seconds)
            if columns:
                wanted = [c for c in columns if c]
                schema_by_name = {f.name: f for f in table.schema}
                missing = [c for c in wanted if c not in schema_by_name]
                if missing:
                    raise ValueError(
                        f"Columns not found in table schema: {missing}. "
                        f"Available columns: {list(schema_by_name.keys())}"
                    )
                selected_fields = [schema_by_name[c] for c in wanted]
            else:
                selected_fields = None

            rows = self.bq_client.client.list_rows(
                table,
                selected_fields=selected_fields,
                retry=bq_retry,
                timeout=timeout_seconds,
            )
            df = rows.to_dataframe()
            df.to_csv(output_file_path, index=False)
            print(f"Downloaded {df.shape[0]} rows from {table_id} to {output_file_path}")
            return output_file_path
        except Exception as e:
            raise Exception(f"Error downloading BigQuery table {table_id}: {e}")

    def delete_bq_table(self, table_id_or_uri: str):
        """
        Deletes a BigQuery table.
        """
        if table_id_or_uri.startswith("bq://"):
            match = re.match(r"bq://([^.]+)\.([^.]+)\.(.+)", table_id_or_uri)
            if not match:
                raise ValueError(f"Invalid BQ URI: {table_id_or_uri}")
            project, dataset, table = match.groups()
            table_id = f"{project}.{dataset}.{table}"
        else:
            table_id = table_id_or_uri

        try:
            self.bq_client.client.delete_table(table_id, not_found_ok=True)
            print(f"Deleted BigQuery table: {table_id}")
        except Exception as e:
            print(f"Error deleting BigQuery table {table_id}: {e}")


# Maintain top-level functions for backward compatibility (using default_bq_client)
def create_bq_dataset(dataset_name: str = "polymer"):
    return BigQueryBatchRunner().create_bq_dataset(dataset_name)


def generate_bq_csv(
    csv_filename: str,
    system_prompt: str | list[str],
    user_prompts: list[str],
    custom_id_list: list[str] = None,
    schema_class: type[BaseModel] = None,
    max_token: int = 500,
    thinking_budget: int = 0,
) -> str:
    return BigQueryBatchRunner().generate_bq_csv(
        csv_filename,
        system_prompt,
        user_prompts,
        custom_id_list,
        schema_class,
        max_token,
        thinking_budget=thinking_budget,
    )


def create_bq_table(csv_path: str, dataset_name: str = "polymer"):
    return BigQueryBatchRunner().create_bq_table(csv_path, dataset_name)


def run_batch_prediction(
    input_bq_uri: str, model: str = "gemini-2.0-flash", output_file_path: str = ""
):
    return BigQueryBatchRunner().run_batch_prediction(
        input_bq_uri, model, output_file_path
    )


def get_batch_job_status(job_name: str):
    return BigQueryBatchRunner().get_batch_job_status(job_name)


def download_bq_table(table_id: str, output_file_path: str = "") -> str:
    return BigQueryBatchRunner().download_bq_table(table_id, output_file_path)


def delete_bq_table(table_id_or_uri: str):
    return BigQueryBatchRunner().delete_bq_table(table_id_or_uri)
