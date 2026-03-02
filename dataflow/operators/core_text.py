from ..core import OperatorABC

class PandasOperator(OperatorABC):
    def __init__(self, transforms):
        self.transforms = transforms or []
    def run(self, storage):
        df = storage.read("dataframe")
        for fn in self.transforms:
            df = fn(df)
        storage.write(df)
        return df

