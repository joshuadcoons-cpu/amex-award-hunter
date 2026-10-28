"""Run one full scan (search -> persist -> match -> alert). Run daily by launchd on the Mac.

Usage:
    python scripts/run_daily_scan.py            # full scan with alerts
    python scripts/run_daily_scan.py --no-alerts
    python scripts/run_daily_scan.py --dry-run  # plan only; no API calls
    python scripts/run_daily_scan.py --test-alert  # send a test notification

Exit codes: 0 ok, 1 partial (some chunks failed), 2 failed/aborted, 3 configuration error.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from award_hunter.config import ConfigError, load_settings  # noqa: E402
from award_hunter.database import Database  # noqa: E402
from award_hunter.logging_config import configure_logging  # noqa: E402
from award_hunter.scheduler import load_profile, run_scan  # noqa: E402
from award_hunter.search import build_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Amex Award Hunter daily scan")
    parser.add_argument("--no-alerts", action="store_true", help="search and store, but do not send notifications")
    parser.add_argument("--dry-run", action="store_true", help="print the query plan and estimated API calls, then exit")
    parser.add_argument("--test-alert", action="store_true", help="send a test notification to every configured channel, then exit")
    args = parser.parse_args()

    configure_logging()
    try:
        settings = load_settings()
        settings.require_api_key()
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}")
        return 3

    db = Database(settings.database_path)
    if args.test_alert:
        from award_hunter.alerts import AlertSender
        sender = AlertSender(settings, db)
        if not sender.channels:
            print("No alert channel is configured. On a Mac, notifications are on by default; otherwise set NTFY_TOPIC in .env.")
            return 1
        results = sender.send_test()
        for channel, (ok, err) in results.items():
            print(f"  {channel:8s}: {'OK' if ok else 'FAILED - ' + str(err)}")
        return 0 if all(ok for ok, _ in results.values()) else 1
    profile = load_profile(db)
    plan = build_plan(profile)
    print(f"Search window {profile.start_date} .. {profile.end_date} ({profile.window_days} days)")
    print(f"Origins: {', '.join(profile.effective_origins())}")
    print(f"Destinations: {', '.join(profile.effective_destinations())}")
    print(f"Programs: {', '.join(profile.sources)}")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Query chunks: {len(plan.chunks)}  (estimated {plan.estimated_calls()} API calls; budget left today: "
          f"{settings.api_daily_budget - db.budget_load(today)})")
    if args.dry_run:
        for c in plan.chunks:
            print("  ", c.describe())
        return 0

    def progress(i: int, n: int, label: str) -> None:
        print(f"[{i}/{n}] {label}")

    outcome = run_scan(settings, db, profile=profile, trigger="scheduled", send_alerts=not args.no_alerts, progress=progress)
    r = outcome.result
    print()
    print(f"API calls made      : {r.calls_made}")
    print(f"Records seen        : {r.records_seen}")
    print(f"Qualifying awards   : {len(r.qualifying)}  (outbound {len(r.outbound)}, return {len(r.inbound)})")
    print(f"Round trips matched : {len(outcome.round_trips)}  ({outcome.new_round_trips} new)")
    print(f"New awards          : {outcome.new_awards}   Disappeared: {outcome.disappeared}")
    print(f"Coverage            : {r.coverage_summary()}")
    print(f"Alerts              : {len(outcome.alerts)} generated, {outcome.alert_stats}")
    if r.errors:
        print("Errors:")
        for e in r.errors[:10]:
            print("  -", e)
    db.close()
    if r.aborted:
        return 2
    return 1 if r.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
