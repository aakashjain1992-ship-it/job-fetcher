"""
Google Sheets writer — writes raw and enriched jobs to Google Sheets.
Uses a cached gspread client to avoid re-authenticating on every call.
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TAB_RAW       = "Raw Jobs"
TAB_ENRICHED  = "Enriched Jobs"
TAB_COMPANIES = "Target Companies"

HEADER_ROW = 1
DATA_START = 2

RAW_HEADERS = [
    "Job ID", "Job Title", "Company", "Location", "Region",
    "Seniority Level", "Posted Date", "Fetched Date", "Expiry Date",
    "Source Platform", "Job URL", "Direct URL",
    "Full Job Description",
    "Salary Mentioned?", "Salary Text", "Remote / Hybrid / Onsite",
    "Status", "Sent to Enrichment?"
]

ENRICHED_HEADERS = [
    "Job ID", "Job Title", "Company", "Region",
    "Role Match (0-10)", "Visa Sponsor Detected", "Remote Type",
    "Salary Range", "INR Equivalent (L)", "Resume Match %",
    "Key Matching Skills", "Red Flags",
    "Gap to Close Before Applying",
    "Composite Score", "Apply Priority",
    "Job URL", "Direct URL",
    "Glassdoor Rating", "Company Size",
    "Apply Status", "Applied Date", "Notes"
]

# Column index for "Sent to Enrichment?" in RAW_HEADERS (0-indexed)
_ENRICHMENT_COL_IDX = RAW_HEADERS.index("Sent to Enrichment?")
_ENRICHMENT_COL_LETTER = chr(65 + _ENRICHMENT_COL_IDX)  # 'R'

# Cached client to avoid re-authenticating on every call
_cached_client = None


def _get_client():
    global _cached_client
    if _cached_client is not None:
        return _cached_client

    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON not set. "
            "Set it to the path of your service account JSON file, "
            "or paste the JSON contents directly."
        )
    val = creds_json.strip()
    if val.endswith(".json") and not val.startswith("{"):
        with open(val) as f:
            creds_dict = json.load(f)
    else:
        creds_dict = json.loads(val)

    _cached_client = gspread.authorize(
        Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    )
    return _cached_client


def _get_sheet(sid):
    return _get_client().open_by_key(sid)


def _rl():
    """Rate limit — Google Sheets API allows ~60 requests/min."""
    time.sleep(0.6)


def _next_row(ws) -> int:
    """Find last row with actual data."""
    try:
        col_a = ws.col_values(1)
        last = max((i + 1 for i, v in enumerate(col_a) if str(v).strip()), default=HEADER_ROW)
        return max(DATA_START, last + 1)
    except Exception:
        return DATA_START


def clear_all_data(spreadsheet_id: str):
    """Clear data from Raw Jobs and Enriched Jobs — keeps headers."""
    sh = _get_sheet(spreadsheet_id)
    for tab in [TAB_RAW, TAB_ENRICHED]:
        try:
            ws = sh.worksheet(tab)
            vals = ws.get_all_values()
            if len(vals) > 1:
                ws.delete_rows(2, len(vals))
                _rl()
                print(f"  Cleared {len(vals) - 1} rows from {tab}")
            else:
                print(f"  {tab} already empty")
        except Exception as e:
            print(f"  Error clearing {tab}: {e}")


def ensure_headers(spreadsheet_id: str):
    """Write headers at row 1. Freeze header row."""
    sh = _get_sheet(spreadsheet_id)
    for tab, headers in [(TAB_RAW, RAW_HEADERS), (TAB_ENRICHED, ENRICHED_HEADERS)]:
        try:
            ws = sh.worksheet(tab)
            existing = ws.row_values(HEADER_ROW)
            if existing != headers:
                ws.update(f"A{HEADER_ROW}", [headers])
                _rl()
                try:
                    ws.freeze(rows=HEADER_ROW)
                except Exception:
                    pass
                print(f"  Headers set in {tab}")
        except gspread.WorksheetNotFound:
            print(f"  Warning: '{tab}' not found — create it in your Google Sheet")


def write_raw_jobs(spreadsheet_id: str, jobs: List[Dict[str, Any]]) -> int:
    if not jobs:
        return 0

    sh = _get_sheet(spreadsheet_id)
    ws = sh.worksheet(TAB_RAW)
    next_row = _next_row(ws)
    now = datetime.utcnow().strftime("%Y-%m-%d")
    rows = []

    for job in jobs:
        fetched = job.get("fetched_date", now)
        try:
            expiry = (datetime.strptime(fetched, "%Y-%m-%d") + timedelta(days=14)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            expiry = ""

        full_desc = job.get("full_description") or job.get("snippet", "")

        rows.append([
            job.get("job_id", ""),
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            job.get("region", ""),
            job.get("seniority", ""),
            job.get("posted_date", ""),
            fetched,
            expiry,
            job.get("source", ""),
            job.get("url", ""),
            job.get("url_direct", ""),
            full_desc,
            "Yes" if job.get("salary_text") else "No",
            job.get("salary_text", ""),
            job.get("remote_type", ""),
            "New",
            "No",
        ])

    if rows:
        ws.update(f"A{next_row}", rows)
        _rl()

    return len(rows)


def _worth_keeping(job: dict, min_score: float = 5.0) -> bool:
    """Keep jobs scoring >= min_score, OR low-score jobs where gap is closeable."""
    score = float(job.get("composite_score", 0) or 0)
    if score >= min_score:
        return True
    gap = str(job.get("gap_to_close", "")).lower()
    skip = ["skip", "not a fit", "not recommended", "not eligible",
            "do not apply", "disqualif", "hard blocker", "not a pm role",
            "not a product manager"]
    return not any(p in gap for p in skip)


def write_enriched_jobs(spreadsheet_id: str, enriched_jobs: List[Dict[str, Any]]) -> int:
    if not enriched_jobs:
        return 0

    before = len(enriched_jobs)
    enriched_jobs = [j for j in enriched_jobs if _worth_keeping(j)]
    excluded = before - len(enriched_jobs)
    if excluded:
        print(f"  Filtered out {excluded} jobs (score < 5, no clear path)")

    if not enriched_jobs:
        return 0

    sh = _get_sheet(spreadsheet_id)
    ws = sh.worksheet(TAB_ENRICHED)
    next_row = _next_row(ws)
    rows = []

    for job in enriched_jobs:
        # Safely join list fields (may be strings if Claude returned unexpected format)
        skills = job.get("key_matching_skills", [])
        if isinstance(skills, list):
            skills = ", ".join(skills)
        red_flags = job.get("red_flags", [])
        if isinstance(red_flags, list):
            red_flags = ", ".join(red_flags)

        rows.append([
            job.get("job_id", ""),
            job.get("title", ""),
            job.get("company", ""),
            job.get("region", ""),
            job.get("role_match_score", ""),
            job.get("visa_sponsor_detected", "Unclear"),
            job.get("remote_type", ""),
            job.get("salary_range", ""),
            job.get("inr_equivalent_lpa", ""),
            job.get("resume_match_pct", ""),
            skills,
            red_flags,
            job.get("gap_to_close", ""),
            job.get("composite_score", ""),
            job.get("apply_priority", ""),
            job.get("url", ""),
            job.get("url_direct", ""),
            job.get("company_rating", ""),
            job.get("company_size", ""),
            "To Apply",
            "",
            job.get("notes", ""),
        ])

    if rows:
        ws.update(f"A{next_row}", rows)
        _rl()

    mark_raw_jobs_enriched(spreadsheet_id, [j["job_id"] for j in enriched_jobs])
    return len(rows)


def mark_raw_jobs_enriched(spreadsheet_id: str, job_ids: List[str]):
    if not job_ids:
        return
    sh = _get_sheet(spreadsheet_id)
    ws = sh.worksheet(TAB_RAW)
    vals = ws.get_all_values()
    updates = [
        {"range": f"{_ENRICHMENT_COL_LETTER}{i+1}", "values": [["Yes"]]}
        for i, row in enumerate(vals)
        if row and row[0] in job_ids
    ]
    if updates:
        ws.batch_update(updates)
        _rl()


def get_target_companies(spreadsheet_id: str) -> List[Dict[str, Any]]:
    sh = _get_sheet(spreadsheet_id)
    ws = sh.worksheet(TAB_COMPANIES)
    rows = ws.get_all_records(head=HEADER_ROW)
    return [
        {
            "name": r.get("Company", ""),
            "tier": r.get("Tier", ""),
            "region": r.get("Region", ""),
            "career_url": r.get("Career Page URL", ""),
            "domain_fit": r.get("Domain Fit (0-10)", 0),
        }
        for r in rows if r.get("Company")
    ]


def update_dashboard_counts(spreadsheet_id: str):
    sh = _get_sheet(spreadsheet_id)
    raw_ws = sh.worksheet(TAB_RAW)
    raw_count = len([r for r in raw_ws.get_all_values()[DATA_START-1:] if r and r[0]])
    enr_ws = sh.worksheet(TAB_ENRICHED)
    high = len([
        r for r in enr_ws.get_all_values()[DATA_START-1:]
        if r and len(r) >= 15 and r[14] == "High"
    ])
    _rl()
    print(f"  Sheet: {raw_count} raw jobs, {high} high priority")
