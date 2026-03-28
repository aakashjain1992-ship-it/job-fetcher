"""
Regression runner — loads golden test cases and checks Claude's output
against expected values. Catches scoring drift and classification bugs.

Usage:
  python main.py --eval regression         # run all cases
  python main.py --eval regression --case 001  # single case by prefix
"""

import json
import os
import sys
from pathlib import Path
from typing import Any


CASES_DIR = Path(__file__).parent / "golden_cases"

# Terminal colour codes
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"


def _load_cases(prefix: str = None) -> list[dict]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.json")):
        if prefix and not path.stem.startswith(prefix):
            continue
        with open(path) as f:
            cases.append(json.load(f))
    return cases


def _check(result: dict, expected: dict) -> list[str]:
    """
    Compare enriched result against expected assertions.
    Returns list of failure messages (empty = pass).
    """
    failures = []

    for key, exp_val in expected.items():

        # --- Exact match assertions ---
        if key == "visa_sponsor_detected":
            got = result.get("visa_sponsor_detected")
            if got != exp_val:
                failures.append(
                    f"visa_sponsor_detected: expected={exp_val!r} got={got!r}"
                )

        elif key == "apply_priority":
            got = result.get("apply_priority")
            if got != exp_val:
                failures.append(
                    f"apply_priority: expected={exp_val!r} got={got!r}"
                )

        # --- NOT assertions ---
        elif key == "apply_priority_not":
            got = result.get("apply_priority")
            if got == exp_val:
                failures.append(
                    f"apply_priority: expected NOT {exp_val!r} but got {got!r}"
                )

        # --- Min/max assertions (composite_score) ---
        elif key == "composite_score_min":
            got = float(result.get("composite_score", 0) or 0)
            if got < exp_val:
                failures.append(
                    f"composite_score: expected >= {exp_val} got {got:.1f}"
                )

        elif key == "composite_score_max":
            got = float(result.get("composite_score", 0) or 0)
            if got > exp_val:
                failures.append(
                    f"composite_score: expected <= {exp_val} got {got:.1f}"
                )

        # --- Min/max assertions (role_match_score) ---
        elif key == "role_match_score_min":
            got = float(result.get("role_match_score", 0) or 0)
            if got < exp_val:
                failures.append(
                    f"role_match_score: expected >= {exp_val} got {got:.1f}"
                )

        elif key == "role_match_score_max":
            got = float(result.get("role_match_score", 0) or 0)
            if got > exp_val:
                failures.append(
                    f"role_match_score: expected <= {exp_val} got {got:.1f}"
                )

        # --- Boolean assertions ---
        elif key == "red_flags_empty":
            flags = result.get("red_flags", [])
            is_empty = not flags or flags == []
            if is_empty == exp_val:
                # If red_flags_empty=false, we want flags to be non-empty
                pass
            else:
                failures.append(
                    f"red_flags: expected {'empty' if exp_val else 'non-empty'} "
                    f"got {flags!r}"
                )

    return failures


def run(case_prefix: str = None, verbose: bool = True) -> dict[str, Any]:
    """
    Run all golden cases (or a subset by prefix) through Claude.
    Returns summary dict.
    """
    # Import here to avoid circular imports when called from main.py
    try:
        from enricher.claude_enricher import enrich_all
    except ImportError as e:
        print(f"{_RED}ERROR: Could not import enricher — {e}{_RESET}")
        sys.exit(1)

    cases = _load_cases(case_prefix)
    if not cases:
        print(f"{_YELLOW}No golden cases found{' matching prefix ' + case_prefix if case_prefix else ''}{_RESET}")
        return {"total": 0, "passed": 0, "failed": 0}

    print(f"\n{_BOLD}{'='*60}{_RESET}")
    print(f"{_BOLD}Eval: Regression — {len(cases)} case(s){_RESET}")
    print(f"{'='*60}\n")

    passed = 0
    failed = 0
    results_detail = []

    for case in cases:
        name = case.get("name", "?")
        desc = case.get("description", "")
        job  = case.get("job", {})
        expected = case.get("expected", {})

        print(f"  Running: {name}")
        if verbose:
            print(f"           {desc}")

        try:
            enriched_list = enrich_all([job])
            if not enriched_list:
                print(f"  {_RED}✗ FAIL — enricher returned no results{_RESET}\n")
                failed += 1
                results_detail.append({"name": name, "status": "fail", "failures": ["enricher returned empty list"]})
                continue
            enriched = enriched_list[0]
        except Exception as e:
            print(f"  {_RED}✗ ERROR — {e}{_RESET}\n")
            failed += 1
            results_detail.append({"name": name, "status": "error", "failures": [str(e)]})
            continue

        failures = _check(enriched, expected)

        if not failures:
            status_line = (
                f"  {_GREEN}✓ PASS{_RESET}  "
                f"score={enriched.get('composite_score', '?')} "
                f"priority={enriched.get('apply_priority', '?')} "
                f"visa={enriched.get('visa_sponsor_detected', '?')}"
            )
            print(status_line + "\n")
            passed += 1
            results_detail.append({"name": name, "status": "pass"})
        else:
            print(f"  {_RED}✗ FAIL{_RESET}  ({len(failures)} assertion(s) failed):")
            for f in failures:
                print(f"         {_YELLOW}⚠  {f}{_RESET}")
            print(f"         [actual] composite={enriched.get('composite_score')}, "
                  f"role={enriched.get('role_match_score')}, "
                  f"visa={enriched.get('visa_sponsor_detected')}, "
                  f"priority={enriched.get('apply_priority')}, "
                  f"red_flags={len(enriched.get('red_flags') or [])}\n")
            failed += 1
            results_detail.append({"name": name, "status": "fail", "failures": failures})

    total = passed + failed
    pct = round(passed / total * 100) if total else 0
    colour = _GREEN if pct == 100 else (_YELLOW if pct >= 70 else _RED)

    print(f"{'='*60}")
    print(f"{_BOLD}Results: {colour}{passed}/{total} passed ({pct}%){_RESET}")
    if failed:
        print(f"{_RED}{failed} case(s) failed — check scoring logic or prompt calibration{_RESET}")
    print(f"{'='*60}\n")

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate_pct": pct,
        "cases": results_detail,
    }


if __name__ == "__main__":
    import sys
    prefix = sys.argv[1] if len(sys.argv) > 1 else None
    result = run(case_prefix=prefix)
    sys.exit(0 if result["failed"] == 0 else 1)
