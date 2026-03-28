"""
Main orchestrator — runs the full job fetcher pipeline.

Pipeline:
  1. Fetch raw jobs (JobSpy + Career pages)
  2. Deduplicate
  3. Write raw jobs to storage (Google Sheets / JSON / CSV / both)
  4. Enrich with Claude (visa, role match, resume fit, priority)
  5. Write enriched jobs to storage
  6. Send weekly digest (Mondays only)
  7. Enrich Glassdoor ratings (optional)
"""

import os
import sys
import uuid
import argparse
import json
from datetime import datetime
from typing import Dict, Any

# Load .env file when running locally (no-op in GitHub Actions)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from config.profile import (
    CANDIDATE_PROFILE, STORAGE_MODE, JSON_OUTPUT_DIR,
    SEARCHES, CAREER_PAGES,
)
from config.validate import validate_config
from fetchers.jobspy_fetcher import fetch_all as fetch_jobspy
from fetchers.career_pages.scraper import fetch_all as fetch_career_pages
from enricher.claude_enricher import enrich_all
from storage.dedup_cache import (
    is_duplicate, mark_seen, make_title_hash,
    start_run, finish_run, get_stats,
)
from notifier.weekly_digest import send_digest, should_send_digest

SPREADSHEET_ID = os.environ.get("GOOGLE_SPREADSHEET_ID", "")


def _get_storage_writers(storage_mode: str = None):
    """Return write functions based on storage config."""
    mode = storage_mode or STORAGE_MODE
    writers = []

    if mode in ("google_sheets", "both"):
        if not SPREADSHEET_ID:
            print("  Warning: GOOGLE_SPREADSHEET_ID not set — skipping Google Sheets")
        else:
            from storage.sheets_writer import (
                write_raw_jobs, write_enriched_jobs,
                ensure_headers, update_dashboard_counts,
            )
            writers.append({
                "name": "Google Sheets",
                "write_raw": lambda jobs: write_raw_jobs(SPREADSHEET_ID, jobs),
                "write_enriched": lambda jobs: write_enriched_jobs(SPREADSHEET_ID, jobs),
                "ensure_headers": lambda: ensure_headers(SPREADSHEET_ID),
                "update_dashboard": lambda: update_dashboard_counts(SPREADSHEET_ID),
            })

    if mode in ("json", "both"):
        from storage.json_writer import (
            write_raw_jobs as json_write_raw,
            write_enriched_jobs as json_write_enriched,
            ensure_headers as json_ensure_headers,
            update_dashboard_counts as json_update_dashboard,
        )
        writers.append({
            "name": "JSON",
            "write_raw": lambda jobs: json_write_raw(JSON_OUTPUT_DIR, jobs),
            "write_enriched": lambda jobs: json_write_enriched(JSON_OUTPUT_DIR, jobs),
            "ensure_headers": lambda: json_ensure_headers(JSON_OUTPUT_DIR),
            "update_dashboard": lambda: json_update_dashboard(JSON_OUTPUT_DIR),
        })

    if mode == "csv":
        from storage.csv_writer import (
            write_raw_jobs as csv_write_raw,
            write_enriched_jobs as csv_write_enriched,
            ensure_headers as csv_ensure_headers,
            update_dashboard_counts as csv_update_dashboard,
        )
        writers.append({
            "name": "CSV",
            "write_raw": lambda jobs: csv_write_raw(JSON_OUTPUT_DIR, jobs),
            "write_enriched": lambda jobs: csv_write_enriched(JSON_OUTPUT_DIR, jobs),
            "ensure_headers": lambda: csv_ensure_headers(JSON_OUTPUT_DIR),
            "update_dashboard": lambda: csv_update_dashboard(JSON_OUTPUT_DIR),
        })

    if not writers:
        # Fallback to JSON if nothing is configured
        from storage.json_writer import (
            write_raw_jobs as json_write_raw,
            write_enriched_jobs as json_write_enriched,
            ensure_headers as json_ensure_headers,
            update_dashboard_counts as json_update_dashboard,
        )
        print("  No storage configured — falling back to JSON output")
        writers.append({
            "name": "JSON (fallback)",
            "write_raw": lambda jobs: json_write_raw(JSON_OUTPUT_DIR, jobs),
            "write_enriched": lambda jobs: json_write_enriched(JSON_OUTPUT_DIR, jobs),
            "ensure_headers": lambda: json_ensure_headers(JSON_OUTPUT_DIR),
            "update_dashboard": lambda: json_update_dashboard(JSON_OUTPUT_DIR),
        })

    return writers


def list_searches():
    """Print all configured searches and career pages."""
    print(f"\nCandidate: {CANDIDATE_PROFILE['name']}")
    print(f"Role: {CANDIDATE_PROFILE['current_title']} ({CANDIDATE_PROFILE['total_experience_years']} yrs exp)")
    print(f"Storage: {STORAGE_MODE}")
    print()

    print(f"Job Board Searches ({len(SEARCHES)} configured):")
    print(f"{'─'*70}")
    for i, s in enumerate(SEARCHES, 1):
        remote_tag = " [REMOTE]" if s.get("remote") else ""
        print(f"  {i:2d}. {s['region']:12s} | {s['location']:30s} | {s['query'][:40]}{remote_tag}")

    print()
    print(f"Career Pages ({len(CAREER_PAGES)} configured):")
    print(f"{'─'*70}")
    for i, p in enumerate(CAREER_PAGES, 1):
        ptype = p.get("type", "html").upper()
        region = p.get("default_region", "?")
        name = p.get("company", "Unknown")
        print(f"  {i:2d}. {name:20s} | {region:10s} | {ptype}")

    print()


def run_pipeline(
    skip_career_pages: bool = False,
    max_jobs: int = 200,
    skip_ratings: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:

    run_id = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    if not dry_run:
        start_run(run_id)

    print(f"\n{'='*60}")
    print(f"Job Fetcher Pipeline — {run_id}")
    if dry_run:
        print("  MODE: DRY RUN (fetch + dedup only, no API calls or writes)")
    print(f"{'='*60}\n")

    # ── Step 1: Fetch ────────────────────────────────────────────────
    all_raw = []

    print("\nFetching from JobSpy (LinkedIn + Indeed + Google)...")
    try:
        spy_jobs = fetch_jobspy()
        all_raw.extend(spy_jobs)
        print(f"  JobSpy: {len(spy_jobs)} jobs")
    except Exception as e:
        print(f"  JobSpy ERROR: {e}")

    if not skip_career_pages:
        print("\nScraping company career pages...")
        try:
            career_jobs = fetch_career_pages()
            all_raw.extend(career_jobs)
            print(f"  Career pages: {len(career_jobs)} jobs")
        except Exception as e:
            print(f"  Career pages ERROR: {e}")

    total_fetched = len(all_raw)
    print(f"\nTotal fetched (before dedup): {total_fetched}")

    # ── Step 2: Deduplicate ──────────────────────────────────────────
    new_jobs = []
    seen_title_hashes = set()

    for job in all_raw:
        url   = job.get("url", "")
        title = job.get("title", "")
        co    = job.get("company", "")

        if not title or not co:
            continue

        th = make_title_hash(title, co)
        if th in seen_title_hashes:
            continue
        seen_title_hashes.add(th)

        if is_duplicate(url, title, co):
            continue

        new_jobs.append(job)
        if not dry_run:
            mark_seen(url, title, co)

    if len(new_jobs) > max_jobs:
        print(f"  Capping at {max_jobs} jobs (from {len(new_jobs)})")
        new_jobs = new_jobs[:max_jobs]

    print(f"  New after dedup: {len(new_jobs)} jobs")

    if not new_jobs:
        print("  No new jobs to process. Pipeline complete.")
        if not dry_run:
            finish_run(run_id, total_fetched, 0, 0, "success_no_new")
        return {"run_id": run_id, "fetched": total_fetched, "new": 0, "enriched": 0}

    # ── Dry run stops here ───────────────────────────────────────────
    if dry_run:
        print(f"\n{'='*60}")
        print("DRY RUN SUMMARY")
        print(f"  Would process: {len(new_jobs)} new jobs")
        print(f"  Storage:       {STORAGE_MODE}")
        print(f"\nSample jobs:")
        for j in new_jobs[:5]:
            print(f"  - {j['title']} @ {j['company']} ({j['region']})")
        if len(new_jobs) > 5:
            print(f"  ... and {len(new_jobs) - 5} more")
        print(f"{'='*60}\n")
        return {"run_id": run_id, "fetched": total_fetched, "new": len(new_jobs), "enriched": 0, "dry_run": True}

    # ── Step 3: Write raw jobs ───────────────────────────────────────
    writers = _get_storage_writers()
    for w in writers:
        print(f"\nWriting {len(new_jobs)} raw jobs to {w['name']}...")
        try:
            w["ensure_headers"]()
            written = w["write_raw"](new_jobs)
            print(f"  Wrote {written} rows")
        except Exception as e:
            print(f"  {w['name']} write ERROR: {e}")

    # ── Step 4: Enrich with Claude ───────────────────────────────────
    print(f"\nEnriching {len(new_jobs)} jobs with Claude...")
    enriched_jobs = []
    try:
        enriched_jobs = enrich_all(new_jobs)
        print(f"  Enriched: {len(enriched_jobs)} jobs")
    except Exception as e:
        print(f"  Enrichment ERROR: {e}")

    # ── Step 5: Write enriched jobs ──────────────────────────────────
    if enriched_jobs:
        for w in writers:
            print(f"\nWriting enriched jobs to {w['name']}...")
            try:
                written_enriched = w["write_enriched"](enriched_jobs)
                w["update_dashboard"]()
                print(f"  Wrote {written_enriched} enriched rows")
            except Exception as e:
                print(f"  {w['name']} enriched write ERROR: {e}")

    # ── Step 6: Weekly digest (Mondays only) ─────────────────────────
    if should_send_digest() and enriched_jobs:
        print("\nMonday digest — sending email...")
        stats = get_stats()
        run_stats = {
            "total_fetched": stats.get("total_seen", 0),
            "new_this_run": len(new_jobs),
        }
        try:
            send_digest(enriched_jobs, run_stats)
        except Exception as e:
            print(f"  Digest ERROR: {e}")

    # ── Step 7: Glassdoor ratings (optional) ─────────────────────────
    if not skip_ratings and SPREADSHEET_ID:
        print("\nRunning Glassdoor ratings enricher...")
        try:
            import enrich_ratings
            enrich_ratings.run(SPREADSHEET_ID)
        except Exception as e:
            print(f"  Ratings enricher error: {e}")

    # ── Summary ──────────────────────────────────────────────────────
    high = sum(1 for j in enriched_jobs if j.get("apply_priority") == "High")
    medium = sum(1 for j in enriched_jobs if j.get("apply_priority") == "Medium")

    print(f"\n{'='*60}")
    print(f"PIPELINE COMPLETE — {run_id}")
    print(f"  Fetched:  {total_fetched}")
    print(f"  New:      {len(new_jobs)}")
    print(f"  Enriched: {len(enriched_jobs)}")
    print(f"  High priority:   {high}")
    print(f"  Medium priority: {medium}")
    print(f"{'='*60}\n")

    finish_run(run_id, total_fetched, len(new_jobs), len(enriched_jobs), "success")

    return {
        "run_id": run_id,
        "fetched": total_fetched,
        "new": len(new_jobs),
        "enriched": len(enriched_jobs),
        "high_priority": high,
        "medium_priority": medium,
    }


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Job Fetcher — AI-powered job intelligence pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    Full pipeline run
  python main.py --test             Test enrichment with 2 sample jobs
  python main.py --dry-run          Fetch & dedup only, no API calls
  python main.py --list-searches    Show configured searches
  python main.py --skip-career-pages --skip-ratings
        """,
    )
    parser.add_argument("--skip-career-pages", action="store_true",
                        help="Skip career page scraping")
    parser.add_argument("--skip-ratings", action="store_true",
                        help="Skip Glassdoor ratings enrichment")
    parser.add_argument("--max-jobs", type=int, default=200,
                        help="Max jobs to process per run (default: 200)")
    parser.add_argument("--test", action="store_true",
                        help="Test mode — 2 sample jobs, tests enrichment + storage")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and dedup only — no Claude API calls, no storage writes")
    parser.add_argument("--list-searches", action="store_true",
                        help="Show all configured searches and career pages, then exit")
    return parser


def _cli():
    """Entry point for `job-fetcher` console script (via pyproject.toml)."""
    args = _build_parser().parse_args()

    if args.list_searches:
        list_searches()
        return

    # Validate config before running
    if not validate_config(STORAGE_MODE, dry_run=args.dry_run or args.test):
        sys.exit(1)

    if args.test:
        print("TEST MODE — using sample jobs, skipping all fetchers\n")
        sample = [
            {
                "job_id": "TEST-001", "title": "Senior Product Manager - Platform",
                "company": "Atlassian", "location": "Singapore", "region": "Singapore",
                "seniority": "Senior PM", "posted_date": "2026-03-23",
                "fetched_date": "2026-03-23", "source": "Test",
                "url": "https://atlassian.com/careers",
                "snippet": "Senior PM to own Jira Service Management workflow automation...",
                "salary_text": "S$180K-S$220K", "remote_type": "Hybrid",
                "full_description": "Own product vision for workflow automation in Jira Service Management. Work with enterprise customers globally. 5+ years PM experience required. We sponsor Employment Pass for eligible candidates."
            },
            {
                "job_id": "TEST-002", "title": "Principal Product Manager - Ops",
                "company": "PagerDuty", "location": "Remote, USA", "region": "USA",
                "seniority": "Principal PM", "posted_date": "2026-03-22",
                "fetched_date": "2026-03-23", "source": "Test",
                "url": "https://pagerduty.com/careers",
                "snippet": "Lead product strategy for incident management and AIOps...",
                "salary_text": "$175K-$220K", "remote_type": "Remote",
                "full_description": "PagerDuty is seeking a Principal PM for our incident management platform. You will own the roadmap for AI-powered on-call and workflow automation features. H1B sponsorship available. Remote-first culture."
            },
        ]
        enriched = enrich_all(sample)
        print(json.dumps(enriched, indent=2))
    else:
        result = run_pipeline(
            skip_career_pages=args.skip_career_pages,
            skip_ratings=args.skip_ratings,
            max_jobs=args.max_jobs,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli()
