#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# 任务目标（中文注释说明）：
# - 将 Pipeline 的输入（为每个类别准备的 input.jsonl）与获取的结果（Vertex AI 批预测返回的结果）统一放在一个 IO 目录：
#   /uni-curator/user/lcc/lcc/dataflow-dp/io
# - 该脚本支持按 base-dir 扫描文献 JSON，按 batch-size 进行分片；为每个类别分别生成 input.jsonl，并提交至 Vertex AI 的批任务
# - 采用非阻塞提交（提交后立即返回 Job ID），再用统一的 60 秒轮询器查询 Job 状态；成功后将结果从 BigQuery 下载为 CSV，并保存在 IO 目录内
# - 为了便于断点续跑，提交成功的 Job 会记录到 IO 目录内的 .jobs_ledger.jsonl 文件；重启脚本会自动加载并继续轮询未完成的任务
#
# 使用示例：
#   python pipelines/property_extract_pipeline.py \
#     --base-dir /uni-curator/user/zwl/zwl/zwl/literature/selected_polyimide_papers \
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
# - 批预测结果 CSV：IO_DIR/{category}/results/{base}_{job_id}.csv
# - 任务台账文件：IO_DIR/.jobs_ledger.jsonl（追加写入，含 job_id、category、offset、limit、base_name、output_dir、状态）
#

import argparse
import glob
import json
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

import csv
import pandas as pd
from google.cloud import bigquery
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

def _download_bq_table_custom(table_id: str, output_file_path: str, project_id: str = None, location: str = "us-central1") -> str:
    """
    直接使用 BigQuery Client 下载数据
    """
    try:
        client = bigquery.Client(project=project_id, location=location)
        
        # 处理 table_id (如果是 bq:// 开头)
        if table_id.startswith("bq://"):
            table_id = table_id[5:].replace("/", ".")
            
        timeout_seconds = 86400
        bq_retry = bigquery.DEFAULT_RETRY.with_deadline(timeout_seconds)

        table = client.get_table(table_id, retry=bq_retry, timeout=timeout_seconds)
        
        # 只取 custom_id 和 response
        wanted = ["custom_id", "response"]
        schema_by_name = {f.name: f for f in table.schema}
        selected_fields = [schema_by_name[c] for c in wanted if c in schema_by_name]

        rows = client.list_rows(
            table,
            selected_fields=selected_fields,
            retry=bq_retry,
            timeout=timeout_seconds,
        )
        df = rows.to_dataframe()
        df.to_csv(output_file_path, index=False)
        return output_file_path
    except Exception as e:
        raise Exception(f"Error downloading BigQuery table {table_id}: {e}")


# 固定 IO 目录
IO_DIR = "/uni-curator/user/lcc/lcc/dataflow-dp/io"

# 结果写入配置
_CATEGORY_HEADERS = {
    "mechanical": [
        "doi", "file_path", "polymer_name", "record_type", "metric_group", "metric_type",
        "value", "temperature", "temperature_range", "frequency",
        "test_standard", "test_method", "test_conditions", "test_mode", "measurement_direction", "notes"
    ],
    "thermal": [
        "doi", "file_path", "polymer_name", "record_type",
        "value", "temperature", "temperature_range",
        "test_standard", "test_method", "test_conditions", "heating_rate", "decomposition_criterion", "atmosphere", "notes"
    ],
    "electrical": [
        "doi", "file_path", "polymer_name", "record_type",
        "value", "temperature", "frequency",
        "test_standard", "test_method", "test_conditions", "notes"
    ],
    "optical": [
        "doi", "file_path", "polymer_name", "record_type",
        "value", "temperature", "wavelength", "thickness",
        "test_standard", "test_method", "test_conditions", "ri_mode", "notes"
    ],
    "other": [
        "doi", "file_path", "polymer_name", "record_type",
        "value", "temperature",
        "test_standard", "test_method", "test_conditions", "notes"
    ]
}

def parse_vertex_response(resp):
    try:
        if isinstance(resp, dict):
            resp_json = resp
        elif isinstance(resp, str):
            raw_resp = resp.strip()
            if raw_resp.startswith('"') and raw_resp.endswith('"'):
                raw_resp = json.loads(raw_resp)
            resp_json = json.loads(raw_resp)
        else:
            return []

        if not isinstance(resp_json, dict):
            return []

        if "error" in resp_json:
            return []

        candidates = resp_json.get("candidates", [])
        if not candidates:
            return []

        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        if not parts:
            return []

        text_val = ""
        if "text" in parts[0]:
            text_val = parts[0]["text"]
        elif "functionCall" in parts[0]:
            text_val = json.dumps(parts[0]["functionCall"]["args"])
        else:
            text_val = json.dumps(parts[0])

        cleaned = (text_val or "").strip()
        if not cleaned:
            return []

        if cleaned.startswith("```"):
            import re
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return []
    except Exception:
        return []

def _distribute_results_to_csv(
    input_jsonl: str,
    result_csv: str,
    category: str,
    logger: logging.Logger,
    job_offset: int = 0,
    job_limit: int = 0
):
    input_meta = []
    try:
        with open(input_jsonl, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    input_meta.append({
                        "file_path": row.get("file_path"),
                        "doi": row.get("extracted_doi") or row.get("doi_hint") or ""
                    })
                except Exception:
                    input_meta.append(None)
    except Exception as e:
        logger.error(f"Error reading input file {input_jsonl}: {e}")
        return

    start_idx = max(0, int(job_offset or 0))
    end_idx = len(input_meta)
    if job_limit and int(job_limit) > 0:
        end_idx = min(end_idx, start_idx + int(job_limit))

    def _iter_result_rows(path: str):
        lower = str(path or "").lower()
        if lower.endswith(".jsonl"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = (line or "").strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        cid = rec.get("custom_id")
                        if not cid:
                            continue
                        yield cid, rec.get("response")
            except Exception as e:
                raise RuntimeError(f"Error reading jsonl result file {path}: {e}")
            return
        try:
            df = pd.read_csv(path)
            for _, row in df.iterrows():
                cid = row.get("custom_id")
                if not cid:
                    continue
                yield str(cid), row.get("response")
        except Exception as e:
            raise RuntimeError(f"Error reading csv result file {path}: {e}")

    results_map: Dict[int, list] = {i: [] for i in range(start_idx, end_idx)}
    try:
        for cid, resp in _iter_result_rows(result_csv):
            cid = str(cid or "")
            if not cid.startswith("req-"):
                continue
            try:
                local_idx = int(cid.split("-")[1])
                global_idx = local_idx + job_offset
            except Exception:
                continue
            if global_idx < start_idx or global_idx >= end_idx:
                continue
            properties = parse_vertex_response(resp)
            results_map[global_idx] = properties
    except Exception as e:
        logger.error(f"Error reading result file {result_csv}: {e}")

    success_count = 0
    for idx in range(start_idx, end_idx):
        meta = input_meta[idx]
        if not meta or not meta["file_path"]:
            continue

        target_file_path = meta["file_path"]
        target_doi = meta["doi"]

        target_dir = os.path.dirname(target_file_path)
        if not os.path.exists(target_dir):
            continue

        try:
            properties = results_map.get(idx, [])
            if isinstance(properties, dict):
                properties = [properties]
            if not isinstance(properties, list):
                properties = []

            output_csv_name = f"{category}.csv"
            output_csv_path = os.path.join(target_dir, output_csv_name)

            fixed_headers = _CATEGORY_HEADERS.get(category, [])
            final_headers = fixed_headers

            processed_properties = []
            for p in properties:
                if isinstance(p, dict):
                    p["file_path"] = target_file_path
                    p["doi"] = target_doi
                    processed_properties.append(p)

            with open(output_csv_path, 'w', encoding='utf-8', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=final_headers, extrasaction='ignore')
                writer.writeheader()
                for p in processed_properties:
                    row = {}
                    for k in final_headers:
                        val = p.get(k)
                        if isinstance(val, (dict, list)):
                            row[k] = json.dumps(val, ensure_ascii=False)
                        else:
                            row[k] = val
                    writer.writerow(row)
            success_count += 1
        except Exception as e:
            logger.error(f"Error distributing to {target_file_path}: {e}")

    logger.info(f"Distributed {success_count} results from {os.path.basename(result_csv)}")


# 项目内的工具/服务
from prompts.generic_md_prompt import MarkdownSchemaPrompt
from dataflow.serving.api_google_vertexai_serving import APIGoogleVertexAIServing

# 类别与对应的 Prompt/Schema 文件位置
# _CATEGORY_FILES = {
#     "mechanical": ("mechanical/mechanical_properties.md", "mechanical/mechanical_properties.json"),
#     "optical": ("optical/prompt_optical_properties.md", "optical/optical_properties.json"),
#     "electrical": ("electrical/polymer_electrical_properties.md", "electrical/electrical_properties.json"),
#     "other": ("other/polymer_other_properties.md", "other/other_properties.json"),
#     "thermal": ("thermal/polymer_thermal_properties.md", "thermal/thermal_properties.json"),
# }

# BMI
_CATEGORY_FILES = {
    "mechanical": ("/uni-curator/user/zwl/other_polymer_benchmark/epoxy_benchmark/prompts/mechanical/mechanical_properties.md", "/uni-curator/user/lcc/lcc/dataflow-dp/other_polymer/ladder_polymer/schemas/mechanical/mechanical_properties.json"),
    "optical": ("/uni-curator/user/zwl/other_polymer_benchmark/epoxy_benchmark/prompts/optical/prompt_optical_properties.md", "/uni-curator/user/zwl/other_polymer_benchmark/epoxy_benchmark/schemas/optical/optical_properties.json"), 
    "electrical": ("/uni-curator/user/lcc/lcc/dataflow-dp/other_polymer/ladder_polymer/prompts/electrical/polymer_electrical_properties.md", "/uni-curator/user/lcc/lcc/dataflow-dp/other_polymer/ladder_polymer/schemas/electrical/electrical_properties.json"),
    "other": ("/uni-curator/user/lcc/lcc/dataflow-dp/other_polymer/ladder_polymer/prompts/other/polymer_other_properties.md", "/uni-curator/user/lcc/lcc/dataflow-dp/other_polymer/ladder_polymer/schemas/other/other_properties.json"),
    "thermal": ("/uni-curator/user/lcc/lcc/dataflow-dp/other_polymer/ladder_polymer/prompts/thermal/polymer_thermal_properties.md", "/uni-curator/user/lcc/lcc/dataflow-dp/other_polymer/ladder_polymer/schemas/thermal/thermal_properties.json"),
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

def _append_ledger_record(ledger_path: str, record: dict, logger: logging.Logger):
    try:
        with open(ledger_path, "a", encoding="utf-8") as f_ledger:
            f_ledger.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Failed writing ledger {ledger_path}: {e}")

def _load_ledger_latest(ledger_path: str) -> Tuple[Dict[str, dict], Dict[str, str]]:
    latest: Dict[str, dict] = {}
    shard_to_job: Dict[str, str] = {}
    if not os.path.exists(ledger_path):
        return latest, shard_to_job
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            jid = rec.get("job_id")
            if not jid:
                continue
            latest[jid] = rec
            cat = rec.get("category")
            base_name = rec.get("base_name")
            offset = rec.get("offset")
            limit = rec.get("limit")
            status = (rec.get("status") or "").upper()
            if not cat or base_name is None or offset is None or limit is None:
                continue
            if "FAILED" in status or "CANCELLED" in status:
                continue
            shard_key = f"{cat}|{base_name}|{offset}|{limit}"
            shard_to_job[shard_key] = jid
    return latest, shard_to_job

def _expected_result_file(io_category_dir: str, job_id: str) -> str:
    base = os.path.basename(io_category_dir.rstrip("/"))
    short_id = job_id.split("/")[-1]
    return os.path.join(io_category_dir, "results", f"{base}_{short_id}.csv")

def _resume_jobs_from_ledger(
    ledger_latest: Dict[str, dict],
    cats: List[str],
    serving: APIGoogleVertexAIServing,
    poll_interval_sec: int,
    logger: logging.Logger,
    max_checks_per_cycle: int,
):
    jobs_by_dir: Dict[str, List[str]] = {}
    for jid, rec in ledger_latest.items():
        cat = rec.get("category")
        if cat not in cats:
            continue
        io_category_dir = rec.get("output_dir")
        if not io_category_dir or not os.path.isdir(io_category_dir):
            continue
        status = (rec.get("status") or "").upper()
        if status in {"FAILED", "CANCELLED"}:
            continue
        out_file = rec.get("output_file") or _expected_result_file(io_category_dir, jid)
        if out_file and os.path.exists(out_file):
            continue
        jobs_by_dir.setdefault(io_category_dir, []).append(jid)
    for io_category_dir, job_ids in jobs_by_dir.items():
        poll_and_save_results(
            job_ids=job_ids,
            io_category_dir=io_category_dir,
            serving=serving,
            poll_interval_sec=poll_interval_sec,
            logger=logger,
            ledger_path=os.path.join(IO_DIR, ".jobs_ledger.jsonl"),
            ledger_latest=ledger_latest,
            max_checks_per_cycle=max_checks_per_cycle,
        )

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

def _extract_doi_from_dir(dir_path: str, marker: str = "selected_polyimide_papers") -> str:
    try:
        abs_dir = os.path.abspath(dir_path)
        parts = abs_dir.replace("\\", "/").split("/")
        if marker in parts:
            idx = parts.index(marker)
            if idx < len(parts) - 1:
                doi_candidate = "/".join([p for p in parts[idx + 1 :] if p])
                if doi_candidate:
                    return doi_candidate
    except Exception:
        pass
    return os.path.basename(dir_path)

def prepare_input_jsonl(category: str, files: List[str], out_jsonl: str) -> int:
    """
    为给定类别构建 input.jsonl：
    每行包含：file_path, content, doi_hint（若有）, extracted_doi
    DOI 提取逻辑：
    - 如果文件在 selected_polyimide_papers 或其子目录下，
      取其父目录名（或父目录相对路径）作为 DOI。
    - 示例：.../selected_polyimide_papers/10.1007/3-540-06554-7_11/xxx.json
      -> DOI = 10.1007/3-540-06554-7_11
    """
    _ensure_dir(os.path.dirname(out_jsonl))
    written = 0
    
    with open(out_jsonl, "w", encoding="utf-8") as f_out:
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    content_json = json.load(f)
                text_content = content_json.get("content", "")
                if not text_content:
                    continue
                
                dir_path = os.path.dirname(fp)
                doi_candidate = _extract_doi_from_dir(dir_path)

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

def submit_jobs_for_category(
    category: str,
    input_jsonl: str,
    prompt_dir: str,
    schema_dir: str,
    llm_batch: int,
    max_chunk_len: int,
    serving: APIGoogleVertexAIServing,
    ledger_path: str,
    logger: logging.Logger,
    shard_to_job: Dict[str, str],
    ledger_latest: Dict[str, dict],
    use_batch: bool = True,
) -> List[str]:
    """
    对某个类别的 input.jsonl 执行分批提交：
    - 读取 input.jsonl，按 llm_batch 分批构建 prompts
    - 使用 Vertex AI 提交（Batch 或 Realtime）
    - 返回 job_id (Batch) 或 空 (Realtime)
    """
    md_path, schema_path = _resolve_prompt_and_schema(category, prompt_dir, schema_dir)
    prompt = MarkdownSchemaPrompt(md_path=md_path, schema_path=schema_path)
    json_schema = prompt.build_json_schema()
    input_aux_keys = ["file_path", "doi_hint", "extracted_doi"]
    job_ids: List[str] = []
    
    # 1. 读取所有行
    all_rows: List[dict] = []
    base_name = os.path.basename(input_jsonl)
    with open(input_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                all_rows.append(row)
            except Exception:
                continue

    total_rows = len(all_rows)
    if total_rows == 0:
        logger.info(f"No rows to submit for category {category}")
        return []

    # 2. 检查是否已有任务 (shard_key 使用 total_rows)
    # 由于不再分小批，我们用整个文件作为一个 shard
    # key 格式：category|base_name|offset=0|limit=total_rows
    # 
    # 注意：这里我们移除了 ledger 相关的检查逻辑
    # 
    shard_key = f"{category}|{base_name}|0|{total_rows}"
    
    # 3. 构建 prompts 并提交
    chunk_size = _env_int("PROPS_BATCH_CHUNK_SIZE", 1000)
    if chunk_size <= 0: chunk_size = 1000

    submitted = 0
    for start in range(0, total_rows, chunk_size):
        end = min(start + chunk_size, total_rows)
        chunk_rows = all_rows[start:end]
        
        prompts = build_prompts_for_batch(prompt, chunk_rows, max_chunk_len=max_chunk_len, input_aux_keys=input_aux_keys)
        if not prompts:
            continue

        try:
            # 提交当前分片
            job_name_or_list = serving.generate_from_input(
                user_inputs=prompts,
                system_prompt="",
                json_schema=json_schema,
                use_function_call=False,
                use_batch=use_batch,
                batch_wait=False,
            )
            
            if use_batch:
                ids = job_name_or_list if isinstance(job_name_or_list, list) else [job_name_or_list]
                for jid in ids:
                    if not jid: continue
                    job_ids.append(jid)
                    
                    # 记录到 ledger
                    rec = {
                        "job_id": jid,
                        "category": category,
                        "status": "PENDING",
                        "base_name": base_name,
                        "output_dir": os.path.dirname(input_jsonl),
                        "offset": start, # 这里的 offset 是全局偏移
                        "limit": len(chunk_rows),
                        "ts": time.time(),
                    }
                    _append_ledger_record(ledger_path, rec, logger)
                    ledger_latest[jid] = rec
                    shard_to_job[shard_key] = jid # 这里的 shard_key 只是为了更新内存状态
                    logger.info(f"Added job {jid} for {category} chunk {start}-{end}.")
            else:
                # Realtime 模式: generate_from_input 返回的是结果列表
                # 我们需要模拟 Batch 的输出格式 (CSV with custom_id, response)
                # custom_id 格式: req-{local_idx}
                responses = job_name_or_list if isinstance(job_name_or_list, list) else [job_name_or_list]
                
                # 保存为 CSV
                # 结果文件名：{category}/results/{base}_realtime_{start}_{end}.csv
                res_dir = os.path.join(os.path.dirname(input_jsonl), "results")
                _ensure_dir(res_dir)
                out_file = os.path.join(res_dir, f"{base_name}_realtime_{start}_{end}.csv")
                
                with open(out_file, "w", encoding="utf-8", newline="") as f_csv:
                    writer = csv.writer(f_csv)
                    writer.writerow(["custom_id", "response"])
                    for i, resp in enumerate(responses):
                        # custom_id 需要与 batch 模式保持一致: req-{i}
                        # 注意：_distribute_results_to_csv 中会用 global_idx = local_idx + job_offset
                        # 这里 local_idx 就是 i
                        writer.writerow([f"req-{i}", json.dumps({"candidates": [{"content": {"parts": [{"text": resp}]}}]}, ensure_ascii=False)])
                
                logger.info(f"Saved realtime results: {out_file}")
                
                # 立即分发
                _distribute_results_to_csv(input_jsonl, out_file, category, logger, job_offset=start, job_limit=len(chunk_rows))

            submitted += len(chunk_rows)

        except Exception as e:
            logger.error(f"Submit failed {category} rows {start}-{end}: {e}")

    logger.info(f"Submitted {submitted} rows for category {category} (total files={total_rows})")
    return job_ids

def poll_and_save_results(
    job_ids: List[str],
    io_category_dir: str,
    serving: APIGoogleVertexAIServing,
    poll_interval_sec: int,
    logger: logging.Logger,
    ledger_path: Optional[str] = None,
    ledger_latest: Optional[Dict[str, dict]] = None,
    max_checks_per_cycle: int = 10,
):
    """
    统一 60 秒轮询：
    - 对所有 job_id 执行状态查询
    - 成功时下载结果，并保存到 io/{category}/results/{base}_{job_id}.csv
    - 失败/取消记录日志
    """
    _ensure_dir(os.path.join(io_category_dir, "results"))
    unique_job_ids = list(dict.fromkeys([j for j in job_ids if j]))
    remaining = set(unique_job_ids)
    cursor = 0
    while remaining:
        logger.info(f"Polling {len(remaining)} jobs ...")
        done_now: List[str] = []
        checks = min(max(1, max_checks_per_cycle), len(remaining))
        to_check: List[str] = []
        while len(to_check) < checks and remaining:
            if cursor >= len(unique_job_ids):
                cursor = 0
            jid = unique_job_ids[cursor]
            cursor += 1
            if jid in remaining:
                to_check.append(jid)
        for jid in to_check:
            try:
                job = serving.batch_runner.genai_client.batches.get(name=jid)
                state = job.state
                if state == "JOB_STATE_SUCCEEDED":
                    # 下载 BigQuery 输出
                    out_uri = job.dest.bigquery_uri
                    out_file = _expected_result_file(io_category_dir, jid)
                    
                    # 解析 table_id
                    table_id = out_uri
                    if table_id.startswith("bq://"):
                        table_id = table_id[5:]
                    table_id = table_id.replace("/", ".")
                    
                    try:
                        _download_bq_table_custom(table_id, out_file)
                        logger.info(f"Saved results: {out_file}")

                        # 触发分发回原论文目录
                        # input.jsonl 文件位置：io_category_dir/{base_name}
                        # 我们可以从 ledger 或 job name 推断 input 文件路径
                        # 这里直接从 ledger 获取信息更稳妥，或者利用 job id 反查
                        # 简单的办法：ledger_latest 里有 base_name 和 output_dir
                        input_jsonl = None
                        job_offset = 0
                        job_limit = 0
                        if ledger_latest and jid in ledger_latest:
                            rec = ledger_latest[jid]
                            base_name = rec.get("base_name")
                            output_dir = rec.get("output_dir")
                            job_offset = rec.get("offset", 0)
                            job_limit = rec.get("limit", 0)
                            if base_name and output_dir:
                                input_jsonl = os.path.join(output_dir, base_name)
                        
                        if input_jsonl and os.path.exists(input_jsonl):
                            cat_for_dist = os.path.basename(io_category_dir.rstrip("/")) # e.g. thermal
                            _distribute_results_to_csv(input_jsonl, out_file, cat_for_dist, logger, job_offset=job_offset, job_limit=job_limit)
                        else:
                            logger.warning(f"Skipping distribution for {jid}: input file not found or unknown")

                        if ledger_path and ledger_latest is not None:
                            prev = ledger_latest.get(jid, {})
                            rec = dict(prev)
                            rec.update({"job_id": jid, "status": "SUCCEEDED", "output_file": out_file, "completed_ts": time.time()})
                            _append_ledger_record(ledger_path, rec, logger)
                            ledger_latest[jid] = rec
                        done_now.append(jid)
                    except Exception as e:
                        logger.error(f"Failed to download/process results for {jid}: {e}")

                elif state in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED"):
                    logger.error(f"Job {jid} failed: {getattr(job, 'error', None)}")
                    if ledger_path and ledger_latest is not None:
                        prev = ledger_latest.get(jid, {})
                        rec = dict(prev)
                        rec.update({"job_id": jid, "status": "FAILED" if state == "JOB_STATE_FAILED" else "CANCELLED", "completed_ts": time.time()})
                        _append_ledger_record(ledger_path, rec, logger)
                        ledger_latest[jid] = rec
                    done_now.append(jid)
                else:
                    # PENDING / RUNNING / QUEUED
                    pass
            except Exception as e:
                logger.error(f"Poll error {jid}: {e}")
        for jid in done_now:
            remaining.discard(jid)

        if remaining and not done_now:
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

    # 优先加载环境变量配置（如果没提供参数）
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT_ID"):
        # 尝试加载 setup_env.sh
        setup_env_path = os.getenv("LCC_SETUP_ENV_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "setup_env.sh"))
        if os.path.exists(setup_env_path):
             with open(setup_env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("export "):
                         p = line.split(" ", 1)[1]
                         if "=" in p:
                             k, v = p.split("=", 1)
                             v = v.strip().strip('"\'')
                             os.environ[k] = v

    # 初始化 Vertex AI Serving
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
    if not project_id:
        logger.warning("Environment variables GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID not set. Trying to load from setup_env.sh...")
    
    serving = APIGoogleVertexAIServing(
        project=project_id,
        location="us-central1",
        model_name="gemini-2.5-flash",
        use_batch=args.use_batch,
        max_tokens=64000,
    )

    # 统一 IO 台账路径
    _ensure_dir(IO_DIR)
    ledger_path = os.path.join(IO_DIR, ".jobs_ledger.jsonl")
    ledger_latest, shard_to_job = _load_ledger_latest(ledger_path)
    _resume_jobs_from_ledger(
        ledger_latest=ledger_latest,
        cats=cats,
        serving=serving,
        poll_interval_sec=args.poll_interval,
        logger=logger,
        max_checks_per_cycle=max(1, _env_int("MAX_POLL_CHECKS_PER_CYCLE", 10)),
    )
    # 主循环：按文件级分片推进
    all_submitted_jobs: List[tuple] = []
    
    # 优先加载环境变量配置（如果没提供参数）
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT_ID"):
        # 尝试加载 setup_env.sh
        setup_env_path = os.getenv("LCC_SETUP_ENV_PATH", "../setup_env.sh")
        if os.path.exists(setup_env_path):
             with open(setup_env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("export "):
                         p = line.strip().split(" ", 1)[1]
                         if "=" in p:
                             k, v = p.split("=", 1)
                             if k in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT_ID"):
                                 os.environ[k] = v.strip('"\'')

    # 按文件级分片处理
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
                shard_to_job=shard_to_job,
                ledger_latest=ledger_latest,
                use_batch=args.use_batch,
            )
            submitted_jobs.extend([(jid, cat, cat_dir) for jid in jids])
        if len(submitted_jobs) > 0:
            all_submitted_jobs.extend(submitted_jobs)
        if len(all_submitted_jobs) > max_concurrent:
            time.sleep(2)
    
    # 只有 Batch 模式需要轮询，Realtime 模式在 submit_jobs_for_category 里已经处理完了
    if args.use_batch:
        for jid, cat, cat_dir in all_submitted_jobs:
            poll_and_save_results(
                job_ids=[jid],
                io_category_dir=cat_dir,
                serving=serving,
                poll_interval_sec=args.poll_interval,
                logger=logger,
                ledger_path=ledger_path,
                ledger_latest=ledger_latest,
                max_checks_per_cycle=1,
            )

if __name__ == "__main__":
    main()
