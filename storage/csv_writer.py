"""
CSV file storage backend — simplest option, no dependencies.
Saves raw and enriched jobs as CSV files in the output directory.
"""

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

RAW_COLUMNS = [
    "job_id", "title", "company", "location", "region",
    "seniority", "posted_date", "fetched_date", "source",
    "url", "url_direct", "salary_text", "remote_type",
    "full_description",
]

ENRICHED_COLUMNS = [
    "job_id", "title", "company", "region",
    "role_match_score", "visa_sponsor_detected", "remote_type",
    "salary_range", "inr_equivalent_lpa", "resume_match_pct",
    "key_matching_skills", "red_flags", "gap_to_close",
    "composite_score", "apply_priority",
    "url", "url_direct", "notes",
]


def _ensure_dir(output_dir: str) -> Path:
    p = Path(output_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_existing_ids(filepath: Path) -> set:
    """Read existing job IDs from a CSV file."""
    ids = set()
    if filepath.exists():
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                jid = row.get("job_id", "")
                if jid:
                    ids.add(jid)
    return ids


def _append_rows(filepath: Path, columns: List[str], rows: List[Dict]) -> int:
    """Append rows to CSV, creating the file with headers if it doesn't exist."""
    write_header = not filepath.exists()
    with open(filepath, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            # Flatten list fields to comma-separated strings
            flat = {}
            for col in columns:
                val = row.get(col, "")
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val)
                flat[col] = val
            writer.writerow(flat)
    return len(rows)


def write_raw_jobs(output_dir: str, jobs: List[Dict[str, Any]]) -> int:
    if not jobs:
        return 0
    d = _ensure_dir(output_dir)
    filepath = d / "raw_jobs.csv"
    existing_ids = _load_existing_ids(filepath)
    new_jobs = [j for j in jobs if j.get("job_id") not in existing_ids]
    return _append_rows(filepath, RAW_COLUMNS, new_jobs)


def write_enriched_jobs(output_dir: str, enriched_jobs: List[Dict[str, Any]]) -> int:
    if not enriched_jobs:
        return 0
    d = _ensure_dir(output_dir)
    filepath = d / "enriched_jobs.csv"
    existing_ids = _load_existing_ids(filepath)
    new_jobs = [j for j in enriched_jobs if j.get("job_id") not in existing_ids]
    # Sort by composite score descending
    new_jobs.sort(key=lambda x: float(x.get("composite_score", 0) or 0), reverse=True)
    return _append_rows(filepath, ENRICHED_COLUMNS, new_jobs)


def ensure_headers(output_dir: str):
    """No-op for CSV — headers are written on first append."""
    _ensure_dir(output_dir)


def update_dashboard_counts(output_dir: str):
    """Print summary stats from CSV files."""
    d = Path(output_dir)
    raw_count = len(_load_existing_ids(d / "raw_jobs.csv"))
    enriched_path = d / "enriched_jobs.csv"
    high = 0
    if enriched_path.exists():
        with open(enriched_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("apply_priority") == "High":
                    high += 1
    print(f"  CSV: {raw_count} raw jobs, {high} high priority")
