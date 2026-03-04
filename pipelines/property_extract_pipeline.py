import argparse
import copy
import csv
import glob
import json
import os
import sys
import ctypes
from typing import List, Tuple

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from operators.general.chunked_generator import ChunkedPromptedGenerator
from dataflow.operators.core_text import PandasOperator
from prompts.generic_md_prompt import MarkdownSchemaPrompt
from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing
try:
    from dataflow.utils.storage import BatchedFileStorage as FileStorage
except Exception:
    from dataflow.utils.storage import LazyFileStorage as FileStorage
try:
    contractors = ()
    from dataflow.pipeline import BatchedPipelineABC as PipelineBase
except Exception:
    class PipelineBase:
        def __init__(self): ...
        def compile(self): ...
from utils.format_utils import safe_parse_json

def _ensure_local_libstdcpp():
    try:
        if os.environ.get("LCC_LIBSTDCPP_READY") == "1":
            return
        candidates = []
        prefix = os.path.dirname(os.path.dirname(sys.executable))
        candidates.append(os.path.join(prefix, "lib", "libstdc++.so.6"))
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            candidates.append(os.path.join(conda_prefix, "lib", "libstdc++.so.6"))
        candidates.append("/opt/mamba/envs/lcc/lib/libstdc++.so.6")
        candidate = None
        for c in candidates:
            if os.path.exists(c):
                candidate = c
                break
        if not candidate:
            return
        lib_dir = os.path.dirname(candidate)
        cur_ld = os.environ.get("LD_LIBRARY_PATH", "")
        parts = [p for p in cur_ld.split(":") if p]
        if lib_dir not in parts:
            os.environ["LD_LIBRARY_PATH"] = lib_dir + (":" + cur_ld if cur_ld else "")
        try:
            ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
        except Exception:
            pass
        cur = os.environ.get("LD_PRELOAD", "").strip()
        parts = [p for p in cur.split() if p]
        if candidate not in parts:
            os.environ["LD_PRELOAD"] = candidate + (" " + cur if cur else "")
        os.environ["LCC_LIBSTDCPP_READY"] = "1"
    except Exception:
        pass

def _load_env_from_setup_env(path=None):
    try:
        if path is None:
            path = os.getenv("LCC_SETUP_ENV_PATH", "/share/lcc/setup_env.sh")
        if not os.path.exists(path):
            return
        wanted = {
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GCP_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        }
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("export "):
                    continue
                _, rest = line.split("export ", 1)
                if "=" not in rest:
                    continue
                key, value = rest.split("=", 1)
                key = key.strip()
                if key.startswith("PROPS_") or key in wanted:
                    pass
                else:
                    continue
                if os.environ.get(key):
                    continue
                value = value.strip()
                if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
                    value = value[1:-1]
                os.environ[key] = value
        for upper, lower in [("HTTP_PROXY", "http_proxy"), ("HTTPS_PROXY", "https_proxy")]:
            if upper in os.environ and lower not in os.environ:
                os.environ[lower] = os.environ[upper]
    except Exception:
        pass

_ensure_local_libstdcpp()
_load_env_from_setup_env()

PAPER_ROOT = "/share/lcc/paper"

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default

HEADERS = {
    "mechanical": [
        "doi", "file_path", "polymer_name", "record_type", "metric_group", "metric_type", 
        "value", "temperature", "temperature_range", "frequency", 
        "test_standard", "test_method", "test_conditions", "test_mode", "measurement_direction", "notes"
    ],
    "thermal": [
        "doi", "file_path", "polymer_name", "record_type", 
        "value", "temperature", "temperature_range", 
        "test_standard", "test_method", "test_conditions", "heating_rate", "decomposition_criterion", "atmosphere", "notes"
    ],
    "electrical": [
        "doi", "file_path", "polymer_name", "record_type", 
        "value", "temperature", "frequency", 
        "test_standard", "test_method", "test_conditions", "notes"
    ],
    "optical": [
        "doi", "file_path", "polymer_name", "record_type", 
        "value", "temperature", "wavelength", "thickness", 
        "test_standard", "test_method", "test_conditions", "ri_mode", "notes"
    ],
    "other": [
        "doi", "file_path", "polymer_name", "record_type", 
        "value", "temperature", 
        "test_standard", "test_method", "test_conditions", "notes"
    ]
}

def _fixed_headers(category: str) -> List[str]:
    return HEADERS[category]

def _first_non_empty(d, keys, default=""):
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s != "":
            return s
    return default

def _normalize_item(category: str, item: dict, doi: str, file_path: str) -> dict:
    # Initialize with empty strings for all headers
    headers = _fixed_headers(category)
    row = {h: "" for h in headers}
    
    # Fill common fields
    row["doi"] = _first_non_empty(item, ["doi"], doi)
    row["file_path"] = file_path
    row["polymer_name"] = str(item.get("polymer_name","")).strip()
    
    # Map item keys to row keys
    # Since headers now match schema keys exactly, we can try direct mapping first
    for k, v in item.items():
        if k in row:
            row[k] = _first_non_empty(item, [k])
            
    # Handle any potential aliases or fallback logic if schema keys drift from headers
    # But based on schema inspection, they match exactly now.
    # Just ensure value is captured if schema uses 'value'
    if "value" in row:
        row["value"] = _first_non_empty(item, ["value"])
        
    return row

_DEFAULT_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
_DEFAULT_SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schemas")

_CATEGORY_FILES = {
    "mechanical": (os.path.join("mechanical", "mechanical_properties.md"), ["mechanical_properties.json"]),
    "optical": (os.path.join("optical", "prompt_optical_properties.md"), ["optical_properties.json"]),
    "electrical": (os.path.join("electrical", "polymer_electrical_properties.md"), ["electrical_properties.json"]),
    "other": (os.path.join("other", "polymer_other_properties.md"), ["other_properties.json"]),
    "thermal": (os.path.join("thermal", "polymer_thermal_properties.md"), ["thermal_properties.json"]),
}

def _resolve_prompt_and_schema(category: str, prompt_dir: str, schema_dir: str) -> Tuple[str, str]:
    if category not in _CATEGORY_FILES:
        raise ValueError(f"Unsupported category: {category}")
    prompt_file_rel, schema_candidates = _CATEGORY_FILES[category]
    prompt_base = os.path.basename(prompt_file_rel)
    prompt_candidates = [
        os.path.join(prompt_dir, prompt_file_rel),
        os.path.join(prompt_dir, prompt_base),
        os.path.join(prompt_dir, category, prompt_base),
    ]
    md_path = ""
    for cand in prompt_candidates:
        if os.path.exists(cand):
            md_path = cand
            break
    if not md_path:
        raise FileNotFoundError(f"Prompt file not found for {category} in {prompt_dir}")
    schema_path = ""
    for name in schema_candidates:
        for cand in [os.path.join(schema_dir, name), os.path.join(schema_dir, category, name)]:
            if os.path.exists(cand):
                schema_path = cand
                break
        if schema_path:
            break
    if not schema_path:
        raise FileNotFoundError(f"Schema file not found for {category} in {schema_dir}")
    return md_path, schema_path

class ExtractCategoryProperties(PipelineBase):
    def __init__(self, entry_file_name: str, category: str, prompt_dir: str, schema_dir: str, max_chunk_len=32000, llm_max_workers=None, llm_max_tokens=None):
        super().__init__()
        self.category = category
        md_path, schema_path = _resolve_prompt_and_schema(category, prompt_dir, schema_dir)
        self.pairs = [(category, md_path, schema_path)]
        max_chunk_len = _env_int("PROPS_MAX_CHUNK_LEN", max_chunk_len)
        llm_max_workers = _env_int("PROPS_LLM_MAX_WORKERS", llm_max_workers if llm_max_workers is not None else 100)
        llm_max_tokens = _env_int("PROPS_LLM_MAX_TOKENS", llm_max_tokens if llm_max_tokens is not None else 64000)
        self.storage = FileStorage(
            first_entry_file_name=entry_file_name,
            cache_path=f"../{category}_output",
            cache_type="jsonl",
        )
        self.llm_serving = APIGoogleVertexAIServing(
            project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"),
            location='us-central1',
            model_name="gemini-2.5-pro",
            max_workers=llm_max_workers,
            max_tokens=llm_max_tokens,
        )
        self.generators = []
        disable_chunking = bool(_env_int("PROPS_DISABLE_CHUNKING", 1))
        for key, md_path, schema_path in self.pairs:
            prompt = MarkdownSchemaPrompt(md_path=md_path, schema_path=schema_path)
            gen = ChunkedPromptedGenerator(
                llm_serving=self.llm_serving,
                prompt_template=prompt,
                json_schema=prompt.build_json_schema(),
                max_chunk_len=max_chunk_len,
                disable_chunking=disable_chunking,
            )
            self.generators.append((key, gen))
        self.parse_operator = PandasOperator([
            lambda df: df.assign(
                properties=df.apply(self._parse_all, axis=1)
            )
        ])

    def _parse_all(self, row):
        out = []
        doi_fb = str(row.get("extracted_doi", "") or "").strip()
        if not doi_fb:
            doi_fb = str(row.get("doi_hint", "") or "").strip()
        file_path = str(row.get("file_path","") or "")
        if not doi_fb and file_path:
            try:
                dir_path = os.path.dirname(file_path)
                if dir_path.startswith(PAPER_ROOT + os.sep):
                    doi_fb = os.path.relpath(dir_path, PAPER_ROOT).replace("\\", "/").strip("/")
                else:
                    doi_fb = os.path.basename(dir_path)
            except Exception:
                doi_fb = ""
        key = self.category
        raw_key = f"{key}_raw"
        raw_list = row.get(raw_key)
        items = []
        for chunk_res in (raw_list or []):
            parsed = safe_parse_json(chunk_res, [])
            if isinstance(parsed, dict):
                parsed = [parsed]
            if not isinstance(parsed, list):
                parsed = []
            items.extend(parsed)
        for item in items:
            if isinstance(item, dict):
                norm = _normalize_item(self.category, item, doi_fb, file_path)
                # 以 Schema 为准：各类 Schema 都包含 record_type 与 value
                # 因此前置过滤改为检查 record_type 与 value 是否非空
                if not norm.get("record_type") or not norm.get("value"):
                    continue
                out.append(norm)
        return out

    def forward(self):
        logger = None
        try:
            from dataflow import get_logger
            logger = get_logger()
        except Exception:
            logger = None
        if logger:
            logger.info(f"Running ExtractCategoryProperties category {self.category} prompts {len(self.pairs)}")
        for key, gen in self.generators:
            if logger:
                logger.info(f"ExtractCategoryProperties stage llm_run category {self.category} key {key}")
            gen.run(
                storage=self.storage.step(),
                input_key="content",
                output_key=f"{key}_raw"
            )
        if logger:
            logger.info(f"ExtractCategoryProperties stage parse category {self.category}")
        self.parse_operator.run(storage=self.storage.step())
        if logger:
            logger.info(f"ExtractCategoryProperties complete category {self.category}")

    def compile(self):
        pass

def find_json_files(base_path):
    if os.path.isfile(base_path) and base_path.lower().endswith(".json"):
        return [base_path]
    return glob.glob(os.path.join(base_path, "**", "*.json"), recursive=True)

def prepare_input_data(json_files, output_jsonl, base_dir=""):
    data = []
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content_json = json.load(f)
            text_content = content_json.get('content', '')
            if not text_content:
                continue
            dir_path = os.path.dirname(file_path)
            doi_candidate = ""
            try:
                if dir_path.startswith(PAPER_ROOT + os.sep):
                    rel = os.path.relpath(dir_path, PAPER_ROOT).replace("\\", "/").strip("/")
                    doi_candidate = rel
            except Exception:
                doi_candidate = ""
            if not doi_candidate:
                doi_candidate = os.path.basename(dir_path)

            data.append({
                "file_path": file_path,
                "content": text_content,
                "doi_hint": content_json.get('token', ''),
                "extracted_doi": doi_candidate,
            })
        except Exception:
            continue
    with open(output_jsonl, 'w', encoding='utf-8') as f:
        for entry in data:
            f.write(json.dumps(entry) + '\n')
    return len(data)

def _read_pipeline_df(pipeline):
    storage_obj = pipeline.storage
    buffers = getattr(storage_obj, "_buffers", None)
    if isinstance(buffers, dict) and len(buffers) > 0:
        last_step = max(buffers.keys())
        reader = copy.copy(storage_obj)
        reader.operator_step = last_step
        return reader.read("dataframe")
    return storage_obj.step().read("dataframe")

def _csv_has_data(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            header = f.readline()
            if not header:
                return False
            return bool(f.readline())
    except Exception:
        return False

def _write_csv_fixed(path, rows, headers):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in headers})

def _write_one(row, category):
    file_path = getattr(row, "file_path", None)
    props = getattr(row, "properties", None)
    if not file_path or not os.path.exists(file_path):
        return "skip", 0
    dir_path = os.path.dirname(file_path)
    csv_path = os.path.join(dir_path, f"{category}.csv")
    if not props:
        if os.path.exists(csv_path) and _csv_has_data(csv_path):
            return "skip", 0
        _write_csv_fixed(csv_path, [], _fixed_headers(category))
        return "empty", 0
    headers = _fixed_headers(category)
    _write_csv_fixed(csv_path, props, headers)
    return "nonempty", len(props)

def save_results_to_csv(pipeline, category):
    logger = None
    try:
        from dataflow import get_logger
        logger = get_logger()
    except Exception:
        logger = None
    try:
        df = _read_pipeline_df(pipeline)
    except Exception:
        return
    total = len(df)
    if total == 0:
        return
    try:
        progress_every = int(os.getenv("PROPS_SAVE_PROGRESS_EVERY") or "50")
    except Exception:
        progress_every = 50
    if progress_every <= 0:
        progress_every = 0
    if logger:
        logger.info(f"Running save_results_to_csv category {category} total {total}")
    count = 0
    empty_written = 0
    nonempty_written = 0
    for i, row in enumerate(df.itertuples(index=False), 1):
        status, rows_written = _write_one(row, category=category)
        if status == "empty":
            empty_written += 1
        elif status == "nonempty":
            nonempty_written += 1
            count += 1
        if logger and progress_every and i % progress_every == 0:
            logger.info(f"save_results_to_csv progress category {category} {i}/{total}")
    res = {"nonempty": nonempty_written, "empty": empty_written, "rows": count}
    if logger:
        logger.info(f"save_results_to_csv complete category {category} total {total} nonempty {res['nonempty']} empty {res['empty']} rows {res['rows']}")
    return res

def main():
    logger = None
    try:
        from dataflow import get_logger
        logger = get_logger()
    except Exception:
        logger = None
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True, help="单个类别或逗号分隔的多个类别，如: electrical,mechanical,thermal,optical,other")
    parser.add_argument("--categories", default=None, help="等价于 --category，逗号分隔多个类别")
    parser.add_argument("--base-dir", type=str, default="")
    parser.add_argument("--entry-file", type=str, default="")
    parser.add_argument("--output-jsonl", type=str, default="")
    parser.add_argument("--prompt-dir", type=str, default="")
    parser.add_argument("--schema-dir", type=str, default="")
    parser.add_argument("--max-chunk-len", type=int, default=32000)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    cats_arg = args.categories if args.categories else args.category
    cats = [c.strip().lower() for c in cats_arg.split(",") if c.strip()]
    allowed = {"electrical","mechanical","optical","other","thermal"}
    cats = [c for c in cats if c in allowed]
    if not cats:
        raise SystemExit(1)
    if logger:
        logger.info(f"Running property_extract_pipeline categories {','.join(cats)} base_dir {args.base_dir or ''} entry_file {args.entry_file or ''}")
    for category in cats:
        prompt_dir = args.prompt_dir or _DEFAULT_PROMPT_DIR
        schema_dir = args.schema_dir or _DEFAULT_SCHEMA_DIR
        entry_file = args.entry_file
        if args.base_dir:
            base_dir = args.base_dir
            output_jsonl = args.output_jsonl
            if not output_jsonl:
                if os.path.isdir(base_dir):
                    output_jsonl = os.path.join(base_dir, f"{category}_input.jsonl")
                else:
                    output_jsonl = os.path.join(os.path.dirname(base_dir), f"{category}_input.jsonl")
            json_files = find_json_files(base_dir)
            if args.limit and args.limit > 0:
                json_files = json_files[:args.limit]
            n = prepare_input_data(json_files, output_jsonl, base_dir=base_dir)
            if logger:
                logger.info(f"Prepared input_jsonl category {category} files {len(json_files)} rows {n} path {output_jsonl}")
            entry_file = output_jsonl
        if not entry_file:
            continue
        if logger:
            logger.info(f"Running category {category} entry_file {entry_file} prompt_dir {prompt_dir} schema_dir {schema_dir}")
        pipeline = ExtractCategoryProperties(
            entry_file_name=entry_file,
            category=category,
            prompt_dir=prompt_dir,
            schema_dir=schema_dir,
            max_chunk_len=args.max_chunk_len
        )
        pipeline.compile()
        try:
            pipeline.forward()
            save_results_to_csv(pipeline, category=category)
        except Exception as e:
            if logger:
                logger.info(f"property_extract_pipeline failed category {category} err {type(e).__name__}: {e}")
            else:
                raise

if __name__ == "__main__":
    main()
