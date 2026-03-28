-- ============================================================================
-- Job Fetcher — Supabase Schema
-- Run this in: Supabase Dashboard → SQL Editor → New Query → Run
-- ============================================================================

-- Jobs table (raw + enriched combined, upserted each run)
CREATE TABLE IF NOT EXISTS jobs (
  job_id              TEXT PRIMARY KEY,
  title               TEXT NOT NULL,
  company             TEXT,
  location            TEXT,
  region              TEXT,          -- Dubai | Singapore | EU | USA | India
  seniority           TEXT,
  posted_date         DATE,
  fetched_date        DATE,
  expiry_date         DATE,
  source              TEXT,
  url                 TEXT,
  url_direct          TEXT,
  snippet             TEXT,
  full_description    TEXT,
  salary_text         TEXT,
  remote_type         TEXT,          -- Remote | Remote-friendly | Hybrid | Onsite

  -- Enrichment (populated after Claude scores the job)
  role_match_score    NUMERIC(4,1),
  visa_sponsor_detected TEXT,        -- Yes | No | Unclear
  resume_match_pct    INTEGER,       -- 0–100
  key_matching_skills JSONB DEFAULT '[]',
  red_flags           JSONB DEFAULT '[]',
  gap_to_close        TEXT,
  composite_score     NUMERIC(4,2),  -- 0–10
  apply_priority      TEXT,          -- High | Medium | Low
  apply_after         TEXT,
  enrichment_notes    TEXT,
  inr_equivalent_lpa  INTEGER,
  salary_range        TEXT,

  -- Company data (populated by enrich_ratings.py)
  glassdoor_rating    TEXT,          -- e.g. "4.1"
  company_size        TEXT,          -- e.g. "10,000-50,000"

  -- Application tracking (updated by user in dashboard)
  apply_status        TEXT DEFAULT 'New',
  -- New | Saved | Applied | Screening | Interview | Final | Offer | Accepted | Rejected | Withdrawn
  applied_date        DATE,
  interview_date      DATE,
  offer_details       TEXT,
  recruiter_notes     TEXT,
  follow_up_date      DATE,

  -- Metadata
  created_at          TIMESTAMPTZ DEFAULT NOW(),
  updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-update updated_at on every row change
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER jobs_updated_at
  BEFORE UPDATE ON jobs
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Fetch run history (used for smart fetch window)
CREATE TABLE IF NOT EXISTS fetch_runs (
  id            SERIAL PRIMARY KEY,
  run_id        TEXT UNIQUE,
  started_at    TIMESTAMPTZ DEFAULT NOW(),
  finished_at   TIMESTAMPTZ,
  jobs_fetched  INTEGER DEFAULT 0,
  jobs_new      INTEGER DEFAULT 0,
  jobs_enriched INTEGER DEFAULT 0,
  status        TEXT DEFAULT 'running'  -- running | success | error
);

-- Resume analysis cache (one row per analysis run)
CREATE TABLE IF NOT EXISTS resume_analyses (
  id              SERIAL PRIMARY KEY,
  analyzed_at     TIMESTAMPTZ DEFAULT NOW(),
  quick_wins      JSONB DEFAULT '[]',
  roadmap_30d     JSONB DEFAULT '[]',
  roadmap_90d     JSONB DEFAULT '[]',
  keyword_gaps    JSONB DEFAULT '[]',
  score_before    NUMERIC(4,2),
  score_after_sim NUMERIC(4,2),
  jobs_unlocked   INTEGER DEFAULT 0,
  raw_analysis    TEXT
);

-- Case studies portfolio (written by user + Claude-assisted drafts)
CREATE TABLE IF NOT EXISTS case_studies (
  id              SERIAL PRIMARY KEY,
  title           TEXT NOT NULL,
  company         TEXT,               -- company where this happened
  role            TEXT,               -- your role at the time
  tags            JSONB DEFAULT '[]', -- ["logistics", "automation", "B2B SaaS"]
  problem         TEXT,               -- what problem you solved
  approach        TEXT,               -- how you approached it
  outcome         TEXT,               -- measurable results
  metrics         TEXT,               -- key numbers e.g. "70% TAT reduction, $2M ARR"
  full_draft      TEXT,               -- Claude-generated full narrative
  status          TEXT DEFAULT 'draft', -- draft | polished | published
  target_roles    JSONB DEFAULT '[]', -- which job types this case study is suited for
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER case_studies_updated_at
  BEFORE UPDATE ON case_studies
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Indexes for common dashboard queries
CREATE INDEX IF NOT EXISTS idx_jobs_composite_score ON jobs(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_region ON jobs(region);
CREATE INDEX IF NOT EXISTS idx_jobs_apply_priority ON jobs(apply_priority);
CREATE INDEX IF NOT EXISTS idx_jobs_apply_status ON jobs(apply_status);
CREATE INDEX IF NOT EXISTS idx_jobs_fetched_date ON jobs(fetched_date DESC);
CREATE INDEX IF NOT EXISTS idx_fetch_runs_finished ON fetch_runs(finished_at DESC);

-- Enable Realtime on jobs table (optional — for live dashboard updates)
-- ALTER PUBLICATION supabase_realtime ADD TABLE jobs;
