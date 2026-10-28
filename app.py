"""Amex Award Hunter - Streamlit dashboard.

Run:  streamlit run app.py
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from award_hunter.airports import EUROPEAN_AIRPORTS, US_ORIGINS, country_of, european_countries
from award_hunter.config import load_settings, validate_security
from award_hunter.database import Database
from award_hunter.logging_config import configure_logging, mask_secret
from award_hunter.alerts import AlertSender
from award_hunter.bonus_tracker import last_check, sync_bonuses
from award_hunter.scheduler import (
    THRESHOLDS_KEY, load_profile, load_scoring_config, load_thresholds, make_client, run_scan, save_profile, save_scoring_config,
)
from award_hunter.scoring import PRIORITY_PRESETS, fees_to_usd
from award_hunter.search import SearchProfile, build_plan, to_rows
from award_hunter.transfer_bonuses import TransferEngine
from award_hunter.transfer_partners import PROGRAMS, SWEET_SPOTS
from award_hunter.watchlist import Watch, match_watches, resolve_destinations
from award_hunter.ui import (  # noqa: E402
    CARD_CSS, PALETTE, _esc, _fmt_day, _leg_stops, affordability, curve_svg, deal_card, dedupe_trips, flow_svg,
    fmt_pts, rank_trips, rt_from_row, scored_from_row, short_program, tier_ladder_svg, tile, trip_is_new, trip_is_nonstop, trip_lie_flat,
    weights_bar_svg, when_to_go_svg,
)
from award_hunter.seasonality import TIMING_NOTES

st.set_page_config(page_title="Amex Award Hunter", page_icon="✈️", layout="wide")
configure_logging()


def _version_on_disk() -> str | None:
    """Version string in award_hunter/__init__.py right now (the imported module keeps the old one after an update)."""
    import re
    from pathlib import Path
    try:
        text = (Path(__file__).resolve().parent / "award_hunter" / "__init__.py").read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else None
    except OSError:
        return None


import award_hunter as _ah  # noqa: E402

if (_disk := _version_on_disk()) and getattr(_ah, "__version__", None) != _disk:
    st.warning(f"A new version ({_disk}) was downloaded while the dashboard was running. "
               "Close the dashboard window (the one that says *Starting Amex Award Hunter*) and double-click "
               "**Open Award Hunter.command** again to load it.", icon="🔄")
    st.stop()

VERIFY_WARNING = ("**Verify both award seats directly with the airline before transferring Amex points. "
                  "Transfers are generally irreversible.** All results are cached Seats.aero observations, not reservations.")


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #
@st.cache_resource
def get_settings():
    return load_settings()


@st.cache_resource
def get_db(path: str) -> Database:
    return Database(path)


settings = get_settings()
db = get_db(str(settings.database_path))
TZ = ZoneInfo(settings.timezone)


def pacific(value) -> str:
    if not value:
        return "unknown"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TZ).strftime("%b %d, %Y %I:%M %p %Z")


def inject_css() -> None:
    st.markdown(CARD_CSS, unsafe_allow_html=True)


def html(fragment: str, container=None) -> None:
    """Render raw HTML. Dollar signs are escaped so Streamlit never mistakes '$50 ... $600' for LaTeX."""
    (container or st).markdown(fragment.replace("$", "&#36;"), unsafe_allow_html=True)


BALANCE_KEY = "points_balance"


def load_balance() -> dict:
    raw = db.get_setting(BALANCE_KEY) or {}
    return {"balance": int(raw.get("balance") or 0), "monthly_earn": int(raw.get("monthly_earn") or 0)}


def api_tile() -> str:
    """Seats.aero connection + quota as a status tile; sits above the Scan button."""
    tone = {"🟢": "ok", "🟡": "warn", "🔴": "bad"}.get(icon, "")
    used = db.budget_load(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    remaining = db.last_remaining_quota()
    state = {"ok": "Connected", "warn": "Not tested yet", "bad": "Problem"}.get(tone, "Unknown")
    sub = f"<b>{used}</b> used today" + (f" · <b>{remaining:,}</b> calls left" if remaining is not None else "")
    if tone == "bad":
        sub = _esc(status_text)
    return tile("Seats.aero API", f"{icon} {state}", sub, tone)


def bonus_panel(key_prefix: str) -> None:
    """Amex transfer bonuses running now: detected from the tracker + anything added by hand."""
    today = date.today()
    check = last_check(db) or {}
    rows = db.transfer_bonuses()
    detected = check.get("detected") or []
    hdr, btn = st.columns([5, 1.2])
    hdr.markdown('<div class="ah-home-h" style="margin-top:14px">Amex transfer bonuses running now</div>', unsafe_allow_html=True)
    btn.write("")
    if btn.button("Check now", key=f"{key_prefix}_bonus_check", use_container_width=True):
        res = sync_bonuses(db)
        st.toast(res.summary())
        st.rerun()
    when = pacific(check["checked_at"]) if check.get("checked_at") else "never"
    status = "" if check.get("ok", True) else f" · last check failed: {_esc(check.get('error') or '')}"
    st.caption(f"Checked {when} against awardtravelfinder.com{status}. Bonuses are often targeted, so each one is applied to the points math "
               f"only after you confirm it is in your Amex account.")

    # Merge detected (tracker) with stored rows (which also include manual ones)
    items: list[dict] = []
    seen: set[tuple] = set()
    for d in detected:
        row = next((r for r in rows if r["source"] == d.get("source") and float(r["bonus_percent"]) == float(d["pct"])
                    and (r.get("expiration_date") or None) == d.get("end")), None)
        items.append({"source": d.get("source"), "label": d["partner"], "pct": float(d["pct"]), "end": d.get("end"), "row": row})
        seen.add((d.get("source"), float(d["pct"]), d.get("end")))
    for r in rows:
        k = (r["source"], float(r["bonus_percent"]), r.get("expiration_date") or None)
        if k in seen or not r.get("active"):
            continue
        exp = r.get("expiration_date")
        if exp and date.fromisoformat(exp) < today:
            continue
        name = PROGRAMS[r["source"]].program_name if r["source"] in PROGRAMS else r["source"]
        items.append({"source": r["source"], "label": name, "pct": float(r["bonus_percent"]), "end": exp, "row": r})
    if not items:
        st.info("No Amex transfer bonuses detected right now. The daily scan re-checks automatically.")
        return
    cols = st.columns(min(len(items), 3))
    for i, it in enumerate(items):
        with cols[i % 3]:
            row = it["row"]
            in_app = it["source"] in PROGRAMS
            ends = f"ends {date.fromisoformat(it['end']).strftime('%b %-d')}" if it["end"] else "end date not listed"
            if not in_app:
                html(tile(_esc(it["label"]), f"+{it['pct']:g}%", f"{ends} · not an airline this tool searches"))
                continue
            name = PROGRAMS[it["source"]].program_name
            if row and row.get("verified"):
                html(tile(name, f"+{it['pct']:g}%", f"{ends} · <b>applied to the points math</b>", "ok"))
            else:
                html(tile(name, f"+{it['pct']:g}%", f"{ends} · not yet confirmed", "warn"))
                if row and st.button("It's in my account → apply", key=f"{key_prefix}_confirm_{row['id']}", use_container_width=True,
                                     help="Only press this after you see the bonus on the transfer page of your own Amex account."):
                    db.confirm_bonus(int(row["id"]), today.isoformat())
                    st.toast(f"+{it['pct']:g}% {name} bonus applied. Press Scan now to re-score deals with it.")
                    st.rerun()


def watchlist_section() -> None:
    """Watches: 'alert me the moment N seats open for this trip'. Lives on Best Deals."""
    rows = db.watches()
    hdr, add_col = st.columns([5, 1.2])
    hdr.markdown('<div class="ah-home-h" style="margin-top:4px">Watchlist</div>', unsafe_allow_html=True)
    st.caption("A watch alerts you the moment two business seats appear for a specific trip, whatever the score. "
               "Watched dates and airports are added to every scan automatically.")
    if rows:
        watches = [Watch.from_row(r) for r in rows]
        stored = db.current_awards(qualifying_only=False)
        scored = [scored_from_row(r) for r in stored]
        hits = match_watches([w for w in watches if w.active], scored)
        cols = st.columns(min(len(watches), 3))
        for i, w in enumerate(watches):
            with cols[i % 3]:
                n = len(hits.get(w.id, []))
                if not w.active:
                    html(tile(w.name, "paused", _esc(w.describe())))
                elif n:
                    html(tile(w.name, f"{n} match{'es' if n != 1 else ''}", _esc(w.describe()), "ok"))
                else:
                    html(tile(w.name, "nothing yet", _esc(w.describe()), "warn"))
                b1, b2 = st.columns(2)
                if b1.button("Resume" if not w.active else "Pause", key=f"w_pause_{w.id}", use_container_width=True):
                    db.set_watch_active(w.id, not w.active); st.rerun()
                if b2.button("Delete", key=f"w_del_{w.id}", use_container_width=True):
                    db.delete_watch(w.id); st.rerun()
                if n:
                    with st.expander(f"See {n} match{'es' if n != 1 else ''}"):
                        st.dataframe(pd.DataFrame([{
                            "Date": s.award.travel_date.isoformat(), "Route": f"{s.award.origin} → {s.award.destination}",
                            "Seats": s.award.remaining_seats, "Program": short_program(s.award.source, s.amex.program_name),
                            "Nonstop": _leg_stops(s.award) == "nonstop",
                            "Cash / pp": round(fees_to_usd(s.award.taxes_minor_units, s.award.taxes_currency, load_scoring_config(db, 2)) or 0) if s.award.fees_known else None,
                            "Amex pts / pp": s.amex.amex_points_per_passenger, "Score": s.score.total,
                        } for s in hits[w.id][:40]]), hide_index=True, use_container_width=True)
    else:
        st.info("No watches yet. Add one below, for example \"Rome, any departure May 1–20, nonstop\".")
    with st.expander("Add a watch", expanded=not rows):
        with st.form("watch_form", clear_on_submit=True):
            name = st.text_input("Name", placeholder="Rome in May")
            c1, c2 = st.columns(2)
            countries = c1.multiselect("Countries", european_countries())
            airports = c2.multiselect("Or specific airports", list(EUROPEAN_AIRPORTS), format_func=lambda c: f"{c} – {EUROPEAN_AIRPORTS[c].city}")
            d1, d2 = st.columns(2)
            date_from = d1.date_input("Earliest departure", value=date.today() + timedelta(days=30), min_value=date.today())
            date_to = d2.date_input("Latest departure", value=date.today() + timedelta(days=60), min_value=date.today())
            e1, e2, e3 = st.columns(3)
            nonstop = e1.checkbox("Nonstop only", False)
            seats = e2.number_input("Seats needed", 1, 4, 2)
            returns = e3.checkbox("Also watch the return", True)
            f1, f2, f3 = st.columns(3)
            max_pts = f1.number_input("Max Amex points / person (0 = any)", 0, 300000, 0, step=5000)
            max_cash = f2.number_input("Max cash / person one way (0 = any)", 0, 3000, 0, step=50)
            trip = f3.slider("Trip length for the return (days)", 3, 30, (7, 14))
            if st.form_submit_button("Add watch", type="primary"):
                dests = resolve_destinations(airports, countries)
                if not dests:
                    st.error("Pick at least one country or airport.")
                elif date_to < date_from:
                    st.error("Latest departure must be on or after the earliest departure.")
                else:
                    db.add_watch(name or f"Watch {len(rows) + 1}", dests, date_from.isoformat(), date_to.isoformat(), passengers=int(seats),
                                 nonstop_only=nonstop, max_points_pp=int(max_pts) or None, max_cash_pp_usd=float(max_cash) or None,
                                 include_returns=returns, min_trip_days=trip[0], max_trip_days=trip[1])
                    st.toast("Watch added. Press Scan now to check it right away; the daily scan covers it from now on.")
                    st.rerun()


def api_status() -> tuple[str, str]:
    if not settings.has_api_key:
        return "🔴", "No API key configured (.env)"
    last = db.one("SELECT at_utc, status, error FROM api_calls WHERE from_cache = 0 ORDER BY id DESC LIMIT 1")
    if not last:
        return "🟡", "Key loaded; no request made yet"
    if last["status"] and last["status"] < 400:
        return "🟢", f"Last call OK at {pacific(last['at_utc'])}"
    return "🔴", f"Last call failed ({last['status'] or last['error']}) at {pacific(last['at_utc'])}"


def budget_line() -> str:
    used = db.budget_load(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    remaining_hdr = db.last_remaining_quota()
    txt = f"App budget today: {used}/{settings.api_daily_budget} calls used"
    if remaining_hdr is not None:
        txt += f" · Seats.aero reports {remaining_hdr} calls left (resets midnight UTC)"
    return txt


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
inject_css()
st.sidebar.title("✈️ Amex Award Hunter")
NAV = ["Home", "Best Deals", "Search", "Settings"]
page = st.sidebar.radio("Navigate", NAV, key="nav")
icon, status_text = api_status()
st.sidebar.markdown(f"**API** {icon} {status_text}")
st.sidebar.caption(budget_line())
last_run = db.last_run()
if last_run:
    st.sidebar.caption(f"Last scan: {pacific(last_run.get('finished_at') or last_run['started_at'])} ({last_run['status']})")
for w in validate_security(settings):
    st.sidebar.warning(w)

with st.sidebar.expander("My Amex points", expanded=False):
    _bal = load_balance()
    _b = st.number_input("Membership Rewards balance", 0, 10_000_000, _bal["balance"], step=5000, key="bal_balance",
                         help="Used to mark deals you can already afford. Stored only on this computer.")
    _e = st.number_input("Points earned per month (optional)", 0, 500_000, _bal["monthly_earn"], step=1000, key="bal_earn")
    if (_b, _e) != (_bal["balance"], _bal["monthly_earn"]):
        db.set_setting(BALANCE_KEY, {"balance": int(_b), "monthly_earn": int(_e)})
        st.rerun()

profile = load_profile(db)


# --------------------------------------------------------------------------- #
# Home (landing): what this is and how scoring works
# --------------------------------------------------------------------------- #
def page_home() -> None:
    cfg = load_scoring_config(db, profile.passengers)
    th = load_thresholds(db)
    last = db.last_run()
    trips = db.round_trips(min_score=None, limit=1000)
    complete = [r for r in trips if r["fees_complete"] and r["total_fees_two"] is not None]
    hero = min(complete, key=lambda r: (r["total_fees_two"], r["total_points_two"] or 10**9)) if complete else None
    preset = PRIORITY_PRESETS.get(cfg.priority, PRIORITY_PRESETS["minimize_cash"])

    st.title("✈️ Amex Award Hunter")
    html('<div class="ah-home-p">Every morning this quietly checks Seats.aero for <b>business-class award seats for two</b> from '
                'Southern California to Europe, converts the airline miles into the <b>Amex Membership Rewards points</b> you would transfer, '
                'pairs flights into complete round trips, ranks them with <b>the least cash out of pocket first</b>, and alerts you when '
                'something is worth jumping on. It never books anything and never moves your points.</div>')
    st.write("")
    c1, c2, c3 = st.columns(3)
    html(tile("Best deal right now", f"${hero['total_fees_two']:,.0f} cash" if hero else "—",
                     f"+ <b>{hero['total_points_two']:,}</b> pts · {_esc(hero['destination_city'])}" if hero else "run a scan to find out"), c1)
    html(api_tile(), c2)
    if last and last.get("finished_at"):
        fin = pacific(last["finished_at"])
        html(tile("Last scan", fin.split(",")[0], fin.split(" ", 3)[-1] + f" · <b>{len(trips)}</b> round trips on file"), c3)
    else:
        html(tile("Last scan", "never", "daily at 7:30 AM once scheduled"), c3)
    st.write("")
    st.button("See today's Best Deals →", type="primary", on_click=lambda: st.session_state.update(nav="Best Deals"))

    bonus_panel("home")

    html('<div class="ah-home-h">When to go</div>')
    html('<div class="ah-home-p">Historical pattern for West Coast → Europe business awards: green weeks have the most space and the '
         'lowest dynamic prices, red weeks are peak demand where saver seats are rare. Holiday weeks move each year and are computed. '
         'Past days are faded. A dot marks a departure date where the latest scan actually found two business seats.</div>')
    found = {date.fromisoformat(a["travel_date"]) for a in db.current_awards(qualifying_only=True, direction="outbound")}
    html(when_to_go_svg(date.today().replace(day=1), 12, found))
    tn = "".join(f"<div><b>{when}</b><br><small>{what}</small></div>" for when, what in TIMING_NOTES)
    html(f'<div class="ah-legend" style="display:grid;grid-template-columns:repeat(3,1fr);margin-top:14px">{tn}</div>')

    html('<div class="ah-home-h">How it works</div>')
    html(flow_svg())

    html('<div class="ah-home-h">How a deal is scored</div>')
    html(f'<div class="ah-home-p">Every award gets a score from 0 to 100. Six factors each earn a share of the score; the bar shows how '
                f'big each share is under your current priority, <b>{_esc(preset["label"])}</b>. Change it in Settings → Scoring.</div>')
    html(weights_bar_svg(cfg))

    html('<div class="ah-home-h">Why the cheapest cash wins</div>')
    html('<div class="ah-home-p">The two biggest factors are curves, not cliffs. Cash fees lose points quickly: by $400 per person a leg has '
                'lost three quarters of its fee score. Points are forgiving up to about 70,000 per person, which is why a 75,000-point Aeroplan '
                'award with $58 in fees beats a 60,000-point award with $500 in fees.</div>')
    g1, g2 = st.columns(2)
    html(curve_svg(cfg.fee_anchors_usd, x_label="taxes & fees per person, one way (USD)", x_fmt=lambda v: f"${v:,.0f}", color=PALETTE[0],
                          title="Cash fees → score factor"), g1)
    html(curve_svg(cfg.points_anchors, x_label="Amex points per person, one way", x_fmt=lambda v: f"{v // 1000:.0f}k", color=PALETTE[1],
                          title="Amex points → score factor"), g2)

    html('<div class="ah-home-h">What the score means</div>')
    l1, l2 = st.columns([1.2, 1])
    html(tier_ladder_svg(), l1)
    cash_cap = f"${th.round_trip_max_fees_two_usd:,.0f}" if th.round_trip_max_fees_two_usd else "no cap"
    html(f"""<div class="ah-home-p"><b>When you get alerted</b><br>
• A complete round trip for two with <b>{cash_cap}</b> or less in total fees, <b>{th.round_trip_max_points_two:,}</b> points or fewer, scoring <b>{th.round_trip_min_score:.0f}+</b>.<br>
• A single award scoring <b>{th.new_award_min_score:.0f}+</b>; <b>{th.exceptional_min_score:.0f}+</b> is flagged exceptional.<br>
• At most {th.max_alerts_per_run} alerts per scan; the rest are folded into one message. The same deal never alerts twice.<br><br>
<b>&#9733; Sweet spot badge</b><br>
An award priced at that program's realistic floor for West Coast → Europe business: {"; ".join(f"{PROGRAMS[k].program_name.split(' ')[0] if k != 'flyingblue' else 'Flying Blue'} ≤ {v['miles'] // 1000}k" for k, v in SWEET_SPOTS.items())}. "Best price" means at or under the lowest level ever seen.<br><br>
<b>Watchlist</b><br>
On Best Deals you can watch a specific trip ("Rome, any departure May 1–20"). The moment two seats appear you get an urgent alert, score or not.<br><br>
<b>What is never counted as good news</b><br>
• Programs that don't report seat counts are not treated as "two seats".<br>
• Missing or $0 fees are "not provided", never free.<br>
• Observations older than {int(profile.max_observation_age_hours // 24)} days don't qualify.<br>
• Every result is a cached observation: <b>verify with the airline before transferring points</b>.</div>""", l2)


# --------------------------------------------------------------------------- #
# Best Deals
# --------------------------------------------------------------------------- #
def page_best_deals() -> None:
    cfg = load_scoring_config(db, profile.passengers)
    last = db.last_run()
    last_started = last["started_at"] if last else None
    trips = db.round_trips(min_score=None, limit=1000)
    awards = db.current_awards(qualifying_only=True, min_score=profile.min_score)

    st.title("Best Deals")
    preset = PRIORITY_PRESETS.get(cfg.priority, PRIORITY_PRESETS["minimize_cash"])
    st.caption(f"Business class for two, Southern California ⇄ Europe · ranking: **{preset['label']}** · see **Home** for how scoring works")

    if not trips and not awards:
        st.warning("Nothing stored yet. Press **Scan now**, or wait for the first scheduled scan.")

    complete = [r for r in trips if r["fees_complete"] and r["total_fees_two"] is not None]
    hero = min(complete, key=lambda r: (r["total_fees_two"], r["total_points_two"] or 10**9)) if complete else None
    h1, h2 = st.columns([3.2, 1.1])
    with h1:
        if hero:
            o_date = date.fromisoformat(hero["out_date"]); i_date = date.fromisoformat(hero["in_date"])
            _state, _txt = affordability(hero["total_points_two"], load_balance())
            hero_you = (f'<div><div class="k">Your points</div><div class="v ah-{_state}">{"Enough &#10003;" if _state == "ok" else _txt.capitalize()}</div></div>'
                        if _state != "none" else "")
            html(f"""<div class="ah-herotile"><div class="ah-hlabel">Lowest cash out of pocket · complete round trip · two people</div>
<div class="ah-hvalue">${hero['total_fees_two']:,.0f}</div>
<div class="ah-hunit">in taxes and fees, total for both of you</div>
<div class="ah-hrow">
<div><div class="k">Plus</div><div class="v">{hero['total_points_two']:,} Amex points</div></div>
<div><div class="k">Where</div><div class="v">{_esc(hero['destination_city'])}, {_esc(hero['destination_country'])}</div></div>
<div><div class="k">When</div><div class="v">{_fmt_day(o_date)} → {_fmt_day(i_date)} · {hero['trip_days']} days</div></div>
<div><div class="k">Score</div><div class="v">{hero['score']:.0f}/100</div></div>
{hero_you}
</div></div>""")
        else:
            html('<div class="ah-herotile"><div class="ah-hlabel">Lowest cash out of pocket</div><div class="ah-hvalue">—</div>'
                        '<div class="ah-hunit">No complete round trip with known fees yet.</div></div>')
    with h2:
        html(api_tile())
        st.write("")
        if st.button("🔄 Scan now", use_container_width=True, type="primary", disabled=not settings.has_api_key, help="About a minute; ~10 API calls."):
            bar = st.progress(0.0, text="Starting…")
            with st.spinner("Searching Seats.aero…"):
                outcome = run_scan(settings, db, profile=profile, trigger="dashboard", send_alerts=True,
                                   progress=lambda i, n, label: bar.progress(i / n, text=f"[{i}/{n}] {label}"))
            bar.empty()
            st.toast(f"Scan done: {len(outcome.result.qualifying)} awards, {len(outcome.round_trips)} trips, {outcome.new_round_trips} new")
            st.rerun()

    new_trips = sum(1 for r in trips if trip_is_new(r, last_started))
    fewest = min(trips, key=lambda r: r["total_points_two"] or 10**9) if trips else None
    html('<div class="ah-stats"></div>')
    s1, s2, s3, s4 = st.columns(4)
    html(tile("Round trips found", f"{len(trips):,}", f"<b>{new_trips}</b> new since last scan" if new_trips else "none new since last scan"), s1)
    html(tile("Qualifying one-way awards", f"{len(awards):,}", "2 seats · fresh · within your caps"), s2)
    html(tile("Fewest points, round trip", f"{fewest['total_points_two'] // 1000}k" if fewest and fewest["total_points_two"] else "—",
                     f"for two · <b>{_esc(fewest['destination_city'])}</b>" if fewest else ""), s3)
    if last and last.get("finished_at"):
        fin = pacific(last["finished_at"])            # e.g. "Oct 28, 2026 11:30 AM PDT"
        scan_val, scan_sub = fin.split(",")[0], fin.split(" ", 3)[-1] + f" · <b>{_esc(last['status'])}</b> · {last['calls_made']} API calls"
    else:
        scan_val, scan_sub = "never", "press Scan now"
    html(tile("Last scan", scan_val, scan_sub), s4)
    st.write("")
    with st.expander("Amex transfer bonuses running now", expanded=False):
        bonus_panel("deals")
    watchlist_section()

    if not trips and not awards:
        return
    if not trips:
        st.info("One-way awards were found but no outbound/return pair fits your trip length. Widen the trip length or window in **Search**.")
    else:
        st.subheader("Top complete trips")
        bal = load_balance()
        c1, c2, c3, c4, c5 = st.columns([2.1, 1, 1, 1, 1])
        mode = c1.radio("Rank by", ["Least cash", "Fewest points", "All-in cost", "Best overall"], horizontal=True)
        per_dest = c3.checkbox("One per city", True, help="Off = one card per destination and departure date.")
        nonstop_only = c4.checkbox("Nonstop only", False, help="Both the outbound and the return must be nonstop.")
        lie_flat_only = c4.checkbox("Lie-flat likely", False, help="Hide trips whose long-haul leg is a known recliner-style narrowbody.")
        affordable_only = c5.checkbox("Affordable only", False, help="Trips within the balance you entered under 'My Amex points'.") if bal["balance"] else False
        fees_known = [r["total_fees_two"] for r in trips if r["total_fees_two"] is not None]
        cash_default = min(5000, int(max(fees_known) // 100 * 100 + 100)) if fees_known else 5000
        max_cash = c2.number_input("Max cash for two ($)", 0, 5000, cash_default, step=100)
        only_new = c3.checkbox("New only", False)
        if st.session_state.get("rank_mode") != mode:
            st.session_state["rank_mode"] = mode
            st.session_state["cards_limit"] = 9
        ranked = rank_trips(trips, mode, cfg)
        ranked = [r for r in ranked if (r["total_fees_two"] is None or r["total_fees_two"] <= max_cash) and (not only_new or trip_is_new(r, last_started))]
        if nonstop_only:
            ranked = [r for r in ranked if trip_is_nonstop(r)]
        if affordable_only:
            ranked = [r for r in ranked if r["total_points_two"] is not None and r["total_points_two"] <= bal["balance"]]
        if lie_flat_only:
            ranked = [r for r in ranked if trip_lie_flat(r)]
        limit = st.session_state.get("cards_limit", 9)
        all_cards = dedupe_trips(ranked, per_destination=per_dest)
        cards = all_cards[:limit]
        sort_hint = {"Least cash": "sorted by cash for two, then points", "Fewest points": "sorted by Amex points for two, then cash",
                     "All-in cost": f"sorted by cash + points at {cfg.cents_per_point:g}¢ each", "Best overall": "sorted by deal score"}[mode]
        st.caption(f"#1 is best · {sort_hint}" + (" · one card per destination" if per_dest else " · one card per destination and departure date")
                   + (" · nonstop both ways only" if nonstop_only else ""))
        if not cards:
            st.info("No trips match these filters." + (" No round trip is nonstop in both directions right now; untick *Nonstop only* "
                                                        "or set *Max connections* to 0 on the Search page to search for them specifically." if nonstop_only else ""))
        cols = st.columns(3)
        for idx, (row, alts) in enumerate(cards):
            with cols[idx % 3]:
                html(deal_card(rt_from_row(row), row, is_new=trip_is_new(row, last_started), alternatives=alts, cfg=cfg, rank=idx + 1,
                               emphasis="points" if mode == "Fewest points" else "cash",
                               alt_label="dates" if per_dest else "returns", bal=bal))
        b1, b2 = st.columns([1, 4])
        if len(all_cards) > len(cards) and b1.button(f"Show more ({len(all_cards) - len(cards)} left)"):
            st.session_state["cards_limit"] = limit + 9
            st.rerun()
        b2.caption(f"Showing {len(cards)} of {len(all_cards)} cards ({len(ranked)} trips). "
                   f"\"+N other …\" on a card means more combinations exist for that destination; turn off \"One per destination\" to see them.")

        # Cash vs points: one series, lower-left is better.
        pts_rows = [r for r in trips if r["fees_complete"] and r["total_fees_two"] is not None and r["total_points_two"]]
        if len(pts_rows) >= 3:
            st.subheader("Cash vs points, every complete trip")
            top_keys = {(r["fingerprint"]) for r, _ in cards[:3]}
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=[r["total_fees_two"] for r in pts_rows], y=[r["total_points_two"] / 1000 for r in pts_rows], mode="markers+text",
                text=[r["destination_city"] if r["fingerprint"] in top_keys else "" for r in pts_rows], textposition="top center",
                textfont=dict(size=12, color="#52514e"),
                marker=dict(size=10, color="#2a78d6", opacity=0.75, line=dict(width=2, color="#fcfcfb")),
                customdata=[[r["destination_city"], r["out_date"], r["in_date"], r["trip_days"], r["score"]] for r in pts_rows],
                hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]} → %{customdata[2]} (%{customdata[3]} d)<br>"
                              "$%{x:,.0f} cash · %{y:.0f}k points · score %{customdata[4]:.0f}<extra></extra>",
                name="Round trips"))
            fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10), showlegend=False, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                              xaxis=dict(title="Cash for two (USD)", tickprefix="$", gridcolor="#e9e8e3", zeroline=False),
                              yaxis=dict(title="Amex points for two (thousands)", ticksuffix="k", gridcolor="#e9e8e3", zeroline=False),
                              hoverlabel=dict(bgcolor="#ffffff", font_color="#0b0b0b"))
            st.plotly_chart(fig, use_container_width=True)

    if awards:
      with st.expander("Cheapest one-way awards by route (when no round trip fits)"):
          best_by_route: dict[str, dict] = {}
          for a in awards:
              if not a["fees_known"] or a["taxes_minor"] is None:
                  continue
              key = f"{a['origin']}-{a['destination']}"
              fee = a["taxes_minor"] / 100
              cur = best_by_route.get(key)
              if cur is None or (fee, a["amex_points_pp"] or 10**9) < (cur["taxes_minor"] / 100, cur["amex_points_pp"] or 10**9):
                  best_by_route[key] = a
          rows = sorted(best_by_route.values(), key=lambda a: (a["taxes_minor"], a["amex_points_pp"] or 10**9))[:12]
          st.dataframe(pd.DataFrame([{
              "Route": f"{a['origin']} → {a['destination']}", "Date": a["travel_date"],
              "Program": PROGRAMS[a["source"]].program_name if a["source"] in PROGRAMS else a["source"],
              "Cash / person": a["taxes_minor"] / 100, "Amex pts / person": a["amex_points_pp"], "Seats": a["seats"],
              "Nonstop": "yes" if a["direct"] else "no", "Score": a["score"],
          } for a in rows]), hide_index=True, use_container_width=True,
              column_config={"Cash / person": st.column_config.NumberColumn(format="$%.0f"), "Amex pts / person": st.column_config.NumberColumn(format="%d"),
                             "Score": st.column_config.NumberColumn(format="%.0f")})

    changes = [c for c in db.recent_changes(200) if c["change_type"] in ("new", "price_drop", "seats_increased", "disappeared")][:12]
    if changes:
        with st.expander("Recent changes", expanded=False):
            st.dataframe(pd.DataFrame([{"When": pacific(c["at_utc"]), "Type": c["change_type"], "Detail": c["summary"], "Score": c["score"]} for c in changes]),
                         hide_index=True, use_container_width=True)
    st.caption(VERIFY_WARNING)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def page_search() -> None:
    global profile
    t1, t2 = st.columns([4, 1.3])
    t1.title("Search")
    t1.caption("Set what you're looking for, then press **Search Awards**. One search is about 10–20 of your 1,000 daily Seats.aero calls.")
    t2.write("")
    if t2.button("↺ Reset to original settings", use_container_width=True,
                 help="Puts every search setting back to how it was when installed: SAN/LAX/SFO/SEA to Europe minus France, "
                      "90 days from two weeks out, 7–14 day trips, 2 seats, up to 90k points and $700 fees per person, max 2 connections."):
        save_profile(db, SearchProfile())
        profile = SearchProfile()
        st.session_state["search_reset_done"] = True
        st.rerun()
    if st.session_state.pop("search_reset_done", False):
        st.success("Search settings reset to the originals. Press **Search Awards** to scan with them.")
    with st.form("search_form"):
        col1, col2 = st.columns(2)
        with col1:
            origins = st.multiselect("Fly from", list(US_ORIGINS), default=[o for o in profile.origins if o in US_ORIGINS],
                                     format_func=lambda c: f"{c} – {US_ORIGINS[c].city}")
            home_opts = list(US_ORIGINS)
            home = st.selectbox("Home airport (for positioning-cost estimates)", home_opts,
                                index=home_opts.index(profile.home_airport) if profile.home_airport in home_opts else 0,
                                format_func=lambda c: f"{c} – {US_ORIGINS[c].city}")
            countries = st.multiselect("Countries to visit", european_countries(),
                                       default=sorted({country_of(d) for d in profile.destinations if country_of(d)}))
            excluded_countries = st.multiselect("Never these countries", european_countries(), default=profile.excluded_destination_countries)
            start = st.date_input("Earliest departure", value=profile.start_date, min_value=date.today())
            window = st.slider("How many days to search from that date", 30, 180, profile.window_days, step=5)
            trip_min, trip_max = st.slider("Trip length (days)", 3, 30, (profile.min_trip_days, profile.max_trip_days))
        with col2:
            max_fees = st.number_input("Max cash fees per person, round trip ($)", 0, 3000, int(profile.max_fees_per_passenger_usd or 700), step=50)
            max_pts = st.number_input("Max Amex points per person, one way", 20000, 200000, profile.max_amex_points_per_passenger, step=5000)
            max_stops = st.selectbox("Max connections", [0, 1, 2, 3], index=min(profile.max_stops, 3))
            passengers = st.number_input("Travelers", 1, 4, profile.passengers)
            open_jaw = st.checkbox("Allow flying into one city and home from another", profile.include_open_jaw)
        with st.expander("Advanced"):
            dest_options = [c for c in EUROPEAN_AIRPORTS if EUROPEAN_AIRPORTS[c].country in set(countries)] or list(EUROPEAN_AIRPORTS)
            destinations = st.multiselect("Destination airports", dest_options, default=[d for d in profile.destinations if d in dest_options],
                                          format_func=lambda c: f"{c} – {EUROPEAN_AIRPORTS[c].city}")
            excluded_conns = st.multiselect("Excluded connection airports", list(EUROPEAN_AIRPORTS) + ["YYZ", "YUL", "YVR", "DXB", "DOH", "AUH", "IST", "KEF"],
                                            default=profile.excluded_connection_airports)
            sources = st.multiselect("Loyalty programs", list(PROGRAMS), default=[s for s in profile.sources if s in PROGRAMS],
                                     format_func=lambda s: f"{PROGRAMS[s].program_name}{'' if PROGRAMS[s].is_amex_partner else ' (not Amex)'}{'' if PROGRAMS[s].seats_aero_source_active else ' (not on Seats.aero)'}")
            excluded_airlines = st.text_input("Excluded airline codes (comma separated)", ",".join(profile.excluded_airlines))
            e1, e2, e3 = st.columns(3)
            min_seats = e1.number_input("Minimum reported seats", 1, 4, profile.min_seats)
            min_score = e2.slider("Minimum deal score to show", 0, 100, int(profile.min_score))
            max_age_days = e3.number_input("Ignore observations older than (days)", 1, 60, int(profile.max_observation_age_hours // 24))
            include_unknown = st.checkbox("Include awards whose program does not report seat counts (flagged)", profile.include_unknown_seat_counts)
            hide_mixed = st.checkbox("Hide mixed-cabin itineraries", profile.hide_mixed_cabin)
            return_search = st.checkbox("Also search return flights (needed for round trips)", profile.include_return_search)
            e4, e5, e6 = st.columns(3)
            per_query = e4.slider("Airports per API query", 1, 20, profile.max_airports_per_query)
            days_per_query = e5.slider("Days per API query", 7, 120, profile.max_days_per_query)
            include_trips = e6.checkbox("Request flight-level details", profile.include_trips)
        c_save, c_run = st.columns([1, 1])
        save = c_save.form_submit_button("Save preferences")
        run = c_run.form_submit_button("🔎 Search Awards", type="primary")

    if save or run:
        new_profile = SearchProfile(
            origins=origins, destinations=destinations, excluded_destination_countries=excluded_countries,
            excluded_connection_airports=excluded_conns, excluded_airlines=[a.strip().upper() for a in excluded_airlines.split(",") if a.strip()],
            sources=[s for s in sources if PROGRAMS[s].seats_aero_source_active], start_date=start, window_days=window, passengers=passengers,
            min_seats=min_seats, include_unknown_seat_counts=include_unknown, max_amex_points_per_passenger=max_pts,
            max_fees_per_passenger_usd=float(max_fees) if max_fees else None, max_stops=max_stops, hide_mixed_cabin=hide_mixed, min_score=min_score,
            max_observation_age_hours=float(max_age_days) * 24, home_airport=home,
            min_trip_days=trip_min, max_trip_days=trip_max, include_open_jaw=open_jaw, include_return_search=return_search,
            max_airports_per_query=per_query, max_days_per_query=days_per_query, include_trips=include_trips,
        )
        save_profile(db, new_profile)
        profile = new_profile
        st.success("Preferences saved.")

    plan = build_plan(profile)
    st.caption(f"Plan: {len(plan.chunks)} API query chunks (~{plan.estimated_calls()} calls with pagination). {budget_line()}")

    if run:
        if not settings.has_api_key:
            st.error("No API key configured. Add SEATS_AERO_API_KEY to your .env file and restart the dashboard.")
            return
        if not plan.chunks:
            st.error("Nothing to search: every destination is excluded or no airports selected.")
            return
        bar = st.progress(0.0, text="Starting search…")

        def prog(i, n, label):
            bar.progress(i / n, text=f"[{i}/{n}] {label}")

        with st.spinner("Searching Seats.aero…"):
            outcome = run_scan(settings, db, profile=profile, trigger="dashboard", send_alerts=True, progress=prog)
        bar.progress(1.0, text="Done")
        r = outcome.result
        cov = r.coverage_summary()
        st.subheader("Scan summary")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("API calls", r.calls_made)
        m2.metric("Records seen", r.records_seen)
        m3.metric("Qualifying awards", len(r.qualifying), f"{outcome.new_awards} new")
        m4.metric("Round trips", len(outcome.round_trips), f"{outcome.new_round_trips} new")
        m5.metric("Coverage", f"{cov.get('ok', 0) + cov.get('empty', 0)}/{len(r.coverage)} chunks", f"{cov.get('error', 0)} errors, {cov.get('skipped', 0) + cov.get('budget', 0)} skipped")
        if r.errors:
            with st.expander(f"{len(r.errors)} error(s) – some dates/airports were NOT searched", expanded=True):
                for e in r.errors:
                    st.write("•", e)
        if outcome.alerts:
            st.info(f"{len(outcome.alerts)} alert(s) generated: {outcome.alert_stats}")
        if r.qualifying:
            st.success("Done. Open **Best Deals** in the sidebar to see the ranked trips.")
        else:
            st.warning("Search completed but nothing met your filters. Check Coverage above: an empty chunk means 'searched, nothing matched', "
                       "not 'no seats'. Try widening the window, raising max points, or including unreported seat counts.")
        with st.expander("Hidden results (did not qualify)"):
            hidden = to_rows(outcome.result.non_qualifying)
            if hidden:
                st.dataframe(pd.DataFrame(hidden)[["date", "route", "program", "amex_pp", "seats_reported", "seat_status", "fees_pp_usd", "stops", "score", "hidden_reasons"]],
                             hide_index=True, use_container_width=True)
            else:
                st.write("None.")


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def page_settings() -> None:
    st.title("Settings")
    tabs = st.tabs(["Alerts", "Scoring", "Status", "Advanced: transfer partners", "Advanced: transfer bonuses", "Scan history"])

    with tabs[2]:
        st.write(f"API key: `{mask_secret(settings.api_key) if settings.has_api_key else 'not set'}` (from .env, never stored in the database)")
        st.write(f"Base URL: `{settings.base_url}` · auth scheme: `{settings.auth_scheme}` · database: `{settings.database_path}`")
        st.write(budget_line())
        if st.button("Run connection test"):
            if not settings.has_api_key:
                st.error("No API key configured.")
            else:
                with st.spinner("Contacting Seats.aero…"):
                    client = make_client(settings, db)
                    res = client.test_connection()
                    client.close()
                if res.ok:
                    st.success(f"{res.message} · {res.latency_ms} ms · auth scheme `{res.auth_scheme_used}` · quota left: {res.remaining_quota}")
                    if res.sample_fields:
                        st.caption("Fields present: " + ", ".join(res.sample_fields))
                else:
                    st.error(f"HTTP {res.status_code}: {res.message}")
        calls = db.query("SELECT at_utc, path, status, latency_ms, from_cache, remaining_quota, error FROM api_calls ORDER BY id DESC LIMIT 25")
        if calls:
            st.caption("Recent API calls")
            st.dataframe(pd.DataFrame([dict(c) for c in calls]), hide_index=True, use_container_width=True)

    with tabs[3]:
        st.caption("Edit ratios the moment Amex changes them. amex_points_in → miles_out (e.g. 1000 → 1000 is 1:1; JetBlue 250 → 200). "
                   "Set is_amex_partner to 0 for programs you cannot transfer to.")
        pdf = pd.DataFrame(db.transfer_partners())
        edited = st.data_editor(pdf, hide_index=True, use_container_width=True, disabled=["source"], key="partners_editor")
        if st.button("Save partner table"):
            for _, r in edited.iterrows():
                db.update_transfer_partner(r["source"], **{k: (None if pd.isna(r[k]) else (int(r[k]) if k in ("is_amex_partner", "amex_points_in", "miles_out", "transfer_increment", "seats_aero_source_active", "enabled") else r[k]))
                                                             for k in ["program_name", "airline", "alliance", "is_amex_partner", "amex_points_in", "miles_out", "transfer_increment", "booking_url", "notes", "last_verified", "seats_aero_source_active", "enabled"]})
            st.success("Saved. New searches will use the updated ratios.")

    with tabs[4]:
        st.caption("Only ACTIVE + VERIFIED bonuses within their date window are applied. Unverified bonuses are shown as an alternative calculation only. "
                   "Expired bonuses are never applied.")
        bdf = pd.DataFrame(db.transfer_bonuses()) if db.transfer_bonuses() else pd.DataFrame(columns=["id", "source", "bonus_percent", "effective_date", "expiration_date", "verified", "account_eligible", "active"])
        st.dataframe(bdf, hide_index=True, use_container_width=True)
        with st.form("bonus_form"):
            b1, b2, b3 = st.columns(3)
            src = b1.selectbox("Program", [s for s, p in PROGRAMS.items() if p.is_amex_partner], format_func=lambda s: PROGRAMS[s].program_name)
            pct = b2.number_input("Bonus %", 0.0, 100.0, 25.0, step=5.0)
            url = b3.text_input("Source URL / where you saw it")
            d1, d2, d3 = st.columns(3)
            eff = d1.date_input("Effective", date.today())
            exp = d2.date_input("Expires", date.today() + timedelta(days=30))
            ver = d3.checkbox("I verified this bonus is live in my Amex account", False)
            elig = st.checkbox("Confirmed my account is eligible (not targeted-only)", False)
            if st.form_submit_button("Add bonus"):
                db.add_transfer_bonus(src, pct, eff.isoformat(), exp.isoformat(), url, date.today().isoformat(), elig, ver)
                st.success("Bonus added.")
                st.rerun()
        if len(bdf):
            bid = st.selectbox("Deactivate / delete bonus id", [int(x) for x in bdf["id"]])
            c1, c2 = st.columns(2)
            if c1.button("Deactivate"):
                db.set_bonus_active(bid, False); st.rerun()
            if c2.button("Delete"):
                db.delete_bonus(bid); st.rerun()
        st.subheader("Try a conversion")
        t1, t2 = st.columns(2)
        tsrc = t1.selectbox("Program ", [s for s, p in PROGRAMS.items() if p.is_amex_partner], format_func=lambda s: PROGRAMS[s].program_name, key="try_src")
        tmiles = t2.number_input("Airline miles needed", 1000, 500000, 60000, step=1000)
        calc = TransferEngine.from_database(db).calculate(tsrc, int(tmiles))
        st.write(f"→ **{fmt_pts(calc.amex_points_per_passenger)} Amex points per person** (no bonus: {fmt_pts(calc.amex_points_per_passenger_no_bonus)})")
        for n in calc.notes:
            st.caption(n)

    with tabs[1]:
        cfg = load_scoring_config(db, profile.passengers)
        st.subheader("What matters most to you?")
        keys = list(PRIORITY_PRESETS)
        choice = st.radio("Ranking priority", keys, index=keys.index(cfg.priority) if cfg.priority in keys else 0,
                          format_func=lambda k: f"{PRIORITY_PRESETS[k]['label']} — {PRIORITY_PRESETS[k]['description']}")
        cpp = st.number_input("For the 'all-in cost' comparison, value one Amex point at (cents)", 0.5, 3.0, float(cfg.cents_per_point), step=0.1)
        with st.expander("Fine-tune the weights (advanced)"):
            st.caption("Weights are normalized to 100. See docs/SCORING.md for the formulas. Leave as-is to use the preset.")
            preset_w = PRIORITY_PRESETS[choice]["weights"]
            cols = st.columns(6)
            vals = {}
            for col, name in zip(cols, ["points", "fees", "convenience", "confidence", "quality", "flexibility"]):
                default = float(getattr(cfg.weights, name)) if choice == cfg.priority else float(preset_w[name])
                vals[name] = col.number_input(name, 0.0, 100.0, default, step=5.0)
            st.write(f"Total {sum(vals.values()):g} → normalized to 100.")
        if st.button("Save scoring priority"):
            custom = {k: v for k, v in vals.items() if abs(v - float(PRIORITY_PRESETS[choice]["weights"][k])) > 1e-9}
            save_scoring_config(db, choice, custom or None, cpp)
            st.success("Saved. The next scan (or pressing Search Awards) re-scores everything with this priority.")

    with tabs[0]:
        th = load_thresholds(db)
        sender = AlertSender(settings, db)
        st.subheader("Where alerts go")
        desc = sender.describe_channels()
        if desc:
            for ch, text in desc:
                st.write(f"• **{text}**" + ("" if ch in sender.channels else " — not active on this computer"))
        else:
            st.warning("No alert channel is configured.")
        with st.expander("How to set up alerts"):
            st.markdown("""
**Mac notification banners** — on automatically when the daily scan runs on your Mac. Nothing to set up.
If you don't see them, open System Settings → Notifications → *Script Editor* (or *osascript*) and allow notifications.

**Push alerts on your iPhone (free)** — install the **ntfy** app from the App Store, tap **+**, and subscribe to a topic
with a hard-to-guess name (for example `mark-awards-7h3k`). Then open the `.env` file in the project folder, put the same
name after `NTFY_TOPIC=`, save, and press **Send test alert** below.

**Email** — set `ALERT_EMAIL_TO` and the `SMTP_*` lines in `.env` (Gmail and iCloud both need an app-specific password).
""")
        if st.button("Send test alert"):
            if not sender.channels:
                st.error("No channel is active. On a Mac, notifications are on by default; otherwise set NTFY_TOPIC in .env and restart the dashboard.")
            else:
                results = sender.send_test()
                for ch, (ok, err) in results.items():
                    (st.success if ok else st.error)(f"{ch}: {'sent' if ok else err}")
        st.subheader("What counts as worth jumping on")
        a1, a2, a3 = st.columns(3)
        new_min = a1.number_input("Alert on new award scoring ≥", 0.0, 100.0, th.new_award_min_score)
        exc_min = a2.number_input("Exceptional alert scoring ≥", 0.0, 100.0, th.exceptional_min_score)
        rt_min = a3.number_input("Round-trip alert min score", 0.0, 100.0, th.round_trip_min_score)
        a4, a5, a6 = st.columns(3)
        rt_cash = a4.number_input("Round-trip alert only if cash for two ≤ ($)", 0, 5000, int(th.round_trip_max_fees_two_usd or 0), step=50,
                                  help="0 = no cash cap. Only trips whose fees are known on both legs can pass a cash cap.")
        rt_max = a5.number_input("…and Amex points for two ≤", 100000, 600000, th.round_trip_max_points_two, step=10000)
        seats_to = a6.number_input("Seat-increase alert when seats reach", 1, 4, th.notify_seats_increase_to)
        daily = st.checkbox("Send a short 'scan finished' summary after each scheduled scan", th.daily_summary)
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Off"]
        wk = st.selectbox("Weekly digest (top five cheapest trips) on", days, index=th.weekly_digest_weekday if th.weekly_digest_weekday is not None else 7)
        if st.button("Save alert thresholds"):
            db.set_setting(THRESHOLDS_KEY, {"new_award_min_score": new_min, "exceptional_min_score": exc_min, "round_trip_max_points_two": int(rt_max),
                                            "round_trip_max_fees_two_usd": float(rt_cash) if rt_cash else None, "round_trip_min_score": rt_min,
                                            "notify_seats_increase_to": int(seats_to), "daily_summary": bool(daily),
                                            "weekly_digest_weekday": None if wk == "Off" else days.index(wk)})
            st.success("Saved.")
        hist = db.alerts(30)
        if hist:
            st.caption("Alert history")
            st.dataframe(pd.DataFrame([{"sent": pacific(h["sent_at"]), "kind": h["kind"], "channel": h["channel"], "score": h["score"], "ok": bool(h["ok"]),
                                        "title": (json.loads(h["payload_json"]) or {}).get("title"), "error": h["error"]} for h in hist]), hide_index=True, use_container_width=True)

    with tabs[5]:
        runs = db.runs(30)
        if runs:
            st.dataframe(pd.DataFrame([{"id": r["id"], "started": pacific(r["started_at"]), "finished": pacific(r["finished_at"]), "status": r["status"],
                                        "trigger": r["trigger"], "calls": r["calls_made"], "records": r["records_seen"], "qualifying": r["qualifying"],
                                        "round_trips": r["round_trips"], "new": r["new_awards"], "summary": r["summary"]} for r in runs]), hide_index=True, use_container_width=True)
            rid = st.selectbox("Coverage for run", [r["id"] for r in runs])
            st.dataframe(pd.DataFrame(db.coverage(rid)), hide_index=True, use_container_width=True)
        else:
            st.write("No runs yet.")


PAGES = {"Home": page_home, "Best Deals": page_best_deals, "Search": page_search, "Settings": page_settings}
PAGES[page]()
