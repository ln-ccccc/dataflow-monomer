import json
import os
import itertools
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from typing import Literal

# from operators.general.chunked_generator import ChunkedPromptedGenerator
from dataflow.operators.core_text import PandasOperator, PromptedGenerator
from prompts.evaluation import BenchmarkCompareEvaluationPrompt
from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing
from operators.evaluation.benchmark_compare import BenchmarkEvaluator
from dataflow.utils.storage import FileStorage


class EvaluationPipeline():
    def __init__(self, entry_file_name:str, max_chunk_len=128000):
        self.storage = FileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="./evaluation_test_output",
            cache_type="json",
        )

        self.llm_serving = APIGoogleVertexAIServing(
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location='us-central1',
            model_name="gemini-2.5-flash",
            max_workers=10,
            max_tokens=64000,
            use_batch=False,
            batch_wait=False,
            use_function_call=False,
            batch_dataset="evaluation_test",
            csv_filename="evaluation_test_prompt.csv",
            bq_csv_filename="evaluation_test_prompt.csv",
        )

        self.evaluation = BenchmarkEvaluator(
            llm_serving = self.llm_serving, 
            prompt_template=BenchmarkCompareEvaluationPrompt(),
            json_schema=BenchmarkCompareEvaluationPrompt().build_json_schema(),
        )
        
    def forward(self):
        self.evaluation.run(
            storage = self.storage.step(),
            benchmark_path = "./data/BenchmarkCompareEvaluationPipeline/benchmark.json",
            output_key = "evaluation_results"
        )


if __name__ == "__main__":
    model = EvaluationPipeline(entry_file_name="./data/BenchmarkCompareEvaluationPipeline/extraction.json")
    model.forward()
