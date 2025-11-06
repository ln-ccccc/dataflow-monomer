import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import itertools

from operators.general.chunked_generator import ChunkedPromptedGenerator
from prompts.cof_extract import CofExtractPrompt

from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage

from dataflow.operators.core_text import PandasOperator
from utils.format_utils import safe_parse_json

class ExtractCOF():
    def __init__(self):
        self.storage = FileStorage(
            first_entry_file_name="./data/CofExtractPipeline/cof_contents_short.jsonl",
            cache_path="../cof_output",
            cache_type="jsonl",
        )
        self.llm_serving = APILLMServing_request(
                api_url="http://123.129.219.111:3000/v1/chat/completions",
                key_name_of_api_key="DF_API_KEY",
                model_name="gemini-2.5-pro",
                max_workers=200,
        )
        self.prompt = CofExtractPrompt()
        self.prompt_generator = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt,
            json_schema=self.prompt.build_json_schema(),
        )
        
        self.parse_result = PandasOperator([
            lambda df: df.assign(
                result=df["result"].apply(
                    lambda lst: [safe_parse_json(x, {}) for x in lst]
                    )
                )
        ])

    def forward(self):
        self.prompt_generator.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = "result"
        )
        self.parse_result.run(
            storage = self.storage.step(),
        )


if __name__ == "__main__":
    # This is the entry point for the pipeline
    model = ExtractCOF()
    model.forward()
