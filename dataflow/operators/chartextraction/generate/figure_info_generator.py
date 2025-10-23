import pandas as pd
import os
import json
import requests
from typing import Dict, Any, List, Tuple, Optional
from dataflow.utils.chartextraction.get_figure import batch_extract_figures_and_components
from dataflow.utils.chartextraction.extract_figure_info import extract_figure_components
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow import get_logger
from dataflow.prompts.chartextraction import ChartInfoExtractionPrompt
from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC
from dataflow.core import LLMServingABC
from dataflow.core.prompt import prompt_restrict
from tqdm import tqdm

@prompt_restrict(
    ChartInfoExtractionPrompt
)
@OPERATOR_REGISTRY.register()
class FigureInfoGenerator(OperatorABC):
    def __init__(self, vlm_serving: LLMServingABC, uniparser_host: str = "http://101.126.82.63:40001"):
        self.logger = get_logger()
        self.vlm_serving = vlm_serving
        self.prompt_template = ChartInfoExtractionPrompt()
        self.json_schema = self.prompt_template.get_json_schema()
        self.uniparser_host = uniparser_host
        
    
    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "从PDF文档中提取图表并使用VLM生成结构化图表信息。\n"
                "输入参数：\n"
                "- vlm_serving：VLM服务对象（可选），用于图表信息提取\n"
                "- input_path_key：PDF文件路径字段名，默认为'pdf_path'\n"
                "- parser_key：UniParser JSON文件路径字段名，默认为'parser_json'\n"
                "- output_save_dir：输出目录字段名，默认为'output_dir'（可选）\n"
                "- output_key：图表信息输出字段名，默认为'figure_info'\n"
                "输出参数：\n"
                "- 提取的PNG文件和对应的JSON文件保存在output_dir中\n"
                "注意：只处理 _chart.png 图片（包括所有子chart），不处理 figure、caption、legend 等其他组件"
            )
        elif lang == "en":
            return (
                "Extract figures from PDF documents and generate structured chart information using VLM.\n"
                "Input Parameters:\n"
                "- vlm_serving: VLM serving object (optional) for chart information extraction\n"
                "- input_path_key: Field name for PDF file path, default is 'pdf_path'\n"
                "- parser_key: Field name for UniParser JSON file path, default is 'parser_json'\n"
                "- output_save_dir: Field name for output directory, default is 'output_dir' (optional)\n"
                "- output_key: Field name for figure info output, default is 'figure_info'\n"
                "Output Parameters:\n"
                "- Extracted PNG files and corresponding JSON files saved in output_dir\n"
                "Note: Only processes _chart.png images (including sub-charts), excluding figure, caption, legend components"
            )
        else:
            return (
                "FigureInfoGenerator extracts figures from PDFs and generates structured chart information."
            )

    def _parse_chart_info(self, response: str) -> Dict[str, Any]:
        """
        解析 VLM 响应的 JSON 格式图表信息
        
        Args:
            response: VLM 返回的响应文本
            
        Returns:
            解析后的图表信息字典
        """
        try:
            response_text = response.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()
            
            result = json.loads(response_text)
            return result
        except json.JSONDecodeError as e:
            self.logger.warning(f"JSON decode error: {e}")
            self.logger.warning(f"Response text: {response_text[:500]}...")
            return self._get_empty_chart_info()
        except Exception as e:
            self.logger.warning(f"Error parsing chart info: {str(e)}")
            return self._get_empty_chart_info()
    
    def _get_empty_chart_info(self) -> Dict[str, Any]:
        """返回空的图表信息"""
        return {
            "x_label": "",
            "y_label": "",
            "x_tick_labels": [],
            "y_tick_labels": [],
            "legend_names": [],
            "legend_shapes": [],
            "legend_colors": [],
            "text": [],
            "figure_type": "",
        }
    
    def _parse_pdf_with_uniparser(self, pdf_path: str, output_json_path: str) -> Optional[Dict]:
        """
        调用UniParser API解析PDF文件
        参考: uni_parser_api.py
        
        Args:
            pdf_path: PDF文件路径
            output_json_path: 输出JSON文件路径
            
        Returns:
            解析结果字典，失败返回 None
        """
        try:
            trigger_url = f"{self.uniparser_host}/trigger-file-async"
            result_url = f"{self.uniparser_host}/get-result"
            
            token = os.path.basename(pdf_path)[:15]
            
            data = {
                'token': token,
                'sync': True,
                'textual': 3,
                'chart': False, 
                "table": True, 
                "molecule": True, 
                "equation": True,
                "figure": False,
                "expression": False,
            }
            files = {
                'file': open(pdf_path, 'rb')
            }
            
            self.logger.info(f"Calling UniParser API: {trigger_url}")
            r = requests.post(trigger_url, files=files, data=data, timeout=300)
            files['file'].close()
            
            response_json = r.json()
            if response_json.get('status') != 'success':
                self.logger.error(f"UniParser API returned error: {response_json}")
                return None
            
            # Step 2: Get parse result
            result = requests.post(result_url, json={'token': token}, timeout=300).json()
            with open(output_json_path, "w", encoding='utf-8') as f:
                json.dump(result, f, indent=4, ensure_ascii=False)
            
            self.logger.info(f"✓ Saved UniParser result to {output_json_path}")
            return result
            
        except Exception as e:
            self.logger.error(f"Failed to parse PDF {pdf_path}: {e}")
            import traceback
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
            return None

    def run(self, storage: DataFlowStorage, 
            input_path_key: str = "input_path", 
            parser_key: str = "uniparser_json", 
            output_save_dir: str = "output_dir",
            output_key: str = "figure_info",
            ):

        self.input_path_key, self.parser_key, self.output_save_dir, self.output_key = input_path_key, parser_key, output_save_dir, output_key
        self.logger.info("Running FigureInfoGenerator...")

        # Load the raw dataframe from the input file
        dataframe = storage.read('dataframe')
        self.logger.info(f"Loading, number of rows: {len(dataframe)}")
        
        # Step 1: 提取所有 PDF 的图表到 PNG
        png_metadata_list = []
        figure_info_list = [None] * len(dataframe)
        
        for idx, row in dataframe.iterrows():
            try:
                pdf_path = row.get(input_path_key)
                parser_json_path = row.get(parser_key)
                output_dir = row.get(output_save_dir)
                
                # 如果输入的是 PNG，跳过 Step 1（PDF -> PNG 提取），直接进入后续统一处理
                if isinstance(pdf_path, str) and pdf_path.lower().endswith(".png"):
                    if not os.path.exists(pdf_path):
                        self.logger.warning(f"Row {idx}: PNG file not found: {pdf_path}")
                        continue
                    # 统一改名为 *_chart.png
                    png_dir = os.path.dirname(pdf_path)
                    base_name = os.path.basename(pdf_path)
                    if "_chart.png" not in base_name:
                        name_wo_ext, _ = os.path.splitext(base_name)
                        new_name = f"{name_wo_ext}_chart.png"
                        new_path = os.path.join(png_dir, new_name)
                        try:
                            # 若目标已存在则直接使用目标
                            if os.path.exists(new_path):
                                pic_path = new_path
                            else:
                                os.rename(pdf_path, new_path)
                                pic_path = new_path
                        except Exception as e:
                            self.logger.warning(f"Row {idx}: Failed to rename PNG to *_chart.png: {e}. Using original path.")
                            pic_path = pdf_path
                            base_name = os.path.basename(pic_path)
                    else:
                        pic_path = pdf_path
                        new_name = base_name
                    # 设定输出目录：优先使用行里的 output_dir，其次使用 PNG 所在目录
                    if not output_dir:
                        output_dir = os.path.dirname(pic_path)
                    os.makedirs(output_dir, exist_ok=True)
                    # 保存到元数据列表，便于后续 VLM 统一处理
                    filename = os.path.basename(pic_path)
                    png_metadata_list.append((idx, row.to_dict(), pic_path, output_dir, filename))
                    figure_info_list[idx] = {}
                    continue
                
                if not pdf_path or not os.path.exists(pdf_path):
                    self.logger.warning(f"Row {idx}: PDF file not found: {pdf_path}")
                    continue
                
                if not parser_json_path or not os.path.exists(parser_json_path):
                    self.logger.info(f"Row {idx}: Parsing PDF file {pdf_path} to {parser_json_path}")
                    if not parser_json_path:
                        doi = os.path.splitext(os.path.basename(pdf_path))[0]
                        parser_json_path = os.path.join(os.path.dirname(pdf_path), f"{doi}.json")

                    result = self._parse_pdf_with_uniparser(pdf_path, parser_json_path)
                    if result is None:
                        self.logger.error(f"Row {idx}: Failed to parse PDF, skipping...")
                        continue
                
                # Create output directory if not specified
                if not output_dir:
                    doi = os.path.splitext(os.path.basename(pdf_path))[0]
                    output_dir = os.path.join(storage.get_workspace(), "extracted_figures", doi)
                
                os.makedirs(output_dir, exist_ok=True)
                
                # Load uniparser JSON
                with open(parser_json_path, 'r', encoding='utf-8') as f:
                    parser_json = json.load(f)
                
                # Extract figures with components from parser result
                figure_list = extract_figure_components(parser_json)
                
                # Extract figures and components from PDF
                self.logger.info(f"Row {idx}: Extracting figures from {pdf_path}")
                batch_extract_figures_and_components(
                    pdf_path=pdf_path, 
                    output_dir=output_dir, 
                    figures_info=figure_list
                )
                
                # 收集所有提取的 PNG 文件路径（只收集 chart 图，包括所有子chart）
                for root, dirs, files in os.walk(output_dir):
                    for file in files:
                        # 只处理 chart 图片（包括所有子chart），排除 figure、caption、legend 等
                        if file.endswith(".png") and "_chart.png" in file:
                            pic_path = os.path.join(root, file)
                            # 保存完整的行数据用于后续展开
                            png_metadata_list.append((idx, row.to_dict(), pic_path, output_dir, file))
                
                figure_info_list[idx] = {}
                
            except Exception as e:
                self.logger.error(f"Row {idx}: Error processing row: {str(e)}")
                continue
        
        # Step 2: 批量调用 VLM 处理所有图片
        png_results = {}  # {(row_idx, filename): (chart_info, json_path, png_path)}
        
        self.logger.info(f"Processing {len(png_metadata_list)} PNG files with VLM...")
        
        image_paths = [metadata[2] for metadata in png_metadata_list]
        
        try:
            llm_outputs = self.vlm_serving.generate_from_input(image_paths, self.prompt_template.build_prompt(), json_schema = self.json_schema)
                            
            for (row_idx, row_data, pic_path, output_dir, filename), response in tqdm(zip(png_metadata_list, llm_outputs), total=len(png_metadata_list), desc="Parsing chart infos (VLM)"):
                try:
                    chart_info = self._parse_chart_info(response)
                    
                    json_name = filename.replace(".png", ".json")
                    json_path = os.path.join(output_dir, json_name)
                    with open(json_path, 'w', encoding='utf-8') as jf:
                        json.dump(chart_info, jf, ensure_ascii=False, indent=4)
                    
                    png_results[(row_idx, filename)] = (chart_info, json_path, pic_path)
                    
                    if figure_info_list[row_idx] is not None:
                        figure_info_list[row_idx][filename] = chart_info
                        
                except Exception as e:
                    self.logger.warning(f"Row {row_idx}: Error processing {pic_path}: {str(e)}")
                    chart_info = self._get_empty_chart_info()
                    json_path = os.path.join(output_dir, filename.replace(".png", ".json"))
                    png_results[(row_idx, filename)] = (chart_info, json_path, pic_path)
                    if figure_info_list[row_idx] is not None:
                        figure_info_list[row_idx][filename] = chart_info
                
        except Exception as e:
            self.logger.error(f"Error in batch VLM processing: {str(e)}")
            # 如果批量处理失败，填充空结果
            for row_idx, row_data, pic_path, output_dir, filename in png_metadata_list:
                chart_info = self._get_empty_chart_info()
                json_path = os.path.join(output_dir, filename.replace(".png", ".json"))
                png_results[(row_idx, filename)] = (chart_info, json_path, pic_path)
                if figure_info_list[row_idx] is not None:
                    figure_info_list[row_idx][filename] = chart_info
        
        # Step 4: 构建输出 DataFrame
        new_rows = []
        for (row_idx, row_data, pic_path, output_dir, filename) in png_metadata_list:
            chart_info, json_path, png_path = png_results.get((row_idx, filename), (self._get_empty_chart_info(), "", pic_path))
            
            new_row = row_data.copy()
            new_row['png_path'] = png_path
            new_row['json_path'] = json_path
            new_row['figure_filename'] = filename
            new_row[self.output_key] = chart_info
            new_rows.append(new_row)
        
        output_df = pd.DataFrame(new_rows)
        self.logger.info(f"Expanded {len(dataframe)} PDFs to {len(output_df)} figures")

        
        # Save the updated dataframe to the output file
        output_file = storage.write(output_df)
        self.logger.info(f"FigureInfoGenerator completed. Output saved to {output_file}")
        return [self.output_key, "png_path", "json_path", "figure_filename"]
