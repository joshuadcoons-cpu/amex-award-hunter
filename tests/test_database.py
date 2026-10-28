import logging
from datetime import date

from award_hunter.database import Database
from award_hunter.logging_config import RedactingFilter, mask_secret
from award_hunter.models import RawAvailability
from award_hunter.normalization import normalize_availability
from award_hunter.scoring import score_award
from award_hunter.transfer_bonuses import TransferEngine
from award_hunter.models import ScoredAward
from tests.conftest import FAKE_KEY, NOW, make_availability


def _scored(row, miles=None):
    obs = normalize_availability(RawAvailability.model_validate(row), retrieved_at=NOW)
    amex = TransferEngine(today=date(2026, 10, 28)).calculate(obs.source, obs.miles_per_passenger)
    return ScoredAward(award=obs, amex=amex, score=score_award(obs, amex))


def test_migrations_idempotent(tmp_path):
    p = tmp_path / "x.sqlite"
    Database(p).close()
    d = Database(p)
    assert d.schema_version() == 2 and len(d.transfer_partners()) >= 25
    d.close()


def test_observation_history_and_change_detection(db):
    run1 = db.start_run("test", {})
    s1 = _scored(make_availability("a", "LAX", "FCO", "2027-04-08", miles=60000, seats=1))
    db.insert_observation(run1, s1)
    ch = db.upsert_current(run1, s1, "outbound")
    assert [c["change_type"] for c in ch] == ["new"]

    run2 = db.start_run("test", {})
    s2 = _scored(make_availability("a", "LAX", "FCO", "2027-04-08", miles=50000, seats=2))
    db.insert_observation(run2, s2)
    ch = db.upsert_current(run2, s2, "outbound")
    kinds = {c["change_type"] for c in ch}
    assert {"price_drop", "seats_increased"} <= kinds
    hist = db.award_history("a")
    assert [h["miles"] for h in hist] == [60000, 50000]  # history preserved, not overwritten
    cur = db.current_awards()
    assert len(cur) == 1 and cur[0]["amex_points_pp"] == 50000 and cur[0]["seats"] == 2

    # unchanged re-observation -> no change rows
    assert db.upsert_current(run2, s2, "outbound") == []


def test_disappeared_detection(db):
    run = db.start_run("t", {})
    s = _scored(make_availability("gone", "LAX", "FCO", "2027-04-08"))
    db.upsert_current(run, s, "outbound")
    n = db.mark_disappeared(run, set(), "outbound", "2027-04-01", "2027-04-30", ["LAX"], ["FCO"])
    assert n == 1 and db.current_awards() == [] and db.recent_changes()[0]["change_type"] == "disappeared"
    # out of scope awards are untouched
    s2 = _scored(make_availability("keep", "SFO", "MAD", "2027-06-01"))
    db.upsert_current(run, s2, "outbound")
    assert db.mark_disappeared(run, set(), "outbound", "2027-04-01", "2027-04-30", ["LAX"], ["FCO"]) == 0


def test_settings_cache_budget(db):
    db.set_setting("profile", {"origins": ["LAX"]})
    assert db.get_setting("profile") == {"origins": ["LAX"]}
    db.cache_set("k", {"x": 1}, 60)
    assert db.cache_get("k") == {"x": 1}
    db.cache_set("old", {"x": 1}, -5)
    assert db.cache_get("old") is None
    db.budget_save("2026-10-28", 7)
    assert db.budget_load("2026-10-28") == 7 and db.budget_load("2026-10-29") == 0


def test_alert_dedupe(db):
    assert not db.alert_already_sent("fp", "new_award")
    db.record_alert("fp", "new_award", "webhook", 85, {"t": 1}, ok=True)
    assert db.alert_already_sent("fp", "new_award") and not db.alert_already_sent("fp", "exceptional")


def test_api_key_never_in_database_or_logs(db, caplog):
    db.set_setting("anything", {"note": "no secrets"})
    dump = "".join(str(dict(r)) for r in db.query("SELECT * FROM app_settings"))
    assert FAKE_KEY not in dump
    logger = logging.getLogger("redaction-test")
    logger.addFilter(RedactingFilter([FAKE_KEY]))
    with caplog.at_level(logging.INFO):
        logger.info("sending header Partner-Authorization: Bearer %s", FAKE_KEY)
    assert FAKE_KEY not in caplog.text and "[REDACTED]" in caplog.text
    assert mask_secret(FAKE_KEY).startswith("pro_") and len(mask_secret(FAKE_KEY)) < 12
