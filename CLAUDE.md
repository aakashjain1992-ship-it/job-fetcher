# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated job intelligence pipeline. Fetches jobs from LinkedIn/Indeed/Google (via JobSpy) and configurable company career pages, enriches with Claude Haiku AI scoring, writes results to Supabase (primary), Google Sheets, JSON, or CSV. Weekly email digest via Resend. Web dashboard at `localhost:8000`.

All configuration (role, experience, regions, searches, career pages, storage, scoring preset) lives in `config/profile.yaml`.

## Commands

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt          # or: pip install -e .
cp config/profile.example.yaml config/profile.yaml
cp .env.example .env

# Local dev (loads .env correctly, starts dashboard)
./start.sh                         # Recommended: loads .env + starts --serve

# Pipeline
python3 main.py                    # Full pipeline
python3 main.py --serve            # Web dashboard only (localhost:8000)
python3 main.py --test             # Test enrichment with 2 sample jobs
python3 main.py --dry-run          # Fetch + dedup only, no API calls
python3 main.py --list-searches    # Preview configured searches
python3 main.py --skip-career-pages
python3 main.py --skip-ratings
python3 main.py --max-jobs 50      # Limit jobs per run (default 200)

# Evaluation
python3 main.py --eval regression            # All golden test cases (eval/golden_cases/)
python3 main.py --eval regression --case 004 # Single case by prefix
python3 main.py --eval consistency           # Score same job 3x, check variance
python3 main.py --eval feedback              # Show application outcome summary
python3 main.py --eval feedback --job-id JOB-123 --outcome interview

# Standalone
python3 enrich_ratings.py          # Glassdoor ratings enrichment
```

No test suite. Use `--test` for enrichment validation, `--dry-run` for fetch validation.

## Required Environment Variables (.env)

```
ANTHROPIC_API_KEY          # Claude API — required for enrichment
SUPABASE_URL               # Required when storage=supabase
SUPABASE_SERVICE_KEY       # Required when storage=supabase
RESEND_API_KEY             # Optional: weekly digest email
DIGEST_EMAIL               # Optional: recipient for digest
SERPAPI_KEY                # Optional: Glassdoor ratings (100 free/month)
GOOGLE_SERVICE_ACCOUNT_JSON  # Optional: Google Sheets backend
GOOGLE_SPREADSHEET_ID        # Optional: Google Sheets backend
```

**Critical**: Always use `load_dotenv(override=True)` — without `override=True`, empty shell env vars block `.env` from loading (common CI/subprocess issue).

## Architecture

### Configuration

- **config/profile.yaml** — Single YAML config for everything: candidate profile, search queries, career pages, storage mode, scoring preset, thresholds. Template at `profile.example.yaml`.
- **config/profile.py** — Parses YAML, merges with env vars (env takes precedence). Exports `CANDIDATE_PROFILE`, `SEARCHES`, `CAREER_PAGES`, `STORAGE_MODE`, `SCORING_WEIGHTS`, `EQUIVALENT_ROLES`, `HIGH_FIT_DOMAINS`, `VISA_POSITIVE/NEGATIVE_SIGNALS`.
- **config/validate.py** — Startup validation. Runs before pipeline to catch misconfig early.
- `_SCORING_PRESETS` in profile.py: balanced / visa_first / role_first / remote_first / salary_first. Selected via `scoring_preset` in profile.yaml.

### Pipeline Flow (main.py)

```
Fetch (JobSpy + Career Pages)
  → Dedup (SQLite cache, 30-day TTL)
  → Write raw jobs to storage
  → Enrich with Claude Haiku (batches of 8)
  → Write enriched jobs to storage
  → Weekly digest (Saturdays only, if RESEND_API_KEY set)
  → Glassdoor ratings (if not --skip-ratings)
```

`_cli()` is the entry point, also registered as `job-fetcher` console script. `_compute_fetch_window()` auto-calculates lookback period (first run = 720h; subsequent = hours since last run + 12h buffer).

`_get_storage_writers()` returns a list of writer dicts based on `STORAGE_MODE`, each with `write_raw`, `write_enriched`, `ensure_headers`, `update_dashboard` keys.

### Storage Backends

Controlled by `storage` field in profile.yaml: `"supabase"`, `"google_sheets"`, `"json"`, `"csv"`, or `"both"` (sheets + json).

- **storage/supabase_writer.py** — Primary backend. Upserts to `jobs` and `fetch_runs` tables. `_get_client()` creates Supabase client on demand.
- **storage/sheets_writer.py** — Google Sheets via `gspread`. Tabs: `Raw Jobs`, `Enriched Jobs`.
- **storage/json_writer.py** — Appends to `output/raw_jobs.json` / `output/enriched_jobs.json`. Deduplicates by job_id.
- **storage/csv_writer.py** — Appends to `output/raw_jobs.csv` / `output/enriched_jobs.csv`.
- **storage/dedup_cache.py** — SQLite (`data/dedup_cache.db`), 30-day TTL. Two layers: URL+title+company hash (cross-run) and title+company hash (within-run).

### Supabase Schema (`supabase/schema.sql`)

Four tables:
- **jobs** — Main table. Raw fields + enrichment scores + ratings + application tracking. JSONB for `key_matching_skills`, `red_flags`. Auto-updated `updated_at` trigger. Indexed on `composite_score`, `region`, `apply_priority`, `apply_status`, `fetched_date`.
- **fetch_runs** — Pipeline audit log (run_id, started/finished timestamps, counts, status).
- **resume_analyses** — Gap analysis results (quick_wins, roadmap_30d/90d, keyword_gaps JSONB).
- **case_studies** — Portfolio case studies (problem, approach, outcome, metrics, full_draft, status: draft→polished→published).

### Fetchers

- **fetchers/jobspy_fetcher.py** — `python-jobspy`. Searches from `SEARCHES` config. Title keyword filtering + `max_experience_years` threshold. Retry logic (2 attempts, 10s delay).
- **fetchers/career_pages/scraper.py** — Greenhouse JSON API (`_greenhouse()`) or generic HTML scraping (`_scrape_html()`). Company list from `CAREER_PAGES` config.

### Enrichment

- **enricher/claude_enricher.py** — Batches of 8 jobs to `claude-haiku-4-5` (max_tokens=3000). System prompt is the full candidate profile. Returns per-job scores:
  - `role_match_score` (0-10), `visa_sponsor_detected` (Yes/No/Unclear), `resume_match_pct` (0-100), `key_matching_skills`, `red_flags`, `gap_to_close`, `composite_score`, `apply_priority`, `apply_after`, `notes`
  - `composite_score` weights come from `SCORING_WEIGHTS` (default: role_match 40% + visa 20% + resume 30% + no_red_flags 10%)
  - apply_priority thresholds: ≥8.0 → High, 6.0–7.9 → Medium, <5.0 → Low
  - `enrich_all()` validates output via `eval/output_validator.py` after each batch

### Web Dashboard (`web/app.py`)

FastAPI app served via `python main.py --serve`. All data reads from Supabase.

Key API routes:
- `GET /api/stats` — Summary counts (total, high priority, applied, interviews, new this week)
- `GET /api/jobs` — Filterable job list (region, priority, status)
- `PATCH /api/jobs/{job_id}/status` — Update apply_status / recruiter notes
- `POST /api/training/prep/{job_id}` — Claude generates interview prep for a job
- `GET /api/training/behavioral` — STAR answer templates from job history
- `POST /api/resume/analyze` — Claude gap analysis against JD corpus (requires ≥5 jobs)
- `GET /api/resume/skill-roadmap` — Missing skills ranked by jobs-unlocked estimate
- `POST/GET/PUT/DELETE /api/training/case-studies` — Portfolio CRUD
- `POST /api/training/case-studies/generate` — Claude drafts case study narrative
- `GET /api/pipeline/runs` — Last 20 run records + live `pipeline_running` state
- `POST /api/pipeline/trigger` — Spawn `main.py` subprocess (logs to `data/pipeline_last_run.log`)
- `POST /api/resume/upload` — Upload .docx/.txt resume (saved to `data/uploaded_resume.txt`)

Dashboard tabs: Jobs · Training · Resume · System (pipeline controls, run history, resume upload, scoring preset selector)

### Evaluation System (`eval/`)

- **eval/regression.py** — Runs golden test cases in `eval/golden_cases/*.json`. Each case has input job + expected score ranges. Pass threshold: composite ±1.5.
- **eval/consistency.py** — Scores the same job 3 times, flags variance >1.0.
- **eval/feedback.py** — Logs application outcomes to `eval/feedback_log.jsonl` (gitignored).
- **eval/output_validator.py** — Validates every batch: checks required fields, recomputes composite score, warns if Claude's reported score diverges >1.0 from recomputed.

### Other

- **notifier/weekly_digest.py** — HTML email via Resend on **Saturdays**. Top 10 by composite score.
- **enrich_ratings.py** — Glassdoor ratings. Has `run_supabase()` (queries high-fit jobs from Supabase: role≥8, resume≥70%) and `run_sheets()`. Called by `main.py` step 7 when `STORAGE_MODE == "supabase"` or `SPREADSHEET_ID` is set.

### Data Flow

Job dicts flow through the pipeline as plain Python dicts. Core fields: `job_id`, `title`, `company`, `region`, `url`, `full_description`, `salary_text`, `remote_type`. Enrichment adds: `role_match_score`, `visa_sponsor_detected`, `resume_match_pct`, `composite_score`, `apply_priority`.

`job_id` format: `{SOURCE_PREFIX}-{REGION_PREFIX}-{HASH}` (e.g., `LKD-LI-4388353`, `IND-IN-108BC28`).

### CI/CD & Deployment

- **GitHub Actions** (`.github/workflows/fetch.yml`) — 3× daily. Dedup cache persisted via `actions/cache`. Supports manual dispatch with skip flags.
- **Linode/VPS** — `deploy/` folder has systemd service (`jobfetcher.service`), nginx config, weekly cron (`pipeline-cron.sh`, Saturday 6am UTC), and full setup script (`deploy/setup.sh`).
