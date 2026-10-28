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
from award_hunter.models import RoundTrip, ScoredAward
from award_hunter.alerts import AlertSender
from award_hunter.scheduler import (
    THRESHOLDS_KEY, load_profile, load_scoring_config, load_thresholds, make_client, run_scan, save_profile, save_scoring_config,
)
from award_hunter.scoring import PRIORITY_PRESETS, all_in_cost_usd, fees_to_usd
from award_hunter.search import SearchProfile, build_plan, to_rows
from award_hunter.transfer_bonuses import TransferEngine
from award_hunter.transfer_partners import PROGRAMS

st.set_page_config(page_title="Amex Award Hunter", page_icon="✈️", layout="wide")
configure_logging()

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


def fmt_pts(v) -> str:
    return f"{int(v):,}" if v is not None and not pd.isna(v) else "n/a"


def fmt_money(v, cur="USD") -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "Not provided"
    return f"{v:,.0f} {cur or ''}".strip()


def scored_from_row(row: dict) -> ScoredAward:
    return ScoredAward.model_validate_json(row["payload_json"])


def rt_from_row(row: dict) -> RoundTrip:
    return RoundTrip.model_validate_json(row["payload_json"])


CARD_CSS = """
<style>
:root {
  --ah-surface: #ffffff; --ah-border: #e3e2dd; --ah-ink: #0b0b0b; --ah-muted: #52514e;
  --ah-accent: #2a78d6; --ah-accent-soft: #e7effc; --ah-good-soft: #e2f1e4; --ah-good-ink: #0a5d1e;
  --ah-warn-soft: #fdf1dc; --ah-warn-ink: #7a4b00;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ah-surface: #1f1f1e; --ah-border: #3a3a38; --ah-ink: #f0efec; --ah-muted: #c3c2b7;
    --ah-accent: #3987e5; --ah-accent-soft: #1c3a5e; --ah-good-soft: #163d22; --ah-good-ink: #bfe6c6;
    --ah-warn-soft: #4a3310; --ah-warn-ink: #f3d9a6;
  }
}
.ah-hero { padding: 6px 0 2px 0; }
.ah-hero .ah-label { color: var(--ah-muted); font-size: .9rem; margin-bottom: 2px; }
.ah-hero .ah-value { font-size: 52px; font-weight: 700; line-height: 1.05; color: var(--ah-ink); letter-spacing: -0.01em; }
.ah-hero .ah-detail { color: var(--ah-muted); font-size: 1rem; margin-top: 4px; }
.ah-card { border: 1px solid var(--ah-border); border-radius: 14px; padding: 16px 18px 14px 18px; background: var(--ah-surface);
           margin-bottom: 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
.ah-card .ah-top { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.ah-card .ah-dest { font-size: 1.25rem; font-weight: 700; color: var(--ah-ink); }
.ah-card .ah-dates { color: var(--ah-muted); font-size: .9rem; margin-top: 2px; }
.ah-card .ah-money { display: flex; gap: 22px; align-items: flex-end; margin: 12px 0 10px 0; flex-wrap: wrap; }
.ah-card .ah-cash { font-size: 2rem; font-weight: 700; line-height: 1; color: var(--ah-ink); }
.ah-card .ah-pts { font-size: 1.25rem; font-weight: 600; line-height: 1.15; color: var(--ah-ink); }
.ah-card .ah-sub { color: var(--ah-muted); font-size: .78rem; margin-top: 3px; }
.ah-card .ah-leg { display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; font-size: .9rem; color: var(--ah-ink);
                   padding: 6px 0; border-top: 1px solid var(--ah-border); }
.ah-card .ah-leg .ah-muted { color: var(--ah-muted); }
.ah-rank { display: inline-block; min-width: 1.9em; text-align: center; padding: 1px 6px; border-radius: 8px; background: var(--ah-ink); color: var(--ah-surface);
           font-size: .8rem; font-weight: 700; margin-right: 6px; vertical-align: middle; }
.ah-badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600; margin-left: 6px; vertical-align: middle; }
.ah-badge.new { background: var(--ah-good-soft); color: var(--ah-good-ink); }
.ah-badge.tier { background: var(--ah-accent-soft); color: var(--ah-accent); }
.ah-badge.warn { background: var(--ah-warn-soft); color: var(--ah-warn-ink); }
.ah-foot { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; font-size: .8rem; color: var(--ah-muted); }
.ah-foot a { color: var(--ah-accent); font-weight: 600; text-decoration: none; }
.ah-foot a:hover { text-decoration: underline; }
</style>
"""


def inject_css() -> None:
    st.markdown(CARD_CSS, unsafe_allow_html=True)


def _leg_stops(award) -> str:
    best = award.best_option()
    stops = best.stops if best and best.stops is not None else (0 if award.direct_available else None)
    if stops is None:
        return "stops unknown"
    if stops == 0:
        return "nonstop"
    return f"{stops} stop" if stops == 1 else f"{stops} stops"


def _fmt_day(d: date) -> str:
    return d.strftime("%a %b %-d")


def _esc(text) -> str:
    return (str(text) if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def trip_is_new(row: dict, last_started: str | None) -> bool:
    return bool(last_started) and (row.get("first_seen_at") or "") >= last_started


def deal_card(t: RoundTrip, row: dict, *, is_new: bool, alternatives: int, cfg, rank: int | None = None,
              emphasis: str = "cash", alt_label: str = "other return date") -> str:
    o, i = t.outbound.award, t.inbound.award
    cash = (f"${t.total_fees_two_major:,.0f}" + ("" if t.fees_complete else "+")) if t.total_fees_two_major is not None else "n/a"
    cash_sub = "cash for two, taxes & fees" + ("" if t.fees_complete else " (one leg missing fees)") if t.total_fees_two_major is not None else "fees not reported"
    pts = f"{t.total_amex_points_two:,}" if t.total_amex_points_two else "n/a"
    all_in = all_in_cost_usd(t.total_amex_points_two, t.total_fees_two_major if t.fees_complete else None, cfg.cents_per_point)
    badges = ""
    if is_new:
        badges += '<span class="ah-badge new">NEW</span>'
    badges += f'<span class="ah-badge tier">{t.score.total:.0f}/100</span>'
    if t.open_jaw_europe or t.open_jaw_us:
        badges += '<span class="ah-badge warn">open-jaw</span>'
    info = PROGRAMS.get(o.source)
    link = f'<a href="{info.booking_url}" target="_blank">Verify &amp; book on {_esc(info.program_name)} &rarr;</a>' if info and info.booking_url else ""
    alt = f"+{alternatives} {alt_label}{'s' if alternatives != 1 else ''}" if alternatives else ""
    rank_html = f'<span class="ah-rank">#{rank}</span> ' if rank else ""
    cash_cls, pts_cls = ("ah-cash", "ah-pts") if emphasis != "points" else ("ah-pts", "ah-cash")
    all_in_txt = f"All-in about ${all_in:,.0f} at {cfg.cents_per_point:g}&cent;/pt" if all_in is not None else ""
    legs = ""
    for label, leg in (("Out", t.outbound), ("Back", t.inbound)):
        a = leg.award
        usd = fees_to_usd(a.taxes_minor_units, a.taxes_currency, cfg) if a.fees_known else None
        if usd is not None and (a.taxes_currency or "USD").upper() == "USD":
            fee = f"${usd:,.0f}"
        elif usd is not None:
            fee = f"~${usd:,.0f} <span class=\"ah-muted\">({a.taxes_major:,.0f} {a.taxes_currency})</span>"
        else:
            fee = "fees n/a"
        seats = f"{a.remaining_seats} seats" if a.remaining_seats else "seats n/r"
        legs += (f'<div class="ah-leg"><div><b>{label}</b> {a.origin} &rarr; {a.destination} &middot; {_fmt_day(a.travel_date)}'
                 f'<div class="ah-muted">{_esc(a.airlines or "airline n/a")} &middot; {_esc(leg.amex.program_name)} &middot; {_leg_stops(a)} &middot; {seats}</div></div>'
                 f'<div style="text-align:right">{fee}<div class="ah-muted">{(leg.amex.amex_points_per_passenger or 0) // 1000}k pts/pp</div></div></div>')
    return f"""
<div class="ah-card">
  <div class="ah-top"><div>{rank_html}<span class="ah-dest">{_esc(t.destination_city)}, {_esc(t.destination_country)}</span>{badges}
    <div class="ah-dates">{_fmt_day(o.travel_date)} &rarr; {_fmt_day(i.travel_date)} &middot; {t.trip_days} days &middot; business class for 2</div></div></div>
  <div class="ah-money">
    <div><div class="{cash_cls}">{cash}</div><div class="ah-sub">{cash_sub}</div></div>
    <div><div class="{pts_cls}">{pts} pts</div><div class="ah-sub">Amex points for two</div></div>
  </div>
  {legs}
  <div class="ah-foot"><span>{all_in_txt}{(" &middot; " + alt) if all_in_txt and alt else alt}</span>{link}</div>
</div>"""


def rank_trips(rows: list[dict], mode: str, cfg) -> list[dict]:
    big = 10**12
    if mode == "Least cash":
        key = lambda r: (0 if r["fees_complete"] else 1, r["total_fees_two"] if r["total_fees_two"] is not None else big, r["total_points_two"] or big)  # noqa: E731
    elif mode == "Fewest points":
        key = lambda r: (r["total_points_two"] or big, r["total_fees_two"] if r["total_fees_two"] is not None else big)  # noqa: E731
    elif mode == "All-in cost":
        key = lambda r: (all_in_cost_usd(r["total_points_two"], r["total_fees_two"] if r["fees_complete"] else None, cfg.cents_per_point) or big)  # noqa: E731
    else:
        key = lambda r: (-(r["score"] or 0), r["total_fees_two"] if r["total_fees_two"] is not None else big)  # noqa: E731
    return sorted(rows, key=key)


def dedupe_trips(rows: list[dict], *, per_destination: bool = False) -> list[tuple[dict, int]]:
    """Keep the first (best-ranked) trip per destination (or per destination + outbound date) and count the rest."""
    seen: dict[tuple, int] = {}
    kept: list[dict] = []
    keyf = (lambda r: (r["destination_city"],)) if per_destination else (lambda r: (r["destination_city"], r["out_date"]))
    for r in rows:
        k = keyf(r)
        if k in seen:
            seen[k] += 1
            continue
        seen[k] = 0
        kept.append(r)
    return [(r, seen[keyf(r)]) for r in kept]


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
page = st.sidebar.radio("Navigate", ["Best Deals", "Search", "Settings"])
icon, status_text = api_status()
st.sidebar.markdown(f"**API** {icon} {status_text}")
st.sidebar.caption(budget_line())
last_run = db.last_run()
if last_run:
    st.sidebar.caption(f"Last scan: {pacific(last_run.get('finished_at') or last_run['started_at'])} ({last_run['status']})")
for w in validate_security(settings):
    st.sidebar.warning(w)

profile = load_profile(db)


# --------------------------------------------------------------------------- #
# Best Deals (home)
# --------------------------------------------------------------------------- #
def page_best_deals() -> None:
    cfg = load_scoring_config(db, profile.passengers)
    last = db.last_run()
    last_started = last["started_at"] if last else None
    trips = db.round_trips(min_score=None, limit=1000)
    awards = db.current_awards(qualifying_only=True, min_score=profile.min_score)

    t1, t2 = st.columns([4, 1])
    t1.title("Best Deals")
    preset = PRIORITY_PRESETS.get(cfg.priority, PRIORITY_PRESETS["minimize_cash"])
    t1.caption(f"Business class for two, Southern California ⇄ Europe · ranking: **{preset['label']}** "
               f"· last scan {pacific(last['finished_at']) if last and last.get('finished_at') else 'never'}")
    t2.write("")
    if t2.button("🔄 Scan now", use_container_width=True, disabled=not settings.has_api_key, help="About a minute; ~10 API calls."):
        bar = st.progress(0.0, text="Starting…")
        with st.spinner("Searching Seats.aero…"):
            outcome = run_scan(settings, db, profile=profile, trigger="dashboard", send_alerts=True,
                               progress=lambda i, n, label: bar.progress(i / n, text=f"[{i}/{n}] {label}"))
        bar.empty()
        st.toast(f"Scan done: {len(outcome.result.qualifying)} awards, {len(outcome.round_trips)} trips, {outcome.new_round_trips} new")
        st.rerun()

    if not trips and not awards:
        st.warning("Nothing stored yet. Go to **Search** and press **Search Awards**, or wait for the first scheduled scan.")
        return

    # Hero: the single number this dashboard leads with.
    complete = [r for r in trips if r["fees_complete"] and r["total_fees_two"] is not None]
    hero = min(complete, key=lambda r: (r["total_fees_two"], r["total_points_two"] or 10**9)) if complete else None
    h1, h2 = st.columns([3, 2])
    with h1:
        if hero:
            st.markdown(f"""<div class="ah-hero"><div class="ah-label">Lowest cash out of pocket for a complete round trip, two people</div>
<div class="ah-value">${hero['total_fees_two']:,.0f}</div>
<div class="ah-detail">+ {hero['total_points_two']:,} Amex points · {hero['destination_city']} · {hero['out_date']} → {hero['in_date']} ({hero['trip_days']} days)</div></div>""",
                        unsafe_allow_html=True)
        else:
            st.markdown('<div class="ah-hero"><div class="ah-label">No complete round trip with known fees yet</div></div>', unsafe_allow_html=True)
    with h2:
        new_trips = sum(1 for r in trips if trip_is_new(r, last_started))
        m1, m2 = st.columns(2)
        m1.metric("Round trips found", len(trips), f"{new_trips} new" if new_trips else None)
        m2.metric("Qualifying one-ways", len(awards))
        m3, m4 = st.columns(2)
        fewest = min(trips, key=lambda r: r["total_points_two"] or 10**9) if trips else None
        m3.metric("Fewest points, round trip", f"{fewest['total_points_two'] // 1000}k" if fewest and fewest["total_points_two"] else "—",
                  fewest["destination_city"] if fewest else None)
        m4.metric("API", icon, status_text.split(" at ")[0] if status_text else "")

    if not trips:
        st.info("One-way awards were found but no outbound/return pair fits your trip length. Widen the trip length or window in **Search**.")
    else:
        st.subheader("Top complete trips")
        c1, c2, c3, c4 = st.columns([2.2, 1, 0.9, 0.9])
        mode = c1.radio("Rank by", ["Least cash", "Fewest points", "All-in cost", "Best overall"], horizontal=True)
        per_dest = c4.checkbox("One per destination", True, help="Off = one card per destination and departure date.")
        fees_known = [r["total_fees_two"] for r in trips if r["total_fees_two"] is not None]
        cash_default = min(5000, int(max(fees_known) // 100 * 100 + 100)) if fees_known else 5000
        max_cash = c2.number_input("Max cash for two ($)", 0, 5000, cash_default, step=100)
        only_new = c3.checkbox("New only", False)
        if st.session_state.get("rank_mode") != mode:
            st.session_state["rank_mode"] = mode
            st.session_state["cards_limit"] = 9
        ranked = rank_trips(trips, mode, cfg)
        ranked = [r for r in ranked if (r["total_fees_two"] is None or r["total_fees_two"] <= max_cash) and (not only_new or trip_is_new(r, last_started))]
        limit = st.session_state.get("cards_limit", 9)
        all_cards = dedupe_trips(ranked, per_destination=per_dest)
        cards = all_cards[:limit]
        sort_hint = {"Least cash": "sorted by cash for two, then points", "Fewest points": "sorted by Amex points for two, then cash",
                     "All-in cost": f"sorted by cash + points at {cfg.cents_per_point:g}¢ each", "Best overall": "sorted by deal score"}[mode]
        st.caption(f"#1 is best · {sort_hint}" + (" · one card per destination" if per_dest else " · one card per destination and departure date"))
        if not cards:
            st.info("No trips match these filters.")
        cols = st.columns(3)
        for idx, (row, alts) in enumerate(cards):
            with cols[idx % 3]:
                st.markdown(deal_card(rt_from_row(row), row, is_new=trip_is_new(row, last_started), alternatives=alts, cfg=cfg, rank=idx + 1,
                                      emphasis="points" if mode == "Fewest points" else "cash",
                                      alt_label="other date option" if per_dest else "other return date"),
                            unsafe_allow_html=True)
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
    st.title("Search")
    st.caption("Set what you're looking for, then press **Search Awards**. One search is about 10–20 of your 1,000 daily Seats.aero calls.")
    with st.form("search_form"):
        col1, col2 = st.columns(2)
        with col1:
            origins = st.multiselect("Fly from", list(US_ORIGINS), default=[o for o in profile.origins if o in US_ORIGINS],
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
            max_observation_age_hours=float(max_age_days) * 24,
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
        if st.button("Save alert thresholds"):
            db.set_setting(THRESHOLDS_KEY, {"new_award_min_score": new_min, "exceptional_min_score": exc_min, "round_trip_max_points_two": int(rt_max),
                                            "round_trip_max_fees_two_usd": float(rt_cash) if rt_cash else None, "round_trip_min_score": rt_min,
                                            "notify_seats_increase_to": int(seats_to), "daily_summary": bool(daily)})
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


PAGES = {"Best Deals": page_best_deals, "Search": page_search, "Settings": page_settings}
PAGES[page]()
