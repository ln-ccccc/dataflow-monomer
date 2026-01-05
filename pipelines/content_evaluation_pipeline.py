import json
import os
import itertools
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from typing import Literal

# from operators.general.chunked_generator import ChunkedPromptedGenerator
from dataflow.operators.core_text import PandasOperator, PromptedGenerator
from prompts.evaluation import ContentOnlyEvaluationPrompt
from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing
from operators.evaluation.content_evaluation import ContentOnlyEvaluator
from dataflow.utils.storage import FileStorage, BatchedFileStorage
from dataflow.pipeline import BatchedPipelineABC
class ContentOnlyEvaluationPipeline(BatchedPipelineABC):
    def __init__(self, entry_file_name:str):
        super().__init__()
        self.storage = BatchedFileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="./evaluation_test_output2",
            cache_type="jsonl",
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

        self.evaluation = ContentOnlyEvaluator(
            llm_serving = self.llm_serving, 
            prompt_template=ContentOnlyEvaluationPrompt(),
            json_schema=ContentOnlyEvaluationPrompt().build_json_schema(),
            input_evaluation_keys = ["doi","structure_info","material_indexes","computation_detail","computation_indexes","thermal_properties","mechanical_properties","electrical_or_magnetic_properties"], # 待评估的提取字段列
        )
        
    def forward(self):
        self.evaluation.run(
            storage = self.storage.step(),
            input_key = "content",           # 原文列
            output_key = "content_evaluation"
        )


if __name__ == "__main__":
    model = ContentOnlyEvaluationPipeline(entry_file_name="./data/BenchmarkCompareEvaluationPipeline/content_extraction.jsonl")
    model.compile()
    model.forward(batch_size=100, resume_from_last=True)
