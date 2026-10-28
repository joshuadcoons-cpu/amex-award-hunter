# Development plan and status

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Secure config, Seats.aero client (retries, backoff, quota header, daily budget, cache), response validation, normalization, connection test, API notes | Code + tests complete. **Live authenticated request not yet run** – the build environment could not reach seats.aero; run `scripts/test_connection.py` locally. |
| 2 | 60–180 day multi-airport, multi-program search with chunking, pagination, two-seat business filtering, completeness tracking, CSV export | Complete (mocked tests). |
| 3 | Amex transfer partners/bonuses, increment rounding, expired-bonus exclusion, round-trip + open-jaw matching, 0–100 scoring | Complete (mocked tests). |
| 4 | Streamlit dashboard: overview, search, results, calendar, trip builder, deal details, settings | Complete; every page smoke-tested with Streamlit AppTest. |
| 5 | SQLite history, change detection (new / price drop / seats up / disappeared), email+webhook alerts with dedupe, daily scan script, Task Scheduler setup | Complete (mocked tests). |
| 6 | Docs, Windows setup, test coverage | This document, README, SETUP_WINDOWS, API_NOTES, SCORING. |
| 7 | Live validation, cash-first scoring presets, Mac notifications + ntfy push, per-run alert cap, Best Deals home page, double-click Mac installer/launchers, START_HERE guide | Complete. Live connection and full scans run against the real Partner API (raw auth header). |

## Next priorities
1. Verify Amex ratios/partners on americanexpress.com and update the partner table.
2. Record real field coverage per program in API_NOTES (first live scans: aeroplan, flyingblue, delta, qatar, qantas, virginatlantic, jetblue, aeromexico, etihad all report seat counts; emirates and singapore returned no business rows for these routes).
3. Positioning-flight awareness (search SAN→LAX/SFO feeders), aircraft/cabin quality ratings, historical price trend charts.
4. Optional: `/trips/{id}` on-demand refresh button for a single award (1 call) when `include_trips` data is missing.

## Architecture
Modular monolith in `award_hunter/`: `config` → `api_client` → `normalization` →
`search` (plan/run/filter) → `transfer_bonuses` + `scoring` → `trip_matcher` →
`database` (history/changes) → `alerts` → `scheduler` (orchestrates). `app.py`
(Streamlit) and `scripts/run_daily_scan.py` both call `scheduler.run_scan`, so the
dashboard and the scheduled job behave identically.
