"""
Career page scrapers — loads company list from profile.yaml.
Uses Greenhouse JSON API (reliable) or HTML scraping as fallback.
"""

import re
import hashlib
import requests
import time
from datetime import datetime
from typing import List, Dict, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config.profile import CAREER_PAGES

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

SENIORITY_KW = ["senior", "principal", "group", "director", "head of", "lead", "staff", "sr."]
PM_KW = ["product manager", "product owner", "director of product", "head of product", "vp of product"]
CUTOFF_DAYS = 30

INDIA_KW = ["bangalore", "bengaluru", "hyderabad", "gurgaon", "gurugram",
            "pune", "mumbai", "delhi", "india", "chennai", "noida"]


def _is_valid(title: str) -> bool:
    if not title:
        return False
    t = title.lower()
    if not any(kw in t for kw in PM_KW):
        return False
    return any(kw in t for kw in SENIORITY_KW)


def _is_recent(date_str: str) -> bool:
    if not date_str:
        return True
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
        try:
            d = datetime.strptime(date_str[:19], fmt)
            return (datetime.utcnow() - d).days <= CUTOFF_DAYS
        except ValueError:
            continue
    return True


def _make_id(url: str, title: str, company: str) -> str:
    return "CRP-" + hashlib.sha256(f"{url}|{title}|{company}".encode()).hexdigest()[:10].upper()


def _classify(title: str) -> str:
    t = title.lower()
    if any(x in t for x in ["director", "head of", "vp"]):
        return "Director / VP"
    if any(x in t for x in ["principal", "staff", "group"]):
        return "Principal PM"
    if any(x in t for x in ["senior", "sr."]):
        return "Senior PM"
    return "PM"


def _remote(text: str) -> str:
    t = text.lower()
    if any(x in t for x in ["fully remote", "100% remote", "remote-first"]):
        return "Remote"
    if "remote" in t:
        return "Remote-friendly"
    if "hybrid" in t:
        return "Hybrid"
    return "Onsite"


def _detect_region(loc: str, default: str) -> str:
    l = loc.lower()
    if any(k in l for k in INDIA_KW):
        return "India"
    if any(k in l for k in ["dubai", "uae", "emirates", "abu dhabi"]):
        return "Dubai"
    if "singapore" in l:
        return "Singapore"
    if any(k in l for k in ["germany", "berlin", "amsterdam", "netherlands",
                              "london", "uk", "paris", "europe"]):
        return "EU"
    if any(k in l for k in ["remote", "usa", "united states", "new york",
                              "san francisco", "seattle", "austin"]):
        return "USA"
    return default


def _std(title, co, url, loc, region, desc="", posted=""):
    if not posted:
        posted = datetime.utcnow().strftime("%Y-%m-%d")
    return {
        "job_id": _make_id(url, title, co),
        "title": title, "company": co,
        "location": loc, "region": region,
        "seniority": _classify(title),
        "posted_date": posted[:10],
        "fetched_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "expiry_date": "",
        "source": f"CareerPage/{co}",
        "url": url, "url_direct": url,
        "snippet": desc[:500], "full_description": desc,
        "salary_text": "", "remote_type": _remote(title + " " + desc),
    }


def _greenhouse(board_token: str, company: str, default_region: str) -> List[Dict]:
    """Fetch jobs via Greenhouse JSON API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    {company}: {e}")
        return []

    jobs = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        if not _is_valid(title):
            continue
        posted = job.get("updated_at", "")
        if not _is_recent(posted):
            continue
        job_url = job.get("absolute_url", "")
        loc = job.get("location", {}).get("name", "") or default_region
        region = _detect_region(loc, default_region)
        desc = BeautifulSoup(job.get("content", ""), "html.parser").get_text(" ", strip=True)
        jobs.append(_std(title, company, job_url, loc, region, desc, posted))
    return jobs


def _scrape_html(url: str, company: str, region: str) -> List[Dict]:
    """Generic HTML scraper fallback."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    {company}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    links = soup.find_all("a", href=re.compile(
        r"/jobs/|/careers/|gh_jid|greenhouse\.io|lever\.co|smartrecruiters",
        re.I
    ))
    for link in links[:50]:
        title = link.get_text(strip=True)
        if not title or not _is_valid(title):
            continue
        href = link.get("href", "")
        full_url = urljoin(url, href)
        jobs.append(_std(title, company, full_url, region, region))
    return jobs


def fetch_all() -> List[Dict[str, Any]]:
    all_jobs = []
    seen_ids = set()

    if not CAREER_PAGES:
        print("  No career pages configured in profile.yaml")
        return []

    for page in CAREER_PAGES:
        name = page.get("company", "Unknown")
        page_type = page.get("type", "html")
        default_region = page.get("default_region", "USA")

        print(f"  Scraping {name}...")
        try:
            if page_type == "greenhouse":
                board_token = page.get("board_token", "")
                if not board_token:
                    print(f"    {name}: missing board_token in config")
                    continue
                jobs = _greenhouse(board_token, name, default_region)
            else:
                scrape_url = page.get("url", "")
                if not scrape_url:
                    print(f"    {name}: missing url in config")
                    continue
                jobs = _scrape_html(scrape_url, name, default_region)

            new = 0
            for job in jobs:
                if job["job_id"] not in seen_ids:
                    all_jobs.append(job)
                    seen_ids.add(job["job_id"])
                    new += 1
            if new:
                print(f"    -> {new} valid PM jobs")
        except Exception as e:
            print(f"    {name} error: {e}")
        time.sleep(1)

    print(f"\n  Career pages total: {len(all_jobs)} unique jobs")
    return all_jobs
