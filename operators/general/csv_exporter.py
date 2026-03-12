import os
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from dataflow.core import OperatorABC
from dataflow.utils.registry import OPERATOR_REGISTRY


@OPERATOR_REGISTRY.register()
class CsvExportOperator(OperatorABC):
    def __init__(
        self,
        columns: List[str],
        csv_path_resolver: Callable[[object, Optional[str]], str],
        row_expander: Callable[[object], List[Dict]],
        csv_workers: int = 1,
        progress_every: int = 500,
        skip_if_has_data: bool = True,
        write_empty_file: bool = True,
    ):
        self.columns = list(columns or [])
        self.csv_path_resolver = csv_path_resolver
        self.row_expander = row_expander
        self.csv_workers = max(1, int(csv_workers or 1))
        self.progress_every = int(progress_every or 0)
        self.skip_if_has_data = bool(skip_if_has_data)
        self.write_empty_file = bool(write_empty_file)

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return "将 dataframe 按行展开并写入到各自目录下的 CSV 文件。"
        return "Expand dataframe rows and export CSV files."

    def _csv_has_data(self, path: str) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as f:
                header = f.readline()
                if not header:
                    return False
                return bool(f.readline())
        except Exception:
            return False

    def _write_csv(self, path: str, rows: List[Dict]):
        import csv
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.columns)
            writer.writeheader()
            if rows:
                writer.writerows(rows)

    def _write_one(self, row: object, output_root: Optional[str], path_locks: Dict[str, threading.Lock], locks_lock: threading.Lock) -> Tuple[str, int]:
        csv_path = self.csv_path_resolver(row, output_root)
        if not csv_path:
            return "skip", 0
        expanded = self.row_expander(row) or []
        if not expanded and self.skip_if_has_data and os.path.exists(csv_path) and self._csv_has_data(csv_path):
            return "skip", 0
        with locks_lock:
            lock = path_locks.get(csv_path)
            if lock is None:
                lock = threading.Lock()
                path_locks[csv_path] = lock
        with lock:
            if not expanded:
                if self.write_empty_file:
                    self._write_csv(csv_path, [])
                    return "empty", 0
                return "skip", 0
            self._write_csv(csv_path, expanded)
            return "nonempty", len(expanded)

    def run(self, dataframe_or_storage, output_root: Optional[str] = None):
        if isinstance(dataframe_or_storage, pd.DataFrame):
            df = dataframe_or_storage
        else:
            df = dataframe_or_storage.read("dataframe")
        total = len(df)
        if total == 0:
            return {"nonempty": 0, "empty": 0}
        path_locks = {}
        locks_lock = threading.Lock()
        empty_written = 0
        nonempty_written = 0

        def handle_status(status: str):
            nonlocal empty_written, nonempty_written
            if status == "empty":
                empty_written += 1
            elif status == "nonempty":
                nonempty_written += 1

        if self.csv_workers <= 1 or total <= 1:
            for i, row in enumerate(df.itertuples(index=False), 1):
                status, _ = self._write_one(row, output_root, path_locks, locks_lock)
                handle_status(status)
                if self.progress_every and i % self.progress_every == 0:
                    pass
            return {"nonempty": nonempty_written, "empty": empty_written}

        it = df.itertuples(index=False)
        futures = set()
        max_inflight = max(self.csv_workers * 2, 4)

        def submit_next(executor):
            try:
                row = next(it)
            except StopIteration:
                return False
            futures.add(executor.submit(self._write_one, row, output_root, path_locks, locks_lock))
            return True

        with ThreadPoolExecutor(max_workers=self.csv_workers) as executor:
            for _ in range(max_inflight):
                if not submit_next(executor):
                    break
            processed = 0
            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for fut in done:
                    status, _ = fut.result()
                    handle_status(status)
                    processed += 1
                    if self.progress_every and processed % self.progress_every == 0:
                        pass
                    submit_next(executor)
        return {"nonempty": nonempty_written, "empty": empty_written}
