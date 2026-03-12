# 数据抽取流水线（Monomer / Polymer / Properties）

本文档汇总本仓库中与“单体/聚合物/材料属性抽取”相关的能力与用法：
- Monomer：推荐使用 `pipelines/monomer_extract_pipeline.py` 作为入口
- Polymer：可直接运行 `pipelines/polymer_extract_pipeline.py`
- Properties：可直接运行 `pipelines/property_extract_pipeline.py`（按类别批量抽取）

## Monomer 快速开始
- 推荐入口脚本：`python pipelines/monomer_extract_pipeline.py`（内部调用 `pipelines/monomer_extract_cli.py`）
- 详细说明（流程/规则/字段/环境变量）：[README_MONOMER.md](README_MONOMER.md)

运行示例：
```bash
python pipelines/monomer_extract_pipeline.py \
  --base-dir <论文JSON根目录> \
  --batch-size 1000
```

环境与依赖：
- 自动加载 `setup_env.sh`（GCP 凭据、HTTP/HTTPS 代理；可用环境变量 `LCC_SETUP_ENV_PATH` 指定位置）
- LLM：Vertex AI Gemini（通过 APIGoogleVertexAIServing）
- 化学：RDKit 用于 SMILES 解析与规范化

CSV 并发与进度（仅影响本地 CSV 写入，非 LLM 调用）：
- `MONOMER_CSV_WORKERS`：写 `monomers.csv` 的线程数（默认 `min(4, os.cpu_count())`）
- `MONOMER_PROGRESS_EVERY`：写 CSV 时的进度步长（默认 500）

## Monomer 抽取阶段概览
Monomer 抽取通常包含以下阶段（以实际实现为准）：
1. MonomerSeedStage：单体名称种子抽取（monomers_seed_raw / monomers_seed）
2. MonomerSmilesEnrichStage：三库补全 SMILES（monomers_info）
3. MonomerLibrarySaveStage：全局单体库追加保存（monomer_library.csv）
4. 每论文写 CSV：monomers.csv

## Polymer 抽取（单独运行）
- 目标：从论文正文中抽取聚合物名称、类型、组分/配比、分子量与测试方法等结构化信息
- Prompt 与 Schema
  - Prompt 文本：`prompts/details/polymer.md`
  - Prompt 加载与 Schema 定义：[prompts/polymer.py](prompts/polymer.py) / `schemas/polymer.json`
- 运行
  - 独立脚本：`python pipelines/polymer_extract_pipeline.py --base-dir <论文JSON根目录> [--max-chunk-len 32000]`
  - 输出：每篇论文目录生成 `polymers.csv`（与论文 JSON 同目录）
  - 可选输入：
    - `--entry-file <input.jsonl>`：跳过扫描，直接以 JSONL 作为输入（每行需要至少包含 `file_path` 与 `content`）
    - `--use-batch`：启用 Vertex AI 批量推理
- 关键实现
  - 阶段：`ExtractPolymer`（见 [pipelines/polymer_extract_pipeline.py](pipelines/polymer_extract_pipeline.py)）
  - 机制：`ChunkedPromptedGenerator` 调用 LLM，输出到 `polymers_raw`，经解析汇总至 `polymers`
  - 白名单（可选）：如果论文 JSON 同目录存在 `monomers.json`（字符串数组），会作为 `monomer_whitelist` 传入 prompt，用于约束 `components` 的取值范围
- 字段与规则（来自 prompt 与 schema）
  - 基本字段（强制）：`polymer_name`、`polymer_type`、`components`（monomer 缩写列表）
  - 配比（互斥策略）：优先抽取明确类别到对应字段（`diamine_ratio`、`dianhydride_ratio`、`diisocyanate_ratio`、`diol_ratio`、`diacid_ratio`）；无法归类时写入 `ratio_values_text`；`feed_ratio_text` 始终保留原始上下文；`ratio_type` 支持 `mole`/`weight`/`unknown`
  - 分子量与方法：`mn_value`、`mw_value`、`pdi_value`、`mw_unit`、`test_method`（标准化缩写：GPC/SEC/Viscosity/NMR/LS）
  - 排除：不将单体当作聚合物；忽略商业参比品；缺少核心三字段的条目直接丢弃
- 输出
52→  - 每篇论文一个 `polymers.csv`（写回各自 JSON 目录）
53→- CSV 写入并发：
54→  - `POLYMER_CSV_WORKERS`：写 `polymers.csv` 的线程数（默认 1，可根据 CPU/IO 调整）
55→  - `POLYMER_PROGRESS_EVERY`：写 CSV 时的进度步长（默认 500）
56→- 当前说明
57→  - 目前 Polymer 与 Monomer 流程“分开运行”，未做自动联动；后续测试通过后，再把 Polymer 的配比与 Monomer 结果联动（例如按 monomer 别名匹配 SMILES）

## Property 抽取（批量：Vertex AI Batch）
- 目标：按类别（optical/thermal/mechanical/other/electrical）从论文正文中抽取材料属性的结构化字段
- Prompt 与 Schema：
  - Prompt：`prompts/<category>/...md`
  - Schema：`schemas/<category>/*_properties.json`
- 运行（单类别或逗号分隔多类别）：
```bash
python pipelines/property_extract_pipeline.py \
  --base-dir <论文JSON根目录> \
  --category optical,thermal,mechanical,other,electrical \
  --use-batch \
  --batch-size 500
```
- 输出（统一 IO 目录）：
  - 固定目录：`dataflow-dp/io`
  - 输入 JSONL：`io/<category>/input_<offset>_<limit>.jsonl`
  - 批预测结果 CSV：`io/<category>/results/<base>_<job_id>.csv`
  - 台账：`io/.jobs_ledger.jsonl`（用于断点续跑与状态追踪）
74→- 并发与批大小（环境变量）：
75→  - `PROPS_BATCH_CHUNK_SIZE`：文件级分片大小（未传 `--batch-size` 时使用，默认 1000）
76→  - `MONOMER_LLM_BATCH`：提示级提交批大小（默认 100）
77→  - `MAX_CONCURRENT_CATEGORIES`：类别并发上限（默认 2）

## Monomer 规则（SMILES 一致性）
- 判定口径：
  - 正文 `smiles` 为空 → invalid
  - 三库（PubChem/OPSIN/CACTUS）结果全部为空 → invalid（允许 1~2 个为空）
  - 正文 SMILES 与任一非空外部 SMILES 经 RDKit 正则化后完全一致 → valid
- 输出口径：
  - valid 时：`smiles_final=正文 canonical smiles`
  - invalid 时：`smiles_final=""`

## 数据与文件
- Monomer 中间输入 JSONL：`data/monomer_input_full.jsonl`（或你指定的路径）
- 每论文输出：
  - `monomers.csv`：单体信息与 SMILES 补全结果
  - `polymers.csv`：聚合物名称/类型/组成/分子量等
- 全局输出（Monomer 可选）：
  - `data/monomer_library.csv`：跨论文的 API 命中单体记录（不做去重）
  - `data/monomer_smiles_issues.csv`：`smiles_valid == "invalid"` 的问题条目
- Properties 输出：
  - `io/<category>/input_*.jsonl`：每次分片提交的输入
  - `io/<category>/results/*.csv`：批预测的结果落盘（按 job 切分）
  - 每篇论文目录下的 `<category>.csv`：按 `file_path` 写回的最终属性表

## 常见问题
- 运行结束未见输出
  - 确认扫描到了 JSON（日志 `Found X JSON files`），并且 `content` 非空
  - 检查写 CSV 日志或目标目录是否可写
- 外部 API 报 429/503
  - 下调 `MONOMER_API_WORKERS` 或提高 `MONOMER_API_SLEEP_EVERY` / `MONOMER_API_SLEEP_SECONDS`
  - 确认代理/凭据正常（`source setup_env.sh` 或设置 `LCC_SETUP_ENV_PATH`）
- polymer 抽取未产出
  - 确认文本确实包含满足身份校验（name/type/components）的聚合物条目
  - 查看 `polymers_raw`/`polymers` 在存储中的内容是否为空
- properties 抽取长时间无结果文件
  - 批量推理默认 60 秒轮询一次；先检查 `io/.jobs_ledger.jsonl` 中的 job 状态是否仍处于 RUNNING/PENDING
  - 确认 `--use-batch` 且 GCP 凭据生效（`GOOGLE_APPLICATION_CREDENTIALS`/`GCP_PROJECT_ID`）

## 代码参考
- 流水线与阶段：
  - [pipelines/monomer_extract_pipeline.py](pipelines/monomer_extract_pipeline.py)
    - ExtractMonomer / MonomerSeedStage / MonomerSmilesEnrichStage / MonomerLibrarySaveStage
  - [pipelines/polymer_extract_pipeline.py](pipelines/polymer_extract_pipeline.py)
    - ExtractPolymer
  - [pipelines/property_extract_pipeline.py](pipelines/property_extract_pipeline.py)
- Prompt 与 Schema：
  - [prompts/polymer.py](prompts/polymer.py) / `prompts/details/polymer.md` / `schemas/polymer.json`
  - [prompts/monomer.py](prompts/monomer.py) / [schemas/monomer_schemas](schemas/monomer_schemas)
  - [prompts/generic_md_prompt.py](prompts/generic_md_prompt.py) / [schemas](schemas)
- 通用算子与 CSV profile：
  - 输入准备算子：`PaperJsonInputGenerator`（扫描论文 JSON 并生成统一 input 记录）
    - [operators/general/paper_input_generator.py](operators/general/paper_input_generator.py)
  - CSV 导出算子：`CsvExportOperator`（可配置 columns / path / row_expander）
    - [operators/general/csv_exporter.py](operators/general/csv_exporter.py)
  - Monomer CSV profile（列定义与行展开逻辑）：
    - [operators/monomer/monomer_csv_profile.py](operators/monomer/monomer_csv_profile.py)
  - Polymer CSV profile（列定义与行展开逻辑）：
    - [operators/polymer/polymer_csv_profile.py](operators/polymer/polymer_csv_profile.py)
