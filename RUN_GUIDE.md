# 运行指南（Monomer / Polymer / Property / COF / Material / Content Evaluation）

本文汇总当前可直接执行的命令行，用于运行各抽取与评估流水线。涉及的代码参考：
- Monomer（请使用自定义 Runner，见 README_MONOMER.md）
- Polymer 流水线类：[polymer_extract_pipeline.py](file:///share/lcc/dataflow-dp/pipelines/polymer_extract_pipeline.py)
- Property 流水线类：[property_extract_pipeline.py](file:///share/lcc/dataflow-dp/pipelines/property_extract_pipeline.py)
- COF 流水线类：[cof_extract_pipeline.py](file:///share/lcc/dataflow-dp/pipelines/cof_extract_pipeline.py)
- Material 流水线类：[material_extract_pipeline.py](file:///share/lcc/dataflow-dp/pipelines/material_extract_pipeline.py)
- 内容评估流水线类：[content_evaluation_pipeline.py](file:///share/lcc/dataflow-dp/pipelines/content_evaluation_pipeline.py)

## 环境准备
- 加载凭据与代理（必需）：

```bash
cd /share/lcc/dataflow-dp
source /share/lcc/setup_env.sh
```

- 若运行依赖 `GOOGLE_CLOUD_PROJECT` 的管线（如 Material / Content Evaluation），同步项目变量：

```bash
export GOOGLE_CLOUD_PROJECT="$GCP_PROJECT_ID"
```

## Monomer 抽取
- 运行方式：请参考 [README_MONOMER.md](file:///share/lcc/dataflow-dp/README_MONOMER.md) 中的 Runner 示例，集成以下步骤：
  - 扫描 JSON → 生成 `input.jsonl`
  - 运行 Monomer 流水线（Seed → Enrich → Library）
  - 写出每论文 `monomers.csv` 与全局 `monomer_library.csv`、问题汇总 `monomer_smiles_issues.csv`
- 推荐设置（代理环境）：
  - `MONOMER_LLM_MAX_WORKERS=2`
  - `MONOMER_API_WORKERS=2`
  - `MONOMER_API_SLEEP_EVERY=20` / `MONOMER_API_SLEEP_SECONDS=0.2`

## Polymer 抽取
- 单文件可运行（类继承 BatchedPipeline，统一风格）：

```bash
cd /share/lcc/dataflow-dp
python pipelines/polymer_extract_pipeline.py --base-dir /share/lcc/paper --max-chunk-len 32000
```

- 产物：
  - 每篇论文目录：`polymers.csv`

## 属性抽取（Property）
- 单个类别（例如 thermal），扫描目录并写回各论文目录 `thermal.csv`：

```bash
cd /share/lcc/dataflow-dp
python pipelines/property_extract_pipeline.py --category thermal --base-dir /share/lcc/paper --limit 50
```

- 多类别一次跑（例如 thermal、mechanical、electrical）：

```bash
python pipelines/property_extract_pipeline.py --categories thermal,mechanical,electrical --base-dir /share/lcc/paper --limit 50
```

- 产物：
  - 每篇论文目录：`{category}.csv`（如 `thermal.csv`、`mechanical.csv`）

## COF 抽取
- 单文件可运行（使用仓库示例数据路径）：

```bash
cd /share/lcc/dataflow-dp
python pipelines/cof_extract_pipeline.py
```

## Material 抽取
- 需要 `GOOGLE_CLOUD_PROJECT`：

```bash
cd /share/lcc/dataflow-dp
export GOOGLE_CLOUD_PROJECT="$GCP_PROJECT_ID"
python pipelines/material_extract_pipeline.py
```

## 内容评估（Content Evaluation）
- 同样需要 `GOOGLE_CLOUD_PROJECT`：

```bash
cd /share/lcc/dataflow-dp
export GOOGLE_CLOUD_PROJECT="$GCP_PROJECT_ID"
python pipelines/content_evaluation_pipeline.py
```

## 常用环境变量（节选）
- 来自 `.env` / `setup_env.sh`，用于控制并发、分块与代理等：
  - `GCP_PROJECT_ID` / `GOOGLE_CLOUD_PROJECT`：Vertex AI 项目
  - `GOOGLE_APPLICATION_CREDENTIALS`：服务账号 JSON 凭证
  - `HTTP_PROXY` / `HTTPS_PROXY` / `no_proxy`：网络代理
  - `MONOMER_API_WORKERS` / `MONOMER_API_SLEEP_EVERY` / `MONOMER_API_SLEEP_SECONDS` / `MONOMER_API_TIMEOUT`：外部化学库访问并发与限流
  - `MONOMER_LLM_MAX_WORKERS` / `MONOMER_LLM_MAX_TOKENS`：Monomer 名称识别（LLM）并发与 tokens（代理环境建议 LLM 并发 2–5）
  - `PROPS_MAX_CHUNK_LEN` / `PROPS_LLM_MAX_WORKERS` / `PROPS_LLM_MAX_TOKENS`：属性抽取分块大小、并发与 tokens

## 说明
- Monomer 以脚本 [run_monomer.py](file:///share/lcc/dataflow-dp/run_monomer.py) 为入口（负责扫描、调用流水线、落盘与问题汇总）。
- Polymer 与 Property 采用统一的 BatchedPipeline 风格，支持单文件直接运行。
- COF、Material、Content Evaluation 本就支持单文件运行；若使用 Vertex AI，请确保凭据与项目变量已设置。 
