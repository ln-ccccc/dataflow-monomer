import json
import os
import itertools
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from typing import Literal

from operators.general.chunked_generator import ChunkedPromptedGenerator
from dataflow.operators.core_text import PandasOperator
from prompts.alloy import AlloyNameExtractPrompt, AlloyInfoExtractPrompt, AlloyFigureClassifyPrompt
from prompts.materials import StructureInfoExtractPrompt, ComputationDetailExtractPrompt, PropertyExtractPrompt
from operators.alloy_extract.figure_classify import FigureClassifier
from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing

from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage, BatchedFileStorage
from utils.chartextraction.extract_figure_info import extract_figure_components
from utils.format_utils import safe_parse_json, safe_parse_json_and_get_key

from functools import partial
from dataflow.pipeline import BatchedPipelineABC
def extract_nested_fields_list_of_dicts(records, sublist_key="material_structures", keys=None):
    """
    返回 list[list[dict]]，每行对应 records 的每个 block，每个 block 是 material_structures 的 dict 列表

    Args:
        records (list[dict]): 每行的嵌套数据，如 structure_info 或 computation_detail
        sublist_key (str): 指向嵌套列表的 key
        keys (list[str]): 想提取的字段名

    Returns:
        list[list[dict]]: 外层 list 对应每个 block，内层 list 对应 block 内的 dict
    """
    if keys is None:
        keys = []

    result = []

    for block in records:
        sublist = block.get(sublist_key, [])
        block_result = []
        for item in sublist:
            item_dict = {k: item[k] for k in keys if k in item}
            block_result.append(item_dict)
        result.append(block_result)

    return result

class ExtractMaterial(BatchedPipelineABC):
    def _parse_and_flatten_column(self, df, column_name):
        return df.assign(
            **{
                column_name: df[column_name].apply(
                    lambda lst: list(
                        itertools.chain.from_iterable(
                            safe_parse_json(x, []) for x in lst
                        )
                    )
                )
            }
        )
    def _parse_column(self, df, column_name):
        return df.assign(
            **{
                column_name: df[column_name].apply(
                    lambda lst: [safe_parse_json(x, []) for x in lst]
                )
            }
        )    
    
    def __init__(self, entry_file_name:str, max_chunk_len=128000):
        super().__init__()
        self.storage = BatchedFileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="./material_google_test3_output",
            cache_type="json",
        )
        self.model_cache_dir = './dataflow_cache'
        # self.llm_serving = APILLMServing_request(
        #         api_url="http://123.129.219.111:3000/v1/chat/completions",
        #         model_name="gemini-2.5-flash",
        #         max_workers=100,
        # )

        self.llm_serving = APIGoogleVertexAIServing(
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location='us-central1',
            model_name="gemini-2.5-flash",
            max_workers=10,
            max_tokens=64000,
            use_batch=False,
            batch_wait=False,
            use_function_call=False,
            batch_dataset="material_test",
            csv_filename="material_test_prompt.csv",
            bq_csv_filename="material_test_prompt.csv",
        )

        self.prompt_1 = StructureInfoExtractPrompt()
        self.prompt_generator_1 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_1,
            json_schema=self.prompt_1.build_json_schema(),
            max_chunk_len=max_chunk_len
        )
        
        self.prompt_2 = ComputationDetailExtractPrompt()
        self.prompt_generator_2 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_2,
            json_schema=self.prompt_2.build_json_schema(),
            max_chunk_len=max_chunk_len,
            input_aux_keys = ["material_indexes"],
        )
        
        self.prompt_3 = PropertyExtractPrompt(mode='thermal')
        self.prompt_generator_3 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_3,
            json_schema=self.prompt_3.build_json_schema(),
            max_chunk_len=max_chunk_len,
            input_aux_keys = ["computation_indexes"],
        )
        
        self.prompt_4 = PropertyExtractPrompt(mode='mechanical')
        self.prompt_generator_4 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_4,
            json_schema=self.prompt_4.build_json_schema(),
            max_chunk_len=max_chunk_len,
            input_aux_keys = ["computation_indexes"],
        )
        
        self.prompt_5 = PropertyExtractPrompt(mode='electrical or magnetic')
        self.prompt_generator_5 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_5,
            json_schema=self.prompt_5.build_json_schema(),
            max_chunk_len=max_chunk_len,
            input_aux_keys = ["computation_indexes"],
        )
        
        self.parser_1 = PandasOperator([
            partial(self._parse_column, column_name="structure_info")
        ])

        self.parser_2 = PandasOperator([
            partial(self._parse_column, column_name="computation_detail")
        ])
        
        self.parser_3 = PandasOperator([
            partial(self._parse_column, column_name="thermal_properties")
        ])
        self.parser_4 = PandasOperator([
            partial(self._parse_column, column_name="mechanical_properties")
        ])
        self.parser_5 = PandasOperator([
            partial(self._parse_column, column_name="electrical_or_magnetic_properties")
        ])
        
        self.get_material_indexes = PandasOperator([
            lambda df: df.assign(
                material_indexes=df["structure_info"].apply(
                    extract_nested_fields_list_of_dicts,
                    sublist_key="material_structures",
                    keys=["composition", "lattice_parameter", "space_group", "number_of_atoms", "note"]
                )
            )
        ])

        self.get_computation_indexes = PandasOperator([
            lambda df: df.assign(
                computation_indexes=df["computation_detail"].apply(
                    extract_nested_fields_list_of_dicts,
                    sublist_key="material_structures",
                    keys=["composition", "space_group", "number_of_atoms", "K_points", "theoretical_calculation_method"]
                )
            )
        ])


    def forward(self):
        self.prompt_generator_1.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = "structure_info"
        )
        self.parser_1.run(storage = self.storage.step())
        self.get_material_indexes.run(storage = self.storage.step())
        self.prompt_generator_2.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = f"computation_detail",
        )
        self.parser_2.run(storage = self.storage.step())
        self.get_computation_indexes.run(storage = self.storage.step())
        self.prompt_generator_3.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = f"thermal_properties",
        )
        self.parser_3.run(storage = self.storage.step())
        self.prompt_generator_4.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = f"mechanical_properties",
        )
        self.parser_4.run(storage = self.storage.step())
        self.prompt_generator_5.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = f"electrical_or_magnetic_properties",
        )
        self.parser_5.run(storage = self.storage.step())


if __name__ == "__main__":
    model = ExtractMaterial(entry_file_name="./data/MaterialExtractPipeline/material_papers.jsonl", max_chunk_len=32000)
    model.compile()
    model.forward(batch_size=100, resume_from_last=True)
