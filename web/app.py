"""
Job Fetcher Dashboard — FastAPI backend.
Serves the 3-tab dashboard and provides JSON APIs for jobs, tracking, and training.

Run:  python main.py --serve
      (or directly: uvicorn web.app:app --reload --port 8000)
"""

import os
import json
from datetime import datetime, timezone, date
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from supabase import create_client
except ImportError:
    raise ImportError("Run: pip install supabase")

try:
    import anthropic
except ImportError:
    raise ImportError("Run: pip install anthropic")

# ── Config ────────────────────────────────────────────────────────────────────

TEMPLATES_DIR = Path(__file__).parent / "templates"

CURRENT_CTC_LPA = 49.9          # Used for % delta calculation
CURRENT_STOCK_USD = 10_000      # Akamai annual RSU
FX_INR_PER_USD = 83.5


def _supa():
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
    if not url or not key:
        raise HTTPException(500, "Supabase credentials not configured")
    return create_client(url, key)


def _claude():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=key)


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Job Fetcher Dashboard", docs_url=None, redoc_url=None)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    html_path = TEMPLATES_DIR / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Dashboard HTML not found</h1>", status_code=500)
    return HTMLResponse(html_path.read_text())


# ── Jobs API ──────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    """Summary stats for the dashboard header."""
    db = _supa()
    all_jobs = db.table("jobs").select("composite_score, apply_priority, apply_status, fetched_date, region").execute()
    jobs = all_jobs.data or []

    today = date.today().isoformat()
    week_ago = (datetime.now(timezone.utc).date().replace(day=datetime.now(timezone.utc).day - 7)).isoformat()

    total = len(jobs)
    high = sum(1 for j in jobs if j.get("apply_priority") == "High")
    applied = sum(1 for j in jobs if j.get("apply_status") == "Applied")
    interviews = sum(1 for j in jobs if j.get("apply_status") in ("Screening", "Interview", "Final"))
    new_this_week = sum(1 for j in jobs if (j.get("fetched_date") or "") >= week_ago)

    return {
        "total_jobs": total,
        "high_priority": high,
        "applied": applied,
        "interviews": interviews,
        "new_this_week": new_this_week,
        "current_ctc_lpa": CURRENT_CTC_LPA,
    }


@app.get("/api/jobs")
def list_jobs(
    region: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    visa: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    remote: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100),
    offset: int = Query(0),
):
    db = _supa()
    q = db.table("jobs").select(
        "job_id, title, company, region, seniority, posted_date, fetched_date, "
        "url, url_direct, salary_text, remote_type, role_match_score, "
        "visa_sponsor_detected, resume_match_pct, key_matching_skills, red_flags, "
        "gap_to_close, composite_score, apply_priority, apply_after, enrichment_notes, "
        "inr_equivalent_lpa, salary_range, apply_status, applied_date, follow_up_date"
    ).order("composite_score", desc=True)

    if region:
        q = q.eq("region", region)
    if priority:
        q = q.eq("apply_priority", priority)
    if visa:
        q = q.eq("visa_sponsor_detected", visa)
    if status:
        q = q.eq("apply_status", status)
    if remote:
        q = q.eq("remote_type", remote)
    if min_score is not None:
        q = q.gte("composite_score", min_score)

    result = q.range(offset, offset + limit - 1).execute()
    jobs = result.data or []

    # Attach CTC delta
    for job in jobs:
        inr = job.get("inr_equivalent_lpa") or 0
        if inr > 0:
            job["ctc_delta_pct"] = round(((inr - CURRENT_CTC_LPA) / CURRENT_CTC_LPA) * 100, 1)
        else:
            job["ctc_delta_pct"] = None

    # Client-side search filter (Supabase free tier has limited full-text search)
    if search:
        q_lower = search.lower()
        jobs = [
            j for j in jobs
            if q_lower in (j.get("title") or "").lower()
            or q_lower in (j.get("company") or "").lower()
        ]

    return {"jobs": jobs, "total": len(jobs)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    db = _supa()
    result = db.table("jobs").select("*").eq("job_id", job_id).single().execute()
    if not result.data:
        raise HTTPException(404, "Job not found")
    job = result.data
    inr = job.get("inr_equivalent_lpa") or 0
    if inr > 0:
        job["ctc_delta_pct"] = round(((inr - CURRENT_CTC_LPA) / CURRENT_CTC_LPA) * 100, 1)
    return job


class StatusUpdate(BaseModel):
    apply_status: str
    applied_date: Optional[str] = None
    interview_date: Optional[str] = None
    follow_up_date: Optional[str] = None
    recruiter_notes: Optional[str] = None
    offer_details: Optional[str] = None


VALID_STATUSES = {
    "New", "Saved", "Applied", "Screening", "Interview",
    "Final", "Offer", "Accepted", "Rejected", "Withdrawn"
}


@app.patch("/api/jobs/{job_id}/status")
def update_status(job_id: str, body: StatusUpdate):
    if body.apply_status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(VALID_STATUSES))}")
    db = _supa()
    update = {"apply_status": body.apply_status}
    if body.applied_date:
        update["applied_date"] = body.applied_date
    if body.interview_date:
        update["interview_date"] = body.interview_date
    if body.follow_up_date:
        update["follow_up_date"] = body.follow_up_date
    if body.recruiter_notes:
        update["recruiter_notes"] = body.recruiter_notes
    if body.offer_details:
        update["offer_details"] = body.offer_details
    db.table("jobs").update(update).eq("job_id", job_id).execute()
    return {"ok": True}


# ── Training API ──────────────────────────────────────────────────────────────

@app.post("/api/training/prep/{job_id}")
def generate_interview_prep(job_id: str):
    """Generate tailored interview prep for a specific job using Claude."""
    db = _supa()
    result = db.table("jobs").select(
        "title, company, region, full_description, key_matching_skills, "
        "gap_to_close, enrichment_notes, composite_score"
    ).eq("job_id", job_id).single().execute()

    if not result.data:
        raise HTTPException(404, "Job not found")

    job = result.data
    claude = _claude()

    profile_context = """
Candidate: Aakash Jain, Senior Technical Product Manager, 10 years experience
Current: Akamai Technologies — 3 external partner platforms (Logistics Portal, Field Tech Portal, Network Partner Portal)
Key metrics: 30% user satisfaction ↑, 40% support tickets ↓, 70% TAT reduction (workflow automation), 18% MRR ↑, 20+ integrations
Domain: Platform PM, B2B SaaS, Operations automation, Logistics tech, Enterprise platforms
Tech: SQL, PostgreSQL, Figma, SAFe, Agile, Jira
"""

    jd_summary = f"""
Role: {job.get('title')} at {job.get('company')} ({job.get('region')})
Description excerpt: {(job.get('full_description') or '')[:1500]}
Matching skills: {', '.join(job.get('key_matching_skills') or [])}
Gap to close: {job.get('gap_to_close', '')}
"""

    prompt = f"""You are a PM interview coach preparing Aakash Jain for a specific interview.

CANDIDATE PROFILE:
{profile_context}

TARGET ROLE:
{jd_summary}

Generate a targeted interview prep pack with these sections:

1. COMPANY & ROLE SNAPSHOT (3 bullets: what this company does, what this PM role owns, why it's relevant to Aakash)

2. TOP 5 BEHAVIORAL QUESTIONS (specific to this JD's focus areas)
   For each: question + Aakash's suggested STAR answer using his actual experience + metrics

3. PM CASE STUDY CHALLENGE (one realistic case problem this company would give)
   Include: problem statement, framework to use, key trade-offs to address, what a great answer looks like

4. WHICH CASE STUDY TO PITCH (from Aakash's experience, which project maps best to this role and why)

5. QUESTIONS TO ASK THE INTERVIEWER (3 smart questions that show product depth)

6. RED FLAGS TO ADDRESS (based on gap_to_close: how to proactively handle the gap in the interview)

Keep it practical, specific, and directly tied to Aakash's real experience. No generic PM advice.
"""

    message = claude.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return {"prep": message.content[0].text, "job_id": job_id}


@app.get("/api/training/behavioral")
def get_behavioral_questions():
    """Return standard behavioral questions tailored to Aakash's profile."""
    return {
        "questions": [
            {
                "category": "Platform PM",
                "question": "Tell me about a time you managed multiple products simultaneously. How did you prioritise?",
                "hint": "Use Akamai: 3 platforms, SAFe OKR-aligned prioritisation, cross-functional delivery"
            },
            {
                "category": "Stakeholder Management",
                "question": "Describe a situation where engineering and business had conflicting priorities. How did you resolve it?",
                "hint": "Use examples from cross-functional delivery at Akamai or Vinculum (20+ integrations)"
            },
            {
                "category": "Data-Driven Decisions",
                "question": "Walk me through a product decision you made using data. What was the outcome?",
                "hint": "70% TAT reduction at Jiffyship — rule-based decisioning backed by operational data"
            },
            {
                "category": "Product Strategy",
                "question": "Tell me about a new product or feature you launched from 0 to 1.",
                "hint": "LCL feature at Jiffyship — identified underserved SME segment, end-to-end launch"
            },
            {
                "category": "Revenue Impact",
                "question": "Have you ever directly influenced revenue? How?",
                "hint": "18% MRR increase at Vinculum via pricing redesign; LCL on track for 30% annual revenue"
            },
            {
                "category": "Technical PM",
                "question": "How do you work with engineering teams? Give an example where your technical understanding made a difference.",
                "hint": "SQL query optimisation at Vinculum reducing page load from 10s → 2s; API integration work"
            },
            {
                "category": "User Research",
                "question": "Tell me about a time user feedback changed your product direction.",
                "hint": "30% satisfaction improvement at Akamai via structured user research and UX iteration"
            },
            {
                "category": "Failure",
                "question": "Tell me about a product decision that didn't go as planned. What did you learn?",
                "hint": "Be honest and specific — shows maturity. Pick something with a clear learning."
            },
        ]
    }


@app.get("/api/training/case-frameworks")
def get_case_frameworks():
    """PM case study frameworks relevant to Aakash's target roles."""
    return {
        "frameworks": [
            {
                "name": "Product Design (Platform)",
                "steps": ["Clarify goals & constraints", "Define users & their jobs-to-be-done",
                          "Identify pain points (rank by freq × severity)", "Brainstorm solutions (breadth first)",
                          "Prioritise (impact vs effort matrix)", "Define success metrics", "MVP scope"],
                "relevant_for": "Platform PM, B2B SaaS, Infrastructure"
            },
            {
                "name": "Product Improvement",
                "steps": ["Understand current product & metrics", "Pick one metric to improve (explain why)",
                          "Diagnose root causes", "Generate solutions", "Prioritise",
                          "Measure success", "Roll-out plan"],
                "relevant_for": "Operations tools, Workflow automation, Enterprise platforms"
            },
            {
                "name": "Go-to-Market",
                "steps": ["Define target segment", "Understand buyer vs user", "Competitive landscape",
                          "Pricing strategy", "Channels", "Launch sequence", "Success metrics"],
                "relevant_for": "New product launch (LCL-type scenarios), integration launches"
            },
            {
                "name": "Metric Deep-Dive",
                "steps": ["Clarify the metric", "Check if it's real (data quality)", "Segment the change",
                          "Hypothesise causes (internal vs external)", "Validate with data",
                          "Recommended action"],
                "relevant_for": "Data-driven PM roles, Operations, Analytics-heavy companies"
            },
        ]
    }


# ── Resume Analysis API ───────────────────────────────────────────────────────

@app.get("/api/resume/latest")
def get_latest_analysis():
    """Get the most recent resume gap analysis."""
    db = _supa()
    result = db.table("resume_analyses") \
        .select("*") \
        .order("analyzed_at", desc=True) \
        .limit(1) \
        .execute()
    if not result.data:
        return {"analysis": None, "message": "No analysis run yet. Click 'Run Analysis' to start."}
    return {"analysis": result.data[0]}


@app.post("/api/resume/analyze")
def run_resume_analysis():
    """
    Fetch a sample of recent JDs and run gap analysis vs Aakash's profile.
    Expensive — run manually, not on every request.
    """
    db = _supa()
    # Use recent high-priority jobs as the JD corpus
    jobs_result = db.table("jobs").select(
        "title, company, full_description, composite_score, key_matching_skills, red_flags, gap_to_close"
    ).gte("composite_score", 5).order("composite_score", desc=True).limit(30).execute()

    jobs = jobs_result.data or []
    if len(jobs) < 5:
        return JSONResponse({"error": "Not enough jobs fetched yet. Run the pipeline first."}, 400)

    jd_corpus = "\n\n---\n\n".join([
        f"ROLE: {j.get('title')} at {j.get('company')}\n{(j.get('full_description') or '')[:800]}"
        for j in jobs[:20]
    ])

    claude = _claude()
    prompt = f"""You are a senior talent advisor analyzing a PM candidate's profile against real market JD data.

CANDIDATE: Aakash Jain, Senior Technical Product Manager, 10 years
STRENGTHS: Platform PM (3 external platforms at Akamai CDN), 70% TAT reduction automation, 18% MRR increase, 20+ integrations, B2B SaaS/logistics
GAPS: No dedicated AI product shipped, no consumer product experience, no published case studies

RECENT JD CORPUS ({len(jobs)} roles):
{jd_corpus}

Analyse the gap between this candidate and the market. Return as JSON:
{{
  "quick_wins": [
    {{"action": "...", "why": "appears in X% of JDs", "score_impact": "+0.X avg", "time": "1-2 days"}}
  ],
  "roadmap_30d": [
    {{"action": "...", "why": "...", "score_impact": "...", "time": "..."}}
  ],
  "roadmap_90d": [
    {{"action": "...", "why": "...", "score_impact": "...", "time": "..."}}
  ],
  "keyword_gaps": ["phrase 1", "phrase 2"],
  "score_before": 6.2,
  "score_after_sim": 7.8,
  "jobs_unlocked": 8,
  "top_insight": "one sentence on the single biggest leverage point"
}}

Be specific to this candidate's actual experience. No generic advice."""

    message = claude.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    import re
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    try:
        analysis = json.loads(raw)
    except json.JSONDecodeError:
        analysis = {"raw_analysis": raw}

    # Save to Supabase
    db.table("resume_analyses").insert({
        "quick_wins":      analysis.get("quick_wins", []),
        "roadmap_30d":     analysis.get("roadmap_30d", []),
        "roadmap_90d":     analysis.get("roadmap_90d", []),
        "keyword_gaps":    analysis.get("keyword_gaps", []),
        "score_before":    analysis.get("score_before"),
        "score_after_sim": analysis.get("score_after_sim"),
        "jobs_unlocked":   analysis.get("jobs_unlocked", 0),
        "raw_analysis":    json.dumps(analysis),
    }).execute()

    return {"analysis": analysis}
