import os
import copy
import pandas as pd

LCC_LOCAL_STORAGE = True

class DataFlowStorage:
    def __init__(self, first_entry_file_name, cache_path, cache_type="json"):
        self.first_entry_file_name = first_entry_file_name
        self.cache_path = cache_path
        self.cache_type = cache_type
        self.operator_step = -1
        self._buffers = {}
        os.makedirs(cache_path, exist_ok=True)

    def _get_cache_file_path(self, step):
        ext = {"json":"json", "jsonl":"jsonl", "csv":"csv"}.get(self.cache_type, "json")
        return os.path.join(self.cache_path, f"step_{step}.{ext}")

    def read(self, key="dataframe"):
        if key != "dataframe":
            raise KeyError(key)
        if self.operator_step in self._buffers:
            return self._buffers[self.operator_step]
        if self.operator_step == -1 and os.path.exists(self.first_entry_file_name):
            path = self.first_entry_file_name
            try:
                if path.endswith(".jsonl"):
                    df = pd.read_json(path, lines=True)
                else:
                    df = pd.read_json(path)
            except Exception:
                df = pd.read_json(path, lines=True)
            self._buffers[self.operator_step] = df
            return df
        path = self._get_cache_file_path(self.operator_step)
        if self.cache_type == "jsonl":
            return pd.read_json(path, lines=True)
        if self.cache_type == "json":
            return pd.read_json(path)
        if self.cache_type == "csv":
            return pd.read_csv(path)
        raise ValueError(self.cache_type)

    def write(self, data):
        if isinstance(data, list):
            if len(data) > 0 and isinstance(data[0], dict):
                dataframe = pd.DataFrame(data)
            else:
                raise ValueError("Unsupported list payload")
        elif isinstance(data, pd.DataFrame):
            dataframe = data
        else:
            raise ValueError("Unsupported payload")
        file_path = self._get_cache_file_path(self.operator_step + 1)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if self.cache_type == "json":
            dataframe.to_json(file_path, orient="records", force_ascii=False, indent=2)
        elif self.cache_type == "jsonl":
            dataframe.to_json(file_path, orient="records", lines=True, force_ascii=False)
        elif self.cache_type == "csv":
            dataframe.to_csv(file_path, index=False)
        self._buffers[self.operator_step + 1] = dataframe
        return file_path

    def step(self):
        view = copy.copy(self)
        view.operator_step = self.operator_step
        self.operator_step += 1
        return view

class FileStorage(DataFlowStorage):
    pass

class LazyFileStorage(DataFlowStorage):
    pass

