# Job Fetcher

An automated job intelligence pipeline that fetches, deduplicates, and AI-scores job listings from multiple sources. Customize it for **any role, experience level, and location**.

## What It Does

```
JobSpy (LinkedIn + Indeed + Google)     ~150-200 jobs/run
Company Career Pages (configurable)     ~20-30 jobs/run
              |
     Deduplication (30-day SQLite cache)
     + Title/Company dedup within run
     + Experience filter (configurable max years)
              |
  Claude AI Enrichment (batches of 8)
  - Role match score (0-10)
  - Visa sponsorship detected
  - Resume match %
  - Key matching skills
  - Red flags
  - Gap to close before applying
  - Composite score + Apply Priority
              |
     Storage (choose your backend):
     - Google Sheets (2 tabs: Raw Jobs, Enriched Jobs)
     - JSON files (output/ directory)
     - CSV files (output/ directory)
     - Both (Sheets + JSON simultaneously)
              |
     Weekly Email Digest (Mondays via Resend)
```

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/job-fetcher.git
cd job-fetcher
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Or install as a package:
```bash
pip install -e .
```

### 2. Create your profile

```bash
cp config/profile.example.yaml config/profile.yaml
```

Edit `config/profile.yaml` with your details:
- **Basic info**: name, current title, years of experience, target salary
- **Strengths**: your key skills and achievements (be specific for better AI scoring)
- **Known gaps**: areas you're still developing (calibrates match scores honestly)
- **Search config**: which regions, roles, and queries to search for
- **Career pages**: which company career pages to scrape
- **Storage**: choose `google_sheets`, `json`, `csv`, or `both`

### 3. Set up environment variables

```bash
cp .env.example .env
```

Fill in your `.env`:

```env
# Required -- Claude API key for AI enrichment
ANTHROPIC_API_KEY=sk-ant-...

# Required for Google Sheets storage (skip if using JSON/CSV only)
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service_account.json
GOOGLE_SPREADSHEET_ID=your_sheet_id_here

# Optional -- weekly email digest via Resend
RESEND_API_KEY=re_...
DIGEST_EMAIL=you@example.com

# Optional -- Glassdoor ratings via SerpAPI (100 free searches/month)
SERPAPI_KEY=...
```

Candidate profile fields (name, title, etc.) can be set either in `profile.yaml` or as env vars. Env vars take precedence — useful for keeping secrets out of files.

### 4. Choose your storage

#### Option A: JSON files (simplest -- no setup needed)

Set `storage: "json"` in `config/profile.yaml`. Results saved to `output/raw_jobs.json` and `output/enriched_jobs.json`.

#### Option B: CSV files (easiest to open in Excel/Sheets)

Set `storage: "csv"` in `config/profile.yaml`. Results saved to `output/raw_jobs.csv` and `output/enriched_jobs.csv`.

#### Option C: Google Sheets

1. Create a Google Cloud project, enable **Google Sheets API** and **Google Drive API**
2. Create a **Service Account** and download the JSON key
3. Create a Google Sheet with two tabs: `Raw Jobs` and `Enriched Jobs`
4. Share the sheet with your service account email as **Editor**
5. Set `GOOGLE_SPREADSHEET_ID` in `.env` to the Sheet ID from the URL
6. Set `storage: "google_sheets"` in `config/profile.yaml`

Initialize headers:
```bash
python3 -c "
import os; from dotenv import load_dotenv; load_dotenv()
from storage.sheets_writer import ensure_headers
ensure_headers(os.environ['GOOGLE_SPREADSHEET_ID'])
print('Headers set')
"
```

#### Option D: Both (Sheets + JSON)

Set `storage: "both"` to write to Google Sheets AND JSON files simultaneously.

### 5. Verify your setup

```bash
# Preview configured searches without running anything
python3 main.py --list-searches

# Fetch + dedup only -- no Claude API calls, no storage writes
python3 main.py --dry-run

# Test enrichment with 2 sample jobs (uses Claude API, skips fetching)
python3 main.py --test
```

### 6. Run

```bash
# Full pipeline
python3 main.py

# Skip career page scraping
python3 main.py --skip-career-pages

# Skip Glassdoor ratings enrichment
python3 main.py --skip-ratings

# Limit jobs processed per run
python3 main.py --max-jobs 50
```

## Customizing for Your Role

The entire pipeline is driven by `config/profile.yaml`. To adapt it for a different role:

### Change target roles

Edit the `target_roles` list and the `searches` section. For example, for a **Software Engineer**:

```yaml
target_roles:
  - "Senior Software Engineer"
  - "Staff Engineer"
  - "Principal Engineer"

searches:
  - query: '"Senior Software Engineer" OR "Staff Engineer"'
    location: "San Francisco, CA"
    country: "USA"
    region: "USA"
    remote: true
```

### Change scoring criteria

Edit `strengths`, `known_gaps`, `domain_strengths`, and `key_metrics`. Claude uses these to score each job against YOUR specific background. The more specific you are (include company names, metrics, technologies), the better the scoring.

### Add/remove career pages

Edit the `career_pages` list. Greenhouse companies use the JSON API (most reliable):

```yaml
career_pages:
  - company: "Stripe"
    type: "greenhouse"
    board_token: "stripe"
    default_region: "USA"
```

For other ATS systems, use HTML scraping:

```yaml
  - company: "Netflix"
    type: "html"
    url: "https://jobs.netflix.com/search?q=engineer"
    default_region: "USA"
```

To find a company's Greenhouse board token, check if `https://boards-api.greenhouse.io/v1/boards/COMPANY_NAME/jobs` returns JSON. Common tokens: company name in lowercase, sometimes with hyphens.

### Change regions

Edit the `searches` list to add/remove regions. Each search entry specifies:

| Field | Description | Example |
|-------|-------------|---------|
| `query` | Search string (use quotes for exact phrases) | `'"Senior Engineer" OR "Staff Engineer"'` |
| `location` | City/country for the search | `"Berlin, Germany"` |
| `country` | Country code for Indeed | `"Germany"` |
| `region` | Your tag for grouping results | `"EU"` |
| `remote` | Filter for remote-only jobs | `true` / `false` |

### Adjust filters

```yaml
# Exclude jobs requiring more than this many years
max_experience_years: 12

# Minimum composite score for enriched results
min_enrichment_score: 5
```

## Automating with GitHub Actions

The included workflow (`.github/workflows/fetch.yml`) runs the pipeline 3x daily.

### Setup

1. Push the repo to GitHub
2. Go to **Settings > Secrets and variables > Actions**
3. Add these secrets:

| Secret | Required | Description |
|--------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | If using Sheets | Full JSON contents of service account key |
| `GOOGLE_SPREADSHEET_ID` | If using Sheets | Sheet ID from URL |
| `RESEND_API_KEY` | No | For weekly email digest |
| `DIGEST_EMAIL` | No | Email to receive digest |
| `SERPAPI_KEY` | No | For Glassdoor ratings |
| `CANDIDATE_NAME` | No | Overrides profile.yaml |
| `CANDIDATE_CURRENT_TITLE` | No | Overrides profile.yaml |
| `CANDIDATE_CURRENT_CO` | No | Overrides profile.yaml |
| `CANDIDATE_YEARS_EXP` | No | Overrides profile.yaml |
| `CANDIDATE_LOCATION` | No | Overrides profile.yaml |
| `CANDIDATE_TARGET_CTC` | No | Overrides profile.yaml |

4. Adjust the cron schedule in the workflow file if needed

You can also trigger runs manually from the **Actions** tab with options to skip career pages, skip ratings, or run in test mode.

## CLI Reference

```
python main.py [OPTIONS]

Options:
  --test              Test mode -- 2 sample jobs, tests enrichment + storage
  --dry-run           Fetch & dedup only -- no Claude API calls, no writes
  --list-searches     Show all configured searches and career pages
  --skip-career-pages Skip career page scraping
  --skip-ratings      Skip Glassdoor ratings enrichment
  --max-jobs N        Max jobs to process per run (default: 200)
```

## Scoring Logic

```
Composite Score = (role_match x 3 + visa_score x 2 + resume_match/20) x 10/7

Visa score:  Yes = 2  |  Unclear = 1  |  No = 0

Apply Priority:
  >= 8.0  ->  High    (apply within 5 days)
  6-7.9   ->  Medium  (apply after specific prep)
  < 5.0   ->  Filtered out (unless gap is closeable)
```

## Project Structure

```
job-fetcher/
|
├── main.py                          # Pipeline orchestrator + CLI
├── pyproject.toml                   # Python packaging (pip install -e .)
├── enrich_ratings.py                # Glassdoor ratings (standalone)
├── requirements.txt
├── .env.example                     # Template -- copy to .env
|
├── config/
│   ├── profile.py                   # Loads profile from YAML + env vars
│   ├── profile.example.yaml         # Template -- copy to profile.yaml
│   └── validate.py                  # Startup config validation
|
├── fetchers/
│   ├── jobspy_fetcher.py            # LinkedIn + Indeed + Google (free via JobSpy)
│   └── career_pages/
│       └── scraper.py               # Greenhouse API + HTML scraping
|
├── enricher/
│   └── claude_enricher.py           # Claude AI scoring pipeline
|
├── storage/
│   ├── sheets_writer.py             # Google Sheets backend
│   ├── json_writer.py               # JSON file backend
│   ├── csv_writer.py                # CSV file backend
│   └── dedup_cache.py               # SQLite dedup (30-day TTL)
|
├── notifier/
│   └── weekly_digest.py             # Monday email digest via Resend
|
├── data/                            # Auto-created, gitignored
│   └── dedup_cache.db
|
└── output/                          # JSON/CSV output, gitignored
    ├── raw_jobs.json / .csv
    └── enriched_jobs.json / .csv
```

## Cost Estimate

| Service | Usage | Monthly Cost |
|---------|-------|-------------|
| Anthropic Claude Haiku | ~200 jobs/run x 4 runs/month | ~$2-4 |
| JobSpy | Unlimited | Free |
| Google Sheets API | Well within free limits | Free |
| Resend (email) | 4 emails/month | Free |
| SerpAPI (ratings) | 100 searches free/month | Free |
| **Total** | | **~$2-4/month** |

## License

MIT
