"""Booking helpers: deep links into the program's award search, and transfer-speed facts.

Deep links exist only where the program's website accepts the route and date in the
URL in a stable way (Aeroplan, Air France-KLM Flying Blue). Everything else falls
back to the program's booking home page. Transfer speeds are the typical time for
Amex Membership Rewards to post, used to warn when seats are scarce.
"""
from __future__ import annotations

from datetime import date

from .transfer_partners import PROGRAMS

# Typical Amex -> program posting time. "instant" means minutes.
TRANSFER_SPEED: dict[str, str] = {
    "aeroplan": "instant", "flyingblue": "instant", "virginatlantic": "instant", "delta": "instant", "jetblue": "instant",
    "emirates": "instant", "qatar": "instant", "qantas": "instant",
    "etihad": "up to 24 hours", "aeromexico": "up to 24 hours", "singapore": "1–2 days",
}


def transfer_speed(source: str) -> str:
    return TRANSFER_SPEED.get((source or "").lower(), "unknown")


def transfer_warning(source: str, seats: int | None, passengers: int = 2) -> str | None:
    """Warn when the transfer is slow and the seat count is exactly what you need (or unknown)."""
    speed = transfer_speed(source)
    if speed == "instant":
        return None
    if seats is None or seats <= passengers:
        return f"Transfers to this program take {speed}; with only {seats if seats is not None else 'an unknown number of'} seats they may be gone first."
    return None


def booking_link(source: str, origin: str, destination: str, day: date, passengers: int = 2) -> str | None:
    """A link that lands on the award search for this exact flight where the site supports it."""
    source = (source or "").lower()
    o, d = origin.upper(), destination.upper()
    if source == "aeroplan":
        return (f"https://www.aircanada.com/aeroplan/redeem/availability/outbound?org0={o}&dest0={d}&departureDate0={day:%Y-%m-%d}"
                f"&ADT={passengers}&YTH=0&CHD=0&INF=0&INS=0&lang=en-US&tripType=O&marketCode=INT")
    if source == "flyingblue":
        return (f"https://wwws.airfrance.us/search/offers?pax={passengers}:0:0:0:0:0:0:0&cabinClass=BUSINESS&activeConnection=0"
                f"&connections={o}:A>{day:%Y%m%d}>{d}:A&bookingFlow=REWARD")
    info = PROGRAMS.get(source)
    return info.booking_url if info else None


def link_is_deep(source: str) -> bool:
    return (source or "").lower() in {"aeroplan", "flyingblue"}
