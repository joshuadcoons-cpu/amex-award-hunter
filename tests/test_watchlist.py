from datetime import date

import httpx

from award_hunter.alerts import AlertThresholds, build_watch_alerts
from award_hunter.api_client import DailyBudget, SeatsAeroClient
from award_hunter.models import RawAvailability, ScoredAward
from award_hunter.normalization import normalize_availability
from award_hunter.scheduler import run_scan, save_profile
from award_hunter.scoring import score_award
from award_hunter.search import SearchProfile
from award_hunter.transfer_bonuses import TransferEngine
from award_hunter.transfer_partners import sweet_spot
from award_hunter.watchlist import Watch, award_matches, match_watches, profile_with_watches, resolve_destinations
from tests.conftest import NOW, make_availability, search_response

TODAY = date(2026, 10, 28)


def _scored(row) -> ScoredAward:
    obs = normalize_availability(RawAvailability.model_validate(row), retrieved_at=NOW)
    amex = TransferEngine(today=TODAY).calculate(obs.source, obs.miles_per_passenger)
    return ScoredAward(award=obs, amex=amex, score=score_award(obs, amex))


def _watch(**kw) -> Watch:
    base = dict(id=1, name="Rome in May", destinations=["FCO"], date_from=date(2027, 5, 1), date_to=date(2027, 5, 20))
    base.update(kw)
    return Watch(**base)


def test_resolve_destinations_from_countries_and_codes():
    codes = resolve_destinations(["fco"], ["Spain"])
    assert "FCO" in codes and "MAD" in codes and "BCN" in codes and all(c.isupper() for c in codes)


def test_award_matches_ignores_score_but_respects_seats_dates_destination():
    w = _watch()
    ok = _scored(make_availability("a", "LAX", "FCO", "2027-05-10", miles=120000, seats=2, taxes=90000))  # terrible score, still matches
    assert award_matches(w, ok)
    assert not award_matches(w, _scored(make_availability("b", "LAX", "FCO", "2027-05-10", seats=1)))
    assert not award_matches(w, _scored(make_availability("c", "LAX", "FCO", "2027-06-10", seats=2)))
    assert not award_matches(w, _scored(make_availability("d", "LAX", "MAD", "2027-05-10", seats=2)))


def test_award_matches_returns_and_options():
    w = _watch(nonstop_only=True, max_points_pp=60000)
    ret = _scored(make_availability("r", "FCO", "LAX", "2027-05-20", seats=3, miles=55000, origin_region="Europe", dest_region="North America"))
    assert award_matches(w, ret)  # return inside date_from+7 .. date_to+14
    late = _scored(make_availability("r2", "FCO", "LAX", "2027-06-20", seats=3, miles=55000, origin_region="Europe", dest_region="North America"))
    assert not award_matches(w, late)
    pricey = _scored(make_availability("p", "LAX", "FCO", "2027-05-10", seats=2, miles=75000))
    assert not award_matches(w, pricey)
    conn = _scored(make_availability("c", "LAX", "FCO", "2027-05-10", seats=2, miles=55000, direct=False))
    assert not award_matches(w, conn)


def test_profile_with_watches_widens_window_destinations_and_unexcludes_country():
    p = SearchProfile(start_date=date(2026, 11, 11), window_days=90, destinations=["FCO", "MXP"], excluded_destination_countries=["France"])
    w = _watch(destinations=["CDG"], date_from=date(2027, 4, 1), date_to=date(2027, 4, 30))
    p2 = profile_with_watches(p, [w], today=TODAY)
    assert p2.start_date == date(2026, 11, 11) and p2.end_date >= date(2027, 4, 30)
    assert "CDG" in p2.destinations and p2.excluded_destination_countries == []
    # Inactive or past watches change nothing
    assert profile_with_watches(p, [_watch(active=False)], today=TODAY) == p
    assert profile_with_watches(p, [_watch(date_from=date(2026, 1, 1), date_to=date(2026, 2, 1))], today=TODAY) == p


def test_run_scan_matches_watches_and_alerts(settings, db):
    rows = [make_availability("w1", "LAX", "FCO", "2027-05-10", seats=2, miles=130000, taxes=5000),   # over the profile cap -> non-qualifying
            make_availability("w2", "LAX", "FCO", "2027-05-11", seats=1, miles=60000, taxes=5000)]
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=search_response(rows)))
    save_profile(db, SearchProfile(origins=["LAX"], destinations=["FCO"], start_date=date(2027, 5, 1), window_days=30, max_days_per_query=90,
                                   include_return_search=False))
    wid = db.add_watch("Rome in May", ["FCO"], "2027-05-01", "2027-05-20")
    client = SeatsAeroClient(settings, budget=DailyBudget(50), transport=transport, sleep=lambda s: None)
    out = run_scan(settings, db, trigger="test", client=client, send_alerts=False)
    assert out.watch_hits and [s.award.availability_id for s in out.watch_hits[wid]] == ["w1"]
    assert db.watches()[0]["match_count"] == 1
    alerts = [a for a in out.alerts if a.kind == "watch"]
    assert len(alerts) == 1 and alerts[0].priority == "urgent" and "Rome in May" in alerts[0].title and "2 seats" in alerts[0].headline


def test_watch_alert_dedupes_by_availability():
    w = _watch()
    s = _scored(make_availability("a", "LAX", "FCO", "2027-05-10", seats=2))
    alerts = build_watch_alerts({1: [s]}, {1: w})
    assert alerts[0].fingerprint == "watch|1|a|2"


def test_sweet_spots():
    assert sweet_spot("aeroplan", 60000) == "best" and sweet_spot("aeroplan", 70000) == "sweet" and sweet_spot("aeroplan", 75000) is None
    assert sweet_spot("flyingblue", 45000) == "sweet" and sweet_spot("flyingblue", 37500) == "best"
    assert sweet_spot("emirates", 10000) is None and sweet_spot("aeroplan", None) is None
