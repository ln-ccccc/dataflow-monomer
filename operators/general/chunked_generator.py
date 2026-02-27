import pandas as pd
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow import get_logger

from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC
from dataflow.core import LLMServingABC

import tiktoken

from prompts.monomer import MonomerNameExtractPrompt
from prompts.polymer import PolymerExtractPrompt
from dataflow.core.prompt import prompt_restrict

@prompt_restrict(MonomerNameExtractPrompt, PolymerExtractPrompt)
@OPERATOR_REGISTRY.register()
class ChunkedPromptedGenerator(OperatorABC):
    """
    基于Prompt的生成算子，支持自动chunk输入。
    - 使用tiktoken精确计算token数量；
    - 若输入超过max_chunk_len，采用递归二分法切分；
    - 输出为每行对应的生成结果列表（而非拼接字符串）。
    """

    def __init__(
        self,
        llm_serving: LLMServingABC,
        prompt_template: MonomerNameExtractPrompt | PolymerExtractPrompt,
        json_schema: dict = None,
        max_chunk_len: int = 128000,
        input_aux_keys: list[str] = []
    ):
        self.logger = get_logger()
        self.llm_serving = llm_serving
        self.prompt_template = prompt_template
        self.json_schema = json_schema
        self.max_chunk_len = max_chunk_len
        self.enc = tiktoken.get_encoding("cl100k_base")
        self.input_aux_keys = input_aux_keys

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "基于提示词的生成算子，支持长文本自动分chunk。"
                "采用递归二分方式进行chunk切分，确保每段不超过max_chunk_len tokens。"
                "返回每行对应的生成结果列表。"
            )
        else:
            return (
                "Prompt-based generator with recursive chunk splitting."
                "Uses tiktoken for token counting and returns a list of chunked responses."
            )

    # === token计算 ===
    def _count_tokens(self, text: str) -> int:
        return len(self.enc.encode(text))

    # === 递归二分分chunk ===
    def _split_recursive(self, text: str) -> list[str]:
        """递归地将文本拆分为不超过max_chunk_len的多个chunk"""
        token_len = self._count_tokens(text)
        if token_len <= self.max_chunk_len:
            return [text]
        else:
            mid = len(text) // 2
            left, right = text[:mid], text[mid:]
            return self._split_recursive(left) + self._split_recursive(right)

    def run(
        self,
        storage: DataFlowStorage,
        input_key: str = "raw_content",
        output_key: str = "generated_content",
    ):
        self.logger.info("Running ChunkedPromptedGenerator...")
        dataframe = storage.read("dataframe")
        self.logger.info(f"Loaded DataFrame with {len(dataframe)} rows.")

        all_generated_results = []

        all_llm_inputs = []
        row_chunk_map = []  # 记录每个row对应的chunk数量

        # === 先收集所有chunk ===
        for i, row in dataframe.iterrows():
            raw_content = row.get(input_key, "")
            prompt_kwargs = {}
            for aux_key in self.input_aux_keys:
                prompt_kwargs[aux_key] = row.get(aux_key)
            if not raw_content:
                row_chunk_map.append(0)
                continue

            chunks = self._split_recursive(raw_content)
            self.logger.info(f"Row {i}: split into {len(chunks)} chunks")

            system_prompts = self.prompt_template.build_prompt(**prompt_kwargs)
            if not isinstance(system_prompts, list):
                system_prompts = [system_prompts] * len(chunks)
            llm_inputs = [system_prompt + chunk for chunk, system_prompt in zip(chunks, system_prompts)]
            all_llm_inputs.extend(llm_inputs)
            row_chunk_map.append(len(chunks))

        # === 一次性并发调用 ===
        self.logger.info(f"Total {len(all_llm_inputs)} chunks to generate")

        try:
            # Force disable schema to avoid SDK errors, rely on prompt instructions
            # if self.json_schema:
            #     all_responses = self.llm_serving.generate_from_input(
            #         all_llm_inputs, response_schema=self.json_schema
            #     )
            # else:
            all_responses = self.llm_serving.generate_from_input(all_llm_inputs)
        except Exception as e:
            self.logger.error(f"Global generation failed: {e}")
            all_generated_results = [[] for _ in range(len(dataframe))]
        else:
            # === 按row重新划分responses ===
            all_generated_results = []
            idx = 0
            for num_chunks in row_chunk_map:
                if num_chunks == 0:
                    all_generated_results.append([])
                else:
                    all_generated_results.append(all_responses[idx:idx + num_chunks])
                    idx += num_chunks

        dataframe[output_key] = all_generated_results
        output_file = storage.write(dataframe)
        self.logger.info(f"Generation complete. Output saved to {output_file}")
        return output_key
