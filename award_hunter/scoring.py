"""Transparent 0-100 deal scoring.

Weights (configurable via :class:`ScoringWeights`; see ``PRIORITY_PRESETS``):

    default "minimize_cash": points 22 | cash fees 38 | convenience 15 | data confidence 10 | airline/cabin 10 | flexibility 5
    "balanced" (original spec): points 40 | fees 20 | convenience 15 | confidence 10 | quality 10 | flexibility 5

Component formulas (each returns 0..1, multiplied by its weight):

points efficiency (per person, one way, Amex points after transfer):
    <= 40,000 -> 1.00 | 50,000 -> 0.80 | 60,000 -> 0.60 | 70,000 -> 0.40 | 85,000 -> 0.15 | >= 100,000 -> 0
    (piecewise linear between anchors). Non-Amex partner -> 0 and the award is hidden.

cash fees (per person, one way, USD-equivalent when currency known):
    unknown -> 0.45 (missing data is NOT rewarded) | <= $100 -> 1.0 | $200 -> 0.85 | $350 -> 0.55 | $600 -> 0.2 | >= $900 -> 0

convenience: start 1.0; -0.25 per stop beyond nonstop (direct award = 0 stops), -0.2 overnight layover,
    -0.15 layover > 4h, -0.1 when no flight-level detail exists (unknown stops), -0.5 mixed cabin.

data confidence: seat count reported & >= passengers 1.0 | reported but fewer 0.2 | not reported 0.5;
    minus freshness penalty: data age > 24h -0.2, > 72h -0.4; unknown age -0.3.

airline/cabin: 0.7 baseline (business label, lie-flat unverified); +0.3 when the program is a known
    long-haul business product and flights are nonstop; -0.3 when mixed-cabin; unknown cabin segments -0.1.

flexibility: 1.0 when the program is a known Amex partner with a verified ratio and no bonus assumption,
    0.7 when a verified bonus is assumed, 0.4 when an unverified bonus is applied.

Round trips are scored separately (see :func:`score_round_trip`): points component on total for two,
fees on total known fees, convenience as the mean of both legs, plus an open-jaw penalty.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import AmexCalculation, AwardObservation, Cabin, ScoreBreakdown, SeatConfidence

TIERS = [(90, "Exceptional"), (80, "Excellent"), (70, "Worth considering"), (0, "Below threshold")]

# Approximate USD conversion for fee comparison only (user-editable in settings). Never shown as a quote.
DEFAULT_FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "CHF": 1.12, "AUD": 0.66, "SGD": 0.74,
                     "AED": 0.27, "JPY": 0.0067, "MXN": 0.055, "SEK": 0.095, "DKK": 0.145, "NOK": 0.093, "HKD": 0.128,
                     "QAR": 0.27, "TRY": 0.03, "INR": 0.012, "BRL": 0.18}


# --------------------------------------------------------------------------- #
# Priority presets
# --------------------------------------------------------------------------- #
# "minimize_cash" is the default: the owner would rather spend more Amex points
# than pay cash out of pocket, so taxes/fees carry the largest weight and the fee
# curve is steep, while the points curve is forgiving up to ~70k per person.
# "balanced" is the original spec. "minimize_points" favours the cheapest award
# in points even if the cash fees are higher.
DEFAULT_PRIORITY = "minimize_cash"

PRIORITY_PRESETS: dict[str, dict] = {
    "minimize_cash": {
        "label": "Minimize cash out of pocket",
        "description": "Prefers deals with the lowest taxes and fees, even if they cost more points.",
        "weights": {"points": 22.0, "fees": 38.0, "convenience": 15.0, "confidence": 10.0, "quality": 10.0, "flexibility": 5.0},
        "points_anchors": [(50_000, 1.0), (60_000, 0.85), (70_000, 0.65), (85_000, 0.4), (100_000, 0.15), (120_000, 0.0)],
        "fee_anchors_usd": [(50.0, 1.0), (125.0, 0.85), (250.0, 0.55), (400.0, 0.25), (600.0, 0.05), (800.0, 0.0)],
        "cents_per_point": 1.0,
    },
    "balanced": {
        "label": "Balanced",
        "description": "Weighs points and cash roughly as most award-travel guides do.",
        "weights": {"points": 40.0, "fees": 20.0, "convenience": 15.0, "confidence": 10.0, "quality": 10.0, "flexibility": 5.0},
        "points_anchors": [(40_000, 1.0), (50_000, 0.8), (60_000, 0.6), (70_000, 0.4), (85_000, 0.15), (100_000, 0.0)],
        "fee_anchors_usd": [(100.0, 1.0), (200.0, 0.85), (350.0, 0.55), (600.0, 0.2), (900.0, 0.0)],
        "cents_per_point": 1.5,
    },
    "minimize_points": {
        "label": "Minimize points",
        "description": "Prefers the cheapest award in points; cash fees matter less.",
        "weights": {"points": 50.0, "fees": 10.0, "convenience": 15.0, "confidence": 10.0, "quality": 10.0, "flexibility": 5.0},
        "points_anchors": [(35_000, 1.0), (45_000, 0.8), (55_000, 0.55), (65_000, 0.3), (80_000, 0.1), (95_000, 0.0)],
        "fee_anchors_usd": [(150.0, 1.0), (300.0, 0.8), (500.0, 0.5), (800.0, 0.2), (1200.0, 0.0)],
        "cents_per_point": 2.0,
    },
}


@dataclass
class ScoringWeights:
    points: float = 22.0
    fees: float = 38.0
    convenience: float = 15.0
    confidence: float = 10.0
    quality: float = 10.0
    flexibility: float = 5.0

    def total(self) -> float:
        return self.points + self.fees + self.convenience + self.confidence + self.quality + self.flexibility

    @classmethod
    def for_priority(cls, priority: str) -> "ScoringWeights":
        preset = PRIORITY_PRESETS.get(priority, PRIORITY_PRESETS[DEFAULT_PRIORITY])
        return cls(**preset["weights"])


@dataclass
class ScoringConfig:
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    passengers: int = 2
    fx_to_usd: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FX_TO_USD))
    points_anchors: list[tuple[int, float]] = field(
        default_factory=lambda: list(PRIORITY_PRESETS[DEFAULT_PRIORITY]["points_anchors"]))
    fee_anchors_usd: list[tuple[float, float]] = field(
        default_factory=lambda: list(PRIORITY_PRESETS[DEFAULT_PRIORITY]["fee_anchors_usd"]))
    unknown_fee_score: float = 0.45
    stale_hours_warn: float = 24.0
    stale_hours_bad: float = 72.0
    priority: str = DEFAULT_PRIORITY
    # Used only for the "all-in cost" comparison: how many cents one Amex point is worth to the owner.
    cents_per_point: float = 1.0

    @classmethod
    def for_priority(cls, priority: str | None, passengers: int = 2, **overrides) -> "ScoringConfig":
        """Build a config from a named preset; unknown names fall back to the default preset."""
        name = priority if priority in PRIORITY_PRESETS else DEFAULT_PRIORITY
        preset = PRIORITY_PRESETS[name]
        cfg = cls(weights=ScoringWeights(**preset["weights"]), passengers=passengers,
                  points_anchors=list(preset["points_anchors"]), fee_anchors_usd=list(preset["fee_anchors_usd"]),
                  priority=name, cents_per_point=preset["cents_per_point"])
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg


def all_in_cost_usd(points: int | None, cash_usd: float | None, cents_per_point: float) -> float | None:
    """Cash plus points valued at ``cents_per_point``. Comparison aid only, never a quote."""
    if points is None or cash_usd is None:
        return None
    return round(cash_usd + points * cents_per_point / 100.0, 2)


def tier_for(score: float) -> str:
    for threshold, name in TIERS:
        if score >= threshold:
            return name
    return "Below threshold"


def _interp(value: float, anchors: list[tuple[float, float]]) -> float:
    if value <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return anchors[-1][1]


def fees_to_usd(minor_units: int | None, currency: str | None, cfg: ScoringConfig) -> float | None:
    if minor_units is None:
        return None
    rate = cfg.fx_to_usd.get((currency or "USD").upper())
    if rate is None:
        return None
    return (minor_units / 100.0) * rate


def score_award(award: AwardObservation, amex: AmexCalculation, cfg: ScoringConfig | None = None) -> ScoreBreakdown:
    cfg = cfg or ScoringConfig()
    w = cfg.weights
    penalties: list[str] = []
    best = award.best_option()

    # Points efficiency
    pts = amex.amex_points_per_passenger
    if not amex.is_amex_partner or pts is None:
        points_score = 0.0
        penalties.append("Not an Amex transfer partner" if not amex.is_amex_partner else "Mileage cost missing")
    else:
        points_score = _interp(pts, cfg.points_anchors)

    # Fees
    fee_usd = fees_to_usd(award.taxes_minor_units, award.taxes_currency, cfg) if award.fees_known else None
    if fee_usd is None:
        fee_score = cfg.unknown_fee_score
        penalties.append("Taxes/fees not provided")
    else:
        fee_score = _interp(fee_usd, cfg.fee_anchors_usd)
        if fee_usd > 350:
            penalties.append(f"High one-way fees ~${fee_usd:,.0f}")

    # Convenience
    conv = 1.0
    stops = best.stops if best and best.stops is not None else (0 if award.direct_available else None)
    if stops is None:
        conv -= 0.1
        penalties.append("Stops unknown (no flight-level detail)")
    elif stops > 0:
        conv -= 0.25 * stops
        if stops >= 2:
            penalties.append(f"{stops} connections")
    if best:
        if best.overnight_layover:
            conv -= 0.2
            penalties.append("Overnight layover")
        if best.longest_layover_minutes and best.longest_layover_minutes > 240:
            conv -= 0.15
            penalties.append("Layover over 4 hours")
        if best.mixed_cabin:
            conv -= 0.5
            penalties.append("Mixed cabin itinerary")
    conv = max(conv, 0.0)

    # Data confidence
    if award.seat_confidence == SeatConfidence.REPORTED and (award.remaining_seats or 0) >= cfg.passengers:
        conf = 1.0
    elif award.seat_confidence == SeatConfidence.REPORTED:
        conf = 0.2
        penalties.append(f"Only {award.remaining_seats} seat(s) reported")
    else:
        conf = 0.5
        penalties.append("Seat count not reported by program")
    age = award.data_age_hours
    if age is None:
        conf -= 0.3
        penalties.append("Observation time unknown")
    elif age > cfg.stale_hours_bad:
        conf -= 0.4
        penalties.append(f"Stale observation ({age / 24:.1f} days old)")
    elif age > cfg.stale_hours_warn:
        conf -= 0.2
        penalties.append(f"Observation {age:.0f}h old")
    conf = max(conf, 0.0)

    # Airline / cabin quality (lie-flat is never assumed)
    quality = 0.7
    if best:
        if best.mixed_cabin:
            quality -= 0.3
        if any(s.cabin == Cabin.UNKNOWN for s in best.segments):
            quality -= 0.1
        if (best.stops or 0) == 0 and award.source in {"aeroplan", "flyingblue", "virginatlantic", "singapore", "qatar", "emirates", "delta"}:
            quality += 0.3
    elif award.direct_available:
        quality += 0.1
    quality = min(max(quality, 0.0), 1.0)

    # Flexibility / transfer certainty
    if not amex.is_amex_partner:
        flex = 0.0
    elif amex.bonus_percent > 0 and not amex.bonus_verified:
        flex = 0.4
        penalties.append("Unverified transfer bonus assumed")
    elif amex.bonus_percent > 0:
        flex = 0.7
    else:
        flex = 1.0

    components = {
        "points": round(points_score * w.points, 2),
        "fees": round(fee_score * w.fees, 2),
        "convenience": round(conv * w.convenience, 2),
        "confidence": round(conf * w.confidence, 2),
        "quality": round(quality * w.quality, 2),
        "flexibility": round(flex * w.flexibility, 2),
    }
    total = sum(components.values()) * (100.0 / w.total()) if w.total() else 0.0
    if not amex.is_amex_partner:
        total = min(total, 49.0)  # never surfaces as a deal
    total = round(min(max(total, 0.0), 100.0), 1)
    return ScoreBreakdown(total=total, components=components, penalties=penalties, tier=tier_for(total))


def score_round_trip(outbound: ScoreBreakdown, inbound: ScoreBreakdown, *, total_points_two: int | None,
                     total_fees_usd_two: float | None, fees_complete: bool, open_jaw_europe: bool, open_jaw_us: bool,
                     trip_days: int, cfg: ScoringConfig | None = None) -> ScoreBreakdown:
    cfg = cfg or ScoringConfig()
    w = cfg.weights
    penalties: list[str] = []
    if total_points_two is None:
        pts = 0.0
    else:
        per_leg_pp = total_points_two / (2 * max(cfg.passengers, 1))
        pts = _interp(per_leg_pp, cfg.points_anchors)
    if total_fees_usd_two is None or not fees_complete:
        fee = cfg.unknown_fee_score
        penalties.append("Fees incomplete")
    else:
        per_leg_pp = total_fees_usd_two / (2 * max(cfg.passengers, 1))
        fee = _interp(per_leg_pp, cfg.fee_anchors_usd)
    mean = lambda k: (outbound.components[k] + inbound.components[k]) / 2  # noqa: E731
    conv = mean("convenience") / w.convenience if w.convenience else 0
    if open_jaw_europe:
        conv = max(conv - 0.1, 0)
        penalties.append("Open-jaw in Europe: ground/air transfer between cities needed (cost not estimated)")
    if open_jaw_us:
        conv = max(conv - 0.1, 0)
        penalties.append("Different U.S. airports out/back: positioning needed")
    components = {
        "points": round(pts * w.points, 2),
        "fees": round(fee * w.fees, 2),
        "convenience": round(conv * w.convenience, 2),
        "confidence": round(mean("confidence"), 2),
        "quality": round(mean("quality"), 2),
        "flexibility": round(mean("flexibility"), 2),
    }
    total = round(min(max(sum(components.values()) * (100.0 / w.total()), 0.0), 100.0), 1)
    penalties.extend(sorted(set(outbound.penalties) | set(inbound.penalties)))
    return ScoreBreakdown(total=total, components=components, penalties=penalties, tier=tier_for(total))


def compare(a_label: str, a: dict, b_label: str, b: dict) -> list[str]:
    """Concise reasons why option A ranks above B. Inputs are dicts of comparable metrics."""
    reasons: list[str] = []
    if a.get("points") is not None and b.get("points") is not None and a["points"] != b["points"]:
        better, worse = (a_label, b_label) if a["points"] < b["points"] else (b_label, a_label)
        reasons.append(f"{better} needs {abs(a['points'] - b['points']):,} fewer Amex points than {worse}.")
    if a.get("fees") is not None and b.get("fees") is not None and abs(a["fees"] - b["fees"]) >= 25:
        better, worse = (a_label, b_label) if a["fees"] < b["fees"] else (b_label, a_label)
        reasons.append(f"{better} has about ${abs(a['fees'] - b['fees']):,.0f} lower known fees than {worse}.")
    if a.get("stops") is not None and b.get("stops") is not None and a["stops"] != b["stops"]:
        better, worse = (a_label, b_label) if a["stops"] < b["stops"] else (b_label, a_label)
        reasons.append(f"{better} has fewer connections ({min(a['stops'], b['stops'])} vs {max(a['stops'], b['stops'])}).")
    if a.get("duration") and b.get("duration") and abs(a["duration"] - b["duration"]) >= 90:
        better, worse = (a_label, b_label) if a["duration"] < b["duration"] else (b_label, a_label)
        reasons.append(f"{better} is {abs(a['duration'] - b['duration']) // 60}h shorter than {worse}.")
    if a.get("age_hours") is not None and b.get("age_hours") is not None and abs(a["age_hours"] - b["age_hours"]) >= 12:
        fresher, staler = (a_label, b_label) if a["age_hours"] < b["age_hours"] else (b_label, a_label)
        reasons.append(f"{fresher} was observed more recently than {staler}.")
    return reasons or ["Options are equivalent on points, fees, connections, duration and freshness."]
