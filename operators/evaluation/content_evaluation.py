import json
import re
from dataflow import get_logger
from typing import Any, Dict, List, Optional
from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC
from prompts.evaluation import ContentOnlyEvaluationPrompt
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.core.prompt import prompt_restrict
from collections import Counter

@prompt_restrict(ContentOnlyEvaluationPrompt)
@OPERATOR_REGISTRY.register()
class ContentOnlyEvaluator(OperatorABC):
    """
    基于论文原文的自动化评估算子。
    无需 Benchmark 数据，纯粹基于原文内容（Content）对提取出的字段进行质量评分。
    """
    def __init__(
        self, 
        llm_serving: Any, 
        prompt_template: Optional[Any] = None, 
        json_schema: Optional[Dict] = None, 
        max_tokens: int = 64000
    ):
        self.llm_serving = llm_serving
        self.prompt_template = prompt_template or ContentOnlyEvaluationPrompt()
        self.json_schema = json_schema
        self.logger = get_logger()
        # 显式配置 LLM 输出上限
        if hasattr(self.llm_serving, 'max_tokens'):
            self.llm_serving.max_tokens = max_tokens

    def run(
        self,
        storage: DataFlowStorage,
        input_key: str = "content",           # 原文列
        input_evaluation_keys: List[str] = [], # 待评估的提取字段列
        output_key: str = "content_evaluation",
    ) -> str:
        self.logger.info("Starting ContentOnlyEvaluator...")
        
        # === 1. 读取数据 ===
        dataframe = storage.read("dataframe")

        # === 2. 构造批量 Prompt ===
        all_llm_inputs = []
        extraction_records = [] 

        for i, row in dataframe.iterrows():
            paper_content = row.get(input_key, "")
            if not paper_content:
                self.logger.warning(f"Row {i}: Content column '{input_key}' is empty.")
                all_llm_inputs.append(None)
                extraction_records.append(None)
                continue

            # 动态筛选需要评估的字段
            extraction_result = {
                key: row.get(key) for key in input_evaluation_keys if key in row
            }
            # 注入辅助信息

            prompt = self.prompt_template.build_prompt(paper_content, extraction_result)
            all_llm_inputs.append(prompt)
            extraction_records.append(extraction_result)

        # === 3. 并发 LLM 调用 ===
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


        dataframe["responses"] = all_responses
        # === 4. 结果处理与评分修正 ===
        final_evaluations = []
        for i, resp in enumerate(all_responses):
            if resp is None:
                final_evaluations.append({"error": "No response", "overall_score": 0})
                continue
            
            # 解析与修复
            eval_dict = self._parse_and_repair(resp)
            # 评分修正：对齐 input_evaluation_keys 的数量
            eval_dict = self._post_process_scores(eval_dict, extraction_records[i])
            final_evaluations.append(eval_dict)

        # === 5. 写回存储 ===
        dataframe[output_key] = final_evaluations
        storage.write(dataframe)
        
        self._log_statistics(final_evaluations)
        return output_key

    # --- 内部逻辑函数 ---

    def _parse_and_repair(self, raw_str: str) -> Dict:
        try:
            json_str = raw_str.strip()
            # 移除 Markdown 代码块标记
            if '```json' in json_str:
                json_str = json_str.split('```json')[1].split('```')[0].strip()
            elif '```' in json_str:
                json_str = json_str.split('```')[1].split('```')[0].strip()
            
            # 准确定位 JSON 对象
            start = json_str.find('{')
            end = json_str.rfind('}')
            if start >= 0 and end > start:
                json_str = json_str[start : end + 1]
            
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                fixed = self._fix_incomplete_json(json_str)
                return json.loads(fixed)
        except Exception as e:
            return {"error": f"JSON parse error: {str(e)}", "overall_score": 0}

    def _fix_incomplete_json(self, json_str: str) -> str:
        # 补全未闭合符号
        o_br, c_br = json_str.count('{'), json_str.count('}')
        o_bk, c_bk = json_str.count('['), json_str.count(']')
        fixed = json_str
        if o_br > c_br: fixed += '}' * (o_br - c_br)
        if o_bk > c_bk: fixed += ']' * (o_bk - c_bk)
        # 移除非法尾随逗号
        return re.sub(r',(\s*[}\]])', r'\1', fixed)

    def _post_process_scores(self, evaluation: Dict, extraction_result: Optional[Dict]) -> Dict:
        """
        评分修正算法：
        1. 针对 LLM 误按 100 分满分计算 Category Score 的情况进行缩放。
        2. 针对类别评估不全的情况按比例调整 Overall Score。
        """
        if not extraction_result or "error" in evaluation or "category_scores" not in evaluation:
            return evaluation

        category_scores = evaluation.get('category_scores', {})
        # 排除非评估字段（如 DOI）
        target_categories = set(extraction_result.keys()) - {'doi'}
        evaluated_categories = set(category_scores.keys())

        # 计算当前得分总和
        current_sum = sum(cat.get('score', 0) for cat in category_scores.values())
        
        # 缩放逻辑：如果总分显著超过 100 (150+)，认为 LLM 没按 100/N 分配
        if current_sum > 150 and evaluated_categories:
            proper_max = 100.0 / len(target_categories) if target_categories else 100.0 / len(evaluated_categories)
            for cat_name in category_scores:
                old_val = category_scores[cat_name].get('score', 0)
                if old_val > proper_max * 1.5:
                    # 使用 LaTeX 公式概念：Score_scaled = Score_old * (Max_proper / 100)
                    category_scores[cat_name]['score'] = old_val * (proper_max / 100)
            current_sum = sum(cat.get('score', 0) for cat in category_scores.values())

        # 比例调整：如果评估的类别少于实际存在的类别
        if target_categories and evaluated_categories != target_categories:
            ratio = len(evaluated_categories) / len(target_categories)
            evaluation['overall_score'] = current_sum * ratio
        else:
            evaluation['overall_score'] = current_sum

        return evaluation

    # def _log_statistics(self, results: List[Dict]):
    #     valid_scores = [r['overall_score'] for r in results if 'error' not in r]
    #     self.logger.info("--- Evaluation Summary ---")
    #     self.logger.info(f"Total: {len(results)} | Success: {len(valid_scores)}")
    #     if valid_scores:
    #         avg = sum(valid_scores) / len(valid_scores)
    #         self.logger.info(f"Average Score: {avg:.2f}")
    #     self.logger.info("--------------------------")

    def _log_statistics(self, results: List[Dict]):
        successful = [r for r in results if 'error' not in r]
        failed = [r for r in results if 'error' in r]
        
        self.logger.info("=" * 40)
        self.logger.info(f"EVALUATION SUMMARY")
        self.logger.info("=" * 40)
        self.logger.info(f"Total Papers: {len(results)}")
        self.logger.info(f"Successful:   {len(successful)}")
        self.logger.info(f"Failed:       {len(failed)}")

        if successful:
            # 1. 分数统计
            scores = [r.get('overall_score', 0) for r in successful]
            avg_score = sum(scores) / len(scores)
            self.logger.info(f"Score Statistics:")
            self.logger.info(f"  Average: {avg_score:.2f}")
            self.logger.info(f"  Min: {min(scores):.2f}")
            self.logger.info(f"  Max: {max(scores):.2f}")

            # 2. 抽取问题细项统计
            total_wrong_values = 0
            all_fabricated = []
            all_errors = []
            all_missing = []

            for res in successful:
                cat_scores = res.get('category_scores', {})
                for cat_name, cat_data in cat_scores.items():
                    acc = cat_data.get('accuracy', {})
                    comp = cat_data.get('completeness', {})
                    
                    total_wrong_values += acc.get('wrong_value_count', 0)
                    all_fabricated.extend([f"{cat_name}: fabricated"] * acc.get('fabricated_values', 0))
                    all_errors.extend(acc.get('errors', []))
                    all_missing.extend(comp.get('missing_information', []))

            self.logger.info(f"Issue Statistics:")
            self.logger.info(f"  Total Wrong Values: {total_wrong_values}")
            self.logger.info(f"  Fabricated Values:  {len(all_fabricated)}")
            self.logger.info(f"  Errors:  {len(all_errors)}")
            self.logger.info(f"  Missing Info Items: {len(all_missing)}")

            # 3. Top 5 频次分析
            self._log_counter_top_n("Top Fabricated Categories", all_fabricated)
            self._log_counter_top_n("Top Error Details", all_errors)
            self._log_counter_top_n("Top Missing Information", all_missing)

            # 4. 极端案例排行榜
            sorted_res = sorted(successful, key=lambda x: x.get('overall_score', 0))
            self.logger.info(f"Lowest Scores (Bottom 5):")
            for r in sorted_res[:5]:
                self.logger.info(f"  {r.get('doi', 'unknown')}: {r.get('overall_score', 0):.2f}")

            self.logger.info(f"Highest Scores (Top 5):")
            for r in sorted_res[-5:][::-1]:
                self.logger.info(f"  {r.get('doi', 'unknown')}: {r.get('overall_score', 0):.2f}")
        
        if failed:
            self.logger.info(f"Failed DOIs Sample:")
            for r in failed[:5]:
                self.logger.info(f"  {r.get('doi', 'unknown')}: {r.get('error', 'unknown error')[:50]}")
        
        self.logger.info("=" * 40)

    def _log_counter_top_n(self, title: str, data_list: list):
        if not data_list:
            return
        counter = Counter(data_list)
        self.logger.info(f"{title}:")
        for item, count in counter.most_common(5):
            # 截断过长的错误描述
            display_item = (item[:75] + '...') if len(item) > 75 else item
            self.logger.info(f"    - {display_item} ({count} times)")