# Monomer Extraction Pipeline（单体抽取流水线）

只针对 monomer 抽取流水线做说明，其他 pipeline 不再介绍。实现与集成参考：
- 流水线实现（如已包含）：[monomer_extract_pipeline.py](file:///share/lcc/dataflow-dp/pipelines/monomer_extract_pipeline.py)
- Prompt 与 Schema：
  - [prompts/monomer.py](file:///share/lcc/dataflow-dp/prompts/monomer.py)
  - [schemas/monomer_schemas](file:///share/lcc/dataflow-dp/schemas/monomer_schemas)

## 1. 流水线目标与输入输出
- 目标：从论文 JSON 中抽取起始单体信息（名称/缩写/SMILES 等），为后续聚合物设计与分析提供结构化 monomer 数据。
- 输入：
  - 论文 JSON 文件树（默认扫描 `/share/lcc/paper`）
  - 每个 JSON 至少包含 `content`（正文文本），脚本会补充：
    - `file_path`：源文件路径
    - `doi_hint`：来自原始 JSON 的 token 字段
    - `extracted_doi`：由目录名简单解析得到的 DOI 候选
- 核心中间产物：
  - `/share/lcc/dataflow-dp/data/monomer_input_full.jsonl`：聚合后的输入 JSONL，供流水线读取
- 输出：
  - 每篇论文一个 `monomers.csv`
  - 全局单体库 `monomer_library.csv`

## 2. 整体流程概览
流水线从磁盘 JSON 到最终 CSV 的大致步骤如下（`run_monomer.py` 会把这些步骤串起来执行）：
1. 扫描 JSON 并生成输入 JSONL  
   - 遍历 `base_dir` 下所有 `.json` 文件，将有效内容写入 `input.jsonl`

2. LLM 单体种子抽取（MonomerSeedStage）  
   - 类：`MonomerSeedStage`（见 [monomer_extract_pipeline.py](file:///share/lcc/dataflow-dp/pipelines/monomer_extract_pipeline.py)）
   - 使用 Vertex AI Gemini 模型，通过 `ChunkedPromptedGenerator` 按 chunk 调用 LLM
   - 输入：每行的 `content`
   - 输出列：
     - `monomers_seed_raw`：LLM 原始分片输出
     - `monomers_seed`：经 `MonomerListProcessor` 归一化与合并后的单体列表

3. 三库 SMILES 补全（MonomerSmilesEnrichStage）  
   - 类：`MonomerSmilesEnrichStage`
   - API：
     - PubChem PUG REST：`_query_pubchem`
     - OPSIN JSON：`_query_opsin`
     - CACTUS CIR：`_query_cactus`
   - 名称处理：
     - 支持 abbreviation / full_name 两类名称
     - 处理常见 Unicode 符号、空白和标点，并派生若干别名变体
     - 对名称中的 `;`、`；` 做拆分，逐个尝试查询
   - 输出字段（单个 monomer dict 上）：
    - `smiles_pubchem` / `smiles_opsin` / `smiles_cactus`
    - `smiles_final`：当且仅当“正文 SMILES RDKit 正则化”与“任一非空外部 SMILES RDKit 正则化”完全一致时，等于正文的 canonical SMILES；否则置空
    - `smiles_valid`：规则同上，一致则 `valid`，否则 `invalid`；若正文 SMILES 为空或三库全空直接 `invalid`

4. 单体库写入（MonomerLibrarySaveStage）  
   - 类：`MonomerLibrarySaveStage`
   - 作用：将本次抽取到且 API 至少命中一次的单体写入全局单体库 CSV
   - 默认路径：`/share/lcc/dataflow-dp/data/monomer_library.csv`，可通过运行脚本的 `--library-output-path` 覆盖
   - 特性：
     - 每次运行会读取现有 CSV 并在末尾追加记录
     - 不做去重，同一 SMILES/名称可能出现多次
     - DOI 字段以 `;` 分隔的列表形式保存

5. 每篇论文 CSV 落盘  
  - 函数：`save_results_to_csv` / `_write_one`（按你的 Runner 集成）
   - 默认行为：
     - 若未指定 `--output-dir`，则写回原 JSON 所在目录
     - 文件名固定为 `monomers.csv`
     - 当本次 monomers 为空且已有非空 CSV 时，不覆盖原文件

6. 问题 SMILES 导出  
  - 函数：`write_smiles_issue_csv`
  - 将 `smiles_valid != "invalid"` 以外的记录过滤掉，仅导出无效 SMILES 的条目
  - 默认输出路径：`/share/lcc/dataflow-dp/data/monomer_smiles_issues.csv`

## 3. 入口脚本：run_monomer.py

`run_monomer.py` 是 Monomer 抽取的官方入口脚本，负责把“扫描 JSON → 生成输入 JSONL → 调用流水线 → 写出各类 CSV”串为一条可执行命令。

### 3.1 基本用法

在仓库根目录运行：

```bash
cd /share/lcc/dataflow-dp
python run_monomer.py \
  --base-dir /share/lcc/paper \
  --input-jsonl /share/lcc/dataflow-dp/data/monomer_input_full.jsonl \
  --smiles-issue-csv /share/lcc/dataflow-dp/data/monomer_smiles_issues.csv
```

关键参数：
- `--base-dir`：论文 JSON 根目录（默认 `/share/lcc/paper`）
- `--input-jsonl`：汇总后的输入 JSONL 路径（默认 `/share/lcc/dataflow-dp/data/monomer_input_full.jsonl`）
- `--limit`：仅处理前 N 个 JSON（0 表示不限制）
- `--output-dir`：若设置，则 `monomers.csv` 写到该目录下以 DOI/目录名为子目录；未设置时写回原 JSON 所在目录
- `--smiles-issue-csv`：SMILES 问题汇总文件路径（默认 `/share/lcc/dataflow-dp/data/monomer_smiles_issues.csv`）
- `--library-output-path`：全局单体库输出路径（默认 `/share/lcc/dataflow-dp/data/monomer_library.csv`，与流水线内部保持一致）

脚本运行时会打印几个关键阶段：
- `Scanning JSON under: ...`：遍历 JSON 文件
- `Prepared X entries in ...`：写入输入 JSONL
- `Initializing Pipeline / Compiling Pipeline / Running Pipeline`：构建并执行 `ExtractMonomer` 流水线
- `Loaded X rows from storage. Writing CSVs with ... workers...`：从流水线存储中读出结果并并发写 `monomers.csv`
- `Saved CSV results to ...`：统计写出成功的论文数
- `Wrote N problem rows to ...monomer_smiles_issues.csv`：输出无效 SMILES 的问题表

### 3.2 相关环境变量

`run_monomer.py` 通过环境变量控制并发与节流参数（命令行不再暴露这些开关）：
- `MONOMER_CSV_WORKERS`：写 `monomers.csv` 的并发 worker 数，默认 `min(4, CPU 核心数)`
- `MONOMER_PROGRESS_EVERY`：写 CSV 时每处理多少行打印一次进度（默认 `500`）
- `MONOMER_API_WORKERS`：调用外部化学库（PubChem / OPSIN / CACTUS）时的并发 worker 数（默认 `4`）
- `MONOMER_API_TIMEOUT`：每次外部请求超时时间（秒，默认 `10`）
- `MONOMER_API_SLEEP_EVERY`：每处理多少个请求触发一次节流睡眠（默认 `1000`）
- `MONOMER_API_SLEEP_SECONDS`：触发节流时睡眠时长（秒，默认 `0.2`）
- `MONOMER_API_ROW_WORKERS`：对同一行内部的 monomer 进行并行处理的 worker 数（默认 `4`）

同时，脚本会在启动时自动：
- 加载 `/share/lcc/setup_env.sh` 中的环境变量（包括 `MONOMER_*`、`PROPS_*`、GCP 凭据与代理变量）
- 将大写的 `HTTP_PROXY` / `HTTPS_PROXY` 同步为小写的 `http_proxy` / `https_proxy`

如需做小规模测试，可以：
- 使用 `--limit` 限制扫描的 JSON 数量，例如 `--limit 100`
- 在环境变量中把 `MONOMER_API_WORKERS` / `MONOMER_CSV_WORKERS` 设为较小值，降低外部依赖压力

## 4. 数据结构说明
- 中间 JSONL（`monomer_input_full.jsonl`）每行字段：
  - `file_path`：原始 JSON 路径
  - `content`：论文正文（字符串）
  - `doi_hint`：从原 JSON 的 `token` 字段带出的信息
  - `extracted_doi`：从父目录名简单转换得到的 DOI 候选
- 流水线内部主要列：
  - `monomers_seed_raw`：LLM 原始返回（按 chunk）
  - `monomers_seed`：经 `MonomerListProcessor` 归一化、去空、合并 DOI 后的列表
  - `monomers_info`：补全 SMILES 等信息后的单体列表（最终写出）
- `monomers_info` 中单体字典的典型结构：
  - `doi`：来自解析或补充的 DOI，可以是字符串或列表
  - `abbreviation`：缩写列表
  - `full_name`：全名列表
  - `cas_no`：CAS 号列表
  - `smiles`：原始文本中携带的 SMILES（若有）
  - `smiles_pubchem` / `smiles_opsin` / `smiles_cactus`
  - `smiles_final`：选出的最终 SMILES
  - `smiles_valid`：`valid` / `invalid`

## 4. 输出文件格式

### Valid / Invalid 判定规则
单体有效性 (`smiles_valid`) 依据正文 SMILES 与外部 API 结果的正则化对比决定：
- **Invalid** (满足任一条件即判定为无效):
  1. 正文 `smiles` 为空（或无法被 RDKit 正则化）。
  2. 三个外部 API (PubChem/OPSIN/CACTUS) 结果**全为空**。
  3. 正文 Canonical SMILES 与**所有**非空 API 结果的 Canonical SMILES 都不一致。
- **Valid** (必须同时满足以下所有条件):
  1. 正文 `smiles` 不为空。
  2. 三个外部 API 至少有一个结果不为空。
  3. 正文 Canonical SMILES 与**任意一个**非空 API 结果的 Canonical SMILES 完全一致。

### 1. 每篇论文的 `monomers.csv`
- **路径**：
  - 默认：各自 JSON 所在目录
  - 或者：`--output-dir` 下以 DOI/目录名为子目录
- **列结构** (对应 `run_monomer.py` 中的 `CSV_COLUMNS`)：
  - `doi` / `abbreviation` / `full_name` / `cas_no` / `iupac_name`
  - `smiles`: 正文原始提取的 SMILES
  - `smiles_can`: 正文 SMILES 经 RDKit 正则化后的 Canonical SMILES
  - `smiles_pubchem` / `smiles_pubchem_can`: PubChem 结果及其 Canonical 形式
  - `smiles_opsin` / `smiles_opsin_can`: OPSIN 结果及其 Canonical 形式
  - `smiles_cactus` / `smiles_cactus_can`: CACTUS 结果及其 Canonical 形式
  - `smiles_api_can`: 多个 API 结果中选出的代表性 Canonical SMILES (若一致则为该值，若不一致但命中正文则为正文值，否则取第一个非空结果)
  - `smiles_final`: 最终采用的 SMILES (Valid 时为 Canonical SMILES，Invalid 时可能为原始值或空)
  - `smiles_valid`: `valid` 或 `invalid`
- **特别说明**：
  - `doi` 优先使用 `extracted_doi`，若为空则回退到 monomer 内部携带的 `doi`
  - 列表字段以 `;` 连接为字符串

### 2. 全局单体库 `monomer_library.csv`
- **默认路径**：`/share/lcc/dataflow-dp/data/monomer_library.csv`
- **列结构**：包含上述所有 SMILES 相关字段 (`smiles_pubchem`...`smiles_api_can`, `smiles_final`) 以及 `abbreviation`, `full_name`, `doi`。
- **行为**：
  - 仅对至少一个外部库命中的单体进行记录。
  - 多次运行会不断追加记录，不进行去重。
  - 包含正则化后的 `_can` 字段，便于后续精确匹配。


## 5. 环境与依赖
- LLM 服务：
  - 使用 Vertex AI Gemini（通过 `APIGoogleVertexAIServing` 封装）
  - 建议在 Runner 中 `source /share/lcc/setup_env.sh` 以加载凭据与代理
  - 需要的关键环境变量：
    - `GOOGLE_APPLICATION_CREDENTIALS`
    - `GCP_PROJECT_ID`
    - 以及 HTTP/HTTPS 代理相关变量
- 代理与网络：
  - 若设置了 `HTTP_PROXY` / `HTTPS_PROXY`，脚本会自动同步为小写的 `http_proxy` / `https_proxy`
  - 访问 PubChem、OPSIN、CACTUS 需要外网连通
- 化学工具：
  - 依赖 RDKit 对 SMILES 进行解析与规范化

## 6. 常见问题排查
- 运行结束未见任何 `monomers.csv`：
  - 检查日志中是否出现：
    - `Prepared X entries in ...`（准备输入数据）
    - `Loaded X rows from storage`（流水线有实际数据）
  - 确认原始 JSON 的 `content` 字段非空
- 进度打印看起来“重复”：
  - LLM 抽取与 SMILES 补全是两个阶段，可能分别打印自己的进度日志
- 外部 API 报 429/503：
  - 适当降低 `--api-workers`、增大 `--api-sleep-every` 或 `--api-sleep-seconds`
  - 检查代理和凭据是否在 `setup_env.sh` 中正确配置

本 README 仅覆盖 monomer 抽取流水线相关内容，其他流水线请参考各自代码与使用场景。 
