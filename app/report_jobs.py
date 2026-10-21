"""Bounded background research jobs with permanent quota keys and durable leases.

Only this execution gate calls the research generator. A process restart may
resume unspent queue entries or local PDF work; ambiguous paid work is retained
as interrupted and is never automatically sent to the provider again.
"""
from __future__ import annotations

import logging
import json
import re
import sqlite3
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from fastapi import HTTPException

from app.config import get_setting
from app.permissions import require_capability
from app.registry import get_agent
from app.report_store import ReportStore, get_report_store
from app.report_errors import safe_diagnostics
from app.storage import StoreError


LOGGER = logging.getLogger(__name__)
_CODE = re.compile(r"^[a-z0-9_]{1,80}$")


def _estimate(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from app.research import estimate_reservation
    return estimate_reservation(*args, **kwargs)


def _generate(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from app.research import generate_report
    return generate_report(*args, **kwargs)


def _render(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from app.report_pdf import render_report_pdf
    return render_report_pdf(*args, **kwargs)


def _error_code(error: Exception, *, pdf: bool = False) -> str:
    code = getattr(error, "code", None)
    if isinstance(error, HTTPException) and isinstance(error.detail, dict):
        code = error.detail.get("code")
    if isinstance(code, str) and _CODE.fullmatch(code):
        return code
    return "pdf_generation_failed" if pdf else "report_generation_failed"


class ReportWorker:
    def __init__(self, report_store: ReportStore | None = None, *, workers: int | None = None,
                 lease_seconds: int = 120, poll_seconds: float = .5,
                 estimate: Callable[..., dict[str, Any]] | None = None,
                 generator: Callable[..., dict[str, Any]] | None = None,
                 pdf_renderer: Callable[..., dict[str, Any]] | None = None):
        self.report_store = report_store or get_report_store()
        if workers is None:
            try:
                workers = int(get_setting("REPORT_JOB_WORKERS", "1") or "1")
            except ValueError:
                raise StoreError("invalid_job_configuration", "REPORT_JOB_WORKERS must be 1 or 2.", 503) from None
        if (type(workers) is not int or not 1 <= workers <= 2 or not 30 <= lease_seconds <= 900
                or not .05 <= poll_seconds <= 10):
            raise StoreError("invalid_job_configuration", "Invalid research worker settings.", 503)
        self.workers, self.lease_seconds, self.poll_seconds = workers, lease_seconds, poll_seconds
        self.estimate, self.generator, self.pdf_renderer = estimate or _estimate, generator or _generate, pdf_renderer or _render
        self.worker_id = "worker_" + uuid4().hex
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if (get_setting("REPORT_WORKER_ENABLED", "true") or "true").strip().lower() in {"false", "0", "no"}:
            return
        with self._lock:
            if any(thread.is_alive() for thread in self._threads):
                return
            self.report_store.initialize()
            self.report_store.recover_expired()
            self._stop.clear()
            self._threads = [threading.Thread(target=self._loop, name=f"aihub-report-{index + 1}", daemon=True)
                             for index in range(self.workers)]
            for thread in self._threads:
                thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 30.0) -> bool:
        """Stop accepting work; bounded provider calls may finish during grace."""
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + min(max(timeout, 0), 60)
        for thread in tuple(self._threads):
            thread.join(max(0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in self._threads)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                # A database problem should not destroy the worker thread or
                # leak provider responses, topics or credentials into logs.
                LOGGER.warning("research_worker_iteration_failed")
                worked = False
            if not worked:
                self._wake.wait(self.poll_seconds)
                self._wake.clear()

    def run_once(self) -> bool:
        """Execute at most one durable job; also supports deterministic tests."""
        job = self.report_store.claim(self.worker_id, concurrency=self.workers, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        completed, lease_lost = threading.Event(), threading.Event()

        def heartbeat() -> None:
            while not completed.wait(min(10.0, self.lease_seconds / 3)):
                try:
                    if not self.report_store.heartbeat(job["job_id"], job["lease_token"], lease_seconds=self.lease_seconds):
                        lease_lost.set()
                        return
                except Exception:
                    lease_lost.set()
                    return

        pulse = threading.Thread(target=heartbeat, name="aihub-report-lease", daemon=True)
        pulse.start()
        try:
            self._execute(job, lease_lost)
        finally:
            completed.set()
            pulse.join(timeout=1)
        return True

    def _execute(self, job: dict[str, Any], lease_lost: threading.Event) -> None:
        reports, base = self.report_store, self.report_store.base_store
        job_id, token = job["job_id"], job["lease_token"]
        report_id = job["report_id"]
        ledger_saved = False
        pdf_phase = job["stage"] == "preparing_pdf"
        attempt_path = None
        current_stage = job["stage"]

        def progress(stage: str) -> None:
            nonlocal current_stage
            if lease_lost.is_set():
                raise StoreError("report_job_lease_lost", "The worker could not renew its lease.", 409)
            reports.set_stage(job_id, token, stage)
            current_stage = stage

        try:
            existing = base.lookup_idempotency(reports.ledger_key(job_id), job["agent_id"], job["topic"], job["language"])
            if existing is not None:
                report_id = existing["report_id"]
                reports.link_run(job_id, token, report_id)
                if existing["status"] != "succeeded" or not isinstance(existing.get("result"), dict):
                    # Reservation existence is sufficient to make a retry
                    # ambiguous, even when the browser never received an ID.
                    base.fail_run(report_id, "execution_interrupted")
                    reports.fail(job_id, token, "execution_interrupted", interrupted=True)
                    return
                ledger_saved = True
                # Restoring the immutable snapshot is still persistence work,
                # even when a recovered job was queued for its eventual PDF.
                # A failure here must not advertise a PDF retry for a report
                # that has not yet been saved in research_reports.
                pdf_phase = False
                saved = reports.save_report(job_id, token, report_id, existing["result"])
            else:
                if pdf_phase:
                    raise StoreError("report_snapshot_missing", "The saved report needed for PDF creation is unavailable.", 409)
                agent = get_agent(job["agent_id"])
                if agent is None or not agent.get("enabled"):
                    raise StoreError("agent_unavailable", "This agent is unavailable.", 403)
                require_capability(job["agent_id"], "actions", "submit_task")
                previous = reports.get_report(job["owner_id"], job["previous_report_id"])["result"] if job["previous_report_id"] else None
                options = {"language": job["language"], "research_window": {
                    "start": job["window_start"], "end": job["window_end"], "timezone": "UTC"},
                    "previous_report": previous}
                estimate = dict(self.estimate(job["agent_id"], job["topic"], **options))
                no_external_calls = all(type(estimate.get(field)) is int and estimate[field] == 0
                                        for field in ("max_input_tokens", "max_output_tokens", "max_web_search_calls"))
                if lease_lost.is_set():
                    raise StoreError("report_job_lease_lost", "The worker could not renew its lease.", 409)
                reservation = base.reserve_run(job["agent_id"], job["topic"], estimate["estimated_cost_usd"],
                    idempotency_key=reports.ledger_key(job_id), language=job["language"],
                    provider=estimate.get("provider", ""), model=estimate.get("model", ""),
                    pricing_snapshot=estimate, allow_zero_cost=no_external_calls)
                report_id = reservation.report["report_id"]
                reports.link_run(job_id, token, report_id)
                if not reservation.created:
                    # Another claim may have reached the ledger during a
                    # lease transition. Do not run its external operation.
                    reports.fail(job_id, token, "execution_interrupted", interrupted=True)
                    return
                # The generator reports only stages it actually performs;
                # user-text workspaces can go directly to analysis.
                raw_result = self.generator(job["agent_id"], job["topic"], **options, progress=progress)
                progress("saving")
                prepared = reports.prepare_result(job, report_id, raw_result)
                ledger_result = {**raw_result, **prepared}
                base.finish_run(report_id, ledger_result,
                    estimated_cost_usd=raw_result.get("estimated_cost_usd"),
                    cost_tracking_complete=raw_result.get("cost_tracking_complete") is True,
                    usage=raw_result.get("usage") if isinstance(raw_result.get("usage"), dict) else {})
                ledger_saved = True
                saved = reports.save_report(job_id, token, report_id, ledger_result)
            pdf_phase = True
            progress("preparing_pdf")
            attempt_path = reports.pdf_attempt_path(report_id, token)
            metadata = self.pdf_renderer(saved["result"], attempt_path)
            if lease_lost.is_set():
                raise StoreError("report_job_lease_lost", "The worker could not renew its lease.", 409)
            reports.finish_pdf(job_id, token, metadata)
        except Exception as error:
            code = _error_code(error, pdf=pdf_phase)
            diagnostics = safe_diagnostics(error, current_stage)
            if code == "report_job_lease_lost":
                return
            try:
                if ledger_saved and not pdf_phase and isinstance(error, (sqlite3.OperationalError, OSError)):
                    # A saved paid result is recoverable from runs.result_json;
                    # only transient local persistence can be attempted again.
                    # Bound recovery without advertising a misleading PDF-only
                    # retry. Paid research is not repeated.
                    if not reports.defer_saved_report(job_id, token):
                        reports.fail(job_id, token, "report_recovery_failed", diagnostics=diagnostics)
                else:
                    if report_id is not None and not ledger_saved:
                        base.fail_run(report_id, code)
                    reports.fail(job_id, token, code, pdf=pdf_phase, diagnostics=diagnostics)
            except Exception:
                # Keep the durable lease for conservative restart recovery.
                LOGGER.warning("research_failure_state_unavailable job_id=%s", job_id)
            LOGGER.warning("research_job_failed job_id=%s code=%s diagnostics=%s", job_id, code,
                           json.dumps(diagnostics, sort_keys=True, separators=(",", ":")))
        finally:
            if attempt_path is not None:
                try:
                    attempt_path.unlink(missing_ok=True)
                except OSError:
                    pass
