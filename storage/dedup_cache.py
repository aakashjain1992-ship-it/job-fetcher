"""
Deduplication cache using SQLite.
Tracks job URLs + hashes seen in the last 30 days.
Prevents the same job appearing multiple times across runs.
"""

import sqlite3
import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "dedup_cache.db"


def _get_conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_jobs (
            job_hash TEXT PRIMARY KEY,
            url TEXT,
            title TEXT,
            company TEXT,
            first_seen TEXT,
            last_seen TEXT,
            expired INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            run_id TEXT PRIMARY KEY,
            started_at TEXT,
            finished_at TEXT,
            jobs_fetched INTEGER DEFAULT 0,
            jobs_new INTEGER DEFAULT 0,
            jobs_enriched INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running'
        )
    """)
    conn.commit()
    return conn


def make_hash(url: str, title: str, company: str) -> str:
    key = f"{url.lower().strip()}|{title.lower().strip()}|{company.lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def make_title_hash(title: str, company: str) -> str:
    """Secondary dedup - same role posted in multiple cities counts as one job."""
    key = f"{title.lower().strip()}|{company.lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def is_duplicate(url: str, title: str, company: str) -> bool:
    h = make_hash(url, title, company)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT first_seen, expired FROM seen_jobs WHERE job_hash = ?", (h,)
        ).fetchone()

    if not row:
        return False

    first_seen_str, expired = row
    try:
        first_seen = datetime.fromisoformat(first_seen_str)
    except (ValueError, TypeError):
        return False
    # Re-fetch if older than 30 days (job might have been updated)
    if datetime.utcnow() - first_seen > timedelta(days=30):
        return False
    return True


def mark_seen(url: str, title: str, company: str):
    h = make_hash(url, title, company)
    now = datetime.utcnow().isoformat()
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO seen_jobs (job_hash, url, title, company, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_hash) DO UPDATE SET last_seen = excluded.last_seen
        """, (h, url, title, company, now, now))


def mark_expired(url: str, title: str, company: str):
    h = make_hash(url, title, company)
    with _get_conn() as conn:
        conn.execute("UPDATE seen_jobs SET expired = 1 WHERE job_hash = ?", (h,))


def start_run(run_id: str):
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO run_log (run_id, started_at) VALUES (?, ?)",
            (run_id, datetime.utcnow().isoformat())
        )


def finish_run(run_id: str, fetched: int, new: int, enriched: int, status: str = "success"):
    with _get_conn() as conn:
        conn.execute("""
            UPDATE run_log
            SET finished_at = ?, jobs_fetched = ?, jobs_new = ?, jobs_enriched = ?, status = ?
            WHERE run_id = ?
        """, (datetime.utcnow().isoformat(), fetched, new, enriched, status, run_id))


def get_last_fetch_date() -> datetime | None:
    """Return the datetime of the last successful pipeline run (for smart fetch window)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT finished_at FROM run_log WHERE status = 'success' ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except (ValueError, TypeError):
        return None


def get_stats() -> dict:
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]
        last_24h = conn.execute(
            "SELECT COUNT(*) FROM seen_jobs WHERE first_seen > ?",
            ((datetime.utcnow() - timedelta(hours=24)).isoformat(),)
        ).fetchone()[0]
        last_run = conn.execute(
            "SELECT * FROM run_log ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return {"total_seen": total, "new_last_24h": last_24h, "last_run": last_run}
