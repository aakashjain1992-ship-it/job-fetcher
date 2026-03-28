"""
JobSpy unified fetcher — fetches from LinkedIn, Indeed, Google.
Free, no API keys required. Search config loaded from profile.yaml.
"""

import hashlib
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any
import time

try:
    from jobspy import scrape_jobs
except ImportError:
    raise ImportError("Run: pip install python-jobspy")

from config.profile import SEARCHES, MAX_EXPERIENCE_YEARS

SENIORITY_KW = ["senior", "principal", "group", "director", "head of", "lead", "staff", "sr."]
PM_KW = ["product manager", "product owner", "director of product", "head of product"]


def _exceeds_experience(description: str) -> bool:
    text = description.lower()
    for pat in [r"(\d+)\+?\s*years?\s*of\s*(?:relevant\s*)?experience",
                r"minimum\s*(\d+)\s*years?",
                r"at\s*least\s*(\d+)\s*years?"]:
        for m in re.findall(pat, text):
            try:
                if int(m) >= MAX_EXPERIENCE_YEARS:
                    return True
            except ValueError:
                pass
    return False


def _is_valid(title):
    if not title:
        return False
    t = title.lower()
    if not any(kw in t for kw in PM_KW):
        return False
    return any(kw in t for kw in SENIORITY_KW)


def _make_id(site, raw_id):
    prefix = {"linkedin": "LKD", "indeed": "IND", "glassdoor": "GSD", "google": "GGL"}.get(site, "JSP")
    return f"{prefix}-{raw_id[:10].upper()}"


def _seniority(title, job_level):
    if job_level and str(job_level) not in ("nan", "None", ""):
        return str(job_level)
    t = title.lower()
    if any(x in t for x in ["director", "head of", "vp"]):
        return "Director / VP"
    if any(x in t for x in ["principal", "staff", "group"]):
        return "Principal PM"
    if any(x in t for x in ["senior", "sr.", "sr "]):
        return "Senior PM"
    return "PM"


def _remote_type(is_remote, wfh, title, desc):
    w = str(wfh or "").lower()
    if "remote" in w or is_remote is True:
        return "Remote"
    if "hybrid" in w:
        return "Hybrid"
    text = (title + " " + desc).lower()
    if "fully remote" in text or "100% remote" in text:
        return "Remote"
    if "remote" in text:
        return "Remote-friendly"
    if "hybrid" in text:
        return "Hybrid"
    return "Onsite"


def _salary_text(min_amt, max_amt, currency, interval):
    try:
        cur = str(currency or "").strip()
        intv = str(interval or "").strip()
        if min_amt and str(min_amt) not in ("nan", "None"):
            if max_amt and str(max_amt) not in ("nan", "None"):
                return f"{cur}{int(float(min_amt)):,}-{cur}{int(float(max_amt)):,}/{intv}"
            return f"{cur}{int(float(min_amt)):,}+/{intv}"
    except (ValueError, TypeError):
        pass
    return ""


def _safe(val, default=""):
    if val is None:
        return default
    s = str(val)
    return default if s in ("nan", "None", "NaT") else s.strip()


MAX_RETRIES = 2
RETRY_DELAY = 10  # seconds


def _fetch_region(search_term, location, country, region, is_remote, hours_old=168):
    df = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = scrape_jobs(
                site_name=["linkedin", "indeed", "google"],
                search_term=search_term,
                location=location,
                country_indeed=country,
                results_wanted=20,
                hours_old=hours_old,
                is_remote=is_remote,
                linkedin_fetch_description=True,
                verbose=0,
            )
            break  # success
        except Exception as e:
            if attempt < MAX_RETRIES:
                print(f"    JobSpy error (attempt {attempt}/{MAX_RETRIES}): {e}")
                print(f"    Retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"    JobSpy error (attempt {attempt}/{MAX_RETRIES}): {e}")
                return []

    if df is None or df.empty:
        return []

    today = datetime.utcnow().strftime("%Y-%m-%d")
    expiry = (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d")
    jobs = []

    for _, row in df.iterrows():
        title = _safe(row.get("title"))
        if not _is_valid(title):
            continue
        desc_check = _safe(row.get("description"))
        if _exceeds_experience(desc_check):
            continue
        company = _safe(row.get("company"))
        site = _safe(row.get("site"))
        raw_id = _safe(row.get("id"), default=hashlib.md5(title.encode()).hexdigest()[:10])
        desc = _safe(row.get("description"))
        posted = _safe(row.get("date_posted"), default=today)[:10]
        jobs.append({
            "job_id": _make_id(site, raw_id),
            "title": title,
            "company": company,
            "location": _safe(row.get("location"), default=location),
            "region": region,
            "seniority": _seniority(title, row.get("job_level")),
            "posted_date": posted,
            "fetched_date": today,
            "expiry_date": expiry,
            "source": f"JobSpy/{site.title()}",
            "url": _safe(row.get("job_url")),
            "url_direct": _safe(row.get("job_url_direct")),
            "snippet": desc[:500],
            "salary_text": _salary_text(row.get("min_amount"), row.get("max_amount"), row.get("currency"), row.get("interval")),
            "remote_type": _remote_type(row.get("is_remote"), row.get("work_from_home_type"), title, desc),
            "full_description": desc,
        })
    return jobs


def fetch_all(hours_old: int = 168):
    all_jobs = []
    seen_ids = set()

    for search in SEARCHES:
        search_term = search["query"]
        location = search["location"]
        country = search["country"]
        region = search["region"]
        is_remote = search.get("remote", False)

        label = search_term[:50]
        print(f"  JobSpy: '{label}' in {location}")
        jobs = _fetch_region(search_term, location, country, region, is_remote, hours_old=hours_old)
        new = 0
        for job in jobs:
            if job["job_id"] not in seen_ids:
                all_jobs.append(job)
                seen_ids.add(job["job_id"])
                new += 1
        print(f"    -> {new} valid PM jobs")
        time.sleep(3)

    print(f"\n  JobSpy total: {len(all_jobs)} unique senior PM jobs")
    return all_jobs
