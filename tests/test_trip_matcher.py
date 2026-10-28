from datetime import date

from award_hunter.models import RawAvailability
from award_hunter.normalization import normalize_many
from award_hunter.scoring import ScoringConfig
from award_hunter.search import SearchProfile, score_and_filter
from award_hunter.transfer_bonuses import TransferEngine
from award_hunter.trip_matcher import build_round_trip, match_round_trips, round_trip_rows
from tests.conftest import NOW, make_availability, make_trip

TODAY = date(2026, 10, 28)


def _scored(rows, profile):
    awards = normalize_many([RawAvailability.model_validate(r) for r in rows], retrieved_at=NOW)
    good, _ = score_and_filter(awards, profile, TransferEngine(today=TODAY), ScoringConfig())
    return good


def _ret(aid, origin, dest, day, **kw):
    return make_availability(aid, origin, dest, day, origin_region="Europe", dest_region="North America", **kw)


def test_open_jaw_round_trip_example():
    """Spec example: LAX->FCO Apr 8 @50k, MXP->SFO Apr 20 @55k => 210k for two, open-jaw, 12 days."""
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30)
    out = _scored([make_availability("o", "LAX", "FCO", "2027-04-08", miles=50000, taxes=10000)], p)
    inb = _scored([_ret("i", "MXP", "SFO", "2027-04-20", miles=55000, taxes=15000)], p)
    trips = match_round_trips(out, inb, p)
    assert len(trips) == 1
    t = trips[0]
    assert t.trip_days == 12 and t.total_amex_points_two == 210000
    assert t.open_jaw_europe and t.open_jaw_us
    assert t.fees_complete and t.total_fees_two_major == 500.0
    assert any("Open-jaw" in w for w in t.warnings) and any("positioning" in w.lower() for w in t.warnings)
    assert t.destination_city == "Rome"
    assert round_trip_rows(trips)[0]["amex_total_two"] == 210000


def test_rejects_out_of_window_and_before_arrival():
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30, min_trip_days=7, max_trip_days=14)
    out = _scored([make_availability("o", "LAX", "FCO", "2027-04-08")], p)
    inb = _scored([_ret("i1", "FCO", "LAX", "2027-04-10"), _ret("i2", "FCO", "LAX", "2027-04-30"), _ret("i3", "FCO", "LAX", "2027-04-05")], p)
    assert match_round_trips(out, inb, p) == []
    assert build_round_trip(out[0], inb[2], p, ScoringConfig()) is None


def test_open_jaw_can_be_disabled():
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30, include_open_jaw=False)
    out = _scored([make_availability("o", "LAX", "FCO", "2027-04-08")], p)
    inb = _scored([_ret("i", "MXP", "LAX", "2027-04-18")], p)
    assert match_round_trips(out, inb, p) == []
    inb2 = _scored([_ret("i2", "FCO", "LAX", "2027-04-18")], p)
    t = match_round_trips(out, inb2, p)
    assert len(t) == 1 and not t[0].open_jaw_europe and not t[0].open_jaw_us


def test_fees_partial_when_one_leg_missing():
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30)
    out = _scored([make_availability("o", "LAX", "FCO", "2027-04-08", taxes=None)], p)
    inb = _scored([_ret("i", "FCO", "LAX", "2027-04-18", taxes=12000)], p)
    t = match_round_trips(out, inb, p)[0]
    assert not t.fees_complete and t.total_fees_two_major == 240.0
    assert any("partial" in w for w in t.warnings)


def test_insufficient_reported_seats_rejected():
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30, include_unknown_seat_counts=True)
    out = _scored([make_availability("o", "LAX", "FCO", "2027-04-08")], p)
    one_seat = normalize_many([RawAvailability.model_validate(_ret("i", "FCO", "LAX", "2027-04-18", seats=1))], retrieved_at=NOW)
    from award_hunter.models import ScoredAward
    from award_hunter.scoring import score_award
    amex = TransferEngine(today=TODAY).calculate("aeroplan", 55000)
    inb = ScoredAward(award=one_seat[0], amex=amex, score=score_award(one_seat[0], amex))
    assert build_round_trip(out[0], inb, p, ScoringConfig()) is None


def test_unknown_seats_round_trip_is_flagged():
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30, include_unknown_seat_counts=True)
    out = _scored([make_availability("o", "LAX", "FCO", "2027-04-08", seats=0)], p)
    inb = _scored([_ret("i", "FCO", "LAX", "2027-04-18")], p)
    t = match_round_trips(out, inb, p)[0]
    assert any("seat count not reported" in w for w in t.warnings)


def test_max_total_points_cap():
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30)
    out = _scored([make_availability("o", "LAX", "FCO", "2027-04-08", miles=65000)], p)
    inb = _scored([_ret("i", "FCO", "LAX", "2027-04-18", miles=65000)], p)
    assert match_round_trips(out, inb, p, max_total_points=240000) == []
    assert len(match_round_trips(out, inb, p, max_total_points=260000)) == 1
