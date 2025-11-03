import os
import pandas as pd
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from dataflow.utils.storage import FileStorage
from serving.paper_downloader_serving import PaperDownloaderServing
from dataflow.serving.api_llm_serving_request import APILLMServing_request
from operators.bio_paper_extract.paper_downloader_generator import PaperDownloaderGenerator
from operators.bio_paper_extract.paper_info_extract_generator import PaperInfoExtractGenerator
from operators.bio_paper_extract.paper_parsing_generator import PaperParsingGenerator
from prompts.bio_paper_extract import (
    BioPaperInfoExtractPrompt,
    BioPaperInfoExtractPrompt5,
    BioPaperInfoExtractPrompt6,
    BioPaperInfoExtractPrompt7,
    BioPaperInfoExtractPrompt8,
    BioPaperInfoExtractPrompt10,
)


class BioPaperExtract_APIPipeline:

    def __init__(self):
        self.storage = FileStorage(
            first_entry_file_name="./data/BioPaperExtractPipeline/example.jsonl",
            cache_path="./cache",
            file_name_prefix="bio_paper_extract",
            cache_type="json",
        )

        self.llm_serving = APILLMServing_request(
            api_url="http://123.129.219.111:3000/v1/chat/completions",
            model_name="gemini-2.5-pro",
            max_workers=200,
        )

        self.paper_serving = PaperDownloaderServing(
            unpaywall_email="zhangjun@dp.tech",
            entrez_email="zhangjun@dp.tech",
            entrez_api_key="6882518fa7c420140b98817f571ef1d8ea08",
        )

        self.downloader_op = PaperDownloaderGenerator(
            paper_serving=self.paper_serving,
        )

        self.parser_op = PaperParsingGenerator(
            host = "http://101.126.82.63:40001", # Uniparser server host
            max_workers=5,
        )

        self.info_extract_op = PaperInfoExtractGenerator(
            llm_serving=self.llm_serving,
            prompt_template=BioPaperInfoExtractPrompt6(),
        )

    def forward(self):
        # Step 1: Download papers
        self.downloader_op.run(
            storage=self.storage.step(),
            input_key="id",
            input_mode_key="input_mode",
            output_key="download_status",
            output_pdf_path="pdf_path",
            output_download_dir="./output/downloaded_papers",
        )

        # Step 2: Parse PDFs to markdown
        # Reads pdf_path from dataframe and outputs md_path
        # Uses multi-threading for parallel processing
        self.parser_op.run(
            storage=self.storage.step(),
            input_pdf_path_key="pdf_path",
            output_md_path="md_path",
            output_dir=".output/parsed_markdown",
        )

        # Step 3: Extract structured info from markdowns using LLM
        # Reads md_path from dataframe and outputs info_json_path
        self.info_extract_op.run(
            storage=self.storage.step(),
            input_paper_id_key="id",
            input_markdown_path_key="md_path",
            output_dir="./output/extract_json",
            output_json_path_key="info_json_path",
        )

if __name__ == "__main__":
    pipeline = BioPaperExtract_APIPipeline()
    pipeline.forward()