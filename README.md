# Amex Award Hunter

A private tool for your Mac that searches the **Seats.aero Partner API** across whole
60–180-day windows and many airports, finds **business-class award seats for two**
from Southern California to Europe, converts airline miles into the **American
Express Membership Rewards points you would actually transfer**, pairs outbound and
return flights into complete 7–14-day trips, scores every deal 0–100, keeps history,
and alerts you when something exceptional appears.

It is a discovery and decision-support tool. It never books flights, never transfers
points, and never presents cached availability as a reservation.

## Quick start (macOS)
Read **[START_HERE.md](START_HERE.md)**. Short version:
1. Move the folder somewhere permanent (home folder or Documents).
2. Double-click **`Install Award Hunter.command`** (right-click → Open if macOS complains). It sets up
   Python privately, checks your key, schedules a 7:30 AM daily scan, runs the first scan and opens the dashboard.
3. Day to day: **`Open Award Hunter.command`** (dashboard), **`Scan Now.command`**, **`Schedule Daily Scan.command`**,
   **`Update Award Hunter.command`** (pull the latest code from GitHub).

Manual equivalent: `./scripts/setup_mac.sh`, then `./scripts/test_connection.sh`, `./scripts/start_dashboard.sh`,
`./scripts/setup_mac_schedule.sh 07 30`. Full details: [docs/SETUP_MAC.md](docs/SETUP_MAC.md).

## Creating the `.env` file
Copy `.env.example` to `.env` (same folder as `app.py`) and edit:
```
SEATS_AERO_API_KEY=your_pro_api_key_here
DATABASE_PATH=data/awards.sqlite
TIMEZONE=America/Los_Angeles
```
`.env` is git-ignored. The key is only ever read from the environment; it is never
written to the database, logs (masked), URLs or notifications.

## Commands
| Task | Command (from the project folder) |
|------|-----------------------------------|
| Install deps | `./scripts/setup_mac.sh` |
| Test API connection | `.venv/bin/python scripts/test_connection.py` |
| Dashboard | `.venv/bin/python -m streamlit run app.py` |
| One scan (what the scheduler runs) | `.venv/bin/python scripts/run_daily_scan.py` |
| Preview the query plan without API calls | `.venv/bin/python scripts/run_daily_scan.py --dry-run` |
| Unit tests (mocked API, no key needed) | `.venv/bin/python -m pytest` |

## What the dashboard shows
Four pages, on purpose:
* **Home** – landing page: what the tool does, a flow diagram, the scoring weights, the fee and
  points curves drawn from the live settings, the tier ladder, and the alert rules.
* **Best Deals** – hero number = lowest cash out of pocket for a complete round trip for two;
  cards for the top trips ranked by *Least cash / Fewest points / All-in cost / Best overall*
  with a Verify & book link; cash-vs-points chart; cheapest one-way per route; recent changes;
  a **Scan now** button.
* **Search** – fly-from airports, countries, dates, trip length, caps on cash and points;
  everything else (airports, programs, API chunking, seat/score/freshness rules) under *Advanced*.
* **Settings** – alert channels with a test button and "worth jumping on" thresholds, scoring
  priority, connection status and quota, and (advanced) editable Amex partner ratios and
  transfer bonuses, scan history.

## Watchlist, sweet spots, points balance
* `award_hunter/watchlist.py`: watches (destinations, outbound window, seats, nonstop, caps) widen each scan
  (`profile_with_watches`) and are matched against every stored award regardless of score; new matches raise
  urgent `watch` alerts, deduped per availability id.
* `transfer_partners.SWEET_SPOTS`: per-program floor prices for West Coast → Europe business; `sweet_spot()`
  returns `best` / `sweet` / None, shown as a badge and in alert headlines.
* Points balance (`points_balance` setting, sidebar): affordability text on cards and the hero, plus an
  "Only what I can afford" filter.

## Amex transfer bonuses
`award_hunter/bonus_tracker.py` reads the public tracker at awardtravelfinder.com on every scheduled or
dashboard scan (soft-fail), records new airline bonuses as **unverified**, retires expired ones, and raises a
"transfer bonus spotted" alert. The dashboard lists running bonuses with an **"It's in my account → apply"**
button; only a confirmed bonus enters the points math (`transfer_bonuses.TransferEngine`). Hotel and
non-searched partners are shown for information only.

## How honesty is enforced
* Programs that don't publish seat counts are shown as **"not reported"** and hidden
  by default – never counted as two seats.
* Missing taxes → **"Not provided"** and a mild score penalty, never a bonus.
* Every result is **cached** and labelled *requires airline verification* with the
  Seats.aero observation time in Pacific time.
* Expired or unverified transfer bonuses are never silently applied.
* Non-Amex programs are shown for comparison but can never score as a deal.
* Blank searches are recorded per chunk as *empty*, *error*, *budget* or *skipped*.

## Project layout
```
app.py                     Streamlit dashboard
award_hunter/
  config.py                .env loading, security validation
  api_client.py            Seats.aero client: retries, backoff, quota, budget, cache
  models.py                Raw + normalized pydantic models
  normalization.py         API rows -> AwardObservation (seat/fee/mixed-cabin rules)
  airports.py              U.S. origins + European destination reference data
  search.py                Profile -> query plan -> run -> filter, coverage tracking
  transfer_partners.py     Amex MR partner reference table (seeded, editable)
  transfer_bonuses.py      Points math with ratios, increments, dated bonuses
  trip_matcher.py          Round-trip / open-jaw pairing with ground + positioning estimates (geo.py)
  booking.py               Deep links into award searches, transfer-speed warnings
  aircraft.py              Lie-flat likelihood from the long-haul aircraft
  ui.py                    Pure HTML/SVG builders for the dashboard
  scoring.py               0-100 scoring with cash-first / balanced / points-first presets (docs/SCORING.md)
  seasonality.py           When-to-go calendar rules
  bonus_tracker.py         Amex transfer-bonus detection
  watchlist.py             Watches: seat alerts for specific trips
  database.py              SQLite schema, history, change detection
  alerts.py                Mac notification / ntfy push / email / webhook alerts, per-run cap, dedupe
  scheduler.py             run_scan orchestration shared by UI and scheduler
scripts/                   test_connection.py, run_daily_scan.py, macOS .sh helpers (launchd)
tests/                     pytest suite with mocked Seats.aero responses
docs/                      API_NOTES, SETUP_MAC, SETUP_WINDOWS, SCORING, DEVELOPMENT_PLAN
```

## Status
See [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md). All phases are implemented and
covered by tests. The live API has been exercised end to end (connection test, full scans);
the Partner API accepts the key in the raw header form, which the installer sets automatically.
