#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 任务目标（中文注释说明）：
# - 将 Pipeline 的输入（为每个类别准备的 input.jsonl）与获取的结果（Vertex AI 批预测返回的结果）统一放在一个 IO 目录：
#   /uni-curator/user/lcc/lcc/dataflow-dp/io
# - 该脚本支持按 base-dir 扫描文献 JSON，按 batch-size 进行分片；为每个类别分别生成 input.jsonl，并提交至 Vertex AI 的批任务
# - 采用非阻塞提交（提交后立即返回 Job ID），再用统一的 60 秒轮询器查询 Job 状态；成功后将结果下载为 JSONL，并保存在 IO 目录内
# - 为了便于断点续跑，提交成功的 Job 会记录到 IO 目录内的 .jobs_ledger.jsonl 文件；重启脚本会自动加载并继续轮询未完成的任务
#
# 使用示例：
#   python pipelines/property_extract_pipeline.py \
#     --base-dir /path/to/selected_polyimide_papers \
#     --category optical,thermal,mechanical,other,electrical \
#     --use-batch \
#     --batch-size 500
#
# 重要参数说明：
# - --batch-size：文件级分片大小（一次批次选取多少个文献 JSON 文件）；若未指定，默认采用环境变量 PROPS_BATCH_CHUNK_SIZE 或 1000
# - MONOMER_LLM_BATCH（环境变量）：提示级提交批大小（每次向 Vertex AI 提交多少条提示）；默认 100
# - MAX_CONCURRENT_CATEGORIES（环境变量）：类别并发提交上限（同时 Running 的 Job 数控制）；建议 1–2
# - 轮询间隔固定为 60 秒，避免高频查询导致连接压力
#
# 输出路径命名规范：
# - IO_DIR = /uni-curator/user/lcc/lcc/dataflow-dp/io
# - 每个类别的 input.jsonl：IO_DIR/{category}/input_{offset}_{limit}.jsonl
# - 批预测结果 JSONL：IO_DIR/{category}/results/{base}_{job_id}.jsonl
# - 任务台账文件：IO_DIR/.jobs_ledger.jsonl（追加写入，含 job_id、category、offset、limit、base_name、output_dir、状态）
#

import argparse
import glob
import json
import logging
import os
import sys
import time
from typing import List, Tuple

# 固定 IO 目录
IO_DIR = "/uni-curator/user/lcc/lcc/dataflow-dp/io"

# 项目内的工具/服务
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from prompts.generic_md_prompt import MarkdownSchemaPrompt
from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing

# 类别与对应的 Prompt/Schema 文件位置（相对 paths）
_CATEGORY_FILES = {
    "mechanical": ("mechanical/mechanical_properties.md", "../schemas/mechanical/mechanical_properties.json"),
    "optical": ("optical/prompt_optical_properties.md", "../schemas/optical/optical_properties.json"),
    "electrical": ("electrical/polymer_electrical_properties.md", "../schemas/electrical/electrical_properties.json"),
    "other": ("other/polymer_other_properties.md", "../schemas/other/other_properties.json"),
    "thermal": ("thermal/polymer_thermal_properties.md", "../schemas/thermal/thermal_properties.json"),
}

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default

def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def _resolve_prompt_and_schema(category: str, prompt_dir: str, schema_dir: str) -> Tuple[str, str]:
    if category not in _CATEGORY_FILES:
        raise ValueError(f"Unsupported category: {category}")
    md_rel, schema_name = _CATEGORY_FILES[category]
    md_path = os.path.join(prompt_dir, md_rel)
    schema_path = os.path.join(schema_dir, schema_name)
    if not os.path.exists(md_path):
        raise FileNotFoundError(f"Prompt file not found: {md_path}")
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    return md_path, schema_path

def find_json_files(base_path: str) -> List[str]:
    if os.path.isfile(base_path) and base_path.lower().endswith(".json"):
        return [base_path]
    selected: List[str] = []
    for root, _, files in os.walk(base_path):
        json_files = [f for f in files if f.lower().endswith(".json")]
        if not json_files:
            continue
        parent = os.path.basename(root)
        preferred = [os.path.join(root, f) for f in json_files if os.path.splitext(f)[0] == parent]
        if preferred:
            selected.append(sorted(preferred)[0])
            continue
        candidates = [os.path.join(root, f) for f in json_files if f.lower() != "monomer.json"]
        if candidates:
            selected.append(sorted(candidates)[0])
        else:
            selected.append(os.path.join(root, sorted(json_files)[0]))
    return sorted(selected)

def prepare_input_jsonl(category: str, files: List[str], out_jsonl: str) -> int:
    """
    为给定类别构建 input.jsonl：
    每行包含：file_path, content, doi_hint（若有）, extracted_doi（使用文件夹名或相对路径作为候选）
    输出路径统一在 IO 目录，例如 io/{category}/input_{offset}_{limit}.jsonl
    """
    _ensure_dir(os.path.dirname(out_jsonl))
    written = 0
    paper_root = os.getenv("LCC_PAPER_ROOT", "../paper")
    with open(out_jsonl, "w", encoding="utf-8") as f_out:
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    content_json = json.load(f)
                text_content = content_json.get("content", "")
                if not text_content:
                    continue
                dir_path = os.path.dirname(fp)
                doi_candidate = ""
                try:
                    if paper_root and dir_path.startswith(paper_root + os.sep):
                        rel = os.path.relpath(dir_path, paper_root).replace("\\", "/").strip("/")
                        doi_candidate = rel
                except Exception:
                    doi_candidate = ""
                if not doi_candidate:
                    doi_candidate = os.path.basename(dir_path)
                entry = {
                    "file_path": fp,
                    "content": text_content,
                    "doi_hint": content_json.get("token", ""),
                    "extracted_doi": doi_candidate,
                }
                f_out.write(json.dumps(entry) + "\n")
                written += 1
            except Exception:
                continue
    return written

def build_prompts_for_batch(prompt: MarkdownSchemaPrompt, rows: List[dict], max_chunk_len: int, input_aux_keys: List[str]) -> List[str]:
    """
    将一批 JSON 行转换为 LLM 提示列表；如果需要分块，可在此实现按 max_chunk_len 切分。
    当前实现：逐行构建一个系统提示 + 内容拼接的输入字符串。
    """
    prompts: List[str] = []
    for row in rows:
        raw_content = row.get("content", "")
        if not raw_content:
            continue
        kwargs = {k: row.get(k) for k in input_aux_keys}
        sys_prompt = prompt.build_prompt(**kwargs)
        if isinstance(sys_prompt, list):
            sys_prompt = sys_prompt[0]
        # 简单拼接：系统提示 + 原文内容
        prompts.append(f"{sys_prompt}{raw_content}")
    return prompts

def submit_jobs_for_category(category: str, input_jsonl: str, prompt_dir: str, schema_dir: str, llm_batch: int, max_chunk_len: int, serving: APIGoogleVertexAIServing, ledger_path: str, logger: logging.Logger) -> List[str]:
    """
    对某个类别的 input.jsonl 执行分批提交：
    - 读取 input.jsonl，按 llm_batch 分批构建 prompts
    - 使用 Vertex AI Batch 提交（batch_wait=False 即非阻塞），返回一组 job_id
    - 将提交的 job 记录到台账（ledger），以便重启后继续轮询
    """
    md_path, schema_path = _resolve_prompt_and_schema(category, prompt_dir, schema_dir)
    prompt = MarkdownSchemaPrompt(md_path=md_path, schema_path=schema_path)
    json_schema = prompt.build_json_schema()
    input_aux_keys = ["file_path", "doi_hint", "extracted_doi"]
    job_ids: List[str] = []
    submitted = 0
    batch_rows: List[dict] = []
    with open(input_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except Exception:
                continue
            batch_rows.append(row)
            if len(batch_rows) >= llm_batch:
                prompts = build_prompts_for_batch(prompt, batch_rows, max_chunk_len=max_chunk_len, input_aux_keys=input_aux_keys)
                if prompts:
                    try:
                        job_name_or_list = serving.generate_from_input(
                            user_inputs=prompts,
                            system_prompt="",
                            json_schema=json_schema,
                            use_function_call=False,
                            use_batch=True,
                            batch_wait=False,
                        )
                        ids = job_name_or_list if isinstance(job_name_or_list, list) else [job_name_or_list]
                        for jid in ids:
                            if not jid:
                                continue
                            job_ids.append(jid)
                            try:
                                with open(ledger_path, "a", encoding="utf-8") as f_ledger:
                                    f_ledger.write(json.dumps({
                                        "job_id": jid,
                                        "category": category,
                                        "status": "PENDING",
                                        "base_name": os.path.basename(input_jsonl),
                                        "output_dir": os.path.dirname(input_jsonl),
                                        "offset": submitted,
                                        "limit": llm_batch,
                                        "ts": time.time()
                                    }) + "\n")
                            except Exception:
                                pass
                            logger.info(f"Added job {jid} for {category}.")
                    except Exception as e:
                        logger.error(f"Submit failed {category} rows {submitted}-{submitted+llm_batch}: {e}")
                submitted += len(batch_rows)
                batch_rows.clear()
    if batch_rows:
        prompts = build_prompts_for_batch(prompt, batch_rows, max_chunk_len=max_chunk_len, input_aux_keys=input_aux_keys)
        if prompts:
            try:
                job_name_or_list = serving.generate_from_input(
                    user_inputs=prompts,
                    system_prompt="",
                    json_schema=json_schema,
                    use_function_call=False,
                    use_batch=True,
                    batch_wait=False,
                )
                ids = job_name_or_list if isinstance(job_name_or_list, list) else [job_name_or_list]
                for jid in ids:
                    if not jid:
                        continue
                    job_ids.append(jid)
                    try:
                        with open(ledger_path, "a", encoding="utf-8") as f_ledger:
                            f_ledger.write(json.dumps({
                                "job_id": jid,
                                "category": category,
                                "status": "PENDING",
                                "base_name": os.path.basename(input_jsonl),
                                "output_dir": os.path.dirname(input_jsonl),
                                "offset": submitted,
                                "limit": len(batch_rows),
                                "ts": time.time()
                            }) + "\n")
                    except Exception:
                        pass
                    logger.info(f"Added job {jid} for {category}.")
            except Exception as e:
                logger.error(f"Submit failed {category} rows {submitted}-{submitted+len(batch_rows)}: {e}")
        submitted += len(batch_rows)
        batch_rows.clear()
    if submitted == 0:
        logger.info(f"No rows to submit for category {category}")
    else:
        logger.info(f"Submitted {submitted} rows for category {category} (llm_batch={llm_batch})")
    return job_ids

def poll_and_save_results(job_ids: List[dict], io_category_dir: str, serving: APIGoogleVertexAIServing, poll_interval_sec: int, logger: logging.Logger):
    """
    统一 60 秒轮询：
    - 对所有 job_id 执行状态查询
    - 成功时下载结果，并保存到 io/{category}/results/{base}_{job_id}.jsonl
    - 失败/取消记录日志
    """
    _ensure_dir(os.path.join(io_category_dir, "results"))
    remaining = set(job_ids)
    while remaining:
        logger.info(f"Polling {len(remaining)} jobs ...")
        done_now: List[str] = []
        for jid in list(remaining):
            try:
                job = serving.batch_runner.genai_client.batches.get(name=jid)
                state = job.state
                if state == "JOB_STATE_SUCCEEDED":
                    # 下载 BigQuery 输出
                    out_uri = job.dest.bigquery_uri
                    res_map = serving.batch_runner.retrieve_results(out_uri)
                    base = os.path.basename(io_category_dir.rstrip("/"))
                    # 结果文件名：{category}/results/{base}_{jobid}.jsonl
                    short_id = jid.split("/")[-1]
                    out_file = os.path.join(io_category_dir, "results", f"{base}_{short_id}.jsonl")
                    with open(out_file, "w", encoding="utf-8") as f_out:
                        for cid, resp in res_map.items():
                            f_out.write(json.dumps({"custom_id": cid, "response": resp}) + "\n")
                    logger.info(f"Saved results: {out_file}")
                    done_now.append(jid)
                elif state in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED"):
                    logger.error(f"Job {jid} failed: {getattr(job, 'error', None)}")
                    done_now.append(jid)
                else:
                    # PENDING / RUNNING / QUEUED
                    pass
            except Exception as e:
                logger.error(f"Poll error {jid}: {e}")
        for jid in done_now:
            remaining.discard(jid)
        if remaining:
            time.sleep(max(1, poll_interval_sec))

def main():
    # 统一日志格式
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logger = logging.getLogger("property_pipeline")
    # 解析参数
    p = argparse.ArgumentParser()
    p.add_argument("--category", required=True, help="单类别或逗号分隔多个类别：optical,thermal,mechanical,other,electrical")
    p.add_argument("--base-dir", required=True, help="包含文献 JSON 的根目录")
    p.add_argument("--prompt-dir", default=os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts"))
    p.add_argument("--schema-dir", default=os.path.join(os.path.dirname(os.path.dirname(__file__)), "schemas"))
    p.add_argument("--batch-size", type=int, default=0, help="文件级分片大小；未指定时采用 PROPS_BATCH_CHUNK_SIZE 或 1000")
    p.add_argument("--offset", type=int, default=0, help="起始索引")
    p.add_argument("--limit", type=int, default=0, help="总处理上限（可选）")
    p.add_argument("--use-batch", action="store_true", help="启用 Vertex AI 批预测")
    p.add_argument("--poll-interval", type=int, default=60, help="轮询间隔秒数")
    p.add_argument("--max-chunk-len", type=int, default=32000, help="分块最大长度（拼提示时用）")
    args = p.parse_args()

    cats = [c.strip().lower() for c in args.category.split(",") if c.strip()]
    allowed = {"electrical", "mechanical", "optical", "other", "thermal"}
    cats = [c for c in cats if c in allowed]
    if not cats:
        raise SystemExit(1)

    # 计算 batch-size
    file_batch_size = args.batch_size if args.batch_size and args.batch_size > 0 else _env_int("PROPS_BATCH_CHUNK_SIZE", 1000)
    llm_batch = _env_int("MONOMER_LLM_BATCH", 100)
    max_concurrent = _env_int("MAX_CONCURRENT_CATEGORIES", 2)
    logger.info(f"Running categories={','.join(cats)} base_dir={args.base_dir} file_batch_size={file_batch_size} llm_batch={llm_batch} concurrent={max_concurrent}")

    # 准备全量文件列表与范围
    all_files = find_json_files(args.base_dir)
    all_files.sort()
    start_global = max(0, args.offset)
    end_global = len(all_files)
    if args.limit and args.limit > 0:
        end_global = min(end_global, start_global + args.limit)
    if start_global >= end_global:
        logger.info("No files in selected range.")
        return
    logger.info(f"Auto-sharding: total={len(all_files)} range=[{start_global},{end_global})")

    # 初始化 Vertex AI Serving
    serving = APIGoogleVertexAIServing(
        project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"),
        location="us-central1",
        model_name="gemini-2.5-flash",
        use_batch=args.use_batch,
    )

    # 统一 IO 台账路径
    _ensure_dir(IO_DIR)
    ledger_path = os.path.join(IO_DIR, ".jobs_ledger.jsonl")
    # 主循环：按文件级分片推进
    all_submitted_jobs: List[tuple] = []
    for start_idx in range(start_global, end_global, file_batch_size):
        current_limit = min(file_batch_size, end_global - start_idx)
        logger.info(f"Starting batch offset={start_idx} limit={current_limit}")
        # 当前分片的文件列表
        cur_files = all_files[start_idx:start_idx + current_limit]
        # 为每个类别准备 input.jsonl（统一到 io/{category}）
        submitted_jobs: List[str] = []
        for cat in cats:
            cat_dir = os.path.join(IO_DIR, cat)
            _ensure_dir(cat_dir)
            input_jsonl = os.path.join(cat_dir, f"input_{start_idx}_{current_limit}.jsonl")
            rows_written = prepare_input_jsonl(cat, cur_files, input_jsonl)
            logger.info(f"Prepared input_jsonl category {cat} files {len(cur_files)} rows {rows_written} path {input_jsonl}")
            if rows_written == 0:
                continue
            # 提交该类别的 Job（分批、非阻塞）
            jids = submit_jobs_for_category(
                category=cat,
                input_jsonl=input_jsonl,
                prompt_dir=args.prompt_dir,
                schema_dir=args.schema_dir,
                llm_batch=llm_batch,
                max_chunk_len=args.max_chunk_len,
                serving=serving,
                ledger_path=ledger_path,
                logger=logger,
            )
            submitted_jobs.extend([(jid, cat, cat_dir) for jid in jids])
        if len(submitted_jobs) > 0:
            all_submitted_jobs.extend(submitted_jobs)
        if len(all_submitted_jobs) > max_concurrent:
            time.sleep(2)
    for jid, cat, cat_dir in all_submitted_jobs:
        poll_and_save_results([jid], cat_dir, serving, args.poll_interval, logger)

if __name__ == "__main__":
    main()
