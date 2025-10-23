# DataFlow 快速开始指南

## 安装步骤

### 1. 创建并激活 Conda 环境

```bash
conda create -n dataflow python=3.10
conda activate dataflow
```

### 2. 克隆代码仓库

```bash
git clone git@git.dp.tech:dataflow-dp/dataflow-dp.git
cd dataflow-dp
```

### 3. 安装依赖

```bash
pip install -e .[chartextract]
```

## 数据准备

### 4. 准备输入数据文件

在运行图表提取 pipeline 之前，需要准备输入数据的 JSONL 文件。可以使用提供的 `path_prepare.py` 脚本自动生成。

> 📌 **支持的输入格式**：
> - ✅ PNG 图片文件
> - ✅ PDF 文档文件
> - ✅ PNG 和 PDF 混合（可在同一目录下混合存放）

#### 使用方法

```bash
python path_prepare.py <输入目录> <输出JSONL文件路径>
```

#### 示例

假设你的图表文件（PNG/PDF 或混合）存放在 `./my_charts` 目录下：

```bash
python path_prepare.py ./my_charts ./input_data/charts.jsonl
```

**目录结构示例：**
```
./my_charts/
  ├── chart1.png          # PNG 图片
  ├── chart2.png          # PNG 图片
  ├── document1.pdf       # PDF 文档
  └── subfolder/
      ├── chart3.png
      └── document2.pdf
```

这个脚本会：
- 递归扫描 `./my_charts` 目录下的所有 PNG 和 PDF 文件
- 为每个文件生成一条记录（自动识别文件类型）
- 输出到 `./input_data/charts.jsonl` 文件

#### 生成的 JSONL 格式

**PNG 文件记录：**
```json
{
  "input_path": "/absolute/path/to/image.png",
  "uniparser_json": "",
  "output_dir": "/absolute/path/to"
}
```

**PDF 文件记录：**
```json
{
  "input_path": "/absolute/path/to/document.pdf",
  "uniparser_json": "/absolute/path/to/document_uniparser.json",
  "output_dir": "/absolute/path/to"
}
```

**混合场景输出示例（charts.jsonl）：**
```jsonl
{"input_path": "/path/to/my_charts/chart1.png", "uniparser_json": "", "output_dir": "/path/to/my_charts"}
{"input_path": "/path/to/my_charts/chart2.png", "uniparser_json": "", "output_dir": "/path/to/my_charts"}
{"input_path": "/path/to/my_charts/document1.pdf", "uniparser_json": "/path/to/my_charts/document1_uniparser.json", "output_dir": "/path/to/my_charts"}
{"input_path": "/path/to/my_charts/subfolder/chart3.png", "uniparser_json": "", "output_dir": "/path/to/my_charts/subfolder"}
{"input_path": "/path/to/my_charts/subfolder/document2.pdf", "uniparser_json": "/path/to/my_charts/subfolder/document2_uniparser.json", "output_dir": "/path/to/my_charts/subfolder"}
```

> 💡 **提示**：生成的 JSONL 文件路径需要在配置文件的 `first_entry_file_name` 参数中指定。

> ⚠️ **注意**：PNG 文件可直接处理，PDF 文件会先通过 UniParser API 解析生成对应的 `_uniparser.json` 文件。

---

## 配置步骤

### 5. 设置 API Key 环境变量

```bash
export DF_API_KEY="your_openai_api_key_here"
```

> 💡 Windows PowerShell 用户请使用: `$env:DF_API_KEY="your_openai_api_key_here"`

### 6. 修改配置文件

在运行示例之前，需要修改 `chartextraction_pipeline.py` 中的配置参数：

#### 6.1 VLM 模型配置（第 13-19 行）

```python
self.vlm_serving = APIVLMServing_openai(
    model_name="gpt-4o-mini",  # 可选: "gpt-4o", "gpt-4o-mini" 等
    api_url="https://api.openai.com/v1/chat/completions", # 你的API_URL
    key_name_of_api_key="DF_API_KEY",
    max_workers=5,
    timeout=1800
)
```

#### 6.2 LineFormer 模型配置（第 21-27 行）

```python
self.lineformer_serving = APILineFormerServing_local(
    config_path="path/to/your/lineformer_swin_t_config.py",  # 修改为实际路径
    checkpoint_path="path/to/your/iter_3000.pth",            # 修改为实际路径
    device="cpu",           
    num_workers=1, 
    padding_size=40
)
```

#### 6.3 输入文件配置（第 30-35 行）

```python
self.storage = FileStorage(
    first_entry_file_name="../example_data/ChartExtractionePipeline/example.jsonl",  # 修改为实际路径（参考步骤4生成的JSONL文件）
    cache_path="./cache",
    file_name_prefix="chart_extraction",
    cache_type="jsonl",
)
```

> 💡 **提示**：`first_entry_file_name` 应指向步骤 4 中使用 `path_prepare.py` 生成的 JSONL 文件路径。

#### 6.4 Parser Host 配置（第 38 和 40 行）

需要在创建 `FigureInfoGenerator` 和 `LineSeriesGenerator` 时传入 host 参数：

```python
# 创建图表信息生成器（传入 VLM serving 和 uniparser_host）
self.figure_generator = FigureInfoGenerator(
    vlm_serving=self.vlm_serving,
    uniparser_host="http://101.126.82.63:40001"  # UniParser API 地址
)

# 创建线条数据生成器（传入 lineformer_serving 和 ocr_parser_host）
self.line_series_generator = LineSeriesGenerator(
    lf_serving=self.lineformer_serving,
    ocr_parser_host="http://101.126.82.63:50010/parse"  # OCR Parser API 地址
)
```

## 运行示例

### 7. 创建运行目录并初始化

```bash
cd ..
mkdir rundataflow
cd rundataflow
dataflow init
```

### 8. 运行图表提取示例

```bash
cd playground
python chartextraction_pipeline.py
```

---

## 配置说明

| 配置项 | 说明 | 示例值 |
|--------|------|--------|
| `model_name` | VLM 模型名称 | `gpt-4o-mini`, `gpt-4o` |
| `api_url` | OpenAI API 地址 | `https://api.openai.com/v1/chat/completions` |
| `DF_API_KEY` | OpenAI API Key（环境变量） | `sk-xxxxxxxx` |
| `config_path` | LineFormer 配置文件路径 | `/path/to/lineformer_swin_t_config.py` |
| `checkpoint_path` | LineFormer 模型权重路径 | `/path/to/iter_3000.pth` |
| `first_entry_file_name` | 输入数据文件路径 | `../example_data/ChartExtractionePipeline/example.jsonl` |
| `uniparser_host` | UniParser API 服务地址 | `http://101.126.82.63:40001` |
| `ocr_parser_host` | OCR Parser API 服务地址 | `http://101.126.82.63:50010/parse` |

## 环境要求

- Python 版本: 3.10
- 安装模式: 可编辑模式（`-e`），包含 `chartextract` 扩展功能
- 需要 OpenAI API Key 或兼容的 API 服务
- 需要 LineFormer 模型文件（配置文件和权重文件）
- 需要访问以下 API 服务：
  - UniParser API：用于 PDF 解析和图表结构识别
  - OCR Parser API：用于图表文本识别和坐标提取
