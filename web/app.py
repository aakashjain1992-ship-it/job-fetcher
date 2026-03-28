"""
Job Fetcher Dashboard — FastAPI backend.
Serves the 3-tab dashboard and provides JSON APIs for jobs, tracking, and training.

Run:  python main.py --serve
      (or directly: uvicorn web.app:app --reload --port 8000)
"""

import os
import json
from datetime import datetime, timezone, date, timedelta
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

try:
    from config.profile import CURRENT_CTC_LPA, CURRENT_STOCK_USD
except Exception:
    CURRENT_CTC_LPA   = 0.0
    CURRENT_STOCK_USD = 0.0
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
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()

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


# ── Skills Roadmap API ────────────────────────────────────────────────────────

@app.get("/api/resume/skill-roadmap")
def get_skill_roadmap():
    """
    Analyse jobs in DB to find which missing skills would unlock the most High-priority roles.
    Returns a ranked list: skill → jobs_currently_blocked → jobs_unlocked_if_acquired.
    """
    db = _supa()
    result = db.table("jobs").select(
        "job_id, key_matching_skills, red_flags, role_match_score, resume_match_pct, "
        "composite_score, apply_priority, visa_sponsor_detected, title, company"
    ).gte("role_match_score", 6).execute()

    jobs = result.data or []
    if not jobs:
        return {"skills": [], "message": "No jobs analysed yet — run the pipeline first."}

    # Candidate's known skills (from profile)
    KNOWN_SKILLS = {
        "product management", "platform pm", "b2b saas", "workflow automation",
        "stakeholder management", "agile", "safe", "scrum", "sql", "postgresql",
        "figma", "jira", "data analysis", "roadmap", "user research", "go-to-market",
        "operations", "logistics", "supply chain", "integrations", "api", "itsm",
        "enterprise software", "okr", "sprint planning", "product strategy",
        "cross-functional", "a/b testing", "metrics", "kpi",
    }

    # Count skill frequency across all JDs
    skill_freq: dict[str, int] = {}
    skill_to_jobs: dict[str, list[str]] = {}

    for job in jobs:
        skills = job.get("key_matching_skills") or []
        for skill in skills:
            s = skill.lower().strip()
            skill_freq[s] = skill_freq.get(s, 0) + 1
            skill_to_jobs.setdefault(s, []).append(job.get("job_id", ""))

    # Find skills that appear in JDs but are not in candidate's known set
    missing_skills = {s: cnt for s, cnt in skill_freq.items()
                      if not any(known in s or s in known for known in KNOWN_SKILLS)}

    # For each missing skill, estimate how many Medium jobs would become High
    # Heuristic: if adding this skill raises resume_match by ~10%, does composite cross 7.5?
    skill_impact = []
    for skill, freq in sorted(missing_skills.items(), key=lambda x: -x[1]):
        if freq < 2:  # ignore noise
            continue

        would_unlock = 0
        blocked_jobs = []
        for job in jobs:
            if skill not in [s.lower() for s in (job.get("key_matching_skills") or [])]:
                continue
            if job.get("apply_priority") == "High":
                continue  # already High
            # Simulate: if resume_match goes up by 10 pts, does composite cross 7.5?
            current_composite = float(job.get("composite_score") or 0)
            current_resume = float(job.get("resume_match_pct") or 0)
            if current_composite < 7.5 and current_composite >= 5:
                simulated_resume = min(100, current_resume + 10)
                # Re-score using actual job values + SCORING_WEIGHTS from profile config
                try:
                    from config.profile import SCORING_WEIGHTS as _sw
                except Exception:
                    _sw = {"role_match": 0.40, "visa": 0.20, "resume": 0.30, "red_flags": 0.10}
                role = float(job.get("role_match_score") or 0)
                visa_ok = 1.0 if job.get("visa_sponsor_detected") == "Yes" else (0.5 if job.get("visa_sponsor_detected") == "Unclear" else 0.0)
                no_red_flags = 0.0 if job.get("red_flags") else 1.0
                sim_composite = (
                    role / 10 * _sw["role_match"]
                    + visa_ok * _sw["visa"]
                    + simulated_resume / 100 * _sw["resume"]
                    + no_red_flags * _sw["red_flags"]
                ) * 10
                if sim_composite >= 7.5:
                    would_unlock += 1
                    blocked_jobs.append({
                        "job_id": job.get("job_id"),
                        "title": job.get("title"),
                        "company": job.get("company"),
                    })

        if freq >= 2:
            skill_impact.append({
                "skill": skill,
                "appears_in_jds": freq,
                "jobs_would_unlock": would_unlock,
                "sample_jobs": blocked_jobs[:3],
            })

    # Sort by jobs unlocked, then frequency
    skill_impact.sort(key=lambda x: (-x["jobs_would_unlock"], -x["appears_in_jds"]))

    return {"skills": skill_impact[:20], "total_jobs_analysed": len(jobs)}


# ── Case Studies API ──────────────────────────────────────────────────────────

class CaseStudyCreate(BaseModel):
    title: str
    company: Optional[str] = None
    role: Optional[str] = None
    tags: list = []
    problem: Optional[str] = None
    approach: Optional[str] = None
    outcome: Optional[str] = None
    metrics: Optional[str] = None
    status: str = "draft"
    target_roles: list = []


@app.get("/api/training/case-studies")
def list_case_studies():
    db = _supa()
    result = db.table("case_studies").select(
        "id, title, company, role, tags, metrics, status, target_roles, created_at, updated_at"
    ).order("updated_at", desc=True).execute()
    return {"case_studies": result.data or []}


@app.get("/api/training/case-studies/{cs_id}")
def get_case_study(cs_id: int):
    db = _supa()
    result = db.table("case_studies").select("*").eq("id", cs_id).single().execute()
    if not result.data:
        raise HTTPException(404, "Case study not found")
    return result.data


@app.post("/api/training/case-studies")
def create_case_study(body: CaseStudyCreate):
    db = _supa()
    result = db.table("case_studies").insert(body.model_dump()).execute()
    return result.data[0] if result.data else {"error": "Insert failed"}


@app.put("/api/training/case-studies/{cs_id}")
def update_case_study(cs_id: int, body: CaseStudyCreate):
    db = _supa()
    db.table("case_studies").update(body.model_dump()).eq("id", cs_id).execute()
    return {"ok": True}


@app.delete("/api/training/case-studies/{cs_id}")
def delete_case_study(cs_id: int):
    db = _supa()
    db.table("case_studies").delete().eq("id", cs_id).execute()
    return {"ok": True}


@app.post("/api/training/case-studies/generate")
def generate_case_study(body: dict):
    """
    Use Claude to generate a polished case study narrative from bullet-point inputs.
    Body: { title, company, role, problem, approach, outcome, metrics, target_role }
    """
    claude = _claude()

    prompt = f"""You are helping Aakash Jain write a portfolio case study for job applications.

INPUT (raw notes from Aakash):
Title: {body.get('title', '')}
Company: {body.get('company', '')}
Role: {body.get('role', '')}
Problem: {body.get('problem', '')}
Approach: {body.get('approach', '')}
Outcome: {body.get('outcome', '')}
Key metrics: {body.get('metrics', '')}
Target role type: {body.get('target_role', 'Senior PM / Platform PM')}

Write a polished PM portfolio case study with these sections:

## The Challenge
(2-3 sentences on the business problem and why it mattered)

## My Approach
(3-4 bullet points — what you did, structured as: Discover → Define → Deliver)

## Key Decisions
(2-3 specific decisions made and the trade-offs considered)

## Results
(Quantified outcomes, in bold for the numbers)

## What I'd Do Differently
(1-2 honest reflections — shows maturity)

Keep it concise (300-400 words total). Tone: confident, specific, no fluff.
Write in first person as Aakash."""

    message = claude.messages.create(
        model="claude-haiku-4-5",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    draft = message.content[0].text.strip()

    # Save to DB
    db = _supa()
    insert_data = {
        "title":      body.get("title", "Untitled"),
        "company":    body.get("company"),
        "role":       body.get("role"),
        "problem":    body.get("problem"),
        "approach":   body.get("approach"),
        "outcome":    body.get("outcome"),
        "metrics":    body.get("metrics"),
        "full_draft": draft,
        "status":     "draft",
        "tags":       body.get("tags", []),
        "target_roles": [body.get("target_role", "")] if body.get("target_role") else [],
    }
    result = db.table("case_studies").insert(insert_data).execute()
    saved_id = result.data[0]["id"] if result.data else None

    return {"draft": draft, "id": saved_id}


# ── Pipeline Control API ──────────────────────────────────────────────────────

import threading
import subprocess
import sys
from pathlib import Path

_pipeline_lock = threading.Lock()
_pipeline_state = {
    "running": False,
    "started_at": None,
    "pid": None,
}

PROJECT_ROOT = Path(__file__).parent.parent


@app.get("/api/pipeline/runs")
def get_pipeline_runs():
    """Return last 20 pipeline runs from fetch_runs table."""
    db = _supa()
    result = db.table("fetch_runs").select(
        "run_id, started_at, finished_at, jobs_fetched, jobs_new, jobs_enriched, status"
    ).order("finished_at", desc=True).limit(20).execute()
    return {
        "runs": result.data or [],
        "pipeline_running": _pipeline_state["running"],
        "pipeline_started_at": _pipeline_state["started_at"],
    }


@app.post("/api/pipeline/trigger")
def trigger_pipeline(body: dict = {}):
    """
    Trigger a full pipeline run in a background subprocess.
    Returns immediately — poll /api/pipeline/runs for status.
    """
    if _pipeline_state["running"]:
        return JSONResponse({"error": "Pipeline already running", "running": True}, 409)

    skip_career = body.get("skip_career_pages", False)
    skip_ratings = body.get("skip_ratings", False)

    def _run():
        _pipeline_state["running"] = True
        _pipeline_state["started_at"] = datetime.now(timezone.utc).isoformat()
        cmd = [sys.executable, str(PROJECT_ROOT / "main.py")]
        if skip_career:
            cmd.append("--skip-career-pages")
        if skip_ratings:
            cmd.append("--skip-ratings")
        log_path = PROJECT_ROOT / "data" / "pipeline_last_run.log"
        log_path.parent.mkdir(exist_ok=True)
        try:
            with open(log_path, "w") as log_f:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
                _pipeline_state["pid"] = proc.pid
                proc.wait()
        finally:
            _pipeline_state["running"] = False
            _pipeline_state["pid"] = None

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True, "message": "Pipeline started", "running": True}


# ── Resume Upload API ─────────────────────────────────────────────────────────

from fastapi import UploadFile, File
import zipfile
import io
import re as _re
import re

RESUME_PATH = PROJECT_ROOT / "data" / "uploaded_resume.txt"


@app.post("/api/resume/upload")
async def upload_resume(file: UploadFile = File(...)):
    """
    Accept a .docx resume upload, extract plain text, save to data/uploaded_resume.txt.
    Returns the extracted text preview.
    """
    filename = file.filename or ""
    content = await file.read()

    if filename.lower().endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                with z.open("word/document.xml") as xml_file:
                    xml_text = xml_file.read().decode("utf-8", errors="replace")
            # Strip XML tags
            text = _re.sub(r"<[^>]+>", " ", xml_text)
            text = _re.sub(r"\s+", " ", text).strip()
        except Exception as e:
            raise HTTPException(400, f"Could not parse DOCX: {e}")
    elif filename.lower().endswith(".txt"):
        text = content.decode("utf-8", errors="replace")
    else:
        raise HTTPException(400, "Only .docx and .txt files are supported")

    # Save locally
    RESUME_PATH.parent.mkdir(exist_ok=True)
    RESUME_PATH.write_text(text, encoding="utf-8")

    # Save to Supabase resume_analyses table as a context note
    try:
        db = _supa()
        db.table("resume_analyses").insert({
            "quick_wins": [],
            "roadmap_30d": [],
            "roadmap_90d": [],
            "keyword_gaps": [],
            "raw_analysis": json.dumps({"uploaded_resume": text[:3000]}),
        }).execute()
    except Exception:
        pass  # non-fatal

    word_count = len(text.split())
    return {
        "ok": True,
        "filename": filename,
        "word_count": word_count,
        "preview": text[:400] + ("…" if len(text) > 400 else ""),
    }


# ── Cover Letter & Resume Tweak ───────────────────────────────────────────────

from fastapi.responses import StreamingResponse
import io as _io


def _clean_for_pdf(text: str) -> str:
    """Replace Unicode chars that fpdf2's core fonts can't encode."""
    subs = {
        '\u2014': '--', '\u2013': '-', '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"', '\u2022': '*', '\u2026': '...',
        '\u00a0': ' ', '\u2010': '-', '\u2011': '-', '\u2012': '-',
    }
    for ch, rep in subs.items():
        text = text.replace(ch, rep)
    return text.encode('latin-1', errors='replace').decode('latin-1')


def _make_cover_letter_pdf(name: str, job_title: str, company: str, letter_text: str) -> bytes:
    from fpdf import FPDF
    import datetime as _dt

    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(25, 22, 25)
    pdf.set_auto_page_break(auto=True, margin=20)

    name = _clean_for_pdf(name)
    job_title = _clean_for_pdf(job_title)
    company = _clean_for_pdf(company)
    letter_text = _clean_for_pdf(letter_text)

    # Candidate name header
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 10, name, new_x="LMARGIN", new_y="NEXT")

    # Date
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 5, _dt.date.today().strftime("%B %d, %Y"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Accent rule
    pdf.set_draw_color(99, 102, 241)
    pdf.set_line_width(0.7)
    pdf.line(25, pdf.get_y(), 185, pdf.get_y())
    pdf.ln(7)

    # Job reference line
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 7, f"Re: {job_title} at {company}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Body — split on double newline into paragraphs
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(51, 65, 85)
    for para in letter_text.split('\n\n'):
        para = para.strip().replace('\n', ' ')
        if para:
            pdf.multi_cell(0, 6.5, para)
            pdf.ln(4)

    # Sign-off
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, "Sincerely,", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6, name, new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


@app.post("/api/jobs/{job_id}/cover-letter")
def generate_cover_letter(job_id: str):
    """Generate a tailored cover letter for a job. Returns letter text as JSON."""
    db = _supa()
    result = db.table("jobs").select("*").eq("job_id", job_id).execute()
    if not result.data:
        raise HTTPException(404, "Job not found")
    job = result.data[0]

    try:
        from config.profile import CANDIDATE_PROFILE
    except Exception:
        CANDIDATE_PROFILE = {}

    claude = _claude()
    name          = CANDIDATE_PROFILE.get("name", "Candidate")
    curr_title    = CANDIDATE_PROFILE.get("current_title", "Senior Product Manager")
    curr_co       = CANDIDATE_PROFILE.get("current_company", "")
    years         = CANDIDATE_PROFILE.get("total_experience_years", 8)
    strengths     = ", ".join(CANDIDATE_PROFILE.get("strengths", [])[:4])
    metrics       = "; ".join(CANDIDATE_PROFILE.get("key_metrics", [])[:3])
    visa_required = CANDIDATE_PROFILE.get("visa_required", False)

    visa_line = ""
    if visa_required and job.get("visa_sponsor_detected") == "Yes":
        visa_line = "Note: candidate requires visa sponsorship — this role provides it, mention it confidently."

    prompt = f"""Write a concise, compelling cover letter. Exactly 3 short paragraphs.

CANDIDATE: {name} | {curr_title} at {curr_co} | {years} yrs experience
STRENGTHS: {strengths}
METRICS: {metrics}
{visa_line}

JOB: {job["title"]} at {job["company"]} ({job.get("location") or job.get("region","")})
MATCHING SKILLS FROM JD: {", ".join(job.get("key_matching_skills") or [])}
AI-NOTED GAP: {job.get("gap_to_close") or "None"}
JD EXCERPT (first 1200 chars): {(job.get("full_description") or "")[:1200]}

RULES:
- Para 1: Sharp hook — specific reason why this company/role (not generic). Reference one real thing about the company.
- Para 2: 2 concrete achievements with numbers that map directly to JD requirements.
- Para 3: Brief close — genuine 1-line fit statement + call to action.
- NEVER start with "I am writing to apply" or "I am passionate about"
- NO placeholder brackets like [Name] — use actual name: {name}
- Under 270 words. Plain text, no markdown, no headings.

Write the letter body only (no "Dear...", no "Sincerely" — those are added automatically):"""

    msg = claude.messages.create(
        model="claude-haiku-4-5",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    letter = msg.content[0].text.strip()

    return {
        "letter": letter,
        "job_title": job["title"],
        "company":   job["company"],
        "candidate_name": name,
    }


@app.post("/api/jobs/{job_id}/cover-letter/pdf")
def download_cover_letter_pdf(job_id: str, body: dict = {}):
    """Accept letter text, return a formatted PDF download."""
    letter    = body.get("letter", "")
    job_title = body.get("job_title", "Position")
    company   = body.get("company", "Company")
    c_name    = body.get("candidate_name", "Candidate")
    if not letter:
        raise HTTPException(400, "letter text required")
    pdf_bytes = _make_cover_letter_pdf(c_name, job_title, company, letter)
    fname = f"Cover_Letter_{company.replace(' ','_')}_{job_id[:8]}.pdf"
    return StreamingResponse(
        _io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/jobs/{job_id}/tweak-resume")
def tweak_resume_for_job(job_id: str):
    """Suggest minimal ATS-friendly word-level tweaks to the uploaded resume."""
    db = _supa()
    result = db.table("jobs").select(
        "job_id, title, company, full_description, key_matching_skills, resume_match_pct"
    ).eq("job_id", job_id).execute()
    if not result.data:
        raise HTTPException(404, "Job not found")
    job = result.data[0]

    resume_path = PROJECT_ROOT / "data" / "uploaded_resume.txt"
    if not resume_path.exists():
        raise HTTPException(
            400,
            "No resume uploaded yet. Upload your resume in the System tab first."
        )
    resume_text = resume_path.read_text(encoding="utf-8", errors="ignore")[:3000]

    claude = _claude()
    known_skills    = ", ".join(job.get("key_matching_skills") or [])
    resume_match_pct = int(job.get("resume_match_pct") or 0)

    prompt = f"""You are an ATS optimization expert. Suggest MINIMAL, precise word-level tweaks to pass ATS screening for this job.

STRICT RULES:
- Max 6 tweaks total
- Each tweak is a word substitution or 1-4 word addition to an EXISTING bullet/line — NOT a rewrite
- Only suggest if the candidate genuinely has this skill (it's implied by their resume context)
- Focus on keywords present in the JD but absent from the resume
- Do NOT invent experience. Do NOT change meaning.

JOB: {job["title"]} at {job["company"]}
ALREADY MATCHING (skip these): {known_skills}
JD (key requirements): {(job.get("full_description") or "")[:1400]}

CANDIDATE RESUME:
{resume_text}

The candidate's current ATS match for this job is already scored at {resume_match_pct}% (from prior analysis).
Focus only on suggesting targeted tweaks and estimating how much the score improves after applying them.

Return ONLY this JSON (no markdown, no commentary):
{{
  "missing_keywords": ["up to 8 important JD keywords not in resume"],
  "tweaks": [
    {{
      "section": "Experience | Skills | Summary",
      "original": "exact phrase from resume (keep it short, 8-15 words)",
      "suggested": "same phrase with 1-4 ATS keywords woven in naturally",
      "keyword_added": "the key ATS term added",
      "reason": "one sentence: why this keyword matters for this specific role"
    }}
  ],
  "ats_score_after": <integer 0-100 estimated after applying these tweaks>
}}"""

    msg = claude.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(500, "Could not parse Claude's suggestions — try again")

    # ats_score_before is always the stored enrichment score — consistent, not re-estimated
    ats_before = int(job.get("resume_match_pct") or 0)
    ats_after  = int(data.get("ats_score_after") or min(100, ats_before + 10))

    return {
        "job_title":        job["title"],
        "company":          job["company"],
        "missing_keywords": data.get("missing_keywords", []),
        "tweaks":           data.get("tweaks", []),
        "ats_score_before": ats_before,
        "ats_score_after":  ats_after,
    }


@app.get("/api/resume/uploaded")
def get_uploaded_resume():
    """Check if a resume has been uploaded and return its preview."""
    if not RESUME_PATH.exists():
        return {"uploaded": False}
    text = RESUME_PATH.read_text(encoding="utf-8")
    return {
        "uploaded": True,
        "word_count": len(text.split()),
        "preview": text[:400] + ("…" if len(text) > 400 else ""),
        "modified": RESUME_PATH.stat().st_mtime,
    }
