"""One execution gate shared by authenticated manual and scheduled reports."""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException

from app import runtime
from app.permissions import require_capability
from app.registry import get_agent
from app.storage import Store, StoreError, get_store

LOGGER = logging.getLogger(__name__)
_SAFE_ERROR = re.compile(r"^[a-z0-9_]{1,80}$")


def _http_store_error(error: StoreError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})


def _response(record: dict[str, Any], *, replayed: bool = False) -> dict[str, Any]:
    response = dict(record.get("result") or {})
    response.update({
        "report_id": record["report_id"],
        "idempotency_replayed": replayed,
        "accounting": {
            "reserved_cost_usd": record["reserved_cost_usd"],
            "estimated_cost_usd": record["estimated_cost_usd"],
            "cost_basis": record["cost_basis"],
            "cost_is_estimate": True,
            "local_date": record["local_date"],
        },
    })
    return response


def _replay(record: dict[str, Any]) -> dict[str, Any]:
    if record["status"] == "succeeded":
        return _response(record, replayed=True)
    pending = record["status"] == "pending"
    raise HTTPException(status_code=409, detail={
        "code": "report_in_progress" if pending else "report_already_attempted",
        "message": "This request is already running." if pending else "This request was already attempted and will not be retried automatically.",
        "report_id": record["report_id"],
        "status": record["status"],
        "error_code": record.get("error_code"),
    })


def _failure_code(error: HTTPException) -> str:
    if isinstance(error.detail, dict):
        code = error.detail.get("code")
        if isinstance(code, str) and _SAFE_ERROR.fullmatch(code):
            return code
    if error.status_code == 429:
        return "provider_rate_limited"
    if error.status_code == 504:
        return "provider_timeout"
    if error.status_code == 503:
        return "provider_unavailable"
    return "provider_request_failed"


def execute_report(
    agent_id: str,
    task: str,
    idempotency_key: str | None = None,
    trigger: str = "manual",
    *,
    store: Store | None = None,
    language: str = "ar",
) -> dict[str, Any]:
    """Reserve atomically, execute once without retries, and persist the result.

    An ambiguous external failure keeps the full reservation. An idempotency key
    is permanent for this database: changing the key explicitly requests a new
    attempt, which remains subject to both quotas and budgets.
    """
    agent = get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_agent", "message": "Unknown agent."})
    require_capability(agent_id, "actions", "submit_task")
    if not agent.get("enabled", False):
        raise HTTPException(status_code=403, detail={"code": "agent_disabled", "message": "This agent is disabled."})
    if not isinstance(task, str) or not task.strip() or len(task) > 8000:
        raise HTTPException(status_code=422, detail={"code": "invalid_task", "message": "Task must contain 1 to 8000 characters."})
    if language not in {"ar", "en"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_language", "message": "Use ar or en."})
    language_options = {"language": language} if language != "ar" else {}
    database = store or get_store()
    try:
        previous = database.lookup_idempotency(idempotency_key, agent_id, task, **language_options)
        if previous is not None:
            return _replay(previous)
        if not runtime.runtime_is_ready():
            raise HTTPException(status_code=503, detail={"code": "runtime_not_configured", "message": "Configure OPENAI_API_KEY before running reports."})
        estimate = runtime.estimate_reservation(agent_id, task, **language_options)
        estimate["language"] = language
        # The runtime declares all three bounds as zero only for deterministic
        # local responses with no model/search calls. Free setup checks can be
        # saved without weakening the positive reservation gate for API work.
        no_external_calls = all(
            type(estimate.get(field)) is int and estimate[field] == 0
            for field in ("max_input_tokens", "max_output_tokens", "max_web_search_calls")
        )
        reservation = database.reserve_run(
            agent_id=agent_id, task=task,
            reservation_usd=estimate["estimated_cost_usd"],
            idempotency_key=idempotency_key, trigger=trigger,
            provider=estimate.get("provider", ""), model=estimate.get("model", ""),
            pricing_snapshot=estimate, allow_zero_cost=no_external_calls, **language_options,
        )
    except StoreError as error:
        raise _http_store_error(error) from None
    if not reservation.created:
        return _replay(reservation.report)
    report_id = reservation.report["report_id"]
    try:
        result = runtime.run_agent(agent_id, task, **language_options)
        if not isinstance(result, dict):
            raise ValueError("Runtime must return a report object")
        result["language"] = language
        record = database.finish_run(
            report_id, result,
            estimated_cost_usd=result.get("estimated_cost_usd"),
            cost_tracking_complete=result.get("cost_tracking_complete") is True,
            usage=result.get("usage") if isinstance(result.get("usage"), dict) else {},
        )
        return _response(record)
    except HTTPException as error:
        code = _failure_code(error)
        database.fail_run(report_id, error_code=code)
        # Never forward raw provider response bodies, connection strings, task
        # text or credentials in an exception. The stored ID supports review.
        detail = {
            "code": code, "report_id": report_id,
            "message": "The report could not be completed. The attempt and conservative cost reservation were retained.",
        }
        upstream_status = error.detail.get("provider_status") if isinstance(error.detail, dict) else None
        if type(upstream_status) is int and 400 <= upstream_status <= 599:
            detail["provider_status"] = upstream_status
        raise HTTPException(status_code=error.status_code, detail=detail) from None
    except Exception:
        # A persistence failure after a successful API call is also ambiguous.
        # Do not repeat the external request to reconstruct a lost response.
        try:
            database.fail_run(report_id, error_code="execution_failed")
        except Exception:
            LOGGER.error("report_failure_record_unavailable report_id=%s", report_id)
        LOGGER.warning("report_execution_failed report_id=%s", report_id)
        raise HTTPException(status_code=502, detail={
            "code": "execution_failed", "report_id": report_id,
            "message": "The report could not be completed. Review the saved attempt before requesting another run.",
        }) from None
