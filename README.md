# 数据抽取流水线（Monomer & Polymer）

本文档汇总本仓库中与“单体/聚合物抽取”相关的能力与用法。Monomer 抽取需通过自定义 Runner 集成（见下文），Polymer 可直接运行对应 pipeline 文件。

## Monomer 快速开始
- 本仓库不再提供单独的 Monomer 入口脚本。请使用自定义 Runner 集成 Monomer 流程，详见：
  - [README_MONOMER.md](file:///share/lcc/dataflow-dp/README_MONOMER.md)（包含 Runner 示例与规则说明）

环境与依赖：
- 自动加载 /share/lcc/setup_env.sh（GCP 凭据、HTTP/HTTPS 代理）
- LLM：Vertex AI Gemini（通过 APIGoogleVertexAIServing）
- 化学：RDKit 用于 SMILES 解析与规范化

## Monomer 抽取阶段概览
Monomer 抽取通常包含以下阶段（以实际实现为准）：
1. MonomerSeedStage：单体名称种子抽取（monomers_seed_raw / monomers_seed）
2. MonomerSmilesEnrichStage：三库补全 SMILES（monomers_info）
3. MonomerLibrarySaveStage：全局单体库追加保存（monomer_library.csv）
4. 每论文写 CSV：monomers.csv

## Polymer 抽取（单独运行）
- 目标：从论文正文中抽取聚合物名称、类型、组分/配比、分子量与测试方法等结构化信息
- Prompt 与 Schema
  - Prompt 文本：/share/lcc/prompt/polymer.md（按你的规范）
  - Prompt 加载与 Schema 定义：[polymer.py](file:///share/lcc/dataflow-dp/prompts/polymer.py) / /share/lcc/schema/polymer.json
- 运行
  - 独立脚本：`python /share/lcc/dataflow-dp/pipelines/polymer_extract_pipeline.py --base-dir <JSON根目录> [--max-chunk-len 32000]`
  - 输出：每篇论文目录生成 `polymers.csv`
- 关键实现
  - 阶段：`ExtractPolymer`（见 [pipelines/polymer_extract_pipeline.py](file:///share/lcc/dataflow-dp/pipelines/polymer_extract_pipeline.py)）
  - 机制：`ChunkedPromptedGenerator` 调用 LLM，输出到 `polymers_raw`，经解析汇总至 `polymers`
- 字段与规则（来自 prompt 与 schema）
  - 基本字段（强制）：`polymer_name`、`polymer_type`、`components`（monomer 缩写列表）
  - 配比（互斥策略）：优先抽取明确类别到对应字段（`diamine_ratio`、`dianhydride_ratio`、`diisocyanate_ratio`、`diol_ratio`、`diacid_ratio`）；无法归类时写入 `ratio_values_text`；`feed_ratio_text` 始终保留原始上下文；`ratio_type` 支持 `mole`/`weight`/`unknown`
  - 分子量与方法：`mn_value`、`mw_value`、`pdi_value`、`mw_unit`、`test_method`（标准化缩写：GPC/SEC/Viscosity/NMR/LS）
  - 排除：不将单体当作聚合物；忽略商业参比品；缺少核心三字段的条目直接丢弃
- 输出
  - 每篇论文一个 `polymers.csv`（写回各自 JSON 目录）
- 当前说明
  - 目前 Polymer 与 Monomer 流程“分开运行”，未做自动联动；后续测试通过后，再把 Polymer 的配比与 Monomer 结果联动（例如按 monomer 别名匹配 SMILES）

## Monomer 规则（SMILES 一致性）
- 判定口径：
  - 正文 `smiles` 为空 → invalid
  - 三库（PubChem/OPSIN/CACTUS）结果全部为空 → invalid（允许 1~2 个为空）
  - 正文 SMILES 与任一非空外部 SMILES 经 RDKit 正则化后完全一致 → valid
- 输出口径：
  - valid 时：`smiles_final=正文 canonical smiles`
  - invalid 时：`smiles_final=""`

## 数据与文件
- 中间输入 JSONL：/share/lcc/dataflow-dp/data/monomer_input_full.jsonl（或你指定的路径）
- 每论文输出：
  - monomers.csv：单体信息与 SMILES 补全结果
  - polymers.csv：聚合物名称/类型/组成/分子量等
- 全局输出：
  - monomer_library.csv：跨论文的 API 命中单体记录（不做去重）
  - monomer_smiles_issues.csv：`smiles_valid == "invalid"` 的问题条目

## 常见问题
- 运行结束未见输出
  - 确认扫描到了 JSON（日志 `Found X JSON files`），并且 `content` 非空
  - 检查写 CSV 日志或目标目录是否可写
- 外部 API 报 429/503
  - 下调 `--api-workers` 或提高 `--api-sleep-every`/`--api-sleep-seconds`
  - 确认代理/凭据正常（`source /share/lcc/setup_env.sh`）
- polymer 抽取未产出
  - 确认文本确实包含满足身份校验（name/type/components）的聚合物条目
  - 查看 `polymers_raw`/`polymers` 在存储中的内容是否为空

## 代码参考
- 流水线与阶段：
  - [monomer_extract_pipeline.py](file:///share/lcc/dataflow-dp/pipelines/monomer_extract_pipeline.py)
    - PolymerExtractStage / MonomerSeedStage / MonomerSmilesEnrichStage / MonomerLibrarySaveStage
- Prompt 与 Schema：
  - [polymer.py](file:///share/lcc/dataflow-dp/prompts/polymer.py) / /share/lcc/prompt/polymer.md / /share/lcc/schema/polymer.json
  - [monomer.py](file:///share/lcc/dataflow-dp/prompts/monomer.py) / [schemas/monomer_schemas](file:///share/lcc/dataflow-dp/schemas/monomer_schemas)
