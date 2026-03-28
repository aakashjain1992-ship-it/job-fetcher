"""
Supabase storage backend.
Upserts raw and enriched jobs into the Supabase `jobs` table.
Requires: SUPABASE_URL and SUPABASE_SERVICE_KEY env vars.
"""

import os
import json
from typing import List, Dict, Any

try:
    from supabase import create_client, Client
except ImportError:
    raise ImportError("Run: pip install supabase")


def _get_client() -> "Client":
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
    return create_client(url, key)


def _safe_json(val):
    """Ensure list/dict values are JSON-serialisable."""
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def write_raw_jobs(jobs: List[Dict[str, Any]]) -> int:
    """Upsert raw (pre-enrichment) jobs to Supabase."""
    if not jobs:
        return 0
    client = _get_client()

    rows = []
    for job in jobs:
        rows.append({
            "job_id":           job.get("job_id", ""),
            "title":            job.get("title", ""),
            "company":          job.get("company", ""),
            "location":         job.get("location", ""),
            "region":           job.get("region", ""),
            "seniority":        job.get("seniority", ""),
            "posted_date":      job.get("posted_date") or None,
            "fetched_date":     job.get("fetched_date") or None,
            "expiry_date":      job.get("expiry_date") or None,
            "source":           job.get("source", ""),
            "url":              job.get("url", ""),
            "url_direct":       job.get("url_direct", ""),
            "snippet":          job.get("snippet", ""),
            "full_description": job.get("full_description", ""),
            "salary_text":      job.get("salary_text", ""),
            "remote_type":      job.get("remote_type", ""),
            "apply_status":     "New",
        })

    result = client.table("jobs").upsert(rows, on_conflict="job_id").execute()
    return len(result.data) if result.data else 0


def write_enriched_jobs(jobs: List[Dict[str, Any]]) -> int:
    """Upsert enriched jobs (with Claude scores) to Supabase."""
    if not jobs:
        return 0
    client = _get_client()

    rows = []
    for job in jobs:
        rows.append({
            "job_id":               job.get("job_id", ""),
            "title":                job.get("title", ""),
            "company":              job.get("company", ""),
            "location":             job.get("location", ""),
            "region":               job.get("region", ""),
            "seniority":            job.get("seniority", ""),
            "posted_date":          job.get("posted_date") or None,
            "fetched_date":         job.get("fetched_date") or None,
            "expiry_date":          job.get("expiry_date") or None,
            "source":               job.get("source", ""),
            "url":                  job.get("url", ""),
            "url_direct":           job.get("url_direct", ""),
            "snippet":              job.get("snippet", ""),
            "full_description":     job.get("full_description", ""),
            "salary_text":          job.get("salary_text", ""),
            "remote_type":          job.get("remote_type", ""),
            # Enrichment
            "role_match_score":     job.get("role_match_score"),
            "visa_sponsor_detected": job.get("visa_sponsor_detected", "Unclear"),
            "resume_match_pct":     job.get("resume_match_pct"),
            "key_matching_skills":  _safe_json(job.get("key_matching_skills", [])),
            "red_flags":            _safe_json(job.get("red_flags", [])),
            "gap_to_close":         job.get("gap_to_close", ""),
            "composite_score":      job.get("composite_score"),
            "apply_priority":       job.get("apply_priority", "Low"),
            "apply_after":          job.get("apply_after", ""),
            "enrichment_notes":     job.get("notes", ""),
            "inr_equivalent_lpa":   job.get("inr_equivalent_lpa"),
            "salary_range":         job.get("salary_range", ""),
        })

    result = client.table("jobs").upsert(rows, on_conflict="job_id").execute()
    return len(result.data) if result.data else 0


def record_fetch_run(run_id: str, fetched: int, new: int, enriched: int, status: str):
    """Log a pipeline run to Supabase fetch_runs table."""
    from datetime import datetime, timezone
    client = _get_client()
    client.table("fetch_runs").upsert({
        "run_id":        run_id,
        "finished_at":   datetime.now(timezone.utc).isoformat(),
        "jobs_fetched":  fetched,
        "jobs_new":      new,
        "jobs_enriched": enriched,
        "status":        status,
    }, on_conflict="run_id").execute()


def get_last_fetch_date():
    """Return datetime of last successful run (for smart fetch window)."""
    from datetime import datetime
    client = _get_client()
    result = client.table("fetch_runs") \
        .select("finished_at") \
        .eq("status", "success") \
        .order("finished_at", desc=True) \
        .limit(1) \
        .execute()
    if result.data:
        try:
            return datetime.fromisoformat(result.data[0]["finished_at"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            return None
    return None


def ensure_headers():
    """No-op — Supabase table schema is managed via SQL migration."""
    pass


def update_dashboard_counts():
    """No-op — Supabase dashboard reads live data."""
    pass
