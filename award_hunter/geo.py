"""Rough geography: airport coordinates, distances, and ballpark ground / positioning costs.

Everything here is an *estimate* used to make the all-in cash figure honest when a
trip is open-jaw (fly into Munich, home from Zurich) or departs from an airport
that is not home (San Diego -> fly out of Seattle). Figures are per person in USD
and deliberately conservative; the dashboard always labels them "~".
"""
from __future__ import annotations

import math

from .airports import metro_of

# lat, lon (decimal degrees) for every airport in airports.py
COORDS: dict[str, tuple[float, float]] = {
    # U.S. / Canada
    "SAN": (32.73, -117.19), "LAX": (33.94, -118.41), "SFO": (37.62, -122.38), "SEA": (47.45, -122.31), "SJC": (37.36, -121.93),
    "OAK": (37.72, -122.22), "SNA": (33.68, -117.87), "ONT": (34.06, -117.60), "BUR": (34.20, -118.36), "LAS": (36.08, -115.15),
    "PHX": (33.43, -112.01), "PDX": (45.59, -122.60), "DEN": (39.86, -104.67), "SLC": (40.79, -111.98), "YVR": (49.19, -123.18),
    "ORD": (41.97, -87.91), "DFW": (32.90, -97.04), "IAH": (29.98, -95.34), "JFK": (40.64, -73.78), "EWR": (40.69, -74.17),
    "BOS": (42.36, -71.01), "IAD": (38.95, -77.46),
    # Europe
    "FCO": (41.80, 12.25), "MXP": (45.63, 8.72), "LIN": (45.45, 9.28), "VCE": (45.50, 12.35), "FLR": (43.81, 11.20), "NAP": (40.88, 14.29),
    "BLQ": (44.53, 11.29), "MAD": (40.47, -3.56), "BCN": (41.30, 2.08), "AGP": (36.67, -4.50), "PMI": (39.55, 2.74), "LIS": (38.77, -9.13),
    "OPO": (41.25, -8.68), "AMS": (52.31, 4.76), "ZRH": (47.46, 8.55), "GVA": (46.24, 6.11), "VIE": (48.11, 16.57), "FRA": (50.03, 8.57),
    "MUC": (48.35, 11.79), "BER": (52.37, 13.50), "DUS": (51.29, 6.77), "HAM": (53.63, 9.99), "LHR": (51.47, -0.46), "LGW": (51.15, -0.19),
    "MAN": (53.35, -2.28), "EDI": (55.95, -3.37), "DUB": (53.43, -6.27), "ATH": (37.94, 23.94), "CPH": (55.62, 12.65), "ARN": (59.65, 17.92),
    "OSL": (60.19, 11.10), "HEL": (60.32, 24.96), "KEF": (63.99, -22.61), "BRU": (50.90, 4.48), "PRG": (50.10, 14.26), "BUD": (47.44, 19.26),
    "WAW": (52.17, 20.97), "KRK": (50.08, 19.78), "ZAG": (45.74, 16.07), "SPU": (43.54, 16.30), "DBV": (42.56, 18.27), "IST": (41.26, 28.74),
    "CDG": (49.01, 2.55), "ORY": (48.73, 2.36), "NCE": (43.66, 7.22), "LYS": (45.73, 5.08),
}

# Places you cannot reasonably reach by train from the continent: a flight is assumed.
_FLIGHT_ONLY = {"DUB", "KEF", "ATH", "PMI", "LHR", "LGW", "MAN", "EDI", "LIS", "OPO", "IST", "SPU", "DBV", "HEL"}


def distance_km(a: str, b: str) -> float | None:
    pa, pb = COORDS.get(a.upper()), COORDS.get(b.upper())
    if not pa or not pb:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (*pa, *pb))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def ground_cost_pp_usd(a: str, b: str) -> tuple[float, str] | None:
    """Ballpark cost per person to get between two European cities (train where realistic, otherwise a budget flight).

    Returns (usd, mode) or None when unknown. Same metro -> (0, 'same city').
    """
    a, b = a.upper(), b.upper()
    if metro_of(a) == metro_of(b):
        return 0.0, "same city"
    d = distance_km(a, b)
    if d is None:
        return None
    flight = d > 750 or a in _FLIGHT_ONLY or b in _FLIGHT_ONLY
    if not flight:
        if d < 150:
            return 30.0, "train"
        if d < 400:
            return round(45 + 0.05 * d), "train"
        return round(60 + 0.06 * d), "train"
    return float(min(200, max(90, round(80 + 0.05 * d)))), "flight"


def positioning_cost_pp_usd(home: str, origin: str) -> tuple[float, str] | None:
    """Ballpark cost per person to get from home to a different U.S. departure airport."""
    home, origin = home.upper(), origin.upper()
    if metro_of(home) == metro_of(origin):
        return 0.0, "home"
    d = distance_km(home, origin)
    if d is None:
        return None
    if d < 250:
        return 50.0, "drive or short hop"
    if d < 1000:
        return 130.0, "short flight"
    return 170.0, "flight"
