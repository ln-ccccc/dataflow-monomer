import pandas as pd
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow import get_logger

from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC
from dataflow.core import LLMServingABC

import tiktoken
import os
import time

from prompts.monomer import MonomerNameExtractPrompt
from prompts.polymer import PolymerExtractPrompt
from dataflow.core.prompt import prompt_restrict

@prompt_restrict(MonomerNameExtractPrompt, PolymerExtractPrompt)
@OPERATOR_REGISTRY.register()
class ChunkedPromptedGenerator(OperatorABC):
    """
    基于Prompt的生成算子，支持自动chunk输入并输出批次进度日志。
    - 使用tiktoken精确计算token数量；
    - 若输入超过max_chunk_len，采用递归二分法切分；
    - 以批次发送到 LLM，打印 dispatch/progress/rate/ETA/空响应计数。
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
                "基于提示词的生成算子，支持长文本自动分chunk并输出批次进度日志。"
            )
        else:
            return (
                "Prompt-based generator with recursive chunk splitting and batched progress logs."
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

        # === 收集所有chunk ===
        for i, row in dataframe.iterrows():
            raw_content = row.get(input_key, "")
            prompt_kwargs = {k: row.get(k) for k in self.input_aux_keys}
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

        # === 分批并发调用 ===
        total = len(all_llm_inputs)
        self.logger.info(f"Total {total} chunks to generate")

        try:
            all_responses = []
            if total > 0:
                bs_env = os.getenv("MONOMER_LLM_BATCH")
                try:
                    batch_size = int(bs_env) if bs_env else 20
                except Exception:
                    batch_size = 20
                if batch_size <= 0:
                    batch_size = 20
                done = 0
                start = time.time()
                batches = (total + batch_size - 1) // batch_size
                for b, i in enumerate(range(0, total, batch_size), start=1):
                    j = min(i + batch_size, total)
                    batch = all_llm_inputs[i:j]
                    self.logger.info(f"LLM dispatch {b}/{batches} size {len(batch)}")
                    out = self.llm_serving.generate_from_input(batch)
                    if not isinstance(out, list) or len(out) != len(batch):
                        out = [""] * len(batch)
                    all_responses.extend(out)
                    done += len(batch)
                    elapsed = max(1e-6, time.time() - start)
                    rate = done / elapsed
                    remain = total - done
                    eta = remain / rate if rate > 0 else 0
                    empty_cnt = sum(1 for x in out if not (str(x).strip()))
                    self.logger.info(f"LLM progress {done}/{total} rate {rate:.2f}/s ETA {eta:.1f}s empty {empty_cnt}/{len(out)}")
            
            # 重新按 row 划分
            all_generated_results = []
            idx = 0
            for num_chunks in row_chunk_map:
                if num_chunks == 0:
                    all_generated_results.append([])
                else:
                    all_generated_results.append(all_responses[idx:idx + num_chunks])
                    idx += num_chunks
                    
        except Exception as e:
            self.logger.error(f"Global generation failed: {e}")
            all_generated_results = [[] for _ in range(len(dataframe))]

        dataframe[output_key] = all_generated_results
        output_file = storage.write(dataframe)
        self.logger.info(f"Generation complete. Output saved to {output_file}")
        return output_key
