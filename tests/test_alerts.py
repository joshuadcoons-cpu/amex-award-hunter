from datetime import date

import httpx

from award_hunter.alerts import Alert, AlertSender, AlertThresholds, build_alerts, build_summary_alert, cap_alerts, fmt_pacific
from award_hunter.config import Settings
from award_hunter.models import RawAvailability, ScoredAward
from award_hunter.normalization import normalize_availability
from award_hunter.scoring import score_award
from award_hunter.search import SearchProfile
from award_hunter.transfer_bonuses import TransferEngine
from award_hunter.trip_matcher import match_round_trips
from tests.conftest import FAKE_KEY, NOW, make_availability


def _scored(row):
    obs = normalize_availability(RawAvailability.model_validate(row), retrieved_at=NOW)
    amex = TransferEngine(today=date(2026, 10, 28)).calculate(obs.source, obs.miles_per_passenger)
    return ScoredAward(award=obs, amex=amex, score=score_award(obs, amex))


def test_build_alerts_new_and_exceptional_and_round_trip():
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=5000))
    meh = _scored(make_availability("m", "LAX", "FCO", "2027-04-09", miles=70000, seats=2, taxes=60000))
    ret = _scored(make_availability("r", "FCO", "LAX", "2027-04-18", miles=40000, seats=2, taxes=5000, origin_region="Europe",
                                    dest_region="North America"))
    changes = {"g": [{"change_type": "new", "summary": "x"}], "m": [{"change_type": "new", "summary": "x"}]}
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30)
    trips = match_round_trips([good], [ret], p)
    alerts = build_alerts([good, meh], changes, trips, {t.fingerprint for t in trips}, AlertThresholds())
    kinds = [a.kind for a in alerts]
    assert "exceptional" in kinds or "new_award" in kinds
    assert "round_trip" in kinds
    assert all("m" != a.fingerprint for a in alerts)  # low score never alerts
    body = next(a for a in alerts if a.kind == "round_trip").body
    assert "Amex points: 160,000 total for two" in body and "Requires airline verification" in body and FAKE_KEY not in body
    assert "Trip length: 10 days" in body
    rt = next(a for a in alerts if a.kind == "round_trip")
    assert rt.headline.startswith("Rome") and "$200 cash" in rt.headline and "160k pts for 2" in rt.headline
    assert rt.click_url and rt.click_url.startswith("https://")


def test_dedupe_and_webhook_delivery(db):
    sent = []
    transport = httpx.MockTransport(lambda r: (sent.append(r), httpx.Response(200))[1])
    settings = Settings(api_key=FAKE_KEY, alert_webhook_url="https://hooks.example.test/abc")
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=5000))
    alerts = build_alerts([good], {"g": [{"change_type": "new", "summary": "x"}]}, [], set(), AlertThresholds())
    sender = AlertSender(settings, db, transport=transport)
    stats = sender.send_all(alerts)
    assert stats["sent"] == 1 and len(sent) == 1
    assert FAKE_KEY not in sent[0].content.decode()
    stats2 = sender.send_all(alerts)
    assert stats2["deduped"] == 1 and len(sent) == 1


def test_failed_delivery_not_marked_sent(db):
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    settings = Settings(api_key=FAKE_KEY, alert_webhook_url="https://hooks.example.test/abc")
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=5000))
    alerts = build_alerts([good], {"g": [{"change_type": "new", "summary": "x"}]}, [], set(), AlertThresholds())
    stats = AlertSender(settings, db, transport=transport).send_all(alerts)
    assert stats["failed"] == 1 and not db.alert_already_sent(alerts[0].fingerprint, alerts[0].kind)


def test_no_channel_records_without_sending(db):
    settings = Settings(api_key=FAKE_KEY, macos_notifications=False)
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=5000))
    alerts = build_alerts([good], {"g": [{"change_type": "new", "summary": "x"}]}, [], set(), AlertThresholds())
    stats = AlertSender(settings, db).send_all(alerts)
    assert stats["no_channel"] == 1 and db.alerts()[0]["channel"] == "none"


def test_email_delivery_uses_smtp(db):
    class FakeSMTP:
        sent = []

        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            FakeSMTP.sent.append(msg)

    settings = Settings(api_key=FAKE_KEY, alert_email_to="me@example.test", smtp_host="smtp.example.test", smtp_username="u", smtp_password="p")
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=5000))
    alerts = build_alerts([good], {"g": [{"change_type": "new", "summary": "x"}]}, [], set(), AlertThresholds())
    stats = AlertSender(settings, db, smtp_factory=FakeSMTP).send_all(alerts)
    assert stats["sent"] == 1 and FakeSMTP.sent[0]["To"] == "me@example.test"


def test_pacific_time_formatting():
    assert fmt_pacific(NOW) == "2026-10-28 05:00 PDT"


def _good_round_trip(fee_minor_each: int):
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=fee_minor_each))
    ret = _scored(make_availability("r", "FCO", "LAX", "2027-04-18", miles=40000, seats=2, taxes=fee_minor_each, origin_region="Europe",
                                    dest_region="North America"))
    p = SearchProfile(start_date=date(2027, 4, 1), window_days=30)
    return match_round_trips([good], [ret], p)


def test_round_trip_alert_respects_cash_cap():
    cheap = _good_round_trip(5000)      # $50 per leg per person -> $200 for two
    pricey = _good_round_trip(40000)    # $400 per leg per person -> $1,600 for two
    th = AlertThresholds(round_trip_max_fees_two_usd=600.0, round_trip_min_score=0)
    assert [a.kind for a in build_alerts([], {}, cheap, {t.fingerprint for t in cheap}, th)] == ["round_trip"]
    assert [a.kind for a in build_alerts([], {}, pricey, {t.fingerprint for t in pricey}, th)] == []
    th_no_cap = AlertThresholds(round_trip_max_fees_two_usd=None, round_trip_min_score=0)
    assert [a.kind for a in build_alerts([], {}, pricey, {t.fingerprint for t in pricey}, th_no_cap)] == ["round_trip"]


def test_award_alert_leads_with_cash():
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=5000))
    alerts = build_alerts([good], {"g": [{"change_type": "new", "summary": "x"}]}, [], set(), AlertThresholds())
    assert alerts and alerts[0].body.startswith("Cash out of pocket per person: 50.00 USD")
    assert "$50 cash + 40k pts /person" in alerts[0].headline and "nonstop" in alerts[0].headline


def test_macos_channel_uses_osascript(db):
    calls = []

    class Proc:
        returncode = 0
        stderr = ""

    def fake_run(args, **kw):
        calls.append(args)
        return Proc()

    settings = Settings(api_key=FAKE_KEY)
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=5000))
    alerts = build_alerts([good], {"g": [{"change_type": "new", "summary": "x"}]}, [], set(), AlertThresholds())
    sender = AlertSender(settings, db, platform="darwin", run_command=fake_run)
    assert sender.channels == ["macos"]
    stats = sender.send_all(alerts)
    assert stats["sent"] == 1 and calls[0][0] == "osascript" and "display notification" in calls[0][2]
    assert "Amex Award Hunter" in calls[0][2] and FAKE_KEY not in calls[0][2]
    # Not on a Mac -> channel silently absent.
    assert AlertSender(settings, db, platform="linux", run_command=fake_run).channels == []


def test_macos_failure_reported(db):
    class Proc:
        returncode = 1
        stderr = "execution error"

    settings = Settings(api_key=FAKE_KEY)
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=5000))
    alerts = build_alerts([good], {"g": [{"change_type": "new", "summary": "x"}]}, [], set(), AlertThresholds())
    stats = AlertSender(settings, db, platform="darwin", run_command=lambda *a, **k: Proc()).send_all(alerts)
    assert stats["failed"] == 1 and db.alerts()[0]["error"] == "execution error"


def test_ntfy_delivery_headers(db):
    sent = []
    transport = httpx.MockTransport(lambda r: (sent.append(r), httpx.Response(200))[1])
    settings = Settings(api_key=FAKE_KEY, ntfy_topic="my-secret-topic", macos_notifications=False)
    good = _scored(make_availability("g", "LAX", "FCO", "2027-04-08", miles=40000, seats=2, taxes=5000))
    alerts = build_alerts([good], {"g": [{"change_type": "new", "summary": "x"}]}, [], set(), AlertThresholds())
    sender = AlertSender(settings, db, transport=transport)
    assert sender.channels == ["ntfy"]
    assert sender.send_all(alerts)["sent"] == 1
    req = sent[0]
    assert str(req.url) == "https://ntfy.sh/my-secret-topic"
    assert req.headers["Title"] and req.headers["Priority"] in {"default", "high"} and req.headers["Click"].startswith("https://")
    assert "$50 cash" in req.content.decode() and FAKE_KEY not in req.content.decode()


def test_send_test_alert_reaches_every_channel(db):
    sent = []
    transport = httpx.MockTransport(lambda r: (sent.append(r), httpx.Response(200))[1])
    settings = Settings(api_key=FAKE_KEY, ntfy_topic="t", alert_webhook_url="https://hooks.example.test/x", macos_notifications=False)
    results = AlertSender(settings, db, transport=transport).send_test()
    assert set(results) == {"ntfy", "webhook"} and all(ok for ok, _ in results.values()) and len(sent) == 2
    assert all(h["kind"] == "test" for h in db.alerts())


def test_summary_alert_dedupes_per_day(db):
    trips = _good_round_trip(5000)
    a = build_summary_alert(run_day="2026-10-28", qualifying=5, round_trips=2, new_awards=1, best_trip=trips[0], calls_made=10, errors=0)
    assert a.kind == "summary" and "Rome" in a.body and a.headline == "5 awards · 2 trips · 1 new"
    settings = Settings(api_key=FAKE_KEY, ntfy_topic="t", macos_notifications=False)
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    sender = AlertSender(settings, db, transport=transport)
    assert sender.send_all([a])["sent"] == 1
    assert sender.send_all([a])["deduped"] == 1


def _many_alerts(n: int) -> list:
    return [Alert("round_trip", f"fp{i}", "Complete round trip for two", "body", 70 + i, headline=f"Trip {i}",
                  priority="high" if i == n - 1 else "default") for i in range(n)]


def test_cap_alerts_keeps_strongest_and_folds_rest():
    keep, folded = cap_alerts(_many_alerts(10), 3)
    assert len(keep) == 4 and len(folded) == 7
    assert keep[0].headline == "Trip 9"  # high priority first
    assert keep[-1].kind == "more_deals" and "7 more" in keep[-1].title and "Trip 0" in keep[-1].body
    keep2, folded2 = cap_alerts(_many_alerts(3), 4)
    assert len(keep2) == 3 and not folded2


def test_send_all_caps_per_run_and_folded_never_realert(db):
    sent = []
    transport = httpx.MockTransport(lambda r: (sent.append(r), httpx.Response(200))[1])
    settings = Settings(api_key=FAKE_KEY, ntfy_topic="t", macos_notifications=False)
    sender = AlertSender(settings, db, transport=transport)
    alerts = _many_alerts(10)
    stats = sender.send_all(alerts, max_individual=2)
    assert stats["sent"] == 3 and stats["folded"] == 8 and len(sent) == 3   # 2 individual + 1 digest
    stats2 = sender.send_all(alerts, max_individual=2)
    assert stats2["deduped"] == 10 and stats2["sent"] == 0 and len(sent) == 3
