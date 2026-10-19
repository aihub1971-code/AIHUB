"""Opt-in daily scheduler for the single Render web service.

The thread dispatches one report at a time. Spread daily times across the day;
occurrences more than five minutes late are skipped rather than caught up in a
billable burst. SQLite claims and execution idempotency survive restarts.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable

from fastapi import HTTPException

from app.config import get_setting
from app.service import execute_report
from app.storage import Store, get_store, utc_now

LOGGER = logging.getLogger(__name__)


def scheduler_enabled() -> bool:
    return (get_setting("SCHEDULER_ENABLED", "false") or "false").lower() in {"1", "true", "yes"}


class Scheduler:
    def __init__(
        self,
        store: Store | None = None,
        execute_fn: Callable[..., dict[str, Any]] | None = None,
        *,
        poll_seconds: float = 15.0,
        clock: Callable[[], datetime] = utc_now,
        enabled: bool | None = None,
    ) -> None:
        self.store = store or get_store()
        self.execute_fn = execute_fn or execute_report
        self.poll_seconds = max(0.1, min(float(poll_seconds), 30.0))
        self.clock = clock
        self.enabled = enabled
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.is_running
            if not (scheduler_enabled() if self.enabled is None else self.enabled):
                return False
            self.store.initialize()
            self.store.recover_stale_runs(now=self.clock())
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="ai-hub-scheduler", daemon=False)
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop new dispatches and give the bounded in-flight request time to finish.

        A host may terminate sooner than the provider timeout. Its pending claim
        and reservation still survive and will not cause an automatic retry.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
            if thread.is_alive():
                LOGGER.info("scheduler_shutdown_waiting_for_inflight_report")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # Do not log provider exceptions or task text; future ticks must
                # continue even if a connection or database operation failed.
                LOGGER.warning("scheduler_tick_failed")
            self._stop.wait(self.poll_seconds)

    def tick(self, now: datetime | None = None) -> int:
        """Dispatch at most one occurrence; this method is deterministic in tests."""
        moment = now if now is not None else self.clock()
        self.store.recover_stale_runs(now=moment)
        claims = self.store.claim_due_occurrences(now=moment, limit=1)
        for claim in claims:
            if self._stop.is_set():
                self.store.finish_occurrence(claim["occurrence_key"], status="interrupted", error_code="shutdown_before_dispatch")
                continue
            report_id = None
            try:
                response = self.execute_fn(
                    claim["agent_id"], claim["task"],
                    idempotency_key=claim["occurrence_key"], trigger="scheduled", store=self.store,
                )
                report_id = response.get("report_id")
                self.store.finish_occurrence(claim["occurrence_key"], status="succeeded", report_id=report_id)
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, dict) else {}
                report_id = detail.get("report_id")
                state = "blocked" if error.status_code in {403, 409, 429, 503} else "failed"
                self.store.finish_occurrence(
                    claim["occurrence_key"], status=state, report_id=report_id,
                    error_code=detail.get("code", "scheduled_execution_failed"),
                )
            except Exception:
                self.store.finish_occurrence(claim["occurrence_key"], status="failed", report_id=report_id, error_code="scheduled_execution_failed")
                LOGGER.warning("scheduled_execution_failed schedule_id=%s", claim["schedule_id"])
        return len(claims)
