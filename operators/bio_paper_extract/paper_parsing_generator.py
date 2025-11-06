import os
import time
import uuid
import requests
from pathlib import Path
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple

from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow import get_logger

from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC

@OPERATOR_REGISTRY.register()
class PaperParsingGenerator(OperatorABC):
    """
    Paper Parsing Generator wraps a remote parser service to parse PDFs.
    - Reads PDF file paths from dataframe (default column: "pdf_path")
    - Parses PDFs in parallel using multi-threading (configurable workers)
    - Exports markdown format only for each paper
    - Updates dataframe with markdown file paths (maintains original order)
    """
    def __init__(self, host: str = "http://192.168.192.225:40001", max_workers: int = 4):
        self.logger = get_logger()
        self.host = host
        self.max_workers = max_workers
        self.logger.info(f"Initializing {self.__class__.__name__} with host={host} and max_workers={max_workers}...")
        self.logger.info(f"{self.__class__.__name__} initialized.")

    # --------------- HTTP helpers ---------------
    def _upload_pdf(self, host: str, file_path: str):
        token = uuid.uuid4().hex
        url_upload = f"{host}/trigger-file-async"
        try:
            with open(file_path, 'rb') as f:
                files = {'file': f}
                data = {'token': token, "sync": True}
                semantic_cfg = {
                    'textual': 3,
                    'chart': 1,
                    "table": True,
                    "molecule": True,
                    "equation": True,
                    "figure": False,
                    "expression": True,
                }
                extra_cfg = {'lang': 'en'}
                data.update(semantic_cfg)
                data.update(extra_cfg)
                response = requests.post(url_upload, files=files, data=data)
                if response.status_code == 200:
                    self.logger.info(f"Successfully uploaded {file_path}, token: {token}")
                    return token
                else:
                    self.logger.error(f"Upload failed: {response.text}")
                    return None
        except Exception as e:
            self.logger.error(f"Upload exception: {str(e)}")
            return None

    def _wait_for_result(self, host: str, token: str, timeout: int = 100):
        url_result = f"{host}/get-result"
        start_time = time.time()
        headers = {"Content-Type": "application/json"}
        while time.time() - start_time < timeout:
            try:
                response = requests.post(url_result, headers=headers, json={"token": token})
                if response.status_code == 200 and response.json().get("status") == "success":
                    self.logger.info(f"Parsing completed, token: {token}")
                    return True
                elif response.json().get("status") == "processing":
                    self.logger.debug(f"Processing... token: {token}")
                    time.sleep(2)
                else:
                    self.logger.error(f"Parsing failed: {response.text}")
                    return False
            except Exception as e:
                self.logger.warning(f"Query result exception: {str(e)}")
                time.sleep(2)
        self.logger.error(f"Timeout waiting for parsing result, token: {token}")
        return False

    def _export_formats(self, host: str, token: str, output_dir: str, name: str):
        """Export only markdown format from the parsing result."""
        result_url = f"{host}/get-formatted"
        fmt = 'markdown'
        try:
            semantic_cfg = {
                'textual': fmt,
                'chart': 'empty',
                'table': fmt,
                'molecule': fmt,
                'equation': fmt,
                'figure': 'empty',
                'expression': fmt,
            }
            data = {'token': token, **semantic_cfg}
            response = requests.post(result_url, json=data)
            if response.status_code == 200:
                content = response.json().get('content', '')
                output_path = os.path.join(output_dir, f'{name}_parser.md')
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.logger.info(f"Successfully exported Markdown: {output_path}")
            else:
                self.logger.error(f"Failed to get Markdown: {response.text}")
        except Exception as e:
            self.logger.error(f"Export Markdown exception: {str(e)}")





    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "PaperParsingGenerator 算子用于从 dataframe 读取 PDF 文件路径，并为每篇论文解析导出 Markdown 格式。\n"
                "支持多线程并行处理，保持结果顺序不变。\n\n"
                "输入参数：\n"
                "- input_pdf_path_key：dataframe 中存储 PDF 路径的列名（默认 'pdf_path'）\n"
                "- output_md_path：dataframe 中存储输出 markdown 路径的列名（默认 'md_path'）\n"
                "- output_dir：解析结果输出根目录（默认 './parser'）\n"
                "- max_workers：并行处理的最大线程数（默认 4）\n\n"
                "输出：在 output_dir 下直接写出所有 Markdown 文件，更新 dataframe"
            )
        else:
            return (
                "Parses PDFs (read from dataframe) and exports markdown format only per paper. "
                "Supports multi-threaded parallel processing while maintaining result order."
            )

    def _validate_dataframe(self, dataframe: pd.DataFrame):
        """
        Validate that the dataframe has required columns and no conflicting columns.
        """
        required_keys = [self.input_pdf_path_key]
        forbidden_keys = [self.output_md_path]

        missing = [k for k in required_keys if k not in dataframe.columns]
        conflict = [k for k in forbidden_keys if k in dataframe.columns]

        if missing:
            raise ValueError(f"Missing required column(s): {missing}")
        if conflict:
            self.logger.warning(f"The following column(s) already exist and will be overwritten: {conflict}")

    def _parse_single_pdf(self, pdf_path: str, output_dir: str) -> str:
        """
        Parse a single PDF file and return the markdown file path.
        
        Args:
            pdf_path: Path to the PDF file
            output_dir: Directory to store the parsed output
            
        Returns:
            Path to the generated markdown file, or empty string if failed
        """
        if not pdf_path or not isinstance(pdf_path, str):
            self.logger.warning(f"Invalid PDF path: {pdf_path}")
            return ""
        
        pdf_path_obj = Path(pdf_path)
        if not pdf_path_obj.exists():
            self.logger.warning(f"PDF file not found: {pdf_path}")
            return ""
        
        try:
            self.logger.info(f"Parsing PDF: {pdf_path}")
            name = pdf_path_obj.stem
            
            # Upload PDF and get token
            token = self._upload_pdf(self.host, str(pdf_path_obj))
            if not token:
                self.logger.error(f"Failed to upload PDF: {pdf_path}")
                return ""
            
            # Wait for parsing result
            if not self._wait_for_result(self.host, token):
                self.logger.error(f"Failed to parse PDF: {pdf_path}")
                return ""
            
            # Export formats directly to output directory (no subfolder)
            self._export_formats(self.host, token, output_dir, name)
            
            # Return markdown file path
            md_path = Path(output_dir) / f"{name}_parser.md"
            if md_path.exists():
                self.logger.info(f"Successfully parsed PDF to: {md_path}")
                return str(md_path)
            else:
                self.logger.warning(f"Markdown file not generated: {md_path}")
                return ""
                
        except Exception as e:
            self.logger.error(f"Error parsing PDF {pdf_path}: {e}")
            return ""

    def _process_row(self, idx: int, pdf_path: str, output_dir: str) -> Tuple[int, str]:
        """
        Process a single row (wrapper for threading).
        
        Args:
            idx: Row index
            pdf_path: Path to the PDF file
            output_dir: Directory to store the parsed output
            
        Returns:
            Tuple of (index, markdown_path)
        """
        if not pdf_path or not isinstance(pdf_path, str):
            self.logger.warning(f"Row {idx}: Invalid or missing PDF path: {pdf_path}")
            return (idx, "")
        
        md_path = self._parse_single_pdf(pdf_path, output_dir)
        return (idx, md_path)

    def run(
        self,
        storage: DataFlowStorage,
        input_pdf_path_key: str = "pdf_path",
        output_md_path: str = "md_path",
        output_dir: str = "./parser",
    ):
        self.input_pdf_path_key = input_pdf_path_key
        self.output_md_path = output_md_path
        
        # Ensure output directory exists and is absolute
        output_dir_abs = str(Path(output_dir).resolve())
        Path(output_dir_abs).mkdir(parents=True, exist_ok=True)
        
        self.logger.info(
            f"Running {self.__class__.__name__} with output_dir={output_dir_abs}, max_workers={self.max_workers}"
        )

        # Read dataframe and validate
        dataframe = storage.read("dataframe")
        self._validate_dataframe(dataframe)

        # Initialize output column
        if self.output_md_path not in dataframe.columns:
            dataframe[self.output_md_path] = None

        # Prepare tasks for parallel processing
        tasks = []
        for idx, row in dataframe.iterrows():
            pdf_path = row.get(self.input_pdf_path_key)
            tasks.append((idx, pdf_path))
        
        # Process PDFs in parallel with ThreadPoolExecutor, maintaining order
        results = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_idx = {
                executor.submit(self._process_row, idx, pdf_path, output_dir_abs): idx
                for idx, pdf_path in tasks
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_idx):
                try:
                    idx, md_path = future.result()
                    results[idx] = md_path
                except Exception as e:
                    idx = future_to_idx[future]
                    self.logger.error(f"Error processing row {idx}: {e}")
                    results[idx] = ""
        
        # Update dataframe in original order
        for idx in dataframe.index:
            if idx in results:
                dataframe.loc[idx, self.output_md_path] = results[idx]
            else:
                dataframe.loc[idx, self.output_md_path] = ""

        # Save updated dataframe
        storage.write(dataframe)
        
        # Log summary
        success_count = dataframe[self.output_md_path].astype(bool).sum()
        total_count = len(dataframe)
        self.logger.info(
            f"Parsing complete. Successfully parsed {success_count}/{total_count} PDFs."
        )
        
        return [self.output_md_path]


