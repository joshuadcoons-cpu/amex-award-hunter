"""Verify the Seats.aero Partner API connection with one small authenticated request.

Usage (from the project folder):
    python scripts/test_connection.py
    python scripts/test_connection.py --origin SFO --destination FCO

Reads SEATS_AERO_API_KEY from .env / environment. Never prints the full key.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from award_hunter.api_client import SeatsAeroClient  # noqa: E402
from award_hunter.config import ConfigError, load_settings, validate_security  # noqa: E402
from award_hunter.logging_config import configure_logging, mask_secret  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the Seats.aero API connection")
    parser.add_argument("--origin", default="LAX")
    parser.add_argument("--destination", default="LHR")
    args = parser.parse_args()

    configure_logging()
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}")
        return 2

    print("Amex Award Hunter - Seats.aero connection test")
    print(f"  Base URL      : {settings.base_url}")
    print(f"  API key       : {mask_secret(settings.api_key) if settings.has_api_key else 'NOT SET'}")
    print(f"  Auth scheme   : {settings.auth_scheme}")
    for warning in validate_security(settings):
        print(f"  WARNING       : {warning}")
    if not settings.has_api_key:
        print("\nFAILED: SEATS_AERO_API_KEY is not configured.")
        print("  1. Copy .env.example to .env in the project folder")
        print("  2. Replace 'replace_with_your_key' with your Seats.aero Pro API key")
        return 1

    with SeatsAeroClient(settings) as client:
        result = client.test_connection(args.origin.upper(), args.destination.upper())

    print()
    if result.ok:
        print("SUCCESS: connected to Seats.aero Partner API")
        print(f"  HTTP status   : {result.status_code}")
        print(f"  Latency       : {result.latency_ms} ms")
        print(f"  Auth scheme   : {result.auth_scheme_used}" + (
            "  <- set SEATS_AERO_AUTH_SCHEME to this in .env" if result.auth_scheme_used != settings.auth_scheme else ""))
        print(f"  Records       : {result.records_returned}")
        print(f"  Quota left    : {result.remaining_quota if result.remaining_quota is not None else 'header not provided'}")
        if result.sample_fields:
            print(f"  Fields seen   : {', '.join(result.sample_fields)}")
        print(f"\n  {result.message}")
        return 0
    print("FAILED: could not complete an authenticated request")
    print(f"  HTTP status   : {result.status_code}")
    print(f"  Detail        : {result.message}")
    if result.status_code in (401, 403):
        print("  Hint          : confirm the key is a Pro *Partner API* key from seats.aero Settings, and that it was pasted "
              "without extra spaces or the word 'Bearer'.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
