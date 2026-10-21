"""Authenticated agent workspaces, sharing behavior with the public interface.

The owner identity comes only from HTTP Basic authentication. No browser-supplied
visitor or owner identifier is accepted, and public reports remain isolated.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app import news_feed, workspace
from app.auth import require_owner
from app.registry import RETIRED_AGENT_IDS
from app.report_store import get_report_store
from app.storage import StoreError, get_store


router = APIRouter(prefix="/v1/workspace", tags=["owner agent workspace"])


@router.get("/bootstrap")
def bootstrap(_: str = Depends(require_owner)) -> dict[str, Any]:
    return {"authenticated": True, "workspace": "owner"}


@router.get("/config")
def configuration(identity: str = Depends(require_owner)) -> dict[str, Any]:
    return workspace.configuration(identity)


def _feed(agent_id: str, language: str, *, refresh: bool = False) -> dict[str, Any]:
    workspace.require_agent(agent_id)
    if not news_feed.supports_agent(agent_id):
        raise HTTPException(404, detail={"code": "news_feed_not_supported"})
    return news_feed.get_feed(agent_id, language, refresh=refresh, force=refresh, database=get_store())


@router.get("/agents/{agent_id}/news")
def news(agent_id: str, language: Literal["ar", "en"] = "ar",
         _: str = Depends(require_owner)) -> dict[str, Any]:
    return _feed(agent_id, language)


@router.post("/agents/{agent_id}/news/refresh")
def refresh_news(agent_id: str, body: workspace.NewsRefreshRequest,
                 _: str = Depends(require_owner)) -> dict[str, Any]:
    return _feed(agent_id, body.language, refresh=True)


@router.get("/agents/{agent_id}")
def agent_workspace(agent_id: str, identity: str = Depends(require_owner)) -> dict[str, Any]:
    profile = workspace.require_agent(agent_id)
    reports = get_report_store(get_store())
    jobs = sorted(reports.list_jobs(identity, agent_id=agent_id, active_only=False, limit=20),
                  key=lambda job: job.get("generation_seq", 0), reverse=True)
    active = next((job for job in jobs if job["status"] in {"queued", "running"}), None)
    return {"agent": workspace.profile_view(profile),
        "last_successful_report": workspace.report_view(reports.latest_report(identity, agent_id)),
        "active_job": workspace.job_view(active) if active else None,
        "last_job": workspace.job_view(jobs[0]) if jobs else None}


@router.post("/agents/{agent_id}/reports", status_code=202)
def generate(agent_id: str, body: workspace.GenerateRequest, request: Request,
             idempotency_key: str = Header(min_length=1, max_length=200),
             identity: str = Depends(require_owner)) -> dict[str, Any]:
    profile = workspace.require_agent(agent_id)
    submission = get_report_store(get_store()).submit(identity, agent_id,
        body.topic or profile["default_topic_" + body.language], body.language, idempotency_key=idempotency_key)
    workspace._wake(request)
    return {"job": workspace.job_view(submission["job"]),
        "last_successful_report": workspace.report_view(submission.get("last_successful_report"), full=False),
        "joined_existing": bool(submission.get("joined_existing"))}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, identity: str = Depends(require_owner)) -> dict[str, Any]:
    reports = get_report_store(get_store())
    job = reports.get_job(identity, job_id)
    if job is None:
        raise HTTPException(404, detail={"code": "job_not_found"})
    report = reports.get_report(identity, job["report_id"]) if job.get("pdf_status") else None
    return {"job": workspace.job_view(job), "report": workspace.report_view(report)}


@router.get("/reports")
def history(agent_id: str | None = None, limit: int = Query(default=30, ge=1, le=100),
            identity: str = Depends(require_owner)) -> dict[str, Any]:
    if agent_id is not None and agent_id not in RETIRED_AGENT_IDS:
        workspace.require_agent(agent_id)
    records = get_report_store(get_store()).list_reports(identity, agent_id=agent_id, limit=limit)
    return {"reports": [workspace.report_view(record, full=False) for record in records]}


@router.get("/reports/{report_id}")
def report_detail(report_id: str, identity: str = Depends(require_owner)) -> dict[str, Any]:
    report = get_report_store(get_store()).get_report(identity, report_id)
    if report is None:
        raise HTTPException(404, detail={"code": "report_not_found"})
    return workspace.report_view(report)


@router.get("/reports/{report_id}/pdf")
def download_pdf(report_id: str, identity: str = Depends(require_owner)) -> FileResponse:
    path = get_report_store(get_store()).get_pdf_path(identity, report_id)
    if path is None:
        raise HTTPException(409, detail={"code": "pdf_not_ready"})
    return FileResponse(path, media_type="application/pdf", filename=f"AI_HUB_{report_id}.pdf",
                        headers={"Cache-Control": "private, no-store"})


@router.post("/reports/{report_id}/pdf/retry", status_code=202)
def retry_pdf(report_id: str, body: workspace.EmptyRequest, request: Request,
              identity: str = Depends(require_owner)) -> dict[str, Any]:
    result = get_report_store(get_store()).retry_pdf(identity, report_id)
    workspace._wake(request)
    return {"job": workspace.job_view(result.get("job", result))}
