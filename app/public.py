"""Anonymous, session-isolated access to assistants and chat.

Existing execution services retain the shared quotas and accounting. This router
only owns session/report associations and explicitly redacts private metadata.
It never authorizes an existing owner-facing route.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from app import chat, runtime, service
from app.registry import get_agents
from app.schemas import RunRequest
from app.storage import Store, StoreError, get_store


router = APIRouter(prefix="/v1/public", tags=["public"])
SESSION_COOKIE = "aihub_public_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_CODE = re.compile(r"^[a-z0-9_]{1,80}$")
_REPORT_ID = re.compile(r"^rpt_[a-f0-9]{32}$")


def _error(code: str, status: int) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code,
                         "message": "The request is unavailable. Refresh the page or try again later."})


def _secure(request: Request) -> bool:
    # Render preserves Host and forwards the original HTTPS scheme. Never use
    # X-Forwarded-Host to decide an authorized origin.
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _check_origin(request: Request) -> None:
    mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
    site = request.headers.get("sec-fetch-site", "").lower()
    if site in {"cross-site", "same-site"}:
        raise _error("public_origin_rejected", 403)
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            scheme = "https" if _secure(request) else "http"
            expected = urlsplit(scheme + "://" + request.headers.get("host", ""))
            actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            expected_port = expected.port or (443 if scheme == "https" else 80)
            if (parsed.scheme != scheme or not parsed.hostname or parsed.hostname != expected.hostname
                    or actual_port != expected_port or parsed.username or parsed.password
                    or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
                raise ValueError
        except ValueError:
            raise _error("public_origin_rejected", 403) from None
    elif mutation and site != "same-origin":
        raise _error("public_origin_rejected", 403)
    if mutation and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise _error("public_json_required", 415)


def _initialize(database: Store) -> None:
    with database._transaction() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS public_sessions (
            session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS public_reports (
            report_id TEXT PRIMARY KEY REFERENCES runs(report_id),
            session_id TEXT NOT NULL REFERENCES public_sessions(session_id))""")


def _session(request: Request) -> str | None:
    value = request.cookies.get(SESSION_COOKIE, "")
    if not _TOKEN.fullmatch(value):
        return None
    database = get_store()
    _initialize(database)
    digest = hashlib.sha256(value.encode()).hexdigest()
    with database._connect() as connection:
        row = connection.execute("SELECT session_id FROM public_sessions WHERE token_hash=? AND expires_at>?",
                                 (digest, int(time.time()))).fetchone()
    return row["session_id"] if row else None


def require_session(request: Request) -> str:
    _check_origin(request)
    session_id = _session(request)
    if session_id is None:
        raise _error("public_session_required", 401)
    return session_id


def _owns(database: Store, session_id: str, report_id: object) -> bool:
    if not isinstance(report_id, str) or not _REPORT_ID.fullmatch(report_id):
        return False
    with database._connect() as connection:
        return connection.execute("SELECT 1 FROM public_reports WHERE session_id=? AND report_id=?",
                                  (session_id, report_id)).fetchone() is not None


def _require_report(database: Store, session_id: str, report_id: object) -> None:
    if not _owns(database, session_id, report_id):
        raise _error("report_not_found", 404)


def _bind_report(database: Store, session_id: str, report_id: str) -> None:
    with database._transaction() as connection:
        existing = connection.execute("SELECT session_id FROM public_reports WHERE report_id=?", (report_id,)).fetchone()
        if existing is not None and existing["session_id"] != session_id:
            raise StoreError("report_not_found", "Report not found.", 404)
        connection.execute("INSERT OR IGNORE INTO public_reports(report_id,session_id) VALUES (?,?)",
                           (report_id, session_id))


class _SessionStore:
    """Keep public ownership durable before execution without changing Store."""
    def __init__(self, database: Store, session_id: str):
        self.database, self.session_id = database, session_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self.database, name)

    def reserve_run(self, *args: Any, **kwargs: Any) -> Any:
        reservation = self.database.reserve_run(*args, **kwargs)
        _bind_report(self.database, self.session_id, reservation.report["report_id"])
        return reservation

    def lookup_idempotency(self, key: str | None, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        record = self.database.lookup_idempotency(key, *args, **kwargs)
        if record is not None and not _owns(self.database, self.session_id, record["report_id"]):
            # Recover a crash between reservation and association. Only the
            # original server-scoped request key can establish ownership;
            # a later continuation alias must never claim someone else's run.
            with self.database._connect() as connection:
                original = connection.execute("SELECT idempotency_key FROM runs WHERE report_id=?",
                                              (record["report_id"],)).fetchone()
            if not key or not key.startswith("public:") or original is None or original["idempotency_key"] != key:
                raise StoreError("report_not_found", "Report not found.", 404)
            _bind_report(self.database, self.session_id, record["report_id"])
        return record

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        _require_report(self.database, self.session_id, report_id)
        return self.database.get_report(report_id)


def _scoped_key(session_id: str, key: str) -> str:
    try:
        Store._validate_key(key)
    except StoreError:
        raise _error("invalid_idempotency_key", 422) from None
    return "public:" + hashlib.sha256((session_id + "\0" + key).encode()).hexdigest()


def _pick(data: object, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in fields if key in data
            and (data[key] is None or isinstance(data[key], (str, bool, int, float)))}


_PAGINATION = ("has_more", "page_index", "page_size", "parent_report_id", "root_report_id",
               "next_report_id", "next_report_status", "total_results", "result_limit", "results_before_page",
               "results_consumed", "limit_reached", "stop_reason", "repeated_cursor_stopped")
_ITEM = ("article_id", "source_id", "headline", "summary", "headline_en", "summary_en", "headline_ar",
         "summary_ar", "title", "description", "publisher", "publisher_id", "published_date", "pubDate",
         "pubDateTZ", "published_day_dubai", "classification", "source_kind", "original_language",
         "display_language", "translation_status", "summary_available", "event_date", "event_date_note",
         "event_timing_relative_to_window", "evidence_level", "publisher_text_verified", "date_basis",
         "relevance_basis", "published_date_note")


def _pagination(data: object, database: Store, session_id: str) -> dict[str, Any]:
    result = _pick(data, _PAGINATION)
    for key in ("parent_report_id", "root_report_id", "next_report_id"):
        if result.get(key) and not _owns(database, session_id, result[key]):
            result[key] = None
    return result


def _result(data: object, database: Store, session_id: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    result = _pick(data, ("report_id", "agent_id", "task", "language", "report_kind", "provider",
        "response", "response_ar", "response_en", "generated_at", "status", "item_count", "target_count",
        "shortfall", "bounded_item_limit", "count_mode", "fresh_event_count", "discarded_item_count",
        "translation_status", "publisher_text_verified", "evidence_level", "idempotency_replayed"))
    for section in ("items", "background_items"):
        if isinstance(data.get(section), list):
            result[section] = []
            for source in data[section][:50]:
                if not isinstance(source, dict):
                    continue
                item = _pick(source, _ITEM)
                for key in ("url", "source_url", "publisher_url"):
                    if key in source:
                        item[key] = runtime._safe_source_url(source[key])
                result[section].append(item)
    if isinstance(data.get("pagination"), dict):
        result["pagination"] = _pagination(data["pagination"], database, session_id)
    for name, fields in (
        ("freshness", ("mode", "window_start", "window_end", "timezone", "plan", "configured_delivery_delay_hours",
                       "free_plan_delay_hours", "coverage_complete", "note_ar", "note_en")),
        ("coverage_window", ("start", "end", "timezone")),
        ("newsdata", ("query", "query_semantics", "language", "endpoint", "returned_item_count",
                      "consumed_item_count", "provider_total_results")),
        ("translation", ("status", "translated_item_count", "original_only_item_count")),
    ):
        if isinstance(data.get(name), dict):
            result[name] = _pick(data[name], fields)
    return result


def _report(record: dict[str, Any], database: Store, session_id: str) -> dict[str, Any]:
    result = _pick(record, ("report_id", "agent_id", "task", "language", "status", "created_at", "updated_at"))
    result["result"] = _result(record["result"], database, session_id) if isinstance(record.get("result"), dict) else None
    if isinstance(record.get("pagination"), dict):
        result["pagination"] = _pagination(record["pagination"], database, session_id)
    return result


def _safe_error(error: HTTPException | StoreError, database: Store, session_id: str) -> HTTPException:
    status = error.status_code
    detail = error.detail if isinstance(error, HTTPException) and isinstance(error.detail, dict) else {}
    code = detail.get("code") if isinstance(error, HTTPException) else error.code
    sanitized = {"code": code if isinstance(code, str) and _CODE.fullmatch(code) else "public_request_failed",
                 "message": "The request could not be completed. Check its saved status before sending again."}
    for key in ("status", "error_code"):
        value = detail.get(key)
        if isinstance(value, str) and _CODE.fullmatch(value):
            sanitized[key] = value
    if _owns(database, session_id, detail.get("report_id")):
        sanitized["report_id"] = detail["report_id"]
    for key, prefix in (("conversation_id", "conv_"), ("turn_id", "cht_")):
        value = detail.get(key)
        if isinstance(value, str) and re.fullmatch(prefix + r"[a-f0-9]{32}", value):
            sanitized[key] = value
    return HTTPException(status_code=status, detail=sanitized)


def _agents() -> list[dict[str, Any]]:
    return [{"id": agent["id"], "number": agent.get("number", ""),
             "name": agent.get("display_name_ar", agent.get("name", "")), "name_en": agent.get("name", ""),
             "description": agent.get("description", ""), "enabled": agent.get("enabled") is True,
             "runtime_ready": runtime.agent_runtime_is_ready(agent["id"])} for agent in get_agents()]


def _platform_context() -> dict[str, Any]:
    return {"name": "AI HUB", "audience": "public", "supported_languages": ["ar", "en"],
        "agents": [{key: value for key, value in agent.items() if key != "runtime_ready"} for agent in _agents()],
        "sections": ["Search and assistants", "Chat with AI HUB"],
        "workflow": "Choose an assistant, write your request, and press Enter or Search. Shift+Enter inserts a new line. Discuss results opens Chat; then choose Attach displayed report to include your own current result in the conversation.",
        "languages": "Use العربية or English in the header. Iran-war pages retain English originals and saved Arabic translations; changing language does not search again. Unavailable translations keep the original text.",
        "iran_news": {"query": "iran war", "query_semantics": "iran AND war", "window_hours": 48,
            "result_limit": 20, "provider": "NewsData.io", "order": "newest first",
            "pagination": "Next page continues this search up to its first 20 provider results. Previous page reopens the saved page. Each attachment contains one page only; excluded invalid results can mean fewer displayed articles.",
            "freshness": "The provider's free plan has a 12-hour delay. Zero accepted results does not prove no relevant news exists."},
        "privacy": "You can discuss only this browser session's own results and conversations. There is no access to administration, other visitors' data, settings, accounts, or secret credentials.",
        "chat_capabilities": {"web_search": False, "execute_actions": False},
        "guidance": "Explain the public interface only. Never direct visitors to configuration, account setup, administration, schedules, usage dashboards or billing. Say a feature is temporarily unavailable when necessary; do not invent service settings or charges."}


def _conversation(value: object) -> dict[str, Any]:
    result = _pick(value, ("conversation_id", "title", "language", "report_id", "created_at", "updated_at",
                           "message_count", "pending_turn_id"))
    if isinstance(value, dict) and isinstance(value.get("messages"), list):
        result["messages"] = [_pick(message, ("id", "role", "content", "created_at", "sequence", "turn_id",
                                             "status", "error_code", "report_id")) for message in value["messages"]]
    if isinstance(value, dict) and isinstance(value.get("pagination"), dict):
        result["pagination"] = _pick(value["pagination"], ("has_more", "next_before"))
    return result


@router.get("/bootstrap")
def bootstrap(request: Request, response: Response) -> dict[str, Any]:
    _check_origin(request)
    session_id = _session(request)
    if session_id is None:
        database = get_store()
        _initialize(database)
        token, session_id, now = secrets.token_urlsafe(32), "ps_" + uuid4().hex, int(time.time())
        with database._transaction() as connection:
            connection.execute("INSERT INTO public_sessions VALUES (?,?,?,?)",
                               (session_id, hashlib.sha256(token.encode()).hexdigest(), now, now + SESSION_SECONDS))
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, path="/", httponly=True,
                            secure=_secure(request), samesite="strict")
    return {"agents": _agents(), "languages": ["ar", "en"], "chat_available": runtime.runtime_is_ready(),
            "iran_result_limit": 20}


@router.post("/agents/{agent_id}/run")
def run(agent_id: str, body: RunRequest, idempotency_key: str = Header(min_length=1, max_length=200),
        session_id: str = Depends(require_session)) -> dict[str, Any]:
    database = get_store()
    if body.continue_from_report_id is not None:
        _require_report(database, session_id, body.continue_from_report_id)
    try:
        result = service.execute_report(agent_id, body.task, _scoped_key(session_id, idempotency_key),
            language=body.language, continue_from_report_id=body.continue_from_report_id,
            store=_SessionStore(database, session_id))
        _require_report(database, session_id, result.get("report_id"))
        return _result(result, database, session_id)
    except (HTTPException, StoreError) as error:
        raise _safe_error(error, database, session_id) from None


@router.get("/reports/{report_id}")
def report_detail(report_id: str, session_id: str = Depends(require_session)) -> dict[str, Any]:
    database = get_store()
    _require_report(database, session_id, report_id)
    record = database.get_report(report_id)
    if record is None:
        raise _error("report_not_found", 404)
    return _report(record, database, session_id)


@router.get("/chat/conversations")
def conversations(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
                  session_id: str = Depends(require_session)) -> dict[str, Any]:
    database = get_store()
    try:
        return {"conversations": [_conversation(item) for item in database.list_chat_conversations(
                    "public:" + session_id, limit=limit, offset=offset)], "limit": limit, "offset": offset}
    except StoreError as error:
        raise _safe_error(error, database, session_id) from None


@router.post("/chat/conversations", status_code=201)
def create_conversation(body: chat.ConversationCreate, session_id: str = Depends(require_session)) -> dict[str, Any]:
    database = get_store()
    try:
        return _conversation(database.create_chat_conversation("public:" + session_id, **body.model_dump()))
    except StoreError as error:
        raise _safe_error(error, database, session_id) from None


@router.get("/chat/conversations/{conversation_id}")
def conversation_detail(conversation_id: str, limit: int = Query(default=100, ge=1, le=100),
                        before: int | None = Query(default=None, ge=1),
                        session_id: str = Depends(require_session)) -> dict[str, Any]:
    database = get_store()
    try:
        return _conversation(database.get_chat_conversation(conversation_id, "public:" + session_id,
                                                            limit=limit, before=before))
    except StoreError as error:
        raise _safe_error(error, database, session_id) from None


@router.post("/chat/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, body: chat.ChatMessageRequest,
                 idempotency_key: str = Header(min_length=1, max_length=200),
                 session_id: str = Depends(require_session)) -> dict[str, Any]:
    database, owner_id = get_store(), "public:" + session_id
    try:
        snapshot = database.chat_snapshot(conversation_id, owner_id)
        selected = body.report_id if "report_id" in body.model_fields_set else snapshot["conversation"]["report_id"]
        if selected is not None:
            _require_report(database, session_id, selected)
        result = chat.execute_chat_message(conversation_id, body.message, body.language,
            owner_id=owner_id, idempotency_key=_scoped_key(session_id, idempotency_key),
            report_id=body.report_id, report_selected="report_id" in body.model_fields_set,
            store=_SessionStore(database, session_id), platform_context=_platform_context())
        response = _pick(result, ("conversation_id", "turn_id", "idempotency_replayed"))
        response["conversation"] = _conversation(result.get("conversation"))
        for key in ("user_message", "message"):
            response[key] = _pick(result.get(key), ("id", "role", "content", "created_at", "sequence", "turn_id",
                                                     "status", "error_code", "report_id"))
        response["context"] = _pick(result.get("context"), ("report_id", "history_messages_included",
                                                            "history_truncated", "report_truncated"))
        return response
    except (HTTPException, StoreError) as error:
        raise _safe_error(error, database, session_id) from None
