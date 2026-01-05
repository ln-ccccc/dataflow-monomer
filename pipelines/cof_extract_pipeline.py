import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import itertools

from operators.general.chunked_generator import ChunkedPromptedGenerator
from prompts.cof_extract import CofExtractPrompt

from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing
from dataflow.utils.storage import FileStorage, BatchedFileStorage

from dataflow.operators.core_text import PandasOperator
from utils.format_utils import safe_parse_json
from dataflow.pipeline import BatchedPipelineABC

class ExtractCOF(BatchedPipelineABC):
    def __init__(self, entry_file_name:str, max_chunk_len=128000):
        super().__init__()
        self.storage = BatchedFileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="../cof_output",
            cache_type="jsonl",
        )
        self.llm_serving = APIGoogleVertexAIServing(
            project=os.getenv("GCP_PROJECT_ID"),
            location='us-central1',
            model_name="gemini-2.5-pro",
            max_workers=100,
            max_tokens=64000,
        )
        self.prompt = CofExtractPrompt()
        self.prompt_generator = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt,
            json_schema=self.prompt.build_json_schema(),
            max_chunk_len=max_chunk_len
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
    model = ExtractCOF(entry_file_name="./data/CofExtractPipeline/cof_contents_short.jsonl", max_chunk_len=32000)
    model.compile()
    model.forward(batch_size=100, resume_from_last=True)
