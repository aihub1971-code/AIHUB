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
        "task": record["task"],
        "language": record.get("language", response.get("language", "ar")),
        "idempotency_replayed": replayed,
        "accounting": {
            "reserved_cost_usd": record["reserved_cost_usd"],
            "estimated_cost_usd": record["estimated_cost_usd"],
            "cost_basis": record["cost_basis"],
            "cost_is_estimate": True,
            "local_date": record["local_date"],
            "reserved_newsdata_credits": record.get("reserved_newsdata_credits", 0),
            "newsdata_credits": record.get("accounted_newsdata_credits", 0),
            "newsdata_credit_basis": record.get("newsdata_credit_basis", "not_used"),
        },
    })
    if isinstance(record.get("pagination"), dict):
        metadata = response.get("pagination")
        response["pagination"] = {**(metadata if isinstance(metadata, dict) else {}), **record["pagination"]}
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
    continue_from_report_id: str | None = None,
) -> dict[str, Any]:
    """Reserve atomically, execute once without retries, and persist the result.

    An ambiguous external failure keeps the full reservation. An idempotency key
    is permanent for this database. Each NewsData continuation has a second,
    server-owned identity: a parent can have only one attempted next page even
    when concurrent callers use different keys.
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
    newsdata_page = agent_id == "iran-news"
    if continue_from_report_id is not None and not newsdata_page:
        raise HTTPException(status_code=422, detail={"code": "invalid_newsdata_continuation", "message": "Only saved NewsData Iran reports support continuation."})
    language_options = {"language": language} if language != "ar" else {}
    database = store or get_store()
    page_context = None
    try:
        identity = {}
        if continue_from_report_id is not None:
            page_context = database.continuation_context(continue_from_report_id, agent_id, task, language)
            identity = {"parent_report_id": continue_from_report_id, "page_index": page_context["page_index"]}
        previous = database.lookup_idempotency(idempotency_key, agent_id, task, **language_options, **identity)
        if previous is not None:
            return _replay(previous)
        if continue_from_report_id is not None:
            previous = database.lookup_newsdata_child(continue_from_report_id, agent_id, task, language,
                                                      idempotency_key=idempotency_key)
            if previous is not None:
                return _replay(previous)
        ready = runtime.agent_runtime_is_ready(agent_id) if newsdata_page else runtime.runtime_is_ready()
        if not ready:
            credential = "NEWSDATA_API_KEY" if newsdata_page else "OPENAI_API_KEY"
            code = "newsdata_not_configured" if newsdata_page else "runtime_not_configured"
            raise HTTPException(status_code=503, detail={"code": code, "message": f"Configure {credential} before running this report."})
        if newsdata_page and page_context is None:
            from app.newsdata import initial_context
            page_context = initial_context(now=database.clock())
        runtime_options = {**language_options, **({"newsdata_context": page_context} if newsdata_page else {})}
        estimate = dict(runtime.estimate_reservation(agent_id, task, **runtime_options))
        estimate["language"] = language
        # The runtime declares all three bounds as zero only for deterministic
        # local responses with no model/search calls. Free setup checks can be
        # saved without weakening the positive reservation gate for API work.
        no_external_calls = all(
            type(estimate.get(field)) is int and estimate[field] == 0
            for field in ("max_input_tokens", "max_output_tokens", "max_web_search_calls")
        )
        credit_reservation = 0
        if newsdata_page:
            if (estimate.get("provider") != "newsdata"
                    or type(estimate.get("max_newsdata_credits")) is not int or estimate["max_newsdata_credits"] != 1
                    or type(estimate.get("max_newsdata_calls")) is not int or estimate["max_newsdata_calls"] != 1):
                raise StoreError("invalid_credit_configuration", "A NewsData page requires exactly one reserved provider call and credit.", 503)
            credit_reservation = 1
        reservation = database.reserve_run(
            agent_id=agent_id, task=task,
            reservation_usd=estimate["estimated_cost_usd"],
            idempotency_key=idempotency_key, trigger=trigger,
            provider=estimate.get("provider", ""), model=estimate.get("model", ""),
            pricing_snapshot=estimate, allow_zero_cost=no_external_calls, **language_options,
            newsdata_credits=credit_reservation, newsdata_context=page_context,
        )
    except StoreError as error:
        raise _http_store_error(error) from None
    if not reservation.created:
        return _replay(reservation.report)
    report_id = reservation.report["report_id"]
    try:
        if newsdata_page:
            # The database assigns the first page's root ID before any paid
            # request. Runtime receives only the frozen server-side context.
            runtime_options["newsdata_context"] = database.get_newsdata_context(report_id)
        result = runtime.run_agent(agent_id, task, **runtime_options)
        if not isinstance(result, dict):
            raise ValueError("Runtime must return a report object")
        result["language"] = language
        private_context = result.pop("newsdata_context", None)
        record = database.finish_run(
            report_id, result,
            estimated_cost_usd=result.get("estimated_cost_usd"),
            cost_tracking_complete=result.get("cost_tracking_complete") is True,
            usage=result.get("usage") if isinstance(result.get("usage"), dict) else {},
            newsdata_credits_used=result.get("newsdata_credits_used"),
            credit_tracking_complete=result.get("credit_tracking_complete") is True,
            newsdata_context=private_context,
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
