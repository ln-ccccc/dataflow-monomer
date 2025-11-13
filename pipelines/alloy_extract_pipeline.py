import json
import os
import itertools
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from typing import Literal

from operators.general.chunked_generator import ChunkedPromptedGenerator
from dataflow.operators.core_text import PandasOperator
from prompts.alloy import AlloyNameExtractPrompt, AlloyInfoExtractPrompt, AlloyFigureClassifyPrompt
from operators.alloy_extract.figure_classify import FigureClassifier

from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage
from utils.chartextraction.extract_figure_info import extract_figure_components
from utils.format_utils import safe_parse_json, safe_parse_json_and_get_key


class ExtractAlloy():
    def __init__(self, entry_file_name:str, mode: Literal['experimental','DFT','MD'], max_chunk_len=128000):
        self.mode=mode
        
        self.storage = FileStorage(
            first_entry_file_name=entry_file_name,
            cache_path="../alloy_output",
            cache_type="jsonl",
        )
        self.model_cache_dir = './dataflow_cache'
        self.llm_serving = APILLMServing_request(
                api_url="http://123.129.219.111:3000/v1/chat/completions",
                key_name_of_api_key="DF_API_KEY",
                model_name="gemini-2.5-pro",
                max_workers=200,
        )
        self.prompt_1 = AlloyNameExtractPrompt()
        self.prompt_generator_1 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_1,
            json_schema=self.prompt_1.build_json_schema(),
            max_chunk_len=max_chunk_len
        )
        
        self.prompt_2 = AlloyInfoExtractPrompt()
        self.prompt_generator_2 = ChunkedPromptedGenerator(
            llm_serving = self.llm_serving, 
            prompt_template=self.prompt_2,
            json_schema=self.prompt_2.build_json_schema(mode=mode),
            max_chunk_len=max_chunk_len,
        )
        
        self.figure_classify_prompt = AlloyFigureClassifyPrompt()
        self.figure_classifier = FigureClassifier(
            llm_serving = self.llm_serving,
            prompt_template = self.figure_classify_prompt,
            classes = ["Alloy Composition", "EDS line scanning analysis", "strain curve", "Bright-ﬁeld TEM image and SADPs", "Magnetization curve", "Other"]
        )
        
        self.parse_alloys = PandasOperator([
            lambda df: df.assign(
                alloys=df["alloys"].apply(
                    lambda lst: list(
                        itertools.chain.from_iterable(
                            [safe_parse_json_and_get_key(x, "alloys", []) for x in lst]
                        )
                    )
                )
            )
        ])
        
        self.parse_alloys_info = PandasOperator([
            lambda df: df.assign(
                **{
                    f"alloys_{self.mode}_info": df[f"alloys_{self.mode}_info"].apply(
                        lambda lst: list(
                            itertools.chain.from_iterable(
                                [safe_parse_json_and_get_key(x, "alloys", []) for x in lst]
                            )
                        )
                    )
                }
            )
        ])
        
        # 提取alloys中每一项的alloy_name, 并去重，然后得到一个列表
        self.get_alloy_names = PandasOperator(
            [
                lambda df: df.assign(
                    materials_name_list=df["alloys"].apply(
                        lambda alloys: list(
                            {
                                alloy.get("alloy_name", "").strip()
                                for alloy in alloys
                                if alloy.get("alloy_name", "").strip() != ""
                            }
                        )
                    )
                ),
            ]
        )

    def forward(self):
        self.figure_classifier.run(
            storage = self.storage.step(),
            input_key = "figure_components",
            input_caption_key="caption",
            output_class_key="figure_class"
        )
        self.prompt_generator_1.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = "alloys"
        )
        self.parse_alloys.run(storage = self.storage.step())
        self.get_alloy_names.run(storage = self.storage.step())
        self.prompt_generator_2.run(
            storage = self.storage.step(),
            input_key = "content",
            output_key = f"alloys_{self.mode}_info",
            input_aux_keys = ["materials_name_list"]
        )
        self.parse_alloys_info.run(storage = self.storage.step())


if __name__ == "__main__":
    # This is the entry point for the pipeline
    # with open("alloy_papers.jsonl", "w") as f:
    #     for root, _, files in os.walk("../Alloy-test"):
    #         for fname in files:
    #             if fname.lower().endswith(".json"):
    #                 paper = os.path.join(root, fname)
    #                 paper_data = json.load(open(paper, "r"))
    #                 f.write(json.dumps({"doi":paper_data["token"],
    #                                     "content": paper_data["content"],
    #                                     "figure_components": extract_figure_components(paper_data)}) + "\n")
    
    model = ExtractAlloy(entry_file_name="./data/AlloyExtractPipeline/alloy_papers_short.jsonl", mode='MD', max_chunk_len=3200)
    model.forward()
