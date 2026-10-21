"""AI HUB: public and owner agent workspaces, reports and bounded research."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import runtime as agent_runtime
from app import newsdata, news_feed
from app.auth import require_owner
from app.chat import router as chat_router
from app.public import router as public_router
from app.workspace import router as workspace_router
from app.owner_workspace import router as owner_workspace_router
from app.workspace_chat import router as workspace_chat_router
from app.workspace_chat import agent_router as agent_chat_router, owner_router as owner_chat_router
from app.report_jobs import ReportWorker
from app.report_store import get_report_store
from app.config import docs_enabled, get_setting
from app.permissions import require_capability
from app.registry import get_agent, get_agents, read_skill
from app.schemas import ScheduleCreate, ScheduleUpdate, RunRequest
from app.scheduler import Scheduler
from app.service import execute_report
from app.storage import StoreError, get_store

STATIC_ROOT = Path(__file__).resolve().parent / "static"
APP_VERSION = "2.1.6"


def runtime_is_ready() -> bool:
    return agent_runtime.runtime_is_ready()


def _agent_ready(agent_id: str) -> bool:
    return agent_runtime.agent_runtime_is_ready(agent_id) if agent_id == "iran-news" else runtime_is_ready()


@asynccontextmanager
async def lifespan(application: FastAPI):
    store = get_store()
    store.initialize()
    store.recover_stale_runs()
    scheduler = Scheduler(store=store)
    application.state.scheduler = scheduler
    scheduler.start()
    reports = get_report_store(store)
    worker = ReportWorker(report_store=reports)
    application.state.report_worker = worker
    worker.start()
    try:
        yield
    finally:
        worker.stop()
        scheduler.stop()


_docs = docs_enabled()
app = FastAPI(
    title="AI HUB", version=APP_VERSION,
    description="Arabic and English research, persistent reports, PDFs and connected discussion, with protected administration.",
    docs_url="/docs" if _docs else None, redoc_url=None,
    openapi_url="/openapi.json" if _docs else None, lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
app.mount("/fonts", StaticFiles(directory=STATIC_ROOT.parent / "assets" / "fonts"), name="fonts")
app.include_router(chat_router)
app.include_router(public_router)
app.include_router(workspace_router)
app.include_router(owner_workspace_router)
app.include_router(workspace_chat_router)
app.include_router(agent_chat_router)
app.include_router(owner_chat_router)


@app.exception_handler(StoreError)
async def storage_error_handler(_: Request, exc: StoreError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": {"code": exc.code, "message": exc.message}})


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", tags=["system"], response_class=HTMLResponse)
def root() -> HTMLResponse:
    return HTMLResponse(STATIC_ROOT.joinpath("workspace.html").read_text(encoding="utf-8"))


@app.get("/admin", tags=["system"], response_class=HTMLResponse)
def admin() -> HTMLResponse:
    return HTMLResponse(STATIC_ROOT.joinpath("index.html").read_text(encoding="utf-8"))


@app.get("/admin/agents", tags=["system"], response_class=HTMLResponse)
def owner_agents(_: str = Depends(require_owner)) -> HTMLResponse:
    html = STATIC_ROOT.joinpath("workspace.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("<body", '<body data-workspace-mode="owner"', 1),
                        headers={"Cache-Control": "private, no-store"})


@app.get("/health", tags=["system"])
def health() -> dict[str, object]:
    return {"status": "ok", "app": "AI HUB", "version": APP_VERSION,
            "stage": "ready" if runtime_is_ready() else "bootstrap"}


@app.get("/v1/agents", tags=["agents"])
def list_agents(_: str = Depends(require_owner)) -> dict[str, object]:
    agents = get_agents()
    return {
        "count": len(agents),
        "agents": [{
            "id": a["id"], "number": a["number"],
            "name": a.get("display_name_ar", a["name"]), "name_en": a["name"],
            "description": a.get("description", ""),
            "skill_ids": [s["id"] for s in a.get("skills", [])],
            "enabled": bool(a.get("enabled", False)),
            "runtime_ready": _agent_ready(a["id"]) and bool(a.get("enabled", False)),
        } for a in agents],
    }


@app.get("/v1/agents/{agent_id}", tags=["agents"])
def agent_detail(agent_id: str, _: str = Depends(require_owner)) -> dict[str, object]:
    agent = get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Unknown agent")
    require_capability(agent_id, "actions", "inspect")
    return {
        "id": agent["id"], "number": agent["number"],
        "name": agent.get("display_name_ar", agent["name"]),
        "description": agent.get("description", ""),
        "skills": [{"id": s["id"], "path": s["path"], "sha256": s["sha256"]}
                   for s in agent.get("skills", [])],
        "runtime_ready": _agent_ready(agent_id) and bool(agent.get("enabled", False)),
    }


@app.get("/v1/agents/{agent_id}/skills/{skill_id}", tags=["agents"])
def get_skill(agent_id: str, skill_id: str, _: str = Depends(require_owner)) -> dict[str, str]:
    require_capability(agent_id, "actions", "read_skill")
    content = read_skill(agent_id, skill_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Unknown skill for this agent")
    return {"agent_id": agent_id, "skill_id": skill_id, "content": content}


@app.post("/v1/agents/{agent_id}/run", tags=["reports"])
def submit_task(
    agent_id: str, request: RunRequest,
    idempotency_key: str | None = Header(default=None, max_length=200),
    _: str = Depends(require_owner),
) -> dict[str, object]:
    require_capability(agent_id, "actions", "submit_task")
    options = {"language": request.language} if request.language != "ar" else {}
    if request.continue_from_report_id is not None:
        options["continue_from_report_id"] = request.continue_from_report_id
    return execute_report(agent_id, request.task, idempotency_key=idempotency_key, trigger="manual", **options)


@app.get("/v1/usage", tags=["reports"])
def usage(_: str = Depends(require_owner)) -> dict[str, object]:
    return get_store().usage_summary()


@app.get("/v1/system", tags=["system"])
def system_status(_: str = Depends(require_owner)) -> dict[str, object]:
    openai = bool(get_setting("OPENAI_API_KEY"))
    scheduler = getattr(app.state, "scheduler", None)
    pricing_ready = False
    configuration_issue = None
    if runtime_is_ready():
        try:
            agent_runtime.estimate_reservation("historical-reading-companion", "اختبار إعداد النموذج")
            agent_runtime.estimate_reservation("geopolitical-morning", "أهم الأخبار اليوم")
            pricing_ready = True
        except HTTPException as exc:
            configuration_issue = exc.detail.get("code") if isinstance(exc.detail, dict) else "configuration_incomplete"
    newsdata_issue = None
    try:
        page_size = newsdata.page_size_setting()
    except HTTPException as exc:
        page_size = None
        newsdata_issue = exc.detail.get("code", "newsdata_configuration_incomplete")
    plan = (get_setting("NEWSDATA_PLAN", "unknown") or "unknown").lower()
    if plan not in {"free", "paid"}:
        plan = "unknown"
    news_ready = newsdata.is_ready()
    return {
        "app": "AI HUB", "version": APP_VERSION, "timezone": "Asia/Dubai",
        "news_items_per_report": None, "news_count_mode": "provider_page",
        "news_item_limit": newsdata.MAX_NEWSDATA_RESULTS, "allowed_source_domains": [],
        "iran_news": {
            "provider": "newsdata", "configured": news_ready,
            "query": newsdata.NEWSDATA_QUERY, "query_semantics": "iran AND war",
            "window_hours": 12, "source_language": "en", "page_size": page_size,
            "result_limit": newsdata.MAX_NEWSDATA_RESULTS,
            "plan": plan, "free_plan_delay_hours": 12,
            "translation_provider": "openai", "translation_configured": openai,
            "configuration_issue": newsdata_issue or (None if news_ready else "newsdata_not_configured"),
        },
        "other_news": {
            "provider": "openai", "configured": openai,
            "processing_limit": agent_runtime.MAX_NEWS_ITEMS,
            "allowed_source_domains": list(agent_runtime.ALLOWED_SOURCE_DOMAINS),
        },
        "model_configured": runtime_is_ready(),
        "provider": "openai" if openai else None,
        "model": get_setting("OPENAI_MODEL_NAME", agent_runtime.DEFAULT_OPENAI_MODEL),
        "search_configured": news_ready,
        "search_provider": "newsdata" if news_ready else None,
        "news_refresh_mode": news_feed.REFRESH_MODE,
        "pricing_configured": pricing_ready, "configuration_issue": configuration_issue,
        "scheduler_enabled": (get_setting("SCHEDULER_ENABLED", "false") or "").lower() in {"1", "true", "yes"},
        "scheduler_running": bool(scheduler and scheduler.is_running),
        "configuration_check_only": True,
    }


@app.get("/v1/reports", tags=["reports"])
def reports(
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, max_length=30),
    _: str = Depends(require_owner),
) -> dict[str, object]:
    return {"reports": get_store().list_reports(limit=limit, offset=offset, status=status),
            "limit": limit, "offset": offset}


def _report_or_404(report_id: str) -> dict:
    record = get_store().get_report(report_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown report")
    return record


@app.get("/v1/reports/{report_id}", tags=["reports"])
def report_detail(report_id: str, _: str = Depends(require_owner)) -> dict:
    return _report_or_404(report_id)


@app.get("/v1/reports/{report_id}/download", tags=["reports"])
def download_report(
    report_id: str, format: Literal["md", "json"] = "md",
    language: Literal["ar", "en"] | None = None,
    _: str = Depends(require_owner),
) -> Response:
    record = _report_or_404(report_id)
    if format == "json":
        content = json.dumps(record, ensure_ascii=False, indent=2)
        mime = "application/json"
    else:
        result = record.get("result") or {}
        localized = result.get("response_" + language) if language and result.get("provider") == "newsdata" else None
        content = "# AI HUB\n\n" + str(localized or result.get("response") or "لم تتوفر نتيجة مكتملة لهذا التقرير.")
        mime = "text/markdown"
    filename = "AI_HUB_Report_" + str(record["report_id"]) + "." + format
    return Response(content, media_type=mime,
                    headers={"Content-Disposition": 'attachment; filename="' + filename + '"'})


@app.get("/v1/schedules", tags=["schedules"])
def schedules(_: str = Depends(require_owner)) -> dict:
    return {"schedules": get_store().list_schedules(), "timezone": "Asia/Dubai"}


@app.post("/v1/schedules", tags=["schedules"], status_code=201)
def create_schedule(request: ScheduleCreate, _: str = Depends(require_owner)) -> dict:
    require_capability(request.agent_id, "actions", "submit_task")
    return get_store().create_schedule(**request.model_dump())


@app.patch("/v1/schedules/{schedule_id}", tags=["schedules"])
def update_schedule(schedule_id: str, request: ScheduleUpdate, _: str = Depends(require_owner)) -> dict:
    changes = request.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Supply a schedule field to change.")
    if "agent_id" in changes:
        require_capability(changes["agent_id"], "actions", "submit_task")
    return get_store().update_schedule(schedule_id, **changes)


@app.delete("/v1/schedules/{schedule_id}", tags=["schedules"], status_code=204)
def delete_schedule(schedule_id: str, _: str = Depends(require_owner)) -> Response:
    if not get_store().delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Unknown schedule")
    return Response(status_code=204)


@app.get("/v1/schedule-occurrences", tags=["schedules"])
def schedule_occurrences(
    limit: int = Query(default=100, ge=1, le=200),
    schedule_id: str | None = Query(default=None, max_length=100),
    _: str = Depends(require_owner),
) -> dict:
    return {"occurrences": get_store().list_occurrences(schedule_id=schedule_id, limit=limit),
            "timezone": "Asia/Dubai", "missed_grace_seconds": 300}
