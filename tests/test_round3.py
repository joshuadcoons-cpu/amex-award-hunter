from datetime import date, datetime, timedelta, timezone

import httpx

from award_hunter.cash_fares import AmadeusClient, attach_cash_fares, configured, fare_for
from award_hunter.config import Settings
from award_hunter.models import Cabin, RawAvailability
from award_hunter.normalization import normalize_availability, normalize_many
from award_hunter.scheduler import health_check
from award_hunter.scoring import score_award
from award_hunter.search import SearchProfile
from award_hunter.transfer_bonuses import TransferEngine
from award_hunter.trip_matcher import match_round_trips
from award_hunter.models import ScoredAward
from tests.conftest import FAKE_KEY, NOW, make_availability, make_trip


def _pe_row(aid="pe", seats=3, miles=42000, taxes=9000, j_miles=70000, j_taxes=20000):
    row = make_availability(aid, "LAX", "AMS", "2027-04-08", source="flyingblue", miles=j_miles, seats=2, taxes=j_taxes, airlines="KL")
    row.update({"WAvailable": True, "WDirect": True, "WMileageCost": str(miles), "WMileageCostRaw": miles, "WRemainingSeats": seats,
                "WAirlines": "KL", "WTotalTaxes": taxes})
    return row


def test_premium_economy_normalization_is_separate_observation():
    row = _pe_row()
    both = normalize_many([RawAvailability.model_validate(row)], retrieved_at=NOW, cabins=(Cabin.BUSINESS, Cabin.PREMIUM))
    assert [o.cabin for o in both] == [Cabin.BUSINESS, Cabin.PREMIUM]
    j, w = both
    assert j.availability_id == "pe" and w.availability_id == "pe:W"
    assert w.miles_per_passenger == 42000 and w.remaining_seats == 3 and w.taxes_minor_units == 9000
    assert j.internal_id != w.internal_id
    # Business-only normalization ignores W entirely
    only = normalize_many([RawAvailability.model_validate(row)], retrieved_at=NOW)
    assert [o.cabin for o in only] == [Cabin.BUSINESS]
    # Row with no W availability yields None for premium
    plain = make_availability("x", "LAX", "AMS", "2027-04-08")
    assert normalize_availability(RawAvailability.model_validate(plain), retrieved_at=NOW, cabin=Cabin.PREMIUM) is None


def test_premium_economy_scores_below_business():
    row = _pe_row(miles=60000, taxes=20000, j_miles=60000, j_taxes=20000)  # identical price to the business row
    obs = normalize_many([RawAvailability.model_validate(row)], retrieved_at=NOW, cabins=(Cabin.BUSINESS, Cabin.PREMIUM))
    eng = TransferEngine(today=date(2026, 10, 28))
    scores = [score_award(o, eng.calculate(o.source, o.miles_per_passenger)) for o in obs]
    assert scores[1].total < scores[0].total and "Premium economy, not business" in scores[1].penalties


def _scored(row):
    obs = normalize_availability(RawAvailability.model_validate(row), retrieved_at=NOW)
    amex = TransferEngine(today=date(2026, 10, 28)).calculate(obs.source, obs.miles_per_passenger)
    return ScoredAward(award=obs, amex=amex, score=score_award(obs, amex))


def test_aeroplan_stopover_tip_in_trip_warnings():
    segs = [{"FlightNumber": "LH 453", "OriginAirport": "LAX", "DestinationAirport": "FRA", "DepartsAt": "2027-04-08T15:00:00",
             "ArrivesAt": "2027-04-09T11:00:00", "Cabin": "business"},
            {"FlightNumber": "LH 230", "OriginAirport": "FRA", "DestinationAirport": "FCO", "DepartsAt": "2027-04-09T13:00:00",
             "ArrivesAt": "2027-04-09T15:00:00", "Cabin": "business"}]
    out = _scored(make_availability("o", "LAX", "FCO", "2027-04-08", trips=[make_trip("o", "LAX", "FCO", "2027-04-08T15:00:00", "2027-04-09T15:00:00",
                                                                                        stops=1, segments=segs)]))
    ret = _scored(make_availability("r", "FCO", "LAX", "2027-04-18", origin_region="Europe", dest_region="North America"))
    trips = match_round_trips([out], [ret], SearchProfile(start_date=date(2027, 4, 1), window_days=30, origins=["LAX"], home_airport="LAX"))
    assert any("Aeroplan tip: add a stopover in Frankfurt" in w for w in trips[0].warnings)


def test_expire_watches(db):
    old = db.add_watch("past", ["FCO"], "2026-01-01", "2026-01-10")
    live = db.add_watch("future", ["FCO"], "2027-05-01", "2027-05-20")
    assert db.expire_watches("2026-10-28") == 1
    rows = {r["id"]: r for r in db.watches()}
    assert rows[old]["active"] == 0 and "[expired]" in rows[old]["notes"] and rows[live]["active"] == 1


def test_health_check(db):
    settings = Settings(api_key=FAKE_KEY, macos_notifications=False)
    healthy, hours = health_check(settings, db, send=False)
    assert not healthy and hours is None
    run = db.start_run("scheduled", {})
    db.finish_run(run, "ok", calls_made=1, records_seen=1, qualifying=0, round_trips=0, new_awards=0, errors=[], summary="x")
    healthy, hours = health_check(settings, db, send=False)
    assert healthy and hours is not None and hours < 1
    # Stale run -> unhealthy; with no channel the alert is recorded, not sent
    db.execute("UPDATE search_runs SET finished_at = ?", ((datetime.now(timezone.utc) - timedelta(hours=50)).isoformat(),))
    healthy, _ = health_check(settings, db, send=True)
    assert not healthy and db.alerts()[0]["kind"] == "health"


def test_route_history(db):
    s = _scored(make_availability("h1", "LAX", "FCO", "2027-04-08", miles=60000))
    run = db.start_run("test", {})
    db.insert_observation(run, s)
    hist = db.route_history(30)
    assert hist[("LAX", "FCO", "aeroplan")]["days"] == 1 and hist[("LAX", "FCO", "aeroplan")]["min"] == 60000


def _amadeus_transport(prices):
    def handler(r: httpx.Request):
        if r.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1799})
        assert r.headers["Authorization"] == "Bearer tok"
        data = [{"price": {"grandTotal": str(p)}, "validatingAirlineCodes": ["LH"]} for p in prices]
        return httpx.Response(200, json={"data": data})
    return httpx.MockTransport(handler)


def test_cash_fares_cached_and_cpp(db):
    settings = Settings(api_key=FAKE_KEY, amadeus_client_id="id", amadeus_client_secret="sec")
    assert configured(settings) and not configured(Settings(api_key=FAKE_KEY))
    client = AmadeusClient(settings, transport=_amadeus_transport([3200.0, 2900.5]))
    f = fare_for(db, client, "LAX", "FCO", date(2027, 4, 8), "business")
    assert f.price_usd == 2900.5 and f.carrier == "LH" and client.calls == 1
    # Second call served from cache
    f2 = fare_for(db, client, "LAX", "FCO", date(2027, 4, 8), "business")
    assert f2.price_usd == 2900.5 and client.calls == 1
    client.close()
    out = _scored(make_availability("o", "LAX", "FCO", "2027-04-08", miles=50000, taxes=5000))
    ret = _scored(make_availability("r", "FCO", "LAX", "2027-04-18", miles=50000, taxes=5000, origin_region="Europe", dest_region="North America"))
    trips = match_round_trips([out], [ret], SearchProfile(start_date=date(2027, 4, 1), window_days=30, origins=["LAX"], home_airport="LAX"))
    calls = attach_cash_fares(db, settings, trips, transport=_amadeus_transport([2900.5]))
    t = trips[0]
    assert t.cash_fare_two_usd == 11602.0 and "¢ per point" in t.cash_fare_note and calls == 1  # LAX-FCO cached, FCO-LAX fetched
    # (11602 - 200) / 200000 * 100 = 5.7¢
    assert "5.7¢" in t.cash_fare_note
    # Not configured -> no-op
    assert attach_cash_fares(db, Settings(api_key=FAKE_KEY), trips) == 0


def test_deal_card_total_points_survive_history_badge():
    from award_hunter.scoring import ScoringConfig
    from award_hunter.ui import deal_card
    out = _scored(make_availability("o", "LAX", "FRA", "2027-01-26", miles=75000, taxes=8000))
    ret = _scored(make_availability("r", "FCO", "LAX", "2027-02-12", miles=90000, taxes=18000, origin_region="Europe", dest_region="North America"))
    t = match_round_trips([out], [ret], SearchProfile(start_date=date(2027, 1, 1), window_days=60, origins=["LAX"], home_airport="LAX", max_trip_days=20))[0]
    history = {("LAX", "FRA", "aeroplan"): {"days": 10, "min": 75000, "median": 80000, "max": 90000},
               ("FCO", "LAX", "aeroplan"): {"days": 10, "min": 90000, "median": 90000, "max": 95000}}
    html = deal_card(t, {}, is_new=False, alternatives=0, cfg=ScoringConfig(), history=history)
    assert "330,000 pts" in html and "30-day low" in html and "90000 pts" not in html
