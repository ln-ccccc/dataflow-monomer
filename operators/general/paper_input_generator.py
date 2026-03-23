import json
import os
from typing import Iterable, Iterator, List, Optional

import pandas as pd

from dataflow.core import OperatorABC
from dataflow.utils.registry import OPERATOR_REGISTRY


@OPERATOR_REGISTRY.register()
class PaperJsonInputGenerator(OperatorABC):
    def __init__(
        self,
        paper_root: Optional[str] = None,
        marker: str = "selected_polyimide_papers",
        exclude_basenames: Optional[List[str]] = None,
        content_key: str = "content",
        token_key: str = "token",
    ):
        self.paper_root = paper_root if paper_root is not None else os.getenv("LCC_PAPER_ROOT", "../paper")
        self.marker = marker
        self.exclude_basenames = set(exclude_basenames or [])
        self.content_key = content_key
        self.token_key = token_key

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return "扫描论文 JSON，生成 pipeline 输入所需的 jsonl 或 dataframe。"
        return "Scan paper JSON files and build input jsonl/dataframe for pipelines."

    def find_json_files(self, base_path: str) -> List[str]:
        import glob
        if os.path.isfile(base_path) and base_path.lower().endswith(".json"):
            paths = [base_path]
        else:
            paths = glob.glob(os.path.join(base_path, "**", "*.json"), recursive=True)
        if not self.exclude_basenames:
            return paths
        return [p for p in paths if os.path.basename(p) not in self.exclude_basenames]

    def iter_json_files(self, base_path: str) -> Iterator[str]:
        if os.path.isfile(base_path) and base_path.lower().endswith(".json"):
            basename = os.path.basename(base_path)
            if basename and basename in self.exclude_basenames:
                return
            yield base_path
            return
        for root, _, files in os.walk(base_path):
            for name in files:
                if not name.lower().endswith(".json"):
                    continue
                if name in self.exclude_basenames:
                    continue
                yield os.path.join(root, name)

    def extract_doi_from_dir(self, dir_path: str) -> str:
        try:
            abs_dir = os.path.abspath(dir_path)
            parts = abs_dir.replace("\\", "/").split("/")
            if self.marker in parts:
                idx = parts.index(self.marker)
                if idx < len(parts) - 1:
                    doi_candidate = "/".join([p for p in parts[idx + 1:] if p])
                    if doi_candidate:
                        return doi_candidate
        except Exception:
            pass
        try:
            root = self.paper_root
            if root:
                root_abs = os.path.abspath(root)
                dir_abs = os.path.abspath(dir_path)
                if dir_abs.startswith(root_abs + os.sep):
                    rel = os.path.relpath(dir_abs, root_abs).replace("\\", "/").strip("/")
                    if rel:
                        return rel
        except Exception:
            pass
        return os.path.basename(dir_path)

    def build_records(self, json_files: Iterable[str]) -> List[dict]:
        records = []
        for file_path in json_files:
            try:
                with open(file_path, "r", encoding="utf-8") as fin:
                    content_json = json.load(fin)
                if not isinstance(content_json, dict):
                    continue
                text_content = content_json.get(self.content_key, "")
                if not text_content:
                    continue
                dir_path = os.path.dirname(file_path)
                doi_candidate = self.extract_doi_from_dir(dir_path)
                records.append({
                    "file_path": file_path,
                    "content": text_content,
                    "doi_hint": content_json.get(self.token_key, ""),
                    "extracted_doi": doi_candidate,
                })
            except Exception:
                continue
        return records

    def write_jsonl(self, records: List[dict], output_jsonl: str) -> int:
        os.makedirs(os.path.dirname(output_jsonl) or ".", exist_ok=True)
        count = 0
        with open(output_jsonl, "w", encoding="utf-8") as f:
            for rec in records:
                try:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    count += 1
                except Exception:
                    continue
        return count

    def write_jsonl_from_files(self, json_files: Iterable[str], output_jsonl: str) -> int:
        os.makedirs(os.path.dirname(output_jsonl) or ".", exist_ok=True)
        count = 0
        with open(output_jsonl, "w", encoding="utf-8") as f:
            for file_path in json_files:
                try:
                    with open(file_path, "r", encoding="utf-8") as fin:
                        content_json = json.load(fin)
                    if not isinstance(content_json, dict):
                        continue
                    text_content = content_json.get(self.content_key, "")
                    if not text_content:
                        continue
                    dir_path = os.path.dirname(file_path)
                    doi_candidate = self.extract_doi_from_dir(dir_path)
                    rec = {
                        "file_path": file_path,
                        "content": text_content,
                        "doi_hint": content_json.get(self.token_key, ""),
                        "extracted_doi": doi_candidate,
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    count += 1
                except Exception:
                    continue
        return count

    def run(
        self,
        base_dir: Optional[str] = None,
        json_files: Optional[List[str]] = None,
        output_jsonl: Optional[str] = None,
        return_dataframe: bool = False,
    ):
        files = list(json_files or [])
        if base_dir:
            files = self.find_json_files(base_dir)
        records = self.build_records(files)
        if output_jsonl:
            self.write_jsonl(records, output_jsonl)
        if return_dataframe:
            return pd.DataFrame(records)
        return len(records)
