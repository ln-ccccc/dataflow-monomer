
import json
import os
import sys
import logging
import argparse

# 添加当前目录到 path 以便导入
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from property_extract_pipeline import _distribute_results_to_csv, IO_DIR

def setup_logger():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    return logging.getLogger("redistribute")

def main():
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Re-distribute results from ledger to CSVs")
    parser.add_argument("--category", help="Filter by category (e.g. thermal)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write CSVs, just check")
    args = parser.parse_args()

    ledger_path = os.path.join(IO_DIR, ".jobs_ledger.jsonl")
    if not os.path.exists(ledger_path):
        logger.error(f"Ledger not found: {ledger_path}")
        return

    logger.info(f"Reading ledger: {ledger_path}")
    
    # Load all jobs (latest status per job_id)
    jobs = {}
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                jid = rec.get("job_id")
                if jid:
                    jobs[jid] = rec
            except:
                pass

    logger.info(f"Found {len(jobs)} unique jobs in ledger.")

    count = 0
    for jid, rec in jobs.items():
        cat = rec.get("category")
        if args.category and cat != args.category:
            continue
            
        base_name = rec.get("base_name")
        output_dir = rec.get("output_dir")
        
        if not base_name or not output_dir:
            continue

        # Input file
        input_jsonl = os.path.join(output_dir, base_name)
        if not os.path.exists(input_jsonl):
            logger.warning(f"Input file missing for {jid}: {input_jsonl}")
            continue

        # Result file
        short_id = jid.split("/")[-1]
        cat_from_dir = os.path.basename(output_dir.rstrip("/"))
        result_dir = os.path.join(output_dir, "results")
        result_file = os.path.join(result_dir, f"{cat_from_dir}_{short_id}.csv")
        fallback_jsonl = os.path.join(result_dir, f"{cat_from_dir}_{short_id}.jsonl")
        
        if not os.path.exists(result_file):
            if os.path.exists(fallback_jsonl):
                result_file = fallback_jsonl
            if rec.get("output_file") and os.path.exists(rec.get("output_file")):
                result_file = rec.get("output_file")
            else:
                logger.warning(f"Result file missing for {jid}: {result_file}")
                continue
        
        logger.info(f"Processing {jid} -> {result_file} (offset={rec.get('offset', 0)})")
        if not args.dry_run:
            _distribute_results_to_csv(input_jsonl, result_file, cat, logger, job_offset=rec.get("offset", 0))
        count += 1

    logger.info(f"Finished processing {count} jobs.")

if __name__ == "__main__":
    main()
