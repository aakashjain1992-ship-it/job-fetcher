"""
Weekly email digest — sends top 10 jobs every Monday 8am IST.
Uses Resend API for clean HTML emails.
"""

import os
import json
from datetime import datetime
from typing import List, Dict, Any

import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
TO_EMAIL       = os.environ.get("DIGEST_EMAIL", "")
FROM_EMAIL     = os.environ.get("DIGEST_FROM_EMAIL", "onboarding@resend.dev")
SPREADSHEET_ID = os.environ.get("GOOGLE_SPREADSHEET_ID", "")
RESEND_URL     = "https://api.resend.com/emails"


def _priority_color(priority: str) -> str:
    return {"High": "#1D9E75", "Medium": "#BA7517", "Low": "#888780"}.get(priority, "#888780")


def _visa_badge(visa: str) -> str:
    colors = {"Yes": ("#EAF3DE", "#3B6D11"), "No": ("#FCEBEB", "#A32D2D"), "Unclear": ("#FAEEDA", "#BA7517")}
    bg, fg = colors.get(visa, ("#F1EFE8", "#888780"))
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{visa}</span>'


def _region_badge(region: str) -> str:
    colors = {
        "Dubai": ("#FAEEDA", "#633806"),
        "Singapore": ("#E6F1FB", "#0C447C"),
        "EU": ("#EEEDFE", "#3C3489"),
        "USA": ("#EAF3DE", "#27500A"),
    }
    bg, fg = colors.get(region, ("#F1EFE8", "#444441"))
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{region}</span>'


def _job_card_html(job: Dict, rank: int) -> str:
    title    = job.get("title", "")
    company  = job.get("company", "")
    region   = job.get("region", "")
    score    = job.get("composite_score", "–")
    match    = job.get("resume_match_pct", "–")
    visa     = job.get("visa_sponsor_detected", "Unclear")
    remote   = job.get("remote_type", "")
    salary   = job.get("salary_range", "") or job.get("salary_text", "")
    inr      = job.get("inr_equivalent_lpa", "")
    skills   = ", ".join(job.get("key_matching_skills", []))
    gap      = job.get("gap_to_close", "")
    notes    = job.get("notes", "")
    url      = job.get("url", "#")
    url_direct = job.get("url_direct", "")
    direct_link = f'<a href="{url_direct}" style="display:inline-block;margin-left:8px;background:transparent;color:#1D9E75;text-decoration:none;padding:8px 18px;border-radius:6px;font-size:13px;font-weight:600;border:1px solid #1D9E75">Company Page →</a>' if url_direct else ""
    priority = job.get("apply_priority", "Medium")
    pc       = _priority_color(priority)

    inr_str = f"≈ ₹{inr}L/yr" if inr else ""
    salary_str = f"{salary}  {inr_str}".strip() if salary or inr_str else "Not disclosed"

    return f"""
<div style="background:#ffffff;border:1px solid #E8E7E2;border-radius:10px;padding:18px 20px;margin-bottom:14px">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:10px">
    <div>
      <span style="font-size:11px;color:#888780;font-weight:600">#{rank}</span>
      <span style="font-size:11px;color:{pc};font-weight:700;margin-left:6px;background:{pc}18;padding:2px 8px;border-radius:4px">{priority.upper()}</span>
    </div>
    <div style="display:flex;gap:6px">
      {_region_badge(region)}
      {_visa_badge(visa)}
    </div>
  </div>
  <div style="font-size:17px;font-weight:700;color:#1A1A18;margin-bottom:3px">{title}</div>
  <div style="font-size:13px;color:#6B6B68;margin-bottom:12px">{company}  ·  {remote}</div>

  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:12px">
    <div style="background:#F8F7F4;border-radius:6px;padding:8px 10px;text-align:center">
      <div style="font-size:20px;font-weight:700;color:#1A1A18">{score}</div>
      <div style="font-size:10px;color:#888780;text-transform:uppercase;letter-spacing:.05em">Match Score</div>
    </div>
    <div style="background:#F8F7F4;border-radius:6px;padding:8px 10px;text-align:center">
      <div style="font-size:20px;font-weight:700;color:#1A1A18">{match}%</div>
      <div style="font-size:10px;color:#888780;text-transform:uppercase;letter-spacing:.05em">Resume Fit</div>
    </div>
    <div style="background:#F8F7F4;border-radius:6px;padding:8px 10px;text-align:center">
      <div style="font-size:13px;font-weight:600;color:#1A1A18">{salary_str}</div>
      <div style="font-size:10px;color:#888780;text-transform:uppercase;letter-spacing:.05em">Comp Range</div>
    </div>
  </div>

  {"<div style='font-size:12px;color:#444441;margin-bottom:8px'><strong>Matching skills:</strong> " + skills + "</div>" if skills else ""}
  {"<div style='font-size:12px;color:#1D9E75;margin-bottom:8px;background:#E1F5EE;padding:6px 10px;border-radius:6px'><strong>Apply after:</strong> " + gap + "</div>" if gap else ""}
  {"<div style='font-size:12px;color:#6B6B68;font-style:italic;margin-bottom:12px'>" + notes + "</div>" if notes else ""}

  <a href="{url}" style="display:inline-block;background:#1A1A18;color:#ffffff;text-decoration:none;padding:8px 18px;border-radius:6px;font-size:13px;font-weight:600">View Job →</a>
  {direct_link}
</div>
"""


def build_digest_html(jobs: List[Dict], run_stats: Dict) -> str:
    today = datetime.utcnow().strftime("%B %d, %Y")
    total = run_stats.get("total_fetched", 0)
    new_jobs = run_stats.get("new_this_run", 0)
    high_count = sum(1 for j in jobs if j.get("apply_priority") == "High")

    job_cards = "".join(_job_card_html(j, i+1) for i, j in enumerate(jobs[:10]))

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F8F7F4;font-family:'Helvetica Neue',Arial,sans-serif">
<div style="max-width:640px;margin:0 auto;padding:24px 16px">

  <!-- Header -->
  <div style="background:#1A1A18;border-radius:12px;padding:24px 28px;margin-bottom:20px">
    <div style="font-size:12px;color:#9FE1CB;font-weight:600;letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px">Career Intelligence System</div>
    <div style="font-size:24px;font-weight:700;color:#ffffff;margin-bottom:4px">Weekly Job Digest</div>
    <div style="font-size:13px;color:rgba(255,255,255,0.5)">{today}  ·  Top {min(len(jobs),10)} matches this week</div>
  </div>

  <!-- Stats -->
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px">
    <div style="background:#fff;border:1px solid #E8E7E2;border-radius:8px;padding:14px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#1A1A18">{new_jobs}</div>
      <div style="font-size:11px;color:#888780;text-transform:uppercase;letter-spacing:.04em">New this run</div>
    </div>
    <div style="background:#fff;border:1px solid #E8E7E2;border-radius:8px;padding:14px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#1D9E75">{high_count}</div>
      <div style="font-size:11px;color:#888780;text-transform:uppercase;letter-spacing:.04em">High priority</div>
    </div>
    <div style="background:#fff;border:1px solid #E8E7E2;border-radius:8px;padding:14px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#1A1A18">{total}</div>
      <div style="font-size:11px;color:#888780;text-transform:uppercase;letter-spacing:.04em">Total in sheet</div>
    </div>
  </div>

  <!-- Reminder -->
  <div style="background:#E1F5EE;border-left:3px solid #1D9E75;border-radius:0 8px 8px 0;padding:10px 16px;margin-bottom:20px;font-size:13px;color:#085041">
    <strong>Remember:</strong> Apply within 5 days of posting. Quote market rate for the role — never anchor to your current CTC.
  </div>

  <!-- Job Cards -->
  <div style="font-size:12px;font-weight:600;color:#888780;text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px">Top Matches</div>
  {job_cards}

  <!-- Footer -->
  <div style="text-align:center;padding:24px 0;font-size:11px;color:#B4B2A9">
    Job Intelligence Pipeline · Auto-generated every Saturday
  </div>
</div>
</body>
</html>"""


def send_digest(jobs: List[Dict], run_stats: Dict) -> bool:
    if not RESEND_API_KEY or not TO_EMAIL:
        print("Warning: RESEND_API_KEY not set — saving digest to digest_preview.html instead")
        html = build_digest_html(jobs, run_stats)
        with open("digest_preview.html", "w") as f:
            f.write(html)
        print("Digest saved to digest_preview.html")
        return True

    top_jobs = sorted(jobs, key=lambda x: float(x.get("composite_score", 0) or 0), reverse=True)[:10]
    html = build_digest_html(top_jobs, run_stats)

    today = datetime.utcnow().strftime("%b %d")
    high_count = sum(1 for j in top_jobs if j.get("apply_priority") == "High")

    payload = {
        "from": FROM_EMAIL,
        "to": [TO_EMAIL],
        "subject": f"Job Digest {today} — {high_count} High Priority Matches",
        "html": html,
    }

    try:
        resp = requests.post(
            RESEND_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        print(f"Email digest sent to {TO_EMAIL}")
        return True
    except Exception as e:
        print(f"Email send error: {e}")
        return False


def should_send_digest() -> bool:
    """
    Send on Saturdays — pipeline runs weekly on Saturday so the digest fires on the same run.
    """
    return datetime.utcnow().weekday() == 5  # 5 = Saturday
