"""Pair outbound (US->Europe) and inbound (Europe->US) awards into complete round trips.

Conventions
* Trip length = inbound departure date minus outbound departure date, in local
  travel dates (the API's ``Date`` field). A 7-14 day window is the default.
* Open-jaw Europe: inbound origin metro differs from outbound destination metro.
  Allowed, flagged, and lightly penalized; the cost of getting between cities is
  NOT estimated.
* Open-jaw U.S.: inbound destination metro differs from outbound origin metro.
* Fees are summed only when every leg reports fees; otherwise ``fees_complete``
  is False and the total is shown as partial.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from .airports import city_of, country_of, metro_of
from .models import RoundTrip, ScoredAward
from .scoring import ScoringConfig, fees_to_usd, score_round_trip
from .search import SearchProfile

log = logging.getLogger(__name__)


def _leg_fees_usd(s: ScoredAward, cfg: ScoringConfig) -> float | None:
    a = s.award
    if not a.fees_known:
        return None
    return fees_to_usd(a.taxes_minor_units, a.taxes_currency, cfg)


def build_round_trip(out: ScoredAward, inb: ScoredAward, profile: SearchProfile, cfg: ScoringConfig) -> RoundTrip | None:
    o, i = out.award, inb.award
    days = (i.travel_date - o.travel_date).days
    if days < profile.min_trip_days or days > profile.max_trip_days:
        return None
    if i.travel_date <= o.travel_date:
        return None
    warnings: list[str] = []
    open_jaw_eu = metro_of(o.destination) != metro_of(i.origin)
    open_jaw_us = metro_of(o.origin) != metro_of(i.destination)
    if open_jaw_eu and not profile.include_open_jaw:
        return None
    if open_jaw_eu:
        same_country = country_of(o.destination) == country_of(i.origin)
        warnings.append(
            f"Open-jaw: arrive {city_of(o.destination)}, depart {city_of(i.origin)}"
            + ("" if same_country else " (different countries)") + ". Transport between cities not included.")
    if open_jaw_us:
        warnings.append(f"Returns to {i.destination}, departed from {o.origin}: positioning required.")
    if out.positioning_required:
        warnings.append(f"Outbound departs {o.origin}, which is not one of your home airports.")
    for leg_name, leg in (("Outbound", out), ("Return", inb)):
        best = leg.award.best_option()
        if best and best.mixed_cabin:
            warnings.append(f"{leg_name} is mixed cabin.")
        if leg.award.seat_confidence.value != "reported":
            warnings.append(f"{leg_name}: seat count not reported; 2 seats unconfirmed.")
        elif (leg.award.remaining_seats or 0) < profile.passengers:
            return None

    pts_out, pts_in = out.amex.amex_points_two_passengers, inb.amex.amex_points_two_passengers
    total_points = pts_out + pts_in if pts_out is not None and pts_in is not None else None
    fo, fi = _leg_fees_usd(out, cfg), _leg_fees_usd(inb, cfg)
    fees_complete = fo is not None and fi is not None
    total_fees = ((fo or 0) + (fi or 0)) * profile.passengers if (fo is not None or fi is not None) else None
    if not fees_complete:
        warnings.append("Fees missing for at least one leg; cash total is partial.")
    score = score_round_trip(out.score, inb.score, total_points_two=total_points, total_fees_usd_two=total_fees,
                             fees_complete=fees_complete, open_jaw_europe=open_jaw_eu, open_jaw_us=open_jaw_us,
                             trip_days=days, cfg=cfg)
    return RoundTrip(
        outbound=out, inbound=inb, trip_days=days, open_jaw_europe=open_jaw_eu, open_jaw_us=open_jaw_us,
        total_amex_points_two=total_points, total_fees_two_major=round(total_fees, 2) if total_fees is not None else None,
        fees_complete=fees_complete, fees_currency="USD" if total_fees is not None else None, score=score, warnings=warnings,
        destination_country=country_of(o.destination), destination_city=city_of(o.destination),
    )


def match_round_trips(outbound: list[ScoredAward], inbound: list[ScoredAward], profile: SearchProfile,
                      cfg: ScoringConfig | None = None, *, limit: int = 500, max_total_points: int | None = None) -> list[RoundTrip]:
    cfg = cfg or ScoringConfig(passengers=profile.passengers)
    by_date: dict = {}
    for s in inbound:
        by_date.setdefault(s.award.travel_date, []).append(s)
    trips: list[RoundTrip] = []
    for out in outbound:
        for d in range(profile.min_trip_days, profile.max_trip_days + 1):
            for inb in by_date.get(out.award.travel_date + timedelta(days=d), []):
                rt = build_round_trip(out, inb, profile, cfg)
                if rt is None:
                    continue
                if max_total_points is not None and rt.total_amex_points_two is not None and rt.total_amex_points_two > max_total_points:
                    continue
                trips.append(rt)
    if cfg.priority == "minimize_cash":
        trips.sort(key=lambda t: (-t.score.total, t.total_fees_two_major if t.fees_complete else 10**9, t.total_amex_points_two or 10**9))
    else:
        trips.sort(key=lambda t: (-t.score.total, t.total_amex_points_two or 10**9, t.total_fees_two_major or 10**9))
    return trips[:limit]


def best_per_destination(trips: list[RoundTrip]) -> list[RoundTrip]:
    seen: set[str] = set()
    out: list[RoundTrip] = []
    for t in trips:
        key = f"{t.destination_city}|{t.outbound.award.travel_date}|{t.inbound.award.travel_date}"
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def round_trip_rows(trips: list[RoundTrip]) -> list[dict]:
    rows = []
    for t in trips:
        o, i = t.outbound.award, t.inbound.award
        rows.append({
            "destination": f"{t.destination_city}, {t.destination_country}",
            "outbound": f"{o.origin}->{o.destination} {o.travel_date}",
            "return": f"{i.origin}->{i.destination} {i.travel_date}",
            "days": t.trip_days,
            "programs": f"{t.outbound.amex.program_name} / {t.inbound.amex.program_name}",
            "amex_total_two": t.total_amex_points_two,
            "fees_total_two_usd": t.total_fees_two_major,
            "fees_complete": t.fees_complete,
            "open_jaw": t.open_jaw_europe or t.open_jaw_us,
            "score": t.score.total,
            "tier": t.score.tier,
            "warnings": " | ".join(t.warnings),
            "fingerprint": t.fingerprint,
        })
    return rows
