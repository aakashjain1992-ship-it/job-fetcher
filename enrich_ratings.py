"""
Company Ratings Enricher
Reads company names from Enriched Jobs tab,
fetches Glassdoor rating + employee count via SerpAPI or direct scrape,
writes back to the Glassdoor Rating and Company Size columns.

Run separately: python3 enrich_ratings.py
"""

import os, sys, time, json, re, requests
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from storage.sheets_writer import _get_sheet, _rl, TAB_ENRICHED

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Cache so we don't re-fetch the same company twice per run
_cache: dict = {}


# ── Column positions (0-indexed) ─────────────────────────────────────────────
COL_COMPANY        = 2   # C — Company name
COL_GLASSDOOR      = 17  # R — Glassdoor Rating
COL_COMPANY_SIZE   = 18  # S — Company Size


def _extract_size(text: str) -> str:
    """Extract employee count from snippet text."""
    import re
    patterns = [
        r"([\d,]+)\s*employees?\s*(?:globally|worldwide|total)?",
        r"number of employees[^\d]*([\d,]+)",
        r"employs?\s*([\d,]+)",
        r"workforce of\s*([\d,]+)",
        r"team of\s*([\d,]+)",
        r"([\d,]+)\+?\s*people",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            num = int(m.group(1).replace(",", ""))
            # Format into readable bands
            if num >= 100000: return "100,000+"
            if num >= 50000:  return "50,000-100,000"
            if num >= 10000:  return "10,000-50,000"
            if num >= 5000:   return "5,000-10,000"
            if num >= 1000:   return "1,000-5,000"
            if num >= 500:    return "500-1,000"
            if num >= 200:    return "200-500"
            if num >= 50:     return "50-200"
            return f"{num} employees"
    return ""


def fetch_via_serpapi(company: str) -> dict:
    """Use SerpAPI Google search to find Glassdoor rating + company size."""
    if not SERPAPI_KEY:
        return {}
    try:
        # Search 1 — Glassdoor rating
        resp1 = requests.get("https://serpapi.com/search", params={
            "engine": "google",
            "q": f"{company} Glassdoor rating reviews",
            "api_key": SERPAPI_KEY,
            "num": 3,
        }, timeout=15)
        resp1.raise_for_status()
        data1 = resp1.json()

        rating = ""
        all_text1 = " ".join([
            r.get("snippet", "") + " " + r.get("title", "")
            for r in data1.get("organic_results", [])[:5]
        ])
        m = re.search(r"(\d\.\d)\s*(?:out of 5|stars?|/5|★)", all_text1, re.I)
        if m:
            rating = m.group(1)

        time.sleep(0.5)

        # Search 2 — employee count
        resp2 = requests.get("https://serpapi.com/search", params={
            "engine": "google",
            "q": f"{company} number of employees {datetime.utcnow().year}",
            "api_key": SERPAPI_KEY,
            "num": 3,
        }, timeout=15)
        resp2.raise_for_status()
        data2 = resp2.json()

        all_text2 = " ".join([
            r.get("snippet", "") + " " + r.get("title", "")
            for r in data2.get("organic_results", [])[:5]
        ])
        size = _extract_size(all_text2)

        return {
            "rating": rating.strip() if rating else "",
            "size": size.strip() if size else "",
        }
    except Exception as e:
        print(f"    SerpAPI error for {company}: {e}")
        return {}


def fetch_via_glassdoor_search(company: str) -> dict:
    """Scrape Glassdoor search results for company rating."""
    try:
        query = company.replace(" ", "+")
        resp = requests.get(
            f"https://www.glassdoor.com/Search/results.htm?keyword={query}",
            headers=HEADERS,
            timeout=15,
        )
        text = resp.text

        # Extract rating from page
        rating_match = re.search(r'"overallRating":\s*"?(\d\.\d)"?', text)
        size_match   = re.search(r'"sizeCategory":\s*"([^"]+)"', text)
        employees_match = re.search(r'"numberOfEmployees":\s*"?([^",}]+)"?', text)

        rating = rating_match.group(1) if rating_match else ""
        size   = employees_match.group(1) if employees_match else (
                 size_match.group(1) if size_match else "")

        return {"rating": rating, "size": size}
    except Exception:
        return {}


def fetch_via_google_scrape(company: str) -> dict:
    """Fallback: scrape Google search for Glassdoor rating in snippet."""
    try:
        resp = requests.get(
            "https://www.google.com/search",
            params={"q": f"{company} glassdoor rating site:glassdoor.com"},
            headers=HEADERS,
            timeout=15,
        )
        text = resp.text
        m = re.search(r"(\d\.\d)\s*(?:★|stars?|out of 5|/5\.0)", text, re.I)
        if m:
            return {"rating": m.group(1), "size": ""}
        # Try JSON-LD structured data
        m2 = re.search(r'"ratingValue":\s*"?(\d\.\d)"?', text)
        if m2:
            return {"rating": m2.group(1), "size": ""}
    except Exception:
        pass
    return {}


def fetch_size_from_wikipedia(company: str) -> str:
    """Fetch employee count from Wikipedia infobox — free, no rate limits."""
    try:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "titles": company,
                "prop": "revisions",
                "rvprop": "content",
                "format": "json",
                "redirects": 1,
            },
            headers=HEADERS,
            timeout=10,
        )
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            content_text = page.get("revisions", [{}])[0].get("*", "")
            # Look for employees in infobox
            m = re.search(
                r"employees\s*=\s*(?:{{.*?}}\s*)?([0-9,]+(?:\.[0-9]+)?(?:\s*[kmb]illion)?(?:\s*\+)?(?:\s*\([^)]+\))?)",
                content_text, re.I
            )
            if m:
                raw = m.group(1).strip().split("(")[0].strip()
                return _extract_size(raw + " employees") or raw
    except Exception:
        pass
    return ""


def get_company_data(company: str) -> dict:
    """Try multiple sources, return best result."""
    if not company or company.strip() == "":
        return {"rating": "", "size": ""}

    company = company.strip()
    if company in _cache:
        return _cache[company]

    print(f"  Fetching: {company}...")

    result = {}

    # Get company size from Wikipedia — free, no quota, no rate limits
    wiki_size = fetch_size_from_wikipedia(company)
    if wiki_size:
        result["size"] = wiki_size

    # SerpAPI for Glassdoor rating (uses monthly quota)
    if SERPAPI_KEY and not result.get("rating"):
        try:
            serp_result = fetch_via_serpapi(company)
            if serp_result.get("rating"):
                result["rating"] = serp_result["rating"]
            if serp_result.get("size") and not result.get("size"):
                result["size"] = serp_result["size"]
        except SystemExit:
            raise
        except Exception:
            pass

    if result.get("rating"):
        print(f"    → Rating: {result['rating']} | Size: {result.get('size', 'N/A')}")
    else:
        print(f"    → Not found")

    _cache[company] = result
    return result


def run(spreadsheet_id: str):
    print("\n" + "="*50)
    print("Company Ratings Enricher")
    print("="*50 + "\n")

    sh = _get_sheet(spreadsheet_id)
    ws = sh.worksheet(TAB_ENRICHED)

    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        print("No data in Enriched Jobs tab")
        return

    header = all_rows[0]
    data_rows = all_rows[1:]

    # Column positions for role match and resume match
    try:
        COL_ROLE_MATCH   = header.index("Role Match (0-10)")
        COL_RESUME_MATCH = header.index("Resume Match %")
    except ValueError:
        COL_ROLE_MATCH, COL_RESUME_MATCH = 4, 9  # fallback defaults

    # Find rows needing rating — only high-fit jobs, deduplicated by company
    seen_companies = set()
    to_update = []
    skipped_low = 0
    skipped_done = 0

    for i, row in enumerate(data_rows, start=2):
        if len(row) <= COL_COMPANY:
            continue
        company = row[COL_COMPANY].strip() if len(row) > COL_COMPANY else ""
        if not company:
            continue

        # Filter: only role match >= 8 AND resume match >= 70%
        try:
            role_match   = float(row[COL_ROLE_MATCH]) if len(row) > COL_ROLE_MATCH and row[COL_ROLE_MATCH] else 0
            resume_match = float(row[COL_RESUME_MATCH]) if len(row) > COL_RESUME_MATCH and row[COL_RESUME_MATCH] else 0
        except (ValueError, TypeError):
            role_match, resume_match = 0, 0

        if role_match < 8 or resume_match < 70:
            skipped_low += 1
            continue

        current_rating = row[COL_GLASSDOOR].strip() if len(row) > COL_GLASSDOOR else ""
        current_size   = row[COL_COMPANY_SIZE].strip() if len(row) > COL_COMPANY_SIZE else ""

        # Skip if already complete
        if current_rating and current_size:
            skipped_done += 1
            continue

        # Deduplicate by company name
        if company.lower() in seen_companies:
            continue
        seen_companies.add(company.lower())

        to_update.append((i, company, current_size))

    total_rows = sum(1 for r in data_rows if len(r) > COL_COMPANY and r[COL_COMPANY].strip())
    print(f"Total rows in sheet:              {total_rows}")
    print(f"Low fit (role<8 or resume<70%):   {skipped_low} — skipped")
    print(f"Already complete:                 {skipped_done} — skipped")
    print(f"Unique high-fit companies to fetch: {len(to_update)}\n")

    if not to_update:
        print("All rows already have ratings — nothing to do")
        return

    # Fetch and write immediately after each company — crash-safe
    rating_col = chr(64 + COL_GLASSDOOR + 1)    # R
    size_col   = chr(64 + COL_COMPANY_SIZE + 1)  # S
    found = 0

    for idx, (sheet_row, company, existing_size) in enumerate(to_update, 1):
        print(f"  [{idx}/{len(to_update)}] Fetching: {company}...")
        data = get_company_data(company)

        rating = data.get("rating", "")
        size   = data.get("size", "") or existing_size

        if rating or size:
            # Write immediately to sheet
            try:
                ws.update(
                    f"{rating_col}{sheet_row}:{size_col}{sheet_row}",
                    [[rating, size]]
                )
                _rl()
                found += 1
                print(f"    → Rating: {rating or 'N/A'} | Size: {size or 'N/A'} ✅ saved")
            except Exception as e:
                print(f"    → Write error: {e}")
        else:
            print(f"    → Not found")

        time.sleep(2)

    print(f"\nDone. {found}/{len(to_update)} companies updated.")
    print(f"Run again anytime to fill remaining gaps — already-filled rows are skipped.")


if __name__ == "__main__":
    sid = os.environ.get("GOOGLE_SPREADSHEET_ID", "")
    if not sid:
        print("❌ GOOGLE_SPREADSHEET_ID not set in .env")
        sys.exit(1)
    run(sid)
