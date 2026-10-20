"""Allowlisted diagnostics for report failures; never persist provider prose."""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:\[\]-]{1,160}$")
_RETRYABLE = frozenset({
    "openai_timeout", "openai_connection_error", "openai_rate_limited", "openai_unavailable",
    "newsdata_timeout", "newsdata_connection_error", "newsdata_rate_limited", "newsdata_unavailable",
    "report_queue_full", "report_queue_session_limit", "execution_interrupted", "provider_timeout",
    "pdf_generation_failed", "invalid_report_pdf", "report_recovery_failed",
})


def safe_diagnostics(error: Exception, stage: str) -> dict[str, Any]:
    detail = error.detail if isinstance(error, HTTPException) and isinstance(error.detail, dict) else {}
    if not detail and isinstance(getattr(error, "diagnostics", None), dict):
        detail = error.diagnostics
    feed_reference = getattr(error, "feed_error_reference", None)
    if isinstance(feed_reference, str) and re.fullmatch(r"news_[a-f0-9]{16}", feed_reference):
        detail = {**detail, "feed_error_reference": feed_reference}
    result: dict[str, Any] = {"stage": stage, "exception_type": type(error).__name__}
    for field in ("provider_code", "provider_type", "provider_param", "request_id", "response_id", "reason", "feed_error_reference"):
        value = detail.get(field)
        if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value):
            result[field] = value
    for field in ("provider_status", "native_action_count", "search_call_count", "allowed_native_actions",
                  "retry_after_seconds"):
        value = detail.get(field)
        if type(value) is int and 0 <= value <= 86400:
            result[field] = value
    return result


def public_error(code: str | None, reference: str, stage: str,
                 diagnostics: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not code:
        return None
    result = {"code": code, "reference": reference, "stage": stage, "retryable": code in _RETRYABLE}
    retry_after = (diagnostics or {}).get("retry_after_seconds")
    if type(retry_after) is int and 0 <= retry_after <= 86400:
        result["retry_after_seconds"] = retry_after
    return result
