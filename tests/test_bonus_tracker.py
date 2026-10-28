from datetime import date

import httpx

from award_hunter.bonus_tracker import DetectedBonus, last_check, parse_tracker, partner_to_source, sync_bonuses
from award_hunter.transfer_bonuses import TransferEngine

# Trimmed from the real tracker page (October 2026): the "Active" block plus surrounding noise.
SAMPLE_HTML = """
<html><body>
<h2>Amex transfer bonus history by year</h2>
<p>Active Now 2 Total Tracked 176 Partners 30 Avg Bonus 26%</p>
<h2>October 2026 Active</h2>
<a href="/x"><h3>Amex Membership Rewards → AeroMexico ClubPremier</h3><p><span>🇺🇸</span> US</p><span>+20%</span>
<div>Valid: Oct 01 - Oct 30, 2026</div></a>
<a href="/y"><h3>Amex Membership Rewards → Air France KLM Flying Blue</h3><p><span>🇺🇸</span> US</p><span>+25%</span>
<div>Valid: Oct 01 - Oct 31, 2026</div></a>
<a href="/z"><h3>Amex Membership Rewards → Hilton Honors</h3><p><span>🇺🇸</span> US</p><span>+30%</span>
<div>Valid: Dec 20 - Jan 14, 2027</div></a>
<h2>When Is the Next Amex Membership Rewards Transfer Bonus?</h2>
<p>Amex Membership Rewards → Air Canada Aeroplan Medium Last bonus +15% (Sep 2022) Avg frequency Every 368 days</p>
</body></html>
"""


def test_parse_tracker_finds_active_bonuses_only():
    found = parse_tracker(SAMPLE_HTML)
    assert [(b.source, b.bonus_percent) for b in found] == [("aeromexico", 20.0), ("flyingblue", 25.0), ("hilton", 30.0)]
    fb = found[1]
    assert fb.partner_text == "Air France KLM Flying Blue"
    assert fb.effective_date == date(2026, 10, 1) and fb.expiration_date == date(2026, 10, 31)
    # Year-spanning promo keeps the start in the previous year
    hilton = found[2]
    assert hilton.effective_date == date(2026, 12, 20) and hilton.expiration_date == date(2027, 1, 14)
    assert not hilton.is_airline_source and fb.is_airline_source


def test_partner_mapping():
    assert partner_to_source("Air France KLM Flying Blue") == "flyingblue"
    assert partner_to_source("Virgin Atlantic Flying Club") == "virginatlantic"
    assert partner_to_source("Singapore Airlines KrisFlyer") == "singapore"
    assert partner_to_source("Something Unknown") is None


def test_parse_garbage_returns_empty():
    assert parse_tracker("<html><body>Nothing here</body></html>") == []


def test_sync_records_unverified_and_never_applies_silently(db):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=SAMPLE_HTML))
    res = sync_bonuses(db, today=date(2026, 10, 28), transport=transport)
    assert res.ok and len(res.detected) == 3 and [a.source for a in res.added] == ["aeromexico", "flyingblue"]
    rows = db.transfer_bonuses()
    assert len(rows) == 2 and all(r["verified"] == 0 and r["active"] == 1 for r in rows)
    # Unverified -> the engine reports the alternative but does not apply it
    calc = TransferEngine.from_database(db, today=date(2026, 10, 28)).calculate("flyingblue", 60000)
    assert calc.bonus_percent == 0 and calc.amex_points_per_passenger == 60000
    assert any("Unverified 25% bonus" in n for n in calc.notes)
    # Second sync: no duplicates
    res2 = sync_bonuses(db, today=date(2026, 10, 28), transport=transport)
    assert res2.added == [] and len(db.transfer_bonuses()) == 2
    assert last_check(db)["ok"] is True and len(last_check(db)["detected"]) == 3


def test_sync_retires_expired_and_confirmation_applies(db):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=SAMPLE_HTML))
    sync_bonuses(db, today=date(2026, 10, 28), transport=transport)
    fb = next(r for r in db.transfer_bonuses() if r["source"] == "flyingblue")
    db.confirm_bonus(int(fb["id"]), "2026-10-28")
    calc = TransferEngine.from_database(db, today=date(2026, 10, 28)).calculate("flyingblue", 60000)
    assert calc.bonus_percent == 25 and calc.amex_points_per_passenger == 48000 and calc.bonus_verified
    # A later day: expired entries are retired and no longer applied
    res = sync_bonuses(db, today=date(2026, 11, 5), transport=transport)
    assert res.expired == 2 and res.added == []
    calc2 = TransferEngine.from_database(db, today=date(2026, 11, 5)).calculate("flyingblue", 60000)
    assert calc2.bonus_percent == 0


def test_sync_failure_is_soft(db):
    transport = httpx.MockTransport(lambda r: httpx.Response(503))
    res = sync_bonuses(db, today=date(2026, 10, 28), transport=transport)
    assert res.ok is False and "HTTPStatusError" in (res.error or "") and db.transfer_bonuses() == []
    assert last_check(db)["ok"] is False


def test_detected_key_dedupes_same_offer():
    a = DetectedBonus("Flying Blue", "flyingblue", 25.0, date(2026, 10, 1), date(2026, 10, 31))
    b = DetectedBonus("Air France KLM Flying Blue", "flyingblue", 25.0, date(2026, 10, 1), date(2026, 10, 31))
    assert a.key == b.key
