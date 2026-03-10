import pandas as pd
from dataflow.utils.registry import OPERATOR_REGISTRY
# from dataflow import get_logger
import logging

from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC, LLMServingABC

import tiktoken
import os
import time

from dataflow.core.prompt import PromptABC, prompt_restrict

@prompt_restrict(PromptABC)
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
        prompt_template: PromptABC,
        json_schema: dict = None,
        max_chunk_len: int = 128000,
        input_aux_keys: list[str] = [],
        disable_chunking: bool = True,
    ):
        self.logger = logging.getLogger() # Use root logger directly
        self.llm_serving = llm_serving
        self.prompt_template = prompt_template
        self.json_schema = json_schema
        self.max_chunk_len = max_chunk_len
        self.disable_chunking = bool(disable_chunking)
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
        if self.disable_chunking:
            return [text]
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

        # === 分批处理每一行，避免全量攒内存 ===
        all_generated_results = [[] for _ in range(len(dataframe))]
        
        use_batch = getattr(self.llm_serving, "use_batch", False)
        # 如果是 batch 模式，我们还是得攒一波，但可以按行分批提交给 serving
        # 如果是非 batch 模式，直接按行/小批次处理
        
        total_rows = len(dataframe)
        done_rows = 0
        
        # 这里的 batch_size 是指一次处理多少个 chunk，对内存非常敏感
        bs_env = os.getenv("MONOMER_LLM_BATCH")
        try:
            row_batch_size = int(bs_env) if bs_env else 20
        except:
            row_batch_size = 20
        if row_batch_size <= 0:
            row_batch_size = 20

        for start_row in range(0, total_rows, row_batch_size):
            end_row = min(start_row + row_batch_size, total_rows)
            df_batch = dataframe.iloc[start_row:end_row]
            
            batch_llm_inputs = []
            row_chunk_indices = [] # 记录当前 batch 中每一行对应的 chunk 在 batch_llm_inputs 中的范围
            
            curr_idx = 0
            for i, row in df_batch.iterrows():
                raw_content = row.get(input_key, "")
                prompt_kwargs = {k: row.get(k) for k in self.input_aux_keys}
                
                if not raw_content:
                    row_chunk_indices.append((curr_idx, curr_idx))
                    continue
                
                chunks = self._split_recursive(raw_content)
                system_prompts = self.prompt_template.build_prompt(**prompt_kwargs)
                if not isinstance(system_prompts, list):
                    system_prompts = [system_prompts] * len(chunks)
                
                llm_inputs = [system_prompt + chunk for chunk, system_prompt in zip(chunks, system_prompts)]
                batch_llm_inputs.extend(llm_inputs)
                row_chunk_indices.append((curr_idx, curr_idx + len(chunks)))
                curr_idx += len(chunks)

            if not batch_llm_inputs:
                continue

            # 调用 LLM
            try:
                out = None
                if self.json_schema is not None:
                    try:
                        out = self.llm_serving.generate_from_input(
                            batch_llm_inputs,
                            json_schema=self.json_schema,
                            use_function_call=False,
                        )
                    except TypeError:
                        out = None
                if out is None:
                    out = self.llm_serving.generate_from_input(batch_llm_inputs)
                
                if not isinstance(out, list) or len(out) != len(batch_llm_inputs):
                    out = [""] * len(batch_llm_inputs)
                
                # 分配回结果
                for idx_in_batch, (s_idx, e_idx) in enumerate(row_chunk_indices):
                    if s_idx < e_idx:
                        all_generated_results[start_row + idx_in_batch] = out[s_idx:e_idx]
                
            except Exception as e:
                self.logger.error(f"Batch rows {start_row}-{end_row} failed: {e}")
            
            done_rows += len(df_batch)
            self.logger.info(f"Progress: {done_rows}/{total_rows} rows processed")

        dataframe[output_key] = all_generated_results
        output_file = storage.write(dataframe)
        self.logger.info(f"Generation complete. Output saved to {output_file}")
        return output_key
