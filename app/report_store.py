"""Session-scoped research snapshots and durable work on the existing SQLite disk.

Research snapshots are immutable. PDF metadata and job state may advance, while
the shared ``Store`` remains the only quota and estimated-cost ledger.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4

from app.config import get_setting
from app.permissions import require_capability
from app.registry import get_agent
from app.storage import Store, StoreError, _aware, _json, _stamp, get_store


MAX_ACTIVE_PER_OWNER = 10
MAX_ACTIVE_GLOBAL = 50
MAX_LOCAL_RECOVERY_ATTEMPTS = 2
# A bounded 100-article multilingual inventory includes both report items and
# immutable source references. Keep the same input ceiling as the PDF worker.
MAX_REPORT_BYTES = 5_000_000
MAX_PDF_BYTES = 50_000_000
STAGES = ("queued", "searching", "checking_sources", "analyzing", "saving", "preparing_pdf", "ready")
_ERROR = re.compile(r"^[a-z0-9_]{1,80}$")
_REPORT_ID = re.compile(r"^rpt_[a-f0-9]{32}$")
_PRIVATE_RESULT_FIELDS = {
    "usage", "usage_stages", "estimated_cost_usd", "cost_tracking_complete", "cost_is_estimate",
    "accounting", "pricing_snapshot", "model", "max_input_tokens", "max_output_tokens",
    "max_web_search_calls", "max_model_calls", "reservation", "pricing_basis",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_jobs (
    job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    language TEXT NOT NULL CHECK(language IN ('ar','en')),
    request_hash TEXT NOT NULL,
    generation_seq INTEGER NOT NULL,
    job_type TEXT NOT NULL DEFAULT 'research' CHECK(job_type IN ('research','pdf_retry')),
    status TEXT NOT NULL CHECK(status IN ('queued','running','ready','failed','interrupted','pdf_failed')),
    stage TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    lease_token TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    report_id TEXT REFERENCES runs(report_id),
    previous_report_id TEXT REFERENCES runs(report_id),
    error_code TEXT,
    local_recovery_attempts INTEGER NOT NULL DEFAULT 0,
    UNIQUE(owner_id,agent_id,generation_seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS research_active_request_idx
    ON research_jobs(owner_id,request_hash)
    WHERE status IN ('queued','running') AND job_type='research';
CREATE INDEX IF NOT EXISTS research_queue_idx ON research_jobs(status,created_at);
CREATE TABLE IF NOT EXISTS research_job_keys (
    owner_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES research_jobs(job_id),
    PRIMARY KEY(owner_id,key_hash)
);
CREATE TABLE IF NOT EXISTS research_heads (
    owner_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    next_sequence INTEGER NOT NULL DEFAULT 0,
    published_sequence INTEGER NOT NULL DEFAULT 0,
    latest_job_id TEXT,
    report_id TEXT REFERENCES runs(report_id),
    PRIMARY KEY(owner_id,agent_id)
);
CREATE TABLE IF NOT EXISTS research_reports (
    report_id TEXT PRIMARY KEY REFERENCES runs(report_id),
    job_id TEXT NOT NULL UNIQUE REFERENCES research_jobs(job_id),
    owner_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    language TEXT NOT NULL,
    generation_seq INTEGER NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    pdf_status TEXT NOT NULL DEFAULT 'pending' CHECK(pdf_status IN ('pending','ready','failed')),
    pdf_path TEXT,
    pdf_sha256 TEXT,
    pdf_size_bytes INTEGER,
    pdf_page_count INTEGER,
    pdf_ready_at TEXT,
    pdf_error_code TEXT
);
CREATE INDEX IF NOT EXISTS research_reports_owner_idx
    ON research_reports(owner_id,agent_id,generation_seq DESC);
CREATE TABLE IF NOT EXISTS research_evidence (
    report_id TEXT NOT NULL REFERENCES research_reports(report_id),
    source_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY(report_id,source_id)
);
"""


def _safe_code(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and _ERROR.fullmatch(value) else fallback


def _window(start: str, end: str) -> dict[str, str]:
    return {"start": start, "end": end, "cutoff": end, "timezone": "UTC", "display_timezone": "Asia/Dubai"}


def _validate_owner(owner_id: str) -> None:
    if (not isinstance(owner_id, str) or not owner_id or len(owner_id) > 256
            or any(ord(char) < 32 for char in owner_id)):
        raise StoreError("invalid_report_owner", "A valid report session is required.", 401)


class ReportStore:
    def __init__(self, base_store: Store, *, max_active_per_owner: int | None = None,
                 max_active_global: int | None = None):
        max_active_per_owner = self._queue_limit(max_active_per_owner, "REPORT_MAX_ACTIVE_PER_OWNER",
                                                MAX_ACTIVE_PER_OWNER, 20)
        max_active_global = self._queue_limit(max_active_global, "REPORT_MAX_ACTIVE_GLOBAL",
                                             MAX_ACTIVE_GLOBAL, 100)
        self.base_store = base_store
        self.max_active_per_owner = max_active_per_owner
        self.max_active_global = max_active_global
        self.pdf_directory = base_store.path.parent / "reports"

    @staticmethod
    def _queue_limit(value: int | None, name: str, default: int, maximum: int) -> int:
        if value is None:
            try:
                value = int(get_setting(name, str(default)) or str(default))
            except (TypeError, ValueError):
                value = None
        if type(value) is not int or not 1 <= value <= maximum:
            raise StoreError("invalid_job_configuration", f"{name} must be an integer from 1 to {maximum}.", 503)
        return value

    def initialize(self) -> None:
        self.base_store.initialize()
        with self.base_store._connect() as connection:
            connection.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA + "\nCOMMIT;")
        with self.base_store._transaction() as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(research_jobs)")}
            if "error_diagnostics_json" not in columns:
                connection.execute("ALTER TABLE research_jobs ADD COLUMN error_diagnostics_json TEXT")
            if "local_recovery_attempts" not in columns:
                connection.execute("ALTER TABLE research_jobs ADD COLUMN local_recovery_attempts INTEGER NOT NULL DEFAULT 0")
        self.pdf_directory.mkdir(parents=True, exist_ok=True)

    def _now(self, now: datetime | None = None) -> datetime:
        return self.base_store._now(now)

    @staticmethod
    def ledger_key(job_id: str) -> str:
        return "research:" + job_id

    @staticmethod
    def _job(row: sqlite3.Row, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        fields = ("job_id", "agent_id", "topic", "language", "generation_seq", "status", "stage",
                  "created_at", "updated_at", "started_at", "finished_at", "report_id", "previous_report_id", "error_code")
        result = {name: row[name] for name in fields}
        from app.report_errors import public_error
        diagnostics = json.loads(row["error_diagnostics_json"] or "{}") if "error_diagnostics_json" in row.keys() else {}
        result["error"] = public_error(row["error_code"], row["job_id"], row["stage"], diagnostics)
        result["research_window"] = _window(row["window_start"], row["window_end"])
        result["research_cutoff"] = row["window_end"]
        result["pdf_status"] = None
        result["queue_position"] = None
        if connection is not None and row["status"] == "queued":
            # Only an aggregate position is public; no other visitor's job,
            # topic, identity or report is exposed by the shared queue.
            ahead = connection.execute("""SELECT COUNT(*) FROM research_jobs WHERE status='queued'
                AND (created_at<? OR (created_at=? AND job_id<?))""",
                (row["created_at"], row["created_at"], row["job_id"])).fetchone()[0]
            result["queue_position"] = ahead + 1
        if connection is not None and row["report_id"]:
            saved = connection.execute("SELECT pdf_status FROM research_reports WHERE report_id=?",
                                       (row["report_id"],)).fetchone()
            result["pdf_status"] = saved["pdf_status"] if saved else None
        return result

    @staticmethod
    def _report(row: sqlite3.Row, *, include_result: bool = True) -> dict[str, Any]:
        result = {name: row[name] for name in ("report_id", "job_id", "agent_id", "topic", "language",
                  "generation_seq", "created_at", "updated_at")}
        result["research_window"] = _window(row["window_start"], row["window_end"])
        result["research_cutoff"] = row["window_end"]
        result["status"] = "ready" if row["pdf_status"] == "ready" else "report_saved"
        result["pdf"] = {"status": row["pdf_status"], "size_bytes": row["pdf_size_bytes"],
                         "page_count": row["pdf_page_count"], "ready_at": row["pdf_ready_at"],
                         "error_code": row["pdf_error_code"]}
        if include_result:
            result["result"] = json.loads(row["payload_json"])
        return result

    def submit(self, owner_id: str, agent_id: str, topic: str, language: str = "ar", *,
               idempotency_key: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        _validate_owner(owner_id)
        agent = get_agent(agent_id)
        if agent is None:
            raise StoreError("unknown_agent", "Unknown agent.", 404)
        if not agent.get("enabled"):
            raise StoreError("agent_disabled", "This agent is unavailable.", 403)
        require_capability(agent_id, "actions", "submit_task")
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 8000:
            raise StoreError("invalid_topic", "Use a topic containing 1 to 8000 characters.", 422)
        Store._validate_language(language)
        Store._validate_key(idempotency_key)
        topic = topic.strip()
        fingerprint = hashlib.sha256(_json([agent_id, topic, language]).encode()).hexdigest()
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key is not None else None
        moment = self._now(now)
        timestamp = _stamp(moment)
        # Expired paid work becomes terminal before queue admission. This
        # housekeeping never replays a provider call or changes a frozen window.
        self.recover_expired(now=moment)
        with self.base_store._transaction() as connection:
            row = None
            if key_hash:
                key = connection.execute("SELECT * FROM research_job_keys WHERE owner_id=? AND key_hash=?",
                                         (owner_id, key_hash)).fetchone()
                if key is not None:
                    if key["request_hash"] != fingerprint:
                        raise StoreError("idempotency_conflict", "This request key belongs to a different report.", 409)
                    row = connection.execute("SELECT * FROM research_jobs WHERE job_id=?", (key["job_id"],)).fetchone()
            if row is None:
                row = connection.execute("""SELECT * FROM research_jobs WHERE owner_id=? AND request_hash=?
                    AND status IN ('queued','running') AND job_type='research'""", (owner_id, fingerprint)).fetchone()
            joined = row is not None
            if row is None:
                self._check_queue_capacity(connection, owner_id)
                connection.execute("INSERT OR IGNORE INTO research_heads(owner_id,agent_id) VALUES (?,?)", (owner_id, agent_id))
                head = connection.execute("SELECT * FROM research_heads WHERE owner_id=? AND agent_id=?", (owner_id, agent_id)).fetchone()
                sequence = head["next_sequence"] + 1
                previous = connection.execute("""SELECT report_id FROM research_reports
                    WHERE report_id=? AND owner_id=? AND topic=? AND language=?""",
                    (head["report_id"], owner_id, topic, language)).fetchone()
                job_id = "job_" + uuid4().hex
                connection.execute("""INSERT INTO research_jobs(job_id,owner_id,agent_id,topic,language,
                    request_hash,generation_seq,status,stage,window_start,window_end,created_at,updated_at,previous_report_id)
                    VALUES (?,?,?,?,?,?,?,'queued','queued',?,?,?,?,?)""",
                    (job_id, owner_id, agent_id, topic, language, fingerprint, sequence,
                     _stamp(moment - timedelta(hours=12)), timestamp, timestamp, timestamp,
                     previous["report_id"] if previous else None))
                connection.execute("UPDATE research_heads SET next_sequence=?,latest_job_id=? WHERE owner_id=? AND agent_id=?",
                                   (sequence, job_id, owner_id, agent_id))
                row = connection.execute("SELECT * FROM research_jobs WHERE job_id=?", (job_id,)).fetchone()
            if key_hash:
                connection.execute("INSERT OR IGNORE INTO research_job_keys VALUES (?,?,?,?)",
                                   (owner_id, key_hash, fingerprint, row["job_id"]))
            latest = self._latest_row(connection, owner_id, agent_id)
            return {"job": self._job(row, connection), "joined_existing": joined,
                    "last_successful_report": self._report(latest, include_result=False) if latest else None}

    def _check_queue_capacity(self, connection: sqlite3.Connection, owner_id: str) -> None:
        active = connection.execute("""SELECT COUNT(*) AS total,COALESCE(SUM(owner_id=?),0) AS owned
            FROM research_jobs WHERE status IN ('queued','running')""", (owner_id,)).fetchone()
        if active["owned"] >= self.max_active_per_owner:
            raise StoreError("report_queue_session_limit",
                f"This visitor already has {self.max_active_per_owner} reports queued or running. Wait for one to finish.", 429)
        if active["total"] >= self.max_active_global:
            raise StoreError("report_queue_full",
                f"The report queue has {self.max_active_global} reports queued or running. Try again after one finishes.", 429)

    @staticmethod
    def _latest_row(connection: sqlite3.Connection, owner_id: str, agent_id: str) -> sqlite3.Row | None:
        return connection.execute("""SELECT r.* FROM research_heads h JOIN research_reports r ON r.report_id=h.report_id
            WHERE h.owner_id=? AND h.agent_id=? AND r.owner_id=?""", (owner_id, agent_id, owner_id)).fetchone()

    def get_job(self, owner_id: str, job_id: str) -> dict[str, Any]:
        _validate_owner(owner_id)
        with self.base_store._connect() as connection:
            row = connection.execute("SELECT * FROM research_jobs WHERE owner_id=? AND job_id=?", (owner_id, job_id)).fetchone()
            if row is None:
                raise StoreError("report_job_not_found", "Report job not found.", 404)
            return self._job(row, connection)

    def get_report(self, owner_id: str, report_id: str) -> dict[str, Any]:
        _validate_owner(owner_id)
        with self.base_store._connect() as connection:
            row = connection.execute("SELECT * FROM research_reports WHERE owner_id=? AND report_id=?", (owner_id, report_id)).fetchone()
        if row is None:
            raise StoreError("report_not_found", "Report not found.", 404)
        return self._report(row)

    def list_jobs(self, owner_id: str, agent_id: str | None = None, active_only: bool = False,
                  limit: int = 20) -> list[dict[str, Any]]:
        _validate_owner(owner_id)
        query, parameters = "SELECT * FROM research_jobs WHERE owner_id=?", [owner_id]
        if agent_id is not None:
            query += " AND agent_id=?"
            parameters.append(agent_id)
        if active_only:
            query += " AND status IN ('queued','running')"
        with self.base_store._connect() as connection:
            rows = connection.execute(query + " ORDER BY created_at DESC,generation_seq DESC LIMIT ?",
                                      (*parameters, min(max(int(limit), 1), 100))).fetchall()
            return [self._job(row, connection) for row in rows]

    def owns(self, owner_id: str, report_id: str) -> bool:
        _validate_owner(owner_id)
        with self.base_store._connect() as connection:
            return connection.execute("SELECT 1 FROM research_jobs WHERE owner_id=? AND report_id=?",
                                      (owner_id, report_id)).fetchone() is not None

    def latest_report(self, owner_id: str, agent_id: str) -> dict[str, Any] | None:
        _validate_owner(owner_id)
        with self.base_store._connect() as connection:
            row = self._latest_row(connection, owner_id, agent_id)
        return self._report(row) if row else None

    def list_reports(self, owner_id: str, agent_id: str | None = None, limit: int = 20,
                     offset: int = 0) -> list[dict[str, Any]]:
        _validate_owner(owner_id)
        limit, offset = min(max(int(limit), 1), 100), max(int(offset), 0)
        query, parameters = "SELECT * FROM research_reports WHERE owner_id=?", [owner_id]
        if agent_id is not None:
            query += " AND agent_id=?"
            parameters.append(agent_id)
        with self.base_store._connect() as connection:
            rows = connection.execute(query + " ORDER BY created_at DESC,generation_seq DESC LIMIT ? OFFSET ?",
                                      (*parameters, limit, offset)).fetchall()
        return [self._report(row, include_result=False) for row in rows]

    def get_evidence(self, owner_id: str, report_id: str, source_ids: list[str] | None = None,
                     limit: int = 50) -> list[dict[str, Any]]:
        self.get_report(owner_id, report_id)
        with self.base_store._connect() as connection:
            rows = connection.execute("SELECT source_id,evidence_json FROM research_evidence WHERE report_id=? ORDER BY position",
                                      (report_id,)).fetchall()
        wanted = set(source_ids) if source_ids is not None else None
        return [json.loads(row["evidence_json"]) for row in rows if wanted is None or row["source_id"] in wanted][:min(max(int(limit), 1), 100)]

    def _internal_job(self, job_id: str) -> dict[str, Any]:
        with self.base_store._connect() as connection:
            row = connection.execute("SELECT * FROM research_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise StoreError("report_job_not_found", "Report job not found.", 404)
        return dict(row)

    @staticmethod
    def _leased(connection: sqlite3.Connection, job_id: str, token: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM research_jobs WHERE job_id=? AND lease_token=? AND status='running'",
                                 (job_id, token)).fetchone()
        if row is None:
            raise StoreError("report_job_lease_lost", "The report job belongs to another worker.", 409)
        return row

    def claim(self, worker_id: str, *, concurrency: int = 1, lease_seconds: int = 120,
              now: datetime | None = None) -> dict[str, Any] | None:
        if type(concurrency) is not int or not 1 <= concurrency <= 2 or not 30 <= lease_seconds <= 900:
            raise StoreError("invalid_job_configuration", "Invalid worker limits.", 503)
        moment = self._now(now)
        self.recover_expired(now=moment)
        with self.base_store._transaction() as connection:
            active = connection.execute("SELECT COUNT(*) FROM research_jobs WHERE status='running'").fetchone()[0]
            if active >= concurrency:
                return None
            row = connection.execute("SELECT * FROM research_jobs WHERE status='queued' ORDER BY created_at,job_id LIMIT 1").fetchone()
            if row is None:
                return None
            token = uuid4().hex
            connection.execute("""UPDATE research_jobs SET status='running',lease_token=?,lease_owner=?,lease_expires_at=?,
                updated_at=?,started_at=COALESCE(started_at,?) WHERE job_id=? AND status='queued'""",
                (token, worker_id, _stamp(moment + timedelta(seconds=lease_seconds)), _stamp(moment), _stamp(moment), row["job_id"]))
            return dict(connection.execute("SELECT * FROM research_jobs WHERE job_id=?", (row["job_id"],)).fetchone())

    def heartbeat(self, job_id: str, token: str, *, lease_seconds: int = 120) -> bool:
        moment = self._now()
        with self.base_store._transaction() as connection:
            result = connection.execute("""UPDATE research_jobs SET lease_expires_at=?,updated_at=?
                WHERE job_id=? AND lease_token=? AND status='running'""",
                (_stamp(moment + timedelta(seconds=lease_seconds)), _stamp(moment), job_id, token))
            return result.rowcount == 1

    def set_stage(self, job_id: str, token: str, stage: str) -> None:
        if stage not in STAGES or stage in {"queued", "ready"}:
            raise StoreError("invalid_report_stage", "Invalid report stage.", 503)
        with self.base_store._transaction() as connection:
            row = self._leased(connection, job_id, token)
            # A bounded follow-up discovery legitimately follows source checking.
            # Persistence and analysis still cannot return to provider work.
            followup_search = row["stage"] == "checking_sources" and stage == "searching"
            if STAGES.index(stage) < STAGES.index(row["stage"]) and not followup_search:
                raise StoreError("invalid_report_stage", "Report stages cannot move backwards.", 503)
            connection.execute("UPDATE research_jobs SET stage=?,updated_at=? WHERE job_id=?",
                               (stage, _stamp(self._now()), job_id))

    def link_run(self, job_id: str, token: str, report_id: str) -> None:
        with self.base_store._transaction() as connection:
            row = self._leased(connection, job_id, token)
            if row["report_id"] not in {None, report_id}:
                raise StoreError("report_job_conflict", "The job already has a different report.", 409)
            connection.execute("UPDATE research_jobs SET report_id=?,updated_at=? WHERE job_id=?",
                               (report_id, _stamp(self._now()), job_id))

    @staticmethod
    def prepare_result(job: dict[str, Any] | sqlite3.Row, report_id: str, result: dict[str, Any]) -> dict[str, Any]:
        try:
            if (not isinstance(result, dict) or result.get("schema_version") != 2
                    or result.get("agent_id") != job["agent_id"] or result.get("language") != job["language"]
                    or not isinstance(result.get("items"), list) or len(result["items"]) > 100
                    or not isinstance(result.get("sources"), list) or len(result["sources"]) > 200
                    or not isinstance(result.get("sections"), list)
                    or not isinstance(result.get("citation_mappings"), dict)):
                raise ValueError
            window = result["research_window"]
            start = _aware(datetime.fromisoformat(window["start"].replace("Z", "+00:00")))
            end = _aware(datetime.fromisoformat(window["end"].replace("Z", "+00:00")))
            if (end - start != timedelta(hours=12) or _stamp(start) != job["window_start"]
                    or _stamp(end) != job["window_end"]):
                raise ValueError
            ids = [source["id"] for source in result["sources"]]
            if (any(not isinstance(value, str) or not value or len(value) > 200 for value in ids)
                    or len(ids) != len(set(ids))):
                raise ValueError
            prepared = {key: value for key, value in result.items()
                        if key not in _PRIVATE_RESULT_FIELDS and not key.startswith("_")}
            prepared.update(report_id=report_id, job_id=job["job_id"], topic=job["topic"],
                            generation_seq=job["generation_seq"], research_window=_window(job["window_start"], job["window_end"]))
            if len(_json(prepared).encode()) > MAX_REPORT_BYTES:
                raise ValueError
            return prepared
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise StoreError("invalid_research_report", "The report did not satisfy its saved scope and format.", 502) from None

    def _save_report(self, connection: sqlite3.Connection, job: sqlite3.Row, report_id: str,
                     result: dict[str, Any], timestamp: str) -> None:
        payload = self.prepare_result(job, report_id, result)
        existing = connection.execute("SELECT job_id,owner_id FROM research_reports WHERE report_id=?", (report_id,)).fetchone()
        if existing is not None:
            if existing["job_id"] != job["job_id"] or existing["owner_id"] != job["owner_id"]:
                raise StoreError("report_job_conflict", "The report belongs to another job.", 409)
            return
        ledger = connection.execute("SELECT finished_at FROM runs WHERE report_id=?", (report_id,)).fetchone()
        created_at = ledger["finished_at"] if ledger and ledger["finished_at"] else timestamp
        payload["created_at"] = created_at
        connection.execute("""INSERT INTO research_reports(report_id,job_id,owner_id,agent_id,topic,language,
            generation_seq,window_start,window_end,created_at,updated_at,payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (report_id, job["job_id"], job["owner_id"], job["agent_id"], job["topic"], job["language"],
             job["generation_seq"], job["window_start"], job["window_end"], created_at, timestamp, _json(payload)))
        for position, source in enumerate(payload["sources"]):
            connection.execute("INSERT INTO research_evidence VALUES (?,?,?,?)", (report_id, source["id"], position, _json(source)))
        connection.execute("""UPDATE research_heads SET report_id=?,published_sequence=?
            WHERE owner_id=? AND agent_id=? AND published_sequence<?""",
            (report_id, job["generation_seq"], job["owner_id"], job["agent_id"], job["generation_seq"]))

    def save_report(self, job_id: str, token: str, report_id: str, result: dict[str, Any]) -> dict[str, Any]:
        timestamp = _stamp(self._now())
        with self.base_store._transaction() as connection:
            job = self._leased(connection, job_id, token)
            if job["report_id"] != report_id:
                raise StoreError("report_job_conflict", "The report reservation does not belong to this job.", 409)
            self._save_report(connection, job, report_id, result, timestamp)
            connection.execute("UPDATE research_jobs SET stage='preparing_pdf',updated_at=?,error_code=NULL WHERE job_id=?",
                               (timestamp, job_id))
            row = connection.execute("SELECT * FROM research_reports WHERE report_id=?", (report_id,)).fetchone()
            return self._report(row)

    def pdf_attempt_path(self, report_id: str, token: str) -> Path:
        if not _REPORT_ID.fullmatch(report_id) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise StoreError("invalid_pdf_path", "Invalid PDF output destination.", 503)
        directory = self.pdf_directory / ".pending"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{report_id}_{token}.pdf"

    def finish_pdf(self, job_id: str, token: str, metadata: dict[str, Any]) -> None:
        job = self._internal_job(job_id)
        report_id = job["report_id"]
        source = self.pdf_attempt_path(report_id, token).resolve()
        try:
            if Path(metadata["path"]).resolve() != source:
                raise ValueError
            data = source.read_bytes()
            size, pages = metadata["size_bytes"], metadata["page_count"]
            digest = hashlib.sha256(data).hexdigest()
            if (not data.startswith(b"%PDF-") or not 1 <= len(data) <= MAX_PDF_BYTES or type(size) is not int
                    or size != len(data) or type(pages) is not int or not 1 <= pages <= 500
                    or metadata["sha256"] != digest):
                raise ValueError
        except (ValueError, KeyError, TypeError, OSError):
            raise StoreError("invalid_report_pdf", "PDF creation did not produce a valid saved file.", 502) from None
        timestamp = _stamp(self._now())
        destination = self.pdf_directory / f"{report_id}.pdf"
        with self.base_store._transaction() as connection:
            self._leased(connection, job_id, token)
            # A stale worker cannot replace a newer worker's published file.
            os.replace(source, destination)
            connection.execute("""UPDATE research_reports SET pdf_status='ready',pdf_path=?,pdf_sha256=?,
                pdf_size_bytes=?,pdf_page_count=?,pdf_ready_at=?,pdf_error_code=NULL,updated_at=? WHERE report_id=?""",
                (str(destination), digest, size, pages, timestamp, timestamp, report_id))
            connection.execute("""UPDATE research_jobs SET status='ready',stage='ready',error_code=NULL,
                finished_at=?,updated_at=?,lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE job_id=?""",
                (timestamp, timestamp, job_id))

    def get_pdf_path(self, owner_id: str, report_id: str) -> Path:
        self.get_report(owner_id, report_id)
        with self.base_store._connect() as connection:
            row = connection.execute("SELECT pdf_status,pdf_path FROM research_reports WHERE owner_id=? AND report_id=?",
                                     (owner_id, report_id)).fetchone()
        path = self.pdf_directory / f"{report_id}.pdf"
        if row["pdf_status"] != "ready" or row["pdf_path"] != str(path) or not path.is_file():
            raise StoreError("report_pdf_not_ready", "The report is readable, but its PDF is not ready.", 409)
        return path

    def retry_pdf(self, owner_id: str, report_id: str) -> dict[str, Any]:
        _validate_owner(owner_id)
        moment = self._now()
        timestamp = _stamp(moment)
        self.recover_expired(now=moment)
        with self.base_store._transaction() as connection:
            report = connection.execute("SELECT * FROM research_reports WHERE owner_id=? AND report_id=?", (owner_id, report_id)).fetchone()
            if report is None:
                raise StoreError("report_not_found", "Report not found.", 404)
            job = connection.execute("SELECT * FROM research_jobs WHERE job_id=?", (report["job_id"],)).fetchone()
            joined = job["status"] in {"queued", "running"}
            if report["pdf_status"] == "ready" and Path(report["pdf_path"] or "").is_file():
                joined = True
            elif not joined:
                self._check_queue_capacity(connection, owner_id)
                connection.execute("""UPDATE research_jobs SET status='queued',stage='preparing_pdf',job_type='pdf_retry',
                    error_code=NULL,finished_at=NULL,updated_at=?,lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE job_id=?""",
                    (timestamp, job["job_id"]))
                connection.execute("UPDATE research_reports SET pdf_status='pending',pdf_error_code=NULL,updated_at=? WHERE report_id=?",
                                   (timestamp, report_id))
                job = connection.execute("SELECT * FROM research_jobs WHERE job_id=?", (job["job_id"],)).fetchone()
            return {"job": self._job(job, connection), "joined_existing": joined,
                    "last_successful_report": self._report(report, include_result=False)}

    def fail(self, job_id: str, token: str, code: str, *, pdf: bool = False, interrupted: bool = False,
             diagnostics: dict[str, Any] | None = None) -> None:
        timestamp = _stamp(self._now())
        with self.base_store._transaction() as connection:
            job = self._leased(connection, job_id, token)
            code = _safe_code(code, "pdf_generation_failed" if pdf else "report_generation_failed")
            if pdf and job["report_id"]:
                connection.execute("UPDATE research_reports SET pdf_status='failed',pdf_error_code=?,updated_at=? WHERE report_id=?",
                                   (code, timestamp, job["report_id"]))
            status = "pdf_failed" if pdf else "interrupted" if interrupted else "failed"
            connection.execute("""UPDATE research_jobs SET status=?,error_code=?,error_diagnostics_json=?,updated_at=?,finished_at=?,
                lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE job_id=?""",
                (status, code, _json(diagnostics or {}), timestamp, timestamp, job_id))

    def defer_saved_report(self, job_id: str, token: str) -> bool:
        """Bound local persistence recovery after a saved result; never repeat research."""
        with self.base_store._transaction() as connection:
            job = self._leased(connection, job_id, token)
            if job["local_recovery_attempts"] >= MAX_LOCAL_RECOVERY_ATTEMPTS:
                return False
            connection.execute("""UPDATE research_jobs SET status='queued',stage='preparing_pdf',updated_at=?,
                local_recovery_attempts=local_recovery_attempts+1,
                lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE job_id=?""", (_stamp(self._now()), job_id))
            return True

    def recover_expired(self, *, now: datetime | None = None) -> int:
        """Requeue unspent/local work; never replay an ambiguous paid operation."""
        timestamp = _stamp(self._now(now))
        recovered = 0
        with self.base_store._transaction() as connection:
            jobs = connection.execute("SELECT * FROM research_jobs WHERE status='running' AND lease_expires_at<=?", (timestamp,)).fetchall()
            for job in jobs:
                ledger = connection.execute("SELECT * FROM runs WHERE idempotency_key=?", (self.ledger_key(job["job_id"]),)).fetchone()
                report_id = ledger["report_id"] if ledger else job["report_id"]
                report = connection.execute("SELECT * FROM research_reports WHERE job_id=?", (job["job_id"],)).fetchone()
                status, stage, error = "queued", "queued", None
                if report is not None:
                    status, stage = ("ready", "ready") if report["pdf_status"] == "ready" else ("queued", "preparing_pdf")
                elif ledger is not None and ledger["status"] == "succeeded" and ledger["result_json"]:
                    try:
                        self._save_report(connection, job, ledger["report_id"], json.loads(ledger["result_json"]), timestamp)
                        stage = "preparing_pdf"
                    except (StoreError, ValueError, TypeError):
                        status, stage, error = "interrupted", job["stage"], "report_recovery_failed"
                elif ledger is not None:
                    status, stage, error = "interrupted", job["stage"], "execution_interrupted"
                    connection.execute("""UPDATE runs SET status='interrupted',error_code='execution_interrupted',
                        updated_at=?,finished_at=?,cost_basis='reservation_retained'
                        WHERE report_id=? AND status='pending'""", (timestamp, timestamp, report_id))
                connection.execute("""UPDATE research_jobs SET status=?,stage=?,error_code=?,report_id=?,updated_at=?,
                    finished_at=?,lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE job_id=?""",
                    (status, stage, error, report_id, timestamp, timestamp if status in {"ready", "interrupted"} else None, job["job_id"]))
                recovered += 1
        return recovered


def get_report_store(base_store: Store | None = None) -> ReportStore:
    """No process-global cache: tests and configured database paths stay isolated."""
    reports = ReportStore(base_store or get_store())
    reports.initialize()
    return reports
