"""Full date-range, multi-airport, multi-program business-class search.

A :class:`SearchProfile` is expanded into a :class:`SearchPlan` of ``/search``
query chunks (origins x destinations x date window). Each chunk is paginated,
normalized, converted to Amex points, scored and filtered. Every chunk gets a
coverage record so the UI can tell "searched, nothing found" from "not searched".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

from pydantic import BaseModel, Field

from .airports import (
    DEFAULT_DESTINATIONS,
    DEFAULT_EXCLUDED_DESTINATION_COUNTRIES,
    DEFAULT_PRIMARY_ORIGINS,
    DEFAULT_SECONDARY_ORIGINS,
    US_ORIGINS,
    country_of,
    filter_excluded_countries,
)
from .api_client import ApiAuthError, ApiError, BudgetExhaustedError, RateLimitError, SeatsAeroClient
from .models import Cabin, AwardObservation, ScoredAward, SeatConfidence
from .normalization import normalize_many
from .scoring import ScoringConfig, fees_to_usd, score_award
from .transfer_bonuses import TransferEngine
from .transfer_partners import DEFAULT_SEARCH_SOURCES, sweet_spot

log = logging.getLogger(__name__)

OUTBOUND = "outbound"  # US -> Europe
INBOUND = "inbound"  # Europe -> US


class SearchProfile(BaseModel):
    """User-editable travel preferences (persisted as JSON in app_settings)."""

    origins: list[str] = Field(default_factory=lambda: DEFAULT_PRIMARY_ORIGINS + DEFAULT_SECONDARY_ORIGINS)
    home_airport: str | None = None  # where you live; defaults to the first origin. Drives positioning-cost estimates.
    destinations: list[str] = Field(default_factory=lambda: list(DEFAULT_DESTINATIONS))
    excluded_destination_countries: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDED_DESTINATION_COUNTRIES))
    excluded_connection_airports: list[str] = Field(default_factory=list)
    excluded_airlines: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=lambda: list(DEFAULT_SEARCH_SOURCES))

    start_date: date = Field(default_factory=lambda: date.today() + timedelta(days=14))
    window_days: int = 90
    passengers: int = 2
    cabin: str = "business"
    include_premium_economy: bool = False  # also collect premium-economy awards as a fallback (shown with a badge)

    min_seats: int = 2
    include_unknown_seat_counts: bool = False
    max_amex_points_per_passenger: int = 90_000
    preferred_max_amex_points_per_passenger: int = 60_000
    max_fees_per_passenger_usd: float | None = 700.0
    max_stops: int = 2
    hide_mixed_cabin: bool = True
    min_score: float = 70.0
    # Observations older than this are shown in the watchlist but never qualify (7 days).
    max_observation_age_hours: float = 168.0

    min_trip_days: int = 7
    max_trip_days: int = 14
    include_open_jaw: bool = True
    include_return_search: bool = True

    # API efficiency knobs
    max_airports_per_query: int = 10
    max_days_per_query: int = 45
    max_pages_per_query: int = 10
    include_trips: bool = True
    cache_ttl_seconds: int = 0

    @property
    def end_date(self) -> date:
        return self.start_date + timedelta(days=max(self.window_days, 1) - 1)

    def effective_destinations(self) -> list[str]:
        return filter_excluded_countries([d.upper() for d in self.destinations], self.excluded_destination_countries)

    def effective_origins(self) -> list[str]:
        return [o.upper() for o in self.origins]


@dataclass
class QueryChunk:
    direction: str
    origins: list[str]
    destinations: list[str]
    start: date
    end: date
    sources: list[str]

    def describe(self) -> str:
        return f"{self.direction}: {','.join(self.origins)} -> {','.join(self.destinations)} {self.start}..{self.end}"


@dataclass
class SearchPlan:
    chunks: list[QueryChunk]

    def estimated_calls(self, pages_per_chunk: int = 2) -> int:
        return len(self.chunks) * pages_per_chunk


def _batches(items: list[str], size: int) -> list[list[str]]:
    size = max(1, size)
    return [items[i:i + size] for i in range(0, len(items), size)] or []


def _date_windows(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=max(max_days, 1) - 1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def build_plan(profile: SearchProfile) -> SearchPlan:
    origins = profile.effective_origins()
    dests = profile.effective_destinations()
    chunks: list[QueryChunk] = []
    if not origins or not dests:
        return SearchPlan(chunks)
    for win_start, win_end in _date_windows(profile.start_date, profile.end_date, profile.max_days_per_query):
        for o_batch in _batches(origins, profile.max_airports_per_query):
            for d_batch in _batches(dests, profile.max_airports_per_query):
                chunks.append(QueryChunk(OUTBOUND, o_batch, d_batch, win_start, win_end, list(profile.sources)))
    if profile.include_return_search:
        # Returns can depart up to max_trip_days after the last outbound date.
        ret_start = profile.start_date + timedelta(days=profile.min_trip_days)
        ret_end = profile.end_date + timedelta(days=profile.max_trip_days)
        for win_start, win_end in _date_windows(ret_start, ret_end, profile.max_days_per_query):
            for d_batch in _batches(dests, profile.max_airports_per_query):
                for o_batch in _batches(origins, profile.max_airports_per_query):
                    chunks.append(QueryChunk(INBOUND, d_batch, o_batch, win_start, win_end, list(profile.sources)))
    return SearchPlan(chunks)


# --------------------------------------------------------------------------- #
# Qualification
# --------------------------------------------------------------------------- #


def direction_of(award: AwardObservation) -> str:
    return OUTBOUND if award.origin in US_ORIGINS or (award.origin_region or "") == "North America" else INBOUND


def qualifies(award: AwardObservation, scored_points: int | None, is_partner: bool, profile: SearchProfile,
              cfg: ScoringConfig) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not is_partner:
        reasons.append("not_amex_partner")
    if scored_points is None:
        reasons.append("points_unknown")
    elif scored_points > profile.max_amex_points_per_passenger:
        reasons.append(f"points_over_max({scored_points:,}>{profile.max_amex_points_per_passenger:,})")
    if award.seat_confidence == SeatConfidence.REPORTED:
        if (award.remaining_seats or 0) < profile.min_seats:
            reasons.append(f"seats_reported_below_min({award.remaining_seats})")
    elif not profile.include_unknown_seat_counts:
        reasons.append("seat_count_not_reported")
    best = award.best_option()
    if best is not None:
        if best.stops is not None and best.stops > profile.max_stops:
            reasons.append(f"too_many_stops({best.stops})")
        if profile.hide_mixed_cabin and best.mixed_cabin:
            reasons.append("mixed_cabin")
        excluded_conns = {c.upper() for c in profile.excluded_connection_airports}
        if excluded_conns and any(c.upper() in excluded_conns for c in best.connection_airports):
            reasons.append("excluded_connection_airport")
    if profile.excluded_airlines and award.airlines:
        codes = {c.strip().upper() for c in award.airlines.replace(",", " ").split()}
        if codes & {a.upper() for a in profile.excluded_airlines}:
            reasons.append("excluded_airline")
    age = award.data_age_hours
    if age is not None and profile.max_observation_age_hours and age > profile.max_observation_age_hours:
        reasons.append(f"stale_observation({age / 24:.0f}d)")
    if profile.max_fees_per_passenger_usd is not None and award.fees_known:
        usd = fees_to_usd(award.taxes_minor_units, award.taxes_currency, cfg)
        # Profile cap is round-trip per person; apply half to a one-way leg.
        if usd is not None and usd > profile.max_fees_per_passenger_usd / 2 * 1.5:
            reasons.append(f"fees_high(${usd:,.0f})")
    # Destination-country exclusion applies to the European end of the trip.
    eu_airport = award.destination if direction_of(award) == OUTBOUND else award.origin
    if (country_of(eu_airport) or "") in set(profile.excluded_destination_countries):
        reasons.append("excluded_country")
    return (not reasons), reasons


def score_and_filter(awards: Iterable[AwardObservation], profile: SearchProfile, engine: TransferEngine,
                     cfg: ScoringConfig) -> tuple[list[ScoredAward], list[ScoredAward]]:
    """Return (qualifying, non_qualifying) scored awards."""
    good: list[ScoredAward] = []
    bad: list[ScoredAward] = []
    for a in awards:
        amex = engine.calculate(a.source, a.miles_per_passenger, profile.passengers)
        sc = score_award(a, amex, cfg)
        ok, reasons = qualifies(a, amex.amex_points_per_passenger, amex.is_amex_partner, profile, cfg)
        positioning = direction_of(a) == OUTBOUND and a.origin not in {o.upper() for o in profile.origins}
        scored = ScoredAward(award=a, amex=amex, score=sc, positioning_required=positioning, hidden_reasons=reasons)
        (good if ok else bad).append(scored)
    good.sort(key=lambda s: (-s.score.total, s.amex.amex_points_per_passenger or 10**9))
    return good, bad


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


@dataclass
class CoverageRecord:
    chunk: QueryChunk
    status: str  # ok | empty | error | budget | skipped
    records: int = 0
    pages: int = 0
    error: str | None = None


@dataclass
class SearchResult:
    run_id: int | None
    profile: SearchProfile
    outbound: list[ScoredAward] = field(default_factory=list)
    inbound: list[ScoredAward] = field(default_factory=list)
    non_qualifying: list[ScoredAward] = field(default_factory=list)
    coverage: list[CoverageRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    calls_made: int = 0
    records_seen: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    aborted: bool = False

    @property
    def qualifying(self) -> list[ScoredAward]:
        return self.outbound + self.inbound

    def coverage_summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.coverage:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def searched_dates(self, direction: str) -> set[date]:
        days: set[date] = set()
        for c in self.coverage:
            if c.chunk.direction == direction and c.status in ("ok", "empty"):
                d = c.chunk.start
                while d <= c.chunk.end:
                    days.add(d)
                    d += timedelta(days=1)
        return days

    def failed_dates(self, direction: str) -> set[date]:
        days: set[date] = set()
        for c in self.coverage:
            if c.chunk.direction == direction and c.status not in ("ok", "empty"):
                d = c.chunk.start
                while d <= c.chunk.end:
                    days.add(d)
                    d += timedelta(days=1)
        return days


def run_search(
    client: SeatsAeroClient,
    profile: SearchProfile,
    engine: TransferEngine,
    *,
    cfg: ScoringConfig | None = None,
    db=None,
    trigger: str = "manual",
    progress: Callable[[int, int, str], None] | None = None,
) -> SearchResult:
    cfg = cfg or ScoringConfig(passengers=profile.passengers)
    plan = build_plan(profile)
    run_id = db.start_run(trigger, profile.model_dump(mode="json")) if db is not None else None
    result = SearchResult(run_id=run_id, profile=profile)
    retrieved_at = datetime.now(timezone.utc)
    all_awards: dict[str, AwardObservation] = {}
    total = len(plan.chunks)
    budget_hit = False
    cabins_req = [profile.cabin] + (["premium"] if profile.include_premium_economy else [])
    cabins_norm = (Cabin.BUSINESS,) + ((Cabin.PREMIUM,) if profile.include_premium_economy else ())

    for i, chunk in enumerate(plan.chunks, start=1):
        if progress:
            progress(i, total, chunk.describe())
        if budget_hit:
            result.coverage.append(CoverageRecord(chunk, "skipped", error="daily budget exhausted"))
            continue
        pages = 0
        records = 0
        try:
            for page in client.iter_search(
                chunk.origins, chunk.destinations, chunk.start, chunk.end,
                cabins=cabins_req, sources=chunk.sources or None, include_trips=profile.include_trips,
                order_by="lowest_mileage", max_pages=profile.max_pages_per_query, cache_ttl=profile.cache_ttl_seconds,
            ):
                pages += 1
                result.calls_made += 1
                records += len(page.data)
                for obs in normalize_many(page.data, retrieved_at=retrieved_at, min_seats=profile.min_seats, cabins=cabins_norm):
                    # Keep the row inside the requested date window (API returns full-range rows per chunk).
                    if chunk.start <= obs.travel_date <= chunk.end:
                        all_awards[obs.availability_id] = obs
            result.records_seen += records
            result.coverage.append(CoverageRecord(chunk, "ok" if records else "empty", records, pages))
        except BudgetExhaustedError as exc:
            budget_hit = True
            result.errors.append(str(exc))
            result.coverage.append(CoverageRecord(chunk, "budget", records, pages, str(exc)))
        except ApiAuthError as exc:
            result.errors.append(str(exc))
            result.coverage.append(CoverageRecord(chunk, "error", records, pages, str(exc)))
            result.aborted = True
            for rest in plan.chunks[i:]:
                result.coverage.append(CoverageRecord(rest, "skipped", error="aborted after auth error"))
            break
        except RateLimitError as exc:
            budget_hit = True
            result.errors.append(str(exc))
            result.coverage.append(CoverageRecord(chunk, "error", records, pages, str(exc)))
        except ApiError as exc:
            result.errors.append(f"{chunk.describe()}: {exc}")
            result.coverage.append(CoverageRecord(chunk, "error", records, pages, str(exc)))
        except Exception as exc:  # noqa: BLE001 - keep scanning other chunks
            log.exception("Unexpected error in chunk %s", chunk.describe())
            result.errors.append(f"{chunk.describe()}: {type(exc).__name__}: {exc}")
            result.coverage.append(CoverageRecord(chunk, "error", records, pages, f"{type(exc).__name__}: {exc}"))

    good, bad = score_and_filter(all_awards.values(), profile, engine, cfg)
    result.outbound = [s for s in good if direction_of(s.award) == OUTBOUND]
    result.inbound = [s for s in good if direction_of(s.award) == INBOUND]
    result.non_qualifying = bad
    result.finished_at = datetime.now(timezone.utc)

    if db is not None:
        for c in result.coverage:
            db.record_coverage(run_id, c.chunk.direction, c.chunk.origins, c.chunk.destinations, c.chunk.start.isoformat(),
                               c.chunk.end.isoformat(), c.chunk.sources, c.status, c.records, c.pages, c.error)
    return result


def to_rows(scored: Iterable[ScoredAward], cfg: ScoringConfig | None = None) -> list[dict]:
    """Flatten scored awards for tables / CSV."""
    cfg = cfg or ScoringConfig()
    rows: list[dict] = []
    for s in scored:
        a, best = s.award, s.award.best_option()
        fee_usd = fees_to_usd(a.taxes_minor_units, a.taxes_currency, cfg) if a.fees_known else None
        rows.append({
            "date": a.travel_date.isoformat(),
            "route": f"{a.origin}-{a.destination}",
            "origin": a.origin,
            "destination": a.destination,
            "direction": direction_of(a),
            "airlines": a.airlines or (best.carriers if best else None) or "Not provided",
            "program": a.program_name,
            "source": a.source,
            "cabin": a.cabin.value,
            "seats_reported": a.remaining_seats if a.seat_confidence == SeatConfidence.REPORTED else None,
            "seat_status": a.seat_confidence.value,
            "miles_pp": a.miles_per_passenger,
            "amex_pp": s.amex.amex_points_per_passenger,
            "amex_two": s.amex.amex_points_two_passengers,
            "bonus_pct": s.amex.bonus_percent,
            "fees_pp": round(a.taxes_major, 2) if a.taxes_major is not None else None,
            "fees_currency": a.taxes_currency,
            "fees_pp_usd": round(fee_usd, 2) if fee_usd is not None else None,
            "stops": best.stops if best and best.stops is not None else (0 if a.direct_available else None),
            "duration_min": best.total_duration_minutes if best else None,
            "flight_numbers": best.flight_numbers if best else None,
            "connections": ",".join(best.connection_airports) if best else None,
            "mixed_cabin": best.mixed_cabin if best else None,
            "score": s.score.total,
            "tier": s.score.tier,
            "sweet_spot": sweet_spot(a.source, a.miles_per_passenger),
            "observed_utc": a.source_updated_at_utc.isoformat() if a.source_updated_at_utc else None,
            "age_hours": round(a.data_age_hours, 1) if a.data_age_hours is not None else None,
            "verification": a.verification.value,
            "availability_id": a.availability_id,
            "hidden_reasons": ";".join(s.hidden_reasons),
        })
    return rows
