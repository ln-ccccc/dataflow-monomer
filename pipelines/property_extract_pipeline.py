import argparse
import copy
import csv
import glob
import json
import os
import sys
import ctypes
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        candidates = []
        env_path = os.getenv("LCC_SETUP_ENV_PATH")
        if env_path:
            candidates.append(env_path)
        
        # 尝试一些相对路径
        candidates.append("./setup_env.sh")
        candidates.append("../setup_env.sh")
        candidates.append("../../setup_env.sh")
        
        local_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "setup_env.sh",
        )
        candidates.append(local_path)
        if path is not None:
            candidates.insert(0, path)
        picked = ""
        for cand in candidates:
            if cand and os.path.exists(cand):
                picked = cand
                break
        if not picked:
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
        with open(picked, "r", encoding="utf-8") as f:
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

PAPER_ROOT = os.getenv("LCC_PAPER_ROOT", "../paper")

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
    if doi:
        row["doi"] = doi
    else:
        row["doi"] = _first_non_empty(item, ["doi"])
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
    def __init__(self, entry_file_name: str, category: str, prompt_dir: str, schema_dir: str, max_chunk_len=32000, llm_max_workers=None, llm_max_tokens=None, use_batch=False):
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
            use_batch=use_batch,
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
            ).drop(columns=[f"{self.category}_raw", "content"], errors="ignore")
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
            if isinstance(chunk_res, dict) and "error" in chunk_res:
                continue
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
    os.makedirs(os.path.dirname(output_jsonl) or ".", exist_ok=True)
    written = 0
    with open(output_jsonl, 'w', encoding='utf-8') as out_f:
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
                entry = {
                    "file_path": file_path,
                    "content": text_content,
                    "doi_hint": content_json.get('token', ''),
                    "extracted_doi": doi_candidate,
                }
                out_f.write(json.dumps(entry) + '\n')
                written += 1
            except Exception:
                continue
    return written

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
    parser.add_argument("--offset", type=int, default=0, help="Start index for processing json files")
    parser.add_argument("--batch-size", type=int, default=0, help="Batch size for auto-sharding loop")
    parser.add_argument("--use-batch", action="store_true", help="是否使用 BigQuery 批量推理")
    args = parser.parse_args()
    cats_arg = args.categories if args.categories else args.category
    cats = [c.strip().lower() for c in cats_arg.split(",") if c.strip()]
    allowed = {"electrical","mechanical","optical","other","thermal"}
    cats = [c for c in cats if c in allowed]
    if not cats:
        raise SystemExit(1)
    if logger:
        logger.info(f"Running property_extract_pipeline categories {','.join(cats)} base_dir {args.base_dir or ''} entry_file {args.entry_file or ''}")

    # 如果启用了自动分批循环模式（指定了 --batch-size > 0 且未指定 --entry-file）
    if args.batch_size > 0 and args.base_dir and not args.entry_file:
        all_json_files = find_json_files(args.base_dir)
        all_json_files.sort()
        total_files = len(all_json_files)
        
        # 应用全局 limit 和 offset
        start_global = args.offset
        end_global = total_files
        if args.limit > 0:
            end_global = min(start_global + args.limit, total_files)
        
        if logger:
            logger.info(f"Auto-sharding mode: total_files={total_files}, processing range [{start_global}, {end_global}), batch_size={args.batch_size}")

        for start_idx in range(start_global, end_global, args.batch_size):
            current_limit = min(args.batch_size, end_global - start_idx)
            if current_limit <= 0:
                break
                
            if logger:
                logger.info(f"Starting batch: offset={start_idx}, limit={current_limit}")
            
            # 构造当前批次的参数，递归调用 _run_pipeline_batch
            # 这里为了避免深度递归或复杂重构，我们把核心逻辑抽取为 _run_pipeline_batch 函数
            _run_pipeline_batch(
                cats=cats,
                base_dir=args.base_dir,
                entry_file=None,
                output_jsonl=args.output_jsonl,
                prompt_dir=args.prompt_dir or _DEFAULT_PROMPT_DIR,
                schema_dir=args.schema_dir or _DEFAULT_SCHEMA_DIR,
                max_chunk_len=args.max_chunk_len,
                use_batch=args.use_batch,
                offset=start_idx,
                limit=current_limit,
                logger=logger
            )
        return

    # 常规单次运行模式
    _run_pipeline_batch(
        cats=cats,
        base_dir=args.base_dir,
        entry_file=args.entry_file,
        output_jsonl=args.output_jsonl,
        prompt_dir=args.prompt_dir or _DEFAULT_PROMPT_DIR,
        schema_dir=args.schema_dir or _DEFAULT_SCHEMA_DIR,
        max_chunk_len=args.max_chunk_len,
        use_batch=args.use_batch,
        offset=args.offset,
        limit=args.limit,
        logger=logger
    )

def _run_pipeline_batch(
    cats, base_dir, entry_file, output_jsonl, prompt_dir, schema_dir, 
    max_chunk_len, use_batch, offset, limit, logger
):
    pipeline_tasks = []
    for category in cats:
        current_entry_file = entry_file
        if base_dir:
            current_output_jsonl = output_jsonl
            if not current_output_jsonl:
                if os.path.isdir(base_dir):
                    current_output_jsonl = os.path.join(base_dir, f"{category}_input.jsonl")
                else:
                    current_output_jsonl = os.path.join(os.path.dirname(base_dir), f"{category}_input.jsonl")
            
            # 如果指定了 offset/limit，给 jsonl 文件名加后缀，避免覆盖全量文件或不同分片冲突
            if offset > 0 or (limit and limit > 0):
                base, ext = os.path.splitext(current_output_jsonl)
                current_output_jsonl = f"{base}_{offset}_{limit}{ext}"

            json_files = find_json_files(base_dir)
            
            # 对 json_files 排序以保证分片确定性
            json_files.sort()
            
            start_idx = offset
            if start_idx > 0:
                if start_idx >= len(json_files):
                    json_files = []
                else:
                    json_files = json_files[start_idx:]
            
            if limit and limit > 0:
                json_files = json_files[:limit]

            if not json_files:
                if logger:
                    logger.info(f"No files to process for category {category} with offset {offset} limit {limit}")
                continue

            n = prepare_input_data(json_files, current_output_jsonl, base_dir=base_dir)
            if logger:
                logger.info(f"Prepared input_jsonl category {category} files {len(json_files)} rows {n} path {current_output_jsonl}")
            
            current_entry_file = current_output_jsonl
        if not current_entry_file:
            continue
        if logger:
            logger.info(f"Initializing category {category} entry_file {current_entry_file}")
        pipeline = ExtractCategoryProperties(
            entry_file_name=current_entry_file,
            category=category,
            prompt_dir=prompt_dir,
            schema_dir=schema_dir,
            max_chunk_len=max_chunk_len,
            use_batch=use_batch
        )
        pipeline.compile()
        pipeline_tasks.append((category, pipeline))

    if not pipeline_tasks:
        return

    # 并发执行 forward 阶段。
    MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_CATEGORIES", "2"))
    max_workers = min(len(pipeline_tasks), MAX_CONCURRENT_JOBS)
    
    if logger:
        logger.info(f"Starting parallel execution for {len(pipeline_tasks)} categories with max_workers={max_workers}")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_cat = {executor.submit(p.forward): (c, p) for c, p in pipeline_tasks}
        for future in as_completed(future_to_cat):
            category, pipeline = future_to_cat[future]
            try:
                future.result()
                if logger:
                    logger.info(f"Pipeline forward completed for {category}, saving to CSV...")
                save_results_to_csv(pipeline, category=category)
            except Exception as e:
                if logger:
                    logger.info(f"property_extract_pipeline failed category {category} err {type(e).__name__}: {e}")
                else:
                    raise

if __name__ == "__main__":
    main()
