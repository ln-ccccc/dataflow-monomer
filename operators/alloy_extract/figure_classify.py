import pandas as pd
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow import get_logger

from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC
from dataflow.core import LLMServingABC

import tiktoken

from prompts.alloy import AlloyFigureClassifyPrompt
from dataflow.core.prompt import prompt_restrict

from utils.format_utils import safe_parse_json

@prompt_restrict(AlloyFigureClassifyPrompt)
@OPERATOR_REGISTRY.register()
class FigureClassifier(OperatorABC):
    """
    基于Caption对图片进行分类的算子。
    需要给定类别。
    会对input_key原地写入类别。
    """

    def __init__(
        self,
        llm_serving: LLMServingABC,
        prompt_template: AlloyFigureClassifyPrompt,
        classes: list[str],
        input_caption_key: str = "caption",
    ):
        self.logger = get_logger()
        self.llm_serving = llm_serving
        self.prompt_template = prompt_template
        self.classes = classes
        self.input_caption_key = input_caption_key

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "基于Caption对图片进行分类的算子。需要给定类别。会对input_key原地写入类别。"
            )
        else:
            return (
                "An operator that classifies images based on captions. Requires specified classes. "
                "Will write the class back to input_key in place."
            )

    def run(
        self,
        storage: DataFlowStorage,
        input_key: str = "figure_components",
        output_class_key: str = "class",
    ):
        dataframe = storage.read("dataframe")
        all_llm_inputs = []
        
        for i, row in dataframe.iterrows():
            figure_components = row[input_key]
            for component in figure_components:
                caption = component.get(self.input_caption_key, "")
                llm_inputs = self.prompt_template.build_prompt(
                    caption=caption,
                    classes=self.classes,
                )
            
                all_llm_inputs.append(llm_inputs)

        all_responses = self.llm_serving.generate_from_input(all_llm_inputs)
        
        # 将类别写回dataframe
        for i, row in dataframe.iterrows():
            figure_components = row[input_key]
            for component in figure_components:
                classified_class = all_responses.pop(0)
                component[output_class_key] = classified_class
            dataframe.at[i, input_key] = figure_components  # 显式写回
            
        storage.write(dataframe)
        return ""