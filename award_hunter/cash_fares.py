"""Cash-fare comparison via the Amadeus Self-Service API (optional).

Gives the "cents per point" a deal really delivers: (cash fare - award taxes) / points.
Needs a free Amadeus developer key (https://developers.amadeus.com): set
``AMADEUS_CLIENT_ID`` / ``AMADEUS_CLIENT_SECRET`` in ``.env``. ``AMADEUS_ENV`` is
``test`` (default; limited, cached airline data) or ``production``. Without a key
nothing here runs and the dashboard simply omits the comparison.

Fares are cached per (route, date, cabin) for 24 hours in ``cash_fares`` so a scan
spends at most a couple of dozen calls on the top trips.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

from .config import Settings

log = logging.getLogger(__name__)

HOSTS = {"test": "https://test.api.amadeus.com", "production": "https://api.amadeus.com"}
CABIN_CODES = {"business": "BUSINESS", "premium": "PREMIUM_ECONOMY", "economy": "ECONOMY", "first": "FIRST"}
CACHE_HOURS = 24


def configured(settings: Settings) -> bool:
    return bool(settings.amadeus_client_id and settings.amadeus_client_secret)


@dataclass
class CashFare:
    origin: str
    destination: str
    day: date
    cabin: str
    price_usd: float
    carrier: str | None
    fetched_at: datetime


class AmadeusClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.base = HOSTS.get((settings.amadeus_env or "test").lower(), HOSTS["test"])
        self._client = httpx.Client(timeout=20, transport=transport)
        self._token: str | None = None
        self.calls = 0

    def close(self) -> None:
        self._client.close()

    def _auth(self) -> str:
        if self._token:
            return self._token
        r = self._client.post(f"{self.base}/v1/security/oauth2/token",
                              data={"grant_type": "client_credentials", "client_id": self.settings.amadeus_client_id,
                                    "client_secret": self.settings.amadeus_client_secret})
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    def cheapest(self, origin: str, destination: str, day: date, cabin: str = "business", adults: int = 1) -> CashFare | None:
        """Cheapest offer for one adult in the cabin; None when the API has no offers."""
        token = self._auth()
        params = {"originLocationCode": origin.upper(), "destinationLocationCode": destination.upper(), "departureDate": day.isoformat(),
                  "adults": adults, "travelClass": CABIN_CODES.get(cabin, "BUSINESS"), "currencyCode": "USD", "max": 10}
        r = self._client.get(f"{self.base}/v2/shopping/flight-offers", params=params, headers={"Authorization": f"Bearer {token}"})
        self.calls += 1
        if r.status_code == 401:  # token expired mid-run
            self._token = None
            return self.cheapest(origin, destination, day, cabin, adults)
        r.raise_for_status()
        offers = r.json().get("data") or []
        best = None
        for o in offers:
            try:
                total = float(o["price"]["grandTotal"])
            except (KeyError, TypeError, ValueError):
                continue
            carrier = (o.get("validatingAirlineCodes") or [None])[0]
            if best is None or total < best.price_usd:
                best = CashFare(origin.upper(), destination.upper(), day, cabin, total, carrier, datetime.now(timezone.utc))
        return best


def _key(origin: str, destination: str, day: date, cabin: str) -> str:
    return f"{origin.upper()}|{destination.upper()}|{day.isoformat()}|{cabin}"


def fare_for(db, client: AmadeusClient | None, origin: str, destination: str, day: date, cabin: str) -> CashFare | None:
    """Cached lookup; fetches through ``client`` when stale or missing (client may be None = cache only)."""
    row = db.cash_fare_get(_key(origin, destination, day, cabin))
    if row:
        fetched = datetime.fromisoformat(row["fetched_at"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - fetched < timedelta(hours=CACHE_HOURS) or client is None:
            if row["price_usd"] is None:
                return None
            return CashFare(origin, destination, day, cabin, float(row["price_usd"]), row.get("carrier"), fetched)
    if client is None:
        return None
    try:
        fare = client.cheapest(origin, destination, day, cabin)
    except Exception as exc:  # noqa: BLE001
        log.warning("Cash fare lookup failed for %s-%s %s: %s", origin, destination, day, type(exc).__name__)
        return None
    db.cash_fare_set(_key(origin, destination, day, cabin), fare.price_usd if fare else None, fare.carrier if fare else None)
    return fare


def attach_cash_fares(db, settings: Settings, trips, *, limit: int = 10, transport: httpx.BaseTransport | None = None) -> int:
    """Fill ``cash_fare_two_usd`` / ``cash_fare_note`` on the top ``limit`` complete trips. Returns API calls made."""
    if not configured(settings) or not trips:
        return 0
    client = AmadeusClient(settings, transport=transport)
    try:
        done = 0
        for t in trips:
            if done >= limit:
                break
            if not t.fees_complete or not t.total_amex_points_two:
                continue
            legs = []
            for leg in (t.outbound, t.inbound):
                a = leg.award
                legs.append(fare_for(db, client, a.origin, a.destination, a.travel_date, a.cabin.value))
            done += 1
            if any(f is None for f in legs):
                t.cash_fare_note = "No cash fare found for comparison."
                continue
            total_two = sum(f.price_usd for f in legs) * 2
            cpp = (total_two - (t.total_fees_two_major or 0)) / t.total_amex_points_two * 100
            t.cash_fare_two_usd = round(total_two, 2)
            t.cash_fare_note = f"Cash would be ~${total_two:,.0f} for two → {cpp:.1f}¢ per point"
        return client.calls
    finally:
        client.close()
