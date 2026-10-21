"""Public research workspace. Identity always comes from the anonymous cookie.

Jobs, immutable reports and PDFs share the existing persistent database. Public
responses are explicit projections: provider usage, paths and accounting never
leave this boundary.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app import news_feed, public, runtime
from app.agent_profiles import get_profile, list_profiles
from app.registry import RETIRED_AGENT_IDS, get_agent
from app.report_store import get_report_store
from app.storage import StoreError, get_store


router = APIRouter(prefix="/v1/public/workspace", tags=["research workspace"])
REPORT_FIELDS = ("schema_version", "report_kind", "report_id", "agent_id", "agent_name", "topic", "language",
                 "kind", "title", "created_at", "previous_report_id", "response", "fresh_item_count",
                 "actual_article_count", "analyzed_article_count", "requested_article_target", "status", "factual_basis")
JOB_FIELDS = ("job_id", "agent_id", "topic", "language", "generation_seq", "status", "stage", "research_window",
              "research_cutoff", "created_at", "updated_at", "started_at", "finished_at", "report_id",
              "previous_report_id", "error_code", "error", "pdf_status", "queue_position")


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    topic: str = Field(default="", max_length=8000)
    language: Literal["ar", "en"] = "ar"


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewsRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["ar", "en"] = "ar"


def owner(session_id: str) -> str:
    return "public:" + session_id


def _pick(value: object, fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: value[key] for key in fields if key in value} if isinstance(value, dict) else {}


def _url(value: object) -> str | None:
    return runtime._safe_source_url(value)


def profile_view(value: dict[str, Any]) -> dict[str, Any]:
    """Profiles contain editorial instructions; only presentation fields are public."""
    result = _pick(value, ("id", "agent_id", "name", "name_ar", "name_en", "kind", "number", "enabled",
                         "description", "description_ar", "description_en", "default_topic_ar", "default_topic_en",
                         "view_labels", "views", "view_labels_ar", "view_labels_en", "uses_retrieval", "primary"))
    result["news_feed_enabled"] = news_feed.supports_agent(value.get("id", value.get("agent_id", "")))
    result["retrieval_options"] = ["newsdata", "web_search"]
    result["background_research_available"] = bool(value.get("allow_background"))
    return result


def require_agent(agent_id: str) -> dict[str, Any]:
    try:
        agent = get_agent(agent_id)
        profile = get_profile(agent_id)
    except (KeyError, ValueError):
        raise HTTPException(404, detail={"code": "agent_not_found"}) from None
    if not agent or not agent.get("enabled"):
        raise HTTPException(404, detail={"code": "agent_not_found"})
    return profile


def result_view(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = _pick(value, REPORT_FIELDS)
    result["research_window"] = _pick(value.get("research_window"),
        ("start", "end", "cutoff", "timezone", "display_timezone"))
    result["items"] = []
    for item in value.get("items", [])[:100]:
        if not isinstance(item, dict):
            continue
        entry = _pick(item, ("id", "headline", "publisher", "published_at", "published_date", "summary",
                             "why_it_matters", "source_ids", "section", "event_date", "classification", "source_kind",
                             "translation_status", "original_language", "original_headline", "original_summary",
                             "publisher_text_verified", "evidence_level"))
        entry["url"] = _url(item.get("url"))
        result["items"].append(entry)
    result["sections"] = [_pick(section, ("id", "title", "body", "source_ids", "classification"))
                          for section in value.get("sections", [])[:40] if isinstance(section, dict)]
    result["sources"] = []
    for source in value.get("sources", [])[:100]:
        if not isinstance(source, dict):
            continue
        entry = _pick(source, ("id", "title", "publisher", "published_at", "role", "source_kind", "excerpt",
                              "publisher_text_verified", "evidence_level", "original_language", "source_category"))
        entry["url"] = _url(source.get("url"))
        verification = source.get("publication_verification")
        if isinstance(verification, dict):
            entry["publication_verification"] = _pick(verification,
                ("method", "verified", "timestamp", "published_at", "evidence", "field", "timezone",
                 "path", "value", "retrieved_at", "label", "provider_metadata_validated"))
        elif isinstance(verification, str):
            entry["publication_verification"] = verification
        annotations = source.get("citation_annotations", [])
        entry["citation_annotations"] = []
        for annotation in annotations[:50] if isinstance(annotations, list) else []:
            if isinstance(annotation, dict):
                safe = _pick(annotation, ("type", "title", "start_index", "end_index"))
                safe["url"] = _url(annotation.get("url"))
                entry["citation_annotations"].append(safe)
        result["sources"].append(entry)
    result["citation_mappings"] = {
        str(source["id"]): {key: source.get(key) for key in ("url", "title", "published_at")}
        for source in result["sources"] if source.get("id") and source.get("url")}
    limitations = value.get("limitations", [])
    result["limitations"] = [text[:3000] for text in limitations[:30] if isinstance(text, str)]
    result["news_feed"] = _pick(value.get("news_feed"),
        ("provider", "status", "snapshot_id", "last_successful_refresh_at", "checked_at", "next_refresh_at",
         "actual_count", "maximum_items", "coverage_hours", "partial", "stale", "cached", "cache_age_seconds",
         "limitation_codes", "observed_latest_provider_publication_at", "observed_earliest_provider_publication_at", "provider_age_seconds",
         "suspected_provider_delay", "publication_basis", "publisher_text_verified"))
    if isinstance(value.get("news_feed"), dict):
        result["news_feed"]["provider_plan"] = _pick(value["news_feed"].get("provider_plan"),
            ("configured_plan", "plan_verified", "page_size", "documented_delay_hours", "real_time_confirmed",
             "configured_delivery_delay_hours", "free_plan_delay_hours", "paid_plan_realtime_supported", "plan_note"))
        result["news_feed"]["pagination"] = _pick(value["news_feed"].get("pagination"),
            ("pages_requested", "raw_results", "accepted_results", "stop_reason", "has_more", "page_size",
             "credits_accounted", "target_reached", "provider_total_results", "raw_count", "accepted_count",
             "requests", "max_requests", "next_page_available"))
    result["freshness"] = _pick(value.get("freshness"),
        ("window_start", "window_end", "coverage_complete", "timestamp_basis", "publisher_text_verified",
         "observed_latest_provider_publication_at", "provider_age_seconds", "suspected_provider_delay"))
    result["retrieval"] = _pick(value.get("retrieval"),
        ("providers_attempted", "selection_reason", "newsdata_raw_count", "cached_evidence_providers"))
    for provider in ("newsdata", "web_search"):
        if isinstance(value.get("retrieval"), dict):
            result["retrieval"][provider + "_error"] = _pick(value["retrieval"].get(provider + "_error"),
                ("code", "scope", "retry_after_seconds", "limit_kind"))
    if isinstance(value.get("freshness"), dict):
        result["freshness"]["snapshot_window"] = _pick(value["freshness"].get("snapshot_window"),
            ("start", "end", "cutoff", "timezone", "display_timezone"))
    visuals = value.get("visuals")
    if isinstance(visuals, dict):
        result["visuals"] = _pick(visuals, ("direction", "reference_source_ids", "caption", "origin"))
        result["visuals"]["palette"] = [_pick(color, ("name", "hex")) for color in visuals.get("palette", [])[:8]
                                         if isinstance(color, dict)]
    return result


def report_view(value: object, *, full: bool = True) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = _pick(value, ("report_id", "agent_id", "topic", "language", "kind", "title", "created_at", "updated_at",
                           "generation_seq", "job_id", "previous_report_id", "research_window", "research_cutoff",
                           "status", "fresh_item_count"))
    result["pdf"] = _pick(value.get("pdf"), ("status", "size_bytes", "page_count", "ready_at", "error_code"))
    if full and isinstance(value.get("result"), dict):
        result["result"] = result_view(value["result"])
    return result


def job_view(value: object) -> dict[str, Any]:
    return _pick(value, JOB_FIELDS)


def _wake(request: Request) -> None:
    worker = getattr(request.app.state, "report_worker", None)
    if worker is not None:
        worker.wake()


@router.get("/config")
def configuration(session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    profiles = [profile_view(value) for value in list_profiles()]
    return {"agents": profiles, "agent_count": len(profiles), "languages": ["ar", "en"],
            "coverage_hours": 12, "maximum_news_items": 50, "maximum_newsdata_articles_per_refresh": 50,
            "display_timezone": "Asia/Dubai",
            "news_enabled_agents": [profile["id"] for profile in profiles if profile["news_feed_enabled"]],
            "refresh_mode": news_feed.REFRESH_MODE, "news_refresh_interval_seconds": news_feed.REFRESH_INTERVAL_SECONDS,
            "chat_available": runtime.runtime_is_ready()}


def _news(agent_id: str, language: str, session_id: str, *, force: bool = False) -> dict[str, Any]:
    require_agent(agent_id)
    if not news_feed.supports_agent(agent_id):
        raise HTTPException(404, detail={"code": "news_feed_not_supported"})
    database = get_store()
    try:
        # GETs, including language changes and old clients' polling, only read
        # the cache. Only the explicit POST refresh route may fetch new news.
        return news_feed.get_feed(agent_id, language, refresh=force, force=force, database=database)
    except (StoreError, HTTPException) as error:
        raise public._safe_error(error, database, session_id) from None


@router.get("/agents/{agent_id}/news")
def live_news(agent_id: str, language: Literal["ar", "en"] = "ar",
              session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    return _news(agent_id, language, session_id)


@router.post("/agents/{agent_id}/news/refresh")
def refresh_news(agent_id: str, body: NewsRefreshRequest,
                 session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    return _news(agent_id, body.language, session_id, force=True)


@router.post("/agents/{agent_id}/reports", status_code=202)
def generate(agent_id: str, body: GenerateRequest, request: Request,
             idempotency_key: str = Header(min_length=1, max_length=200),
             session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    profile = require_agent(agent_id)
    database = get_store()
    try:
        topic = body.topic or profile["default_topic_" + body.language]
        submission = get_report_store(database).submit(owner(session_id), agent_id, topic, body.language,
                                                       idempotency_key=idempotency_key)
        _wake(request)
        return {"job": job_view(submission["job"]),
                "last_successful_report": report_view(submission.get("last_successful_report"), full=False),
                "joined_existing": bool(submission.get("joined_existing"))}
    except (StoreError, HTTPException) as error:
        raise public._safe_error(error, database, session_id) from None


@router.get("/agents/{agent_id}")
def agent_workspace(agent_id: str, session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    require_agent(agent_id)
    database = get_store()
    reports = get_report_store(database)
    try:
        jobs = reports.list_jobs(owner(session_id), agent_id=agent_id, active_only=False, limit=20)
        if isinstance(jobs, dict):
            jobs = jobs.get("jobs", [])
        jobs = sorted(jobs, key=lambda item: item.get("generation_seq", 0), reverse=True)
        active = next((job for job in jobs if job["status"] in {"queued", "running"}), None)
        return {"agent": profile_view(get_profile(agent_id)),
                "last_successful_report": report_view(reports.latest_report(owner(session_id), agent_id)),
                "active_job": job_view(active) if active else None,
                "last_job": job_view(jobs[0]) if jobs else None}
    except (StoreError, HTTPException) as error:
        raise public._safe_error(error, database, session_id) from None


@router.get("/jobs/{job_id}")
def job_status(job_id: str, session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    database = get_store()
    reports = get_report_store(database)
    try:
        job = reports.get_job(owner(session_id), job_id)
        if job is None:
            raise HTTPException(404, detail={"code": "job_not_found"})
        report = reports.get_report(owner(session_id), job["report_id"]) if job.get("pdf_status") else None
        return {"job": job_view(job), "report": report_view(report)}
    except (StoreError, HTTPException) as error:
        raise public._safe_error(error, database, session_id) from None


@router.get("/reports")
def history(agent_id: str | None = None, limit: int = Query(default=30, ge=1, le=100),
            session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    database = get_store()
    if agent_id is not None and agent_id not in RETIRED_AGENT_IDS:
        require_agent(agent_id)
    try:
        records = get_report_store(database).list_reports(owner(session_id), agent_id=agent_id, limit=limit)
        if isinstance(records, dict):
            records = records.get("reports", [])
        return {"reports": [report_view(value, full=False) for value in records]}
    except (StoreError, HTTPException) as error:
        raise public._safe_error(error, database, session_id) from None


@router.get("/reports/{report_id}")
def report_detail(report_id: str, session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    database = get_store()
    try:
        record = get_report_store(database).get_report(owner(session_id), report_id)
        if record is None:
            raise HTTPException(404, detail={"code": "report_not_found"})
        return report_view(record)
    except (StoreError, HTTPException) as error:
        raise public._safe_error(error, database, session_id) from None


@router.get("/reports/{report_id}/pdf")
def download_pdf(report_id: str, session_id: str = Depends(public.require_session)) -> FileResponse:
    database = get_store()
    try:
        reports = get_report_store(database)
        path = reports.get_pdf_path(owner(session_id), report_id)
        if path is None:
            raise HTTPException(409, detail={"code": "pdf_not_ready"})
        return FileResponse(path, media_type="application/pdf", filename=f"AI_HUB_{report_id}.pdf",
                            headers={"Cache-Control": "private, no-store"})
    except (StoreError, HTTPException) as error:
        raise public._safe_error(error, database, session_id) from None


@router.post("/reports/{report_id}/pdf/retry", status_code=202)
def retry_pdf(report_id: str, body: EmptyRequest, request: Request,
              session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    database = get_store()
    try:
        value = get_report_store(database).retry_pdf(owner(session_id), report_id)
        _wake(request)
        job = value.get("job", value) if isinstance(value, dict) else {}
        return {"job": job_view(job)}
    except (StoreError, HTTPException) as error:
        raise public._safe_error(error, database, session_id) from None
