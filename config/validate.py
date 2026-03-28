"""
Startup validation — checks that all required config is in place before
running the pipeline. Catches misconfiguration early instead of failing
10 minutes into a run.
"""

import os
import sys
from pathlib import Path


def validate_config(storage_mode: str, dry_run: bool = False) -> bool:
    """
    Validate that all required configuration is present.
    Returns True if valid, prints errors and returns False if not.
    """
    errors = []
    warnings = []

    # 1. Profile YAML must exist
    profile_path = Path(__file__).parent / "profile.yaml"
    if not profile_path.exists():
        example = Path(__file__).parent / "profile.example.yaml"
        errors.append(
            f"Profile config not found: {profile_path}\n"
            f"  Run: cp {example} {profile_path}\n"
            f"  Then edit it with your details."
        )

    # 2. Anthropic API key (required unless dry-run)
    if not dry_run:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or api_key.startswith("your_"):
            errors.append(
                "ANTHROPIC_API_KEY not set or still has placeholder value.\n"
                "  Set it in your .env file. Get a key at: https://console.anthropic.com/"
            )

    # 3. Google Sheets config (if using sheets)
    if storage_mode in ("google_sheets", "both"):
        sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        sheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "")

        if not sa_json or sa_json.startswith("your_"):
            if storage_mode == "google_sheets":
                errors.append(
                    "GOOGLE_SERVICE_ACCOUNT_JSON not set.\n"
                    "  Set it to the path of your service account JSON file,\n"
                    "  or paste the JSON contents directly in .env.\n"
                    "  Or switch to storage: \"json\" in profile.yaml."
                )
            else:
                warnings.append(
                    "GOOGLE_SERVICE_ACCOUNT_JSON not set — Google Sheets storage will be skipped."
                )

        if not sheet_id or sheet_id.startswith("your_"):
            if storage_mode == "google_sheets":
                errors.append(
                    "GOOGLE_SPREADSHEET_ID not set.\n"
                    "  Copy the Sheet ID from your Google Sheet URL.\n"
                    "  Or switch to storage: \"json\" in profile.yaml."
                )
            else:
                warnings.append(
                    "GOOGLE_SPREADSHEET_ID not set — Google Sheets storage will be skipped."
                )

    # 4. Optional services
    resend_key = os.environ.get("RESEND_API_KEY", "")
    digest_email = os.environ.get("DIGEST_EMAIL", "")
    if resend_key and not digest_email:
        warnings.append("RESEND_API_KEY is set but DIGEST_EMAIL is empty — digest emails won't be sent.")
    if digest_email and not resend_key:
        warnings.append("DIGEST_EMAIL is set but RESEND_API_KEY is empty — digest emails won't be sent.")

    # Print results
    if warnings:
        for w in warnings:
            print(f"  WARNING: {w}")
        print()

    if errors:
        print("CONFIGURATION ERRORS:")
        print()
        for i, e in enumerate(errors, 1):
            print(f"  {i}. {e}")
        print()
        print("Fix the above errors and try again.")
        return False

    return True
