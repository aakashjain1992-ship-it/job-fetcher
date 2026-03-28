# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated job intelligence pipeline. Fetches jobs from LinkedIn/Indeed/Google (via JobSpy) and configurable company career pages, enriches with Claude Haiku AI scoring, writes results to configurable storage (Google Sheets, JSON, CSV). Weekly email digest via Resend.

All configuration (role, experience, regions, searches, career pages, storage) lives in `config/profile.yaml`.

## Commands

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt          # or: pip install -e .
cp config/profile.example.yaml config/profile.yaml
cp .env.example .env

# Run
python3 main.py                    # Full pipeline
python3 main.py --test             # Test enrichment with 2 sample jobs
python3 main.py --dry-run          # Fetch + dedup only, no API calls
python3 main.py --list-searches    # Preview configured searches
python3 main.py --skip-career-pages
python3 main.py --skip-ratings
python3 enrich_ratings.py          # Glassdoor ratings standalone
```

No test suite. Use `--test` for enrichment validation, `--dry-run` for fetch validation.

## Architecture

### Configuration

- **config/profile.yaml** — Single YAML config for everything: candidate profile, search queries, career pages, storage mode, thresholds. Template at `profile.example.yaml`.
- **config/profile.py** — Parses YAML, merges with env vars (env takes precedence for sensitive fields). Exports `CANDIDATE_PROFILE`, `SEARCHES`, `CAREER_PAGES`, `STORAGE_MODE`, etc.
- **config/validate.py** — Startup validation. Checks profile.yaml exists, API keys set, sheets configured. Runs before pipeline to catch errors early.
- **.env** — API keys and credentials only.

### Pipeline flow (main.py)

Fetch -> Dedup -> Write raw -> Enrich with Claude -> Write enriched -> Weekly digest -> Glassdoor ratings

`_cli()` is the entry point (also registered as `job-fetcher` console script via pyproject.toml). Supports `--dry-run` (fetch+dedup only), `--list-searches` (print config), `--test` (sample jobs).

### Storage backends

Controlled by `storage` field in profile.yaml: `"google_sheets"`, `"json"`, `"csv"`, or `"both"` (sheets + json).

- **storage/sheets_writer.py** — Google Sheets via `gspread`. Cached client (single auth per run). Tabs: `Raw Jobs`, `Enriched Jobs`.
- **storage/json_writer.py** — Appends to `output/raw_jobs.json` and `output/enriched_jobs.json`. Deduplicates by job_id.
- **storage/csv_writer.py** — Appends to `output/raw_jobs.csv` and `output/enriched_jobs.csv`. No dependencies beyond stdlib.
- **storage/dedup_cache.py** — SQLite with 30-day TTL. Two dedup layers: URL+title+company hash (cross-run) and title+company hash (within-run).

### Fetchers

- **fetchers/jobspy_fetcher.py** — `python-jobspy` library. Searches from `SEARCHES` in profile config. Title keyword filtering + experience threshold. Retry logic (2 attempts with 10s delay).
- **fetchers/career_pages/scraper.py** — Company list from `CAREER_PAGES` in config. Greenhouse JSON API (`_greenhouse()`) or HTML scraping (`_scrape_html()`).

### Enrichment

- **enricher/claude_enricher.py** — Batches of 8 jobs to Claude Haiku. Candidate profile is the system prompt. Composite score: role_match 40% + visa 20% + resume_match 30% + no_red_flags 10%.

### Other

- **notifier/weekly_digest.py** — HTML email via Resend API on Mondays. Top 10 by composite score.
- **enrich_ratings.py** — Standalone Glassdoor ratings. Wikipedia (free) + SerpAPI (optional). Only high-fit jobs (role >= 8, resume >= 70%).

### Data flow

Job dicts flow through the pipeline. Core fields: `job_id`, `title`, `company`, `region`, `url`, `full_description`, `salary_text`, `remote_type`. After enrichment adds: `role_match_score`, `visa_sponsor_detected`, `resume_match_pct`, `composite_score`, `apply_priority`.

### CI/CD

GitHub Actions (`.github/workflows/fetch.yml`) runs 3x daily. Dedup cache persisted via `actions/cache`. Supports manual dispatch with skip flags.
