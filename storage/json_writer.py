"""
JSON file storage backend — alternative to Google Sheets.
Saves raw and enriched jobs as JSON files in the output directory.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any


def _ensure_dir(output_dir: str) -> Path:
    p = Path(output_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_existing(filepath: Path) -> List[Dict]:
    if filepath.exists():
        with open(filepath) as f:
            return json.load(f)
    return []


def write_raw_jobs(output_dir: str, jobs: List[Dict[str, Any]]) -> int:
    if not jobs:
        return 0
    d = _ensure_dir(output_dir)
    filepath = d / "raw_jobs.json"
    existing = _load_existing(filepath)
    existing_ids = {j.get("job_id") for j in existing}
    new_jobs = [j for j in jobs if j.get("job_id") not in existing_ids]
    existing.extend(new_jobs)
    with open(filepath, "w") as f:
        json.dump(existing, f, indent=2, default=str)
    return len(new_jobs)


def write_enriched_jobs(output_dir: str, enriched_jobs: List[Dict[str, Any]]) -> int:
    if not enriched_jobs:
        return 0
    d = _ensure_dir(output_dir)
    filepath = d / "enriched_jobs.json"
    existing = _load_existing(filepath)
    existing_ids = {j.get("job_id") for j in existing}
    new_jobs = [j for j in enriched_jobs if j.get("job_id") not in existing_ids]
    existing.extend(new_jobs)
    # Sort by composite score descending
    existing.sort(key=lambda x: float(x.get("composite_score", 0) or 0), reverse=True)
    with open(filepath, "w") as f:
        json.dump(existing, f, indent=2, default=str)
    return len(new_jobs)


def ensure_headers(output_dir: str):
    """No-op for JSON storage (no headers needed)."""
    _ensure_dir(output_dir)


def update_dashboard_counts(output_dir: str):
    """Print summary stats from JSON files."""
    d = Path(output_dir)
    raw_path = d / "raw_jobs.json"
    enriched_path = d / "enriched_jobs.json"
    raw_count = len(_load_existing(raw_path))
    enriched = _load_existing(enriched_path)
    high = sum(1 for j in enriched if j.get("apply_priority") == "High")
    print(f"  JSON: {raw_count} raw jobs, {high} high priority")
