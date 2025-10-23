import os
import json
import base64
from io import BytesIO
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
from scipy.optimize import curve_fit
import numpy as np
import requests

# Chart extraction specific dependencies - lazy import with helpful error messages
try:
    import matplotlib.pyplot as plt
except ImportError:
    raise Exception(
        """
Matplotlib is not installed in this environment yet.
Chart extraction requires matplotlib. Install with:
  pip install -e .[chartextract]
  # or directly: pip install matplotlib
"""
    )

try:
    from PIL import Image, ImageOps
except ImportError:
    raise Exception(
        """
Pillow (PIL) is not installed in this environment yet.
Chart extraction requires Pillow for image processing. Install with:
  pip install -e .[chartextract]
  # or directly: pip install pillow
"""
    )

from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow import get_logger
from dataflow.core import OperatorABC
from dataflow.utils.storage import DataFlowStorage


@OPERATOR_REGISTRY.register()
class LineSeriesGenerator(OperatorABC):
    """
    使用本地 LineFormer Serving 批量为图片提取折线数据（line dataseries）。

    DataFrame 输入字段（可配置，默认如下）：
    - png_path_key: PNG图片路径字段，默认为 'png_path'（每行一张图）
    - input_save_dir: 输出目录；若为空则使用原图所在目录
    - figure_info_key: 图表信息字段，默认为 'figure_info'（用于重绘）
    - parser_json_key: OCR Parser JSON路径字段，默认为 'ocr_parser_json'（用于坐标映射，可选）
    
    重要：区分两种 Parser JSON
    - ocr_parser_json: OCR识别结果，格式 [[[bbox], [text, conf]], ...]，用于坐标变换
    - uniparser_json: UniParser结果，用于图表结构识别（FigureInfoGenerator使用）

    输出：
    - dataframe[output_key]: 该图的 line_dataseries（列表）
    - dataframe[lineformer_json_key]: LineFormer JSON文件路径（可选）
    - dataframe[replot_key]: 重绘图表路径（可选）
    
    功能：
    1. 自动生成 ocr_parser.json（可选）：调用 OCR API 解析图像文本和坐标
    2. 提取线条数据（像素坐标）
    3. 保存 JSON 和可视化（可选）
    4. 坐标变换 + 重绘（可选）：通过 curve_fit 拟合像素到真实坐标的映射，生成物理坐标图表
    
    参考实现：
    - OCR 解析: test.py
    - 坐标变换和重绘: plot_result.ipynb
    """

    def __init__(self, lf_serving, ocr_parser_host="http://101.126.82.63:50010/parse"):
        self.logger = get_logger()
        self.lf_serving = lf_serving
        self.ocr_parser_host = ocr_parser_host

    @staticmethod
    def _dump_image_base64_str(image: Image.Image, quality: int = 85) -> str:
        """将 PIL Image 转换为 base64 字符串"""
        img_byte_arr = BytesIO()
        image.save(img_byte_arr, format="JPEG", quality=quality)
        return base64.b64encode(img_byte_arr.getvalue()).decode("ascii")

    def _get_parser_result(self, image_path: str, padding: int = 20, lang: str = 'en') -> Optional[List]:
        """
        调用 OCR API 解析图像
        参考: test.py 中的实现
        
        Args:
            image_path: 图像路径
            padding: 图像边缘填充像素数
            lang: 语言 ('en' 或 'zh-hans')
        
        Returns:
            OCR 解析结果，失败返回 None
        """
        try:
            # 加载并预处理图像
            image = Image.open(image_path).convert("RGB")
            image = ImageOps.expand(image, border=padding, fill=(255, 255, 255))
            
            # 调用 OCR API
            kwargs = dict(
                images=[self._dump_image_base64_str(image)],
                lang=lang,
                cls=False,
                uuid="string_of_image",
            )
            
            self.logger.debug(f"Calling OCR API: {self.ocr_parser_host}")
            r = requests.post(self.ocr_parser_host, json=kwargs, timeout=30)
            
            if r.status_code != 200:
                self.logger.error(f"OCR API returned status code {r.status_code}")
                return None
            
            results = r.json()
            self.logger.debug(f"OCR API returned type: {type(results)}")
            if isinstance(results, dict):
                self.logger.debug(f"OCR API returned dict with keys: {list(results.keys())}")
            self.logger.info(f"OCR parsing successful for {image_path}")
            return results
            
        except Exception as e:
            self.logger.error(f"Failed to parse image {image_path}: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    @staticmethod
    def _linear_model(x, a, b):
        """线性模型：y = ax + b"""
        return a * x + b

    def _build_label_dict(self, parser_data) -> Dict[str, List[float]]:
        """
        从 parser_data 构建 label_dict
        parser_data格式支持：
        - 列表: [[[[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], [text, confidence]], ...]]
        - 字典: {'results': [[...]], ...} 或其他包含结果的字典
        label_dict[text] = [x_min, y_min, x_max, y_max, center_x, center_y]
        """
        label_dict = {}
        
        # 处理字典格式
        if isinstance(parser_data, dict):
            self.logger.debug(f"parser_data is dict with keys: {list(parser_data.keys())}")
            # 尝试常见的键名
            for key in ['results', 'data', 'predictions', 'output', '0']:
                if key in parser_data:
                    parser_data = parser_data[key]
                    self.logger.debug(f"Extracted parser_data from key '{key}'")
                    break
            else:
                # 如果还是字典，尝试获取第一个值
                if isinstance(parser_data, dict) and parser_data:
                    first_key = list(parser_data.keys())[0]
                    parser_data = parser_data[first_key]
                    self.logger.debug(f"Extracted parser_data from first key '{first_key}'")
        
        # 检查类型
        if not isinstance(parser_data, (list, tuple)):
            self.logger.error(f"After extraction, parser_data is still not a list or tuple, got {type(parser_data)}")
            return label_dict
        
        if not parser_data:
            self.logger.warning("parser_data is empty")
            return label_dict
        
        # 检查是否需要访问第一层
        data_to_process = parser_data
        try:
            if isinstance(parser_data[0], list) and parser_data[0] and isinstance(parser_data[0][0], list):
                # 如果第一层是列表的列表，则访问第一个元素
                if len(parser_data[0][0]) == 2 and isinstance(parser_data[0][0][1], list) and len(parser_data[0][0][1]) >= 1:
                    data_to_process = parser_data[0]
        except (KeyError, IndexError, TypeError) as e:
            self.logger.warning(f"Error checking parser_data structure: {e}, using parser_data as-is")
            data_to_process = parser_data
        
        for item in data_to_process:
            try:
                if len(item) >= 2:
                    bbox_points = item[0]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                    text_info = item[1]    # [text, confidence]
                    
                    if isinstance(bbox_points, list) and len(bbox_points) >= 4:
                        if isinstance(text_info, list) and len(text_info) >= 1:
                            text = text_info[0]
                            
                            # 从4个点提取bbox边界
                            x_coords = [pt[0] for pt in bbox_points if len(pt) >= 2]
                            y_coords = [pt[1] for pt in bbox_points if len(pt) >= 2]
                            
                            if x_coords and y_coords:
                                x_min, x_max = min(x_coords), max(x_coords)
                                y_min, y_max = min(y_coords), max(y_coords)
                                center_x = (x_min + x_max) / 2
                                center_y = (y_min + y_max) / 2
                                
                                label_dict[text] = [x_min, y_min, x_max, y_max, center_x, center_y]
            except (IndexError, TypeError, ValueError) as e:
                continue
        
        return label_dict

    def _fit_coordinate_transform(
        self, 
        figure_info: Dict[str, Any], 
        label_dict: Dict[str, List[float]]
    ) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        """
        拟合像素坐标到真实坐标的转换参数
        参考: plot_result.ipynb 中使用 curve_fit 的方式
        返回: (x_params, y_params) 其中每个是 (a, b) 或 None
        """
        if not isinstance(figure_info, dict):
            self.logger.warning(f"figure_info is not a dict: {type(figure_info)}")
            return None, None
        
        x_tick_labels = figure_info.get('x_tick_labels', [])
        y_tick_labels = figure_info.get('y_tick_labels', [])
        
        # 确保是列表
        if not isinstance(x_tick_labels, list):
            self.logger.warning(f"x_tick_labels is not a list: {type(x_tick_labels)}")
            x_tick_labels = []
        if not isinstance(y_tick_labels, list):
            self.logger.warning(f"y_tick_labels is not a list: {type(y_tick_labels)}")
            y_tick_labels = []
        
        # 收集 x 轴映射点
        x_pixel = []
        x_real = []
        for x_label in x_tick_labels:
            if x_label in label_dict:
                try:
                    x_pixel.append(label_dict[x_label][4])  # center_x
                    x_real.append(float(x_label))
                except (ValueError, IndexError):
                    continue
        
        # 收集 y 轴映射点
        y_pixel = []
        y_real = []
        for y_label in y_tick_labels:
            if y_label in label_dict:
                try:
                    y_pixel.append(label_dict[y_label][5])  # center_y
                    y_real.append(float(y_label))
                except (ValueError, IndexError):
                    continue
        
        self.logger.debug(f"X-axis mapping points: {len(x_pixel)} (pixel: {x_pixel[:3]}..., real: {x_real[:3]}...)")
        self.logger.debug(f"Y-axis mapping points: {len(y_pixel)} (pixel: {y_pixel[:3]}..., real: {y_real[:3]}...)")
        
        # 拟合 x 轴变换
        x_params = None
        if len(x_pixel) >= 2 and len(x_real) >= 2:
            try:
                params, _ = curve_fit(self._linear_model, x_pixel, x_real)
                x_params = tuple(params)
                # self.logger.info(f"X-axis transform: x_real = {x_params[0]:.6f} * x_pixel + {x_params[1]:.6f}")
            except Exception as e:
                self.logger.warning(f"Failed to fit x-axis transform: {e}")
        else:
            self.logger.warning(f"Insufficient x-axis mapping points: {len(x_pixel)}")
        
        # 拟合 y 轴变换
        y_params = None
        if len(y_pixel) >= 2 and len(y_real) >= 2:
            try:
                params, _ = curve_fit(self._linear_model, y_pixel, y_real)
                y_params = tuple(params)
                # self.logger.info(f"Y-axis transform: y_real = {y_params[0]:.6f} * y_pixel + {y_params[1]:.6f}")
            except Exception as e:
                self.logger.warning(f"Failed to fit y-axis transform: {e}")
        else:
            self.logger.warning(f"Insufficient y-axis mapping points: {len(y_pixel)}")
        
        return x_params, y_params

    def _transform_line_series(
        self,
        line_series: List[List[Dict[str, float]]],
        x_params: Optional[Tuple[float, float]],
        y_params: Optional[Tuple[float, float]]
    ) -> List[List[Dict[str, float]]]:
        """
        将像素坐标的线条数据转换为真实坐标
        """
        if not isinstance(line_series, list):
            self.logger.error(f"line_series is not a list: {type(line_series)}")
            return []
        
        transformed = []
        for line_idx, line in enumerate(line_series):
            if not isinstance(line, list):
                self.logger.warning(f"Line {line_idx} is not a list: {type(line)}")
                continue
            
            new_line = []
            for pt_idx, pt in enumerate(line):
                try:
                    if not isinstance(pt, dict):
                        self.logger.warning(f"Point {pt_idx} in line {line_idx} is not a dict: {type(pt)}")
                        continue
                    
                    x = pt.get('x')
                    y = pt.get('y')
                    
                    if x is None or y is None:
                        self.logger.warning(f"Point {pt_idx} in line {line_idx} missing x or y: {pt}")
                        continue
                    
                    if x_params is not None:
                        x = self._linear_model(x, x_params[0], x_params[1])
                    if y_params is not None:
                        y = self._linear_model(y, y_params[0], y_params[1])
                    
                    new_line.append({'x': float(x), 'y': float(y)})
                except Exception as e:
                    self.logger.error(f"Error transforming point {pt_idx} in line {line_idx}: {e}")
                    continue
            
            if new_line:
                transformed.append(new_line)
        
        return transformed

    def _replot_figure(
        self,
        line_series: List[List[Dict[str, float]]],
        output_path: str,
        figure_info: Optional[Dict[str, Any]] = None,
        figsize: Tuple[float, float] = (8, 6),
        dpi: int = 300
    ):
        """
        根据线条数据重新绘制图表
        参考: plot_result.ipynb 中的绘图方式
        """
        if not isinstance(line_series, list):
            raise ValueError(f"line_series must be a list, got {type(line_series)}")
        
        if not line_series:
            self.logger.warning("line_series is empty, creating empty plot")
        
        plt.figure(figsize=figsize, dpi=dpi)
        
        # 绘制所有线条
        plotted_lines = 0
        for line_idx, line in enumerate(line_series):
            if not isinstance(line, list):
                self.logger.warning(f"Line {line_idx} is not a list, skipping")
                continue
            if len(line) < 2:
                self.logger.debug(f"Line {line_idx} has fewer than 2 points, skipping")
                continue
            
            try:
                x_data = [pt['x'] for pt in line]
                y_data = [pt['y'] for pt in line]
                plt.plot(x_data, y_data, linewidth=1.5)
                plotted_lines += 1
            except (KeyError, TypeError) as e:
                self.logger.warning(f"Error plotting line {line_idx}: {e}")
                continue
        
        self.logger.debug(f"Plotted {plotted_lines} lines")
        
        # 设置坐标轴标签
        if figure_info and isinstance(figure_info, dict):
            x_label = figure_info.get('x_label', '')
            y_label = figure_info.get('y_label', '')
            if x_label:
                plt.xlabel(x_label, fontsize=12)
            if y_label:
                plt.ylabel(y_label, fontsize=12)
        
        # 添加网格（可选）
        plt.grid(True, alpha=0.3, linestyle='--')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        plt.close()
        
        self.logger.debug(f"Saved replot figure to {output_path}")

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "批量为图片提取 LineFormer 数据系列（线条坐标），以 DataFrame 驱动。\n"
                "输入字段：png_path_key(默认 'png_path')，每行一张PNG图。\n"
                "输出字段：output_key(默认 'line_series')为该图的线条数据。\n"
                "\n"
                "重要区分：\n"
                "- ocr_parser_json: OCR识别文本，用于坐标变换（本Operator使用）\n"
                "- uniparser_json: 图表结构识别（FigureInfoGenerator使用）\n"
                "\n"
                "可选功能：\n"
                "- auto_generate_parser: 自动调用 OCR API 生成 *_parser.json（默认 True）\n"
                "- save_json: 保存 LineFormer JSON 文件\n"
                "- save_vis: 保存 LineFormer 可视化图片\n"
                "- replot: 将像素坐标转换为真实物理坐标并重新绘图（需要 figure_info）\n"
                "\n"
                "完整流程：图像 → OCR解析(*_parser.json) → LineFormer提取 → 坐标变换 → 重绘图表"
            )
        return (
            "Batch extract line series using LineFormer in a DataFrame-driven operator. "
            "One PNG per row. Auto-generates OCR parser.json via OCR API if needed. "
            "Important: ocr_parser_json (for coordinate transform) != uniparser_json (for structure). "
            "Optional replot feature to convert pixel coordinates to real coordinates."
        )

    def run(self,
            storage: DataFlowStorage,
            png_path_key: str = "png_path",
            input_save_dir: str = "output_dir",
            output_key: str = "line_series",
            lineformer_json_key: str = "lineformer_json_path",
            save_json: bool = True,
            save_vis: bool = False,
            replot: bool = True,
            replot_key: str = "replot_path",
            figure_info_key: str = "figure_info",
            parser_json_key: str = "ocr_parser_json",
            auto_generate_parser: bool = True,
            ocr_padding: int = 20,
            ocr_lang: str = 'en'):
        self.logger.info("Running LineSeriesGenerator...")

        df: pd.DataFrame = storage.read('dataframe')
        self.logger.info(f"Loading, number of rows: {len(df)}")

        # 结果容器
        line_series_list: List[Optional[List]] = [None] * len(df)
        json_path_list: List[Optional[str]] = [None] * len(df)
        replot_path_list: List[Optional[str]] = [None] * len(df)

        self.lf_serving.start_serving()

        try:
            # 收集所有有效的 PNG 路径
            batch_image_paths: List[str] = []
            row_indices: List[int] = []

            for idx, row in df.iterrows():
                png_path = row.get(png_path_key)
                if isinstance(png_path, str) and os.path.exists(png_path):
                    batch_image_paths.append(png_path)
                    row_indices.append(idx)
                else:
                    line_series_list[idx] = []
                    json_path_list[idx] = None
                    replot_path_list[idx] = None

            if batch_image_paths:
                self.logger.info(f"Processing {len(batch_image_paths)} PNG images via LineFormer...")

                # 批量处理
                results = self.lf_serving.extract_from_image_paths(
                    batch_image_paths,
                    return_image=save_vis,
                    save_json_dir=None,
                    save_vis_dir=None,
                    chunksize=4
                )

                # 分配结果回各行（假设与输入顺序一致；服务端已保证按输入顺序返回）
                for idx_pos, (idx, png_path, result) in enumerate(zip(row_indices, batch_image_paths, results)):
                    row = df.loc[idx]
                    
                    # 安全校验：确认结果对应的图片路径一致
                    try:
                        res_img_path = result.get("image_path")
                        if isinstance(res_img_path, str) and res_img_path != png_path:
                            self.logger.warning(
                                f"Row {idx}: result image_path mismatch, expected {png_path}, got {res_img_path}"
                            )
                    except Exception:
                        pass

                    if result.get("status") == "success":
                        line_series = result.get("line_dataseries", [])
                        line_series_list[idx] = line_series
                        
                        # 准备输出目录
                        out_dir = row.get(input_save_dir)
                        if not out_dir or not isinstance(out_dir, str):
                            out_dir = os.path.dirname(png_path)
                        os.makedirs(out_dir, exist_ok=True)
                        
                        fname = os.path.basename(png_path)
                        stem = os.path.splitext(fname)[0]
                        
                        # 保存 JSON（可选）
                        if save_json:
                            json_path = os.path.join(out_dir, f"{stem}_lineformer.json")
                            
                            with open(json_path, "w", encoding="utf-8") as jf:
                                json.dump(line_series, jf, ensure_ascii=False, indent=2)
                            
                            json_path_list[idx] = json_path
                            # self.logger.info(f"Row {idx}: Saved line series to {json_path}")
                        
                        # 保存可视化（可选）
                        if save_vis and result.get("visualized_base64"):
                            import base64
                            vis_path = os.path.join(out_dir, f"{stem}_result.png")
                            
                            with open(vis_path, "wb") as vf:
                                vf.write(base64.b64decode(result["visualized_base64"]))
                            
                            # self.logger.info(f"Row {idx}: Saved visualization to {vis_path}")
                        
                        # 重绘功能（可选）
                        if replot:
                            try:
                                figure_info = row.get(figure_info_key)
                                self.logger.debug(f"Row {idx}: figure_info type: {type(figure_info)}, value: {figure_info}")
                                if not isinstance(figure_info, dict):
                                    # self.logger.warning(f"Row {idx}: No valid figure_info for replotting")
                                    replot_path_list[idx] = None
                                    continue
                                
                                # 获取或生成 parser_json
                                parser_json_path = row.get(parser_json_key)
                                label_dict = {}
                                parser_data = None
                                
                                if not parser_json_path or not isinstance(parser_json_path, str):
                                    parser_json_path = png_path.replace('.png', '_parser.json')
                                    self.logger.debug(f"Row {idx}: Generated parser_json_path: {parser_json_path}")
                                
                                # 如果 parser_json 不存在，自动生成
                                self.logger.debug(f"Row {idx}: auto_generate_parser={auto_generate_parser}, file_exists={os.path.exists(parser_json_path)}")
                                
                                if auto_generate_parser and not os.path.exists(parser_json_path):
                                    # self.logger.info(f"Row {idx}: Parser JSON not found at {parser_json_path}, generating via OCR API...")
                                    parser_data = self._get_parser_result(png_path, padding=ocr_padding, lang=ocr_lang)
                                    
                                    if parser_data is not None:
                                        # 保存 parser.json
                                        try:
                                            self.logger.debug(f"Row {idx}: Saving parser_data type={type(parser_data)} to {parser_json_path}")
                                            with open(parser_json_path, 'w', encoding='utf-8') as f:
                                                json.dump(parser_data, f, ensure_ascii=False, indent=4)
                                            # self.logger.info(f"Row {idx}: ✓ Saved parser JSON to {parser_json_path}")
                                            # 验证文件是否真的被创建
                                            if os.path.exists(parser_json_path):
                                                file_size = os.path.getsize(parser_json_path)
                                                self.logger.info(f"Row {idx}: ✓ Verified parser JSON exists, size={file_size} bytes")
                                            else:
                                                self.logger.error(f"Row {idx}: ✗ Parser JSON was not created!")
                                        except Exception as e:
                                            self.logger.error(f"Row {idx}: Failed to save parser JSON: {e}")
                                            import traceback
                                            self.logger.debug(f"Row {idx}: Traceback: {traceback.format_exc()}")
                                    else:
                                        self.logger.warning(f"Row {idx}: Failed to get parser result from OCR API")
                                        parser_json_path = None
                                
                                # 加载 parser_json
                                if parser_json_path and os.path.exists(parser_json_path):
                                    try:
                                        with open(parser_json_path, 'r', encoding='utf-8') as f:
                                            parser_data = json.load(f)
                                        self.logger.debug(f"Row {idx}: Loaded parser JSON from {parser_json_path}")
                                        label_dict = self._build_label_dict(parser_data)
                                        self.logger.debug(f"Row {idx}: Built label_dict with {len(label_dict)} labels")
                                    except Exception as e:
                                        self.logger.error(f"Row {idx}: Failed to load/parse parser JSON: {e}")
                                else:
                                    self.logger.warning(f"Row {idx}: No parser JSON available for coordinate mapping")
                                
                                # 拟合坐标变换参数
                                x_params, y_params = None, None
                                if label_dict:
                                    x_params, y_params = self._fit_coordinate_transform(figure_info, label_dict)
                                
                                # 如果没有有效的坐标变换，仍然可以绘制原始像素坐标
                                if x_params is None and y_params is None:
                                    self.logger.warning(f"Row {idx}: No coordinate transform available, plotting in pixel coordinates")
                                
                                # 调试：检查 line_series
                                self.logger.debug(f"Row {idx}: line_series type: {type(line_series)}, length: {len(line_series)}")
                                if line_series:
                                    self.logger.debug(f"Row {idx}: First line type: {type(line_series[0])}, length: {len(line_series[0]) if line_series[0] else 0}")
                                
                                # 转换坐标
                                transformed_series = self._transform_line_series(line_series, x_params, y_params)
                                self.logger.debug(f"Row {idx}: Transformed series length: {len(transformed_series)}")
                                
                                # 重绘图表
                                replot_path = os.path.join(out_dir, f"{stem}_replot.png")
                                self.logger.debug(f"Row {idx}: Replotting to {replot_path}")
                                self._replot_figure(transformed_series, replot_path, figure_info)
                                replot_path_list[idx] = replot_path
                                
                                self.logger.info(f"Row {idx}: Saved replot to {replot_path}")
                                
                            except Exception as e:
                                import traceback
                                self.logger.error(f"Row {idx}: Failed to replot: {e}")
                                self.logger.debug(f"Row {idx}: Exception type: {type(e).__name__}")
                                self.logger.debug(f"Row {idx}: Traceback:\n{traceback.format_exc()}")
                                replot_path_list[idx] = None
                    else:
                        line_series_list[idx] = []
                        json_path_list[idx] = None
                        replot_path_list[idx] = None
                        self.logger.warning(f"Row {idx}: Failed to process {png_path}: {result.get('error', 'Unknown error')}")

        finally:
            self.lf_serving.stop_serving()

        # 回写 DataFrame
        df[output_key] = line_series_list
        if save_json:
            df[lineformer_json_key] = json_path_list
        if replot:
            df[replot_key] = replot_path_list
        
        output_file = storage.write(df)
        self.logger.info(f"LineSeriesGenerator completed. Output saved to {output_file}")
        return output_key


