import json
from datetime import date

import httpx
import pytest

from award_hunter.api_client import (
    ApiAuthError, ApiError, BudgetExhaustedError, DailyBudget, InMemoryCache, RateLimitError, ResponseShapeError, SeatsAeroClient,
)
from award_hunter.config import ConfigError, Settings
from tests.conftest import FAKE_KEY, make_availability, search_response


def test_bearer_header_default(client, mock_api):
    client.search("LAX", "FCO", date(2027, 4, 1), date(2027, 4, 30))
    req = mock_api.requests[0]
    assert req.headers["Partner-Authorization"] == f"Bearer {FAKE_KEY}"
    assert "Authorization" not in req.headers
    assert FAKE_KEY not in str(req.url)  # never in query string


def test_raw_header_scheme(settings, mock_api):
    settings.auth_scheme = "raw"
    with SeatsAeroClient(settings, transport=mock_api.transport(), sleep=lambda s: None) as c:
        c.search("LAX", "FCO", date(2027, 4, 1), date(2027, 4, 30))
    assert mock_api.requests[0].headers["Partner-Authorization"] == FAKE_KEY


def test_query_parameter_serialization(client, mock_api):
    client.search(["SAN", "LAX"], ["FCO", "MXP"], date(2027, 4, 1), date(2027, 6, 29), sources=["aeroplan", "flyingblue"],
                  take=5000, include_trips=True, only_direct=False)
    q = dict(mock_api.requests[0].url.params)
    assert q["origin_airport"] == "SAN,LAX"
    assert q["destination_airport"] == "FCO,MXP"
    assert q["start_date"] == "2027-04-01" and q["end_date"] == "2027-06-29"
    assert q["cabins"] == "business"
    assert q["sources"] == "aeroplan,flyingblue"
    assert q["take"] == "1000"  # clamped to API max
    assert q["include_trips"] == "true" and q["only_direct_flights"] == "false"
    assert q["order_by"] == "lowest_mileage"
    assert mock_api.requests[0].url.path == "/partnerapi/search"


def test_missing_key_raises_config_error(mock_api):
    s = Settings(api_key=None)
    with SeatsAeroClient(s, transport=mock_api.transport()) as c:
        with pytest.raises(ConfigError):
            c.search("LAX", "FCO", "2027-04-01", "2027-04-30")
    assert mock_api.requests == []


def test_auth_error_401(client, mock_api):
    mock_api.handler = lambda r: httpx.Response(401, json={"error": "unauthorized"})
    with pytest.raises(ApiAuthError) as exc:
        client.search("LAX", "FCO", "2027-04-01", "2027-04-30")
    assert exc.value.status_code == 401
    assert FAKE_KEY not in str(exc.value)


def test_retry_on_5xx_then_success(client, mock_api):
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=search_response([make_availability("x", "LAX", "FCO", "2027-04-01")]))

    mock_api.handler = handler
    resp = client.search("LAX", "FCO", "2027-04-01", "2027-04-30")
    assert calls["n"] == 3 and len(resp.data) == 1


def test_rate_limit_429_exhausts_retries(client, mock_api):
    mock_api.handler = lambda r: httpx.Response(429, text="slow down", headers={"Retry-After": "1"})
    with pytest.raises(RateLimitError):
        client.search("LAX", "FCO", "2027-04-01", "2027-04-30")
    assert len(mock_api.requests) == 3  # 1 + max_retries(2)


def test_quota_header_tracked(client, mock_api):
    mock_api.handler = lambda r: httpx.Response(200, json=search_response([]), headers={"X-RateLimit-Remaining": "742", "X-RateLimit-Limit": "1000"})
    client.search("LAX", "FCO", "2027-04-01", "2027-04-30")
    assert client.last_remaining_quota == 742 and client.last_quota_limit == 1000


def test_daily_budget_enforced(settings, mock_api):
    with SeatsAeroClient(settings, budget=DailyBudget(2), transport=mock_api.transport(), sleep=lambda s: None) as c:
        c.search("LAX", "FCO", "2027-04-01", "2027-04-30")
        c.search("LAX", "FCO", "2027-04-01", "2027-04-30")
        with pytest.raises(BudgetExhaustedError):
            c.search("LAX", "FCO", "2027-04-01", "2027-04-30")
    assert len(mock_api.requests) == 2


def test_budget_persistence_callbacks():
    store = {}
    b = DailyBudget(10, load=lambda day: store.get(day, 0), save=lambda day, used: store.__setitem__(day, used))
    b.consume(3)
    assert b.used == 3 and list(store.values()) == [3]
    b2 = DailyBudget(10, load=lambda day: store.get(day, 0), save=lambda d, u: None)
    assert b2.remaining == 7


def test_cache_avoids_second_call(client, mock_api):
    mock_api.handler = lambda r: httpx.Response(200, json=search_response([make_availability("x", "LAX", "FCO", "2027-04-01")]))
    client.search("LAX", "FCO", "2027-04-01", "2027-04-30", cache_ttl=60)
    client.search("LAX", "FCO", "2027-04-01", "2027-04-30", cache_ttl=60)
    assert len(mock_api.requests) == 1


def test_pagination_follows_cursor_and_dedupes(client, mock_api):
    pages = {
        None: search_response([make_availability("p1", "LAX", "FCO", "2027-04-01"), make_availability("p2", "LAX", "FCO", "2027-04-02")], True, 1700),
        "1700": search_response([make_availability("p2", "LAX", "FCO", "2027-04-02"), make_availability("p3", "LAX", "FCO", "2027-04-03")], False, None),
    }
    mock_api.handler = lambda r: httpx.Response(200, json=pages[r.url.params.get("cursor")])
    ids = [a.ID for page in client.iter_search("LAX", "FCO", "2027-04-01", "2027-04-30") for a in page.data]
    assert ids == ["p1", "p2", "p3"]
    assert mock_api.requests[1].url.params["cursor"] == "1700"


def test_malformed_json_is_shape_error(client, mock_api):
    mock_api.handler = lambda r: httpx.Response(200, text="<html>oops</html>")
    with pytest.raises(ResponseShapeError):
        client.search("LAX", "FCO", "2027-04-01", "2027-04-30")


def test_malformed_schema_is_shape_error(client, mock_api):
    mock_api.handler = lambda r: httpx.Response(200, json={"data": [{"ID": "x"}]})  # missing Route/Date
    with pytest.raises(ResponseShapeError):
        client.search("LAX", "FCO", "2027-04-01", "2027-04-30")


def test_network_error_retries_then_fails(client, mock_api):
    def handler(r):
        raise httpx.ConnectError("boom", request=r)

    mock_api.handler = handler
    with pytest.raises(ApiError):
        client.search("LAX", "FCO", "2027-04-01", "2027-04-30")
    assert len(mock_api.requests) == 3


def test_invalid_cabin_rejected(client):
    with pytest.raises(ValueError):
        client.search("LAX", "FCO", "2027-04-01", "2027-04-30", cabins="suite")


def test_get_trips_and_routes(client, mock_api):
    def handler(r):
        if r.url.path.startswith("/partnerapi/trips/"):
            return httpx.Response(200, json={"data": [], "booking_links": [{"label": "Aeroplan", "link": "https://x", "primary": True}]})
        if r.url.path == "/partnerapi/routes":
            return httpx.Response(200, json={"data": [{"ID": "r", "OriginAirport": "LAX", "DestinationAirport": "FCO"}]})
        return httpx.Response(404)

    mock_api.handler = handler
    t = client.get_trips("abc 123")
    assert t.booking_links[0].link == "https://x"
    assert mock_api.requests[0].url.raw_path.decode().startswith("/partnerapi/trips/abc%20123")
    assert client.get_routes("aeroplan")[0]["OriginAirport"] == "LAX"


def test_connection_test_falls_back_to_raw_scheme(settings, mock_api):
    def handler(r):
        if r.headers["Partner-Authorization"].startswith("Bearer "):
            return httpx.Response(401, text="bad")
        return httpx.Response(200, json=search_response([make_availability("x", "LAX", "LHR", "2026-11-01")]),
                              headers={"X-RateLimit-Remaining": "999"})

    mock_api.handler = handler
    with SeatsAeroClient(settings, transport=mock_api.transport(), sleep=lambda s: None) as c:
        res = c.test_connection()
    assert res.ok and res.auth_scheme_used == "raw" and res.records_returned == 1 and res.remaining_quota == 999


def test_connection_test_reports_failure(settings, mock_api):
    mock_api.handler = lambda r: httpx.Response(403, text="forbidden")
    with SeatsAeroClient(settings, transport=mock_api.transport(), sleep=lambda s: None) as c:
        res = c.test_connection()
    assert not res.ok and res.status_code == 403 and "rejected" in res.message


def test_connection_test_without_key():
    with SeatsAeroClient(Settings(api_key=None)) as c:
        res = c.test_connection()
    assert not res.ok and "not set" in res.message
