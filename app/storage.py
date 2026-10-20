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
from app.newsdata import MAX_NEWSDATA_RESULTS

DUBAI = ZoneInfo("Asia/Dubai")
MAX_DAILY_REPORTS = 100
# Increasing the manual/shared report allowance must not expand scheduled work.
MAX_DAILY_SCHEDULE_SLOTS = 50
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


def _credits(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 2147483647:
        raise StoreError("invalid_credit_configuration", "NewsData credits must be nonnegative integers.", 503)
    return value


@dataclass(frozen=True)
class QuotaLimits:
    max_reports_per_day: int = 50
    daily_budget_usd: Any = "5"
    monthly_budget_usd: Any = "100"
    newsdata_daily_credit_limit: int = 200

    def __post_init__(self) -> None:
        if isinstance(self.max_reports_per_day, bool) or not isinstance(self.max_reports_per_day, int):
            raise StoreError("invalid_quota_configuration", f"MAX_REPORTS_PER_DAY must be an integer from 1 to {MAX_DAILY_REPORTS}.", 503)
        if not 1 <= self.max_reports_per_day <= MAX_DAILY_REPORTS:
            raise StoreError("invalid_quota_configuration", f"MAX_REPORTS_PER_DAY must be between 1 and {MAX_DAILY_REPORTS}.", 503)
        _micros(self.daily_budget_usd)
        _micros(self.monthly_budget_usd)
        _credits(self.newsdata_daily_credit_limit)

    @classmethod
    def from_environment(cls) -> "QuotaLimits":
        try:
            count = int(get_setting("MAX_REPORTS_PER_DAY", "50") or "50")
            newsdata_credits = int(get_setting("NEWSDATA_DAILY_CREDIT_LIMIT", "200") or "200")
        except ValueError:
            raise StoreError("invalid_quota_configuration", "Report and NewsData credit limits must be integers.", 503) from None
        return cls(
            max_reports_per_day=count,
            daily_budget_usd=get_setting("DAILY_BUDGET_USD", "5"),
            monthly_budget_usd=get_setting("MONTHLY_BUDGET_USD", "100"),
            newsdata_daily_credit_limit=newsdata_credits,
        )


@dataclass(frozen=True)
class Reservation:
    created: bool
    report: dict[str, Any]


@dataclass(frozen=True)
class ChatReservation:
    created: bool
    turn: dict[str, Any]


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
    result_json TEXT,
    reserved_newsdata_credits INTEGER NOT NULL DEFAULT 0 CHECK(reserved_newsdata_credits >= 0),
    accounted_newsdata_credits INTEGER NOT NULL DEFAULT 0 CHECK(accounted_newsdata_credits >= 0),
    newsdata_credit_basis TEXT NOT NULL DEFAULT 'not_used'
);
CREATE INDEX IF NOT EXISTS runs_local_date_idx ON runs(local_date);
CREATE INDEX IF NOT EXISTS runs_local_month_idx ON runs(local_month);
CREATE INDEX IF NOT EXISTS runs_created_at_idx ON runs(created_at DESC);
CREATE TABLE IF NOT EXISTS report_request_keys (
    idempotency_key TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES runs(report_id),
    request_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS newsdata_pages (
    report_id TEXT PRIMARY KEY REFERENCES runs(report_id),
    root_report_id TEXT NOT NULL REFERENCES runs(report_id),
    parent_report_id TEXT UNIQUE REFERENCES runs(report_id),
    page_index INTEGER NOT NULL CHECK(page_index >= 1),
    page_size INTEGER NOT NULL CHECK(page_size BETWEEN 1 AND 50),
    language TEXT NOT NULL CHECK(language IN ('ar','en')),
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    cursor_used TEXT,
    cursor_hash TEXT NOT NULL,
    next_cursor TEXT,
    stop_reason TEXT,
    UNIQUE(root_report_id,page_index),
    UNIQUE(root_report_id,cursor_hash)
);
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task TEXT NOT NULL,
    times_json TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    language TEXT NOT NULL DEFAULT 'ar' CHECK(language IN ('ar','en')),
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
CREATE TABLE IF NOT EXISTS chat_conversations (
    conversation_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    language TEXT NOT NULL CHECK(language IN ('ar','en')),
    report_id TEXT REFERENCES runs(report_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_conversations_owner_idx ON chat_conversations(owner_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES chat_conversations(conversation_id),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed','interrupted')),
    error_code TEXT,
    language TEXT NOT NULL CHECK(language IN ('ar','en')),
    report_id TEXT REFERENCES runs(report_id),
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
    context_json TEXT,
    UNIQUE(conversation_id,idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS chat_one_pending_conversation_idx
    ON chat_turns(conversation_id) WHERE status='pending';
CREATE INDEX IF NOT EXISTS chat_turns_local_date_idx ON chat_turns(local_date);
CREATE INDEX IF NOT EXISTS chat_turns_local_month_idx ON chat_turns(local_month);
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES chat_conversations(conversation_id),
    turn_id TEXT NOT NULL REFERENCES chat_turns(turn_id),
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id,sequence),
    UNIQUE(turn_id,role)
);
CREATE INDEX IF NOT EXISTS chat_messages_conversation_idx ON chat_messages(conversation_id,sequence DESC);
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
            # Additive migration: existing Arabic schedules retain their original behavior.
            connection.execute("BEGIN IMMEDIATE")
            try:
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(schedules)")}
                if "language" not in columns:
                    connection.execute("ALTER TABLE schedules ADD COLUMN language TEXT NOT NULL DEFAULT 'ar'")
                run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
                for name, declaration in (
                    ("reserved_newsdata_credits", "INTEGER NOT NULL DEFAULT 0 CHECK(reserved_newsdata_credits >= 0)"),
                    ("accounted_newsdata_credits", "INTEGER NOT NULL DEFAULT 0 CHECK(accounted_newsdata_credits >= 0)"),
                    ("newsdata_credit_basis", "TEXT NOT NULL DEFAULT 'not_used'"),
                ):
                    if name not in run_columns:
                        connection.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
                connection.execute("""INSERT OR IGNORE INTO report_request_keys
                    SELECT idempotency_key,report_id,request_hash FROM runs WHERE idempotency_key IS NOT NULL""")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _now(self, now: datetime | None = None) -> datetime:
        return _aware(now if now is not None else self.clock())

    @staticmethod
    def _fingerprint(agent_id: str, task: str, language: str = "ar", *,
                     parent_report_id: str | None = None, page_index: int | None = None) -> str:
        Store._validate_language(language)
        values = [agent_id, task] if language == "ar" else [agent_id, task, language]
        if parent_report_id is not None:
            values.append({"newsdata_parent_report_id": parent_report_id, "page_index": page_index})
        return hashlib.sha256(_json(values).encode("utf-8")).hexdigest()

    @staticmethod
    def _cursor(value: Any) -> str | None:
        if value is None or value == "":
            return None
        if (not isinstance(value, str) or len(value) > 8192
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise StoreError("invalid_newsdata_context", "Invalid saved NewsData pagination state.", 503)
        return value

    @classmethod
    def _newsdata_context(cls, context: Any) -> dict[str, Any]:
        try:
            if not isinstance(context, dict):
                raise ValueError
            start = _aware(datetime.fromisoformat(context["window_start"].replace("Z", "+00:00")))
            end = _aware(datetime.fromisoformat(context["window_end"].replace("Z", "+00:00")))
            size, index = context["page_size"], context["page_index"]
            if (end - start != timedelta(hours=48) or type(size) is not int or not 1 <= size <= 50
                    or type(index) is not int or not 1 <= index <= 2147483647):
                raise ValueError
            parent, root = context.get("parent_report_id"), context.get("root_report_id")
            cursor = cls._cursor(context.get("cursor"))
            consumed = context.get("results_consumed", 0)
            if type(consumed) is not int or not 0 <= consumed <= MAX_NEWSDATA_RESULTS:
                raise ValueError
            if index == 1:
                if parent is not None or cursor is not None or consumed != 0:
                    raise ValueError
                size = min(size, MAX_NEWSDATA_RESULTS)
            elif not all(isinstance(value, str) and value for value in (parent, root, cursor)):
                raise ValueError
            return {"cursor": cursor, "window_start": _stamp(start), "window_end": _stamp(end),
                    "page_size": size, "page_index": index, "parent_report_id": parent, "root_report_id": root,
                    "results_consumed": consumed}
        except (ValueError, TypeError, KeyError, AttributeError):
            raise StoreError("invalid_newsdata_context", "Invalid saved NewsData pagination state.", 503) from None

    @staticmethod
    def _results_consumed(connection: sqlite3.Connection, row: sqlite3.Row, *, before: bool = False) -> int:
        """Reconstruct saved provider positions, including pre-cap reports."""
        last_index = row["page_index"] - int(before)
        pages = connection.execute("""SELECT p.page_size,r.status,r.result_json FROM newsdata_pages p
            JOIN runs r USING(report_id) WHERE p.root_report_id=? AND p.page_index<=?
            ORDER BY p.page_index""", (row["root_report_id"], last_index)).fetchall()
        consumed = 0
        for page in pages:
            if page["status"] != "succeeded":
                continue
            try:
                metadata = json.loads(page["result_json"] or "{}").get("newsdata", {})
                count = metadata.get("consumed_item_count", metadata.get("returned_item_count"))
            except (ValueError, TypeError, AttributeError):
                count = None
            # Unknown legacy counts consume the saved page size rather than
            # silently restarting the result allowance at zero.
            if type(count) is not int or not 0 <= count <= page["page_size"]:
                count = page["page_size"]
            consumed += count
            if consumed >= MAX_NEWSDATA_RESULTS:
                return MAX_NEWSDATA_RESULTS
        return consumed

    @classmethod
    def _page_context(cls, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        return {"cursor": row["cursor_used"], **{name: row[name] for name in (
            "window_start", "window_end", "page_size", "page_index", "parent_report_id", "root_report_id")},
            "results_consumed": cls._results_consumed(connection, row, before=True)}

    @classmethod
    def _public_report(cls, connection: sqlite3.Connection, row: sqlite3.Row, include_result: bool = True) -> dict[str, Any]:
        report = cls._report(row, include_result=include_result)
        page = connection.execute("SELECT * FROM newsdata_pages WHERE report_id=?", (row["report_id"],)).fetchone()
        if page is not None:
            report["language"] = page["language"]
            report["pagination"] = {name: page[name] for name in (
                "page_index", "page_size", "parent_report_id", "root_report_id")}
            consumed = cls._results_consumed(connection, page)
            reached = consumed >= MAX_NEWSDATA_RESULTS
            report["pagination"].update(result_limit=MAX_NEWSDATA_RESULTS, results_consumed=consumed,
                results_before_page=cls._results_consumed(connection, page, before=True), limit_reached=reached,
                has_more=bool(page["next_cursor"]) and not reached)
            child = connection.execute("""SELECT r.report_id,r.status FROM newsdata_pages p
                JOIN runs r USING(report_id) WHERE p.parent_report_id=?""", (row["report_id"],)).fetchone()
            report["pagination"]["next_report_id"] = child["report_id"] if child and not reached else None
            report["pagination"]["next_report_status"] = child["status"] if child and not reached else None
            if page["stop_reason"]:
                report["pagination"]["stop_reason"] = page["stop_reason"]
            if isinstance(report.get("result"), dict):
                metadata = report["result"].get("pagination")
                report["result"]["pagination"] = {**(metadata if isinstance(metadata, dict) else {}), **report["pagination"]}
        return report

    def get_newsdata_context(self, report_id: str) -> dict[str, Any]:
        """Internal only: opaque provider state never appears in public reports."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM newsdata_pages WHERE report_id=?", (report_id,)).fetchone()
            if row is None:
                raise StoreError("invalid_newsdata_continuation", "This report has no NewsData page state.", 409)
            return self._page_context(connection, row)

    @classmethod
    def _continuation(cls, connection: sqlite3.Connection, parent_report_id: str,
                      agent_id: str, task: str, language: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT p.*,r.agent_id,r.task,r.status FROM newsdata_pages p JOIN runs r USING(report_id) WHERE p.report_id=?",
            (parent_report_id,),
        ).fetchone()
        if row is None or agent_id != "iran-news" or row["agent_id"] != "iran-news":
            raise StoreError("invalid_newsdata_continuation", "Choose a saved NewsData Iran report to continue.", 409)
        if row["status"] != "succeeded":
            raise StoreError("invalid_newsdata_continuation", "Only a successful saved NewsData page can be continued.", 409)
        if row["task"] != task or row["language"] != language:
            raise StoreError("continuation_request_mismatch", "Continue with the saved task and language.", 409)
        consumed = cls._results_consumed(connection, row)
        if consumed >= MAX_NEWSDATA_RESULTS:
            raise StoreError("newsdata_result_limit", "This report has already consumed its first 20 provider results.", 409)
        cursor = cls._cursor(row["next_cursor"])
        if cursor is None:
            raise StoreError("newsdata_no_more_pages", "The saved report has no further page.", 409)
        context = cls._page_context(connection, row)
        context.update(cursor=cursor, page_index=row["page_index"] + 1, parent_report_id=parent_report_id,
                       results_consumed=consumed)
        return cls._newsdata_context(context)

    def continuation_context(self, parent_report_id: str, agent_id: str, task: str, language: str = "ar") -> dict[str, Any]:
        if not isinstance(parent_report_id, str) or not parent_report_id or len(parent_report_id) > 100:
            raise StoreError("invalid_newsdata_continuation", "Invalid parent report ID.", 422)
        with self._connect() as connection:
            return self._continuation(connection, parent_report_id, agent_id, task, language)

    @staticmethod
    def _validate_language(language: str) -> None:
        if not isinstance(language, str) or language not in {"ar", "en"}:
            raise StoreError("invalid_language", "Language must be ar or en.", 422)

    @staticmethod
    def _budget_costs(connection: sqlite3.Connection, local_date: str, local_month: str) -> tuple[int, int]:
        """Both ledgers share the caller's SQLite write transaction and budget."""
        row = connection.execute(
            """SELECT
            (SELECT COALESCE(SUM(accounted_microusd),0) FROM runs WHERE local_date=?) +
            (SELECT COALESCE(SUM(accounted_microusd),0) FROM chat_turns WHERE local_date=?) AS daily,
            (SELECT COALESCE(SUM(accounted_microusd),0) FROM runs WHERE local_month=?) +
            (SELECT COALESCE(SUM(accounted_microusd),0) FROM chat_turns WHERE local_month=?) AS monthly""",
            (local_date, local_date, local_month, local_month),
        ).fetchone()
        return row["daily"], row["monthly"]

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

    @staticmethod
    def _key_row(connection: sqlite3.Connection, key: str) -> sqlite3.Row | None:
        return connection.execute("""SELECT r.* FROM report_request_keys k
            JOIN runs r ON r.report_id=k.report_id WHERE k.idempotency_key=?""", (key,)).fetchone()

    @classmethod
    def _bind_key(cls, connection: sqlite3.Connection, key: str | None, row: sqlite3.Row) -> None:
        if key is not None:
            previous = cls._key_row(connection, key)
            if previous is not None and previous["report_id"] != row["report_id"]:
                raise StoreError("idempotency_conflict", "This Idempotency-Key belongs to a different request.", 409)
            connection.execute("INSERT OR IGNORE INTO report_request_keys VALUES (?,?,?)",
                               (key, row["report_id"], row["request_hash"]))

    def lookup_idempotency(self, key: str | None, agent_id: str, task: str, language: str = "ar", *,
                           parent_report_id: str | None = None, page_index: int | None = None) -> dict[str, Any] | None:
        self._validate_key(key)
        if key is None:
            return None
        with self._connect() as connection:
            row = self._key_row(connection, key)
            if row is None:
                return None
            if row["request_hash"] != self._fingerprint(agent_id, task, language, parent_report_id=parent_report_id, page_index=page_index):
                raise StoreError("idempotency_conflict", "This Idempotency-Key belongs to a different task or page.", 409)
            return self._public_report(connection, row)

    def lookup_newsdata_child(self, parent_report_id: str, agent_id: str, task: str,
                              language: str = "ar", *, idempotency_key: str | None = None) -> dict[str, Any] | None:
        self._validate_key(idempotency_key)
        with self._transaction() as connection:
            context = self._continuation(connection, parent_report_id, agent_id, task, language)
            row = connection.execute("""SELECT r.* FROM newsdata_pages p JOIN runs r USING(report_id)
                WHERE p.parent_report_id=?""", (parent_report_id,)).fetchone()
            if row is None:
                return None
            fingerprint = self._fingerprint(agent_id, task, language, parent_report_id=parent_report_id,
                                            page_index=context["page_index"])
            if row["request_hash"] != fingerprint:
                raise StoreError("idempotency_conflict", "The next page belongs to a different request.", 409)
            self._bind_key(connection, idempotency_key, row)
            return self._public_report(connection, row)

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
        language: str = "ar",
        newsdata_credits: int = 0,
        newsdata_context: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> Reservation:
        self._validate_key(idempotency_key)
        if trigger not in {"manual", "scheduled"}:
            raise StoreError("invalid_trigger", "Trigger must be manual or scheduled.")
        reservation = _micros(reservation_usd)
        credits = _credits(newsdata_credits)
        if reservation <= 0 and not allow_zero_cost and not credits:
            raise StoreError("invalid_cost_configuration", "A positive upfront cost reservation is required.", 503)
        context = self._newsdata_context(newsdata_context) if newsdata_context is not None else None
        if (context is not None and (agent_id != "iran-news" or credits != 1)) or (credits and context is None):
            raise StoreError("invalid_credit_configuration", "A NewsData page must reserve exactly one credit.", 503)
        snapshot = dict(pricing_snapshot or {})
        snapshot.pop("newsdata_context", None)
        limits = self.limits
        daily_budget = _micros(limits.daily_budget_usd)
        monthly_budget = _micros(limits.monthly_budget_usd)
        moment = self._now(now)
        timestamp = _stamp(moment)
        local_date = moment.astimezone(DUBAI).date().isoformat()
        local_month = local_date[:7]
        request_hash = self._fingerprint(agent_id, task, language,
                                         parent_report_id=context["parent_report_id"] if context else None,
                                         page_index=context["page_index"] if context else None)
        with self._transaction() as connection:
            if idempotency_key is not None:
                row = self._key_row(connection, idempotency_key)
                if row is not None:
                    if row["request_hash"] != request_hash:
                        raise StoreError("idempotency_conflict", "This Idempotency-Key belongs to a different task.", 409)
                    return Reservation(False, self._public_report(connection, row))
            if context and context["parent_report_id"] is not None:
                saved = self._continuation(connection, context["parent_report_id"], agent_id, task, language)
                if saved != context:
                    raise StoreError("invalid_newsdata_context", "Pagination must use the exact saved context.", 409)
                existing = connection.execute("""SELECT r.* FROM newsdata_pages p JOIN runs r USING(report_id)
                    WHERE p.parent_report_id=?""", (context["parent_report_id"],)).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise StoreError("idempotency_conflict", "The next page belongs to a different request.", 409)
                    self._bind_key(connection, idempotency_key, existing)
                    return Reservation(False, self._public_report(connection, existing))
                repeated = connection.execute("SELECT 1 FROM newsdata_pages WHERE root_report_id=? AND cursor_hash=?",
                    (context["root_report_id"], hashlib.sha256(context["cursor"].encode()).hexdigest())).fetchone()
                if repeated is not None:
                    raise StoreError("newsdata_cursor_repeated", "NewsData returned a previously consumed cursor.", 409)
            day = connection.execute(
                "SELECT COUNT(*) AS attempts, COALESCE(SUM(accounted_microusd),0) AS cost FROM runs WHERE local_date=?",
                (local_date,),
            ).fetchone()
            day_cost, month_cost = self._budget_costs(connection, local_date, local_month)
            if day["attempts"] >= limits.max_reports_per_day:
                raise StoreError("daily_report_limit", "The daily report attempt limit has been reached (Asia/Dubai).", 429)
            if day_cost + reservation > daily_budget:
                raise StoreError("daily_budget_limit", "The daily estimated USD budget cannot cover this reservation.", 429)
            if month_cost + reservation > monthly_budget:
                raise StoreError("monthly_budget_limit", "The monthly estimated USD budget cannot cover this reservation.", 429)
            credit_cost = connection.execute("SELECT COALESCE(SUM(accounted_newsdata_credits),0) FROM runs WHERE local_date=?",
                                             (local_date,)).fetchone()[0]
            from app.news_feed import accounted_credits
            credit_cost += accounted_credits(connection, local_date=local_date)
            if credits and credit_cost + credits > limits.newsdata_daily_credit_limit:
                raise StoreError("newsdata_daily_credit_limit", "The daily NewsData credit budget cannot cover this page.", 429)
            report_id = "rpt_" + uuid4().hex
            connection.execute(
                """INSERT INTO runs (report_id,idempotency_key,request_hash,agent_id,task,trigger,status,
                created_at,updated_at,local_date,local_month,reserved_microusd,accounted_microusd,
                cost_basis,provider,model,pricing_snapshot_json,reserved_newsdata_credits,accounted_newsdata_credits,newsdata_credit_basis)
                VALUES (?,?,?,?,?,?,'pending',?,?,?,?,?,?,'reservation',?,?,?,?,?,?)""",
                (report_id, idempotency_key, request_hash, agent_id, task, trigger, timestamp,
                 timestamp, local_date, local_month, reservation, reservation, provider, model,
                 _json(snapshot), credits, credits, "reservation" if credits else "not_used"),
            )
            if context is not None:
                root = context["root_report_id"] if context["parent_report_id"] else report_id
                cursor_hash = hashlib.sha256(context["cursor"].encode()).hexdigest() if context["cursor"] else ""
                connection.execute("""INSERT INTO newsdata_pages
                    (report_id,root_report_id,parent_report_id,page_index,page_size,language,window_start,window_end,cursor_used,cursor_hash)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (report_id, root, context["parent_report_id"], context["page_index"], context["page_size"], language,
                     context["window_start"], context["window_end"], context["cursor"], cursor_hash))
            row = connection.execute("SELECT * FROM runs WHERE report_id=?", (report_id,)).fetchone()
            self._bind_key(connection, idempotency_key, row)
            return Reservation(True, self._public_report(connection, row))

    def finish_run(
        self,
        report_id: str,
        result: dict[str, Any],
        *,
        estimated_cost_usd: Any | None = None,
        cost_tracking_complete: bool = False,
        usage: dict[str, Any] | None = None,
        newsdata_credits_used: int | None = None,
        credit_tracking_complete: bool = False,
        newsdata_context: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = _stamp(self._now(now))
        public_result = dict(result)
        embedded_context = public_result.pop("newsdata_context", None)
        if newsdata_context is None:
            newsdata_context = embedded_context
        result_json = _json(public_result)
        usage_json = _json(usage or {})
        actual = _micros(estimated_cost_usd) if cost_tracking_complete and estimated_cost_usd is not None else None
        actual_credits = _credits(newsdata_credits_used) if credit_tracking_complete and newsdata_credits_used is not None else None
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE report_id=?", (report_id,)).fetchone()
            if row is None:
                raise StoreError("report_not_found", "Report not found.", 404)
            if row["status"] in {"pending", "interrupted"}:
                page = connection.execute("SELECT * FROM newsdata_pages WHERE report_id=?", (report_id,)).fetchone()
                if page is not None:
                    returned = self._newsdata_context(newsdata_context)
                    if returned != self._page_context(connection, page):
                        raise StoreError("invalid_newsdata_context", "The returned page changed its saved query window or cursor.", 503)
                    next_cursor = self._cursor(newsdata_context.get("next_cursor"))
                    stop_reason = None
                    if next_cursor is not None and connection.execute(
                        "SELECT 1 FROM newsdata_pages WHERE root_report_id=? AND cursor_hash=?",
                        (page["root_report_id"], hashlib.sha256(next_cursor.encode()).hexdigest()),
                    ).fetchone():
                        next_cursor, stop_reason = None, "repeated_cursor"
                    connection.execute("UPDATE newsdata_pages SET next_cursor=?,stop_reason=? WHERE report_id=?",
                                       (next_cursor, stop_reason, report_id))
                cost = actual if actual is not None else row["reserved_microusd"]
                basis = "usage_estimate" if actual is not None else "reservation_retained"
                if actual is not None and actual > row["reserved_microusd"]:
                    basis = "usage_estimate_exceeds_reservation"
                credits = actual_credits if actual_credits is not None else row["reserved_newsdata_credits"]
                credit_basis = "usage_reported" if actual_credits is not None else "reservation_retained"
                if not row["reserved_newsdata_credits"] and not credits:
                    credit_basis = "not_used"
                elif actual_credits is not None and actual_credits > row["reserved_newsdata_credits"]:
                    credit_basis = "usage_exceeds_reservation"
                connection.execute(
                    """UPDATE runs SET status='succeeded',error_code=NULL,finished_at=?,updated_at=?,
                    accounted_microusd=?,cost_basis=?,usage_json=?,result_json=?,
                    accounted_newsdata_credits=?,newsdata_credit_basis=? WHERE report_id=?""",
                    (timestamp, timestamp, cost, basis, usage_json, result_json, credits, credit_basis, report_id),
                )
            row = connection.execute("SELECT * FROM runs WHERE report_id=?", (report_id,)).fetchone()
            return self._public_report(connection, row)

    def fail_run(self, report_id: str, error_code: str = "execution_failed", *, now: datetime | None = None) -> None:
        code = error_code if _ERROR.fullmatch(error_code or "") else "execution_failed"
        timestamp = _stamp(self._now(now))
        with self._transaction() as connection:
            connection.execute(
                """UPDATE runs SET status='failed',error_code=?,finished_at=?,updated_at=?,
                cost_basis='reservation_retained',newsdata_credit_basis=CASE WHEN reserved_newsdata_credits>0
                THEN 'reservation_retained' ELSE 'not_used' END WHERE report_id=? AND status='pending'""",
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
                updated_at=?,cost_basis='reservation_retained',newsdata_credit_basis=CASE WHEN reserved_newsdata_credits>0
                THEN 'reservation_retained' ELSE 'not_used' END WHERE status='pending' AND created_at<?""",
                (timestamp, timestamp, cutoff),
            )
            connection.execute(
                """UPDATE schedule_occurrences SET status='interrupted',error_code='execution_interrupted',
                finished_at=? WHERE status='claimed' AND claimed_at<?""", (timestamp, cutoff),
            )
            connection.execute(
                """UPDATE chat_turns SET status='interrupted',error_code='execution_interrupted',
                finished_at=?,updated_at=?,cost_basis='reservation_retained'
                WHERE status='pending' AND created_at<?""", (timestamp, timestamp, cutoff),
            )
            return cursor.rowcount

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE report_id=?", (report_id,)).fetchone()
            return self._public_report(connection, row) if row is not None else None

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
            return [self._public_report(connection, row, include_result=False) for row in rows]

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
            chat_daily = connection.execute(
                """SELECT COUNT(*) AS attempts,COALESCE(SUM(accounted_microusd),0) AS cost,
                COALESCE(SUM(status='succeeded'),0) AS succeeded,
                COALESCE(SUM(status IN ('failed','interrupted')),0) AS failed,
                COALESCE(SUM(status='pending'),0) AS pending FROM chat_turns WHERE local_date=?""", (date,),
            ).fetchone()
            chat_monthly = connection.execute(
                "SELECT COUNT(*) AS attempts,COALESCE(SUM(accounted_microusd),0) AS cost FROM chat_turns WHERE local_month=?",
                (month,),
            ).fetchone()
            credits = connection.execute("""SELECT
                (SELECT COALESCE(SUM(reserved_newsdata_credits),0) FROM runs WHERE local_date=?) AS reserved,
                (SELECT COALESCE(SUM(accounted_newsdata_credits),0) FROM runs WHERE local_date=?) AS accounted,
                (SELECT COALESCE(SUM(accounted_newsdata_credits),0) FROM runs WHERE local_month=?) AS monthly""",
                (date, date, month)).fetchone()
            from app.news_feed import accounted_credits
            feed_daily = accounted_credits(connection, local_date=date)
            feed_monthly = accounted_credits(connection, local_month=month)
            credits = {"reserved": credits["reserved"] + feed_daily,
                       "accounted": credits["accounted"] + feed_daily,
                       "monthly": credits["monthly"] + feed_monthly}
        daily_budget, monthly_budget = _micros(limits.daily_budget_usd), _micros(limits.monthly_budget_usd)
        daily_cost = daily["cost"] + chat_daily["cost"]
        monthly_cost = monthly["cost"] + chat_monthly["cost"]
        return {
            "timezone": "Asia/Dubai", "date": date, "month": month,
            "daily": {
                "attempts": daily["attempts"], "limit": limits.max_reports_per_day,
                "remaining": max(0, limits.max_reports_per_day - daily["attempts"]),
                "succeeded": daily["succeeded"], "failed": daily["failed"], "pending": daily["pending"],
                "estimated_cost_usd": _usd(daily_cost), "budget_usd": _usd(daily_budget),
                "remaining_budget_usd": _usd(max(0, daily_budget - daily_cost)),
                "report_estimated_cost_usd": _usd(daily["cost"]),
                "chat_estimated_cost_usd": _usd(chat_daily["cost"]),
                "chat_turns": chat_daily["attempts"], "chat_succeeded": chat_daily["succeeded"],
                "chat_failed": chat_daily["failed"], "chat_pending": chat_daily["pending"],
            },
            "monthly": {
                "attempts": monthly["attempts"], "estimated_cost_usd": _usd(monthly_cost),
                "budget_usd": _usd(monthly_budget),
                "remaining_budget_usd": _usd(max(0, monthly_budget - monthly_cost)),
                "report_estimated_cost_usd": _usd(monthly["cost"]),
                "chat_estimated_cost_usd": _usd(chat_monthly["cost"]),
                "chat_turns": chat_monthly["attempts"],
            },
            "provider_credits": {"newsdata": {
                "daily_limit": limits.newsdata_daily_credit_limit,
                "daily_reserved": credits["reserved"], "daily_accounted": credits["accounted"],
                "daily_remaining": max(0, limits.newsdata_daily_credit_limit - credits["accounted"]),
                "monthly_accounted": credits["monthly"], "unit": "credits",
            }},
            "cost_note": "Combined report and chat estimated USD exposure, not a provider invoice. Pending and ambiguous failures retain their full reservation. Report attempt limits exclude chat turns.",
        }

    @staticmethod
    def _chat_conversation_query() -> str:
        return """SELECT c.*,
            (SELECT COUNT(*) FROM chat_messages m WHERE m.conversation_id=c.conversation_id) AS message_count,
            (SELECT COALESCE(MAX(sequence),0) FROM chat_messages m WHERE m.conversation_id=c.conversation_id) AS last_sequence,
            (SELECT turn_id FROM chat_turns t WHERE t.conversation_id=c.conversation_id AND t.status='pending') AS pending_turn_id
            FROM chat_conversations c"""

    @classmethod
    def _chat_conversation_row(cls, connection: sqlite3.Connection, conversation_id: str, owner_id: str) -> sqlite3.Row:
        row = connection.execute(cls._chat_conversation_query() + " WHERE c.conversation_id=? AND c.owner_id=?",
                                 (conversation_id, owner_id)).fetchone()
        if row is None:
            raise StoreError("chat_conversation_not_found", "Conversation not found.", 404)
        return row

    @staticmethod
    def _chat_conversation(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in ("conversation_id", "title", "language", "report_id", "created_at",
                                          "updated_at", "message_count", "pending_turn_id")}

    @staticmethod
    def _chat_message_query() -> str:
        return """SELECT m.*,t.status,t.error_code,t.report_id FROM chat_messages m
                  JOIN chat_turns t ON t.turn_id=m.turn_id"""

    @staticmethod
    def _chat_message(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["message_id"], **{key: row[key] for key in (
            "role", "content", "created_at", "sequence", "turn_id", "status", "error_code", "report_id")}}

    @classmethod
    def _chat_turn(cls, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["reserved_cost_usd"] = _usd(item.pop("reserved_microusd"))
        item["estimated_cost_usd"] = _usd(item.pop("accounted_microusd"))
        item["pricing_snapshot"] = json.loads(item.pop("pricing_snapshot_json"))
        item["usage"] = json.loads(item.pop("usage_json") or "{}")
        item["context"] = json.loads(item.pop("context_json") or "{}")
        item.pop("request_hash", None)
        item.pop("idempotency_key", None)
        rows = connection.execute(cls._chat_message_query() + " WHERE m.turn_id=? ORDER BY m.sequence",
                                  (row["turn_id"],)).fetchall()
        item["messages"] = [cls._chat_message(message) for message in rows]
        return item

    @staticmethod
    def _chat_fingerprint(message: str, language: str, report_id: str | None, report_selected: bool) -> str:
        Store._validate_language(language)
        return hashlib.sha256(_json([message, language, {
            "selected": report_selected, "report_id": report_id if report_selected else None,
        }]).encode("utf-8")).hexdigest()

    def create_chat_conversation(self, owner_id: str, *, title: str = "", language: str = "ar") -> dict[str, Any]:
        self._validate_language(language)
        if not isinstance(title, str) or len(title) > 120:
            raise StoreError("invalid_chat_title", "Conversation title must contain at most 120 characters.", 422)
        conversation_id, timestamp = "conv_" + uuid4().hex, _stamp(self._now())
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO chat_conversations (conversation_id,owner_id,title,language,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (conversation_id, owner_id, title.strip(), language, timestamp, timestamp),
            )
            return self._chat_conversation(self._chat_conversation_row(connection, conversation_id, owner_id))

    def list_chat_conversations(self, owner_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        self.recover_stale_runs()
        with self._connect() as connection:
            rows = connection.execute(self._chat_conversation_query() +
                                      " WHERE c.owner_id=? ORDER BY c.updated_at DESC,c.conversation_id DESC LIMIT ? OFFSET ?",
                                      (owner_id, min(max(int(limit), 1), 100), max(int(offset), 0))).fetchall()
            return [self._chat_conversation(row) for row in rows]

    def get_chat_conversation(self, conversation_id: str, owner_id: str, *, limit: int = 100,
                              before: int | None = None) -> dict[str, Any]:
        self.recover_stale_runs()
        limit = min(max(int(limit), 1), 100)
        with self._connect() as connection:
            connection.execute("BEGIN")
            conversation = self._chat_conversation(self._chat_conversation_row(connection, conversation_id, owner_id))
            query = self._chat_message_query() + " WHERE m.conversation_id=?"
            values: list[Any] = [conversation_id]
            if before is not None:
                query += " AND m.sequence<?"
                values.append(before)
            rows = connection.execute(query + " ORDER BY m.sequence DESC LIMIT ?", (*values, limit + 1)).fetchall()
            has_more, page = len(rows) > limit, rows[:limit]
            conversation["messages"] = [self._chat_message(row) for row in reversed(page)]
            conversation["pagination"] = {"has_more": has_more,
                                            "next_before": page[-1]["sequence"] if has_more else None}
            return conversation

    def chat_snapshot(self, conversation_id: str, owner_id: str, *, history_messages: int = 40) -> dict[str, Any]:
        self.recover_stale_runs()
        with self._connect() as connection:
            connection.execute("BEGIN")
            row = self._chat_conversation_row(connection, conversation_id, owner_id)
            rows = connection.execute(
                self._chat_message_query() + " WHERE m.conversation_id=? AND t.status='succeeded' ORDER BY m.sequence DESC LIMIT ?",
                (conversation_id, min(max(int(history_messages), 2), 100)),
            ).fetchall()
            complete = connection.execute(
                "SELECT COUNT(*) FROM chat_messages m JOIN chat_turns t ON t.turn_id=m.turn_id WHERE m.conversation_id=? AND t.status='succeeded'",
                (conversation_id,),
            ).fetchone()[0]
            return {"conversation": self._chat_conversation(row), "history_version": row["last_sequence"],
                    "completed_message_count": complete,
                    "history": [self._chat_message(message) for message in reversed(rows)]}

    def lookup_chat_idempotency(self, conversation_id: str, owner_id: str, key: str,
                                message: str, language: str, report_id: str | None,
                                report_selected: bool) -> dict[str, Any] | None:
        self._validate_key(key)
        if key is None:
            raise StoreError("invalid_idempotency_key", "Idempotency-Key is required for chat messages.", 422)
        fingerprint = self._chat_fingerprint(message, language, report_id, report_selected)
        with self._connect() as connection:
            self._chat_conversation_row(connection, conversation_id, owner_id)
            row = connection.execute("SELECT * FROM chat_turns WHERE conversation_id=? AND idempotency_key=?",
                                     (conversation_id, key)).fetchone()
            if row is None:
                return None
            if row["request_hash"] != fingerprint:
                raise StoreError("idempotency_conflict", "This Idempotency-Key belongs to a different chat message.", 409)
            return self._chat_turn(connection, row)

    def reserve_chat_turn(self, conversation_id: str, owner_id: str, message: str, language: str,
                          reservation_usd: Any, *, idempotency_key: str, expected_sequence: int,
                          report_id: str | None = None, report_selected: bool = False,
                          provider: str = "", model: str = "", pricing_snapshot: dict[str, Any] | None = None,
                          context: dict[str, Any] | None = None) -> ChatReservation:
        self._validate_key(idempotency_key)
        if idempotency_key is None:
            raise StoreError("invalid_idempotency_key", "Idempotency-Key is required for chat messages.", 422)
        if not isinstance(message, str) or not message.strip() or len(message) > 8000:
            raise StoreError("invalid_chat_message", "Message must contain 1 to 8000 characters.", 422)
        fingerprint = self._chat_fingerprint(message, language, report_id, report_selected)
        reservation = _micros(reservation_usd)
        if reservation <= 0:
            raise StoreError("invalid_cost_configuration", "A positive upfront cost reservation is required.", 503)
        limits, moment = self.limits, self._now()
        timestamp, local_date = _stamp(moment), moment.astimezone(DUBAI).date().isoformat()
        local_month = local_date[:7]
        with self._transaction() as connection:
            conversation = self._chat_conversation_row(connection, conversation_id, owner_id)
            previous = connection.execute("SELECT * FROM chat_turns WHERE conversation_id=? AND idempotency_key=?",
                                          (conversation_id, idempotency_key)).fetchone()
            if previous is not None:
                if previous["request_hash"] != fingerprint:
                    raise StoreError("idempotency_conflict", "This Idempotency-Key belongs to a different chat message.", 409)
                return ChatReservation(False, self._chat_turn(connection, previous))
            if conversation["pending_turn_id"] is not None:
                raise StoreError("conversation_busy", "Another message in this conversation is still being processed.", 409)
            if conversation["last_sequence"] != expected_sequence:
                raise StoreError("chat_history_changed", "Conversation history changed. Refresh before sending this message.", 409)
            daily_cost, monthly_cost = self._budget_costs(connection, local_date, local_month)
            if daily_cost + reservation > _micros(limits.daily_budget_usd):
                raise StoreError("daily_budget_limit", "The daily estimated USD budget cannot cover this reservation.", 429)
            if monthly_cost + reservation > _micros(limits.monthly_budget_usd):
                raise StoreError("monthly_budget_limit", "The monthly estimated USD budget cannot cover this reservation.", 429)
            effective_report = report_id if report_selected else conversation["report_id"]
            turn_id = "cht_" + uuid4().hex
            connection.execute(
                """INSERT INTO chat_turns (turn_id,conversation_id,idempotency_key,request_hash,status,language,report_id,
                created_at,updated_at,local_date,local_month,reserved_microusd,accounted_microusd,cost_basis,
                provider,model,pricing_snapshot_json,context_json)
                VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?,?,'reservation',?,?,?,?)""",
                (turn_id, conversation_id, idempotency_key, fingerprint, language, effective_report, timestamp,
                 timestamp, local_date, local_month, reservation, reservation, provider, model,
                 _json(pricing_snapshot or {}), _json(context or {})),
            )
            connection.execute(
                "INSERT INTO chat_messages (message_id,conversation_id,turn_id,sequence,role,content,created_at) VALUES (?,?,?,?,'user',?,?)",
                ("msg_" + uuid4().hex, conversation_id, turn_id, expected_sequence + 1, message, timestamp),
            )
            title = conversation["title"] or re.sub(r"\s+", " ", message).strip()[:80]
            connection.execute("UPDATE chat_conversations SET title=?,language=?,report_id=?,updated_at=? WHERE conversation_id=?",
                               (title, language, effective_report, timestamp, conversation_id))
            row = connection.execute("SELECT * FROM chat_turns WHERE turn_id=?", (turn_id,)).fetchone()
            return ChatReservation(True, self._chat_turn(connection, row))

    def finish_chat_turn(self, turn_id: str, content: str, *, estimated_cost_usd: Any | None = None,
                         cost_tracking_complete: bool = False, usage: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip() or len(content) > 80000:
            raise StoreError("invalid_chat_response", "The chat response could not be saved.", 502)
        actual = _micros(estimated_cost_usd) if cost_tracking_complete and estimated_cost_usd is not None else None
        timestamp = _stamp(self._now())
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM chat_turns WHERE turn_id=?", (turn_id,)).fetchone()
            if row is None:
                raise StoreError("chat_turn_not_found", "Chat turn not found.", 404)
            if row["status"] == "succeeded":
                return self._chat_turn(connection, row)
            if row["status"] != "pending":
                raise StoreError("chat_already_attempted", "This chat turn ended before its reply could be saved.", 409)
            sequence = connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM chat_messages WHERE conversation_id=?",
                                          (row["conversation_id"],)).fetchone()[0]
            connection.execute(
                "INSERT INTO chat_messages (message_id,conversation_id,turn_id,sequence,role,content,created_at) VALUES (?,?,?,?,'assistant',?,?)",
                ("msg_" + uuid4().hex, row["conversation_id"], turn_id, sequence, content, timestamp),
            )
            cost = actual if actual is not None else row["reserved_microusd"]
            basis = "usage_estimate" if actual is not None else "reservation_retained"
            if actual is not None and actual > row["reserved_microusd"]:
                basis = "usage_estimate_exceeds_reservation"
            connection.execute(
                """UPDATE chat_turns SET status='succeeded',error_code=NULL,finished_at=?,updated_at=?,
                accounted_microusd=?,cost_basis=?,usage_json=? WHERE turn_id=?""",
                (timestamp, timestamp, cost, basis, _json(usage or {}), turn_id),
            )
            connection.execute("UPDATE chat_conversations SET updated_at=? WHERE conversation_id=?",
                               (timestamp, row["conversation_id"]))
            row = connection.execute("SELECT * FROM chat_turns WHERE turn_id=?", (turn_id,)).fetchone()
            return self._chat_turn(connection, row)

    def fail_chat_turn(self, turn_id: str, error_code: str = "execution_failed") -> None:
        code = error_code if _ERROR.fullmatch(error_code or "") else "execution_failed"
        timestamp = _stamp(self._now())
        with self._transaction() as connection:
            connection.execute(
                """UPDATE chat_turns SET status='failed',error_code=?,finished_at=?,updated_at=?,
                cost_basis='reservation_retained' WHERE turn_id=? AND status='pending'""",
                (code, timestamp, timestamp, turn_id),
            )

    @staticmethod
    def _schedule(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["times"] = json.loads(item.pop("times_json"))
        item["enabled"] = bool(item["enabled"])
        item["timezone"] = "Asia/Dubai"
        return item

    @staticmethod
    def _validate_schedule(agent_id: str, task: str, times: list[str], enabled: bool, name: str, language: str = "ar") -> list[str]:
        from app.registry import get_agent
        from app.permissions import is_allowed

        Store._validate_language(language)
        agent = get_agent(agent_id)
        if agent is None:
            raise StoreError("unknown_agent", "Unknown agent.", 404)
        if not agent.get("enabled", False) or not is_allowed(agent_id, "actions", "submit_task"):
            raise StoreError("agent_disabled", "This agent cannot execute reports.", 403)
        if not isinstance(task, str) or not task.strip() or len(task) > 8000:
            raise StoreError("invalid_task", "Task must contain 1 to 8000 characters.")
        if not isinstance(times, list) or not 1 <= len(times) <= MAX_DAILY_SCHEDULE_SLOTS:
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
        if slots + len(times) > MAX_DAILY_SCHEDULE_SLOTS:
            raise StoreError("schedule_slot_limit", "Enabled schedules may contain at most 50 daily occurrences in total.", 409)

    def create_schedule(self, agent_id: str, task: str, times: list[str], *, enabled: bool = False, name: str = "", language: str = "ar", now: datetime | None = None) -> dict[str, Any]:
        times = self._validate_schedule(agent_id, task, times, enabled, name, language)
        schedule_id, timestamp = "sch_" + uuid4().hex, _stamp(self._now(now))
        with self._transaction() as connection:
            self._check_slots(connection, times, enabled)
            connection.execute(
                "INSERT INTO schedules (schedule_id,name,agent_id,task,times_json,enabled,language,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (schedule_id, name, agent_id, task, _json(times), int(enabled), language, timestamp, timestamp),
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

    def update_schedule(self, schedule_id: str, *, agent_id: str | None = None, task: str | None = None, times: list[str] | None = None, enabled: bool | None = None, name: str | None = None, language: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
            if row is None:
                raise StoreError("schedule_not_found", "Schedule not found.", 404)
            current = self._schedule(row)
            values = {"agent_id": agent_id, "task": task, "times": times, "enabled": enabled, "name": name, "language": language}
            current.update({key: value for key, value in values.items() if value is not None})
            updated_times = self._validate_schedule(current["agent_id"], current["task"], current["times"], current["enabled"], current["name"], current["language"])
            self._check_slots(connection, updated_times, current["enabled"], exclude=schedule_id)
            connection.execute(
                "UPDATE schedules SET name=?,agent_id=?,task=?,times_json=?,enabled=?,language=?,updated_at=? WHERE schedule_id=?",
                (current["name"], current["agent_id"], current["task"], _json(updated_times), int(current["enabled"]), current["language"], _stamp(self._now(now)), schedule_id),
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
                    claimed.append({"occurrence_key": key, "due_at": _stamp(due), "schedule_id": schedule["schedule_id"], "agent_id": schedule["agent_id"], "task": schedule["task"], "language": schedule["language"], "local_time": local_time})
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
