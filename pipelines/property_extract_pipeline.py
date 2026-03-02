import argparse
import copy
import csv
import ctypes
import glob
import json
import os
import sys
from typing import List, Tuple

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
        cur = os.environ.get("LD_PRELOAD", "").strip()
        parts = [p for p in cur.split() if p]
        if candidate not in parts:
            os.environ["LD_PRELOAD"] = candidate + (" " + cur if cur else "")
        os.environ["LCC_LIBSTDCPP_READY"] = "1"
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            try:
                ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
            except Exception:
                pass
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
                if key not in wanted:
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

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default

def _norm_key(s: str) -> str:
    s = s.lower()
    for p in ["prompt_", "extract_polymer_", "extract_", "polymer_"]:
        if s.startswith(p):
            s = s[len(p):]
    return s

def _pair_prompts_and_schemas(prompt_dir: str, schema_dir: str) -> List[Tuple[str, str, str]]:
    md_files = [f for f in glob.glob(os.path.join(prompt_dir, "*.md")) if os.path.isfile(f)]
    json_files = [f for f in glob.glob(os.path.join(schema_dir, "*.json")) if os.path.isfile(f)]
    schemas = {}
    for jf in json_files:
        base = os.path.splitext(os.path.basename(jf))[0]
        schemas[_norm_key(base)] = jf
    pairs = []
    for mf in md_files:
        base = os.path.splitext(os.path.basename(mf))[0]
        key = _norm_key(base)
        jp = schemas.get(key)
        if not jp:
            continue
        pairs.append((key, mf, jp))
    return pairs

HEADERS = {
    "mechanical": ["doi","file_path","polymer_name","record_type","metric_group","metric_type","value","unit","temperature","temperature_range","frequency","test_standard","test_conditions","test_mode","measurement_direction","notes"],
    "thermal": ["doi","file_path","polymer_name","record_type","value","unit","temperature","temperature_range","test_method","decomposition_criterion","atmosphere","notes"],
    "electrical": ["doi","file_path","polymer_name","record_type","value","unit","frequency","temperature","test_standard","test_method","loss_tangent","structural_info","notes"],
    "optical": ["doi","file_path","polymer_name","record_type","value","unit","wavelength","ri_mode","method","conditions","thickness","transmittance_unit","notes"],
    "other": ["doi","file_path","polymer_name","record_type","value","unit","conditions","notes"],
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

def _normalize_item(category: str, key: str, item: dict, doi: str, file_path: str) -> dict:
    row = {h: "" for h in _fixed_headers(category)}
    row["doi"] = doi
    row["file_path"] = file_path
    row["polymer_name"] = str(item.get("polymer_name","")).strip()
    row["record_type"] = key
    if category == "mechanical":
        val = _first_non_empty(item, ["modulus_value","strength_value","value"])
        unit = _first_non_empty(item, ["modulus_unit","strength_unit","unit"])
        row["value"] = val
        row["unit"] = unit
        if key == "tensile_modulus":
            row["metric_group"] = "modulus"
            row["metric_type"] = _first_non_empty(item, ["modulus_type"], "Tensile")
            row["test_standard"] = _first_non_empty(item, ["test_standard"])
            row["test_conditions"] = _first_non_empty(item, ["test_conditions"])
        elif key == "flexural_modulus":
            row["metric_group"] = "modulus"
            row["metric_type"] = _first_non_empty(item, ["modulus_type"], "Flexural")
            row["test_standard"] = _first_non_empty(item, ["test_standard"])
            row["test_conditions"] = _first_non_empty(item, ["test_conditions"])
        elif key == "storage_modulus":
            row["metric_group"] = "modulus"
            row["metric_type"] = "Storage"
            row["temperature"] = _first_non_empty(item, ["temperature"])
            row["frequency"] = _first_non_empty(item, ["frequency"])
            row["test_mode"] = _first_non_empty(item, ["test_mode"])
        elif key == "loss_modulus":
            row["metric_group"] = "modulus"
            row["metric_type"] = "Loss"
            row["temperature"] = _first_non_empty(item, ["temperature"])
            row["frequency"] = _first_non_empty(item, ["frequency"])
        elif key == "tan_delta":
            row["metric_group"] = "damping"
            row["metric_type"] = "TanDelta"
            row["temperature"] = _first_non_empty(item, ["temperature"])
            row["frequency"] = _first_non_empty(item, ["frequency"])
        elif key == "tensile_strength":
            row["metric_group"] = "strength"
            row["metric_type"] = _first_non_empty(item, ["strength_type"], "Not specified")
            row["test_standard"] = _first_non_empty(item, ["test_standard"])
            row["test_conditions"] = _first_non_empty(item, ["test_conditions"])
        else:
            row["metric_group"] = _first_non_empty(item, ["metric_group"])
            row["metric_type"] = _first_non_empty(item, ["metric_type"])
        row["measurement_direction"] = _first_non_empty(item, ["measurement_direction"])
        row["notes"] = _first_non_empty(item, ["notes"])
    elif category == "thermal":
        val = _first_non_empty(item, ["cte_value","tg_value","td_value","tm_value","tc_value","thermal_conductivity_value","value"])
        unit = _first_non_empty(item, ["cte_unit","tg_unit","td_unit","tm_unit","tc_unit","thermal_conductivity_unit","unit"])
        row["value"] = val
        row["unit"] = unit
        row["temperature"] = _first_non_empty(item, ["temperature"])
        row["temperature_range"] = _first_non_empty(item, ["temperature_range"])
        row["test_method"] = _first_non_empty(item, ["test_method"])
        row["decomposition_criterion"] = _first_non_empty(item, ["decomposition_criterion"])
        row["atmosphere"] = _first_non_empty(item, ["atmosphere"])
        row["notes"] = _first_non_empty(item, ["notes"])
    elif category == "electrical":
        val = _first_non_empty(item, ["dielectric_value","dielectric_loss","value"])
        row["value"] = val
        row["unit"] = _first_non_empty(item, ["unit"])
        row["frequency"] = _first_non_empty(item, ["frequency"])
        row["temperature"] = _first_non_empty(item, ["temperature"])
        row["test_standard"] = _first_non_empty(item, ["test_standard"])
        row["test_method"] = _first_non_empty(item, ["test_method"])
        row["loss_tangent"] = _first_non_empty(item, ["loss_tangent"])
        row["structural_info"] = _first_non_empty(item, ["structural_info"])
        row["notes"] = _first_non_empty(item, ["notes"])
    elif category == "optical":
        val = _first_non_empty(item, ["ri_value","transmittance_value","yi_value","value"])
        unit = _first_non_empty(item, ["transmittance_unit","unit"])
        row["value"] = val
        row["unit"] = unit
        row["wavelength"] = _first_non_empty(item, ["measurement_wavelength","wavelength"])
        row["ri_mode"] = _first_non_empty(item, ["ri_mode"])
        row["method"] = _first_non_empty(item, ["method"])
        row["conditions"] = _first_non_empty(item, ["conditions","test_conditions"])
        row["thickness"] = _first_non_empty(item, ["thickness"])
        row["transmittance_unit"] = _first_non_empty(item, ["transmittance_unit"])
        row["notes"] = _first_non_empty(item, ["notes"])
    else:
        row["value"] = _first_non_empty(item, ["value"])
        row["unit"] = _first_non_empty(item, ["unit"])
        row["conditions"] = _first_non_empty(item, ["conditions","test_conditions"])
        row["notes"] = _first_non_empty(item, ["notes"])
    return row

class ExtractCategoryProperties(PipelineBase):
    def __init__(self, entry_file_name: str, category: str, prompt_dir: str, schema_dir: str, max_chunk_len=32000, llm_max_workers=None, llm_max_tokens=None):
        super().__init__()
        self.category = category
        self.pairs = _pair_prompts_and_schemas(prompt_dir, schema_dir)
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
            model_name="gemini-2.5-flash",
            max_workers=llm_max_workers,
            max_tokens=llm_max_tokens,
        )
        self.generators = []
        for key, md_path, schema_path in self.pairs:
            prompt = MarkdownSchemaPrompt(md_path=md_path, schema_path=schema_path)
            gen = ChunkedPromptedGenerator(
                llm_serving=self.llm_serving,
                prompt_template=prompt,
                json_schema=prompt.build_json_schema(),
                max_chunk_len=max_chunk_len
            )
            self.generators.append((key, gen))
        self.parse_operator = PandasOperator([
            lambda df: df.assign(
                properties=df.apply(self._parse_all, axis=1)
            )
        ])

    def _parse_all(self, row):
        out = []
        doi_fb = str(row.get("doi_hint", "") or "").strip()
        file_path = str(row.get("file_path","") or "")
        if not doi_fb and file_path:
            try:
                doi_fb = os.path.basename(os.path.dirname(file_path))
            except Exception:
                doi_fb = ""
        for key, _, _ in self.pairs:
            raw_key = f"{key}_raw"
            raw_list = row.get(raw_key)
            if raw_list is None:
                continue
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
                    norm = _normalize_item(self.category, key, item, doi_fb, file_path)
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

def prepare_input_data(json_files, output_jsonl):
    data = []
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content_json = json.load(f)
            text_content = content_json.get('content', '')
            if not text_content:
                continue
            data.append({
                "file_path": file_path,
                "content": text_content,
                "doi_hint": content_json.get('token', '')
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
    _load_env_from_setup_env()
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
        prompt_dir = args.prompt_dir or f"/share/lcc/prompt/{category}"
        schema_dir = args.schema_dir or f"/share/lcc/schema/{category}"
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
            prepare_input_data(json_files, output_jsonl)
            entry_file = output_jsonl
        if not entry_file:
            continue
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
