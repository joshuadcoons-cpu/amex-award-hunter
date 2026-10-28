from datetime import date, timedelta

import httpx

from award_hunter.models import RawAvailability
from award_hunter.normalization import normalize_many
from award_hunter.scoring import ScoringConfig
from award_hunter.search import INBOUND, OUTBOUND, SearchProfile, build_plan, run_search, score_and_filter, to_rows
from award_hunter.transfer_bonuses import TransferEngine
from tests.conftest import NOW, make_availability, search_response

TODAY = date(2026, 10, 28)


def test_plan_covers_full_window_with_batches():
    p = SearchProfile(origins=["SAN", "LAX", "SFO", "SEA"], destinations=["FCO", "MXP", "MAD", "BCN", "LIS", "AMS", "ZRH", "VIE", "FRA", "MUC", "LHR", "DUB"],
                      start_date=date(2027, 3, 1), window_days=90, max_airports_per_query=10, max_days_per_query=30, include_return_search=False)
    plan = build_plan(p)
    windows = sorted({(c.start, c.end) for c in plan.chunks})
    assert windows[0] == (date(2027, 3, 1), date(2027, 3, 30)) and windows[-1][1] == date(2027, 5, 29)
    assert len(windows) == 3 and len(plan.chunks) == 6  # 3 windows x 2 destination batches
    days = set()
    for s, e in windows:
        d = s
        while d <= e:
            days.add(d)
            d += timedelta(days=1)
    assert len(days) == 90


def test_plan_excludes_france_but_includes_return_direction():
    p = SearchProfile(origins=["LAX"], destinations=["FCO", "CDG"], start_date=date(2027, 3, 1), window_days=10, max_days_per_query=90)
    plan = build_plan(p)
    assert all("CDG" not in c.destinations and "CDG" not in c.origins for c in plan.chunks)
    inbound = [c for c in plan.chunks if c.direction == INBOUND]
    assert inbound and inbound[0].origins == ["FCO"] and inbound[0].destinations == ["LAX"]
    assert inbound[0].start == date(2027, 3, 8) and inbound[-1].end == date(2027, 3, 10) + timedelta(days=14)


def test_plan_empty_when_everything_excluded():
    p = SearchProfile(destinations=["CDG"], excluded_destination_countries=["France"])
    assert build_plan(p).chunks == []


def _scored(rows, profile=None):
    profile = profile or SearchProfile(start_date=date(2027, 4, 1), window_days=30)
    awards = normalize_many([RawAvailability.model_validate(r) for r in rows], retrieved_at=NOW)
    return score_and_filter(awards, profile, TransferEngine(today=TODAY), ScoringConfig())


def test_filters_two_seats_business_partner(sample_rows):
    good, bad = _scored(sample_rows)
    good_ids = {s.award.availability_id for s in good}
    bad_by_id = {s.award.availability_id: s.hidden_reasons for s in bad}
    assert "a1" in good_ids and "a7" in good_ids
    assert "seat_count_not_reported" in bad_by_id["a2"]
    assert "mixed_cabin" in bad_by_id["a3"]
    assert "not_amex_partner" in bad_by_id["a4"]
    assert "excluded_country" in bad_by_id["a6"]
    assert any(r.startswith("seats_reported_below_min") for r in bad_by_id["a8"])


def test_unknown_seats_can_be_included_but_flagged(sample_rows):
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30, include_unknown_seat_counts=True)
    good, _ = _scored(sample_rows, p)
    a2 = next(s for s in good if s.award.availability_id == "a2")
    assert "Seat count not reported by program" in a2.score.penalties


def test_points_threshold():
    row = make_availability("hi", "LAX", "FCO", "2027-04-08", miles=110000)
    _, bad = _scored([row])
    assert any(r.startswith("points_over_max") for r in bad[0].hidden_reasons)


def test_max_stops_filter(sample_rows):
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30, max_stops=0)
    good, bad = _scored(sample_rows, p)
    assert "a1" not in {s.award.availability_id for s in good}


def test_excluded_airline():
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30, excluded_airlines=["AC"])
    _, bad = _scored([make_availability("x", "LAX", "FCO", "2027-04-08")], p)
    assert "excluded_airline" in bad[0].hidden_reasons


def test_run_search_end_to_end_with_coverage(client, mock_api, sample_rows, db):
    def handler(r):
        q = r.url.params
        origins = q["origin_airport"].split(",")
        if "LAX" in origins:  # outbound chunk
            return httpx.Response(200, json=search_response(sample_rows[:6]), headers={"X-RateLimit-Remaining": "950"})
        if "FCO" in origins:  # inbound chunk
            return httpx.Response(200, json=search_response(sample_rows[6:]))
        return httpx.Response(500, text="boom")

    mock_api.handler = handler
    p = SearchProfile(origins=["LAX", "SFO", "SAN"], destinations=["FCO", "MXP", "VCE", "MAD", "MUC", "CDG"], start_date=date(2027, 4, 1),
                      window_days=30, max_days_per_query=90, max_airports_per_query=10)
    res = run_search(client, p, TransferEngine(today=TODAY), db=db, trigger="test")
    assert res.run_id is not None and res.calls_made >= 2
    assert {s.award.availability_id for s in res.outbound} == {"a1"}
    assert {s.award.availability_id for s in res.inbound} == {"a7"}
    assert res.coverage_summary().get("ok") == 2 and not res.errors
    assert date(2027, 4, 8) in res.searched_dates(OUTBOUND)
    cov = db.coverage(res.run_id)
    assert len(cov) == 2 and all(c["status"] == "ok" for c in cov)
    rows = to_rows(res.qualifying)
    assert rows[0]["amex_pp"] in (50000, 55000) and rows[0]["verification"] == "requires_airline_verification"


def test_run_search_records_errors_and_continues(client, mock_api, db):
    def handler(r):
        if r.url.params["start_date"] == "2027-04-01":  # first window always fails, even after retries
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=search_response([]))

    mock_api.handler = handler
    p = SearchProfile(origins=["LAX"], destinations=["FCO"], start_date=date(2027, 4, 1), window_days=60, max_days_per_query=30,
                      include_return_search=False)
    res = run_search(client, p, TransferEngine(today=TODAY), db=db)
    statuses = [c.status for c in res.coverage]
    assert statuses == ["error", "empty"] and len(res.errors) == 1
    assert date(2027, 4, 1) in res.failed_dates(OUTBOUND) and date(2027, 5, 1) in res.searched_dates(OUTBOUND)
    assert db.last_run()["status"] == "running"  # finish_run is the scheduler's job


def test_run_search_aborts_on_auth_error(client, mock_api, db):
    mock_api.handler = lambda r: httpx.Response(401, text="nope")
    p = SearchProfile(origins=["LAX"], destinations=["FCO", "MXP"], start_date=date(2027, 4, 1), window_days=10, max_airports_per_query=1)
    res = run_search(client, p, TransferEngine(today=TODAY), db=db)
    assert res.aborted and len(mock_api.requests) == 1
    assert {c.status for c in res.coverage} == {"error", "skipped"}


def test_blank_results_are_empty_not_zero_seats(client, mock_api, db):
    p = SearchProfile(origins=["LAX"], destinations=["FCO"], start_date=date(2027, 4, 1), window_days=5, include_return_search=False)
    res = run_search(client, p, TransferEngine(today=TODAY), db=db)
    assert res.coverage[0].status == "empty" and res.qualifying == []


def test_stale_observation_does_not_qualify():
    fresh = make_availability("f", "LAX", "FCO", "2027-04-08", updated_hours_ago=20)
    stale = make_availability("s", "LAX", "FCO", "2027-04-09", updated_hours_ago=24 * 30)
    good, bad = _scored([fresh, stale])
    assert [s.award.availability_id for s in good] == ["f"]
    assert any(r.startswith("stale_observation") for r in bad[0].hidden_reasons)
