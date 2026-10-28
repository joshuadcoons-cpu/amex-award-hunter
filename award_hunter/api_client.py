"""Seats.aero Partner API client.

Confirmed behaviour this client relies on (see docs/API_NOTES.md for sources):

* Base URL ``https://seats.aero/partnerapi``
* Header ``Partner-Authorization`` carrying the Pro API key (``Bearer <key>`` by
  default; a raw-key variant is supported via ``SEATS_AERO_AUTH_SCHEME=raw``).
* ``GET /search``  - cached availability across comma-separated origin/destination
  airports, a ``start_date``/``end_date`` window, ``cabins``, ``sources``,
  ``take`` (10-1000), ``cursor``/``skip`` pagination, ``include_trips``,
  ``only_direct_flights``, ``order_by=lowest_mileage``, ``include_filtered``.
* ``GET /availability`` - bulk cached availability for one ``source``.
* ``GET /trips/{id}`` - flight-level detail for one Availability ID.
* ``GET /routes?source=`` - routes tracked for one program.
* Quota: 1,000 calls per UTC day on Pro; ``X-RateLimit-Remaining`` header.
* ``POST /live`` is NOT available to Pro keys and is intentionally not wrapped.

Everything else (exact field semantics, which programs report seats/taxes) is
treated as data to be validated at runtime, never assumed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Protocol
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from .config import SEATS_AERO_BASE_URL, Settings
from .logging_config import mask_secret
from .models import RawSearchResponse, RawTripsResponse

log = logging.getLogger(__name__)

CABIN_VALUES = ("economy", "premium", "business", "first")
MAX_TAKE = 1000
MIN_TAKE = 10


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = (body or "")[:500]


class ApiAuthError(ApiError):
    """401/403 - key missing, invalid, or endpoint not permitted on this plan."""


class RateLimitError(ApiError):
    """429 that persisted through retries, or quota exhausted server-side."""


class BudgetExhaustedError(ApiError):
    """Local daily budget reached; refusing to spend more calls today."""


class ResponseShapeError(ApiError):
    """The response was 2xx but did not match the expected schema."""


# --------------------------------------------------------------------------- #
# Budget + cache plumbing
# --------------------------------------------------------------------------- #


@dataclass
class ApiCallRecord:
    path: str
    params: dict[str, Any]
    status_code: int | None
    latency_ms: float
    from_cache: bool
    remaining_quota: int | None
    error: str | None
    at_utc: datetime


class ResponseCache(Protocol):
    def get(self, key: str) -> dict | None: ...

    def set(self, key: str, value: dict, ttl_seconds: int) -> None: ...


class InMemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> dict | None:
        item = self._store.get(key)
        if not item:
            return None
        expires, value = item
        if time.time() > expires:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        self._store[key] = (time.time() + ttl_seconds, value)


class DailyBudget:
    """Counts calls per UTC day. Persisted through optional load/save callbacks."""

    def __init__(
        self,
        limit: int,
        load: Callable[[str], int] | None = None,
        save: Callable[[str, int], None] | None = None,
    ) -> None:
        self.limit = limit
        self._load = load
        self._save = save
        self._day: str | None = None
        self._used = 0

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _sync(self) -> None:
        today = self._today()
        if self._day != today:
            self._day = today
            self._used = self._load(today) if self._load else 0

    @property
    def used(self) -> int:
        self._sync()
        return self._used

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    def check(self, needed: int = 1) -> None:
        if self.remaining < needed:
            raise BudgetExhaustedError(
                f"Daily API budget of {self.limit} calls reached ({self.used} used, UTC day). "
                "The Seats.aero quota resets at midnight UTC."
            )

    def consume(self, n: int = 1) -> None:
        self._sync()
        self._used += n
        if self._save:
            self._save(self._day or self._today(), self._used)


@dataclass
class ConnectionTestResult:
    ok: bool
    status_code: int | None
    latency_ms: float | None
    message: str
    auth_scheme_used: str | None = None
    records_returned: int | None = None
    remaining_quota: int | None = None
    sample_fields: list[str] = field(default_factory=list)
    base_url: str = SEATS_AERO_BASE_URL


def cache_key(path: str, params: dict[str, Any]) -> str:
    canonical = json.dumps({"p": path, "q": params}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in params.items():
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (list, tuple, set)):
            out[k] = ",".join(str(x) for x in v)
        elif isinstance(v, (date, datetime)):
            out[k] = v.strftime("%Y-%m-%d")
        else:
            out[k] = str(v)
    return out


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class SeatsAeroClient:
    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        settings: Settings,
        *,
        cache: ResponseCache | None = None,
        budget: DailyBudget | None = None,
        on_call: Callable[[ApiCallRecord], None] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.base_url = settings.base_url.rstrip("/") or SEATS_AERO_BASE_URL
        self.cache = cache or InMemoryCache()
        self.budget = budget or DailyBudget(settings.api_daily_budget)
        self.on_call = on_call
        self._sleep = sleep
        self.last_remaining_quota: int | None = None
        self.last_quota_limit: int | None = None
        self._auth_scheme = settings.auth_scheme
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            headers={"Accept": "application/json", "User-Agent": "amex-award-hunter/0.1 (personal, non-commercial)"},
            transport=transport,
        )

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SeatsAeroClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- auth ----------------------------------------------------------------
    @property
    def auth_scheme(self) -> str:
        return self._auth_scheme

    def _auth_headers(self, scheme: str | None = None) -> dict[str, str]:
        key = self.settings.require_api_key()
        scheme = scheme or self._auth_scheme
        value = f"Bearer {key}" if scheme == "bearer" else key
        return {"Partner-Authorization": value}

    # -- low level -----------------------------------------------------------
    def _record(self, rec: ApiCallRecord) -> None:
        if self.on_call:
            try:
                self.on_call(rec)
            except Exception:  # pragma: no cover
                log.exception("on_call hook failed")

    def _parse_quota(self, resp: httpx.Response) -> None:
        rem = resp.headers.get("X-RateLimit-Remaining")
        lim = resp.headers.get("X-RateLimit-Limit")
        try:
            if rem is not None:
                self.last_remaining_quota = int(rem)
            if lim is not None:
                self.last_quota_limit = int(lim)
        except ValueError:
            pass

    def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        cache_ttl: int = 0,
        scheme: str | None = None,
    ) -> dict:
        params = _clean_params(params or {})
        key = cache_key(path, params)
        if cache_ttl > 0:
            cached = self.cache.get(key)
            if cached is not None:
                self._record(ApiCallRecord(path, params, 200, 0.0, True, self.last_remaining_quota, None,
                                           datetime.now(timezone.utc)))
                return cached

        self.budget.check(1)
        headers = self._auth_headers(scheme)
        attempt = 0
        delay = 1.0
        while True:
            attempt += 1
            started = time.perf_counter()
            status: int | None = None
            err: str | None = None
            try:
                resp = self._client.get(path, params=params, headers=headers)
                status = resp.status_code
                latency = (time.perf_counter() - started) * 1000
                self.budget.consume(1)
                self._parse_quota(resp)

                if status in (401, 403):
                    err = f"auth/permission error {status}"
                    self._record(ApiCallRecord(path, params, status, latency, False, self.last_remaining_quota, err,
                                               datetime.now(timezone.utc)))
                    raise ApiAuthError(
                        f"Seats.aero returned HTTP {status} for {path}. Check that SEATS_AERO_API_KEY is a valid "
                        f"Pro Partner API key (key {mask_secret(self.settings.api_key)}) and that this endpoint is "
                        "permitted on your plan.",
                        status, resp.text,
                    )
                if status in self.RETRY_STATUSES and attempt <= self.settings.max_retries:
                    retry_after = resp.headers.get("Retry-After")
                    wait = delay
                    if retry_after:
                        try:
                            wait = max(float(retry_after), 1.0)
                        except ValueError:
                            pass
                    err = f"retryable status {status}"
                    self._record(ApiCallRecord(path, params, status, latency, False, self.last_remaining_quota, err,
                                               datetime.now(timezone.utc)))
                    log.warning("Seats.aero %s -> %s, retrying in %.1fs (attempt %d)", path, status, wait, attempt)
                    self._sleep(wait + random.uniform(0, 0.5))
                    delay = min(delay * 2, 30)
                    continue
                if status == 429:
                    self._record(ApiCallRecord(path, params, status, latency, False, self.last_remaining_quota,
                                               "rate limited", datetime.now(timezone.utc)))
                    raise RateLimitError("Seats.aero rate limit / daily quota exceeded (HTTP 429).", 429, resp.text)
                if status >= 400:
                    self._record(ApiCallRecord(path, params, status, latency, False, self.last_remaining_quota,
                                               f"http {status}", datetime.now(timezone.utc)))
                    raise ApiError(f"Seats.aero returned HTTP {status} for {path}: {resp.text[:200]}", status, resp.text)

                try:
                    payload = resp.json()
                except ValueError as exc:
                    self._record(ApiCallRecord(path, params, status, latency, False, self.last_remaining_quota,
                                               "invalid json", datetime.now(timezone.utc)))
                    raise ResponseShapeError(f"Seats.aero returned non-JSON for {path}", status, resp.text) from exc
                if not isinstance(payload, dict):
                    raise ResponseShapeError(f"Expected JSON object from {path}, got {type(payload).__name__}", status)

                self._record(ApiCallRecord(path, params, status, latency, False, self.last_remaining_quota, None,
                                           datetime.now(timezone.utc)))
                if cache_ttl > 0:
                    self.cache.set(key, payload, cache_ttl)
                return payload

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                latency = (time.perf_counter() - started) * 1000
                err = f"{type(exc).__name__}"
                self._record(ApiCallRecord(path, params, None, latency, False, self.last_remaining_quota, err,
                                           datetime.now(timezone.utc)))
                if attempt <= self.settings.max_retries:
                    log.warning("Seats.aero %s network error %s, retrying in %.1fs", path, err, delay)
                    self._sleep(delay + random.uniform(0, 0.5))
                    delay = min(delay * 2, 30)
                    continue
                raise ApiError(f"Network error talking to Seats.aero after {attempt} attempts: {exc}") from exc

    # -- endpoints -----------------------------------------------------------
    def search(
        self,
        origins: list[str] | str,
        destinations: list[str] | str,
        start_date: date | str,
        end_date: date | str,
        *,
        cabins: list[str] | str = "business",
        sources: list[str] | None = None,
        carriers: list[str] | None = None,
        take: int = MAX_TAKE,
        cursor: int | None = None,
        skip: int | None = None,
        include_trips: bool = True,
        only_direct: bool = False,
        order_by: str | None = "lowest_mileage",
        include_filtered: bool = False,
        minify_trips: bool = False,
        cache_ttl: int = 0,
    ) -> RawSearchResponse:
        if isinstance(cabins, str):
            cabins = [cabins]
        for c in cabins:
            if c not in CABIN_VALUES:
                raise ValueError(f"Unknown cabin {c!r}; expected one of {CABIN_VALUES}")
        take = max(MIN_TAKE, min(int(take), MAX_TAKE))
        params: dict[str, Any] = {
            "origin_airport": origins,
            "destination_airport": destinations,
            "start_date": start_date,
            "end_date": end_date,
            "cabins": cabins,
            "sources": sources,
            "carriers": carriers,
            "take": take,
            "cursor": cursor,
            "skip": skip,
            "include_trips": include_trips,
            "only_direct_flights": only_direct,
            "order_by": order_by,
            "include_filtered": include_filtered if include_filtered else None,
            "minify_trips": minify_trips if minify_trips else None,
        }
        payload = self._get_json("/search", params, cache_ttl=cache_ttl)
        try:
            return RawSearchResponse.model_validate(payload)
        except ValidationError as exc:
            raise ResponseShapeError(f"/search response failed validation: {exc.errors()[:3]}") from exc

    def iter_search(self, *args: Any, max_pages: int = 20, **kwargs: Any) -> Iterator[RawSearchResponse]:
        """Yield successive pages of ``/search`` following ``cursor``/``hasMore``."""
        cursor = kwargs.pop("cursor", None)
        skip = kwargs.pop("skip", None)
        pages = 0
        seen_ids: set[str] = set()
        while True:
            page = self.search(*args, cursor=cursor, skip=skip, **kwargs)
            pages += 1
            fresh = [a for a in page.data if a.ID not in seen_ids]
            seen_ids.update(a.ID for a in page.data)
            page.data = fresh
            yield page
            if not page.hasMore or pages >= max_pages:
                return
            if page.cursor is not None:
                cursor, skip = page.cursor, None
            else:
                # Fall back to skip-based paging when no cursor is supplied.
                skip = (skip or 0) + kwargs.get("take", MAX_TAKE)
                cursor = None

    def get_trips(self, availability_id: str, *, include_filtered: bool = False, cache_ttl: int = 0) -> RawTripsResponse:
        if not availability_id:
            raise ValueError("availability_id required")
        payload = self._get_json(
            f"/trips/{quote(availability_id, safe='')}",
            {"include_filtered": include_filtered if include_filtered else None},
            cache_ttl=cache_ttl,
        )
        try:
            return RawTripsResponse.model_validate(payload)
        except ValidationError as exc:
            raise ResponseShapeError(f"/trips response failed validation: {exc.errors()[:3]}") from exc

    def get_routes(self, source: str, *, cache_ttl: int = 6 * 3600) -> list[dict]:
        payload = self._get_json("/routes", {"source": source}, cache_ttl=cache_ttl)
        data = payload.get("data")
        if not isinstance(data, list):
            raise ResponseShapeError("/routes response missing 'data' list")
        return data

    def bulk_availability(
        self,
        source: str,
        *,
        cabin: str | None = "business",
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        origin_region: str | None = None,
        destination_region: str | None = None,
        take: int = MAX_TAKE,
        cursor: int | None = None,
        skip: int | None = None,
        cache_ttl: int = 0,
    ) -> RawSearchResponse:
        params = {
            "source": source,
            "cabin": cabin,
            "start_date": start_date,
            "end_date": end_date,
            "origin_region": origin_region,
            "destination_region": destination_region,
            "take": max(MIN_TAKE, min(int(take), MAX_TAKE)),
            "cursor": cursor,
            "skip": skip,
        }
        payload = self._get_json("/availability", params, cache_ttl=cache_ttl)
        try:
            return RawSearchResponse.model_validate(payload)
        except ValidationError as exc:
            raise ResponseShapeError(f"/availability response failed validation: {exc.errors()[:3]}") from exc

    # -- diagnostics ---------------------------------------------------------
    def test_connection(self, origin: str = "LAX", destination: str = "LHR") -> ConnectionTestResult:
        """Make one small authenticated ``/search`` request and report what happened.

        Tries the configured auth scheme first; if that is rejected with 401/403 the
        alternate scheme is tried once so the user learns which header form works.
        """
        if not self.settings.has_api_key:
            return ConnectionTestResult(False, None, None,
                                        "SEATS_AERO_API_KEY is not set. Create a .env file and add your key.",
                                        base_url=self.base_url)
        today = date.today()
        # A 30-day window so an empty route doesn't look like an auth failure.
        params = {
            "origin_airport": origin, "destination_airport": destination,
            "start_date": today, "end_date": today + timedelta(days=30),
            "cabins": "business", "take": MIN_TAKE, "include_trips": False, "order_by": "lowest_mileage",
        }

        schemes = [self._auth_scheme, "raw" if self._auth_scheme == "bearer" else "bearer"]
        last_err: ApiError | None = None
        for scheme in schemes:
            started = time.perf_counter()
            try:
                payload = self._get_json("/search", params, scheme=scheme)
                latency = (time.perf_counter() - started) * 1000
                parsed = RawSearchResponse.model_validate(payload)
                if scheme != self._auth_scheme:
                    log.info("Auth scheme %s was rejected; %s works. Set SEATS_AERO_AUTH_SCHEME=%s", self._auth_scheme, scheme, scheme)
                    self._auth_scheme = scheme
                fields = sorted(parsed.data[0].model_dump(exclude_none=True).keys())[:40] if parsed.data else []
                return ConnectionTestResult(
                    True, 200, round(latency, 1),
                    f"Authenticated OK. /search returned {len(parsed.data)} cached business-class record(s) for "
                    f"{origin}->{destination} in the next 30 days.",
                    auth_scheme_used=scheme, records_returned=len(parsed.data),
                    remaining_quota=self.last_remaining_quota, sample_fields=fields, base_url=self.base_url,
                )
            except ApiAuthError as exc:
                last_err = exc
                continue
            except ApiError as exc:
                return ConnectionTestResult(False, exc.status_code, None, str(exc), auth_scheme_used=scheme,
                                            remaining_quota=self.last_remaining_quota, base_url=self.base_url)
        return ConnectionTestResult(False, last_err.status_code if last_err else None, None,
                                    (str(last_err) if last_err else "Authentication failed")
                                    + " Both 'Bearer <key>' and raw-key header forms were rejected.",
                                    base_url=self.base_url)
