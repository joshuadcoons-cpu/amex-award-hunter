from datetime import date

from award_hunter.transfer_bonuses import Bonus, PartnerTerms, TransferEngine, points_required, round_up_to_increment

TODAY = date(2026, 10, 28)


def test_one_to_one_ratio():
    e = TransferEngine(today=TODAY)
    c = e.calculate("aeroplan", 55000)
    assert c.is_amex_partner and c.amex_points_per_passenger == 55000 and c.amex_points_two_passengers == 110000


def test_increment_rounding_up():
    assert round_up_to_increment(55001, 1000) == 56000
    assert round_up_to_increment(500, 1000) == 1000  # Amex minimum
    assert round_up_to_increment(60001, 250) == 60250


def test_jetblue_250_to_200():
    c = TransferEngine(today=TODAY).calculate("jetblue", 60000)
    assert c.standard_ratio == 0.8 and c.amex_points_per_passenger == 75000


def test_aeromexico_1_to_1_6():
    c = TransferEngine(today=TODAY).calculate("aeromexico", 80000)
    assert c.amex_points_per_passenger == 50000


def test_non_partner_returns_none():
    c = TransferEngine(today=TODAY).calculate("united", 60000)
    assert not c.is_amex_partner and c.amex_points_per_passenger is None


def test_unknown_miles():
    c = TransferEngine(today=TODAY).calculate("aeroplan", None)
    assert c.amex_points_per_passenger is None and "not provided" in c.notes[0]


def test_verified_bonus_applied_with_increment():
    b = Bonus("flyingblue", 25, date(2026, 10, 1), date(2026, 11, 30), verified=True, account_eligible=True, active=True, label="25% bonus")
    c = TransferEngine(bonuses=[b], today=TODAY).calculate("flyingblue", 60000)
    assert c.amex_points_per_passenger == 48000 and c.amex_points_per_passenger_no_bonus == 60000 and c.bonus_percent == 25
    c2 = TransferEngine(bonuses=[b], today=TODAY).calculate("flyingblue", 61000)
    assert c2.amex_points_per_passenger == 49000  # 48,800 rounded up to 1,000


def test_expired_bonus_excluded():
    b = Bonus("flyingblue", 25, date(2026, 1, 1), date(2026, 3, 31), verified=True, account_eligible=True, active=True)
    c = TransferEngine(bonuses=[b], today=TODAY).calculate("flyingblue", 60000)
    assert c.amex_points_per_passenger == 60000 and c.bonus_percent == 0


def test_future_bonus_excluded():
    b = Bonus("flyingblue", 25, date(2027, 1, 1), None, verified=True, account_eligible=True, active=True)
    assert TransferEngine(bonuses=[b], today=TODAY).calculate("flyingblue", 60000).bonus_percent == 0


def test_inactive_bonus_excluded():
    b = Bonus("flyingblue", 25, None, None, verified=True, account_eligible=True, active=False)
    assert TransferEngine(bonuses=[b], today=TODAY).calculate("flyingblue", 60000).bonus_percent == 0


def test_unverified_bonus_not_applied_but_reported():
    b = Bonus("flyingblue", 30, None, None, verified=False, account_eligible=False, active=True)
    c = TransferEngine(bonuses=[b], today=TODAY).calculate("flyingblue", 65000)
    assert c.amex_points_per_passenger == 65000 and c.bonus_percent == 0
    assert any("Unverified 30% bonus" in n and "50,000" in n for n in c.notes)


def test_unverified_bonus_applied_when_allowed_flags_it():
    b = Bonus("flyingblue", 30, None, None, verified=False, account_eligible=False, active=True)
    c = TransferEngine(bonuses=[b], today=TODAY, apply_unverified_bonuses=True).calculate("flyingblue", 65000)
    assert c.amex_points_per_passenger == 50000 and c.bonus_verified is False


def test_engine_from_database(db):
    db.add_transfer_bonus("aeroplan", 20, "2026-10-01", "2026-12-31", verified=True, account_eligible=True)
    db.update_transfer_partner("qatar", amex_points_in=1000, miles_out=1000, transfer_increment=1000)
    e = TransferEngine.from_database(db, today=TODAY)
    assert e.calculate("aeroplan", 60000).amex_points_per_passenger == 50000
    assert e.calculate("qatar", 70000).amex_points_per_passenger == 70000


def test_points_required_direct():
    terms = PartnerTerms("x", "X", True, 1000, 1000, 1000)
    assert points_required(70000, terms) == 70000 and points_required(0, terms) is None
