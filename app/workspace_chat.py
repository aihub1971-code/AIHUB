"""Automatic, session-scoped report retrieval for inline and main AI HUB chat.

Fresh research runs as a durable report job. A persisted message plan pins its
selected snapshots and job before the answer step, so retries, parallel clicks
and newer reports cannot silently change a previously submitted question.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app import chat, public, research, runtime
from app.agent_profiles import get_profile, list_profiles
from app.registry import RETIRED_AGENT_IDS, get_agent, get_agents
from app.report_store import ReportStore, get_report_store
from app.storage import Store, StoreError, get_store
from app.workspace import job_view, owner, require_agent, _wake


router = APIRouter(prefix="/v1/public/workspace/chat", tags=["research discussion"])
MAX_CONTEXT_REPORTS = 3
MAX_REPORT_CONTEXT_BYTES = 19000
MAX_CONTEXT_BYTES = 60000
_URL = re.compile(r"https?://[^\s<>\[\]{}\"']+", re.IGNORECASE)
_MARKDOWN_DESTINATION = re.compile(r"!?\[[^\]\r\n]*\]\(\s*<?([^\s<>]+)")
_COMPARE = re.compile(r"\bcompar|\bprevious\b|\bearlier\b|\bsince\b|قارن|مقارن|السابق|سابقة|سابقه|منذ", re.I)
_FRESH = re.compile(
    r"\bnow\b|\btoday\b|\bcurrent(?:ly)?\b|\blatest\b|\bfresh\b|\brecent\b|\bupdate(?:s|d)?\b|"
    r"\bsince\b|\bnew developments\b|what(?:'s| is) (?:happening|new)|ما الجديد|"
    r"الآن|الان|اليوم|آخر|اخر|الأحدث|الاحدث|مستجد|تحديث|منذ|حاليا|حالياً", re.I)
_SINCE = re.compile(r"\bsince\b|منذ", re.I)
_REPORT_MENTION = re.compile(r"\breports?\b|تقرير", re.I)
_DISCUSS_REPORT = re.compile(
    r"\bsummari[sz]e\b|\bexplain\b|\btranslate\b|\bdescribe\b|\bcritique\b|"
    r"\b(?:sources?|cutoff|conclusion|assessment)\b|لخص|لخّص|اشرح|فسر|فسّر|ترجم|ناقش|تقييم|مصادر", re.I)
_EXPLICIT_RESEARCH = re.compile(
    r"\bsearch\b|\bresearch\b|\blook up\b|\brefresh\b|\bregenerate\b|\bcheck for\b|"
    r"\bupdate (?:this |the |my |our )?report\b|ابحث|أعد البحث|اعد البحث|حدّث|تحديث التقرير", re.I)
_PLATFORM = re.compile(
    r"how (?:do|can) I (?:use|download|upload|open|select|change)|how (?:does|do) (?:ai hub|this platform)|"
    r"how (?:do|can) I (?:generate|create) (?:a |the )?(?:fresh |new )?report|"
    r"كيف (?:أستخدم|استخدم|أحمّل|احمل|أنزل|انزل|أفتح|افتح|أختار|اختار|أغير|اغير)|"
    r"كيف (?:أولد|اولد|أنشئ|انشئ|أصدر|اصدر) (?:تقرير|التقرير)|زر التحويل|تغيير اللغة|language (?:switch|button)", re.I)
_DOMAIN_TERMS = {
    "iran-news": ["iran", "iranian", "tehran", "hormuz", "irgc", "إيران", "ايران", "طهران", "هرمز", "إيراني", "ايراني"],
    "geopolitical-morning": ["geopolit", "global conflict", "global assessment", "world news", "international affairs", "جيوسياس", "جيو سياس", "الأخبار العالمية", "اخبار العالم", "التقييم العالمي"],
    "ubi-research": ["basic income", "ubi", "guaranteed income", "الدخل الأساسي", "الدخل الاساسي", "دخل مضمون"],
    "uae-eu-trade-talks": ["uae-eu", "eu–uae", "uae–eu", "eu-uae", "trade talks", "fta", "cepa", "مفاوضات التجارة", "التجارية الأوروبية", "الاتحاد الأوروبي", "الاتحاد الاوروبي"],
    "education-observatory": ["education", "school", "universit", "teachers", "skills policy", "تعليم", "مدارس", "جامع", "المعلمين", "مهارات"],
    "historical-reading-companion": ["historical", "history", "abbasid", "reading guide", "تاريخ", "عباسي", "القراءة التاريخية"],
    "story-moodboard": ["moodboard", "story", "character", "creative brief", "قصة", "قصص", "لوحة إبداعية", "لوحة ابداعية", "شخصيات", "خيال"],
    "economic-cultural-briefing": ["econom", "inflation", "interest rate", "tourism", "culture", "cultural", "creative economy", "اقتصاد", "ثقاف", "سياح", "تضخم", "استثمار", "أسواق", "اسواق"],
    "decision-briefing": ["decision brief", "decision criteria", "اتخاذ القرار", "معايير القرار"],
}
_ENGLISH_DOMAIN_STEMS = {"geopolit", "universit", "econom"}

_SCHEMA = """CREATE TABLE IF NOT EXISTS workspace_chat_requests (
    owner_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES chat_conversations(conversation_id),
    key_hash TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    job_id TEXT,
    answer_report_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(owner_id,conversation_id,key_hash)
)"""


class WorkspaceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=8000)
    language: Literal["ar", "en"] = "ar"
    agent_id: str | None = Field(default=None, max_length=100)
    report_id: str | None = Field(default=None, pattern=r"^rpt_[a-f0-9]{32}$")
    topic: str = Field(default="", max_length=8000)
    fresh_job_id: str | None = Field(default=None, max_length=100)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _tokens(value: str) -> set[str]:
    return {part.casefold() for part in re.findall(r"[\w\u0600-\u06ff]+", value) if len(part) > 2}


def route_agents(message: str, selected_agent: str | None = None) -> list[str]:
    lower = message.casefold()
    matches = []
    active_ids = {agent["id"] for agent in get_agents() if agent.get("enabled") is True}
    for index, (agent_id, terms) in enumerate(_DOMAIN_TERMS.items()):
        if agent_id not in active_ids:
            continue
        # English words must not match inside unrelated words (history/story,
        # Tirana/Iran). The three editorial stems deliberately allow suffixes.
        score = sum(1 for term in terms if (
            re.search(r"(?<!\w)" + re.escape(term) + (r"\w*" if term in _ENGLISH_DOMAIN_STEMS else r"(?!\w)"), lower)
            if term.isascii() else term in lower))
        if score:
            matches.append((-score, index, agent_id))
    routed = [value[2] for value in sorted(matches)]
    if selected_agent in active_ids:
        routed = [selected_agent] + [value for value in routed if value != selected_agent]
    return routed[:MAX_CONTEXT_REPORTS]


def needs_fresh_research(message: str, agent_id: str | None, has_report: bool) -> bool:
    agent = get_agent(agent_id) if agent_id else None
    if (agent is None or agent.get("enabled") is not True or _PLATFORM.search(message)
            or not get_profile(agent_id)["uses_retrieval"]):
        return False
    if not has_report or _SINCE.search(message):
        return True
    # "Summarize today's/latest report" discusses a saved snapshot; it does
    # not authorize another search. Explicit searches and "since" still do.
    if (_REPORT_MENTION.search(message) and _DISCUSS_REPORT.search(message)
            and not _EXPLICIT_RESEARCH.search(message)):
        return False
    return bool(_FRESH.search(message) or _EXPLICIT_RESEARCH.search(message))


def _previous(reports: ReportStore, owner_id: str, current: dict[str, Any]) -> dict[str, Any] | None:
    for candidate in reports.list_reports(owner_id, current["agent_id"], limit=30):
        if candidate["generation_seq"] < current["generation_seq"]:
            return reports.get_report(owner_id, candidate["report_id"])
    return None


def _make_plan(database: Store, reports: ReportStore, owner_id: str, conversation_id: str,
               body: WorkspaceMessage) -> dict[str, Any]:
    # Check conversation ownership before reading report context or starting work.
    snapshot = database.chat_snapshot(conversation_id, owner_id)
    if snapshot["conversation"]["pending_turn_id"]:
        raise StoreError("conversation_busy", "Another message is still being processed.", 409)
    selected_agent = body.agent_id
    selected = reports.get_report(owner_id, body.report_id) if body.report_id else None
    # A retired identity is valid only as the identity of an owned snapshot.
    # An inherited conversation binding is checked with the same owner scope.
    if selected_agent in RETIRED_AGENT_IDS and selected is None:
        inherited = snapshot["conversation"].get("report_id")
        if inherited and reports.owns(owner_id, inherited):
            previous = reports.get_report(owner_id, inherited)
            if previous["agent_id"] == selected_agent:
                selected = previous
    if selected_agent and selected and selected["agent_id"] != selected_agent:
        raise StoreError("report_agent_mismatch", "The selected report belongs to a different assistant.", 409)
    if selected_agent and not (selected and selected_agent in RETIRED_AGENT_IDS):
        require_agent(selected_agent)
    if selected:
        selected_agent = selected["agent_id"]
    agents = route_agents(body.message, selected_agent)
    if selected and selected_agent in RETIRED_AGENT_IDS:
        agents = [selected_agent, *agents][:MAX_CONTEXT_REPORTS]
    if not agents and not _PLATFORM.search(body.message) and snapshot["conversation"].get("report_id"):
        inherited = snapshot["conversation"]["report_id"]
        if reports.owns(owner_id, inherited):
            selected = reports.get_report(owner_id, inherited)
            agents = [selected["agent_id"]]
    primary_agent = agents[0] if agents else None
    if selected is None and primary_agent:
        selected = reports.latest_report(owner_id, primary_agent)
    context_ids = [selected["report_id"]] if selected else []
    for agent_id in agents[1:]:
        other = reports.latest_report(owner_id, agent_id)
        if other and other["report_id"] not in context_ids:
            context_ids.append(other["report_id"])
    if selected and _COMPARE.search(body.message) and len(context_ids) < MAX_CONTEXT_REPORTS:
        previous = _previous(reports, owner_id, selected)
        if previous and previous["report_id"] not in context_ids:
            context_ids.append(previous["report_id"])
    profile = get_profile(primary_agent) if primary_agent and primary_agent not in RETIRED_AGENT_IDS else None
    topic = body.topic or (selected.get("topic") if selected else None) or (
        profile["default_topic_" + body.language] if profile else "")
    return {"agent_ids": agents, "primary_agent_id": primary_agent, "report_ids": context_ids,
            "original_report_id": selected["report_id"] if selected else None, "topic": topic,
            "fresh": needs_fresh_research(body.message, primary_agent, bool(selected))}


def _binding(database: Store, reports: ReportStore, owner_id: str, conversation_id: str,
             key: str, body: WorkspaceMessage) -> tuple[dict[str, Any], str]:
    database.chat_snapshot(conversation_id, owner_id, history_messages=2)
    digest = hashlib.sha256(key.encode()).hexdigest()
    fingerprint = hashlib.sha256(_dump(body.model_dump(exclude={"fresh_job_id"})).encode()).hexdigest()
    with database._transaction() as connection:
        connection.execute(_SCHEMA)
        row = connection.execute("SELECT * FROM workspace_chat_requests WHERE owner_id=? AND conversation_id=? AND key_hash=?",
                                 (owner_id, conversation_id, digest)).fetchone()
    if row is None:
        plan = _make_plan(database, reports, owner_id, conversation_id, body)
        with database._transaction() as connection:
            connection.execute("""INSERT OR IGNORE INTO workspace_chat_requests
                (owner_id,conversation_id,key_hash,request_hash,plan_json,answer_report_id,created_at)
                VALUES (?,?,?,?,?,?,?)""", (owner_id, conversation_id, digest, fingerprint, _dump(plan),
                    plan["original_report_id"], datetime.now(timezone.utc).isoformat()))
            row = connection.execute("SELECT * FROM workspace_chat_requests WHERE owner_id=? AND conversation_id=? AND key_hash=?",
                                     (owner_id, conversation_id, digest)).fetchone()
    if row["request_hash"] != fingerprint:
        raise StoreError("idempotency_conflict", "The message key was already used for different content.", 409)
    return dict(row), digest


def _section_score(section: dict[str, Any], query: set[str], message: str) -> int:
    score = len(_tokens(str(section.get("title", "")) + " " + str(section.get("body", ""))) & query)
    sid = section.get("id")
    if sid == "executive_summary":
        score += 6
    if sid == "what_changed":
        score += 4
    if sid == "uae_implications" and re.search(r"uae|emirates|الإمارات|الامارات|أبوظبي|ابوظبي", message, re.I):
        score += 15
    if sid in {"analyst_assessment", "iran_assessment", "us_assessment"} and re.search(r"why|basis|conclu|لماذا|أساس|استنتاج|تقييم", message, re.I):
        score += 10
    return score


def _scoped_report(record: dict[str, Any], message: str) -> dict[str, Any]:
    value = record["result"]
    query = _tokens(message)
    context = {key: record.get(key) for key in ("report_id", "agent_id", "topic", "language", "created_at", "generation_seq")}
    context.update(title=value.get("title"), kind=value.get("kind"), research_window=value.get("research_window"),
                   limitations=value.get("limitations", [])[:8], sections=[], items=[], sources=[], truncated=False)
    sections = [section for section in value.get("sections", []) if isinstance(section, dict)]
    sections.sort(key=lambda section: -_section_score(section, query, message))
    for section in sections[:8]:
        context["sections"].append({"id": section.get("id"), "title": str(section.get("title", ""))[:200],
                                    "body": str(section.get("body", ""))[:2200], "source_ids": section.get("source_ids", [])[:30]})
    available_items = [item for item in value.get("items", []) if isinstance(item, dict)]
    ranked = sorted(available_items, key=lambda item: -len(_tokens(str(item.get("headline", "")) + " " + str(item.get("summary", ""))) & query))
    for item in ranked[:12]:
        context["items"].append({key: item.get(key) for key in
            ("id", "headline", "publisher", "published_at", "summary", "why_it_matters", "url", "source_ids", "event_date", "section")})
    relevant = {str(source_id) for block in context["items"] + context["sections"] for source_id in block.get("source_ids", [])}
    available_sources = [source for source in value.get("sources", []) if isinstance(source, dict)]
    available_sources.sort(key=lambda source: (str(source.get("id")) not in relevant,))
    for source in available_sources[:25]:
        url = runtime._safe_source_url(source.get("url"))
        if url:
            verification = source.get("publication_verification")
            if isinstance(verification, dict):
                verification = public._pick(verification,
                    ("method", "verified", "timestamp", "published_at", "evidence", "field", "timezone"))
            elif not isinstance(verification, str):
                verification = None
            context["sources"].append({"id": source.get("id"), "title": str(source.get("title", ""))[:300],
                "publisher": str(source.get("publisher", ""))[:160], "url": url, "published_at": source.get("published_at"),
                "role": source.get("role"), "source_kind": source.get("source_kind"),
                "excerpt": str(source.get("excerpt", ""))[:700],
                "publication_verification": verification})
    # Trim whole blocks, retaining their identity. Keep the sources referenced by
    # retained sections before adding less relevant evidence.
    while runtime._json_bytes(context) > MAX_REPORT_CONTEXT_BYTES:
        context["truncated"] = True
        if len(context["items"]) > 3:
            context["items"].pop()
        elif len(context["sections"]) > 3:
            context["sections"].pop()
        elif len(context["sources"]) > 8:
            context["sources"].pop()
        elif context["items"]:
            context["items"].pop()
        elif len(context["sections"]) > 1:
            context["sections"].pop()
        elif context["sources"]:
            context["sources"].pop()
        else:
            context["sections"] = []
            context["limitations"] = ["Context was reduced to fit the bounded discussion window."]
            break
    context["truncated"] = context["truncated"] or len(sections) > len(context["sections"]) or len(available_items) > len(context["items"])
    return context


def build_context(reports: ReportStore, owner_id: str, plan: dict[str, Any], message: str,
                  answer_report_id: str | None) -> dict[str, Any]:
    ids = ([answer_report_id] if answer_report_id else []) + plan["report_ids"]
    ids = list(dict.fromkeys(ids))[:MAX_CONTEXT_REPORTS]
    snapshots = [_scoped_report(reports.get_report(owner_id, report_id), message) for report_id in ids]
    context = {"report_id": answer_report_id, "report_ids": ids, "agent_ids": plan["agent_ids"],
        "research_cutoffs": [{"report_id": value["report_id"], "agent_id": value["agent_id"],
                              "cutoff": value["research_window"].get("end")} for value in snapshots],
        "fresh_research": bool(plan["fresh"]), "reports": snapshots,
        "truncated": any(value["truncated"] for value in snapshots),
        "evidence_note": "Only this visitor's selected dated reports. Older references are not fresh developments."}
    if runtime._json_bytes(context) > MAX_CONTEXT_BYTES:
        raise StoreError("chat_context_too_large", "The selected evidence exceeds the discussion limit.", 413)
    return context


def validate_answer_links(answer: str, context: dict[str, Any]) -> None:
    allowed = {source["url"] for report in context.get("reports", []) for source in report.get("sources", []) if source.get("url")}
    for destination in _MARKDOWN_DESTINATION.findall(answer):
        if not destination.lower().startswith(("https://", "http://")):
            raise HTTPException(502, detail={"code": "chat_source_mismatch", "message": "The answer contained an unverified source link."})
    for match in _URL.findall(answer):
        # Markdown closes its destination with ')'. A balanced parenthesis can
        # be part of a real publisher URL: try each short punctuation suffix,
        # preserving the source's own parentheses rather than stripping all.
        candidates = [match]
        for _ in range(16):
            if not candidates[-1] or candidates[-1][-1] not in ".,;:!?،؛)]}":
                break
            candidates.append(candidates[-1][:-1])
        if not any(runtime._safe_source_url(candidate) in allowed for candidate in candidates):
            raise HTTPException(502, detail={"code": "chat_source_mismatch", "message": "The answer contained an unverified source link."})


def platform_context() -> dict[str, Any]:
    return {"name": "AI HUB", "audience": "public", "workspace_version": 2,
        "supported_languages": ["ar", "en"], "timezone": "Asia/Dubai",
        "agents": [{key: profile[key] for key in ("id", "name_en", "name_ar", "kind")} for profile in list_profiles()],
        "workflow": "Choose an agent card to generate its report. Refine its topic and press Enter or Generate fresh report. Open the two content tabs, Download PDF, or discuss it in the attached chat. History opens saved snapshots without research.",
        "news": {"coverage_hours": 12, "maximum_items_after_validation_and_dedup": research.MAX_NEWS_ITEMS,
                 "source_scope": "Broad reputable and primary sources through OpenAI live web research, with verified publication times.",
                 "refresh": "On demand; exact cutoff is frozen at job creation. Not continuous monitoring."},
        "chat": "Report context is automatic and scoped to this visitor. Questions about now or since a report can start a fresh research job; saved snapshots remain unchanged.",
        "archives": "Saved reports from retired assistants remain available for reading, PDF download and discussion. Retired assistants cannot generate new reports or fresh research; discussions use the saved evidence and its original cutoff.",
        "language": "Use the Arabic/English switch in the header. UI switching does not generate or rewrite a report. New reports follow the chosen language. Chat can explain any saved report in either language.",
        "privacy": "Anonymous browser sessions isolate reports and conversations. Credentials, administration, costs and other visitors are unavailable.",
        "pdf": "Generated automatically from each structured report. A failed PDF can be retried without researching again."}


def response_view(value: dict[str, Any]) -> dict[str, Any]:
    result = public._pick(value, ("conversation_id", "turn_id", "idempotency_replayed"))
    result["conversation"] = public._conversation(value.get("conversation"))
    for key in ("user_message", "message"):
        result[key] = public._pick(value.get(key), ("id", "role", "content", "created_at", "sequence", "turn_id", "status", "error_code", "report_id"))
    context = value.get("context") if isinstance(value.get("context"), dict) else {}
    result["context"] = public._pick(context,
        ("report_id", "history_messages_included", "history_truncated", "report_truncated", "fresh_research"))
    for key in ("report_ids", "agent_ids"):
        values = context.get(key, [])
        result["context"][key] = [item for item in values[:MAX_CONTEXT_REPORTS] if isinstance(item, str)] if isinstance(values, list) else []
    cutoffs = context.get("research_cutoffs", [])
    result["context"]["research_cutoffs"] = [public._pick(item, ("report_id", "agent_id", "cutoff"))
        for item in cutoffs[:MAX_CONTEXT_REPORTS] if isinstance(item, dict)] if isinstance(cutoffs, list) else []
    return result


@router.post("/conversations/{conversation_id}/messages", response_model=None)
def send_message(conversation_id: str, body: WorkspaceMessage, request: Request,
                 idempotency_key: str = Header(min_length=1, max_length=200),
                 session_id: str = Depends(public.require_session)) -> dict[str, Any] | JSONResponse:
    database = get_store()
    reports = get_report_store(database)
    owner_id = owner(session_id)
    try:
        binding, digest = _binding(database, reports, owner_id, conversation_id, idempotency_key, body)
        plan = json.loads(binding["plan_json"])
        answer_report_id = binding["answer_report_id"]
        # Plans persisted before retirement may still request fresh work. Keep
        # an owned archived answer usable, without resubmitting its assistant.
        if plan["primary_agent_id"] in RETIRED_AGENT_IDS:
            if not answer_report_id or not reports.owns(owner_id, answer_report_id):
                require_agent(plan["primary_agent_id"])
            plan["fresh"] = False
        if plan["fresh"]:
            if body.fresh_job_id and not binding["job_id"]:
                raise StoreError("chat_research_mismatch", "This research job belongs to a different question.", 409)
            if not binding["job_id"]:
                submission = reports.submit(owner_id, plan["primary_agent_id"], plan["topic"], body.language,
                    idempotency_key="chat:" + hashlib.sha256((conversation_id + "\0" + idempotency_key).encode()).hexdigest())
                job_id = submission["job"]["job_id"]
                with database._transaction() as connection:
                    connection.execute("UPDATE workspace_chat_requests SET job_id=? WHERE owner_id=? AND conversation_id=? AND key_hash=? AND job_id IS NULL",
                                       (job_id, owner_id, conversation_id, digest))
                binding["job_id"] = job_id
                _wake(request)
            if body.fresh_job_id and body.fresh_job_id != binding["job_id"]:
                raise StoreError("chat_research_mismatch", "This research job belongs to a different question.", 409)
            job = reports.get_job(owner_id, binding["job_id"])
            if job.get("pdf_status"):
                answer_report_id = job["report_id"]
                with database._transaction() as connection:
                    connection.execute("UPDATE workspace_chat_requests SET answer_report_id=? WHERE owner_id=? AND conversation_id=? AND key_hash=?",
                                       (answer_report_id, owner_id, conversation_id, digest))
            elif job["status"] in {"failed", "interrupted"}:
                raise StoreError("chat_research_failed", "Fresh research could not be completed. The older report remains available.", 502)
            else:
                return JSONResponse(status_code=202, content={"status": "researching", "job": job_view(job),
                    "conversation_id": conversation_id, "agent_id": plan["primary_agent_id"]})
        elif body.fresh_job_id:
            raise StoreError("chat_research_mismatch", "This question did not request fresh research.", 409)
        context = build_context(reports, owner_id, plan, body.message, answer_report_id)
        value = chat.execute_chat_message(conversation_id, body.message, body.language, owner_id=owner_id,
            idempotency_key=public._scoped_key(session_id, "workspace:" + idempotency_key),
            report_id=answer_report_id, report_selected=True,
            store=public._SessionStore(database, session_id), platform_context=platform_context(), workspace_context=context)
        return response_view(value)
    except (HTTPException, StoreError) as error:
        raise public._safe_error(error, database, session_id) from None
