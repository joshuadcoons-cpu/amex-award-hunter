"""Alert generation, deduplication and delivery.

Channels (any combination; configured in ``.env``):

* **macos** - native macOS Notification Center banner via ``osascript``. On by
  default when running on a Mac; needs no setup. Set ``ALERT_MACOS_NOTIFICATIONS=0``
  to turn it off.
* **ntfy** - free push notification to a phone through https://ntfy.sh (or a
  self-hosted server). Set ``NTFY_TOPIC`` to any hard-to-guess name and subscribe
  to the same topic in the ntfy app.
* **webhook** - JSON POST (Discord / Slack / Teams / Home Assistant style).
* **email** - SMTP.

Nothing sensitive is ever included in a message: only route, dates, program,
points, known fees, score and the observation time. Deduplication is keyed by
(fingerprint, kind) in ``alert_history`` so an unchanged award alerts once.
"""
from __future__ import annotations

import json
import logging
import shutil
import smtplib
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import httpx

from .config import Settings
from .models import RoundTrip, ScoredAward
from .transfer_partners import PROGRAMS

log = logging.getLogger(__name__)

DEFAULT_NTFY_SERVER = "https://ntfy.sh"


@dataclass
class AlertThresholds:
    """What counts as 'worth jumping on'. Defaults are tuned for the cash-first scoring preset."""

    new_award_min_score: float = 80.0
    exceptional_min_score: float = 88.0
    improved_min_delta: float = 10.0
    round_trip_max_points_two: int = 320_000
    round_trip_max_fees_two_usd: float | None = 600.0  # total known cash for two passengers, round trip
    round_trip_min_score: float = 78.0
    notify_seats_increase_to: int = 2
    daily_summary: bool = True
    max_alerts_per_run: int = 4  # beyond this, remaining alerts are folded into one "more deals" message


@dataclass
class Alert:
    kind: str  # new_award | exceptional | improved | seats_available | round_trip | summary | test
    fingerprint: str
    title: str
    body: str
    score: float | None
    headline: str = ""  # one line for push notifications; cash first
    click_url: str | None = None
    priority: str = "default"  # ntfy priority: min | low | default | high | urgent
    payload: dict = field(default_factory=dict)


def fmt_pacific(dt: datetime | None, tz: str = "America/Los_Angeles") -> str:
    if dt is None:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M %Z")


def _fmt_date(d) -> str:
    try:
        return d.strftime("%b %-d")
    except Exception:  # noqa: BLE001
        return str(d)


def _booking_url(source: str) -> str | None:
    info = PROGRAMS.get(source)
    return info.booking_url if info else None


def _award_headline(s: ScoredAward) -> str:
    a, best = s.award, s.award.best_option()
    fees = f"${a.taxes_major:,.0f}" if a.fees_known and a.taxes_major is not None and (a.taxes_currency or "USD") == "USD" \
        else (f"{a.taxes_major:,.0f} {a.taxes_currency}" if a.fees_known and a.taxes_major is not None else "fees n/a")
    stops = best.stops if best and best.stops is not None else (0 if a.direct_available else None)
    stop_txt = "nonstop" if stops == 0 else (f"{stops} stop" if stops == 1 else (f"{stops} stops" if stops else "stops ?"))
    pts = f"{s.amex.amex_points_per_passenger // 1000}k pts" if s.amex.amex_points_per_passenger else "pts n/a"
    return f"{a.origin}→{a.destination} {_fmt_date(a.travel_date)} · {fees} cash + {pts} /person · {stop_txt} · {a.program_name}"


def _award_body(s: ScoredAward, tz: str) -> str:
    a, best = s.award, s.award.best_option()
    fees = f"{a.taxes_major:,.2f} {a.taxes_currency}" if a.fees_known and a.taxes_major is not None else "Not provided"
    seats = str(a.remaining_seats) if a.remaining_seats is not None else "not reported"
    stops = best.stops if best and best.stops is not None else ("0" if a.direct_available else "unknown")
    lines = [
        f"Cash out of pocket per person: {fees}",
        f"Amex points per person: {s.amex.amex_points_per_passenger:,}" if s.amex.amex_points_per_passenger else "Amex points: n/a",
        f"Amex points for two: {s.amex.amex_points_two_passengers:,}" if s.amex.amex_points_two_passengers else "",
        "",
        f"Route: {a.origin} -> {a.destination}",
        f"Date: {a.travel_date}",
        f"Program: {a.program_name}",
        "Cabin: Business (lie-flat not verified)",
        f"Miles per person: {a.miles_per_passenger:,}" if a.miles_per_passenger else "Miles per person: unknown",
        f"Seats reported: {seats}",
        f"Stops: {stops}",
        f"Score: {s.score.total:.0f}/100 ({s.score.tier})",
        f"Award last observed: {fmt_pacific(a.source_updated_at_utc, tz)}",
        "Status: Cached availability - requires airline verification",
    ]
    if s.amex.bonus_percent:
        lines.append(f"Assumes {s.amex.bonus_percent:g}% transfer bonus ({'verified' if s.amex.bonus_verified else 'UNVERIFIED'})")
    return "\n".join(line for line in lines if line is not None).replace("\n\n\n", "\n\n")


def _round_trip_headline(t: RoundTrip) -> str:
    o, i = t.outbound.award, t.inbound.award
    cash = (f"${t.total_fees_two_major:,.0f}" + ("" if t.fees_complete else "+")) if t.total_fees_two_major is not None else "cash n/a"
    pts = f"{t.total_amex_points_two // 1000}k pts" if t.total_amex_points_two else "pts n/a"
    bo, bi = o.best_option(), i.best_option()
    so = bo.stops if bo and bo.stops is not None else (0 if o.direct_available else None)
    si = bi.stops if bi and bi.stops is not None else (0 if i.direct_available else None)
    stops = "nonstop both ways" if so == 0 and si == 0 else "with connections"
    return (f"{t.destination_city} {_fmt_date(o.travel_date)}–{_fmt_date(i.travel_date)} ({t.trip_days}d) · "
            f"{cash} cash + {pts} for 2 · {stops}")


def _round_trip_body(t: RoundTrip, tz: str) -> str:
    o, i = t.outbound.award, t.inbound.award
    fees = (f"${t.total_fees_two_major:,.0f} total for two" + ("" if t.fees_complete else " (partial - some fees missing)")) \
        if t.total_fees_two_major is not None else "Not provided"
    lines = [
        f"Cash out of pocket: {fees}",
        f"Amex points: {t.total_amex_points_two:,} total for two" if t.total_amex_points_two else "Amex points: n/a",
        "",
        f"Destination: {t.destination_city}, {t.destination_country}",
        f"Outbound: {o.origin} -> {o.destination}  {o.travel_date}  ({t.outbound.amex.program_name}, {o.airlines or 'airline n/a'})",
        f"Return:   {i.origin} -> {i.destination}  {i.travel_date}  ({t.inbound.amex.program_name}, {i.airlines or 'airline n/a'})",
        f"Trip length: {t.trip_days} days",
        "Passengers: 2",
        "Cabin: Business",
        f"Score: {t.score.total:.0f}/100",
        "",
        f"Outbound last observed: {fmt_pacific(o.source_updated_at_utc, tz)}",
        f"Return last observed:   {fmt_pacific(i.source_updated_at_utc, tz)}",
        "Status: Requires airline verification",
    ]
    if t.warnings:
        lines += ["", "Notes:"] + [f" - {w}" for w in t.warnings]
    return "\n".join(lines)


def build_alerts(qualifying: list[ScoredAward], changes_by_id: dict[str, list[dict]], round_trips: list[RoundTrip],
                 new_round_trip_fps: set[str], thresholds: AlertThresholds, tz: str = "America/Los_Angeles") -> list[Alert]:
    alerts: list[Alert] = []
    for s in qualifying:
        a = s.award
        changes = changes_by_id.get(a.availability_id, [])
        kinds = {c["change_type"] for c in changes}
        url = _booking_url(a.source)
        if s.score.total >= thresholds.exceptional_min_score and ("new" in kinds or "score_improved" in kinds or "price_drop" in kinds):
            alerts.append(Alert("exceptional", a.availability_id, "Exceptional award - jump on it", _award_body(s, tz), s.score.total,
                                headline=_award_headline(s), click_url=url, priority="high"))
        elif "new" in kinds and s.score.total >= thresholds.new_award_min_score:
            alerts.append(Alert("new_award", a.availability_id, "New excellent award", _award_body(s, tz), s.score.total,
                                headline=_award_headline(s), click_url=url))
        elif ("price_drop" in kinds or "score_improved" in kinds) and s.score.total >= thresholds.new_award_min_score:
            summary = "; ".join(c["summary"] for c in changes if c["change_type"] in ("price_drop", "score_improved"))
            alerts.append(Alert("improved", f"{a.availability_id}|{s.amex.amex_points_per_passenger}", "Award improved",
                                summary + "\n\n" + _award_body(s, tz), s.score.total, headline=_award_headline(s), click_url=url))
        if "seats_increased" in kinds and (a.remaining_seats or 0) >= thresholds.notify_seats_increase_to and s.score.total >= thresholds.new_award_min_score - 10:
            alerts.append(Alert("seats_available", f"{a.availability_id}|seats{a.remaining_seats}", "Two seats now reported",
                                _award_body(s, tz), s.score.total, headline=_award_headline(s), click_url=url))
    for t in round_trips:
        if t.fingerprint not in new_round_trip_fps:
            continue
        if t.total_amex_points_two is None or t.total_amex_points_two > thresholds.round_trip_max_points_two:
            continue
        if t.score.total < thresholds.round_trip_min_score:
            continue
        if thresholds.round_trip_max_fees_two_usd is not None:
            # Cash cap applies only when every leg reported fees; a partial total could hide a big surcharge.
            if not t.fees_complete or t.total_fees_two_major is None or t.total_fees_two_major > thresholds.round_trip_max_fees_two_usd:
                continue
        alerts.append(Alert("round_trip", t.fingerprint, "Complete round trip for two", _round_trip_body(t, tz), t.score.total,
                            headline=_round_trip_headline(t), click_url=_booking_url(t.outbound.award.source),
                            priority="high" if t.score.total >= thresholds.exceptional_min_score else "default"))
    return alerts


_PRIORITY_RANK = {"urgent": 0, "high": 1, "default": 2, "low": 3, "min": 4}


def cap_alerts(alerts: list[Alert], max_individual: int) -> tuple[list[Alert], list[Alert]]:
    """Keep the strongest ``max_individual`` alerts as individual notifications; fold the rest into one digest.

    Returns (alerts_to_send, folded). Every folded alert is still recorded as delivered under the
    digest so it does not re-alert on the next scan.
    """
    if max_individual <= 0 or len(alerts) <= max_individual:
        return list(alerts), []
    ordered = sorted(alerts, key=lambda a: (_PRIORITY_RANK.get(a.priority, 2), -(a.score or 0)))
    keep, rest = ordered[:max_individual], ordered[max_individual:]
    lines = [a.headline or a.title for a in rest[:12]]
    if len(rest) > 12:
        lines.append(f"... and {len(rest) - 12} more")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    digest = Alert("more_deals", f"more|{stamp}", f"{len(rest)} more deals worth a look",
                   "Open the dashboard (Best Deals) to see them all.\n\n" + "\n".join(f"- {ln}" for ln in lines), None,
                   headline=f"{len(rest)} more trips/awards passed your thresholds", priority="default")
    keep.append(digest)
    return keep, rest


def build_bonus_alerts(added, tz: str = "America/Los_Angeles") -> list[Alert]:
    """One alert per newly detected Amex transfer bonus for a program this app searches."""
    out: list[Alert] = []
    for d in added:
        info = PROGRAMS.get(d.source or "")
        name = info.program_name if info else d.partner_text
        ends = d.expiration_date.strftime("%b %-d") if d.expiration_date else "unknown date"
        body = "\n".join([
            f"Amex is offering +{d.bonus_percent:g}% when transferring Membership Rewards to {name}.",
            f"Runs through {ends}." if d.expiration_date else "End date not listed.",
            "Bonuses are often targeted: open your Amex account, check the transfer page, then press",
            "\"It's in my account\" on the dashboard so the points math uses it.",
            f"Source: {d.source_url}",
        ])
        out.append(Alert("bonus", f"bonus|{d.source}|{d.bonus_percent:g}|{d.expiration_date}", "Amex transfer bonus spotted", body, None,
                         headline=f"+{d.bonus_percent:g}% Amex → {name} through {ends}", click_url=d.source_url, priority="high"))
    return out


def build_summary_alert(*, run_day: str, qualifying: int, round_trips: int, new_awards: int, best_trip: RoundTrip | None,
                        calls_made: int, errors: int) -> Alert:
    """One short 'scan finished' notification per day so the owner knows the scheduler is alive."""
    if best_trip is not None:
        best = "Best trip: " + _round_trip_headline(best_trip)
    else:
        best = "No complete round trip matched your filters."
    headline = f"{qualifying} awards · {round_trips} trips · {new_awards} new" + (f" · {errors} errors" if errors else "")
    body = "\n".join([
        f"Scan finished ({calls_made} API calls).",
        f"Qualifying awards: {qualifying}",
        f"Complete round trips: {round_trips}",
        f"New since last scan: {new_awards}",
        best,
        "Open the dashboard for details.",
    ])
    return Alert("summary", f"summary|{run_day}", "Daily scan complete", body, None, headline=headline, priority="low")


def build_test_alert() -> Alert:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return Alert("test", f"test|{stamp}", "Amex Award Hunter test alert",
                 "If you can read this, alerts are working.\nReal alerts will lead with cash out of pocket and Amex points.",
                 None, headline="Test: Rome Apr 8–18 (10d) · $340 cash + 300k pts for 2 · nonstop both ways", priority="default")


def _osascript_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class AlertSender:
    def __init__(self, settings: Settings, db, *, transport: httpx.BaseTransport | None = None, smtp_factory=None,
                 platform: str | None = None, run_command=None) -> None:
        self.settings = settings
        self.db = db
        self._transport = transport
        self._smtp_factory = smtp_factory or smtplib.SMTP
        self._platform = platform or sys.platform
        self._run_command = run_command or subprocess.run

    @property
    def macos_available(self) -> bool:
        return self._platform == "darwin" and (self._run_command is not subprocess.run or shutil.which("osascript") is not None)

    @property
    def channels(self) -> list[str]:
        ch = []
        if self.settings.macos_notifications and self.macos_available:
            ch.append("macos")
        if self.settings.ntfy_topic:
            ch.append("ntfy")
        if self.settings.alert_webhook_url:
            ch.append("webhook")
        if self.settings.alert_email_to and self.settings.smtp_host:
            ch.append("email")
        return ch

    def describe_channels(self) -> list[tuple[str, str]]:
        """(channel, human description) for the Settings page."""
        out = []
        if self.settings.macos_notifications:
            out.append(("macos", "Mac notification banners" + ("" if self.macos_available else " (only when running on a Mac)")))
        if self.settings.ntfy_topic:
            out.append(("ntfy", f"Phone push via ntfy topic '{self.settings.ntfy_topic}'"))
        if self.settings.alert_webhook_url:
            out.append(("webhook", "Webhook"))
        if self.settings.alert_email_to and self.settings.smtp_host:
            out.append(("email", f"Email to {self.settings.alert_email_to}"))
        return out

    def send_all(self, alerts: list[Alert], *, dry_run: bool = False, max_individual: int | None = None) -> dict[str, int]:
        stats = {"sent": 0, "deduped": 0, "failed": 0, "no_channel": 0, "folded": 0}
        channels = self.channels
        # Drop anything already delivered before deciding what to fold, so a re-run does not re-digest old deals.
        fresh = [a for a in alerts if not self.db.alert_already_sent(a.fingerprint, a.kind)]
        stats["deduped"] = len(alerts) - len(fresh)
        folded: list[Alert] = []
        if max_individual is not None:
            fresh, folded = cap_alerts(fresh, max_individual)
        for alert in folded:
            stats["folded"] += 1
            self.db.record_alert(alert.fingerprint, alert.kind, "digest", alert.score, {"title": alert.title, "headline": alert.headline}, ok=True)
        alerts = fresh
        for alert in alerts:
            if not channels:
                stats["no_channel"] += 1
                # Record so the dashboard can show "would have alerted" without re-triggering later.
                self.db.record_alert(alert.fingerprint, alert.kind, "none", alert.score,
                                     {"title": alert.title, "headline": alert.headline}, ok=True, error="no channel configured")
                continue
            for channel in channels:
                ok, err = (True, None) if dry_run else self._deliver(channel, alert)
                self.db.record_alert(alert.fingerprint, alert.kind, channel, alert.score,
                                     {"title": alert.title, "headline": alert.headline, "body": alert.body}, ok=ok, error=err)
                stats["sent" if ok else "failed"] += 1
        return stats

    def send_test(self) -> dict[str, tuple[bool, str | None]]:
        """Deliver a test alert to every configured channel; returns per-channel (ok, error)."""
        alert = build_test_alert()
        results: dict[str, tuple[bool, str | None]] = {}
        for channel in self.channels:
            ok, err = self._deliver(channel, alert)
            self.db.record_alert(alert.fingerprint, alert.kind, channel, None, {"title": alert.title, "headline": alert.headline}, ok=ok, error=err)
            results[channel] = (ok, err)
        return results

    # -- delivery ------------------------------------------------------------
    def _deliver(self, channel: str, alert: Alert) -> tuple[bool, str | None]:
        try:
            if channel == "macos":
                return self._deliver_macos(alert)
            if channel == "ntfy":
                return self._deliver_ntfy(alert)
            if channel == "webhook":
                payload = {"title": alert.title, "text": f"{alert.title}\n{alert.headline}\n\n{alert.body}",
                           "content": f"**{alert.title}**\n{alert.headline}\n{alert.body}", "kind": alert.kind, "score": alert.score}
                with httpx.Client(timeout=15, transport=self._transport) as c:
                    r = c.post(self.settings.alert_webhook_url, json=payload)  # type: ignore[arg-type]
                    r.raise_for_status()
                return True, None
            if channel == "email":
                msg = EmailMessage()
                msg["Subject"] = f"Amex Award Hunter: {alert.title}" + (f" - {alert.headline}" if alert.headline else "")
                msg["From"] = self.settings.smtp_from or self.settings.smtp_username or "award-hunter@localhost"
                msg["To"] = self.settings.alert_email_to
                msg.set_content((alert.headline + "\n\n" if alert.headline else "") + alert.body)
                with self._smtp_factory(self.settings.smtp_host, self.settings.smtp_port, timeout=20) as smtp:
                    try:
                        smtp.starttls()
                    except smtplib.SMTPException:
                        pass
                    if self.settings.smtp_username and self.settings.smtp_password:
                        smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                    smtp.send_message(msg)
                return True, None
            return False, f"unknown channel {channel}"
        except Exception as exc:  # noqa: BLE001
            log.warning("Alert delivery via %s failed: %s", channel, type(exc).__name__)
            return False, f"{type(exc).__name__}: {exc}"[:300]

    def _deliver_macos(self, alert: Alert) -> tuple[bool, str | None]:
        title = _osascript_escape("Amex Award Hunter")
        subtitle = _osascript_escape(alert.title)
        text = _osascript_escape(alert.headline or alert.body.splitlines()[0])
        sound = ' sound name "Glass"' if alert.kind != "summary" else ""
        script = f'display notification "{text}" with title "{title}" subtitle "{subtitle}"{sound}'
        proc = self._run_command(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return False, (proc.stderr or "osascript failed").strip()[:300]
        return True, None

    def _deliver_ntfy(self, alert: Alert) -> tuple[bool, str | None]:
        server = (self.settings.ntfy_server or DEFAULT_NTFY_SERVER).rstrip("/")
        url = f"{server}/{self.settings.ntfy_topic}"
        headers = {
            "Title": f"{alert.title}",
            "Priority": alert.priority,
            "Tags": "airplane" if alert.kind != "summary" else "white_check_mark",
        }
        if alert.click_url:
            headers["Click"] = alert.click_url
        body = (alert.headline + "\n\n" if alert.headline else "") + alert.body
        with httpx.Client(timeout=15, transport=self._transport) as c:
            r = c.post(url, content=body.encode("utf-8"), headers=headers)
            r.raise_for_status()
        return True, None


def alerts_to_json(alerts: list[Alert]) -> str:
    return json.dumps([{"kind": a.kind, "title": a.title, "headline": a.headline, "score": a.score, "body": a.body} for a in alerts], indent=2)
