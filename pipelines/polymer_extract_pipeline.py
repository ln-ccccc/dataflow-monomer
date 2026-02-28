import argparse
import copy
import csv
import glob
import json
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from operators.general.chunked_generator import ChunkedPromptedGenerator
from prompts.polymer import PolymerExtractPrompt
from serving.api_openai_serving import APIOpenAICompatServing
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


class ExtractPolymer(BatchedPipelineABC):
    def __init__(self, entry_file_name: str, max_chunk_len=128000):
        super().__init__()
        self.storage = BatchedFileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="../polymer_output",
            cache_type="jsonl",
        )
        self.llm_serving = APIOpenAICompatServing(
            base_url=os.getenv("LLM_OPENAI_BASE_URL"),
            api_key=os.getenv("LLM_OPENAI_API_KEY"),
            model_name=os.getenv("LLM_OPENAI_MODEL", "gemini-2.5-pro"),
            max_workers=int(os.getenv("MONOMER_LLM_MAX_WORKERS", "100")),
            max_tokens=int(os.getenv("MONOMER_LLM_MAX_TOKENS", "64000")),
            timeout=int(os.getenv("LLM_OPENAI_TIMEOUT", "60")),
        )
        self.prompt = PolymerExtractPrompt()
        self.prompt_generator = ChunkedPromptedGenerator(
            llm_serving=self.llm_serving,
            prompt_template=self.prompt,
            json_schema=self.prompt.build_json_schema(),
            max_chunk_len=max_chunk_len
        )

        self.parse_result = PandasOperator([
            lambda df: df.assign(
                polymers=df["polymers_raw"].apply(self._parse_polymers)
            )
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
    def _load_env_from_setup_env(path="/share/lcc/setup_env.sh"):
        if not os.path.exists(path):
            return
        wanted = {
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GCP_PROJECT_ID",
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

    _load_env_from_setup_env()
    for upper, lower in [("HTTP_PROXY", "http_proxy"), ("HTTPS_PROXY", "https_proxy")]:
        if upper in os.environ and lower not in os.environ:
            os.environ[lower] = os.environ[upper]

    sys.path.append("/share/lcc/dataflow-dp")

    CSV_COLUMNS = [
        "polymer_name",
        "polymer_type",
        "components",
        "ratio_type",
        "ratio_values_text",
        "feed_ratio_text",
        "diamine_ratio",
        "dianhydride_ratio",
        "diisocyanate_ratio",
        "diol_ratio",
        "diacid_ratio",
        "mn_value",
        "mw_value",
        "pdi_value",
        "mw_unit",
        "test_method",
    ]

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

    def run_pipeline(input_file, max_chunk_len=32000):
        pipeline = ExtractPolymer(
            entry_file_name=input_file,
            max_chunk_len=max_chunk_len
        )
        pipeline.compile()
        pipeline.forward(batch_size=50, resume_from_last=True)
        return pipeline

    def _csv_has_data(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                header = f.readline()
                if not header:
                    return False
                return bool(f.readline())
        except Exception:
            return False

    def _write_csv(path, rows):
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            if rows:
                writer.writerows(rows)

    def _row_to_csv_data(m):
        return {
            "polymer_name": m.get("polymer_name"),
            "polymer_type": m.get("polymer_type"),
            "components": ";".join(m.get("components", [])),
            "ratio_type": m.get("ratio_type"),
            "ratio_values_text": m.get("ratio_values_text"),
            "feed_ratio_text": m.get("feed_ratio_text"),
            "diamine_ratio": m.get("diamine_ratio"),
            "dianhydride_ratio": m.get("dianhydride_ratio"),
            "diisocyanate_ratio": m.get("diisocyanate_ratio"),
            "diol_ratio": m.get("diol_ratio"),
            "diacid_ratio": m.get("diacid_ratio"),
            "mn_value": m.get("mn_value"),
            "mw_value": m.get("mw_value"),
            "pdi_value": m.get("pdi_value"),
            "mw_unit": m.get("mw_unit"),
            "test_method": m.get("test_method"),
        }

    def _write_one(row):
        file_path = getattr(row, "file_path", None)
        polymers = getattr(row, "polymers", None)
        if not file_path or not os.path.exists(file_path):
            return "skip", 0
        dir_path = os.path.dirname(file_path)
        csv_path = os.path.join(dir_path, "polymers.csv")
        if not polymers:
            if os.path.exists(csv_path) and _csv_has_data(csv_path):
                return "skip", 0
            _write_csv(csv_path, [])
            return "empty", 0
        csv_data = [_row_to_csv_data(m) for m in polymers]
        _write_csv(csv_path, csv_data)
        return "nonempty", len(csv_data)

    def _read_pipeline_df(pipeline):
        storage_obj = pipeline.storage
        buffers = getattr(storage_obj, "_buffers", None)
        if isinstance(buffers, dict) and len(buffers) > 0:
            last_step = max(buffers.keys())
            reader = copy.copy(storage_obj)
            reader.operator_step = last_step
            return reader.read("dataframe")
        return storage_obj.step().read("dataframe")

    def save_results_to_csv(pipeline, progress_every=500):
        try:
            df = _read_pipeline_df(pipeline)
        except Exception:
            return
        total = len(df)
        if total == 0:
            return
        count = 0
        empty_written = 0
        nonempty_written = 0
        for i, row in enumerate(df.itertuples(index=False), 1):
            status, rows_written = _write_one(row)
            if status == "empty":
                empty_written += 1
            elif status == "nonempty":
                nonempty_written += 1
                count += 1
            if progress_every and i % progress_every == 0:
                pass
        return {"nonempty": nonempty_written, "empty": empty_written, "rows": count}

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=str, default="")
    parser.add_argument("--entry-file", type=str, default="./data/MaterialExtractPipeline/material_papers.jsonl")
    parser.add_argument("--output-jsonl", type=str, default="")
    parser.add_argument("--max-chunk-len", type=int, default=32000)
    args = parser.parse_args()

    entry_file = args.entry_file
    if args.base_dir:
        base_dir = args.base_dir
        output_jsonl = args.output_jsonl
        if not output_jsonl:
            if os.path.isdir(base_dir):
                output_jsonl = os.path.join(base_dir, "polymer_input.jsonl")
            else:
                output_jsonl = os.path.join(os.path.dirname(base_dir), "polymer_input.jsonl")
        json_files = find_json_files(base_dir)
        prepare_input_data(json_files, output_jsonl)
        entry_file = output_jsonl

    model = run_pipeline(entry_file, max_chunk_len=args.max_chunk_len)
    save_results_to_csv(model)
