"""
Claude Haiku enricher.
Processes jobs in batches of 8, scores them against the candidate's profile,
detects visa sponsorship, classifies remote type, and calculates apply priority.
"""

import os
import json
import time
from typing import List, Dict, Any

import re

import anthropic

from config.profile import (
    CANDIDATE_PROFILE, EQUIVALENT_ROLES,
    HIGH_FIT_DOMAINS, VISA_POSITIVE_SIGNALS, VISA_NEGATIVE_SIGNALS
)

_NAME = CANDIDATE_PROFILE["name"]

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

BATCH_SIZE = 8  # Jobs per API call — balances cost vs context length

# FX rates for INR conversion (update periodically)
FX_RATES = {
    "USD": 83.5,
    "AED": 22.7,
    "SGD": 62.0,
    "EUR": 90.5,
    "GBP": 106.0,
    "GBP_sym": "£",
}

_visa_str = "Yes" if CANDIDATE_PROFILE.get("visa_required") else "No"
_loc_str = CANDIDATE_PROFILE.get("location", "")
_visa_line = f"Yes — based in {_loc_str}, needs work visa sponsorship" if CANDIDATE_PROFILE.get("visa_required") else "No — already authorized to work"

SYSTEM_PROMPT = f"""You are a career intelligence analyst for a senior product manager.

CANDIDATE PROFILE:
- Name: {CANDIDATE_PROFILE["name"]}
- Experience: {CANDIDATE_PROFILE['total_experience_years']} years, all in Product Management
- Current role: {CANDIDATE_PROFILE['current_title']} at {CANDIDATE_PROFILE['current_company']}
- Location: {_loc_str}
- Target: ₹{CANDIDATE_PROFILE['target_ctc_lpa']}L+ or international equivalent (AED 450K+, S$180K+, $200K+, €110K+)
- Visa required: {_visa_line}

CORE STRENGTHS:
{chr(10).join(f"- {s}" for s in CANDIDATE_PROFILE['strengths'])}

KNOWN GAPS (being actively closed):
{chr(10).join(f"- {g}" for g in CANDIDATE_PROFILE['known_gaps'])}

DOMAIN FIT (high → low):
{chr(10).join(f"- {d}" for d in CANDIDATE_PROFILE['domain_strengths'])}

KEY METRICS:
{chr(10).join(f"- {m}" for m in CANDIDATE_PROFILE['key_metrics'])}

PORTFOLIO STATUS:
- Case studies published: {CANDIDATE_PROFILE['portfolio_status']['case_studies_published']}
- AI products launched publicly: {CANDIDATE_PROFILE['portfolio_status']['ai_products_launched']}
- Portfolio site live: {CANDIDATE_PROFILE['portfolio_status']['portfolio_site_live']}

ROLE EQUIVALENTS (all count as target roles):
{', '.join(EQUIVALENT_ROLES)}

Your job is to evaluate each job opportunity and return structured JSON scores.
Be honest and precise. Do not inflate scores to be encouraging.
"""

USER_PROMPT_TEMPLATE = """Evaluate these {n} job listings for the candidate.

For EACH job, return a JSON object with these exact fields:

{{
  "job_id": "the job_id provided",
  "role_match_score": 0-10 (how well title+JD matches Senior PM or equivalent — be strict: 10=perfect, 7=strong, 5=reasonable, 3=stretch, 1=misaligned),
  "visa_sponsor_detected": "Yes" | "No" | "Unclear" (scan JD for visa/sponsorship language),
  "remote_type": "Remote" | "Remote-friendly" | "Hybrid" | "Onsite",
  "salary_range": "extracted salary string or empty string",
  "inr_equivalent_lpa": number (estimated annual comp in INR lakhs, 0 if unknown),
  "resume_match_pct": 0-100 (how closely JD requirements match the candidate's actual profile — do not round to 5s, be precise),
  "key_matching_skills": ["up to 4 skills from JD that match the candidate's background"],
  "red_flags": ["list of blocking issues e.g. 'requires US citizenship', 'on-site only Dubai with no relocation', 'needs 15+ years' — empty list if none"],
  "gap_to_close": "specific thing he needs before applying — e.g. 'portfolio case study (2 weeks)' or 'ready now — apply within 5 days'",
  "composite_score": 0-10 (weighted: role_match 40% + visa_ok 20% + resume_match 30% + no_red_flags 10%),
  "apply_priority": "High" | "Medium" | "Low",
  "apply_after": "now" | "resume fix done" | "1 case study published" | "portfolio ready" | "AI product launched" | "60 days",
  "notes": "1 sentence on why this role is or isn't a strong match for the candidate specifically"
}}

Return a JSON array of {n} objects. No other text, no markdown, no explanation — just the JSON array.

JOBS TO EVALUATE:

{jobs_json}
"""


def _estimate_inr(salary_text: str, region: str) -> int:
    """Rough INR conversion from salary text."""
    if not salary_text:
        return 0
    try:
        nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", salary_text) if len(n.replace(",","")) >= 4]
        if not nums:
            return 0
        avg = sum(nums) / len(nums)

        t = salary_text.upper()
        if "AED" in t or region == "Dubai":
            return round(avg * FX_RATES["AED"] / 100000)
        elif "SGD" in t or "S$" in t or region == "Singapore":
            return round(avg * FX_RATES["SGD"] / 100000)
        elif "EUR" in t or "€" in t or region == "EU":
            return round(avg * FX_RATES["EUR"] / 100000)
        elif "GBP" in t or "£" in t:
            return round(avg * FX_RATES["GBP"] / 100000)
        elif "$" in t or region == "USA":
            return round(avg * FX_RATES["USD"] / 100000)
    except Exception:
        pass
    return 0


def enrich_batch(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Send a batch of jobs to Claude Haiku for enrichment.
    Returns list of enriched job dicts.
    """
    if not jobs:
        return []

    # Prepare compact job summaries for Claude (reduce tokens)
    jobs_for_claude = []
    for job in jobs:
        jobs_for_claude.append({
            "job_id": job.get("job_id", ""),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "region": job.get("region", ""),
            "description": (job.get("full_description") or job.get("snippet", ""))[:1200],
            "salary_text": job.get("salary_text", ""),
            "remote_type_raw": job.get("remote_type", ""),
        })

    prompt = USER_PROMPT_TEMPLATE.format(
        n=len(jobs),
        jobs_json=json.dumps(jobs_for_claude, indent=2)
    )

    raw = ""
    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()

        # Strip any accidental markdown fences
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        enriched_list = json.loads(raw)

    except json.JSONDecodeError as e:
        print(f"Claude JSON parse error: {e}")
        print(f"Raw response: {raw[:300] if raw else '(empty)'}")
        return []
    except Exception as e:
        print(f"Claude API error: {e}")
        return []

    # Merge enrichment data back onto original job dicts
    enriched_by_id = {e["job_id"]: e for e in enriched_list}
    results = []

    for job in jobs:
        jid = job.get("job_id", "")
        enrichment = enriched_by_id.get(jid, {})

        # Fill any missing inr_equivalent from salary_text
        inr = enrichment.get("inr_equivalent_lpa", 0)
        if not inr and job.get("salary_text"):
            inr = _estimate_inr(job["salary_text"], job.get("region", ""))

        merged = {**job, **enrichment, "inr_equivalent_lpa": inr}
        results.append(merged)

    return results


def enrich_all(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Enrich all jobs in batches.
    Returns all enriched jobs.
    """
    if not jobs:
        return []

    print(f"\nEnriching {len(jobs)} jobs with Claude Haiku...")
    all_enriched = []

    for i in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[i:i + BATCH_SIZE]
        print(f"  Batch {i // BATCH_SIZE + 1}/{(len(jobs) - 1) // BATCH_SIZE + 1} ({len(batch)} jobs)...")

        enriched = enrich_batch(batch)
        all_enriched.extend(enriched)

        # Rate limit: ~5 requests/min for Haiku is safe
        if i + BATCH_SIZE < len(jobs):
            time.sleep(2)

    # Sort by composite score descending
    all_enriched.sort(key=lambda x: float(x.get("composite_score", 0) or 0), reverse=True)

    high = sum(1 for j in all_enriched if j.get("apply_priority") == "High")
    medium = sum(1 for j in all_enriched if j.get("apply_priority") == "Medium")
    print(f"\nEnrichment complete: {high} High priority, {medium} Medium priority")

    return all_enriched
