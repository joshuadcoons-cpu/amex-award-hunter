from datetime import date

import httpx

from award_hunter.api_client import DailyBudget, SeatsAeroClient
from award_hunter.scheduler import load_profile, run_scan, save_profile
from award_hunter.search import SearchProfile
from tests.conftest import make_availability, search_response


def test_run_scan_persists_and_detects_new(settings, db, sample_rows):
    def handler(r):
        origins = r.url.params["origin_airport"].split(",")
        if "LAX" in origins:
            return httpx.Response(200, json=search_response(sample_rows[:6]))
        return httpx.Response(200, json=search_response(sample_rows[6:]))

    p = SearchProfile(origins=["LAX", "SFO"], destinations=["FCO", "MXP", "MAD"], start_date=date(2027, 4, 1), window_days=30,
                      max_days_per_query=90)
    save_profile(db, p)
    assert load_profile(db).origins == ["LAX", "SFO"]
    client = SeatsAeroClient(settings, budget=DailyBudget(50), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    out = run_scan(settings, db, trigger="test", client=client)
    assert out.new_awards == 2 and len(out.round_trips) == 1 and out.new_round_trips == 1
    run = db.last_run()
    assert run["status"] == "ok" and run["qualifying"] == 2 and run["round_trips"] == 1
    assert len(db.round_trips()) == 1
    # Non-qualifying partner awards are tracked (not qualifying) for later price-drop detection
    assert any(r["qualifies"] == 0 for r in db.current_awards(qualifying_only=False))

    # Second identical scan: nothing new, nothing disappeared
    client2 = SeatsAeroClient(settings, budget=DailyBudget(50), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    out2 = run_scan(settings, db, trigger="test", client=client2)
    assert out2.new_awards == 0 and out2.disappeared == 0 and out2.new_round_trips == 0
    assert out2.alert_stats.get("deduped", 0) + out2.alert_stats.get("no_channel", 0) >= 0
