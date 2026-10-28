"""Watchlist: "tell me the moment two business seats open up for THIS trip".

A watch is a destination set (airports and/or countries), an outbound date window,
optional caps, and a nonstop flag. Unlike the main deal ranking, a watch ignores
the score: any award with the requested seats, in the window, to one of the
destinations, triggers an alert the first time it is seen. Watches also widen the
daily search so their dates and airports are actually scanned.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from .airports import EUROPEAN_AIRPORTS, country_of, destinations_for_countries
from .models import ScoredAward
from .search import OUTBOUND, SearchProfile, direction_of

MAX_WINDOW_DAYS = 330  # Seats.aero cached availability rarely goes beyond ~11 months


@dataclass
class Watch:
    id: int | None
    name: str
    destinations: list[str]          # airport codes (resolved from countries at creation time too)
    date_from: date                  # earliest outbound departure
    date_to: date                    # latest outbound departure
    passengers: int = 2
    nonstop_only: bool = False
    max_points_pp: int | None = None  # Amex points per person one way; None = any
    max_cash_pp_usd: float | None = None
    include_returns: bool = True     # also alert on Europe -> home seats in the return window
    min_trip_days: int = 7
    max_trip_days: int = 14
    active: bool = True
    notes: str = ""
    created_at: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def return_from(self) -> date:
        return self.date_from + timedelta(days=self.min_trip_days)

    @property
    def return_to(self) -> date:
        return self.date_to + timedelta(days=self.max_trip_days)

    def describe(self) -> str:
        where = ", ".join(sorted({country_of(d) or d for d in self.destinations}))
        return f"{where} · depart {self.date_from:%b %-d}–{self.date_to:%b %-d, %Y}" + (" · nonstop" if self.nonstop_only else "")

    @classmethod
    def from_row(cls, row: dict) -> "Watch":
        return cls(
            id=int(row["id"]), name=row["name"], destinations=json.loads(row["destinations_json"] or "[]"),
            date_from=date.fromisoformat(row["date_from"]), date_to=date.fromisoformat(row["date_to"]),
            passengers=int(row.get("passengers") or 2), nonstop_only=bool(row.get("nonstop_only")),
            max_points_pp=row.get("max_points_pp"), max_cash_pp_usd=row.get("max_cash_pp_usd"),
            include_returns=bool(row.get("include_returns", 1)), min_trip_days=int(row.get("min_trip_days") or 7),
            max_trip_days=int(row.get("max_trip_days") or 14), active=bool(row.get("active", 1)), notes=row.get("notes") or "",
            created_at=row.get("created_at") or "",
        )


def resolve_destinations(airports: list[str], countries: list[str]) -> list[str]:
    codes = {a.upper() for a in airports if a}
    codes |= set(destinations_for_countries(countries)) if countries else set()
    return sorted(c for c in codes if c in EUROPEAN_AIRPORTS)


def _stops(award) -> int | None:
    best = award.best_option()
    if best and best.stops is not None:
        return best.stops
    return 0 if award.direct_available else None


def award_matches(watch: Watch, s: ScoredAward, cfg=None) -> bool:
    """Seats, dates and destination are what matter; score and the main profile's caps do not."""
    a = s.award
    if (a.remaining_seats or 0) < watch.passengers:
        return False
    outbound = direction_of(a) == OUTBOUND
    if outbound:
        if a.destination not in watch.destinations or not (watch.date_from <= a.travel_date <= watch.date_to):
            return False
    else:
        if not watch.include_returns or a.origin not in watch.destinations:
            return False
        if not (watch.return_from <= a.travel_date <= watch.return_to):
            return False
    if watch.nonstop_only and _stops(a) != 0:
        return False
    if watch.max_points_pp is not None:
        pts = s.amex.amex_points_per_passenger
        if pts is None or pts > watch.max_points_pp:
            return False
    if watch.max_cash_pp_usd is not None and a.fees_known and a.taxes_major is not None:
        from .scoring import ScoringConfig, fees_to_usd
        usd = fees_to_usd(a.taxes_minor_units, a.taxes_currency, cfg or ScoringConfig())
        if usd is not None and usd > watch.max_cash_pp_usd:
            return False
    return True


def match_watches(watches: list[Watch], awards: list[ScoredAward], cfg=None) -> dict[int, list[ScoredAward]]:
    out: dict[int, list[ScoredAward]] = {}
    for w in watches:
        if not w.active or w.id is None:
            continue
        hits = [s for s in awards if award_matches(w, s, cfg)]
        if hits:
            hits.sort(key=lambda s: (s.award.travel_date, s.amex.amex_points_per_passenger or 10**9))
            out[w.id] = hits
    return out


def profile_with_watches(profile: SearchProfile, watches: list[Watch], today: date | None = None) -> SearchProfile:
    """Widen the search so every active watch's dates and airports are covered. Never narrows anything."""
    today = today or date.today()
    active = [w for w in watches if w.active and w.date_to >= today]
    if not active:
        return profile
    start = profile.start_date
    end = profile.end_date
    dests = list(profile.destinations)
    for w in active:
        start = min(start, max(w.date_from, today + timedelta(days=1)))
        end = max(end, w.date_to)
        for d in w.destinations:
            if d not in dests:
                dests.append(d)
    end = min(end, today + timedelta(days=MAX_WINDOW_DAYS))
    window = max(1, (end - start).days + 1)
    excluded = [c for c in profile.excluded_destination_countries
                if not any(country_of(d) == c for w in active for d in w.destinations)]
    return profile.model_copy(update={"start_date": start, "window_days": window, "destinations": dests,
                                      "excluded_destination_countries": excluded})
