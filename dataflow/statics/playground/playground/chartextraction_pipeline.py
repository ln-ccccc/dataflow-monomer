from dataflow.operators.chartextraction import FigureInfoGenerator, LineSeriesGenerator
from dataflow.serving.api_vlm_serving_openai import APIVLMServing_openai
from dataflow.utils.storage import FileStorage
from dataflow.serving.api_lineformer_serving_local import APILineFormerServing_local



class ChartExtractionPipeline:
    def __init__(self):
        """初始化图表提取 Pipeline"""
        
        # 配置 VLM serving
        self.vlm_serving = APIVLMServing_openai(
            model_name="gpt-4o-mini",  # 或 "gpt-4o" 等其他模型
            api_url="https://api.openai.com/v1/chat/completions",
            key_name_of_api_key="DF_API_KEY",
            max_workers=5,
            timeout=1800
        )

        self.lineformer_serving = APILineFormerServing_local(
            config_path="path/to/your/lineformer_swin_t_config.py",
            checkpoint_path="path/to/your/iter_3000.pth",
            device="cpu",           
            num_workers=1,
            padding_size=40
        )
        
        # 配置存储
        self.storage = FileStorage(
            first_entry_file_name="../example_data/ChartExtractionePipeline/example.jsonl",
            cache_path="./cache",
            file_name_prefix="chart_extraction",
            cache_type="jsonl",
        )
        
        # 创建图表信息生成器（传入 VLM serving）
        self.figure_generator = FigureInfoGenerator(vlm_serving=self.vlm_serving, uniparser_host= "http://101.126.82.63:40001")
        # 创建线条数据生成器（内部调用 lineformer_serving）
        self.line_series_generator = LineSeriesGenerator(lf_serving=self.lineformer_serving, ocr_parser_host="http://101.126.82.63:50010/parse")
    
    def forward(self):
        """执行图表提取流程"""
        # Step 1: 提取图表并生成结构化信息（每张图一行）
        self.figure_generator.run(
            storage=self.storage.step(),
            input_path_key="input_path",           # PDF 路径字段名
            parser_key="uniparser_json",        # UniParser JSON 路径字段名（用于图表结构识别）
            output_save_dir="output_dir",        # 输出目录字段名（可选）
            output_key="figure_info",           # 输出字段名
        )

        # Step 2: 为每张图提取线条数据并重绘
        self.line_series_generator.run(
            storage=self.storage.step(),
            png_path_key='png_path',            # PNG路径字段名
            input_save_dir='output_dir',        # 输出目录字段名
            output_key='line_series',           # 输出字段名
            lineformer_json_key='lineformer_json_path',  # LineFormer JSON路径字段名
            save_json=True,                     # 保存JSON
            save_vis=True,                      # 保存可视化
            replot=True,                        # 启用重绘功能
            replot_key='replot_path',           # 重绘图表路径字段名
            figure_info_key='figure_info',      # 图表信息字段名（来自Step 1）
            parser_json_key='ocr_parser_json',  # OCR Parser JSON路径字段名（用于坐标变换）
            auto_generate_parser=True,          # 自动生成OCR parser（*_parser.json）
            ocr_padding=20,                     # OCR图像填充
            ocr_lang='en'                       # OCR语言
        )


if __name__ == "__main__":
    # 使用方式 1: 使用 Pipeline
    pipeline = ChartExtractionPipeline()
    pipeline.forward()