"""
Output validator — runs on every enriched job after Claude scores it.
Catches missing fields, out-of-range values, logic inconsistencies.
Called automatically inside enrich_batch().
"""

from typing import Any

REQUIRED_FIELDS = [
    "job_id", "role_match_score", "visa_sponsor_detected",
    "resume_match_pct", "composite_score", "apply_priority",
    "red_flags", "gap_to_close", "apply_after", "notes",
]

VALID_RANGES = {
    "role_match_score":  (0, 10),
    "resume_match_pct":  (0, 100),
    "composite_score":   (0, 10),
}

VALID_ENUMS = {
    "visa_sponsor_detected": {"Yes", "No", "Unclear"},
    "apply_priority":        {"High", "Medium", "Low"},
    "remote_type":           {"Remote", "Remote-friendly", "Hybrid", "Onsite"},
}

# Weights — pulled from profile config so they stay in sync with the enricher prompt
try:
    from config.profile import SCORING_WEIGHTS as DEFAULT_WEIGHTS
except Exception:
    DEFAULT_WEIGHTS = {"role_match": 0.40, "visa": 0.20, "resume": 0.30, "red_flags": 0.10}


def validate(enrichment: dict[str, Any], weights: dict = None) -> list[str]:
    """
    Validate a single enriched job dict.
    Returns a list of issue strings (empty = all good).
    """
    issues = []
    w = weights or DEFAULT_WEIGHTS

    # 1. Required fields
    for f in REQUIRED_FIELDS:
        if f not in enrichment or enrichment[f] is None:
            issues.append(f"missing_field:{f}")

    # 2. Range checks
    for field, (lo, hi) in VALID_RANGES.items():
        val = enrichment.get(field)
        if val is not None:
            try:
                if not (lo <= float(val) <= hi):
                    issues.append(f"out_of_range:{field}={val} (expected {lo}–{hi})")
            except (TypeError, ValueError):
                issues.append(f"not_numeric:{field}={val!r}")

    # 3. Enum checks
    for field, valid_set in VALID_ENUMS.items():
        val = enrichment.get(field)
        if val is not None and val not in valid_set:
            issues.append(f"invalid_enum:{field}={val!r} (expected {sorted(valid_set)})")

    # 4. Composite score consistency — recompute from components and compare
    try:
        role  = float(enrichment.get("role_match_score", 0) or 0)
        visa_str = enrichment.get("visa_sponsor_detected", "Unclear")
        visa_score = {"Yes": 2, "Unclear": 1, "No": 0}.get(visa_str, 1)
        res   = float(enrichment.get("resume_match_pct", 0) or 0)
        flags = 0 if enrichment.get("red_flags") else 1

        # Normalise visa (0–2) to 0–1 range before weighting
        expected = (
            role / 10   * w["role_match"] +
            visa_score / 2 * w["visa"] +
            res / 100   * w["resume"] +
            flags       * w["red_flags"]
        ) * 10   # scale to 0–10

        actual = float(enrichment.get("composite_score", 0) or 0)
        if abs(expected - actual) > 1.5:
            issues.append(
                f"score_inconsistency:recomputed={expected:.1f} claude_said={actual:.1f} "
                f"(role={role}, visa={visa_str}, resume={res}%, red_flags={bool(enrichment.get('red_flags'))})"
            )
    except Exception as e:
        issues.append(f"score_check_error:{e}")

    # 5. Priority vs score alignment
    try:
        score = float(enrichment.get("composite_score", 0) or 0)
        priority = enrichment.get("apply_priority", "")
        if score >= 8.5 and priority != "High":
            issues.append(f"priority_mismatch:score={score:.1f} but priority={priority!r} (expected High)")
        if score < 4.0 and priority == "High":
            issues.append(f"priority_mismatch:score={score:.1f} but priority=High (too generous)")
    except Exception:
        pass

    # 6. List field type checks
    for list_field in ("red_flags", "key_matching_skills"):
        val = enrichment.get(list_field)
        if val is not None and not isinstance(val, list):
            issues.append(f"wrong_type:{list_field} should be list, got {type(val).__name__}")

    return issues


def validate_batch(enriched_jobs: list[dict], verbose: bool = True) -> dict:
    """
    Validate a list of enriched jobs.
    Returns summary stats + per-job issues.
    """
    results = []
    total_issues = 0

    for job in enriched_jobs:
        job_id = job.get("job_id", "?")
        issues = validate(job)
        total_issues += len(issues)
        if issues:
            results.append({"job_id": job_id, "issues": issues})
            if verbose:
                print(f"  [EVAL] {job_id} — {len(issues)} issue(s):")
                for iss in issues:
                    print(f"         ⚠  {iss}")

    passed = len(enriched_jobs) - len(results)
    if verbose and enriched_jobs:
        pct = round(passed / len(enriched_jobs) * 100)
        print(f"  [EVAL] Validation: {passed}/{len(enriched_jobs)} passed ({pct}%) — {total_issues} total issues")

    return {
        "total": len(enriched_jobs),
        "passed": passed,
        "failed": len(results),
        "total_issues": total_issues,
        "failures": results,
    }
