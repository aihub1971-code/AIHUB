"""Durable SQLite records, atomic quotas and schedule occurrence claims.

Amounts are integer millionths of a USD, rounded upward on reservation. Every
accepted run counts, including failures. This ledger estimates exposure; it is
not a provider invoice. Deploy one service with one persistent local disk.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.config import PROJECT_ROOT, get_setting

DUBAI = ZoneInfo("Asia/Dubai")
MAX_DAILY_REPORTS = 50
MISSED_OCCURRENCE_GRACE_SECONDS = 300
STALE_EXECUTION_SECONDS = 900
_MICROS = Decimal("1000000")
_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_ERROR = re.compile(r"^[a-z0-9_]{1,80}$")


class StoreError(Exception):
    """A safe error suitable for an authenticated API response."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware datetime is required")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _aware(value).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _micros(value: Any) -> int:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return int((amount * _MICROS).to_integral_value(rounding=ROUND_CEILING))
    except (ValueError, InvalidOperation, TypeError, OverflowError):
        raise StoreError("invalid_cost_configuration", "Costs must be finite nonnegative USD amounts.", 503) from None


def _usd(value: int) -> float:
    return float(Decimal(value) / _MICROS)


@dataclass(frozen=True)
class QuotaLimits:
    max_reports_per_day: int = 50
    daily_budget_usd: Any = "5"
    monthly_budget_usd: Any = "100"

    def __post_init__(self) -> None:
        if isinstance(self.max_reports_per_day, bool) or not isinstance(self.max_reports_per_day, int):
            raise StoreError("invalid_quota_configuration", "MAX_REPORTS_PER_DAY must be an integer from 1 to 50.", 503)
        if not 1 <= self.max_reports_per_day <= MAX_DAILY_REPORTS:
            raise StoreError("invalid_quota_configuration", "MAX_REPORTS_PER_DAY must be between 1 and 50.", 503)
        _micros(self.daily_budget_usd)
        _micros(self.monthly_budget_usd)

    @classmethod
    def from_environment(cls) -> "QuotaLimits":
        try:
            count = int(get_setting("MAX_REPORTS_PER_DAY", "50") or "50")
        except ValueError:
            raise StoreError("invalid_quota_configuration", "MAX_REPORTS_PER_DAY must be an integer from 1 to 50.", 503) from None
        return cls(
            max_reports_per_day=count,
            daily_budget_usd=get_setting("DAILY_BUDGET_USD", "5"),
            monthly_budget_usd=get_setting("MONTHLY_BUDGET_USD", "100"),
        )


@dataclass(frozen=True)
class Reservation:
    created: bool
    report: dict[str, Any]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    report_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed','interrupted')),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    local_date TEXT NOT NULL,
    local_month TEXT NOT NULL,
    reserved_microusd INTEGER NOT NULL CHECK(reserved_microusd >= 0),
    accounted_microusd INTEGER NOT NULL CHECK(accounted_microusd >= 0),
    cost_basis TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    pricing_snapshot_json TEXT NOT NULL,
    usage_json TEXT,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS runs_local_date_idx ON runs(local_date);
CREATE INDEX IF NOT EXISTS runs_local_month_idx ON runs(local_month);
CREATE INDEX IF NOT EXISTS runs_created_at_idx ON runs(created_at DESC);
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task TEXT NOT NULL,
    times_json TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_occurrences (
    occurrence_key TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    due_at TEXT NOT NULL,
    local_time TEXT NOT NULL,
    status TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    finished_at TEXT,
    report_id TEXT,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS occurrences_schedule_idx ON schedule_occurrences(schedule_id, due_at DESC);
"""


class Store:
    def __init__(
        self,
        path: str | Path,
        limits: QuotaLimits | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self._limits = limits
        self.clock = clock

    @property
    def limits(self) -> QuotaLimits:
        return self._limits or QuotaLimits.from_environment()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)

    def _now(self, now: datetime | None = None) -> datetime:
        return _aware(now if now is not None else self.clock())

    @staticmethod
    def _fingerprint(agent_id: str, task: str) -> str:
        return hashlib.sha256(_json([agent_id, task]).encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_key(key: str | None) -> None:
        if key is not None and (
            not isinstance(key, str) or not key.strip() or len(key) > 200
            or any(ord(char) < 32 or ord(char) == 127 for char in key)
        ):
            raise StoreError("invalid_idempotency_key", "Idempotency-Key must contain 1 to 200 printable characters.")

    @staticmethod
    def _report(row: sqlite3.Row, include_result: bool = True) -> dict[str, Any]:
        item = dict(row)
        item["reserved_cost_usd"] = _usd(item.pop("reserved_microusd"))
        item["estimated_cost_usd"] = _usd(item.pop("accounted_microusd"))
        item["cost_is_estimate"] = True
        item["pricing_snapshot"] = json.loads(item.pop("pricing_snapshot_json"))
        item["usage"] = json.loads(item.pop("usage_json") or "{}")
        result_json = item.pop("result_json")
        if include_result:
            item["result"] = json.loads(result_json) if result_json else None
        item.pop("request_hash", None)
        item.pop("idempotency_key", None)
        return item

    def lookup_idempotency(self, key: str | None, agent_id: str, task: str) -> dict[str, Any] | None:
        self._validate_key(key)
        if key is None:
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != self._fingerprint(agent_id, task):
            raise StoreError("idempotency_conflict", "This Idempotency-Key belongs to a different task.", 409)
        return self._report(row)

    def reserve_run(
        self,
        agent_id: str,
        task: str,
        reservation_usd: Any,
        *,
        idempotency_key: str | None = None,
        trigger: str = "manual",
        provider: str = "",
        model: str = "",
        pricing_snapshot: dict[str, Any] | None = None,
        allow_zero_cost: bool = False,
        now: datetime | None = None,
    ) -> Reservation:
        self._validate_key(idempotency_key)
        if trigger not in {"manual", "scheduled"}:
            raise StoreError("invalid_trigger", "Trigger must be manual or scheduled.")
        reservation = _micros(reservation_usd)
        if reservation <= 0 and not allow_zero_cost:
            raise StoreError("invalid_cost_configuration", "A positive upfront cost reservation is required.", 503)
        limits = self.limits
        daily_budget = _micros(limits.daily_budget_usd)
        monthly_budget = _micros(limits.monthly_budget_usd)
        moment = self._now(now)
        timestamp = _stamp(moment)
        local_date = moment.astimezone(DUBAI).date().isoformat()
        local_month = local_date[:7]
        request_hash = self._fingerprint(agent_id, task)
        with self._transaction() as connection:
            if idempotency_key is not None:
                row = connection.execute("SELECT * FROM runs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if row is not None:
                    if row["request_hash"] != request_hash:
                        raise StoreError("idempotency_conflict", "This Idempotency-Key belongs to a different task.", 409)
                    return Reservation(False, self._report(row))
            day = connection.execute(
                "SELECT COUNT(*) AS attempts, COALESCE(SUM(accounted_microusd),0) AS cost FROM runs WHERE local_date=?",
                (local_date,),
            ).fetchone()
            month_cost = connection.execute(
                "SELECT COALESCE(SUM(accounted_microusd),0) FROM runs WHERE local_month=?", (local_month,),
            ).fetchone()[0]
            if day["attempts"] >= limits.max_reports_per_day:
                raise StoreError("daily_report_limit", "The daily report attempt limit has been reached (Asia/Dubai).", 429)
            if day["cost"] + reservation > daily_budget:
                raise StoreError("daily_budget_limit", "The daily estimated USD budget cannot cover this reservation.", 429)
            if month_cost + reservation > monthly_budget:
                raise StoreError("monthly_budget_limit", "The monthly estimated USD budget cannot cover this reservation.", 429)
            report_id = "rpt_" + uuid4().hex
            connection.execute(
                """INSERT INTO runs (report_id,idempotency_key,request_hash,agent_id,task,trigger,status,
                created_at,updated_at,local_date,local_month,reserved_microusd,accounted_microusd,
                cost_basis,provider,model,pricing_snapshot_json) VALUES (?,?,?,?,?,?,'pending',?,?,?,?,?,?,'reservation',?,?,?)""",
                (report_id, idempotency_key, request_hash, agent_id, task, trigger, timestamp,
                 timestamp, local_date, local_month, reservation, reservation, provider, model,
                 _json(pricing_snapshot or {})),
            )
            row = connection.execute("SELECT * FROM runs WHERE report_id=?", (report_id,)).fetchone()
            return Reservation(True, self._report(row))

    def finish_run(
        self,
        report_id: str,
        result: dict[str, Any],
        *,
        estimated_cost_usd: Any | None = None,
        cost_tracking_complete: bool = False,
        usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = _stamp(self._now(now))
        result_json = _json(result)
        usage_json = _json(usage or {})
        actual = _micros(estimated_cost_usd) if cost_tracking_complete and estimated_cost_usd is not None else None
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE report_id=?", (report_id,)).fetchone()
            if row is None:
                raise StoreError("report_not_found", "Report not found.", 404)
            if row["status"] in {"pending", "interrupted"}:
                cost = actual if actual is not None else row["reserved_microusd"]
                basis = "usage_estimate" if actual is not None else "reservation_retained"
                if actual is not None and actual > row["reserved_microusd"]:
                    basis = "usage_estimate_exceeds_reservation"
                connection.execute(
                    """UPDATE runs SET status='succeeded',error_code=NULL,finished_at=?,updated_at=?,
                    accounted_microusd=?,cost_basis=?,usage_json=?,result_json=? WHERE report_id=?""",
                    (timestamp, timestamp, cost, basis, usage_json, result_json, report_id),
                )
            row = connection.execute("SELECT * FROM runs WHERE report_id=?", (report_id,)).fetchone()
        return self._report(row)

    def fail_run(self, report_id: str, error_code: str = "execution_failed", *, now: datetime | None = None) -> None:
        code = error_code if _ERROR.fullmatch(error_code or "") else "execution_failed"
        timestamp = _stamp(self._now(now))
        with self._transaction() as connection:
            connection.execute(
                """UPDATE runs SET status='failed',error_code=?,finished_at=?,updated_at=?,
                cost_basis='reservation_retained' WHERE report_id=? AND status='pending'""",
                (code, timestamp, timestamp, report_id),
            )

    def recover_stale_runs(self, *, now: datetime | None = None, stale_after_seconds: int = STALE_EXECUTION_SECONDS) -> int:
        """Mark old unfinished work ambiguous, retaining its reservation; never retry."""
        moment = self._now(now)
        timestamp = _stamp(moment)
        cutoff = _stamp(moment - timedelta(seconds=stale_after_seconds))
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE runs SET status='interrupted',error_code='execution_interrupted',finished_at=?,
                updated_at=?,cost_basis='reservation_retained' WHERE status='pending' AND created_at<?""",
                (timestamp, timestamp, cutoff),
            )
            connection.execute(
                """UPDATE schedule_occurrences SET status='interrupted',error_code='execution_interrupted',
                finished_at=? WHERE status='claimed' AND claimed_at<?""", (timestamp, cutoff),
            )
            return cursor.rowcount

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE report_id=?", (report_id,)).fetchone()
        return self._report(row) if row is not None else None

    def list_reports(self, limit: int = 50, offset: int = 0, status: str | None = None) -> list[dict[str, Any]]:
        limit, offset = min(max(int(limit), 1), 200), max(int(offset), 0)
        query = "SELECT * FROM runs"
        params: list[Any] = []
        if status is not None:
            if status not in {"pending", "succeeded", "failed", "interrupted"}:
                raise StoreError("invalid_report_status", "Unknown report status.")
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at DESC,report_id DESC LIMIT ? OFFSET ?"
        with self._connect() as connection:
            rows = connection.execute(query, (*params, limit, offset)).fetchall()
        return [self._report(row, include_result=False) for row in rows]

    def usage_summary(self, *, now: datetime | None = None) -> dict[str, Any]:
        moment = self._now(now)
        # A restart can happen before the stale threshold while scheduling is
        # disabled. Reading usage later resolves those abandoned pending rows.
        self.recover_stale_runs(now=moment)
        date = moment.astimezone(DUBAI).date().isoformat()
        month = date[:7]
        limits = self.limits
        with self._connect() as connection:
            daily = connection.execute(
                """SELECT COUNT(*) AS attempts,COALESCE(SUM(accounted_microusd),0) AS cost,
                COALESCE(SUM(status='succeeded'),0) AS succeeded,
                COALESCE(SUM(status IN ('failed','interrupted')),0) AS failed,
                COALESCE(SUM(status='pending'),0) AS pending FROM runs WHERE local_date=?""", (date,),
            ).fetchone()
            monthly = connection.execute(
                "SELECT COUNT(*) AS attempts,COALESCE(SUM(accounted_microusd),0) AS cost FROM runs WHERE local_month=?", (month,),
            ).fetchone()
        daily_budget, monthly_budget = _micros(limits.daily_budget_usd), _micros(limits.monthly_budget_usd)
        return {
            "timezone": "Asia/Dubai", "date": date, "month": month,
            "daily": {
                "attempts": daily["attempts"], "limit": limits.max_reports_per_day,
                "remaining": max(0, limits.max_reports_per_day - daily["attempts"]),
                "succeeded": daily["succeeded"], "failed": daily["failed"], "pending": daily["pending"],
                "estimated_cost_usd": _usd(daily["cost"]), "budget_usd": _usd(daily_budget),
                "remaining_budget_usd": _usd(max(0, daily_budget - daily["cost"])),
            },
            "monthly": {
                "attempts": monthly["attempts"], "estimated_cost_usd": _usd(monthly["cost"]),
                "budget_usd": _usd(monthly_budget),
                "remaining_budget_usd": _usd(max(0, monthly_budget - monthly["cost"])),
            },
            "cost_note": "Estimated USD exposure, not a provider invoice. Pending and ambiguous failures retain their full reservation.",
        }

    @staticmethod
    def _schedule(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["times"] = json.loads(item.pop("times_json"))
        item["enabled"] = bool(item["enabled"])
        item["timezone"] = "Asia/Dubai"
        return item

    @staticmethod
    def _validate_schedule(agent_id: str, task: str, times: list[str], enabled: bool, name: str) -> list[str]:
        from app.registry import get_agent
        from app.permissions import is_allowed

        agent = get_agent(agent_id)
        if agent is None:
            raise StoreError("unknown_agent", "Unknown agent.", 404)
        if not agent.get("enabled", False) or not is_allowed(agent_id, "actions", "submit_task"):
            raise StoreError("agent_disabled", "This agent cannot execute reports.", 403)
        if not isinstance(task, str) or not task.strip() or len(task) > 8000:
            raise StoreError("invalid_task", "Task must contain 1 to 8000 characters.")
        if not isinstance(times, list) or not 1 <= len(times) <= MAX_DAILY_REPORTS:
            raise StoreError("invalid_schedule_times", "Provide between 1 and 50 daily times.")
        if any(not isinstance(item, str) or _TIME.fullmatch(item) is None for item in times):
            raise StoreError("invalid_schedule_times", "Daily times must use the 24-hour clock, for example 09:30.")
        if len(set(times)) != len(times):
            raise StoreError("duplicate_schedule_times", "Daily times must be unique within a schedule.")
        if not isinstance(enabled, bool):
            raise StoreError("invalid_schedule_enabled", "enabled must be a boolean.")
        if not isinstance(name, str) or len(name) > 120:
            raise StoreError("invalid_schedule_name", "Name must contain at most 120 characters.")
        return sorted(times)

    @staticmethod
    def _check_slots(connection: sqlite3.Connection, times: list[str], enabled: bool, exclude: str | None = None) -> None:
        if not enabled:
            return
        rows = connection.execute("SELECT schedule_id,times_json FROM schedules WHERE enabled=1").fetchall()
        slots = sum(len(json.loads(row["times_json"])) for row in rows if row["schedule_id"] != exclude)
        if slots + len(times) > MAX_DAILY_REPORTS:
            raise StoreError("schedule_slot_limit", "Enabled schedules may contain at most 50 daily occurrences in total.", 409)

    def create_schedule(self, agent_id: str, task: str, times: list[str], *, enabled: bool = False, name: str = "", now: datetime | None = None) -> dict[str, Any]:
        times = self._validate_schedule(agent_id, task, times, enabled, name)
        schedule_id, timestamp = "sch_" + uuid4().hex, _stamp(self._now(now))
        with self._transaction() as connection:
            self._check_slots(connection, times, enabled)
            connection.execute(
                "INSERT INTO schedules (schedule_id,name,agent_id,task,times_json,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (schedule_id, name, agent_id, task, _json(times), int(enabled), timestamp, timestamp),
            )
            row = connection.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
        return self._schedule(row)

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
        return self._schedule(row) if row is not None else None

    def list_schedules(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM schedules ORDER BY created_at,schedule_id").fetchall()
        return [self._schedule(row) for row in rows]

    def update_schedule(self, schedule_id: str, *, agent_id: str | None = None, task: str | None = None, times: list[str] | None = None, enabled: bool | None = None, name: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
            if row is None:
                raise StoreError("schedule_not_found", "Schedule not found.", 404)
            current = self._schedule(row)
            values = {"agent_id": agent_id, "task": task, "times": times, "enabled": enabled, "name": name}
            current.update({key: value for key, value in values.items() if value is not None})
            updated_times = self._validate_schedule(current["agent_id"], current["task"], current["times"], current["enabled"], current["name"])
            self._check_slots(connection, updated_times, current["enabled"], exclude=schedule_id)
            connection.execute(
                "UPDATE schedules SET name=?,agent_id=?,task=?,times_json=?,enabled=?,updated_at=? WHERE schedule_id=?",
                (current["name"], current["agent_id"], current["task"], _json(updated_times), int(current["enabled"]), _stamp(self._now(now)), schedule_id),
            )
            row = connection.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
        return self._schedule(row)

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._transaction() as connection:
            return connection.execute("DELETE FROM schedules WHERE schedule_id=?", (schedule_id,)).rowcount > 0

    def claim_due_occurrences(self, *, now: datetime | None = None, limit: int = 1, grace_seconds: int = MISSED_OCCURRENCE_GRACE_SECONDS) -> list[dict[str, Any]]:
        """Claim due work atomically. Skip backlog older than five minutes.

        New or edited definitions only run future times. A claimed occurrence
        is never retried, even after interruption or restart.
        """
        moment = self._now(now)
        local_now = moment.astimezone(DUBAI)
        cutoff = moment - timedelta(seconds=grace_seconds)
        timestamp = _stamp(moment)
        claimed: list[dict[str, Any]] = []
        with self._transaction() as connection:
            schedules = connection.execute("SELECT * FROM schedules WHERE enabled=1 ORDER BY created_at,schedule_id").fetchall()
            candidates: list[tuple[datetime, dict[str, Any], str]] = []
            for raw in schedules:
                schedule = self._schedule(raw)
                effective_at = datetime.fromisoformat(schedule["updated_at"])
                for day in (local_now.date() - timedelta(days=1), local_now.date()):
                    for local_time in schedule["times"]:
                        hour, minute = map(int, local_time.split(":"))
                        due = datetime(day.year, day.month, day.day, hour, minute, tzinfo=DUBAI).astimezone(timezone.utc)
                        if due <= moment and due >= effective_at:
                            candidates.append((due, schedule, local_time))
            for due, schedule, local_time in sorted(candidates, key=lambda item: (item[0], item[1]["schedule_id"])):
                local_date = due.astimezone(DUBAI).date().isoformat()
                key = f"schedule:{schedule['schedule_id']}:{local_date}:{local_time}"
                missed = due < cutoff
                if not missed and len(claimed) >= min(max(limit, 1), 50):
                    continue
                state = "skipped_missed" if missed else "claimed"
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO schedule_occurrences
                    (occurrence_key,schedule_id,due_at,local_time,status,claimed_at,finished_at,error_code)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (key, schedule["schedule_id"], _stamp(due), local_time, state, timestamp,
                     timestamp if missed else None, "missed_grace_window" if missed else None),
                )
                if cursor.rowcount and not missed:
                    claimed.append({"occurrence_key": key, "due_at": _stamp(due), "schedule_id": schedule["schedule_id"], "agent_id": schedule["agent_id"], "task": schedule["task"], "local_time": local_time})
        return claimed

    def finish_occurrence(self, occurrence_key: str, *, status: str, report_id: str | None = None, error_code: str | None = None, now: datetime | None = None) -> None:
        if status not in {"succeeded", "failed", "blocked", "interrupted"}:
            raise StoreError("invalid_occurrence_status", "Invalid schedule occurrence status.")
        code = error_code if error_code and _ERROR.fullmatch(error_code) else None
        with self._transaction() as connection:
            connection.execute(
                "UPDATE schedule_occurrences SET status=?,finished_at=?,report_id=?,error_code=? WHERE occurrence_key=? AND status='claimed'",
                (status, _stamp(self._now(now)), report_id, code, occurrence_key),
            )

    def list_occurrences(self, schedule_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 500)
        with self._connect() as connection:
            if schedule_id is None:
                rows = connection.execute("SELECT * FROM schedule_occurrences ORDER BY due_at DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM schedule_occurrences WHERE schedule_id=? ORDER BY due_at DESC LIMIT ?", (schedule_id, limit)).fetchall()
        return [dict(row) for row in rows]


@lru_cache(maxsize=8)
def _store_at(path: str) -> Store:
    store = Store(path)
    store.initialize()
    return store


def get_store() -> Store:
    path = get_setting("AI_HUB_DATABASE_PATH") or str(PROJECT_ROOT / "data" / "ai_hub.sqlite3")
    return _store_at(str(Path(path).expanduser().resolve()))
