"""
Feedback recorder — saves real-world application outcomes so you can
calibrate whether the AI scoring matches reality.

Outcomes are stored in eval/feedback_log.jsonl (one JSON object per line).
Over time this gives you a ground-truth dataset: "AI said High, I got no reply"
vs "AI said Medium, I got an interview."

Usage (interactive):
  python main.py --eval feedback

Usage (non-interactive / scripted):
  python main.py --eval feedback --job-id JOB-123 --outcome interview --notes "recruiter call booked"

Outcome values: applied, no_reply, rejected, screening, interview, final, offer, accepted
"""

import json
import sys
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_FILE = Path(__file__).parent / "feedback_log.jsonl"

VALID_OUTCOMES = [
    "applied", "no_reply", "rejected",
    "screening", "interview", "final",
    "offer", "accepted", "withdrawn",
]

_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"


def _read_log() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    entries = []
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def _append_entry(entry: dict) -> None:
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _find_job_from_storage(job_id: str) -> dict | None:
    """Try to look up a job from Supabase or local JSON output."""
    # Try Supabase
    try:
        from config.profile import STORAGE_MODE
        if STORAGE_MODE == "supabase":
            from storage.supabase_writer import _get_client
            client = _get_client()
            resp = client.table("jobs").select(
                "job_id, title, company, region, composite_score, apply_priority"
            ).eq("job_id", job_id).maybe_single().execute()
            if resp.data:
                return resp.data
    except Exception:
        pass

    # Try local JSON
    try:
        from config.profile import JSON_OUTPUT_DIR
        import os
        path = os.path.join(JSON_OUTPUT_DIR, "enriched_jobs.json")
        if os.path.exists(path):
            with open(path) as f:
                jobs = json.load(f)
            for job in jobs:
                if job.get("job_id") == job_id:
                    return job
    except Exception:
        pass

    return None


def record(
    job_id: str,
    outcome: str,
    notes: str = "",
    ai_priority: str = None,
    ai_score: float = None,
    title: str = None,
    company: str = None,
) -> dict[str, Any]:
    """
    Save one feedback entry to the log.
    Returns the entry dict.
    """
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"Invalid outcome {outcome!r}. Valid: {VALID_OUTCOMES}")

    entry = {
        "job_id":     job_id,
        "outcome":    outcome,
        "notes":      notes,
        "recorded_at": datetime.utcnow().isoformat(),
    }
    if ai_priority:
        entry["ai_priority"] = ai_priority
    if ai_score is not None:
        entry["ai_score"] = ai_score
    if title:
        entry["title"] = title
    if company:
        entry["company"] = company

    _append_entry(entry)
    return entry


def show_summary(verbose: bool = False) -> None:
    """Print a summary of all recorded feedback."""
    entries = _read_log()
    if not entries:
        print(f"  {_YELLOW}No feedback recorded yet.{_RESET}")
        print(f"  Run: python main.py --eval feedback --job-id JOB-123 --outcome applied\n")
        return

    total = len(entries)
    by_outcome: dict[str, int] = {}
    conversions: dict[str, list[str]] = {}  # ai_priority → outcomes
    score_buckets: dict[str, list[str]] = {}

    for e in entries:
        out = e.get("outcome", "?")
        by_outcome[out] = by_outcome.get(out, 0) + 1

        pri = e.get("ai_priority")
        if pri:
            conversions.setdefault(pri, []).append(out)

        score = e.get("ai_score")
        if score is not None:
            bucket = "8+" if score >= 8 else ("6-8" if score >= 6 else "<6")
            score_buckets.setdefault(bucket, []).append(out)

    positive = {"interview", "final", "offer", "accepted", "screening"}
    negative = {"no_reply", "rejected", "withdrawn"}

    print(f"\n{_BOLD}{'='*60}{_RESET}")
    print(f"{_BOLD}Eval: Feedback Summary — {total} applications tracked{_RESET}")
    print(f"{'='*60}\n")

    print("  Outcomes:")
    for out in VALID_OUTCOMES:
        count = by_outcome.get(out, 0)
        if count:
            bar = "█" * count
            print(f"    {out:12s}  {bar} {count}")

    if conversions:
        print("\n  AI Priority → Actual Outcome:")
        for pri in ["High", "Medium", "Low"]:
            outs = conversions.get(pri, [])
            if not outs:
                continue
            pos = sum(1 for o in outs if o in positive)
            neg = sum(1 for o in outs if o in negative)
            hit_rate = round(pos / len(outs) * 100) if outs else 0
            colour = _GREEN if hit_rate >= 50 else _YELLOW
            print(f"    {pri:8s} ({len(outs)} jobs)  →  {colour}{pos} positive / {neg} negative  ({hit_rate}% hit rate){_RESET}")

    if score_buckets:
        print("\n  AI Score Bucket → Positive outcome rate:")
        for bucket in ["8+", "6-8", "<6"]:
            outs = score_buckets.get(bucket, [])
            if not outs:
                continue
            pos = sum(1 for o in outs if o in positive)
            hit_rate = round(pos / len(outs) * 100) if outs else 0
            colour = _GREEN if hit_rate >= 50 else _YELLOW
            print(f"    score {bucket}  ({len(outs)} jobs)  →  {colour}{hit_rate}% positive{_RESET}")

    if verbose:
        print(f"\n  Recent entries:")
        for e in sorted(entries, key=lambda x: x.get("recorded_at", ""), reverse=True)[:10]:
            line = (
                f"    {e.get('job_id', '?'):20s}  "
                f"{e.get('outcome', '?'):12s}  "
                f"score={e.get('ai_score', '?')}  "
                f"priority={e.get('ai_priority', '?')}"
            )
            if e.get("title"):
                line += f"  [{e['title'][:30]}]"
            print(line)

    print(f"\n{'='*60}\n")


def run_interactive(job_id: str = None, outcome: str = None, notes: str = "") -> dict[str, Any] | None:
    """
    Main entry point from main.py --eval feedback.
    If job_id + outcome provided, records directly.
    Otherwise shows summary + prompts interactively.
    """
    if job_id and outcome:
        # Try to look up existing AI assessment
        job_info = _find_job_from_storage(job_id)
        entry = record(
            job_id=job_id,
            outcome=outcome,
            notes=notes,
            ai_priority=job_info.get("apply_priority") if job_info else None,
            ai_score=float(job_info.get("composite_score") or 0) if job_info else None,
            title=job_info.get("title") if job_info else None,
            company=job_info.get("company") if job_info else None,
        )
        print(f"\n  {_GREEN}✓ Recorded:{_RESET} {job_id} → {outcome}")
        if notes:
            print(f"    Notes: {notes}")
        if job_info:
            print(f"    AI had: priority={job_info.get('apply_priority')} score={job_info.get('composite_score')}")
        print()
        show_summary()
        return entry
    else:
        # No args — just show summary
        show_summary(verbose=True)
        print(f"  To record a new outcome, run:")
        print(f"  {_CYAN}python main.py --eval feedback --job-id JOB-123 --outcome interview{_RESET}")
        print(f"  Valid outcomes: {', '.join(VALID_OUTCOMES)}\n")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record job application feedback")
    parser.add_argument("--job-id", help="Job ID to record outcome for")
    parser.add_argument("--outcome", choices=VALID_OUTCOMES, help="Actual outcome")
    parser.add_argument("--notes", default="", help="Optional context")
    args = parser.parse_args()

    run_interactive(args.job_id, args.outcome, args.notes)
