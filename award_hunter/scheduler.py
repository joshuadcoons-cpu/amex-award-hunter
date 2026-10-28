"""Daily scan orchestration: search -> persist -> detect changes -> match trips -> alert.

Used by ``scripts/run_daily_scan.py`` (Windows Task Scheduler) and by the
dashboard's "Run Search" button so both paths behave identically.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from .alerts import Alert, AlertSender, AlertThresholds, build_alerts, build_summary_alert
from .api_client import DailyBudget, SeatsAeroClient
from .config import Settings
from .database import Database
from .models import RoundTrip
from .scoring import ScoringConfig, ScoringWeights
from .search import INBOUND, OUTBOUND, SearchProfile, SearchResult, direction_of, run_search
from .transfer_bonuses import TransferEngine
from .trip_matcher import match_round_trips

log = logging.getLogger(__name__)

PROFILE_KEY = "search_profile"
WEIGHTS_KEY = "scoring_weights"
THRESHOLDS_KEY = "alert_thresholds"


def load_profile(db: Database) -> SearchProfile:
    raw = db.get_setting(PROFILE_KEY)
    if raw:
        try:
            return SearchProfile.model_validate(raw)
        except Exception:  # noqa: BLE001
            log.warning("Stored search profile invalid; using defaults")
    return SearchProfile()


def save_profile(db: Database, profile: SearchProfile) -> None:
    db.set_setting(PROFILE_KEY, profile.model_dump(mode="json"))


def load_scoring_config(db: Database, passengers: int) -> ScoringConfig:
    """Scoring config = named priority preset, optionally overridden by explicitly saved weights."""
    raw = db.get_setting(WEIGHTS_KEY) or {}
    cfg = ScoringConfig.for_priority(raw.get("priority"), passengers)
    custom = {k: float(v) for k, v in raw.items() if k in ScoringWeights.__dataclass_fields__}
    if custom:
        for k, v in custom.items():
            setattr(cfg.weights, k, v)
    if raw.get("cents_per_point") is not None:
        cfg.cents_per_point = float(raw["cents_per_point"])
    return cfg


def save_scoring_config(db: Database, priority: str, weights: dict | None = None, cents_per_point: float | None = None) -> None:
    payload: dict = {"priority": priority}
    if weights:
        payload.update({k: float(v) for k, v in weights.items() if k in ScoringWeights.__dataclass_fields__})
    if cents_per_point is not None:
        payload["cents_per_point"] = float(cents_per_point)
    db.set_setting(WEIGHTS_KEY, payload)


def load_thresholds(db: Database) -> AlertThresholds:
    raw = db.get_setting(THRESHOLDS_KEY) or {}
    return AlertThresholds(**{k: v for k, v in raw.items() if k in AlertThresholds.__dataclass_fields__})


def make_client(settings: Settings, db: Database, **kwargs) -> SeatsAeroClient:
    class _DbCache:
        def get(self, key: str):
            return db.cache_get(key)

        def set(self, key: str, value: dict, ttl_seconds: int) -> None:
            db.cache_set(key, value, ttl_seconds)

    budget = DailyBudget(settings.api_daily_budget, load=db.budget_load, save=db.budget_save)
    return SeatsAeroClient(settings, cache=_DbCache(), budget=budget, on_call=db.log_api_call, **kwargs)


@dataclass
class ScanOutcome:
    result: SearchResult
    round_trips: list[RoundTrip]
    new_awards: int
    changes: dict[str, list[dict]]
    new_round_trips: int
    alerts: list[Alert]
    alert_stats: dict[str, int] = field(default_factory=dict)
    disappeared: int = 0


def run_scan(settings: Settings, db: Database, *, profile: SearchProfile | None = None, trigger: str = "manual",
             send_alerts: bool = True, client: SeatsAeroClient | None = None,
             progress: Callable[[int, int, str], None] | None = None) -> ScanOutcome:
    profile = profile or load_profile(db)
    cfg = load_scoring_config(db, profile.passengers)
    engine = TransferEngine.from_database(db)
    own_client = client is None
    client = client or make_client(settings, db)
    try:
        result = run_search(client, profile, engine, cfg=cfg, db=db, trigger=trigger, progress=progress)
    finally:
        if own_client:
            client.close()

    changes: dict[str, list[dict]] = {}
    new_awards = 0
    for s in result.qualifying:
        db.insert_observation(result.run_id, s)
        ch = db.upsert_current(result.run_id, s, direction_of(s.award), qualifies=True)
        if ch:
            changes[s.award.availability_id] = ch
            if any(c["change_type"] == "new" for c in ch):
                new_awards += 1
    for s in result.non_qualifying:
        # Keep history for near-misses that are Amex partners so price drops can be detected later.
        if s.amex.is_amex_partner and s.amex.amex_points_per_passenger is not None:
            db.insert_observation(result.run_id, s)
            db.upsert_current(result.run_id, s, direction_of(s.award), qualifies=False)

    disappeared = 0
    seen = {s.award.availability_id for s in result.qualifying}
    for direction in (OUTBOUND, INBOUND):
        ok_chunks = [c for c in result.coverage if c.chunk.direction == direction and c.status in ("ok", "empty")]
        for c in ok_chunks:
            disappeared += db.mark_disappeared(result.run_id or 0, seen, direction, c.chunk.start.isoformat(), c.chunk.end.isoformat(),
                                               c.chunk.origins, c.chunk.destinations)

    trips = match_round_trips(result.outbound, result.inbound, profile, cfg)
    new_fps: set[str] = set()
    for t in trips:
        if db.upsert_round_trip(result.run_id, t):
            new_fps.add(t.fingerprint)

    thresholds = load_thresholds(db)
    alerts = build_alerts(result.qualifying, changes, trips, new_fps, thresholds, settings.timezone)
    stats: dict[str, int] = {}
    if send_alerts:
        sender = AlertSender(settings, db)
        stats = sender.send_all(alerts, max_individual=thresholds.max_alerts_per_run)
        if thresholds.daily_summary and trigger == "scheduled":
            from datetime import datetime, timezone as _tz
            summary = build_summary_alert(run_day=datetime.now(_tz.utc).strftime("%Y-%m-%d"), qualifying=len(result.qualifying),
                                          round_trips=len(trips), new_awards=new_awards, best_trip=trips[0] if trips else None,
                                          calls_made=result.calls_made, errors=len(result.errors))
            sender.send_all([summary])

    status = "failed" if result.aborted else ("partial" if result.errors else "ok")
    if status == "ok" and result.run_id is not None:
        pruned = db.prune_round_trips(result.run_id)
        if pruned:
            log.info("Pruned %d round trips no longer matched", pruned)
    db.finish_run(result.run_id, status, calls_made=result.calls_made, records_seen=result.records_seen,
                  qualifying=len(result.qualifying), round_trips=len(trips), new_awards=new_awards, errors=result.errors,
                  summary=f"{len(result.qualifying)} qualifying awards, {len(trips)} round trips, {new_awards} new, "
                          f"{disappeared} disappeared, coverage {result.coverage_summary()}")
    return ScanOutcome(result, trips, new_awards, changes, len(new_fps), alerts, stats, disappeared)
