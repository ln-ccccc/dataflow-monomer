import pandas as pd
import json
from typing import Any, List, Dict
from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC
from prompts.evaluation import BenchmarkCompareEvaluationPrompt
from dataflow import get_logger
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.core.prompt import prompt_restrict

@prompt_restrict(
    BenchmarkCompareEvaluationPrompt
)
@OPERATOR_REGISTRY.register()
class BenchmarkEvaluator(OperatorABC):
    def __init__(self, llm_serving, prompt_template=None, json_schema=None, logger=None):
        self.llm_serving = llm_serving
        self.prompt_template = prompt_template or BenchmarkCompareEvaluationPrompt()
        self.json_schema = json_schema
        self.logger = get_logger()


    def load_json(self, path: str) -> List[Dict]:
        with open(path, 'r', encoding='utf-8') as f:
            if path.endswith('.jsonl'):
                data = []
                for line in f:
                    line = line.strip()
                    if line:
                        data.append(json.loads(line))
                return data

            # Standard JSON
            data = json.load(f)
            if isinstance(data, dict):
                return [data]
            return data

    def _parse_and_repair(self, raw_str: str) -> dict:
        """内部 JSON 修复逻辑（与前次回复一致）"""
        try:
            # 1. 基础清理
            text = raw_str.strip()
            if "```json" in text:
                text = text.split("```json")[-1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[-1].split("```")[0]
            
            # 2. 提取 JSON 块
            import re
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                text = match.group()
            
            return json.loads(text)
        except Exception:
            return {"error": "JSON parse failed", "raw": raw_str[:200]}

    def run(
        self,
        storage: DataFlowStorage,
        benchmark_path: str,
        output_key: str = "evaluation_result",
    ):
        self.logger.info(f"Loading data from {benchmark_path}")

        # === 1. 加载并预处理数据 ===
        benchmark_list = self.load_json(benchmark_path)
        df_bench = pd.DataFrame(benchmark_list)
        df_ext = storage.read("dataframe")

        # 通过 DOI 对齐数据，确保 row 内 benchmark 和 extraction 匹配
        # 使用 inner join 自动过滤掉无法配对的数据
        dataframe = pd.merge(
            df_bench, 
            df_ext, 
            on="doi", 
            suffixes=("_bench", "_ext")
        )
        self.logger.info(f"Matched {len(dataframe)} papers for evaluation.")

        # === 2. 构造 Prompt 输入 ===
        all_llm_inputs = []
        for _, row in dataframe.iterrows():
            # 这里的逻辑必须与 Prompt 类中 build_prompt 的入参对齐
            # 原版逻辑需要两个 dict
            bench_dict = row.filter(like="_bench").rename(lambda x: x.replace("_bench", "")).to_dict()
            ext_dict = row.filter(like="_ext").rename(lambda x: x.replace("_ext", "")).to_dict()
            
            # 特别处理：恢复 DOI 字段（因为 merge 后 DOI 是公共列，没带后缀）
            bench_dict["doi"] = row["doi"]
            ext_dict["doi"] = row["doi"]

            # 构建 Prompt
            prompt = self.prompt_template.build_prompt(bench_dict, ext_dict)
            all_llm_inputs.append(prompt)

        # === 3. 并发生成 ===
        system_prompt = self.prompt_template.build_system_prompt()
        self.logger.info(f"Starting batch generation for {len(all_llm_inputs)} prompts...")

        try:
            # DataFlow 框架通常在这一步处理多线程/多进程
            all_responses = self.llm_serving.generate_from_input(
                all_llm_inputs, 
                system_prompt=system_prompt,
                response_schema=self.json_schema,  # 传入之前定义的 JSON Schema
                use_function_call=False
            )
        except Exception as e:
            self.logger.error(f"Batch evaluation failed: {e}")
            raise e

        # === 4. 解析与修复结果 ===
        processed_results = []
        for resp in all_responses:
            # 此处复用之前提供的 JSON 修复逻辑
            processed_results.append(self._parse_and_repair(resp))

        dataframe[output_key] = processed_results
        
        if processed_results:
            scores = [r.get('overall_score', 0.0) for r in processed_results 
                    if isinstance(r.get('overall_score'), (int, float))]
            errors = [r for r in processed_results if 'error' in r]
            
            if scores:
                self.logger.info("=" * 70)
                self.logger.info("Evaluation Summary".center(70))   
                self.logger.info("=" * 70)
                self.logger.info(f"Total papers:      {len(processed_results)}")
                self.logger.info(f"Successful:       {len(scores)}")
                self.logger.info(f"Failed:           {len(errors)}")
                if scores:
                    self.logger.info(f"Average score:     {sum(scores) / len(scores):.2f}")
                    self.logger.info(f"Min score:         {min(scores):.2f}")
                    self.logger.info(f"Max score:         {max(scores):.2f}")
                self.logger.info("=" * 70)

        # === 5. 写回存储 ===
        # 只保留 DOI 和结果，或者保留全部
        result_df = dataframe[["doi", output_key]]
        output_file = storage.write(result_df)
        
        self.logger.info(f"Evaluation complete. Saved to {output_file}")
        return output_key