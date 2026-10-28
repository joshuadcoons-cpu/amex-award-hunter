"""Aircraft facts for the long-haul segment: is this likely a lie-flat seat?

Seats.aero supplies the aircraft per segment when flight-level detail is
requested. Across the Atlantic almost every widebody business cabin is lie-flat;
narrowbodies usually are not, with a few well-known exceptions (JetBlue Mint,
Aer Lingus and TAP A321LR). We expose three states: widebody / narrowbody-lie-flat
("yes"), narrowbody recliner ("no"), unknown ("?") - never a confident claim about
a specific seat.
"""
from __future__ import annotations

from .airports import US_ORIGINS
from .models import AwardObservation, Segment

NARROWBODY_CODES = {"319", "320", "321", "32A", "32B", "32N", "32Q", "32S", "321", "73G", "73H", "737", "738", "739", "73J", "7M8", "7M9",
                    "752", "753", "757", "E90", "E95", "E75", "E70", "CR9", "CRJ", "CR7", "DH4", "AT7", "223", "221", "BCS3", "BCS1"}
NARROWBODY_WORDS = ("A319", "A320", "A321", "737", "757", "EMBRAER", "E190", "E195", "CRJ", "A220")
# Narrowbodies that do have lie-flat business across the Atlantic, keyed by marketing airline code prefix in the flight number.
LIE_FLAT_NARROWBODY_AIRLINES = {"B6", "EI", "TP"}


def long_haul_segment(award: AwardObservation) -> Segment | None:
    best = award.best_option()
    if not best or not best.segments:
        return None
    crossing = [s for s in best.segments if (s.origin in US_ORIGINS) != (s.destination in US_ORIGINS)]
    if crossing:
        return crossing[0]
    return max(best.segments, key=lambda s: s.distance_miles or 0)


def is_narrowbody(seg: Segment) -> bool | None:
    code = (seg.aircraft_code or "").upper()
    name = (seg.aircraft_name or "").upper()
    if not code and not name:
        return None
    if code in NARROWBODY_CODES or any(w in name for w in NARROWBODY_WORDS):
        return True
    return False


def lie_flat_likely(award: AwardObservation) -> tuple[str, str]:
    """('yes'|'no'|'?', aircraft label)."""
    seg = long_haul_segment(award)
    if seg is None:
        return "?", ""
    label = seg.aircraft_name or seg.aircraft_code or ""
    nb = is_narrowbody(seg)
    if nb is None:
        return "?", label
    if not nb:
        return "yes", label
    airline = (seg.flight_number or "")[:2].upper()
    if airline in LIE_FLAT_NARROWBODY_AIRLINES:
        return "yes", label
    return "no", label
