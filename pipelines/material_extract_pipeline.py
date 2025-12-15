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
from dataflow.utils.storage import FileStorage
from utils.chartextraction.extract_figure_info import extract_figure_components
from utils.format_utils import safe_parse_json, safe_parse_json_and_get_key

from functools import partial

def extract_material_indexes(materials, keys):
    """
    从 materials 列表中提取相关信息，返回 list[list[dict]]
    """
    material_indexes = []
    for material in materials:
        mat_list = []
        for m in material:
            mat_dict = {}
            for key in keys:
                if key in m:
                    mat_dict[key] = m[key]
            mat_list.append(mat_dict)
        material_indexes.append(mat_list)
    return material_indexes


class ExtractMaterial():
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
        self.storage = FileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="../material_google_test_output",
            cache_type="jsonl",
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
        )
        
        self.prompt_3 = PropertyExtractPrompt(mode='thermal')
        self.prompt_generator_3 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_3,
            json_schema=self.prompt_3.build_json_schema(),
            max_chunk_len=max_chunk_len,
        )
        
        self.prompt_4 = PropertyExtractPrompt(mode='mechanical')
        self.prompt_generator_4 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_4,
            json_schema=self.prompt_4.build_json_schema(),
            max_chunk_len=max_chunk_len,
        )
        
        self.prompt_5 = PropertyExtractPrompt(mode='electrical or magnetic')
        self.prompt_generator_5 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_5,
            json_schema=self.prompt_5.build_json_schema(),
            max_chunk_len=max_chunk_len,
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
        
        self.get_material_indexes = PandasOperator(
            [
                lambda df: df.assign(
                    material_indexes=df["structure_info"].apply(
                        extract_material_indexes,
                        args=(["composition", "lattice_parameter", "space_group", "number_of_atoms", "note"],)
                    )
                ),
            ]
        )
        
        self.get_computation_indexes = PandasOperator(
            [
                lambda df: df.assign(
                    computation_indexes=df["computation_detail"].apply(
                        extract_material_indexes,
                        args=(["composition", "space_group", "number_of_atoms", "K_points", "theoretical_calculation_method"],)
                    )
                ),
            ]
        )

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
            input_aux_keys = ["material_indexes"],
        )
        self.parser_2.run(storage = self.storage.step())
        self.get_computation_indexes.run(storage = self.storage.step())
        self.prompt_generator_3.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = f"thermal_properties",
            input_aux_keys = ["computation_indexes"],
        )
        self.parser_3.run(storage = self.storage.step())
        self.prompt_generator_4.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = f"mechanical_properties",
            input_aux_keys = ["computation_indexes"],
        )
        self.parser_4.run(storage = self.storage.step())
        self.prompt_generator_5.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = f"electrical_or_magnetic_properties",
            input_aux_keys = ["computation_indexes"],
        )
        self.parser_5.run(storage = self.storage.step())


if __name__ == "__main__":
    # # This is the entry point for the pipeline
    # with open("data/MaterialExtractPipeline/material_papers.jsonl", "w") as f:
    #     for root, _, files in os.walk("../material_test_pdfs"):
    #         for fname in files:
    #             if fname.lower().endswith(".json"):
    #                 paper = os.path.join(root, fname)
    #                 paper_data = json.load(open(paper, "r"))
    #                 f.write(json.dumps({"doi":paper_data["token"],
    #                                     "content": paper_data["content"],
    #                                     "figure_components": extract_figure_components(paper_data)}) + "\n")
    
    model = ExtractMaterial(entry_file_name="/share/djw/dataflow-dp/data/MaterialExtractPipeline/material_papers.jsonl", max_chunk_len=32000)
    model.forward()
