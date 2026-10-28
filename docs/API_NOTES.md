# Seats.aero Partner API notes

Last reviewed: 2026-10-28. The official reference lives at <https://developers.seats.aero/>.
The developer-docs host was not reachable from the environment used to write this
code, so the facts below were cross-checked against the Seats.aero knowledge-base
article "Seats.aero Pro API: Access, Limits, and Usage", the public Go client
(`denverquane/seats-aero-go`) and two open-source MCP servers that wrap the API
(`gavgrego/seats.aero-mcp-server`, `baeleb/seats-MCP`). **Anything marked
"unverified" must be confirmed against the live API with `scripts/test_connection.py`
before relying on it.** The code validates every response at runtime and marks
missing data as missing rather than assuming it.

## Access and plan

| Item | Status |
|------|--------|
| Plan required | Seats.aero **Pro** (personal, non-commercial). Not every Pro account is guaranteed API access; the key is generated under Settings on seats.aero. |
| Quota | **1,000 calls per UTC day**, resets at midnight UTC. Usage-based, not per-minute. Shared with any OAuth apps on the account. |
| Quota header | `X-RateLimit-Remaining` on every response (the app records it and shows it in the sidebar). |
| Live search (`POST /live`) | **Not available on Pro keys** (commercial agreement only). The app never calls it. |
| Terms | Non-commercial personal use only; no scraping; respect quota. This app is a private decision-support tool and never books or transfers anything. |

## Authentication

* Base URL: `https://seats.aero/partnerapi`
* Header: `Partner-Authorization: Bearer <api key>` (default). Some sources show the raw key without `Bearer`; set `SEATS_AERO_AUTH_SCHEME=raw` if the connection test reports the bearer form rejected. The connection test tries both and tells you which worked.
* The key is read from `.env` / environment only. It is never logged in full, stored in SQLite, or placed in a URL.

## Endpoints used

### `GET /search` (cached search) — primary endpoint
Query parameters (confirmed via open-source clients that mirror the reference):

| Param | Notes |
|-------|-------|
| `origin_airport` | comma-separated IATA codes (`SAN,LAX,SFO,SEA`) |
| `destination_airport` | comma-separated IATA codes |
| `start_date`, `end_date` | `YYYY-MM-DD`; the whole window is returned, no per-date calls |
| `cabins` | comma-separated: `economy`, `premium`, `business`, `first` |
| `sources` | comma-separated program codes (see below) |
| `carriers` | comma-separated 2-letter codes |
| `take` | 10–1000 (default 500); the app uses 1000 |
| `cursor` / `skip` | pagination; prefer `cursor` (response `cursor` + `hasMore`) |
| `order_by` | `lowest_mileage` |
| `include_trips` | `true` adds flight-level `AvailabilityTrips[]` (segments, seats, taxes) without extra calls |
| `only_direct_flights` | restrict to nonstop |
| `include_filtered` | include dynamically-priced results normally filtered out |
| `minify_trips` | fewer trip fields |

Unverified: the maximum number of comma-separated airports and the maximum date
span per call are not documented in the sources available. The app chunks
requests (default 10 airports and 45 days per call, both adjustable in Search →
API efficiency) and records a coverage row for every chunk.

Response shape: `{ data: [...], count, hasMore, cursor, moreURL }`. Each `data[]`
row is an **Availability** object for one route/date/program with cabin-prefixed
fields: `JAvailable`, `JDirect`, `JMileageCost` (string), `JMileageCostRaw` (int),
`JRemainingSeats`, `JAirlines`, `JTotalTaxes` (minor units; **not reported by every
program**), `TaxesCurrency`, `Route{OriginAirport, DestinationAirport, OriginRegion,
DestinationRegion, Distance, Source}`, `Date`, `ParsedDate`, `Source`, `UpdatedAt`
(the observation timestamp), `AvailabilityTrips[]` when `include_trips=true`.

Interpretation rules implemented in `award_hunter/normalization.py`:

* `JRemainingSeats == 0` while `JAvailable == true` means the program does **not
  publish seat counts**, not that zero seats exist → `seat_confidence = not_reported`.
  The app never counts these as two confirmed seats; they are hidden by default
  and can be included (flagged) via a checkbox.
* Missing `JTotalTaxes` → fees shown as "Not provided"; the score treats unknown
  fees as a mild negative (never a bonus).
* Trip `DepartsAt`/`ArrivesAt` are **local airport time** strings; `UpdatedAt` is UTC.
* `Cabin` on each segment is used to detect mixed-cabin itineraries; when segment
  cabins are absent the itinerary is marked "unknown", not "mixed" and not "all business".

### `GET /trips/{id}`
Flight-level detail for one Availability ID, plus `booking_links[]`. One call per
award; the app prefers `include_trips=true` on `/search` to avoid spending quota.

### `GET /availability?source=`
Bulk cached availability for one program (optionally `cabin`, `start_date`,
`end_date`, `origin_region`, `destination_region`, pagination). Wrapped but not used
by the default scan.

### `GET /routes?source=`
Routes tracked for one program. Wrapped; useful for future positioning features.

## Program (source) codes seen in the API

`eurobonus, virginatlantic, aeromexico, american, delta, etihad, united, emirates,
aeroplan, alaska, velocity, qantas, connectmiles, azul, smiles, flyingblue, jetblue,
qatar, turkish, singapore, ethiopian, saudia, finnair, lufthansa, frontier, spirit`

Of these, the app treats the following as U.S. Amex Membership Rewards partners
(editable in Settings → Transfer partners): aeroplan, flyingblue, virginatlantic,
emirates, etihad, singapore, qantas, qatar, delta, jetblue (250→200), aeromexico
(1000→1600). Avianca LifeMiles, British Airways, Iberia, Cathay and ANA are Amex
partners but were **not** Seats.aero sources at the time of writing; they are listed
as inactive so you can enable them if Seats.aero adds them.

## Known limitations (handled honestly in the UI)

* All data is cached; "Observed" shows when Seats.aero last saw the award.
* Seat counts and taxes are program-dependent.
* No live search on Pro: every award says "requires airline verification".
* Default scan ≈ 20–40 calls for 90 days × 4 origins × 19 destinations × 2 directions,
  well under the 1,000/day quota. The app refuses to exceed `API_DAILY_BUDGET`.
