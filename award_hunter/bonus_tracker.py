"""Detect currently running Amex Membership Rewards transfer bonuses.

American Express publishes no API for transfer bonuses, and many bonuses are
targeted, so this module only *detects* them from a public tracker page and
records them as **unverified**. The transfer math applies a bonus only after the
owner confirms it is visible in their own Amex account (Home → Transfer bonuses →
"It's in my account"). A detected bonus is never applied silently.

Source: https://awardtravelfinder.com/transfer-bonuses/amex-membership-rewards
(re-checked daily by that site). Its "Active" section renders each bonus as::

    Amex Membership Rewards → Air France KLM Flying Blue  US  +25%  Valid: Oct 01 - Oct 31, 2026

If the page layout changes the parser returns nothing, the dashboard says the
check failed, and the manual bonus form in Settings still works.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

log = logging.getLogger(__name__)

TRACKER_URL = "https://awardtravelfinder.com/transfer-bonuses/amex-membership-rewards"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
BONUS_CHECK_KEY = "bonus_check"

# Tracker partner wording -> Seats.aero source code used throughout the app. Partners that are not
# Seats.aero sources for this app (hotels, Avianca, etc.) are still shown, just not applied to awards.
_PARTNER_KEYWORDS: list[tuple[str, str]] = [
    ("flying blue", "flyingblue"), ("air france", "flyingblue"), ("klm", "flyingblue"),
    ("aeromexico", "aeromexico"), ("club premier", "aeromexico"), ("clubpremier", "aeromexico"),
    ("aeroplan", "aeroplan"), ("air canada", "aeroplan"),
    ("virgin atlantic", "virginatlantic"), ("flying club", "virginatlantic"),
    ("krisflyer", "singapore"), ("singapore", "singapore"),
    ("qatar", "qatar"), ("privilege club", "qatar"),
    ("etihad", "etihad"), ("emirates", "emirates"), ("skywards", "emirates"),
    ("qantas", "qantas"), ("delta", "delta"), ("skymiles", "delta"),
    ("jetblue", "jetblue"), ("trueblue", "jetblue"),
    ("avianca", "avianca"), ("lifemiles", "avianca"),
    ("british airways", "british"), ("avios", "british"), ("iberia", "iberia"),
    ("cathay", "cathay"), ("asia miles", "cathay"), ("ana", "ana"), ("hawaiian", "hawaiian"),
    ("virgin australia", "velocity"), ("velocity", "velocity"),
    ("hilton", "hilton"), ("marriott", "marriott"), ("bonvoy", "marriott"), ("choice", "choice"),
]

_ACTIVE_RE = re.compile(
    r"Amex Membership Rewards\s*(?:→|->|to)\s*(?P<partner>[^→]{3,80}?)\s+(?:\S{1,4}\s+)?US\s+\+(?P<pct>\d{1,3})%\s+"
    r"Valid:\s*(?P<start>[A-Z][a-z]{2}\s+\d{1,2})(?:,\s*(?P<start_year>\d{4}))?\s*-\s*(?P<end>[A-Z][a-z]{2}\s+\d{1,2}),\s*(?P<end_year>\d{4})",
)


@dataclass(frozen=True)
class DetectedBonus:
    partner_text: str          # wording on the tracker, e.g. "Air France KLM Flying Blue"
    source: str | None         # Seats.aero source code when the partner is one this app searches
    bonus_percent: float
    effective_date: date | None
    expiration_date: date | None
    source_url: str = TRACKER_URL

    @property
    def is_airline_source(self) -> bool:
        from .transfer_partners import PROGRAMS
        return self.source in PROGRAMS

    @property
    def key(self) -> tuple:
        return (self.source or self.partner_text.lower(), self.bonus_percent, self.expiration_date)


def partner_to_source(partner_text: str) -> str | None:
    low = partner_text.lower()
    for needle, source in _PARTNER_KEYWORDS:
        if needle in low:
            return source
    return None


def _strip_html(raw: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text)


def _parse_day(token: str, year: str) -> date | None:
    try:
        return datetime.strptime(f"{token} {year}", "%b %d %Y").date()
    except ValueError:
        return None


def parse_tracker(raw_html: str) -> list[DetectedBonus]:
    """Extract the active Amex bonuses from the tracker page. Returns [] when nothing parses."""
    text = _strip_html(raw_html)
    # Only the "Active" block lists "Valid:" ranges; the history section uses other wording.
    out: list[DetectedBonus] = []
    seen: set[tuple] = set()
    for m in _ACTIVE_RE.finditer(text):
        partner = m.group("partner").strip(" -–·")
        pct = float(m.group("pct"))
        end_year = m.group("end_year")
        start_year = m.group("start_year") or end_year
        start = _parse_day(m.group("start"), start_year)
        end = _parse_day(m.group("end"), end_year)
        if start and end and start > end:  # promo spanning New Year, e.g. Dec 20 - Jan 15, 2027
            start = _parse_day(m.group("start"), str(int(end_year) - 1))
        b = DetectedBonus(partner, partner_to_source(partner), pct, start, end)
        if b.key in seen:
            continue
        seen.add(b.key)
        out.append(b)
    return out


def fetch_tracker(url: str = TRACKER_URL, *, transport: httpx.BaseTransport | None = None, timeout: float = 20.0) -> str:
    with httpx.Client(timeout=timeout, transport=transport, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


@dataclass
class BonusSyncResult:
    checked_at: datetime
    ok: bool
    detected: list[DetectedBonus]
    added: list[DetectedBonus]
    expired: int
    error: str | None = None

    def summary(self) -> str:
        if not self.ok:
            return f"Bonus check failed: {self.error}"
        live = [d for d in self.detected if d.is_airline_source]
        return (f"{len(self.detected)} Amex bonus(es) live on the tracker, {len(live)} for programs this app searches, "
                f"{len(self.added)} new, {self.expired} expired entries retired")


def sync_bonuses(db, *, today: date | None = None, transport: httpx.BaseTransport | None = None, raw_html: str | None = None) -> BonusSyncResult:
    """Fetch the tracker, record new bonuses as unverified, retire expired detected ones, remember the check time.

    Never raises: network or parse failures come back as ``ok=False`` so a daily scan is never blocked by it.
    """
    today = today or date.today()
    now = datetime.now(timezone.utc)
    try:
        html_text = raw_html if raw_html is not None else fetch_tracker(transport=transport)
        detected = parse_tracker(html_text)
    except Exception as exc:  # noqa: BLE001
        log.warning("Transfer-bonus check failed: %s", type(exc).__name__)
        res = BonusSyncResult(now, False, [], [], 0, f"{type(exc).__name__}: {exc}"[:200])
        db.set_setting(BONUS_CHECK_KEY, {"checked_at": now.isoformat(), "ok": False, "error": res.error})
        return res

    existing = db.transfer_bonuses()
    added: list[DetectedBonus] = []
    for d in detected:
        if not d.is_airline_source:
            continue  # hotels etc.: shown on the dashboard from the check record, never stored as award math
        if d.expiration_date and d.expiration_date < today:
            continue
        dup = any(e["source"] == d.source and float(e["bonus_percent"]) == d.bonus_percent
                  and (e.get("expiration_date") or None) == (d.expiration_date.isoformat() if d.expiration_date else None)
                  for e in existing)
        if dup:
            continue
        db.add_transfer_bonus(d.source, d.bonus_percent, d.effective_date.isoformat() if d.effective_date else None,
                              d.expiration_date.isoformat() if d.expiration_date else None, d.source_url, None,
                              account_eligible=False, verified=False,
                              notes=f"Detected {today.isoformat()} on awardtravelfinder.com ({d.partner_text}). Confirm in your Amex account before relying on it.")
        added.append(d)

    expired = 0
    for e in existing:
        exp = e.get("expiration_date")
        if e.get("active") and exp and date.fromisoformat(exp) < today:
            db.set_bonus_active(int(e["id"]), False)
            expired += 1

    res = BonusSyncResult(now, True, detected, added, expired)
    db.set_setting(BONUS_CHECK_KEY, {
        "checked_at": now.isoformat(), "ok": True, "error": None,
        "detected": [{"partner": d.partner_text, "source": d.source, "pct": d.bonus_percent,
                      "start": d.effective_date.isoformat() if d.effective_date else None,
                      "end": d.expiration_date.isoformat() if d.expiration_date else None} for d in detected],
    })
    return res


def last_check(db) -> dict | None:
    return db.get_setting(BONUS_CHECK_KEY)
