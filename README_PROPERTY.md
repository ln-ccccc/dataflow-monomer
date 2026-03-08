# Property Extraction Pipeline（属性抽取流水线）

本文档只介绍“属性抽取”流水线，其他流水线请参考各自 README 或 RUN_GUIDE。

核心实现与脚本：
- 流水线实现：[`property_extract_pipeline.py`](file:///uni-curator/user/lcc/lcc/dataflow-dp/pipelines/property_extract_pipeline.py)
- 通用组件：
  - 分块生成算子：[`ChunkedPromptedGenerator`](file:///uni-curator/user/lcc/lcc/dataflow-dp/operators/general/chunked_generator.py)
  - 存储：[`storage.py`](file:///uni-curator/user/lcc/lcc/dataflow-dp/dataflow/utils/storage.py)
  - Vertex AI Serving：[`api_google_vertexai_serving.py`](file:///uni-curator/user/lcc/lcc/dataflow-dp/dataflow/serving/api_google_vertexai_serving.py)

## 1. 目标与输入输出

- 目标：从论文 JSON 中抽取聚合物的各类性能数据，并按类别写出结构化 CSV：
  - mechanical（力学）
  - thermal（热学）
  - electrical（电学）
  - optical（光学）
  - other（其他）
- 输入：
  - 论文 JSON 文件树（默认从 `--base-dir` 指定的目录递归扫描）
  - 每个 JSON 至少包含字段：
    - `content`：正文文本
    - 可选：`token`：作为 DOI 线索写入 `doi_hint`
  - 脚本会补充：
    - `file_path`：源 JSON 路径
    - `doi_hint`：从 JSON 的 `token` 字段拷贝
    - `extracted_doi`：由目录名相对 `PAPER_ROOT` 解析得到的 DOI 候选
- 中间产物：
  - 每个类别一个输入 JSONL：
    - `<base-dir>/{category}_input.jsonl`
    - 启用分片后：`{category}_input_{offset}_{limit}.jsonl`
- 输出：
  - 每篇论文目录下生成 `{category}.csv`（如 `thermal.csv`、`mechanical.csv`）
  - 单行是一条结构化性能记录，字段集合由内部 `HEADERS[category]` 定义

## 2. 整体流程概览

流水线从磁盘 JSON 到最终 CSV 的步骤如下：

1. 扫描 JSON 并生成输入 JSONL  
   - 调用 `find_json_files(base_dir)` 递归收集所有 `.json` 文件  
   - 使用 `prepare_input_data(json_files, output_jsonl)` 逐文件读取 JSON，写入 JSONL，每行包含：
     - `file_path`
     - `content`
     - `doi_hint`
     - `extracted_doi`

2. 按类别构造流水线实例  
   - 类：`ExtractCategoryProperties`
   - 关键组件：
     - Prompt 与 Schema：
       - 每个类别映射到一个 Markdown Prompt 与 JSON Schema（见 `_CATEGORY_FILES`）
     - 存储：
       - `FileStorage(first_entry_file_name=entry_file_name, cache_type="jsonl")`
     - LLM Serving：
       - `APIGoogleVertexAIServing`，默认 `model_name="gemini-2.5-pro"`
     - 生成算子：
       - `ChunkedPromptedGenerator`，按行分批调用 LLM
     - 解析算子：
       - `PandasOperator`，对 DataFrame 的每行调用 `_parse_all`，生成标准化的 `properties` 列

3. LLM 抽取阶段  
   - 对每一行：
     - 使用 `ChunkedPromptedGenerator.run` 读取列 `content`  
     - 在需要时按 token 数进行分 chunk 调用 LLM  
     - 将原始响应写入列 `{category}_raw`（按 chunk 列表）

4. 解析与归一化  
   - `_parse_all` 对每行的 `{category}_raw` 逐元素执行：
     - 使用 `safe_parse_json` 将字符串解析为 dict 或 list[dict]
     - 调用 `_normalize_item`，补全：
       - `doi` / `file_path` / `polymer_name`
       - 各类属性字段（如 `record_type`、`value`、`temperature`、`frequency` 等）
     - 仅保留 `record_type` 与 `value` 非空的条目
   - 最终每行的 `properties` 是一个 `list[dict]`，列名与内部 `HEADERS[category]` 一致
   - 解析完成后，会从 DataFrame 中删除 `{category}_raw` 与 `content` 列，以降低后续内存占用

5. 写出 CSV  
   - 函数：`save_results_to_csv(pipeline, category)`
   - 通过 `_read_pipeline_df` 读取最后一步 DataFrame，对每一行调用 `_write_one`：
     - 确认 `file_path` 存在
     - 目标 CSV 路径：`<dir_of_file_path>/{category}.csv`
     - 若 `properties` 为空：
       - 若已有非空 CSV，则跳过
       - 否则写出仅包含表头的空文件
     - 若 `properties` 非空：
       - 使用 `_write_csv_fixed` 按固定列顺序写出

## 3. 命令行用法

脚本入口：[`property_extract_pipeline.py`](file:///uni-curator/user/lcc/lcc/dataflow-dp/pipelines/property_extract_pipeline.py)

```bash
cd /share/lcc/dataflow-dp
python pipelines/property_extract_pipeline.py --help
```

关键参数：

- 基本类别选择与输入：
  - `--category`：单个类别，如 `thermal`、`mechanical`
  - `--categories`：逗号分隔多类别，如 `thermal,mechanical,electrical`
  - `--base-dir`：论文 JSON 根目录（递归扫描 `.json`）
  - `--entry-file`：直接指定已有的输入 JSONL（绕过扫描与生成阶段）
  - `--output-jsonl`：自定义输入 JSONL 路径（默认放在 `base-dir` 旁，以 `{category}_input.jsonl` 命名）
- Prompt 与 Schema：
  - `--prompt-dir`：Prompt 根目录，默认 `pipelines` 上一级的 `prompts`
  - `--schema-dir`：Schema 根目录，默认 `pipelines` 上一级的 `schemas`
- 文本分块与行数限制：
  - `--max-chunk-len`：单 chunk 最大 token 数，默认 `32000`
  - `--limit`：限制最多处理的 JSON 文件数（0 表示不限制）
  - `--offset`：从第几个 JSON 开始（全局偏移）
- 自动分批循环：
  - `--batch-size`：开启自动分片模式，每批处理 `batch-size` 个 JSON 文件
  - `--use-batch`：是否使用 Vertex AI BigQuery Batch 模式进行 LLM 推理

### 3.1 单次运行示例

1. 单类别，小规模测试：

```bash
cd /share/lcc/dataflow-dp
python pipelines/property_extract_pipeline.py \
  --category thermal \
  --base-dir /share/lcc/paper \
  --limit 100
```

2. 多类别，一次运行：

```bash
python pipelines/property_extract_pipeline.py \
  --categories thermal,mechanical,electrical,optical,other \
  --base-dir /share/lcc/paper \
  --limit 200
```

### 3.2 自动分片（每 2000 篇一批）

当数据量较大（数万篇）时，推荐使用自动分片模式：

```bash
python pipelines/property_extract_pipeline.py \
  --categories thermal,mechanical,electrical,optical,other \
  --base-dir /share/lcc/paper \
  --batch-size 2000 \
  --use-batch
```

在该模式下：
- 脚本会先扫描 `base-dir` 下所有 JSON，并排序
- 然后按 `batch-size` 将全体 JSON 分成多个批次：
  - 第 1 批：`offset=0, limit=batch-size`
  - 第 2 批：`offset=batch-size, limit=batch-size`
  - 以此类推，直到全部处理完毕
- 每个批次都会为每个类别生成独立 JSONL：
  - `{category}_input_{offset}_{limit}.jsonl`
- 对每个批次依次执行 LLM 抽取与 CSV 写出，避免一次性加载全部文献

如果同时指定了 `--offset` 与 `--limit`，则自动分片只会在 `[offset, offset+limit)` 这一段文件范围内循环。

## 4. 环境变量与并发控制

流水线启动时会调用 `_load_env_from_setup_env` 自动加载 `setup_env.sh` 中的环境变量。常用变量包括：

- Vertex AI 与 BigQuery：
  - `GCP_PROJECT_ID` / `GOOGLE_CLOUD_PROJECT`：项目 ID
  - `GOOGLE_APPLICATION_CREDENTIALS`：服务账号 JSON 凭证
- 代理：
  - `HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` / `https_proxy` / `no_proxy`
- LLM 分块与并发：
  - `PROPS_MAX_CHUNK_LEN`：覆盖 `--max-chunk-len`
  - `PROPS_LLM_MAX_WORKERS`：LLM 并发 worker 数，默认 100
  - `PROPS_LLM_MAX_TOKENS`：LLM 单次最大输出 tokens，默认 64000
  - `PROPS_DISABLE_CHUNKING`：是否禁用自动分 chunk（默认 1，表示禁用）
- 行级分批：
  - `MONOMER_LLM_BATCH`：`ChunkedPromptedGenerator` 每批处理的行数，默认 20（属性流水线复用该变量）
- Batch 模式与 BigQuery：
  - `PROPS_BATCH_CHUNK_SIZE`：Vertex AI Batch 每个 job 的请求数切片大小，默认 1000
- 类别并发：
  - `MAX_CONCURRENT_CATEGORIES`：并发执行的类别数，默认 2，用于限制同时运行的 `ExtractCategoryProperties` 数量
- 结果写出：
  - `PROPS_SAVE_PROGRESS_EVERY`：`save_results_to_csv` 每处理多少行打印一次进度，默认 50

大规模运行推荐设置：
- `MAX_CONCURRENT_CATEGORIES=1`
- `MONOMER_LLM_BATCH=2000`（或视内存调整）
- `PROPS_LLM_MAX_WORKERS` 调低到 10–20
- 如网络稳定，开启 `--use-batch` 并适当调小 `PROPS_BATCH_CHUNK_SIZE`

## 5. 数据结构与字段

### 5.1 输入 JSONL

由 `prepare_input_data` 写出的每行字段：
- `file_path`：原始 JSON 路径
- `content`：论文正文
- `doi_hint`：来自原始 JSON 的 `token`
- `extracted_doi`：由目录名解析得到的 DOI 候选

### 5.2 中间列

每个类别对应的主要列：
- `{category}_raw`：
  - LLM 原始输出列表，每个元素对应一个内容 chunk 的返回结果
- `properties`：
  - 解析后的属性记录列表
  - 每个元素是一个 dict，键集合由 `HEADERS[category]` 定义

不同类别的字段集合（部分）：
- mechanical：
  - `doi`、`file_path`、`polymer_name`、`record_type`、`metric_group`、`metric_type`、`value`、`temperature`、`frequency`、`test_standard`、`test_method`、`test_conditions`、`test_mode`、`measurement_direction`、`notes`
- thermal：
  - `doi`、`file_path`、`polymer_name`、`record_type`、`value`、`temperature`、`heating_rate`、`decomposition_criterion`、`atmosphere` 等
- electrical：
  - `doi`、`file_path`、`polymer_name`、`record_type`、`value`、`temperature`、`frequency` 等
- optical：
  - `doi`、`file_path`、`polymer_name`、`record_type`、`value`、`temperature`、`wavelength`、`thickness`、`ri_mode` 等
- other：
  - `doi`、`file_path`、`polymer_name`、`record_type`、`value`、`temperature` 等

### 5.3 输出 CSV

每篇论文的 `{category}.csv`：
- 路径：与原始 JSON 同目录
- 表头：对应 `HEADERS[category]`，所有缺失字段会补空字符串
- 行：`properties` 列中的每个 dict 对应一行

## 6. 调试与常见问题

- 没有生成 `{category}.csv`：
  - 检查日志是否有：
    - `Prepared input_jsonl category ... rows N path ...`
    - `Running ExtractCategoryProperties category ...`
    - `save_results_to_csv complete category ... total ...`
  - 确认原始 JSON 的 `content` 字段非空
- LLM 阶段很慢或超时：
  - 降低 `PROPS_LLM_MAX_WORKERS`
  - 减小 `MONOMER_LLM_BATCH`
  - 适当开启 `--use-batch` 并设置合适的 `PROPS_BATCH_CHUNK_SIZE`
- BigQuery / Vertex AI 报 429/503：
  - 降低并发（`MAX_CONCURRENT_CATEGORIES`、`PROPS_LLM_MAX_WORKERS`）
  - 检查代理配置和 GCP 凭据
- OOM 风险：
  - 使用 `--batch-size` 自动分片运行
  - 确保环境内存足够，并限制同时运行的类别数

