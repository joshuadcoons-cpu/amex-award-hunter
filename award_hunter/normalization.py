"""Turn raw Seats.aero payloads into validated :class:`AwardObservation` records.

Rules (all deliberate, see spec Part 5.5 / Part 6):
* Only the business cabin (``J*`` fields) is normalized for this app.
* ``JRemainingSeats`` of ``0``/``None`` while ``JAvailable`` is true means the program
  did not report a seat count -> ``seat_confidence = not_reported``. We never turn
  that into "2 seats available".
* ``JTotalTaxes`` absent or zero -> ``fees_known = False`` and taxes shown as "Not provided".
* Times inside trips are *local airport time* strings; they are kept verbatim.
* ``UpdatedAt`` is the Seats.aero observation time (UTC) and drives data age.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from typing import Iterable

from .models import (
    AwardObservation,
    Cabin,
    FlightOption,
    RawAvailability,
    RawSegment,
    RawTrip,
    SeatConfidence,
    Segment,
)
from .transfer_partners import program_name

log = logging.getLogger(__name__)

_CABIN_MAP = {
    "economy": Cabin.ECONOMY, "y": Cabin.ECONOMY,
    "premium": Cabin.PREMIUM, "premium economy": Cabin.PREMIUM, "w": Cabin.PREMIUM,
    "business": Cabin.BUSINESS, "j": Cabin.BUSINESS,
    "first": Cabin.FIRST, "f": Cabin.FIRST,
}


def parse_cabin(value: str | None) -> Cabin:
    if not value:
        return Cabin.UNKNOWN
    return _CABIN_MAP.get(value.strip().lower(), Cabin.UNKNOWN)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_local(value: str | None) -> datetime | None:
    """Parse an airport-local timestamp, dropping any offset (kept naive)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _mileage_int(raw: int | None, display: str | None) -> int | None:
    if raw is not None and raw > 0:
        return int(raw)
    if display:
        digits = "".join(ch for ch in display if ch.isdigit())
        if digits:
            val = int(digits)
            return val if val > 0 else None
    return None


def _seat_confidence(available: bool | None, seats: int | None) -> tuple[int | None, SeatConfidence]:
    if not available:
        return seats, SeatConfidence.UNKNOWN
    if seats is None or seats <= 0:
        return None, SeatConfidence.NOT_REPORTED
    return seats, SeatConfidence.REPORTED


def normalize_segment(seg: RawSegment) -> Segment:
    return Segment(
        flight_number=seg.FlightNumber,
        origin=seg.OriginAirport,
        destination=seg.DestinationAirport,
        departs_at_local=seg.DepartsAt,
        arrives_at_local=seg.ArrivesAt,
        aircraft_name=seg.AircraftName,
        aircraft_code=seg.AircraftCode,
        cabin=parse_cabin(seg.Cabin),
        fare_class=seg.FareClass,
        distance_miles=seg.Distance,
    )


def _layover_stats(segments: list[Segment]) -> tuple[int | None, bool | None]:
    """Return (longest layover minutes, overnight flag) from consecutive segments."""
    longest: int | None = None
    overnight: bool | None = None
    for a, b in zip(segments, segments[1:]):
        arr = parse_local(a.arrives_at_local)
        dep = parse_local(b.departs_at_local)
        if arr is None or dep is None:
            continue
        gap = int((dep - arr).total_seconds() // 60)
        if gap < 0:
            continue
        longest = gap if longest is None else max(longest, gap)
        crosses_night = dep.date() != arr.date() and (gap >= 6 * 60 or dep.hour < 9)
        overnight = bool(overnight) or crosses_night
    if longest is not None and overnight is None:
        overnight = False
    return longest, overnight


CABIN_PREFIX = {Cabin.BUSINESS: "J", Cabin.PREMIUM: "W"}


def normalize_trip(trip: RawTrip, availability_id: str, target: Cabin = Cabin.BUSINESS) -> FlightOption:
    segments = sorted(
        (normalize_segment(s) for s in (trip.AvailabilitySegments or [])),
        key=lambda s: (s.departs_at_local or ""),
    )
    connection_airports = [s.destination for s in segments[:-1] if s.destination]
    cabins = [s.cabin for s in segments if s.cabin != Cabin.UNKNOWN]
    mixed: bool | None
    if segments and len(cabins) == len(segments):
        mixed = any(c != target for c in cabins)
    else:
        mixed = None
    longest, overnight = _layover_stats(segments)
    stops = trip.Stops if trip.Stops is not None else (len(segments) - 1 if segments else None)
    return FlightOption(
        trip_id=trip.ID,
        availability_id=availability_id,
        flight_numbers=trip.FlightNumbers,
        carriers=trip.Carriers,
        stops=stops,
        total_duration_minutes=trip.TotalDuration,
        departs_at_local=trip.DepartsAt,
        arrives_at_local=trip.ArrivesAt,
        cabin=parse_cabin(trip.Cabin),
        mileage_cost=trip.MileageCost,
        remaining_seats=trip.RemainingSeats if (trip.RemainingSeats or 0) > 0 else None,
        taxes_minor_units=trip.TotalTaxes,
        taxes_currency=trip.TaxesCurrency,
        segments=segments,
        connection_airports=connection_airports,
        mixed_cabin=mixed,
        overnight_layover=overnight,
        longest_layover_minutes=longest,
        updated_at_utc=parse_utc(trip.UpdatedAt),
        filtered=trip.Filtered,
    )


def _choose_best_option(options: list[FlightOption], min_seats: int, target: Cabin = Cabin.BUSINESS) -> int | None:
    """Pick the cheapest option in the target cabin that is not mixed-cabin; prefer fewer stops."""
    if not options:
        return None

    def key(idx_opt: tuple[int, FlightOption]) -> tuple:
        _, o = idx_opt
        cabin_ok = 0 if o.cabin in (target, Cabin.UNKNOWN) else 1
        mixed = 1 if o.mixed_cabin else 0
        seats_ok = 0 if (o.remaining_seats or 0) >= min_seats else 1
        return (cabin_ok, mixed, seats_ok, o.mileage_cost or 10**9, o.stops if o.stops is not None else 9,
                o.total_duration_minutes or 10**6)

    return min(enumerate(options), key=key)[0]


def normalize_availability(
    raw: RawAvailability,
    *,
    retrieved_at: datetime | None = None,
    endpoint: str = "/search",
    min_seats: int = 2,
    cabin: Cabin = Cabin.BUSINESS,
) -> AwardObservation | None:
    """Normalize one availability row for one cabin (business by default, premium economy on request).

    Returns None when that cabin is not available. A premium-economy observation gets the
    availability id suffixed with ``:W`` so both cabins from one row can coexist in storage.
    """
    pfx = CABIN_PREFIX.get(cabin, "J")
    g = lambda name: getattr(raw, f"{pfx}{name}", None)  # noqa: E731
    if not g("Available"):
        return None
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    travel_date = parse_date(raw.ParsedDate) or parse_date(raw.Date)
    if travel_date is None:
        log.warning("Skipping availability %s: unparseable date %r", raw.ID, raw.Date)
        return None

    missing: list[str] = []
    miles = _mileage_int(g("MileageCostRaw"), g("MileageCost"))
    if miles is None:
        missing.append("miles")
    seats, confidence = _seat_confidence(g("Available"), g("RemainingSeats"))
    if confidence != SeatConfidence.REPORTED:
        missing.append("seat_count")
    taxes = g("TotalTaxes")
    # A zero total is treated as "not provided": government taxes alone make a $0 transatlantic
    # business award impossible, and a cash-first ranking must never reward a placeholder zero.
    fees_known = taxes is not None and taxes > 0
    if not fees_known:
        missing.append("taxes")
    if not g("Airlines"):
        missing.append("airlines")

    avail_id = raw.ID if cabin == Cabin.BUSINESS else f"{raw.ID}:{pfx}"
    options = [normalize_trip(t, avail_id, cabin) for t in (raw.AvailabilityTrips or [])]
    # Trip-level records are authoritative for the cabin; keep only matching (or unknown-cabin) trips.
    options = [o for o in options if o.cabin in (cabin, Cabin.UNKNOWN)]
    best_idx = _choose_best_option(options, min_seats, cabin)
    if options:
        best = options[best_idx]  # type: ignore[index]
        if miles is None and best.mileage_cost:
            miles = best.mileage_cost
            missing = [m for m in missing if m != "miles"]
        if not fees_known and best.taxes_minor_units is not None and best.taxes_minor_units > 0:
            taxes, fees_known = best.taxes_minor_units, True
            missing = [m for m in missing if m != "taxes"]
        if confidence != SeatConfidence.REPORTED and best.remaining_seats:
            seats, confidence = best.remaining_seats, SeatConfidence.REPORTED
            missing = [m for m in missing if m != "seat_count"]
    else:
        missing.append("flight_details")

    source = (raw.Source or raw.Route.Source or "unknown").lower()
    internal_id = hashlib.sha1(f"{raw.ID}|{source}|{cabin.value}".encode()).hexdigest()[:16]
    currency = raw.TaxesCurrency or (options[best_idx].taxes_currency if options and best_idx is not None else None)

    return AwardObservation(
        internal_id=internal_id,
        availability_id=avail_id,
        route_id=raw.RouteID or raw.Route.ID,
        source=source,
        program_name=program_name(source),
        provider_endpoint=endpoint,
        origin=raw.Route.OriginAirport.upper(),
        destination=raw.Route.DestinationAirport.upper(),
        origin_region=raw.Route.OriginRegion,
        destination_region=raw.Route.DestinationRegion,
        travel_date=travel_date,
        cabin=cabin,
        direct_available=g("Direct"),
        airlines=g("Airlines"),
        distance_miles=raw.Route.Distance,
        miles_per_passenger=miles,
        miles_display=g("MileageCost"),
        remaining_seats=seats,
        seat_confidence=confidence,
        taxes_minor_units=taxes if fees_known else None,
        taxes_currency=currency if fees_known else None,
        fees_known=fees_known,
        flight_options=options,
        best_option_index=best_idx,
        source_updated_at_utc=parse_utc(raw.UpdatedAt),
        retrieved_at_utc=retrieved_at,
        missing_fields=missing,
    )


def normalize_many(
    rows: Iterable[RawAvailability], *, retrieved_at: datetime | None = None, endpoint: str = "/search", min_seats: int = 2,
    cabins: tuple[Cabin, ...] = (Cabin.BUSINESS,),
) -> list[AwardObservation]:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    out: list[AwardObservation] = []
    seen: set[str] = set()
    for row in rows:
        if row.ID in seen:
            continue
        seen.add(row.ID)
        for cabin in cabins:
            obs = normalize_availability(row, retrieved_at=retrieved_at, endpoint=endpoint, min_seats=min_seats, cabin=cabin)
            if obs is not None:
                out.append(obs)
    return out
