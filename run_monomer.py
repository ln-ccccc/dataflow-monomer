import argparse
import copy
import csv
import glob
import json
import os
import sys
import ctypes
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import pandas as pd
import types

def _ensure_local_libstdcpp():
    try:
        if os.environ.get("LCC_LIBSTDCPP_READY") == "1":
            return
        candidates = []
        prefix = os.path.dirname(os.path.dirname(sys.executable))
        lib_dir = os.path.join(prefix, "lib")
        candidates.append(os.path.join(lib_dir, "libstdc++.so.6"))
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
        if sys.argv and isinstance(sys.argv[0], str) and os.path.isfile(sys.argv[0]):
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        pass


_ensure_local_libstdcpp()

def _patch_file_storage_write():
    try:
        import dataflow.utils.storage as _s
        if getattr(_s, "LCC_LOCAL_STORAGE", False):
            return
        import pandas as _pd
        def clean_surrogates(obj):
            if isinstance(obj, str):
                return obj.encode("utf-8", "replace").decode("utf-8")
            elif isinstance(obj, dict):
                return {k: clean_surrogates(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean_surrogates(i) for i in obj]
            elif isinstance(obj, (int, float, bool)) or obj is None:
                return obj
            else:
                try:
                    return clean_surrogates(str(obj))
                except:
                    return obj
        def _write(self, data):
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], dict):
                    cleaned = [clean_surrogates(item) for item in data]
                    dataframe = _pd.DataFrame(cleaned)
                else:
                    raise ValueError(f"Unsupported data type: {type(data[0]) if data else 'list'}")
            elif isinstance(data, _pd.DataFrame):
                dataframe = data.applymap(clean_surrogates)
            else:
                raise ValueError(f"Unsupported data type: {type(data)}")
            file_path = self._get_cache_file_path(self.operator_step + 1)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            if self.cache_type == "json":
                dataframe.to_json(file_path, orient="records", force_ascii=False, indent=2)
            elif self.cache_type == "jsonl":
                dataframe.to_json(file_path, orient="records", lines=True, force_ascii=False)
            elif self.cache_type == "csv":
                dataframe.to_csv(file_path, index=False)
            elif self.cache_type == "parquet":
                dataframe.to_parquet(file_path)
            elif self.cache_type == "pickle":
                dataframe.to_pickle(file_path)
            else:
                raise ValueError(f"Unsupported file type: {self.cache_type}")
            return file_path
        _s.FileStorage.write = _write
    except Exception:
        pass

_patch_file_storage_write()


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

def _load_env_from_setup_env(path=None):
    if path is None:
        path = os.getenv("LCC_SETUP_ENV_PATH", "/share/lcc/setup_env.sh")
    if not os.path.exists(path):
        return
    wanted = {
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GCP_PROJECT_ID",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
    with open(path, "r", encoding="utf-8") as f:
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


_load_env_from_setup_env()
for upper, lower in [("HTTP_PROXY", "http_proxy"), ("HTTPS_PROXY", "https_proxy")]:
    if upper in os.environ and lower not in os.environ:
        os.environ[lower] = os.environ[upper]

# def _ensure_no_proxy_for_gcp():
#     additions = [
#         "googleapis.com",
#         ".googleapis.com",
#         "google.com",
#         ".google.com",
#         "gstatic.com",
#         ".gstatic.com",
#         "googleusercontent.com",
#         ".googleusercontent.com",
#     ]
#     for var in ["no_proxy", "NO_PROXY"]:
#         cur = os.environ.get(var, "")
#         parts = [p.strip() for p in cur.split(",") if p.strip()]
#         s = set(parts)
#         for a in additions:
#             s.add(a)
#         os.environ[var] = ",".join(sorted(s))

# _ensure_no_proxy_for_gcp()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)
PARENT_DIR = os.path.dirname(ROOT_DIR)
if PARENT_DIR and PARENT_DIR not in sys.path:
    sys.path.append(PARENT_DIR)
from pipelines.monomer_extract_pipeline import ExtractMonomer

PAPER_ROOT = "/share/lcc/paper"

CSV_COLUMNS = [
    "doi",
    "abbreviation",
    "full_name",
    "smiles",
    "smiles_can",
    "cas_no",
    "iupac_name",
    "smiles_pubchem",
    "smiles_pubchem_can",
    "smiles_opsin",
    "smiles_opsin_can",
    "smiles_cactus",
    "smiles_cactus_can",
    "smiles_api_can",
    "smiles_final",
    "smiles_valid",
]

def find_json_files(base_path):
    if os.path.isfile(base_path) and base_path.lower().endswith(".json"):
        return [base_path]
    return glob.glob(os.path.join(base_path, "**", "*.json"), recursive=True)

def prepare_input_data(json_files, output_jsonl):
    print(f"Found {len(json_files)} JSON files. Preparing input...")
    count = 0
    with open(output_jsonl, 'w', encoding='utf-8') as f:
        for file_path in json_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as fin:
                    content_json = json.load(fin)
                text_content = content_json.get('content', '')
                if not text_content:
                    continue

                dir_path = os.path.dirname(file_path)
                doi_candidate = ""
                try:
                    if dir_path.startswith(PAPER_ROOT + os.sep):
                        rel = os.path.relpath(dir_path, PAPER_ROOT).replace("\\", "/").strip("/")
                        if rel.endswith("/merged"):
                            rel = rel[: -len("/merged")]
                        if rel == "merged":
                            rel = ""
                        doi_candidate = rel
                except Exception:
                    doi_candidate = ""
                if not doi_candidate:
                    parent_dir = os.path.basename(dir_path)
                    doi_candidate = parent_dir.replace("merged", "").strip()

                entry = {
                    "file_path": file_path,
                    "content": text_content,
                    "doi_hint": content_json.get('token', ''),
                    "extracted_doi": doi_candidate
                }
                f.write(json.dumps(entry) + '\n')
                count += 1
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
    print(f"Prepared {count} entries in {output_jsonl}")
    return count

def run_pipeline(input_file, api_workers=8, api_timeout=5, api_sleep_every=100, api_sleep_seconds=0.2, api_row_workers=2, llm_max_workers=100, llm_max_tokens=12800, library_output_path=None):
    print("Initializing Pipeline...")
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
    )
    print("Compiling Pipeline...")
    pipeline.compile()
    print("Running Pipeline (this may take a while)...")
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
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        if rows:
            writer.writerows(rows)

def _as_list(value):
    return value if isinstance(value, list) else []

def _row_to_csv_data(m, extracted_doi):
    # extracted_doi 优先
    # 如果 m.get("doi") 存在，也可以作为参考
    final_doi = extracted_doi if extracted_doi else m.get("doi", "")
    return {
        "doi": final_doi,
        "abbreviation": ";".join(m.get("abbreviation", [])),
        "full_name": ";".join(m.get("full_name", [])),
        "smiles": m.get("smiles"),
        "smiles_can": m.get("smiles_can", ""),
        "cas_no": ";".join(m.get("cas_no", [])),
        "iupac_name": m.get("iupac_name"),
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
        # 如果指定了输出根目录，保持原有目录结构
        # 假设 file_path 是 /share/lcc/paper/DOI/.../file.json
        # 我们需要计算相对路径
        # 这里做一个简单的假设：保留从 input_base_dir 开始的相对路径
        # 但 input_base_dir 在这里不可用，所以我们直接用 file_path 的父目录名
        # 或者更稳妥地：如果 output_root 存在，直接在 output_root 下创建对应的 DOI 目录
        
        # 既然 extracted_doi 已经提取出来了，我们可以用它作为目录名
        # 如果 extracted_doi 为空，回退到原目录名
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
    print("Processing results...")
    try:
        df = _read_pipeline_df(pipeline)
    except Exception as e:
        print(f"Could not read dataframe directly: {e}")
        return

    total = len(df)
    print(f"Loaded {total} rows from storage. Writing CSVs with {csv_workers} workers...")
    if total == 0:
        return

    count = 0
    empty_written = 0
    nonempty_written = 0
    
    # 定义内部写函数，便于传入 output_root
    def _write_one_with_root(row):
        return _write_one(row, output_root=output_root)

    if csv_workers <= 1:
        for i, row in enumerate(df.itertuples(index=False), 1):
            status, rows_written = _write_one_with_root(row)
            if status == "empty":
                empty_written += 1
            elif status == "nonempty":
                nonempty_written += 1
                count += 1
            if progress_every and i % progress_every == 0:
                print(f"CSV progress: {i}/{total} rows processed")
    else:
        it = df.itertuples(index=False)
        futures = set()
        max_inflight = max(csv_workers * 2, 4)

        def submit_next():
            try:
                row = next(it)
            except StopIteration:
                return False
            futures.add(executor.submit(_write_one_with_root, row))
            return True

        with ThreadPoolExecutor(max_workers=csv_workers) as executor:
            for _ in range(max_inflight):
                if not submit_next():
                    break
            processed = 0
            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for fut in done:
                    status, rows_written = fut.result()
                    if status == "empty":
                        empty_written += 1
                    elif status == "nonempty":
                        nonempty_written += 1
                        count += 1
                    processed += 1
                    if progress_every and processed % progress_every == 0:
                        print(f"CSV progress: {processed}/{total} rows processed")
                    submit_next()
    print(f"Saved CSV results to {count} folders. Non-empty: {nonempty_written}, Empty: {empty_written}.")

def write_smiles_issue_csv(pipeline, output_path):
    try:
        df = _read_pipeline_df(pipeline)
    except Exception as e:
        print(f"Could not read dataframe directly: {e}")
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
            pubchem = str(m.get("smiles_pubchem", "")).strip()
            opsin = str(m.get("smiles_opsin", "")).strip()
            cactus = str(m.get("smiles_cactus", "")).strip()
            final = str(m.get("smiles_final", "")).strip()
            if smiles_valid != "invalid":
                continue
            rows.append({
                "file_path": file_path,
                "doi": extracted_doi if extracted_doi else m.get("doi"),
                "abbreviation": ";".join(m.get("abbreviation", [])),
                "full_name": ";".join(m.get("full_name", [])),
                "smiles_pubchem": pubchem,
                "smiles_opsin": opsin,
                "smiles_cactus": cactus,
                "smiles_final": final,
                "smiles_valid": smiles_valid,
            })
    if not rows:
        return 0
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)

def merge_global_library(scan_dir, output_path):
    """
    扫描 scan_dir 下所有的 monomers.csv，合并去重后写入 output_path。
    格式与 monomers.csv 完全一致。
    去重策略：
    - Key: SMILES (if valid) > Full Name > Abbreviation
    - 合并字段：
      - doi: 集合合并
      - abbreviation/full_name/cas_no: 集合合并
      - smiles_valid: 优先保留 valid
      - 其他字段: 非空覆盖
    """
    print(f"Merging global library from {scan_dir} ...")
    csv_files = glob.glob(os.path.join(scan_dir, "**", "monomers.csv"), recursive=True)
    if not csv_files:
        print("No monomers.csv files found to merge.")
        return

    unique_map = {}
    total_read = 0
    
    # 辅助函数：标准化列表字符串 "a;b" -> ["a", "b"]
    def _split_clean(s):
        return [x.strip() for x in str(s).split(";") if x.strip()]

    # 辅助函数：选择 Key
    def _get_key(row):
        # 优先使用 smiles_final (如果 valid 且非空)
        s = str(row.get("smiles_final", "")).strip()
        v = str(row.get("smiles_valid", "")).strip()
        if s and v == "valid":
            return s
        # 其次使用 full_name
        fns = _split_clean(row.get("full_name", ""))
        if fns:
            return fns[0]
        # 最后使用 abbreviation
        abs = _split_clean(row.get("abbreviation", ""))
        if abs:
            return abs[0]
        # 如果都没有，回退到 invalid smiles
        if s:
            return s
        return ""

    for fpath in csv_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    total_read += 1
                    key = _get_key(row)
                    if not key:
                        continue
                    if key not in unique_map:
                        unique_map[key] = row
                    else:
                        # Merge logic
                        existing = unique_map[key]
                        
                        # Merge Lists (Set union)
                        for col in ["doi", "abbreviation", "full_name", "cas_no"]:
                            old_list = set(_split_clean(existing.get(col, "")))
                            new_list = set(_split_clean(row.get(col, "")))
                            merged_list = sorted(list(old_list | new_list))
                            existing[col] = ";".join(merged_list)
                        
                        # Merge Single Fields (Priority to valid/non-empty)
                        # 如果 existing 是 invalid 而 incoming 是 valid，则覆盖
                        e_valid = str(existing.get("smiles_valid", "")).strip()
                        n_valid = str(row.get("smiles_valid", "")).strip()
                        
                        should_overwrite = False
                        if e_valid != "valid" and n_valid == "valid":
                            should_overwrite = True
                        elif e_valid == "valid" and n_valid == "valid":
                            # 都是 valid，不做覆盖，维持 existing (人工校正保护原则)
                            should_overwrite = False
                        elif e_valid != "valid" and n_valid != "valid":
                            # 都是 invalid，取非空更长的? 暂不处理，维持 existing
                            should_overwrite = False
                            
                        if should_overwrite:
                            # 覆盖主要的单值字段
                            for col in ["smiles", "smiles_can", "iupac_name", 
                                        "smiles_pubchem", "smiles_pubchem_can", 
                                        "smiles_opsin", "smiles_opsin_can",
                                        "smiles_cactus", "smiles_cactus_can",
                                        "smiles_api_can", "smiles_final", "smiles_valid"]:
                                v = row.get(col, "")
                                if v:
                                    existing[col] = v
                        else:
                            # 如果不完全覆盖，也要补充缺失的字段
                            for col in ["smiles", "smiles_can", "iupac_name", 
                                        "smiles_pubchem", "smiles_pubchem_can", 
                                        "smiles_opsin", "smiles_opsin_can",
                                        "smiles_cactus", "smiles_cactus_can",
                                        "smiles_api_can", "smiles_final"]: # valid 不自动补，由上述逻辑控制
                                if not existing.get(col) and row.get(col):
                                    existing[col] = row.get(col)

        except Exception as e:
            print(f"Error reading {fpath}: {e}")

    # Write out
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    # 确保 CSV_COLUMNS 里的所有列都存在
    final_rows = []
    for row in unique_map.values():
        out_row = {}
        for col in CSV_COLUMNS:
            out_row[col] = row.get(col, "")
        final_rows.append(out_row)
    
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(final_rows)
    
    print(f"Global library merged: {len(final_rows)} unique entries from {total_read} source rows. Saved to {output_path}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default="/share/lcc/paper")
    parser.add_argument("--input-jsonl", default="/share/lcc/dataflow-dp/data/monomer_input_full.jsonl")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--smiles-issue-csv", default="/share/lcc/dataflow-dp/data/monomer_smiles_issues.csv")
    parser.add_argument("--output-dir", default=None, help="Output directory for results. If not set, results are written to the input directories.")
    parser.add_argument("--library-output-path", default=None, help="Output path for monomer library CSV. If not set, uses default path.")
    args = parser.parse_args()

    base_dir = args.base_dir
    input_jsonl = args.input_jsonl
    # 从环境变量读取执行参数（不再暴露命令行开关）
    csv_workers = _env_int("MONOMER_CSV_WORKERS", min(4, os.cpu_count() or 1))
    progress_every = _env_int("MONOMER_PROGRESS_EVERY", 500)
    api_workers = _env_int("MONOMER_API_WORKERS", 4)
    api_timeout = _env_int("MONOMER_API_TIMEOUT", 10)
    api_sleep_every = _env_int("MONOMER_API_SLEEP_EVERY", 1000)
    api_sleep_seconds = _env_float("MONOMER_API_SLEEP_SECONDS", 0.2)
    api_row_workers = _env_int("MONOMER_API_ROW_WORKERS", 4)
    llm_max_workers = _env_int("MONOMER_LLM_MAX_WORKERS", 100)
    llm_max_tokens = _env_int("MONOMER_LLM_MAX_TOKENS", 12800)
    
    print(f"Scanning JSON under: {base_dir}")
    files = find_json_files(base_dir)
    if args.limit and args.limit > 0:
        files = files[:args.limit]
    if not files:
        print("No JSON files found.")
        return

    count = prepare_input_data(files, input_jsonl)
    if count == 0:
        print("No valid data found.")
        return

    pipeline = run_pipeline(
        input_jsonl,
        api_workers=api_workers,
        api_timeout=api_timeout,
        api_sleep_every=api_sleep_every,
        api_sleep_seconds=api_sleep_seconds,
        api_row_workers=api_row_workers,
        llm_max_workers=llm_max_workers,
        llm_max_tokens=llm_max_tokens,
        library_output_path=args.library_output_path,
    )
    save_results_to_csv(pipeline, output_root=args.output_dir, csv_workers=csv_workers, progress_every=progress_every)
    issue_count = write_smiles_issue_csv(pipeline, args.smiles_issue_csv)
    if issue_count:
        print(f"Wrote {issue_count} problem rows to {args.smiles_issue_csv}")

    if args.library_output_path:
        merge_global_library(args.output_dir or base_dir, args.library_output_path)

if __name__ == "__main__":
    main()
