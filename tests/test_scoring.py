from datetime import date

from award_hunter.models import RawAvailability
from award_hunter.normalization import normalize_availability
from award_hunter.scoring import (PRIORITY_PRESETS, ScoringConfig, ScoringWeights, all_in_cost_usd, compare, score_award,
                                  score_round_trip, tier_for)
from award_hunter.transfer_bonuses import Bonus, TransferEngine
from tests.conftest import NOW, make_availability, make_trip

TODAY = date(2026, 10, 28)
ENGINE = TransferEngine(today=TODAY)


def _score(row, engine=ENGINE, cfg=None):
    obs = normalize_availability(RawAvailability.model_validate(row), retrieved_at=NOW)
    amex = engine.calculate(obs.source, obs.miles_per_passenger)
    return score_award(obs, amex, cfg), obs, amex


def _trip(aid, miles, taxes, seats=2, stops=0, segs=None):
    return make_availability(aid, "LAX", "FCO", "2027-04-08", miles=miles, seats=seats, taxes=taxes,
                             trips=[make_trip(aid, "LAX", "FCO", "2027-04-08T15:00:00", "2027-04-09T11:00:00", miles=miles, seats=seats,
                                              taxes=taxes, stops=stops, segments=segs)])


def test_cheaper_points_score_higher():
    s40, _, _ = _score(_trip("a", 40000, 10000))
    s60, _, _ = _score(_trip("b", 60000, 10000))
    s90, _, _ = _score(_trip("c", 90000, 10000))
    assert s40.total > s60.total > s90.total


def test_balanced_preset_matches_original_spec_numbers():
    cfg = ScoringConfig.for_priority("balanced")
    s40, _, _ = _score(_trip("a", 40000, 10000), cfg=cfg)
    s60, _, _ = _score(_trip("b", 60000, 10000), cfg=cfg)
    assert s40.components["points"] == 40.0 and s60.components["points"] == 24.0


def test_default_priority_is_minimize_cash():
    cfg = ScoringConfig()
    assert cfg.priority == "minimize_cash" and cfg.weights.fees > cfg.weights.points
    assert ScoringConfig.for_priority("nonsense").priority == "minimize_cash"
    assert set(PRIORITY_PRESETS) == {"minimize_cash", "balanced", "minimize_points"}


def test_minimize_cash_prefers_low_fees_over_fewer_points():
    cash_first = ScoringConfig.for_priority("minimize_cash")
    points_first = ScoringConfig.for_priority("minimize_points")
    low_fee_more_pts = _trip("a", 65000, 8000)   # 65k pts, $80 fees
    low_pts_high_fee = _trip("b", 45000, 45000)  # 45k pts, $450 fees
    a_cash, _, _ = _score(low_fee_more_pts, cfg=cash_first)
    b_cash, _, _ = _score(low_pts_high_fee, cfg=cash_first)
    a_pts, _, _ = _score(low_fee_more_pts, cfg=points_first)
    b_pts, _, _ = _score(low_pts_high_fee, cfg=points_first)
    assert a_cash.total > b_cash.total, "cash-first must rank the low-fee award higher"
    assert b_pts.total > a_pts.total, "points-first must rank the low-points award higher"


def test_all_in_cost():
    assert all_in_cost_usd(240000, 1600.0, 1.0) == 4000.0
    assert all_in_cost_usd(None, 1600.0, 1.0) is None


def test_high_fees_penalized_and_not_automatically_worse_than_more_points():
    cheap_pts_high_fees, _, _ = _score(_trip("a", 40000, 100000))  # $1000 fees
    more_pts_low_fees, _, _ = _score(_trip("b", 55000, 10000))  # $100 fees
    assert more_pts_low_fees.total > cheap_pts_high_fees.total
    assert any("High one-way fees" in p for p in cheap_pts_high_fees.penalties)


def test_missing_fees_not_rewarded():
    known, _, _ = _score(_trip("a", 50000, 5000))
    unknown, _, _ = _score(_trip("b", 50000, None))
    assert unknown.total < known.total and "Taxes/fees not provided" in unknown.penalties


def test_stale_observation_penalized():
    fresh, _, _ = _score(make_availability("f", "LAX", "FCO", "2027-04-08", updated_hours_ago=2))
    stale, _, _ = _score(make_availability("s", "LAX", "FCO", "2027-04-08", updated_hours_ago=100))
    assert fresh.total > stale.total and any("Stale" in p for p in stale.penalties)


def test_unreported_seats_penalized():
    rep, _, _ = _score(make_availability("r", "LAX", "FCO", "2027-04-08", seats=2))
    unrep, _, _ = _score(make_availability("u", "LAX", "FCO", "2027-04-08", seats=0))
    assert rep.components["confidence"] > unrep.components["confidence"]


def test_mixed_cabin_and_stops_penalized():
    segs = [{"FlightNumber": "A", "OriginAirport": "LAX", "DestinationAirport": "LHR", "DepartsAt": "2027-04-08T15:00:00",
             "ArrivesAt": "2027-04-09T09:00:00", "Cabin": "business"},
            {"FlightNumber": "B", "OriginAirport": "LHR", "DestinationAirport": "FCO", "DepartsAt": "2027-04-09T18:00:00",
             "ArrivesAt": "2027-04-09T21:00:00", "Cabin": "economy"}]
    mixed, _, _ = _score(_trip("m", 50000, 10000, stops=1, segs=segs))
    clean, _, _ = _score(_trip("c", 50000, 10000))
    assert mixed.total < clean.total
    assert "Mixed cabin itinerary" in mixed.penalties and "Layover over 4 hours" in mixed.penalties


def test_non_partner_never_scores_as_deal():
    s, _, amex = _score(make_availability("u", "LAX", "FCO", "2027-04-08", source="united", miles=30000))
    assert not amex.is_amex_partner and s.total <= 49 and s.tier == "Below threshold"


def test_unverified_bonus_lowers_flexibility():
    b = Bonus("flyingblue", 30, None, None, verified=False, account_eligible=False, active=True)
    eng = TransferEngine(bonuses=[b], today=TODAY, apply_unverified_bonuses=True)
    s, _, _ = _score(make_availability("fb", "LAX", "FCO", "2027-04-08", source="flyingblue", miles=60000), eng)
    assert s.components["flexibility"] == 2.0 and "Unverified transfer bonus assumed" in s.penalties


def test_weights_configurable_and_normalized():
    cfg = ScoringConfig(weights=ScoringWeights(points=80, fees=20, convenience=0, confidence=0, quality=0, flexibility=0))
    s, _, _ = _score(_trip("a", 40000, 5000), cfg=cfg)
    assert s.total == 100.0


def test_tiers():
    assert tier_for(95) == "Exceptional" and tier_for(85) == "Excellent" and tier_for(72) == "Worth considering" and tier_for(10) == "Below threshold"


def test_round_trip_score_and_open_jaw_penalty():
    s1, _, _ = _score(_trip("a", 50000, 10000))
    base = score_round_trip(s1, s1, total_points_two=200000, total_fees_usd_two=400, fees_complete=True, open_jaw_europe=False,
                            open_jaw_us=False, trip_days=10)
    oj = score_round_trip(s1, s1, total_points_two=200000, total_fees_usd_two=400, fees_complete=True, open_jaw_europe=True,
                          open_jaw_us=False, trip_days=10)
    assert oj.total < base.total and any("Open-jaw" in p for p in oj.penalties)


def test_compare_explanations():
    reasons = compare("A", {"points": 100000, "fees": 200, "stops": 0, "duration": 600, "age_hours": 2},
                      "B", {"points": 120000, "fees": 600, "stops": 1, "duration": 800, "age_hours": 40})
    assert len(reasons) == 5 and "20,000 fewer" in reasons[0]
