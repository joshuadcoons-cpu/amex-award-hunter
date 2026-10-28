"""Pure presentation helpers for the Streamlit dashboard: HTML cards, tiles, inline SVG graphics, ranking.

Nothing here touches Streamlit or the database, so it is unit-testable and keeps app.py to page logic.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .aircraft import lie_flat_likely
from .booking import booking_link, transfer_warning
from .models import RoundTrip, ScoredAward
from .scoring import all_in_cost_usd, fees_to_usd
from .seasonality import AVOID, CAUTION, GOOD, LABELS, month_summary, rating_for
from .transfer_partners import sweet_spot


def fmt_pts(v) -> str:
    return f"{int(v):,}" if v is not None and not pd.isna(v) else "n/a"

def scored_from_row(row: dict) -> ScoredAward:
    return ScoredAward.model_validate_json(row["payload_json"])

def rt_from_row(row: dict) -> RoundTrip:
    return RoundTrip.model_validate_json(row["payload_json"])

CARD_CSS = """
<style>
:root {
  /* The app is pinned to a light theme in .streamlit/config.toml, so these never switch with the OS dark mode. */
  --ah-surface: #ffffff; --ah-border: #dddcd6; --ah-ink: #0b0b0b; --ah-muted: #3f3e3a;
  --ah-accent: #2a78d6; --ah-accent-soft: #e7effc; --ah-good-soft: #e2f1e4; --ah-good-ink: #0a5d1e;
  --ah-warn-soft: #fdf1dc; --ah-warn-ink: #7a4b00;
}
/* Streamlit's own captions and labels are too faint on a bright screen. */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p, .stCaption { color: #3f3e3a !important; font-size: .92rem !important; }
[data-testid="stWidgetLabel"] p { color: #1f1e1c !important; }
.ah-hero { padding: 6px 0 2px 0; }
.ah-hero .ah-label { color: var(--ah-muted); font-size: .9rem; margin-bottom: 2px; }
.ah-hero .ah-value { font-size: 52px; font-weight: 700; line-height: 1.05; color: var(--ah-ink); letter-spacing: -0.01em; }
.ah-hero .ah-detail { color: var(--ah-muted); font-size: 1rem; margin-top: 4px; }
.ah-card { border: 1px solid var(--ah-border); border-radius: 14px; padding: 16px 18px 14px 18px; background: var(--ah-surface);
           margin-bottom: 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); height: 384px; display: flex; flex-direction: column; overflow: hidden; }
.ah-card .ah-dest { font-size: 1.2rem; font-weight: 700; color: var(--ah-ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ah-card .ah-badges { height: 24px; margin: 4px 0 2px 0; white-space: nowrap; overflow: hidden; }
.ah-card .ah-dates { color: var(--ah-muted); font-size: .92rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ah-card .ah-money { display: flex; gap: 22px; align-items: flex-end; margin: 12px 0 10px 0; height: 54px; }
.ah-card .ah-cash { font-size: 2rem; font-weight: 700; line-height: 1; color: var(--ah-ink); }
.ah-card .ah-pts { font-size: 1.25rem; font-weight: 600; line-height: 1.15; color: var(--ah-ink); }
.ah-card .ah-sub { color: var(--ah-muted); font-size: .82rem; margin-top: 3px; white-space: nowrap; }
.ah-card .ah-leg { display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; font-size: .9rem; color: var(--ah-ink);
                   padding: 7px 0; border-top: 1px solid var(--ah-border); height: 58px; }
.ah-card .ah-leg > div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ah-card .ah-leg .ah-muted { color: var(--ah-muted); font-size: .84rem; }
.ah-badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600; margin-right: 6px; vertical-align: middle; }
.ah-rank { display: inline-block; min-width: 1.9em; text-align: center; padding: 1px 6px; border-radius: 8px; background: var(--ah-ink); color: var(--ah-surface);
           font-size: .8rem; font-weight: 700; margin-right: 6px; vertical-align: middle; }
.ah-badge.new { background: var(--ah-good-soft); color: var(--ah-good-ink); }
.ah-badge.tier { background: var(--ah-accent-soft); color: var(--ah-accent); }
.ah-badge.warn { background: var(--ah-warn-soft); color: var(--ah-warn-ink); }
.ah-badge.star { background: #fff4cc; color: #6b4e00; }
.ah-ok { color: var(--ah-good-ink); font-weight: 600; }
.ah-short { color: var(--ah-warn-ink); font-weight: 600; }
.ah-tile { border: 1px solid var(--ah-border); border-radius: 14px; padding: 14px 16px; background: var(--ah-surface); height: 124px;
           box-shadow: 0 1px 2px rgba(0,0,0,0.04); display: flex; flex-direction: column; justify-content: space-between; overflow: hidden; }
.ah-tile .ah-tlabel { color: var(--ah-muted); font-size: .78rem; letter-spacing: .02em; text-transform: uppercase; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ah-tile .ah-tvalue { font-size: 1.75rem; font-weight: 700; line-height: 1.1; color: var(--ah-ink); margin-top: 2px; white-space: nowrap; }
.ah-tile .ah-tsub { color: var(--ah-muted); font-size: .86rem; line-height: 1.3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ah-stats { margin-top: 14px; }
.ah-tile .ah-tsub b { color: var(--ah-ink); }
.ah-tile.ok { border-left: 5px solid #1baf7a; }
.ah-tile.warn { border-left: 5px solid #eda100; }
.ah-tile.bad { border-left: 5px solid #e34948; }
.ah-herotile { border: 1px solid var(--ah-border); border-radius: 18px; padding: 22px 26px 20px 26px; background: var(--ah-surface);
               box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.ah-herotile .ah-hlabel { color: var(--ah-muted); font-size: .85rem; letter-spacing: .04em; text-transform: uppercase; }
.ah-herotile .ah-hvalue { font-size: 68px; font-weight: 800; line-height: 1.0; color: var(--ah-ink); letter-spacing: -0.02em; margin: 6px 0 4px 0; }
.ah-herotile .ah-hunit { font-size: 1.05rem; color: var(--ah-ink); margin-bottom: 14px; }
.ah-herotile .ah-hrow { display: flex; gap: 28px; flex-wrap: wrap; }
.ah-herotile .ah-hrow .k { color: var(--ah-muted); font-size: .8rem; text-transform: uppercase; letter-spacing: .03em; }
.ah-herotile .ah-hrow .v { color: var(--ah-ink); font-size: 1.25rem; font-weight: 700; line-height: 1.2; }
.ah-section { margin-top: 10px; }
.ah-home-h { font-size: 1.35rem; font-weight: 700; margin: 26px 0 6px 0; color: var(--ah-ink); }
.ah-home-p { color: var(--ah-ink); font-size: 1rem; line-height: 1.55; max-width: 860px; }
.ah-legend { display: flex; flex-wrap: wrap; gap: 14px 22px; margin-top: 10px; }
.ah-legend div { font-size: .9rem; color: var(--ah-ink); }
.ah-legend span.sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 7px; vertical-align: -1px; }
.ah-legend small { color: var(--ah-muted); font-size: .86rem; }
.ah-foot { display: flex; flex-direction: column; gap: 4px; margin-top: auto; padding-top: 8px; font-size: .84rem; color: var(--ah-muted); white-space: nowrap; }
.ah-foot span { overflow: hidden; text-overflow: ellipsis; }
.ah-foot .ah-link { font-size: .9rem; }
.ah-foot a { color: var(--ah-accent); font-weight: 600; text-decoration: none; }
.ah-foot a:hover { text-decoration: underline; }
</style>
"""

SHORT_PROGRAM = {"aeroplan": "Aeroplan", "flyingblue": "Flying Blue", "virginatlantic": "Virgin Atlantic", "emirates": "Emirates",
                 "etihad": "Etihad", "singapore": "KrisFlyer", "qantas": "Qantas", "qatar": "Qatar Avios", "delta": "Delta",
                 "jetblue": "JetBlue", "aeromexico": "Aeromexico"}

def short_program(source: str, fallback: str) -> str:
    return SHORT_PROGRAM.get(source, fallback)

def affordability(points_needed: int | None, bal: dict) -> tuple[str, str]:
    """(state, text): state in {'none','ok','short'}; text is a short human phrase."""
    if not bal.get("balance") or points_needed is None:
        return "none", ""
    gap = points_needed - bal["balance"]
    if gap <= 0:
        return "ok", "you have enough"
    months = f" · ~{max(1, round(gap / bal['monthly_earn']))} mo at your earn rate" if bal.get("monthly_earn") else ""
    return "short", f"short {gap:,}{months}"

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

def trip_is_nonstop(row: dict) -> bool:
    t = rt_from_row(row)
    return _leg_stops(t.outbound.award) == "nonstop" and _leg_stops(t.inbound.award) == "nonstop"

def trip_business_only(row: dict) -> bool:
    t = rt_from_row(row)
    return all(leg.award.cabin.value != "premium" for leg in (t.outbound, t.inbound))


def trip_lie_flat(row: dict) -> bool:
    """True unless a leg is a known recliner-style narrowbody. Unknown aircraft counts as OK (never over-claims)."""
    t = rt_from_row(row)
    return all(lie_flat_likely(leg.award)[0] != "no" for leg in (t.outbound, t.inbound))


def trip_is_new(row: dict, last_started: str | None) -> bool:
    return bool(last_started) and (row.get("first_seen_at") or "") >= last_started

def deal_card(t: RoundTrip, row: dict, *, is_new: bool, alternatives: int, cfg, rank: int | None = None,
              emphasis: str = "cash", alt_label: str = "returns", bal: dict | None = None, history: dict | None = None) -> str:
    o, i = t.outbound.award, t.inbound.award
    cash = (f"${t.total_fees_two_major:,.0f}" + ("" if t.fees_complete else "+")) if t.total_fees_two_major is not None else "n/a"
    cash_sub = "cash for two, taxes & fees" + ("" if t.fees_complete else " (one leg missing fees)") if t.total_fees_two_major is not None else "fees not reported"
    pts = f"{t.total_amex_points_two:,}" if t.total_amex_points_two else "n/a"
    extras = (t.ground_cost_two_usd or 0) + (t.positioning_cost_two_usd or 0)
    all_in = all_in_cost_usd(t.total_amex_points_two, (t.total_fees_two_major + extras) if t.fees_complete else None, cfg.cents_per_point)
    badges = ""
    if is_new:
        badges += '<span class="ah-badge new">NEW</span>'
    badges += f'<span class="ah-badge tier">{t.score.total:.0f}/100</span>'
    if t.open_jaw_europe or t.open_jaw_us:
        badges += '<span class="ah-badge warn">open-jaw</span>'
    bonus_pcts = {leg.amex.bonus_percent for leg in (t.outbound, t.inbound) if leg.amex.bonus_percent}
    if bonus_pcts:
        badges += f'<span class="ah-badge new">+{max(bonus_pcts):g}% bonus</span>'
    spots = {sweet_spot(leg.award.source, leg.award.miles_per_passenger) for leg in (t.outbound, t.inbound)} - {None}
    if spots:
        badges += '<span class="ah-badge star">&#9733; ' + ("best price" if "best" in spots else "sweet spot") + '</span>'
    flat = {lie_flat_likely(leg.award)[0] for leg in (t.outbound, t.inbound)}
    if "no" in flat:
        badges += '<span class="ah-badge warn">recliner seat?</span>'
    if any(transfer_warning(leg.award.source, leg.award.remaining_seats) for leg in (t.outbound, t.inbound)):
        badges += '<span class="ah-badge warn">slow transfer</span>'
    if any(leg.award.cabin.value == "premium" for leg in (t.outbound, t.inbound)):
        badges += '<span class="ah-badge warn">premium econ</span>'
    tips = [w for w in t.warnings if w.startswith("Aeroplan tip")]
    if tips:
        badges += f'<span class="ah-badge tier" title="{_esc(tips[0])}">stopover 5k</span>'
    if history:
        for leg in (t.outbound, t.inbound):
            a, leg_pts = leg.award, leg.amex.amex_points_per_passenger
            h = history.get((a.origin, a.destination, a.source))
            if h and leg_pts and h["days"] >= 7 and leg_pts <= h["min"]:
                badges += f'<span class="ah-badge new" title="Lowest Amex price seen for {a.origin}→{a.destination} on {_esc(short_program(a.source, leg.amex.program_name))} in the last 30 days ({h["days"]} days of data; median {h["median"]:,})">30-day low</span>'
                break
    state, afford_txt = affordability(t.total_amex_points_two, bal or {})
    if state == "ok":
        pts_sub = 'for two &middot; <span class="ah-ok">enough &#10003;</span>'
    elif state == "short":
        gap = t.total_amex_points_two - (bal or {}).get("balance", 0)
        pts_sub = f'for two &middot; <span class="ah-short">short {gap // 1000}k</span>'
    else:
        pts_sub = "Amex points for two"
    out_url = booking_link(o.source, o.origin, o.destination, o.travel_date)
    in_url = booking_link(i.source, i.origin, i.destination, i.travel_date)
    link = ""
    if out_url:
        link += f'<a href="{out_url}" target="_blank">Verify out &rarr;</a>'
    if in_url:
        link += (" &nbsp;·&nbsp; " if link else "") + f'<a href="{in_url}" target="_blank">Verify return &rarr;</a>'
    alt = f"+{alternatives} {alt_label}" if alternatives else ""
    rank_html = f'<span class="ah-rank">#{rank}</span> ' if rank else ""
    cash_cls, pts_cls = ("ah-cash", "ah-pts") if emphasis != "points" else ("ah-pts", "ah-cash")
    extras_detail = "; ".join(x for x in (t.ground_cost_note, t.positioning_note) if x)
    cpp_txt = ""
    if t.cash_fare_two_usd and t.total_amex_points_two:
        cpp = (t.cash_fare_two_usd - (t.total_fees_two_major or 0)) / t.total_amex_points_two * 100
        cpp_txt = f' &middot; <span title="{_esc(t.cash_fare_note or "")}"><b>{cpp:.1f}&cent;/pt</b> vs cash</span>'
    all_in_txt = ((f'<span title="Cash + points at {cfg.cents_per_point:g}&cent; each' + (f'. Extras: {_esc(extras_detail)}' if extras_detail else "")
                   + f'">All-in ~${all_in:,.0f}</span>') + (f" &middot; +${extras:,.0f} extras" if extras and not cpp_txt else "") + cpp_txt
                  if all_in is not None else cpp_txt.lstrip(" &middot;"))
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
        flat_state, aircraft = lie_flat_likely(a)
        craft = f" &middot; {_esc(aircraft)}" if aircraft else ""
        legs += (f'<div class="ah-leg"><div><b>{label}</b> {a.origin} &rarr; {a.destination} &middot; {a.travel_date.strftime("%b %-d")}'
                 f'<br><span class="ah-muted">{_esc(short_program(a.source, leg.amex.program_name))} &middot; {_leg_stops(a)} &middot; {seats}{craft}</span></div>'
                 f'<div style="text-align:right">{fee}<br><span class="ah-muted">{(leg.amex.amex_points_per_passenger or 0) // 1000}k pts/pp'
                 f'{" w/ bonus" if leg.amex.bonus_percent else ""}</span></div></div>')
    return f"""
<div class="ah-card">
  <div class="ah-dest">{rank_html}{_esc(t.destination_city)}, {_esc(t.destination_country)}</div>
  <div class="ah-badges">{badges}</div>
  <div class="ah-dates">{_fmt_day(o.travel_date)} &rarr; {_fmt_day(i.travel_date)} &middot; {t.trip_days} days</div>
  <div class="ah-money">
    <div><div class="{cash_cls}">{cash}</div><div class="ah-sub">{cash_sub}</div></div>
    <div><div class="{pts_cls}">{pts} pts</div><div class="ah-sub">{pts_sub}</div></div>
  </div>
  {legs}
  <div class="ah-foot"><span>{all_in_txt}{(" &middot; " + alt) if all_in_txt and alt else alt}</span><span class="ah-link">{link}</span></div>
</div>"""

def tile(label: str, value: str, sub: str = "", tone: str = "") -> str:
    return (f'<div class="ah-tile {tone}"><div class="ah-tlabel">{_esc(label)}</div><div class="ah-tvalue">{value}</div>'
            f'<div class="ah-tsub">{sub}</div></div>')

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]

FACTORS = [("fees", "Cash fees", "Taxes & fees per person. Under $50 is perfect; over $600 scores almost nothing."),
           ("points", "Amex points", "Points per person one way after the transfer ratio. 50k is perfect; 120k is zero."),
           ("convenience", "Convenience", "Nonstop is best; each connection, long or overnight layover, or mixed cabin costs points."),
           ("confidence", "Data confidence", "Were two seats actually reported, and how fresh is the observation?"),
           ("quality", "Airline & cabin", "Known long-haul business products flying nonstop score highest."),
           ("flexibility", "Transfer certainty", "Full marks when no transfer bonus has to be assumed.")]

def weights_bar_svg(cfg) -> str:
    w = cfg.weights
    total = w.total() or 1
    x, parts = 0.0, []
    for (key, label, _), color in zip(FACTORS, PALETTE):
        frac = getattr(w, key) / total
        width = max(frac * 1000 - 3, 0)
        txt = f"{frac * 100:.0f}%" if frac >= 0.07 else ""
        parts.append(f'<rect x="{x:.1f}" y="0" width="{width:.1f}" height="44" rx="6" fill="{color}"></rect>')
        if txt:
            parts.append(f'<text x="{x + width / 2:.1f}" y="28" text-anchor="middle" font-size="17" font-weight="700" fill="#ffffff">{txt}</text>')
        x += frac * 1000
    legend = "".join(f'<div><span class="sw" style="background:{c}"></span><b>{label}</b> {getattr(w, key):g}<br><small>{desc}</small></div>'
                     for (key, label, desc), c in zip(FACTORS, PALETTE))
    return (f'<svg viewBox="0 0 1000 44" width="100%" height="44" role="img" aria-label="Scoring weights">{"".join(parts)}</svg>'
            f'<div class="ah-legend" style="display:grid;grid-template-columns:repeat(3,1fr)">{legend}</div>')

def curve_svg(anchors, *, x_label: str, x_fmt, color: str, title: str) -> str:
    """Small line chart of a 0-1 scoring factor against its input, drawn from the live anchors."""
    W, H, L, B, T, R = 360, 180, 44, 46, 18, 14
    xs = [a[0] for a in anchors]
    x0, x1 = 0, xs[-1]
    def px(v): return L + (v - x0) / (x1 - x0) * (W - L - R)
    def py(f): return T + (1 - f) * (H - T - B)
    pts = [(px(0), py(anchors[0][1]))] + [(px(a), py(f)) for a, f in anchors]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = poly + f" {px(x1):.1f},{py(0):.1f} {px(0):.1f},{py(0):.1f}"
    grid = "".join(f'<line x1="{L}" y1="{py(f):.1f}" x2="{W - R}" y2="{py(f):.1f}" stroke="#e9e8e3" stroke-width="1"></line>'
                   f'<text x="{L - 8}" y="{py(f) + 4:.1f}" text-anchor="end" font-size="11" fill="#52514e">{f:.1f}</text>' for f in (0, 0.5, 1))
    ticks = "".join(f'<text x="{px(a):.1f}" y="{H - 26}" text-anchor="middle" font-size="11" fill="#52514e">{x_fmt(a)}</text>' for a, _ in anchors[::2])
    dots = "".join(f'<circle cx="{px(a):.1f}" cy="{py(f):.1f}" r="4.5" fill="{color}" stroke="#ffffff" stroke-width="2"></circle>' for a, f in anchors)
    return (f'<div style="font-weight:600;margin:8px 0 2px 0">{title}</div>'
            f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px" role="img" aria-label="{title}">'
            f'{grid}<polygon points="{area}" fill="{color}" opacity="0.10"></polygon>'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"></polyline>{dots}{ticks}'
            f'<text x="{(L + W - R) / 2:.0f}" y="{H - 6}" text-anchor="middle" font-size="11.5" fill="#52514e">{x_label}</text></svg>')

def flow_svg() -> str:
    steps = [("Seats.aero", "Cached award seats across 19 airports and 90+ days, in ~10 API calls"),
             ("Amex math", "Airline miles → Membership Rewards points you would actually transfer"),
             ("Pair trips", "Outbound + return 7–14 days apart, two business seats each way"),
             ("Score 0–100", "Cash first, then points, convenience, data confidence"),
             ("Alert you", "Mac banner / phone push when a deal clears your thresholds")]
    icons = [
        '<circle cx="0" cy="0" r="13" fill="none" stroke="#fff" stroke-width="2.5"/><circle cx="0" cy="0" r="5" fill="#fff"/>',
        '<rect x="-14" y="-9" width="28" height="18" rx="3" fill="none" stroke="#fff" stroke-width="2.5"/><line x1="-14" y1="-2" x2="14" y2="-2" stroke="#fff" stroke-width="2.5"/>',
        '<path d="M-14 6 L-2 -8 L4 -1 L14 -12" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/><path d="M-14 -6 L14 8" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round" opacity=".6"/>',
        '<path d="M-14 10 L-14 -2 M-5 10 L-5 -10 M4 10 L4 -5 M13 10 L13 -12" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/>',
        '<path d="M-10 6 Q-10 -10 0 -10 Q10 -10 10 6 Z" fill="none" stroke="#fff" stroke-width="2.5"/><circle cx="0" cy="11" r="3" fill="#fff"/>',
    ]
    W, n, bw, gap = 1000, 5, 172, 35
    parts = []
    for i, ((title, desc), ic) in enumerate(zip(steps, icons)):
        x = i * (bw + gap)
        parts.append(f'<rect x="{x}" y="0" width="{bw}" height="64" rx="12" fill="{PALETTE[i]}"></rect>')
        parts.append(f'<g transform="translate({x + 32},32)">{ic}</g>')
        parts.append(f'<text x="{x + 60}" y="39" font-size="19" font-weight="700" fill="#ffffff">{title}</text>')
        parts.append(f'<foreignObject x="{x}" y="72" width="{bw}" height="90"><div xmlns="http://www.w3.org/1999/xhtml" '
                     f'style="font-size:13.5px;line-height:1.35;color:#52514e;font-family:inherit">{desc}</div></foreignObject>')
        if i < n - 1:
            ax = x + bw + 4
            parts.append(f'<path d="M{ax} 32 L{ax + gap - 8} 32" stroke="#b9b8b1" stroke-width="2.5"></path>'
                         f'<path d="M{ax + gap - 14} 25 L{ax + gap - 6} 32 L{ax + gap - 14} 39" fill="none" stroke="#b9b8b1" stroke-width="2.5"></path>')
    return f'<svg viewBox="0 0 {W} 165" width="100%" role="img" aria-label="How Amex Award Hunter works">{"".join(parts)}</svg>'

def tier_ladder_svg() -> str:
    tiers = [("90–100", "Exceptional", "Drop everything and verify it", "#0d366b"),
             ("80–89", "Excellent", "Alert-worthy; usually a great deal", "#1c5cab"),
             ("70–79", "Worth considering", "Shown on Best Deals; compare carefully", "#5598e7"),
             ("below 70", "Hidden", "Kept for price-drop tracking only", "#c9c8c1")]
    parts = []
    for i, (rng, name, desc, color) in enumerate(tiers):
        y = i * 58
        parts.append(f'<rect x="0" y="{y}" width="150" height="46" rx="10" fill="{color}"></rect>')
        parts.append(f'<text x="75" y="{y + 29}" text-anchor="middle" font-size="17" font-weight="700" fill="#ffffff">{rng}</text>')
        parts.append(f'<text x="170" y="{y + 20}" font-size="17" font-weight="700" fill="#0b0b0b">{name}</text>')
        parts.append(f'<text x="170" y="{y + 40}" font-size="13.5" fill="#52514e">{desc}</text>')
    return f'<svg viewBox="0 0 560 230" width="100%" style="max-width:560px" role="img" aria-label="Score tiers">{"".join(parts)}</svg>'

SEASON_FILL = {GOOD: "#9fd9b5", CAUTION: "#f6dc8f", AVOID: "#f2a8a7"}

SEASON_INK = {GOOD: "#0f6b2f", CAUTION: "#7a4b00", AVOID: "#8a1f1e"}

def when_to_go_svg(start: date, months: int = 12, found_days: set | None = None) -> str:
    """Year calendar coloured by historical award-space season, with a dot where the latest scan found two seats."""
    found_days = found_days or set()
    cols, cell, gap = 4, 15.5, 2
    mw = 7 * (cell + gap) + 26      # month block width
    mh = 7 * (cell + gap) + 38      # title + 6 week rows
    rows_n = -(-months // cols)
    W, H = cols * (mw + 18), rows_n * (mh + 12) + 4
    parts = []
    y, m = start.year, start.month
    for idx in range(months):
        first = date(y, m, 1)
        bx, by = (idx % cols) * (mw + 18), (idx // cols) * (mh + 12)
        top, _ = month_summary(y, m)
        parts.append(f'<text x="{bx}" y="{by + 14}" font-size="13.5" font-weight="700" fill="#0b0b0b">{first:%b %Y}</text>')
        parts.append(f'<text x="{bx + mw - 26}" y="{by + 14}" text-anchor="end" font-size="11.5" font-weight="600" fill="{SEASON_INK[top]}">{LABELS[top]}</text>')
        for i, wd in enumerate("MTWTFSS"):
            parts.append(f'<text x="{bx + i * (cell + gap) + cell / 2:.1f}" y="{by + 30}" text-anchor="middle" font-size="9.5" fill="#52514e">{wd}</text>')
        day = first
        while day.month == m:
            r, why = rating_for(day)
            week = (day.day + first.weekday() - 1) // 7
            cx = bx + day.weekday() * (cell + gap)
            cy = by + 36 + week * (cell + gap)
            dim = ' opacity="0.45"' if day < date.today() else ""
            parts.append(f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{cell}" height="{cell}" rx="3" fill="{SEASON_FILL[r]}"{dim}>'
                         f'<title>{day:%a %b %-d, %Y}: {LABELS[r]} — {why}' + (" · two business seats found in the latest scan" if day in found_days else "") + '</title></rect>')
            if day in found_days:
                parts.append(f'<circle cx="{cx + cell / 2:.1f}" cy="{cy + cell / 2:.1f}" r="3" fill="#0b0b0b"></circle>')
            day += timedelta(days=1)
        m += 1
        if m == 13:
            m, y = 1, y + 1
    legend = "".join(f'<div><span class="sw" style="background:{SEASON_FILL[k]};border:1px solid {SEASON_INK[k]}"></span><b>{LABELS[k]}</b> '
                     f'<small>{ {GOOD: "most space, lowest prices", CAUTION: "shoulder or holiday week", AVOID: "peak demand, little saver space"}[k]}</small></div>'
                     for k in (GOOD, CAUTION, AVOID))
    legend += ('<div><span class="sw" style="background:#9fd9b5;border:1px solid #0f6b2f;text-align:center;line-height:10px;font-size:11px;color:#0b0b0b">&#9679;</span>'
               '<b>Dot</b> <small>two business seats found in the latest scan</small></div>')
    return (f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="100%" role="img" aria-label="When to go calendar">{"".join(parts)}</svg>'
            f'<div class="ah-legend">{legend}</div>')

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
