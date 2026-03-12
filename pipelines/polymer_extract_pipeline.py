import argparse
import copy
import csv
import glob
import json
import os
import sys
import ctypes

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
            "MONOMER_LLM_MAX_WORKERS",
            "MONOMER_LLM_MAX_TOKENS",
            "LLM_OPENAI_TIMEOUT",
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
from prompts.polymer import PolymerExtractPrompt
# from serving.api_openai_serving import APIOpenAICompatServing
from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing

try:
    from dataflow.utils.storage import BatchedFileStorage
except Exception:
    from dataflow.utils.storage import LazyFileStorage as BatchedFileStorage
try:
    from dataflow.pipeline import BatchedPipelineABC
except Exception:
    class BatchedPipelineABC:
        def __init__(self): ...
        def compile(self): ...

from dataflow.operators.core_text import PandasOperator
from utils.format_utils import safe_parse_json

PAPER_ROOT = os.getenv("LCC_PAPER_ROOT", "../paper")

class ExtractPolymer(BatchedPipelineABC):
    def __init__(self, entry_file_name: str, max_chunk_len=128000, use_batch=False):
        super().__init__()
        self.storage = BatchedFileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="../polymer_output",
            cache_type="jsonl",
        )
        
        # 使用 APIGoogleVertexAIServing 以匹配用户 setup_env.sh 中的 GCP 配置
        self.llm_serving = APIGoogleVertexAIServing(
            project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"),
            location="us-central1",
            model_name=os.getenv("LLM_OPENAI_MODEL", "gemini-2.5-pro"),
            max_workers=int(os.getenv("MONOMER_LLM_MAX_WORKERS", "100")),
            max_tokens=int(os.getenv("MONOMER_LLM_MAX_TOKENS", "8192")),
            # timeout=int(os.getenv("LLM_OPENAI_TIMEOUT", "60")),
            use_batch=use_batch,
        )
        
        self.prompt = PolymerExtractPrompt()
        self.prompt_generator = ChunkedPromptedGenerator(
            llm_serving=self.llm_serving,
            prompt_template=self.prompt,
            json_schema=self.prompt.build_json_schema(),
            max_chunk_len=max_chunk_len,
            input_aux_keys=["monomer_whitelist"]
        )

        def _read_monomers(p):
            try:
                d = os.path.dirname(p)
                jp = os.path.join(d, "monomers.json")
                if not os.path.exists(jp):
                    return []
                with open(jp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [str(x).strip() for x in data if str(x).strip()]
                return []
            except Exception:
                return []

        self.load_monomer_whitelist = PandasOperator([
            lambda df: df.assign(
                monomer_whitelist=df["file_path"].apply(_read_monomers)
            )
        ])

        self.parse_result = PandasOperator([
            lambda df: df.assign(
                polymers=df["polymers_raw"].apply(self._parse_polymers)
            ).drop(columns=["polymers_raw", "content"], errors="ignore")
        ])

    def _parse_polymers(self, raw_list):
        items = []
        for chunk_res in (raw_list or []):
            parsed = safe_parse_json(chunk_res, [])
            if isinstance(parsed, dict):
                parsed = [parsed]
            if not isinstance(parsed, list):
                parsed = []
            items.extend(parsed)
        return items

    def forward(self, batch_size=100, resume_from_last=True):
        self.load_monomer_whitelist.run(
            storage=self.storage.step(),
        )
        self.prompt_generator.run(
            storage=self.storage.step(),
            input_key="content",
            output_key="polymers_raw"
        )
        self.parse_result.run(
            storage=self.storage.step(),
        )

    def compile(self):
        pass


if __name__ == "__main__":
    from operators.general.paper_input_generator import PaperJsonInputGenerator
    from operators.polymer.polymer_csv_profile import build_polymer_csv_exporter

    _input_gen = PaperJsonInputGenerator(paper_root=PAPER_ROOT)

    def find_json_files(base_path):
        return _input_gen.find_json_files(base_path)

    def _extract_doi_from_dir(dir_path: str, marker: str = "selected_polyimide_papers") -> str:
        gen = PaperJsonInputGenerator(paper_root=PAPER_ROOT, marker=marker)
        return gen.extract_doi_from_dir(dir_path)

    def prepare_input_data(json_files, output_jsonl):
        records = _input_gen.build_records(json_files)
        _input_gen.write_jsonl(records, output_jsonl)
        return len(records)

    def run_pipeline(input_file, max_chunk_len=32000, use_batch=False):
        pipeline = ExtractPolymer(
            entry_file_name=input_file,
            max_chunk_len=max_chunk_len,
            use_batch=use_batch
        )
        pipeline.compile()
        pipeline.forward(batch_size=50, resume_from_last=True)
        return pipeline

    def _read_pipeline_df(pipeline):
        storage_obj = pipeline.storage
        buffers = getattr(storage_obj, "_buffers", None)
        if isinstance(buffers, dict) and len(buffers) > 0:
            last_step = max(buffers.keys())
            reader = copy.copy(storage_obj)
            reader.operator_step = last_step
            return reader.read("dataframe")
        return storage_obj.step().read("dataframe")

    def save_results_to_csv(pipeline, csv_workers=1, progress_every=500):
        try:
            df = _read_pipeline_df(pipeline)
        except Exception:
            return
        exporter = build_polymer_csv_exporter(csv_workers=csv_workers, progress_every=progress_every)
        stats = exporter.run(df)
        if isinstance(stats, dict):
            stats["rows"] = stats.get("nonempty", 0)
        return stats

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=str, default="")
    parser.add_argument("--entry-file", type=str, default="./data/MaterialExtractPipeline/material_papers.jsonl")
    parser.add_argument("--output-jsonl", type=str, default="")
    parser.add_argument("--max-chunk-len", type=int, default=32000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument("--use-batch", action="store_true", help="是否使用 BigQuery 批量推理")
    args = parser.parse_args()

    def _env_int(name, default):
        v = os.getenv(name)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except Exception:
            return default

    csv_workers = _env_int("POLYMER_CSV_WORKERS", 1)
    progress_every = _env_int("POLYMER_PROGRESS_EVERY", 500)

    def run_one_batch(base_dir, entry_file, output_jsonl, offset, limit):
        ef = entry_file
        if base_dir:
            out_jl = output_jsonl
            if not out_jl:
                if os.path.isdir(base_dir):
                    out_jl = os.path.join(base_dir, "polymer_input.jsonl")
                else:
                    out_jl = os.path.join(os.path.dirname(base_dir), "polymer_input.jsonl")
            if offset > 0 or (limit and limit > 0):
                b, e = os.path.splitext(out_jl)
                out_jl = f"{b}_{offset}_{limit}{e}"
            files = find_json_files(base_dir)
            files.sort()
            if offset > 0:
                files = files[offset:]
            if limit and limit > 0:
                files = files[:limit]
            if not files:
                return None
            prepare_input_data(files, out_jl)
            ef = out_jl
        return ef

    if args.batch_size > 0 and args.base_dir and not args.entry_file:
        all_files = find_json_files(args.base_dir)
        all_files.sort()
        total = len(all_files)
        start_g = args.offset
        end_g = total
        if args.limit > 0:
            end_g = min(start_g + args.limit, total)
        for st in range(start_g, end_g, args.batch_size):
            cur_lim = min(args.batch_size, end_g - st)
            ef = run_one_batch(args.base_dir, None, args.output_jsonl, st, cur_lim)
            if not ef:
                continue
            model = run_pipeline(ef, max_chunk_len=args.max_chunk_len, use_batch=args.use_batch)
            save_results_to_csv(model, csv_workers=csv_workers, progress_every=progress_every)
    else:
        ef = run_one_batch(args.base_dir, args.entry_file, args.output_jsonl, args.offset, args.limit)
        if ef:
            model = run_pipeline(ef, max_chunk_len=args.max_chunk_len, use_batch=args.use_batch)
            save_results_to_csv(model, csv_workers=csv_workers, progress_every=progress_every)
