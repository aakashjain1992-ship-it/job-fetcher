"""
Candidate profile — loaded from config/profile.yaml + environment variables.
Env vars take precedence over YAML values for sensitive fields.

Setup:
  cp config/profile.example.yaml config/profile.yaml
  # Edit profile.yaml with your background, strengths, and search preferences.
"""

import os
import sys
from pathlib import Path

CONFIG_DIR = Path(__file__).parent
PROJECT_ROOT = CONFIG_DIR.parent
PROFILE_PATH = CONFIG_DIR / "profile.yaml"


def _load_yaml():
    """Load profile from YAML config file."""
    try:
        import yaml
    except ImportError:
        print("ERROR: pyyaml is required. Run: pip install pyyaml")
        sys.exit(1)

    if not PROFILE_PATH.exists():
        example = CONFIG_DIR / "profile.example.yaml"
        print(f"ERROR: Profile not found at {PROFILE_PATH}")
        print(f"  Run: cp {example} {PROFILE_PATH}")
        print(f"  Then edit {PROFILE_PATH} with your details.")
        sys.exit(1)

    with open(PROFILE_PATH) as f:
        return yaml.safe_load(f)


def _build_profile():
    """Build the candidate profile dict from YAML + env vars."""
    cfg = _load_yaml()

    # Env vars override YAML for sensitive fields
    return {
        "name": os.environ.get("CANDIDATE_NAME", cfg.get("name", "PM Candidate")),
        "total_experience_years": int(os.environ.get("CANDIDATE_YEARS_EXP", cfg.get("years_experience", 10))),
        "current_title": os.environ.get("CANDIDATE_CURRENT_TITLE", cfg.get("current_title", "Senior Product Manager")),
        "current_company": os.environ.get("CANDIDATE_CURRENT_CO", cfg.get("current_company", "Current Company")),
        "target_ctc_lpa": int(os.environ.get("CANDIDATE_TARGET_CTC", cfg.get("target_ctc_lpa", 80))),
        "location": os.environ.get("CANDIDATE_LOCATION", cfg.get("location", "India")),
        "target_regions": cfg.get("target_regions", ["Dubai", "Singapore", "EU", "USA"]),
        "visa_required": cfg.get("visa_required", True),
        "strengths": cfg.get("strengths", []),
        "known_gaps": cfg.get("known_gaps", []),
        "domain_strengths": cfg.get("domain_strengths", []),
        "target_roles": cfg.get("target_roles", []),
        "preferred_domains": cfg.get("preferred_domains", []),
        "key_metrics": cfg.get("key_metrics", []),
        "salary_targets": {
            "Dubai_AED": os.environ.get("TARGET_SALARY_AED", cfg.get("salary_targets", {}).get("Dubai_AED", "450000+")),
            "Singapore_SGD": os.environ.get("TARGET_SALARY_SGD", cfg.get("salary_targets", {}).get("Singapore_SGD", "180000+")),
            "EU_EUR": os.environ.get("TARGET_SALARY_EUR", cfg.get("salary_targets", {}).get("EU_EUR", "110000+")),
            "USA_USD": os.environ.get("TARGET_SALARY_USD", cfg.get("salary_targets", {}).get("USA_USD", "200000+")),
        },
        "portfolio_status": cfg.get("portfolio_status", {
            "case_studies_published": 0,
            "ai_products_launched": 0,
            "linkedin_posts": 0,
            "portfolio_site_live": False,
        }),
    }


def _load_config():
    """Load full config including searches, career pages, and settings."""
    return _load_yaml()


# --- Module-level exports (loaded once at import time) -----------------------

CANDIDATE_PROFILE = _build_profile()
_CONFIG = _load_config()

# Search and career page config
SEARCHES = _CONFIG.get("searches", [])
CAREER_PAGES = _CONFIG.get("career_pages", [])
MAX_EXPERIENCE_YEARS = _CONFIG.get("max_experience_years", 12)
MIN_ENRICHMENT_SCORE = _CONFIG.get("min_enrichment_score", 5)
STORAGE_MODE = _CONFIG.get("storage", "json")
JSON_OUTPUT_DIR = _CONFIG.get("json_output_dir", "output")

# Role equivalence map — these titles all count as "Senior PM equivalent"
EQUIVALENT_ROLES = [r.lower() for r in CANDIDATE_PROFILE.get("target_roles", [])] + [
    "director, product management",
    "lead product manager",
    "staff product manager",
    "product manager iii",
    "product manager 3",
    "senior pm",
    "sr. product manager",
    "sr product manager",
    "vice president of product",
]

# Build HIGH_FIT_DOMAINS from the user's preferred domains
HIGH_FIT_DOMAINS = []
for domain in CANDIDATE_PROFILE.get("preferred_domains", []):
    # Split compound domain names into searchable keywords
    for word in domain.lower().replace("/", " ").replace("&", " ").split():
        word = word.strip()
        if len(word) > 2 and word not in HIGH_FIT_DOMAINS:
            HIGH_FIT_DOMAINS.append(word)

# Keywords that signal visa sponsorship willingness
VISA_POSITIVE_SIGNALS = [
    "visa sponsorship", "we sponsor", "work authorization",
    "relocation", "open to candidates", "international candidates",
    "global talent", "we welcome", "sponsorship available",
    "h-1b", "h1b", "employment pass", "work permit",
    "all nationalities", "open to relocation",
]

# Keywords that signal visa is NOT sponsored
VISA_NEGATIVE_SIGNALS = [
    "must be authorized to work",
    "us citizen only", "us citizens only",
    "security clearance required",
    "no sponsorship", "not able to sponsor",
    "no visa sponsorship",
    "must have right to work",
    "citizens and permanent residents only",
]
