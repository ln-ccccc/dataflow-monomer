from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow import get_logger

from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC
import pandas as pd


@OPERATOR_REGISTRY.register()
class PaperDownloaderGenerator(OperatorABC):
    '''
    Paper Downloader Generator wraps a serving to download papers by query/DOI/PMID.
    '''
    def __init__(self,
                 paper_serving,
                 ):
        self.logger = get_logger()
        self.paper_serving = paper_serving

        self.logger.info(f"Initializing {self.__class__.__name__}...")
        self.logger.info(f"{self.__class__.__name__} initialized.")

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "PaperDownloaderGenerator 算子用于批量下载生物医学文献的 PDF 文件，"
                "支持三种模式：query（PubMed 检索式）、doi、pmid。\n\n"
                "输入参数：\n"
                "- paper_serving：下载服务对象，实现 LLMServingABC 接口（如 PaperDownloaderServing）\n"
                "- input_key：输入字段名（根据模式，包含检索式/DOI/PMID），默认 'id'\n"
                "- mode：'query' | 'doi' | 'pmid'，默认 'doi'\n"
                "- output_key：输出状态字段名，默认 'download_status'\n\n"
                "输出参数：\n"
                "- 在 DataFrame 中新增下载状态列（每行对应一个输入条目）\n"
                "- 返回输出字段名（如 'download_status'），可供后续算子引用"
            )
        elif lang == "en":
            return (
                "The PaperDownloaderGenerator operator downloads biomedical papers' PDFs in batch, "
                "supporting modes: query (PubMed query), doi, and pmid.\n\n"
                "Input Parameters:\n"
                "- paper_serving: a downloading serving implementing LLMServingABC (e.g., PaperDownloaderServing)\n"
                "- input_key: column name for inputs (query/DOI/PMID), default 'id'\n"
                "- mode: 'query' | 'doi' | 'pmid', default 'doi'\n"
                "- output_key: column name for output statuses, default 'download_status'\n\n"
                "Output Parameters:\n"
                "- DataFrame with a new status column (one status per input entry)\n"
                "- Returns the output field name (e.g., 'download_status') for subsequent operators"
            )
        else:
            return (
                "Downloads PDFs by query/DOI/PMID and writes statuses into the DataFrame."
            )

    def _validate_dataframe(self, dataframe: pd.DataFrame):
        """
        Validate that the dataframe has required columns and no conflicting columns.
        """
        required_keys = [self.input_key, self.input_mode_key]
        forbidden_keys = [self.output_key, self.output_pdf_path]

        missing = [k for k in required_keys if k not in dataframe.columns]
        conflict = [k for k in forbidden_keys if k in dataframe.columns]

        if missing:
            raise ValueError(f"Missing required column(s): {missing}")
        if conflict:
            self.logger.warning(f"The following column(s) already exist and will be overwritten: {conflict}")

    def run(self,
            storage: DataFlowStorage,
            input_key: str = "id",
            input_mode_key: str = "input_mode",
            output_key: str = "download_status",
            output_pdf_path: str = "pdf_path",
            output_download_dir: str = "./downloaded_papers",
            ):
        dataframe = storage.read("dataframe")
        self.input_key, self.input_mode_key, self.output_key, self.output_pdf_path, self.paper_serving.downloader.download_dir = input_key, input_mode_key, output_key, output_pdf_path, output_download_dir
        self._validate_dataframe(dataframe)

        dataframe[self.output_key] = None
        dataframe[self.output_pdf_path] = None

        # Group by mode and process batches
        valid_modes = {"query", "doi", "pmid"}
        for mode, group_df in dataframe.groupby(self.input_mode_key):
            mode_norm = str(mode).lower().strip()
            idx = group_df.index
            if mode_norm not in valid_modes:
                dataframe.loc[idx, self.output_key] = [f"error: unsupported mode {mode}"] * len(idx)
                continue

            values = [str(v) for v in group_df[self.input_key].tolist()]
            paths = self.paper_serving.generate_from_input(values, mode_norm)
            dataframe.loc[idx, self.output_pdf_path] = paths
            simple_statuses = ["ok" if (isinstance(p, str) and len(p) > 0) else "error" for p in paths]
            dataframe.loc[idx, self.output_key] = simple_statuses

        output_file = storage.write(dataframe)
        self.logger.info(f"Results saved to {output_file}")

        return [self.output_key, self.output_pdf_path]