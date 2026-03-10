import os
import re
import json
import logging
import argparse
from typing import List

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def parse_log_for_jobs(log_path: str) -> List[dict]:
    """Parse the log file to find submitted batch jobs."""
    jobs = []
    
    # Regex to match: Batch job submitted: projects/646220544061/locations/us-central1/batchPredictionJobs/199427340281839616
    job_pattern = re.compile(r"Batch job submitted:\s+(projects/[\d]+/locations/[\w-]+/batchPredictionJobs/(\d+))")
    
    # Regex to capture category context
    # Looking for: "Added job projects/... for thermal. Active jobs: 1"
    # This format is more reliable in your new log structure
    # Example: 2026-03-09 09:59:06 INFO Added job projects/646220544061/locations/us-central1/batchPredictionJobs/199427340281839616 for thermal. Active jobs: 1
    
    added_job_pattern = re.compile(r"Added job (projects/[\d]+/locations/[\w-]+/batchPredictionJobs/(\d+)) for (\w+)\.")

    if not os.path.exists(log_path):
        logger.error(f"Log file not found: {log_path}")
        return []

    with open(log_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Method 1: Find precise category-job mapping using "Added job ..." line
    for match in added_job_pattern.finditer(content):
        job_uri = match.group(1)
        cat = match.group(3)
        
        jobs.append({
            "job_id": job_uri,
            "category": cat,
            "status": "PENDING",
            "offset": 0, # Unknown from log, but acceptable for resume
            "limit": 0,
            "base_name": f"{cat}_input",
            "output_dir": os.getcwd() # Default to cwd
        })

    # Fallback Method 2: Old format "Category {cat} submitted 1 jobs: ['...']"
    if not jobs:
        cat_pattern = re.compile(r"Category\s+(\w+)\s+submitted\s+\d+\s+jobs:\s+\['(.*?)'\]")
        for match in cat_pattern.finditer(content):
            cat = match.group(1)
            job_uri = match.group(2)
            jobs.append({
                "job_id": job_uri,
                "category": cat,
                "status": "PENDING",
                "offset": 0,
                "limit": 0,
                "base_name": f"{cat}_input",
                "output_dir": os.getcwd()
            })
        
    logger.info(f"Found {len(jobs)} jobs in log file.")
    return jobs

def main():
    parser = argparse.ArgumentParser(description="Resume batch jobs from log file")
    parser.add_argument("--log-file", required=True, help="Path to property_full_*.log")
    parser.add_argument("--ledger-file", default=".jobs_ledger.jsonl", help="Path to output ledger file")
    args = parser.parse_args()

    jobs = parse_log_for_jobs(args.log_file)
    
    if not jobs:
        logger.warning("No jobs found to resume.")
        return

    # Load existing ledger to avoid duplicates
    existing_ids = set()
    if os.path.exists(args.ledger_file):
        with open(args.ledger_file, 'r') as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    existing_ids.add(rec.get("job_id"))
                except: pass

    added = 0
    with open(args.ledger_file, 'a', encoding='utf-8') as f:
        for job in jobs:
            if job["job_id"] not in existing_ids:
                # Add metadata required by GlobalJobManager
                # Try to infer output dir from log? Hard. 
                # Let's assume standard path: ../{category}_output or current dir
                # For safety, we set a flag.
                
                f.write(json.dumps(job) + "\n")
                existing_ids.add(job["job_id"])
                added += 1
    
    logger.info(f"Added {added} new jobs to ledger {args.ledger_file}.")
    logger.info("Now you can restart the pipeline script, and it will automatically resume polling these jobs.")

if __name__ == "__main__":
    main()
