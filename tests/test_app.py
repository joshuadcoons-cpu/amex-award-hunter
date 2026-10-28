"""Smoke-test every dashboard page with Streamlit's AppTest against a seeded database."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import httpx
import pytest

from award_hunter.api_client import DailyBudget, SeatsAeroClient
from award_hunter.config import Settings
from award_hunter.database import Database
from award_hunter.scheduler import run_scan, save_profile
from award_hunter.search import SearchProfile
from tests.conftest import FAKE_KEY, search_response

APP = Path(__file__).resolve().parent.parent / "app.py"
PAGES = ["Home", "Best Deals", "Search", "Settings"]


@pytest.fixture(scope="module")
def seeded_db_path(tmp_path_factory, request):
    path = tmp_path_factory.mktemp("app") / "app.sqlite"
    from tests.conftest import build_sample_rows
    sample = build_sample_rows()
    db = Database(path)
    save_profile(db, SearchProfile(origins=["LAX", "SFO"], destinations=["FCO", "MXP", "MAD"], start_date=date(2027, 4, 1), window_days=30, max_days_per_query=90))
    db.add_watch("Rome in April", ["FCO"], "2027-04-01", "2027-04-30")
    db.set_setting("points_balance", {"balance": 150000, "monthly_earn": 10000})
    settings = Settings(api_key=FAKE_KEY, database_path=path)

    def handler(r):
        origins = r.url.params["origin_airport"].split(",")
        return httpx.Response(200, json=search_response(sample[:6] if "LAX" in origins else sample[6:]), headers={"X-RateLimit-Remaining": "980"})

    client = SeatsAeroClient(settings, budget=DailyBudget(50), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    out = run_scan(settings, db, trigger="test", client=client, send_alerts=False)
    assert out.round_trips
    db.close()
    return path


@pytest.mark.parametrize("page", PAGES)
def test_dashboard_pages_render(seeded_db_path, page, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("DATABASE_PATH", str(seeded_db_path))
    monkeypatch.setenv("SEATS_AERO_API_KEY", FAKE_KEY)
    import streamlit as st
    st.cache_resource.clear()
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, f"{page}: {[e.value for e in at.exception]}"
    body = " ".join(m.value for m in at.markdown) + " ".join(str(m.value) for m in at.title)
    assert FAKE_KEY not in body
    if page == "Best Deals":
        html = " ".join(m.value for m in at.markdown)
        assert "Round trips found" in html and "ah-card" in html and "cash for two" in html
        assert "Watchlist" in html and "Rome in April" in html and ("you have enough" in html or "short" in html)
    if page == "Home":
        html = " ".join(m.value for m in at.markdown)
        assert "How Amex Award Hunter works" in html and "Scoring weights" in html and "Score tiers" in html
    if page == "Best Deals":
        assert "Rome" in body


def test_search_button_runs_scan_through_dashboard(seeded_db_path, monkeypatch):
    """Press 'Search Awards' with the API mocked; the scan summary must render with coverage."""
    from streamlit.testing.v1 import AppTest
    import award_hunter.scheduler as sched
    from tests.conftest import build_sample_rows

    sample = build_sample_rows()

    def fake_make_client(settings, db, **kwargs):
        def handler(r):
            origins = r.url.params["origin_airport"].split(",")
            return httpx.Response(200, json=search_response(sample[:6] if "LAX" in origins else sample[6:]))
        return SeatsAeroClient(settings, budget=DailyBudget(50), transport=httpx.MockTransport(handler), sleep=lambda s: None)

    monkeypatch.setattr(sched, "make_client", fake_make_client)
    monkeypatch.setattr(sched, "sync_bonuses", lambda db, **kw: type("R", (), {"added": []})())
    monkeypatch.setenv("DATABASE_PATH", str(seeded_db_path))
    monkeypatch.setenv("SEATS_AERO_API_KEY", FAKE_KEY)
    import streamlit as st
    st.cache_resource.clear()
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    at.sidebar.radio[0].set_value("Search").run()
    run_button = next(b for b in at.button if "Search Awards" in b.label)
    run_button.click().run()
    assert not at.exception, [e.value for e in at.exception]
    labels = [m.label for m in at.metric]
    assert "API calls" in labels and "Qualifying awards" in labels and "Coverage" in labels
    api_calls = next(m for m in at.metric if m.label == "API calls")
    assert int(api_calls.value) >= 2


def test_home_button_navigates_to_best_deals(seeded_db_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("DATABASE_PATH", str(seeded_db_path))
    monkeypatch.setenv("SEATS_AERO_API_KEY", FAKE_KEY)
    import streamlit as st
    st.cache_resource.clear()
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    btn = next(b for b in at.button if "Best Deals" in b.label)
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.sidebar.radio[0].value == "Best Deals"


def test_search_reset_button_restores_defaults(seeded_db_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from award_hunter.database import Database
    from award_hunter.scheduler import load_profile

    monkeypatch.setenv("DATABASE_PATH", str(seeded_db_path))
    monkeypatch.setenv("SEATS_AERO_API_KEY", FAKE_KEY)
    import streamlit as st
    st.cache_resource.clear()
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value("Search").run()
    btn = next(b for b in at.button if "Reset" in b.label)
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    d = Database(seeded_db_path)
    p = load_profile(d)
    d.close()
    assert p.origins == SearchProfile().origins and p.max_amex_points_per_passenger == 90_000 and p.window_days == 90
