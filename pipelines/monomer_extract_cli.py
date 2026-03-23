import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_DIR = os.path.dirname(ROOT_DIR)


def _ensure_local_libstdcpp():
    import ctypes
    try:
        if os.environ.get("LCC_LIBSTDCPP_READY") == "1":
            return
        candidates = []
        prefix = os.path.dirname(os.path.dirname(sys.executable))
        candidates.append(os.path.join(prefix, "lib", "libstdc++.so.6"))
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            candidates.append(os.path.join(conda_prefix, "lib", "libstdc++.so.6"))
        candidates.append("/opt/mamba/envs/lcc/lib/libstdc++.so.6")
        candidate = None
        for c in candidates:
            if os.path.exists(c):
                candidate = c
                break
        if not candidate:
            return
        lib_dir = os.path.dirname(candidate)
        cur_ld = os.environ.get("LD_LIBRARY_PATH", "")
        parts = [p for p in cur_ld.split(":") if p]
        if lib_dir not in parts:
            os.environ["LD_LIBRARY_PATH"] = lib_dir + (":" + cur_ld if cur_ld else "")
        try:
            ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
        except Exception:
            pass
        cur = os.environ.get("LD_PRELOAD", "").strip()
        parts = [p for p in cur.split() if p]
        if candidate not in parts:
            os.environ["LD_PRELOAD"] = candidate + (" " + cur if cur else "")
        os.environ["LCC_LIBSTDCPP_READY"] = "1"
    except Exception:
        pass


def _load_env_from_setup_env(path=None):
    try:
        candidates = []
        env_path = os.getenv("LCC_SETUP_ENV_PATH")
        if env_path:
            candidates.append(env_path)
        candidates.append("./setup_env.sh")
        candidates.append("../setup_env.sh")
        candidates.append("../../setup_env.sh")
        local_path = os.path.join(PARENT_DIR, "setup_env.sh")
        candidates.append(local_path)
        if path is not None:
            candidates.insert(0, path)
        picked = ""
        for cand in candidates:
            if cand and os.path.exists(cand):
                picked = cand
                break
        if not picked:
            return
        wanted = {
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GCP_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        }
        with open(picked, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("export "):
                    continue
                _, rest = line.split("export ", 1)
                if "=" not in rest:
                    continue
                key, value = rest.split("=", 1)
                key = key.strip()
                if key.startswith("MONOMER_") or key.startswith("PROPS_") or key in wanted:
                    pass
                else:
                    continue
                if os.environ.get(key):
                    continue
                value = value.strip()
                if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
                    value = value[1:-1]
                os.environ[key] = value
        for upper, lower in [("HTTP_PROXY", "http_proxy"), ("HTTPS_PROXY", "https_proxy")]:
            if upper in os.environ and lower not in os.environ:
                os.environ[lower] = os.environ[upper]
    except Exception:
        pass


_ensure_local_libstdcpp()
_load_env_from_setup_env()

if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)
if PARENT_DIR and PARENT_DIR not in sys.path:
    sys.path.append(PARENT_DIR)

from pipelines.monomer_extract_pipeline import ExtractMonomer
from operators.general.paper_input_generator import PaperJsonInputGenerator
from operators.monomer.monomer_csv_profile import build_monomer_csv_exporter


PAPER_ROOT = os.getenv("LCC_PAPER_ROOT", "../paper")


def _env_int(name, default):
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except Exception:
        return default


def _env_float(name, default):
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except Exception:
        return default


def find_json_files(base_path):
    gen = PaperJsonInputGenerator(exclude_basenames=["monomer.json", "monomers.json"])
    return gen.find_json_files(base_path)


def _extract_doi_from_dir(dir_path: str, marker: str = "selected_polyimide_papers") -> str:
    gen = PaperJsonInputGenerator(paper_root=PAPER_ROOT, marker=marker, exclude_basenames=["monomer.json", "monomers.json"])
    return gen.extract_doi_from_dir(dir_path)


def prepare_input_data(json_files, output_jsonl):
    gen = PaperJsonInputGenerator(paper_root=PAPER_ROOT, exclude_basenames=["monomer.json", "monomers.json"])
    return gen.write_jsonl_from_files(json_files, output_jsonl)


def run_pipeline(
    input_file,
    api_workers=8,
    api_timeout=5,
    api_sleep_every=100,
    api_sleep_seconds=0.2,
    api_row_workers=2,
    llm_max_workers=100,
    llm_max_tokens=12800,
    library_output_path=None,
    use_batch=False,
):
    pipeline = ExtractMonomer(
        entry_file_name=input_file,
        max_chunk_len=128000,
        api_workers=api_workers,
        api_timeout=api_timeout,
        api_sleep_every=api_sleep_every,
        api_sleep_seconds=api_sleep_seconds,
        api_row_workers=api_row_workers,
        llm_max_workers=llm_max_workers,
        llm_max_tokens=llm_max_tokens,
        library_output_path=library_output_path,
        use_batch=use_batch,
    )
    pipeline.compile()
    pipeline.forward(batch_size=50, resume_from_last=True)
    return pipeline


def _csv_has_data(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            header = f.readline()
            if not header:
                return False
            return bool(f.readline())
    except Exception:
        return False


def _write_csv(path, rows):
    import csv as _csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        if rows:
            writer.writerows(rows)


def _as_list(value):
    return value if isinstance(value, list) else []


def _row_to_csv_data(m, extracted_doi):
    final_doi = extracted_doi if extracted_doi else m.get("doi", "")
    return {
        "doi": final_doi,
        "abbreviation": ";".join(m.get("abbreviation", [])),
        "full_name": ";".join(m.get("full_name", [])),
        "smiles": m.get("smiles"),
        "smiles_can": m.get("smiles_can", ""),
        "smiles_pubchem": m.get("smiles_pubchem", ""),
        "smiles_pubchem_can": m.get("smiles_pubchem_can", ""),
        "smiles_opsin": m.get("smiles_opsin", ""),
        "smiles_opsin_can": m.get("smiles_opsin_can", ""),
        "smiles_cactus": m.get("smiles_cactus", ""),
        "smiles_cactus_can": m.get("smiles_cactus_can", ""),
        "smiles_api_can": m.get("smiles_api_can", ""),
        "smiles_final": m.get("smiles_final", ""),
        "smiles_valid": m.get("smiles_valid", ""),
    }


def _write_one(row, output_root=None):
    file_path = getattr(row, "file_path", None)
    monomers_info = _as_list(getattr(row, "monomers_info", None))
    extracted_doi = getattr(row, "extracted_doi", "")
    if not file_path or not os.path.exists(file_path):
        return "skip", 0
    if output_root:
        subdir_name = extracted_doi if extracted_doi else os.path.basename(os.path.dirname(file_path))
        dir_path = os.path.join(output_root, subdir_name)
        os.makedirs(dir_path, exist_ok=True)
    else:
        dir_path = os.path.dirname(file_path)
    csv_path = os.path.join(dir_path, "monomers.csv")
    if not monomers_info:
        if os.path.exists(csv_path) and _csv_has_data(csv_path):
            return "skip", 0
        _write_csv(csv_path, [])
        return "empty", 0
    csv_data = [_row_to_csv_data(m, extracted_doi) for m in monomers_info]
    _write_csv(csv_path, csv_data)
    return "nonempty", len(csv_data)


def _read_pipeline_df(pipeline):
    import copy
    storage_obj = pipeline.storage
    buffers = getattr(storage_obj, "_buffers", None)
    if isinstance(buffers, dict) and len(buffers) > 0:
        last_step = max(buffers.keys())
        reader = copy.copy(storage_obj)
        reader.operator_step = last_step
        return reader.read("dataframe")
    current_step = getattr(storage_obj, "operator_step", None)
    if isinstance(current_step, int) and current_step >= 0:
        for step in range(current_step, -1, -1):
            reader = copy.copy(storage_obj)
            reader.operator_step = step
            try:
                return reader.read("dataframe")
            except Exception:
                continue
    return storage_obj.step().read("dataframe")


def save_results_to_csv(pipeline, output_root=None, csv_workers=1, progress_every=500):
    try:
        df = _read_pipeline_df(pipeline)
    except Exception:
        return
    exporter = build_monomer_csv_exporter(csv_workers=csv_workers, progress_every=progress_every)
    return exporter.run(df, output_root=output_root)


def write_smiles_issue_csv(pipeline, output_path):
    import csv as _csv
    try:
        df = _read_pipeline_df(pipeline)
    except Exception:
        return 0
    if df is None or len(df) == 0:
        return 0
    rows = []
    for row in df.itertuples(index=False):
        file_path = getattr(row, "file_path", None)
        monomers_info = _as_list(getattr(row, "monomers_info", None))
        extracted_doi = getattr(row, "extracted_doi", "")
        for m in monomers_info:
            smiles_valid = m.get("smiles_valid", "")
            if smiles_valid != "invalid":
                continue
            rows.append({
                "file_path": file_path,
                "doi": extracted_doi if extracted_doi else m.get("doi"),
                "abbreviation": ";".join(m.get("abbreviation", [])),
                "full_name": ";".join(m.get("full_name", [])),
                "smiles_pubchem": str(m.get("smiles_pubchem", "")).strip(),
                "smiles_opsin": str(m.get("smiles_opsin", "")).strip(),
                "smiles_cactus": str(m.get("smiles_cactus", "")).strip(),
                "smiles_final": str(m.get("smiles_final", "")).strip(),
                "smiles_valid": smiles_valid,
            })
    if not rows:
        return 0
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def concat_monomers_csv(scan_dir, output_path):
    import csv as _csv
    import glob
    csv_files = glob.glob(os.path.join(scan_dir, "**", "monomers.csv"), recursive=True)
    if not csv_files:
        return 0
    csv_files = sorted(csv_files)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    total_rows = 0
    with open(output_path, "w", encoding="utf-8", newline="") as fout:
        writer = _csv.DictWriter(fout, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for fpath in csv_files:
            try:
                with open(fpath, "r", encoding="utf-8") as fin:
                    reader = _csv.DictReader(fin)
                    for row in reader:
                        out_row = {col: row.get(col, "") for col in CSV_COLUMNS}
                        writer.writerow(out_row)
                        total_rows += 1
            except Exception:
                pass
    return total_rows


def poll_and_process(submitted_jobs, pipeline, output_dir, csv_workers, progress_every, smiles_issue_csv):
    completed_indices = []
    for i, job_info in enumerate(submitted_jobs):
        job_ids = job_info.get("job_ids", [])
        start_idx = job_info.get("start_idx", 0)
        limit = job_info.get("limit", 0)
        input_jsonl = job_info.get("input_jsonl", "")
        row_mapping = job_info.get("row_mapping", [])
        all_done = True
        if job_ids:
            try:
                client = pipeline.llm_serving.batch_runner.genai_client
                for jid in job_ids:
                    try:
                        job = client.batches.get(name=jid)
                        state = job.state
                        if state not in ("JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED"):
                            all_done = False
                            break
                    except Exception:
                        all_done = False
                        break
            except Exception:
                all_done = False
        if not all_done:
            continue
        try:
            temp_output_dir = output_dir if output_dir else os.path.dirname(input_jsonl)
            dummy_pipeline = pipeline.process_batch_result(job_ids, row_mapping, output_dir=temp_output_dir)
            save_results_to_csv(dummy_pipeline, output_root=output_dir, csv_workers=csv_workers, progress_every=progress_every)
            if smiles_issue_csv:
                base, ext = os.path.splitext(smiles_issue_csv)
                if not ext:
                    ext = ".csv"
                issue_path = f"{base}_{start_idx}_{limit}{ext}"
                write_smiles_issue_csv(dummy_pipeline, issue_path)
        except Exception:
            pass
        completed_indices.append(i)
    for i in sorted(completed_indices, reverse=True):
        submitted_jobs.pop(i)


def main(argv: Optional[list[str]] = None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=PAPER_ROOT)
    parser.add_argument("--input-jsonl", default="./data/monomer_input_full.jsonl")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument("--smiles-issue-csv", default="./data/monomer_smiles_issues.csv")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--library-output-path", default=None)
    parser.add_argument("--use-batch", action="store_true")
    args = parser.parse_args(argv)

    base_dir = args.base_dir
    input_jsonl = args.input_jsonl
    if base_dir and not os.path.exists(base_dir):
        print(f"[monomer_extract_cli] base-dir not found: {base_dir}")
        return 2
    csv_workers = _env_int("MONOMER_CSV_WORKERS", min(4, os.cpu_count() or 1))
    progress_every = _env_int("MONOMER_PROGRESS_EVERY", 500)
    api_workers = _env_int("MONOMER_API_WORKERS", 4)
    api_timeout = _env_int("MONOMER_API_TIMEOUT", 10)
    api_sleep_every = _env_int("MONOMER_API_SLEEP_EVERY", 1000)
    api_sleep_seconds = _env_float("MONOMER_API_SLEEP_SECONDS", 0.2)
    api_row_workers = _env_int("MONOMER_API_ROW_WORKERS", 4)
    llm_max_workers = _env_int("MONOMER_LLM_MAX_WORKERS", 100)
    llm_max_tokens = _env_int("MONOMER_LLM_MAX_TOKENS", 12800)

    gen = PaperJsonInputGenerator(exclude_basenames=["monomer.json", "monomers.json"])
    start_global = max(0, int(args.offset or 0))
    limit_global = int(args.limit or 0)

    def run_one_batch_files(start_idx: int, batch_files: list[str]):
        if not batch_files:
            return None
        base, ext = os.path.splitext(input_jsonl)
        if not ext:
            ext = ".jsonl"
        batch_jsonl = f"{base}_{start_idx}_{len(batch_files)}{ext}"
        count = prepare_input_data(batch_files, batch_jsonl)
        if count == 0:
            return None
        pipeline = run_pipeline(
            batch_jsonl,
            api_workers=api_workers,
            api_timeout=api_timeout,
            api_sleep_every=api_sleep_every,
            api_sleep_seconds=api_sleep_seconds,
            api_row_workers=api_row_workers,
            llm_max_workers=llm_max_workers,
            llm_max_tokens=llm_max_tokens,
            library_output_path=args.library_output_path,
            use_batch=args.use_batch,
        )
        save_results_to_csv(pipeline, output_root=args.output_dir, csv_workers=csv_workers, progress_every=progress_every)
        if args.smiles_issue_csv:
            base_issue, ext_issue = os.path.splitext(args.smiles_issue_csv)
            if not ext_issue:
                ext_issue = ".csv"
            issue_path = f"{base_issue}_{start_idx}_{len(batch_files)}{ext_issue}" if args.batch_size > 0 else args.smiles_issue_csv
            write_smiles_issue_csv(pipeline, issue_path)
        return pipeline

    def iter_files_with_offset_limit():
        idx = 0
        emitted = 0
        for p in gen.iter_json_files(base_dir):
            if idx < start_global:
                idx += 1
                continue
            if limit_global and emitted >= limit_global:
                break
            yield p
            idx += 1
            emitted += 1

    def iter_batches():
        batch_size = int(args.batch_size or 0)
        if batch_size <= 0:
            batch_size = _env_int("MONOMER_FILE_BATCH_SIZE", 1000)
        if batch_size <= 0:
            batch_size = 1000
        batch = []
        start_idx = start_global
        for p in iter_files_with_offset_limit():
            batch.append(p)
            if len(batch) >= batch_size:
                yield start_idx, batch
                start_idx += len(batch)
                batch = []
        if batch:
            yield start_idx, batch

    if args.use_batch:
        pipeline = ExtractMonomer(
            entry_file_name="dummy",
            max_chunk_len=128000,
            api_workers=api_workers,
            api_timeout=api_timeout,
            api_sleep_every=api_sleep_every,
            api_sleep_seconds=api_sleep_seconds,
            api_row_workers=api_row_workers,
            llm_max_workers=llm_max_workers,
            llm_max_tokens=llm_max_tokens,
            library_output_path=args.library_output_path,
            use_batch=True,
        )
        submitted_jobs = []
        max_concurrent = _env_int("MONOMER_MAX_CONCURRENT_BATCHES", 2)
        if max_concurrent < 1:
            max_concurrent = 1
        for start_idx, batch_files in iter_batches():
            while len(submitted_jobs) >= max_concurrent:
                poll_and_process(submitted_jobs, pipeline, args.output_dir, csv_workers, progress_every, args.smiles_issue_csv)
                if len(submitted_jobs) >= max_concurrent:
                    time.sleep(60)
            base, ext = os.path.splitext(input_jsonl)
            if not ext:
                ext = ".jsonl"
            batch_jsonl = f"{base}_{start_idx}_{len(batch_files)}{ext}"
            count = prepare_input_data(batch_files, batch_jsonl)
            if count == 0:
                continue
            try:
                job_ids, row_mapping = pipeline.submit_batch(batch_jsonl)
                submitted_jobs.append({
                    "job_ids": job_ids,
                    "row_mapping": row_mapping,
                    "input_jsonl": batch_jsonl,
                    "start_idx": start_idx,
                    "limit": len(batch_files),
                })
            except Exception:
                pass
        while submitted_jobs:
            poll_and_process(submitted_jobs, pipeline, args.output_dir, csv_workers, progress_every, args.smiles_issue_csv)
            if submitted_jobs:
                time.sleep(60)
    else:
        max_batches = 1 if not (args.batch_size and args.batch_size > 0) else _env_int("MONOMER_MAX_CONCURRENT_BATCHES", 1)
        if max_batches <= 1:
            for start_idx, batch_files in iter_batches():
                run_one_batch_files(start_idx, batch_files)
        else:
            with ThreadPoolExecutor(max_workers=max_batches) as executor:
                futures = []
                for start_idx, batch_files in iter_batches():
                    futures.append(executor.submit(run_one_batch_files, start_idx, batch_files))
                wait(futures)

    if args.library_output_path:
        concat_monomers_csv(args.output_dir or base_dir, args.library_output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
