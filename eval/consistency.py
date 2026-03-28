"""
Consistency checker — runs the same job through Claude 3 times and reports
score variance. High variance means the prompt is under-constrained and
Claude is guessing rather than reasoning.

Thresholds (configurable):
  composite_score variance > 1.0  → WARNING
  composite_score variance > 2.0  → FAIL
  priority flip across runs        → FAIL

Usage:
  python main.py --eval consistency          # uses built-in sample job
  python main.py --eval consistency --case 001  # uses golden case by prefix
"""

import json
import sys
import statistics
from pathlib import Path
from typing import Any


CASES_DIR = Path(__file__).parent / "golden_cases"

_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"

RUNS = 3  # How many times to score the same job
WARN_THRESHOLD = 1.0
FAIL_THRESHOLD = 2.0

# Built-in sample — a representative mid-tier job used when no case specified
_DEFAULT_JOB = {
    "job_id": "CONSISTENCY-001",
    "title": "Senior Product Manager — Platform",
    "company": "Atlassian",
    "location": "Singapore",
    "region": "Singapore",
    "remote_type": "Hybrid",
    "salary_text": "S$160,000-S$200,000/year",
    "full_description": (
        "Atlassian is hiring a Senior PM to lead our Jira Service Management "
        "workflow automation product. You will own the roadmap for enterprise "
        "service desk integrations, automation rules, and approval workflows. "
        "Requirements: 7-10 years of PM experience in B2B SaaS or enterprise "
        "software. Experience with workflow automation, ITSM, or platform products "
        "strongly preferred. We sponsor Employment Pass for eligible candidates. "
        "Hybrid work from Singapore office."
    ),
}


def _load_job_from_case(prefix: str) -> dict | None:
    for path in sorted(CASES_DIR.glob("*.json")):
        if path.stem.startswith(prefix):
            with open(path) as f:
                data = json.load(f)
            return data.get("job")
    return None


def run(case_prefix: str = None, verbose: bool = True) -> dict[str, Any]:
    """
    Score the same job RUNS times and measure variance.
    """
    try:
        from enricher.claude_enricher import enrich_all
    except ImportError as e:
        print(f"{_RED}ERROR: Could not import enricher — {e}{_RESET}")
        sys.exit(1)

    if case_prefix:
        job = _load_job_from_case(case_prefix)
        if job is None:
            print(f"{_YELLOW}No golden case matching prefix {case_prefix!r}{_RESET}")
            sys.exit(1)
        job_label = f"golden case {case_prefix}"
    else:
        job = _DEFAULT_JOB.copy()
        job_label = "built-in sample (Atlassian Singapore)"

    print(f"\n{_BOLD}{'='*60}{_RESET}")
    print(f"{_BOLD}Eval: Consistency — {RUNS} runs × {job_label}{_RESET}")
    print(f"  Job: {job.get('title')} @ {job.get('company')} ({job.get('region')})")
    print(f"{'='*60}\n")

    scores = []
    priorities = []
    visa_results = []
    role_scores = []

    for i in range(1, RUNS + 1):
        # Give each run a unique job_id so dedup doesn't skip it
        run_job = {**job, "job_id": f"{job.get('job_id', 'CONS')}-run{i}"}
        print(f"  Run {i}/{RUNS}...", end=" ", flush=True)
        try:
            enriched_list = enrich_all([run_job])
            if not enriched_list:
                print(f"{_RED}no result{_RESET}")
                continue
            e = enriched_list[0]
            composite = float(e.get("composite_score", 0) or 0)
            priority  = e.get("apply_priority", "?")
            visa      = e.get("visa_sponsor_detected", "?")
            role      = float(e.get("role_match_score", 0) or 0)
            scores.append(composite)
            priorities.append(priority)
            visa_results.append(visa)
            role_scores.append(role)
            print(f"composite={composite:.1f}  role={role:.1f}  visa={visa}  priority={priority}")
        except Exception as e:
            print(f"{_RED}ERROR — {e}{_RESET}")

    if not scores:
        print(f"{_RED}All runs failed — no results to analyse{_RESET}\n")
        return {"status": "error", "message": "all runs failed"}

    composite_range = max(scores) - min(scores)
    role_range      = max(role_scores) - min(role_scores)
    priority_set    = set(priorities)
    visa_set        = set(visa_results)
    priority_flip   = len(priority_set) > 1
    visa_flip       = len(visa_set) > 1

    mean_composite = statistics.mean(scores)
    mean_role      = statistics.mean(role_scores)
    stdev_composite = statistics.stdev(scores) if len(scores) > 1 else 0.0

    print()
    print(f"  {'─'*50}")
    print(f"  Composite scores : {[round(s, 1) for s in scores]}")
    print(f"  Mean             : {mean_composite:.2f}")
    print(f"  Range (max-min)  : {composite_range:.2f}")
    print(f"  Std dev          : {stdev_composite:.2f}")
    print(f"  Role scores      : {[round(r, 1) for r in role_scores]}  (range {role_range:.1f})")
    print(f"  Priorities       : {priorities}")
    print(f"  Visa detections  : {visa_results}")
    print()

    issues = []
    if composite_range >= FAIL_THRESHOLD:
        issues.append(f"composite_score range={composite_range:.1f} >= {FAIL_THRESHOLD} (FAIL)")
    elif composite_range >= WARN_THRESHOLD:
        issues.append(f"composite_score range={composite_range:.1f} >= {WARN_THRESHOLD} (WARN)")
    if priority_flip:
        issues.append(f"priority flipped across runs: {sorted(priority_set)}")
    if visa_flip:
        issues.append(f"visa detection inconsistent: {sorted(visa_set)}")

    if not issues:
        verdict = f"{_GREEN}✓ STABLE{_RESET}"
        outcome = "pass"
    elif any("FAIL" in i or "flip" in i for i in issues):
        verdict = f"{_RED}✗ UNSTABLE{_RESET}"
        outcome = "fail"
    else:
        verdict = f"{_YELLOW}⚠ WARNING{_RESET}"
        outcome = "warn"

    print(f"  Verdict: {verdict}")
    for iss in issues:
        colour = _RED if "FAIL" in iss or "flip" in iss else _YELLOW
        print(f"  {colour}  {iss}{_RESET}")

    print(f"\n{'='*60}\n")

    return {
        "status": outcome,
        "runs": RUNS,
        "scores": scores,
        "mean_composite": round(mean_composite, 2),
        "stdev_composite": round(stdev_composite, 2),
        "composite_range": round(composite_range, 2),
        "role_range": round(role_range, 2),
        "priority_flip": priority_flip,
        "visa_flip": visa_flip,
        "issues": issues,
    }


if __name__ == "__main__":
    prefix = sys.argv[1] if len(sys.argv) > 1 else None
    result = run(case_prefix=prefix)
    sys.exit(0 if result["status"] in ("pass", "warn") else 1)
