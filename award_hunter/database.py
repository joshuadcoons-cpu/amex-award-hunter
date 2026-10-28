"""SQLite persistence with a versioned, forward-only migration list.

Design notes
* All timestamps are stored as ISO-8601 UTC strings.
* ``award_observations`` is append-only history; ``awards_current`` holds the
  latest state per Seats.aero availability ID and is what the dashboard reads.
* ``award_changes`` records diffs (new award, price drop, seats increased, gone)
  computed when a scan upserts into ``awards_current``.
* The API key is never written here.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .transfer_partners import PROGRAMS

log = logging.getLogger(__name__)

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS api_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, path TEXT NOT NULL, params_json TEXT,
        status INTEGER, latency_ms REAL, from_cache INTEGER NOT NULL DEFAULT 0, remaining_quota INTEGER, error TEXT);
    CREATE INDEX IF NOT EXISTS ix_api_calls_at ON api_calls(at_utc);
    CREATE TABLE IF NOT EXISTS api_budget (day TEXT PRIMARY KEY, used INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS response_cache (key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, expires_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS search_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
        trigger TEXT NOT NULL DEFAULT 'manual', config_json TEXT, calls_made INTEGER DEFAULT 0,
        records_seen INTEGER DEFAULT 0, qualifying INTEGER DEFAULT 0, round_trips INTEGER DEFAULT 0,
        new_awards INTEGER DEFAULT 0, errors_json TEXT, summary TEXT);
    CREATE TABLE IF NOT EXISTS search_coverage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, direction TEXT NOT NULL,
        origins TEXT NOT NULL, destinations TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        sources TEXT, status TEXT NOT NULL, records INTEGER DEFAULT 0, pages INTEGER DEFAULT 0, error TEXT, at_utc TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES search_runs(id));
    CREATE INDEX IF NOT EXISTS ix_cov_run ON search_coverage(run_id);
    CREATE TABLE IF NOT EXISTS award_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, internal_id TEXT NOT NULL, availability_id TEXT NOT NULL,
        source TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL, travel_date TEXT NOT NULL,
        miles INTEGER, seats INTEGER, seat_confidence TEXT, taxes_minor INTEGER, taxes_currency TEXT, fees_known INTEGER,
        direct INTEGER, airlines TEXT, source_updated_at TEXT, retrieved_at TEXT NOT NULL,
        amex_points_pp INTEGER, score REAL, payload_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_obs_avail ON award_observations(availability_id, retrieved_at);
    CREATE INDEX IF NOT EXISTS ix_obs_date ON award_observations(travel_date);
    CREATE TABLE IF NOT EXISTS awards_current (
        availability_id TEXT PRIMARY KEY, internal_id TEXT NOT NULL, source TEXT NOT NULL, direction TEXT NOT NULL,
        origin TEXT NOT NULL, destination TEXT NOT NULL, travel_date TEXT NOT NULL,
        miles INTEGER, seats INTEGER, seat_confidence TEXT, taxes_minor INTEGER, taxes_currency TEXT, fees_known INTEGER,
        direct INTEGER, airlines TEXT, source_updated_at TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        last_run_id INTEGER, amex_points_pp INTEGER, amex_points_two INTEGER, score REAL, tier TEXT, qualifies INTEGER DEFAULT 1,
        verification_status TEXT DEFAULT 'requires_airline_verification', user_note TEXT, payload_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_cur_date ON awards_current(travel_date);
    CREATE INDEX IF NOT EXISTS ix_cur_score ON awards_current(score);
    CREATE TABLE IF NOT EXISTS award_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, run_id INTEGER, availability_id TEXT NOT NULL,
        change_type TEXT NOT NULL, old_value TEXT, new_value TEXT, score REAL, summary TEXT);
    CREATE INDEX IF NOT EXISTS ix_changes_run ON award_changes(run_id);
    CREATE TABLE IF NOT EXISTS transfer_partners (
        source TEXT PRIMARY KEY, program_name TEXT NOT NULL, airline TEXT, alliance TEXT, is_amex_partner INTEGER NOT NULL,
        amex_points_in INTEGER, miles_out INTEGER, transfer_increment INTEGER, booking_url TEXT, notes TEXT,
        last_verified TEXT, seats_aero_source_active INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS transfer_bonuses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, bonus_percent REAL NOT NULL,
        effective_date TEXT, expiration_date TEXT, source_url TEXT, last_verified TEXT,
        account_eligible INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1, notes TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS round_trips (
        fingerprint TEXT PRIMARY KEY, run_id INTEGER, outbound_id TEXT NOT NULL, inbound_id TEXT NOT NULL,
        out_date TEXT NOT NULL, in_date TEXT NOT NULL, trip_days INTEGER NOT NULL, total_points_two INTEGER,
        total_fees_two REAL, fees_complete INTEGER, score REAL, tier TEXT, destination_country TEXT, destination_city TEXT,
        open_jaw INTEGER, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, payload_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_rt_score ON round_trips(score);
    CREATE TABLE IF NOT EXISTS alert_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL, kind TEXT NOT NULL, sent_at TEXT NOT NULL,
        channel TEXT NOT NULL, score REAL, payload_json TEXT, ok INTEGER NOT NULL DEFAULT 1, error TEXT);
    CREATE INDEX IF NOT EXISTS ix_alert_fp ON alert_history(fingerprint, kind);
    """),
]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL") if str(self.path) != ":memory:" else None
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # -- infra ---------------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def schema_version(self) -> int:
        try:
            row = self.one("SELECT MAX(version) AS v FROM schema_version")
            return int(row["v"] or 0) if row else 0
        except sqlite3.OperationalError:
            return 0

    def migrate(self) -> None:
        current = self.schema_version()
        for version, sql in MIGRATIONS:
            if version <= current:
                continue
            with self.tx() as conn:
                conn.executescript(sql) if False else [conn.execute(stmt) for stmt in _split_sql(sql)]
                conn.execute("INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES (?, ?)", (version, utcnow_iso()))
            log.info("Applied database migration v%d", version)
        self.seed_transfer_partners()

    # -- settings ------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value_json FROM app_settings WHERE key = ?", (key,))
        return json.loads(row["value_json"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (key, json.dumps(value, default=str), utcnow_iso()),
        )

    # -- budget + cache + call log ------------------------------------------
    def budget_load(self, day: str) -> int:
        row = self.one("SELECT used FROM api_budget WHERE day = ?", (day,))
        return int(row["used"]) if row else 0

    def budget_save(self, day: str, used: int) -> None:
        self.execute("INSERT INTO api_budget(day, used) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET used = excluded.used",
                     (day, used))

    def cache_get(self, key: str) -> dict | None:
        row = self.one("SELECT payload_json, expires_at FROM response_cache WHERE key = ?", (key,))
        if not row:
            return None
        if row["expires_at"] < time.time():
            self.execute("DELETE FROM response_cache WHERE key = ?", (key,))
            return None
        return json.loads(row["payload_json"])

    def cache_set(self, key: str, value: dict, ttl_seconds: int) -> None:
        self.execute(
            "INSERT INTO response_cache(key, payload_json, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload_json = excluded.payload_json, expires_at = excluded.expires_at",
            (key, json.dumps(value), time.time() + ttl_seconds),
        )

    def cache_purge(self) -> int:
        cur = self.execute("DELETE FROM response_cache WHERE expires_at < ?", (time.time(),))
        return cur.rowcount

    def log_api_call(self, rec: Any) -> None:
        self.execute(
            "INSERT INTO api_calls(at_utc, path, params_json, status, latency_ms, from_cache, remaining_quota, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rec.at_utc.isoformat(timespec="seconds"), rec.path, json.dumps(rec.params, default=str), rec.status_code,
             rec.latency_ms, 1 if rec.from_cache else 0, rec.remaining_quota, rec.error),
        )

    def api_calls_today(self) -> int:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.one("SELECT COUNT(*) AS n FROM api_calls WHERE at_utc LIKE ? AND from_cache = 0", (f"{day}%",))
        return int(row["n"]) if row else 0

    def last_remaining_quota(self) -> int | None:
        row = self.one("SELECT remaining_quota FROM api_calls WHERE remaining_quota IS NOT NULL ORDER BY id DESC LIMIT 1")
        return int(row["remaining_quota"]) if row else None

    # -- transfer partners / bonuses ----------------------------------------
    def seed_transfer_partners(self) -> None:
        existing = {r["source"] for r in self.query("SELECT source FROM transfer_partners")}
        with self.tx() as conn:
            for src, p in PROGRAMS.items():
                if src in existing:
                    continue
                conn.execute(
                    "INSERT INTO transfer_partners(source, program_name, airline, alliance, is_amex_partner, amex_points_in, "
                    "miles_out, transfer_increment, booking_url, notes, last_verified, seats_aero_source_active, enabled) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (src, p.program_name, p.airline, p.alliance, 1 if p.is_amex_partner else 0, p.amex_points_in, p.miles_out,
                     p.transfer_increment, p.booking_url, p.notes, p.last_verified, 1 if p.seats_aero_source_active else 0),
                )

    def transfer_partners(self) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM transfer_partners ORDER BY is_amex_partner DESC, program_name")]

    def update_transfer_partner(self, source: str, **fields: Any) -> None:
        allowed = {"program_name", "airline", "alliance", "is_amex_partner", "amex_points_in", "miles_out",
                   "transfer_increment", "booking_url", "notes", "last_verified", "seats_aero_source_active", "enabled"}
        cols = [k for k in fields if k in allowed]
        if not cols:
            return
        sets = ", ".join(f"{c} = ?" for c in cols)
        self.execute(f"UPDATE transfer_partners SET {sets} WHERE source = ?", (*[fields[c] for c in cols], source))

    def transfer_bonuses(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM transfer_bonuses" + (" WHERE active = 1" if active_only else "") + " ORDER BY source, expiration_date"
        return [dict(r) for r in self.query(sql)]

    def add_transfer_bonus(self, source: str, bonus_percent: float, effective_date: str | None, expiration_date: str | None,
                           source_url: str | None = None, last_verified: str | None = None, account_eligible: bool = False,
                           verified: bool = False, notes: str | None = None) -> int:
        cur = self.execute(
            "INSERT INTO transfer_bonuses(source, bonus_percent, effective_date, expiration_date, source_url, last_verified, "
            "account_eligible, verified, active, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (source, bonus_percent, effective_date, expiration_date, source_url, last_verified, 1 if account_eligible else 0,
             1 if verified else 0, notes, utcnow_iso()),
        )
        return int(cur.lastrowid)

    def confirm_bonus(self, bonus_id: int, day: str) -> None:
        """Owner saw the bonus in their own Amex account: mark verified + eligible so the engine applies it."""
        self.execute("UPDATE transfer_bonuses SET verified = 1, account_eligible = 1, last_verified = ?, active = 1 WHERE id = ?", (day, bonus_id))

    def set_bonus_active(self, bonus_id: int, active: bool) -> None:
        self.execute("UPDATE transfer_bonuses SET active = ? WHERE id = ?", (1 if active else 0, bonus_id))

    def delete_bonus(self, bonus_id: int) -> None:
        self.execute("DELETE FROM transfer_bonuses WHERE id = ?", (bonus_id,))

    # -- runs / coverage -----------------------------------------------------
    def start_run(self, trigger: str, config: dict) -> int:
        cur = self.execute("INSERT INTO search_runs(started_at, status, trigger, config_json) VALUES (?, 'running', ?, ?)",
                           (utcnow_iso(), trigger, json.dumps(config, default=str)))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, **stats: Any) -> None:
        cols = ["finished_at", "status"]
        vals: list[Any] = [utcnow_iso(), status]
        for k in ("calls_made", "records_seen", "qualifying", "round_trips", "new_awards", "summary"):
            if k in stats:
                cols.append(k)
                vals.append(stats[k])
        if "errors" in stats:
            cols.append("errors_json")
            vals.append(json.dumps(stats["errors"], default=str))
        sets = ", ".join(f"{c} = ?" for c in cols)
        self.execute(f"UPDATE search_runs SET {sets} WHERE id = ?", (*vals, run_id))

    def last_run(self, status: str | None = None) -> dict | None:
        sql = "SELECT * FROM search_runs" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC LIMIT 1"
        row = self.one(sql, (status,) if status else ())
        return dict(row) if row else None

    def runs(self, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM search_runs ORDER BY id DESC LIMIT ?", (limit,))]

    def record_coverage(self, run_id: int, direction: str, origins: list[str], destinations: list[str], start: str, end: str,
                        sources: list[str] | None, status: str, records: int = 0, pages: int = 0, error: str | None = None) -> None:
        self.execute(
            "INSERT INTO search_coverage(run_id, direction, origins, destinations, start_date, end_date, sources, status, records, "
            "pages, error, at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, direction, ",".join(origins), ",".join(destinations), start, end, ",".join(sources or []), status, records,
             pages, error, utcnow_iso()),
        )

    def coverage(self, run_id: int) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM search_coverage WHERE run_id = ? ORDER BY id", (run_id,))]

    # -- awards --------------------------------------------------------------
    def insert_observation(self, run_id: int | None, scored: Any) -> None:
        a = scored.award
        self.execute(
            "INSERT INTO award_observations(run_id, internal_id, availability_id, source, origin, destination, travel_date, miles, "
            "seats, seat_confidence, taxes_minor, taxes_currency, fees_known, direct, airlines, source_updated_at, retrieved_at, "
            "amex_points_pp, score, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, a.internal_id, a.availability_id, a.source, a.origin, a.destination, a.travel_date.isoformat(),
             a.miles_per_passenger, a.remaining_seats, a.seat_confidence.value, a.taxes_minor_units, a.taxes_currency,
             1 if a.fees_known else 0, None if a.direct_available is None else int(a.direct_available), a.airlines,
             a.source_updated_at_utc.isoformat() if a.source_updated_at_utc else None, a.retrieved_at_utc.isoformat(),
             scored.amex.amex_points_per_passenger, scored.score.total, scored.model_dump_json()),
        )

    def upsert_current(self, run_id: int | None, scored: Any, direction: str, qualifies: bool = True) -> list[dict]:
        """Insert/update awards_current and return a list of detected changes."""
        a = scored.award
        now = utcnow_iso()
        prev = self.one("SELECT * FROM awards_current WHERE availability_id = ?", (a.availability_id,))
        changes: list[dict] = []
        new_points = scored.amex.amex_points_per_passenger
        if prev is None:
            changes.append({"change_type": "new", "old_value": None, "new_value": new_points,
                            "summary": f"New award {a.origin}->{a.destination} {a.travel_date} via {a.program_name}"})
        else:
            if prev["amex_points_pp"] and new_points and new_points < prev["amex_points_pp"]:
                changes.append({"change_type": "price_drop", "old_value": prev["amex_points_pp"], "new_value": new_points,
                                "summary": f"Price drop {prev['amex_points_pp']:,} -> {new_points:,} Amex pts/person"})
            elif prev["amex_points_pp"] and new_points and new_points > prev["amex_points_pp"]:
                changes.append({"change_type": "price_increase", "old_value": prev["amex_points_pp"], "new_value": new_points,
                                "summary": f"Price increase {prev['amex_points_pp']:,} -> {new_points:,} Amex pts/person"})
            if (a.remaining_seats or 0) > (prev["seats"] or 0):
                changes.append({"change_type": "seats_increased", "old_value": prev["seats"], "new_value": a.remaining_seats,
                                "summary": f"Reported seats {prev['seats'] or 'n/a'} -> {a.remaining_seats}"})
            if scored.score.total >= (prev["score"] or 0) + 10:
                changes.append({"change_type": "score_improved", "old_value": prev["score"], "new_value": scored.score.total,
                                "summary": f"Score improved {prev['score']:.0f} -> {scored.score.total:.0f}"})
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO awards_current(availability_id, internal_id, source, direction, origin, destination, travel_date, miles, "
                "seats, seat_confidence, taxes_minor, taxes_currency, fees_known, direct, airlines, source_updated_at, first_seen_at, "
                "last_seen_at, last_run_id, amex_points_pp, amex_points_two, score, tier, qualifies, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(availability_id) DO UPDATE SET internal_id=excluded.internal_id, source=excluded.source, "
                "direction=excluded.direction, origin=excluded.origin, destination=excluded.destination, travel_date=excluded.travel_date, "
                "miles=excluded.miles, seats=excluded.seats, seat_confidence=excluded.seat_confidence, taxes_minor=excluded.taxes_minor, "
                "taxes_currency=excluded.taxes_currency, fees_known=excluded.fees_known, direct=excluded.direct, airlines=excluded.airlines, "
                "source_updated_at=excluded.source_updated_at, last_seen_at=excluded.last_seen_at, last_run_id=excluded.last_run_id, "
                "amex_points_pp=excluded.amex_points_pp, amex_points_two=excluded.amex_points_two, score=excluded.score, "
                "tier=excluded.tier, qualifies=excluded.qualifies, payload_json=excluded.payload_json",
                (a.availability_id, a.internal_id, a.source, direction, a.origin, a.destination, a.travel_date.isoformat(),
                 a.miles_per_passenger, a.remaining_seats, a.seat_confidence.value, a.taxes_minor_units, a.taxes_currency,
                 1 if a.fees_known else 0, None if a.direct_available is None else int(a.direct_available), a.airlines,
                 a.source_updated_at_utc.isoformat() if a.source_updated_at_utc else None, now, now, run_id,
                 new_points, scored.amex.amex_points_two_passengers, scored.score.total, scored.score.tier, 1 if qualifies else 0,
                 scored.model_dump_json()),
            )
            for ch in changes:
                conn.execute(
                    "INSERT INTO award_changes(at_utc, run_id, availability_id, change_type, old_value, new_value, score, summary) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, run_id, a.availability_id, ch["change_type"], json.dumps(ch["old_value"]), json.dumps(ch["new_value"]),
                     scored.score.total, ch["summary"]),
                )
        return changes

    def mark_disappeared(self, run_id: int, seen_ids: set[str], direction: str, start: str, end: str,
                         origins: list[str], destinations: list[str]) -> int:
        """Flag current awards inside the searched scope that were not seen in this run."""
        if not origins or not destinations:
            return 0
        rows = self.query(
            f"SELECT availability_id, qualifies FROM awards_current WHERE direction = ? AND travel_date BETWEEN ? AND ? AND qualifies = 1 "
            f"AND origin IN ({','.join('?' * len(origins))}) AND destination IN ({','.join('?' * len(destinations))})",
            (direction, start, end, *origins, *destinations),
        )
        gone = [r["availability_id"] for r in rows if r["availability_id"] not in seen_ids]
        now = utcnow_iso()
        with self.tx() as conn:
            for aid in gone:
                conn.execute("UPDATE awards_current SET qualifies = 0 WHERE availability_id = ?", (aid,))
                conn.execute("INSERT INTO award_changes(at_utc, run_id, availability_id, change_type, summary) VALUES (?, ?, ?, 'disappeared', ?)",
                             (now, run_id, aid, "Award no longer reported in searched scope"))
        return len(gone)

    def current_awards(self, *, qualifying_only: bool = True, min_score: float | None = None, direction: str | None = None,
                       limit: int | None = None) -> list[dict]:
        clauses, params = [], []
        if qualifying_only:
            clauses.append("qualifies = 1")
        if min_score is not None:
            clauses.append("score >= ?")
            params.append(min_score)
        if direction:
            clauses.append("direction = ?")
            params.append(direction)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM awards_current{where} ORDER BY score DESC, amex_points_pp ASC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.query(sql, tuple(params))]

    def award_history(self, availability_id: str) -> list[dict]:
        return [dict(r) for r in self.query(
            "SELECT retrieved_at, miles, seats, taxes_minor, amex_points_pp, score FROM award_observations WHERE availability_id = ? "
            "ORDER BY retrieved_at", (availability_id,))]

    def set_verification(self, availability_id: str, status: str, note: str | None = None) -> None:
        self.execute("UPDATE awards_current SET verification_status = ?, user_note = COALESCE(?, user_note) WHERE availability_id = ?",
                     (status, note, availability_id))

    def changes_for_run(self, run_id: int) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM award_changes WHERE run_id = ? ORDER BY id", (run_id,))]

    def recent_changes(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM award_changes ORDER BY id DESC LIMIT ?", (limit,))]

    # -- round trips ---------------------------------------------------------
    def upsert_round_trip(self, run_id: int | None, rt: Any) -> bool:
        """Returns True when the round trip is new."""
        now = utcnow_iso()
        prev = self.one("SELECT fingerprint FROM round_trips WHERE fingerprint = ?", (rt.fingerprint,))
        self.execute(
            "INSERT INTO round_trips(fingerprint, run_id, outbound_id, inbound_id, out_date, in_date, trip_days, total_points_two, "
            "total_fees_two, fees_complete, score, tier, destination_country, destination_city, open_jaw, first_seen_at, last_seen_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(fingerprint) DO UPDATE SET run_id=excluded.run_id, trip_days=excluded.trip_days, total_points_two=excluded.total_points_two, "
            "total_fees_two=excluded.total_fees_two, fees_complete=excluded.fees_complete, score=excluded.score, tier=excluded.tier, "
            "last_seen_at=excluded.last_seen_at, payload_json=excluded.payload_json",
            (rt.fingerprint, run_id, rt.outbound.award.availability_id, rt.inbound.award.availability_id,
             rt.outbound.award.travel_date.isoformat(), rt.inbound.award.travel_date.isoformat(), rt.trip_days,
             rt.total_amex_points_two, rt.total_fees_two_major, 1 if rt.fees_complete else 0, rt.score.total, rt.score.tier,
             rt.destination_country, rt.destination_city, 1 if (rt.open_jaw_europe or rt.open_jaw_us) else 0, now, now,
             rt.model_dump_json()),
        )
        return prev is None

    def prune_round_trips(self, keep_run_id: int | None) -> int:
        """Delete round trips not refreshed by the given run (call only after a clean, complete run)."""
        if keep_run_id is None:
            return 0
        cur = self.execute("DELETE FROM round_trips WHERE run_id IS NULL OR run_id != ?", (keep_run_id,))
        return cur.rowcount if cur.rowcount is not None else 0

    def clear_round_trips(self) -> None:
        self.execute("DELETE FROM round_trips")

    def round_trips(self, min_score: float | None = None, limit: int = 200) -> list[dict]:
        where = " WHERE score >= ?" if min_score is not None else ""
        params: tuple = (min_score, limit) if min_score is not None else (limit,)
        return [dict(r) for r in self.query(f"SELECT * FROM round_trips{where} ORDER BY score DESC, total_points_two ASC LIMIT ?", params)]

    # -- alerts --------------------------------------------------------------
    def alert_already_sent(self, fingerprint: str, kind: str) -> bool:
        return self.one("SELECT 1 FROM alert_history WHERE fingerprint = ? AND kind = ? AND ok = 1", (fingerprint, kind)) is not None

    def record_alert(self, fingerprint: str, kind: str, channel: str, score: float | None, payload: dict, ok: bool, error: str | None = None) -> None:
        self.execute("INSERT INTO alert_history(fingerprint, kind, sent_at, channel, score, payload_json, ok, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (fingerprint, kind, utcnow_iso(), channel, score, json.dumps(payload, default=str), 1 if ok else 0, error))

    def alerts(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM alert_history ORDER BY id DESC LIMIT ?", (limit,))]


def _split_sql(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]
