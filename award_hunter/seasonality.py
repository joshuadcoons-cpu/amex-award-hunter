"""Historical seasonality for U.S. West Coast -> Europe business-class award space.

A rule-of-thumb calendar, not a forecast: it encodes the well-known pattern that
award space and dynamic award prices follow leisure demand. "good" = most space,
lowest dynamic prices; "caution" = shoulder or holiday-adjacent; "avoid" = peak
demand, little saver space, dynamic prices at their highest. Moving holidays
(Easter, Thanksgiving) are computed per year.

Edit the rules below if your own experience differs; the dashboard reads them live.
"""
from __future__ import annotations

from datetime import date, timedelta

GOOD, CAUTION, AVOID = "good", "caution", "avoid"

LABELS = {GOOD: "Target", CAUTION: "Possible", AVOID: "Avoid"}


def easter(year: int) -> date:
    """Western Easter (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def thanksgiving(year: int) -> date:
    """Fourth Thursday of November."""
    first = date(year, 11, 1)
    offset = (3 - first.weekday()) % 7  # Thursday = 3
    return first + timedelta(days=offset + 21)


def rating_for(day: date) -> tuple[str, str]:
    """Return (rating, short reason) for an outbound departure on ``day``."""
    y, m, d = day.year, day.month, day.day
    # --- fixed holiday peaks ----------------------------------------------------
    if (m == 12 and d >= 18) or (m == 1 and d <= 4):
        return AVOID, "Christmas / New Year peak: almost no saver space, highest dynamic prices"
    tg = thanksgiving(y)
    if tg - timedelta(days=5) <= day <= tg + timedelta(days=3):
        return CAUTION, "Thanksgiving week: popular for Europe trips, space tightens"
    e = easter(y)
    if e - timedelta(days=9) <= day <= e + timedelta(days=7):
        return CAUTION, "Easter / spring break: European holiday demand, fewer seats"
    # --- seasons ------------------------------------------------------------------
    if (m == 1 and d >= 5) or m == 2 or (m == 3 and d <= 14):
        return GOOD, "Deep winter: the most award space of the year and the lowest dynamic prices"
    if (m == 3 and d >= 15) or m == 4:
        return CAUTION, "Spring break season and Easter: decent space outside holiday weeks"
    if m == 5:
        return CAUTION, "Late spring: demand rising toward summer; book early-month dates"
    if m in (6, 7) or (m == 8 and d <= 20):
        return AVOID, "Summer peak: saver business space is rare; plan 11 months ahead or inside 2 weeks"
    if (m == 8 and d >= 21) or (m == 9 and d <= 10):
        return CAUTION, "End of summer: space reopens gradually, prices still elevated"
    if (m == 9 and d >= 11) or m == 10:
        return GOOD, "Fall shoulder: great weather, strong award space, reasonable prices"
    if m == 11:
        return GOOD, "November (outside Thanksgiving week): quiet and cheap"
    if m == 12 and d <= 17:
        return GOOD, "Early December: Christmas-market season with plenty of space"
    return CAUTION, "Shoulder period"


def month_summary(year: int, month: int) -> tuple[str, str]:
    """Dominant rating for a month plus the reason for its most common rating."""
    from collections import Counter
    counts: Counter = Counter()
    reasons: dict[str, str] = {}
    day = date(year, month, 1)
    while day.month == month:
        r, why = rating_for(day)
        counts[r] += 1
        reasons.setdefault(r, why)
        day += timedelta(days=1)
    top = counts.most_common(1)[0][0]
    return top, reasons[top]


TIMING_NOTES = [
    ("~11 months out", "First release. The only reliable way to get summer and holiday dates in business."),
    ("3 months to 3 weeks out", "Usually the thinnest: early space is gone, last-minute space hasn't opened."),
    ("Inside ~14 days", "Second wave. Lufthansa group, Air France-KLM and Delta often dump unsold business seats."),
]
