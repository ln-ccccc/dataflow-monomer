import os
from pathlib import Path
from typing import List, Dict, Any
import pandas as pd

from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow import get_logger

from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC, LLMServingABC
from dataflow.core.prompt import prompt_restrict, DIYPromptABC
from prompts.bio_paper_extract import (
    BioPaperInfoExtractPrompt,
    BioPaperInfoExtractPrompt5,
    BioPaperInfoExtractPrompt6,
    BioPaperInfoExtractPrompt7,
    BioPaperInfoExtractPrompt8,
    BioPaperInfoExtractPrompt10,
)


def _read_text_file(file_path: str) -> str:
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()


def _save_json_to_file(data: Dict[str, Any], output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    import json
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


@prompt_restrict(
    BioPaperInfoExtractPrompt,
    BioPaperInfoExtractPrompt5,
    BioPaperInfoExtractPrompt6,
    BioPaperInfoExtractPrompt7,
    BioPaperInfoExtractPrompt8,
    BioPaperInfoExtractPrompt10,
)

@OPERATOR_REGISTRY.register()
class PaperInfoExtractGenerator(OperatorABC):
    '''
    Extract structured info from markdown files using an LLM.
    - Reads markdown file paths from dataframe (default column: "md_path")
    - Reads the complete markdown content for each paper
    - Builds system prompt with literature_id and user prompt with full markdown content
    - Calls LLM serving (APILLMServing_request or any LLMServingABC) to get structured JSON
    - Saves one JSON per paper directly under output_dir
    - Updates dataframe with output JSON paths
    '''

    def __init__(self,
                 llm_serving: LLMServingABC,
                 prompt_template = BioPaperInfoExtractPrompt | BioPaperInfoExtractPrompt5 | BioPaperInfoExtractPrompt6 | BioPaperInfoExtractPrompt7 | BioPaperInfoExtractPrompt8 | BioPaperInfoExtractPrompt10 | DIYPromptABC,
                 ):
        self.logger = get_logger()
        self.llm_serving = llm_serving
        # default prompt
        if prompt_template is None:
            prompt_template = BioPaperInfoExtractPrompt5()
        self.prompts = prompt_template

        self.logger.info(f"Initializing {self.__class__.__name__} ...")
        self.logger.info(f"{self.__class__.__name__} initialized.")

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "PaperInfoExtractGenerator 算子用于从 dataframe 中读取 markdown 文件路径，"
                "读取完整的 markdown 内容，调用大模型从中抽取结构化信息并保存为 JSON 文件。\n\n"
                "输入参数：\n"
                "- llm_serving：大模型服务（如 APILLMServing_request）\n"
                "- input_markdown_path_key：dataframe 中存储 markdown 路径的列名（默认 'md_path'）\n"
                "- output_dir：结果输出根目录（默认 './extract_output'）\n"
                "- output_json_path_key：dataframe 中存储输出 JSON 路径的列名（默认 'info_json_path'）\n"
                "- prompt_template：提示模板类（默认 BioPaperInfoExtractPrompt）\n\n"
                "输出：为每篇论文生成一个 JSON 结果文件，并更新 dataframe"
            )
        else:
            return (
                "Extract structured info from markdown files (read from dataframe) using an LLM and save per-paper JSON outputs."
            )

    def _validate_dataframe(self, dataframe: pd.DataFrame):
        """
        Validate that the dataframe has required columns and no conflicting columns.
        """
        required_keys = [self.input_markdown_path_key, self.input_paper_id_key]
        forbidden_keys = [self.output_json_path_key]

        missing = [k for k in required_keys if k not in dataframe.columns]
        conflict = [k for k in forbidden_keys if k in dataframe.columns]

        if missing:
            raise ValueError(f"Missing required column(s): {missing}")
        if conflict:
            self.logger.warning(f"The following column(s) already exist and will be overwritten: {conflict}")

    def run(self,
            storage: DataFlowStorage,
            input_paper_id_key: str = "id",
            input_markdown_path_key: str = "md_path",
            output_dir: str = "./extract_output",
            output_json_path_key: str = "info_json_path",
            ):
        output_dir_abs = str(Path(output_dir).resolve())
        Path(output_dir_abs).mkdir(parents=True, exist_ok=True)
        df = storage.read('dataframe')
        self.input_paper_id_key, self.input_markdown_path_key, self.output_json_path_key = input_paper_id_key, input_markdown_path_key, output_json_path_key
        # Validate dataframe has required columns
        self._validate_dataframe(df)
        
        # Initialize output column
        if output_json_path_key not in df.columns:
            df[output_json_path_key] = None

        results: List[Dict[str, Any]] = []

        batch_data = []
        for idx, row in df.iterrows():
            md_path = row.get(input_markdown_path_key)

            if not md_path or not isinstance(md_path, str) or not os.path.exists(md_path):
                self.logger.warning(f"Row {idx}: Invalid or missing md file path: {md_path}")
                df.loc[idx, output_json_path_key] = None
                continue
            
            paper_id = row.get(self.input_paper_id_key, os.path.basename(os.path.dirname(md_path)))
            
            try:
                content = _read_text_file(md_path)
            except Exception as e:
                err = {"literature_id": paper_id, "status": "failed", "error": f"read error: {e}"}
                results.append(err)
                self.logger.error(err)
                df.loc[idx, output_json_path_key] = None
                continue

            # Build prompt
            try:
                user_input = self.prompts.build_prompt(content, literature_id=paper_id)
                batch_data.append((idx, paper_id, user_input))
            except Exception as e:
                err = {"literature_id": paper_id, "status": "failed", "error": f"prompt error: {e}"}
                results.append(err)
                self.logger.error(err)
                df.loc[idx, output_json_path_key] = None
                continue
        
        user_inputs = [item[2] for item in batch_data]
        system_prompt = self.prompts.build_system_prompt()
        
        self.logger.info(f"Sending batch of {len(user_inputs)} inputs to LLM...")
        try:
            outputs = self.llm_serving.generate_from_input(user_inputs, system_prompt)
        except Exception as e:
            self.logger.error(f"Batch LLM error: {e}")
            # Mark all as failed
            for idx, paper_id, _ in batch_data:
                err = {"literature_id": paper_id, "status": "failed", "error": f"LLM error: {e}"}
                results.append(err)
                df.loc[idx, output_json_path_key] = None
            outputs = []
        
        # Step 3: Process results and save
        for i, (idx, paper_id, _) in enumerate(batch_data):
            if i < len(outputs):
                model_output = outputs[i]
                # Save JSON result per paper (directly in output_dir, no subfolder)
                try:
                    # Sanitize paper_id to create a safe filename (replace / and other unsafe chars)
                    safe_paper_id = str(paper_id).replace('/', '_').replace('\\', '_')
                    out_path = os.path.join(output_dir_abs, f"{safe_paper_id}_info.json")
                    to_save: Dict[str, Any] = {
                        "literature_id": paper_id,
                        "status": "success",
                        "model_output": model_output,
                    }
                    _save_json_to_file(to_save, out_path)
                    results.append({"literature_id": paper_id, "status": "success", "output_path": out_path})
                    # Update dataframe with output JSON path
                    df.loc[idx, output_json_path_key] = out_path
                except Exception as e:
                    err = {"literature_id": paper_id, "status": "failed", "error": f"save error: {e}"}
                    results.append(err)
                    self.logger.error(err)
                    df.loc[idx, output_json_path_key] = None
            else:
                # No output for this item
                err = {"literature_id": paper_id, "status": "failed", "error": "No output received"}
                results.append(err)
                self.logger.error(err)
                df.loc[idx, output_json_path_key] = None

        # persist dataframe with updated paths
        storage.write(df)
        self.logger.info(f"Processed {len(results)} items. Success: {sum(1 for r in results if r['status']=='success')}.")
        return [input_markdown_path_key, output_json_path_key]


