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

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app import chat, public, research, runtime
from app.agent_profiles import get_profile, list_profiles
from app.auth import require_owner
from app.registry import RETIRED_AGENT_IDS, get_agent, get_agents
from app.report_store import ReportStore, get_report_store
from app.storage import Store, StoreError, get_store
from app.workspace import job_view, owner, require_agent, _wake


router = APIRouter(prefix="/v1/public/workspace/chat", tags=["research discussion"])
agent_router = APIRouter(prefix="/v1/public/workspace", tags=["agent conversations"])
owner_router = APIRouter(prefix="/v1/workspace", tags=["owner agent conversations"])
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
    r"\bsearch\b|\blook up\b|\brefresh\b|\bregenerate\b|\bcheck for\b|"
    r"^\s*research\b|\b(?:please|can you|could you|would you|let us|let's)\s+research\b|"
    r"\b(?:do|conduct|perform|undertake)\s+(?:(?:new|fresh|further|some)\s+)?research\b|"
    r"\bupdate (?:this |the |my |our )?report\b|ابحث|أعد البحث|اعد البحث|حدّث|تحديث التقرير", re.I)
_PLATFORM = re.compile(
    r"how (?:do|can) I (?:use|download|upload|open|select|change)|how (?:does|do) (?:ai hub|this platform)|"
    r"how (?:do|can) I (?:generate|create) (?:a |the )?(?:fresh |new )?report|"
    r"كيف (?:أستخدم|استخدم|أحمّل|احمل|أنزل|انزل|أفتح|افتح|أختار|اختار|أغير|اغير)|"
    r"كيف (?:أولد|اولد|أنشئ|انشئ|أصدر|اصدر) (?:تقرير|التقرير)|زر التحويل|تغيير اللغة|language (?:switch|button)", re.I)
_MEMORY_DISCUSS = re.compile(r"\bremember\b|\bmy preference|\bour conversation|\bwe discuss(?:ed)?\b|\bI (?:prefer|told|mentioned|said)\b|\b(?:another|other) agent\b|تذكر|تذكّر|أفضل أن|افضل ان|ناقشنا|محادثتنا", re.I)
_SELF_DESCRIPTION = re.compile(r"\bI (?:am|work)\b|أنا أعمل|انا اعمل|أعمل في|اعمل في", re.I)
_QUESTION_INTENT = re.compile(r"\b(?:what|which|how|why|when|where|compare|evaluate|assess|explain)\b|[?؟]|ما هي|ما الذي|كيف|قارن|قيّم", re.I)
_EVIDENCE_QUERY = re.compile(
    r"\bevidence\b|\bresearch\b|\bstud(?:y|ies)\b|\bprogrammes?\b|\bprograms?\b|\bevaluat|"
    r"\bscholar|\bhistori|\bhistory\b|\babbasid\b|\bbooks?\b|\bauthors?\b|\binterpretation|"
    r"\bpolicy\b|\bpolicies\b|\bemployment\b|\bfunding\b|\boutcomes?\b|\bcosts?\b|"
    r"\binterventions?\b|\bexperiments?\b|\bpoverty\b|\bexports?\b|\btariffs?\b|\bnegotiat|"
    r"\bcurricul|\bteachers?\b|\bskills?\b|\bdiploma|\beffects?\b|\bimpacts?\b|\bcompar|"
    r"أدلة|الأدلة|دراسة|دراسات|تقييم|أبحاث|ابحاث|بحث|مصادر|سياسة|سياسات|تأريخ|تاريخ|"
    r"كتب|كتاب|تفسير|عباسي|تمويل|تكلفة|تكاليف|نتائج|توظيف|مهارات|معلمين|مفاوضات|قارن", re.I)
_BACKGROUND_QUERY = re.compile(
    r"\b(?:background|older|historical|dated)\b|\bas of\b|\b(?:19|20)\d{2}\b|"
    r"خلفية|الخلفية|تاريخي|قديمة|القديم|المؤرخ|المؤرّخ", re.I)
_CONTEXTUAL_FOLLOWUP = re.compile(
    r"\b(?:this|that|these|those)\s+(?:evidence|findings?|conclusions?|results?|"
    r"stud(?:y|ies)|programmes?|programs?|trials?|pilots?|assessments?|reports?|interventions?)\b|"
    r"هذه (?:الأدلة|الادلة|الدراسة|النتائج|التجربة)|هذا (?:التقرير|التقييم|البرنامج)|آثارها|تكلفتها", re.I)
_PRONOUN_FOLLOWUP = re.compile(r"\b(?:its|their)\b", re.I)
_QUESTION_WORDS = set("""what which when where why how does did can could would should are was were has have
    the and for from with about into than that this these those their its them our your you they there
    please explain discuss describe compare summarize summarise evidence research scholarship analysis assessment finding findings
    result results implication implications limit limits limitation limitations report reports saved selected
    older historical dated background using based say says tell help understand show regarding respect
    now today current currently latest fresh recent new updates updated update since
    search lookup look refresh regenerate check investigate conduct perform create generate
    ما ماذا كيف متى أين اين لماذا هل الذي التي هذا هذه ذلك تلك عن على إلى الى من في مع بين
    اشرح شرح ناقش قارن لخص لخّص أدلة الادلة الأدلة تحليل التقييم تقرير التقرير المحفوظ
    خلفية الخلفية قديمة القديم المؤرخ المؤرخة النتائج وحدودها حدودها آثارها تكلفتها""".split())
_TOPIC_FACETS = """programme programs trial trials pilot pilots study studies evaluation evaluations evaluated
    funding funded cost costs employment unemployment effect effects impact impacts outcome outcomes finding findings
    policy policies evidence research assessment analysis result results intervention interventions experiment experiments
    change changed changes development developments compare comparison comparing difference differences benefit benefits
    limitation limitations implication implications fiscal finance financial spending public service services
    reading questions question framework lessons implementation design funding budget budgets summary brief briefing
    preparing looking want interested issue issues detail details additional further publication published original
    تكاليف تكلفة تكلفه تمويل توظيف آثار اثار أثر اثر نتائج برامج برنامج تجارب تجربة تجريبي تقييم
    دراسة دراسات أدلة الادلة بحث أبحاث ابحاث سياسات سياسة تغير تغيرات تطورات آثارها تكلفتها"""
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


class MemoryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["preference", "correction", "knowledge"]
    content: str = Field(min_length=1, max_length=2000)
    source_urls: list[str] = Field(default_factory=list, max_length=10)


class MemoryReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject"]


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
    if _MEMORY_DISCUSS.search(message) and not _EXPLICIT_RESEARCH.search(message):
        return False
    if (_SELF_DESCRIPTION.search(message) and not _EXPLICIT_RESEARCH.search(message)
            and not (_QUESTION_INTENT.search(message) and (_FRESH.search(message) or _EVIDENCE_QUERY.search(message)))):
        return False
    if _SINCE.search(message):
        return True
    # "Summarize today's/latest report" discusses a saved snapshot; it does
    # not authorize another search. Explicit searches and "since" still do.
    if (_REPORT_MENTION.search(message) and _DISCUSS_REPORT.search(message)
            and not _EXPLICIT_RESEARCH.search(message)):
        return False
    return bool(_FRESH.search(message) or _EXPLICIT_RESEARCH.search(message)
                or (not has_report and _EVIDENCE_QUERY.search(message)))


def _concepts(value: str) -> set[str]:
    """Conservative lexical coverage; a domain match alone is not evidence."""
    result = set()
    for term in _tokens(value):
        if term in _QUESTION_WORDS:
            continue
        if term.isascii():
            term = "programme" if term in {"program", "programs", "programme", "programmes"} else term
            if len(term) > 5 and term.endswith("ies"):
                term = term[:-3] + "y"
            elif len(term) > 4 and term.endswith("s") and not term.endswith("ss"):
                term = term[:-1]
        else:
            term = term.translate(str.maketrans("أإآى", "اااي"))
            if len(term) > 4 and term.startswith("ال"):
                term = term[2:]
        result.add(term)
    return result


def _report_covers_question(report: dict[str, Any] | None, message: str, agent_id: str,
                            now: datetime) -> bool:
    if report is None:
        return False
    # Explicit snapshot discussion and ordinary references to an earlier answer
    # can use a dated archive; they do not request another paid investigation.
    if _REPORT_MENTION.search(message) and _DISCUSS_REPORT.search(message):
        return True
    value = report.get("result") or {}
    if not value.get("sources") or value.get("status") in {"no_evidence", "awaiting_input"}:
        return False
    domain = _concepts(" ".join(_DOMAIN_TERMS.get(agent_id, [])))
    requested = _concepts(message) - domain
    text = [str(report.get("topic", "")), str(value.get("title", ""))]
    for section in value.get("sections", [])[:12]:
        text.append(str(section.get("body", ""))[:6000])
    for source in value.get("sources", [])[:50]:
        text.extend(str(source.get(field, ""))[:3000] for field in ("title", "excerpt", "published_at"))
    observed = _concepts(" ".join(text))
    # Every specific concept must be represented. Broad words such as costs or
    # employment must not hide an absent named programme, country or year.
    if not requested.issubset(observed):
        return False
    # A pronoun alone cannot turn a newly named country or programme into a
    # reference to the old report. Check all specific concepts first.
    if _CONTEXTUAL_FOLLOWUP.search(message) or _PRONOUN_FOLLOWUP.search(message):
        return True
    cutoff = research._timestamp((report.get("research_window") or {}).get("end"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age = (now - cutoff).total_seconds() if cutoff else None
    return bool(_BACKGROUND_QUERY.search(message) or age is not None and -300 <= age <= 12 * 3600)


def _topic_fits_question(topic: str, message: str, agent_id: str) -> bool:
    """Keep useful supplied scope, without overriding a new named subject.

    Older clients send the report form's topic automatically. A question about
    costs can use that scope; a question that introduces Finland cannot be
    constrained to a previously displayed Canada topic merely because both
    discuss costs and employment.
    """
    subjects = (_concepts(message) - _concepts(_TOPIC_FACETS)
                - _concepts(" ".join(_DOMAIN_TERMS.get(agent_id, []))))
    return subjects.issubset(_concepts(topic))


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
    bound_agent = snapshot["conversation"].get("agent_id")
    if bound_agent and body.agent_id and bound_agent != body.agent_id:
        raise StoreError("conversation_agent_mismatch", "This conversation belongs to a different agent.", 409)
    selected_agent = bound_agent or body.agent_id
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
        raise StoreError("report_agent_mismatch", "The selected report belongs to a different agent.", 409)
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
    if (primary_agent and primary_agent not in RETIRED_AGENT_IDS
            and _EVIDENCE_QUERY.search(body.message) and not _MEMORY_DISCUSS.search(body.message)
            and not _report_covers_question(selected, body.message, primary_agent, database.clock())):
        # The UI attaches its displayed report automatically. An independent
        # question can instead use a relevant dated background from this owner.
        # Explicit discussion of the displayed snapshot still stays pinned.
        for record in reports.list_reports(owner_id, primary_agent, limit=8):
            if selected and record["report_id"] == selected["report_id"]:
                continue
            candidate = reports.get_report(owner_id, record["report_id"])
            if _report_covers_question(candidate, body.message, primary_agent, database.clock()):
                selected = candidate
                break
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
    usable_report = bool(selected and primary_agent and _report_covers_question(
        selected, body.message, primary_agent, database.clock()))
    fresh = needs_fresh_research(body.message, primary_agent, usable_report)
    if fresh:
        supplied_topic = body.topic.strip()
        default_topic = profile and supplied_topic in {profile["default_topic_en"], profile["default_topic_ar"]}
        refers_to_snapshot = bool(_CONTEXTUAL_FOLLOWUP.search(body.message) or _PRONOUN_FOLLOWUP.search(body.message)
            or _REPORT_MENTION.search(body.message) and (_DISCUSS_REPORT.search(body.message) or _SINCE.search(body.message)))
        # A newly named subject must not inherit an unrelated old report's
        # topic. The agent profile already supplies the domain instructions.
        candidates = ([supplied_topic] if supplied_topic and not default_topic else [])
        if selected and selected.get("topic") and (refers_to_snapshot
                or _topic_fits_question("", body.message, primary_agent)):
            candidates.append(selected["topic"])
        base = next((candidate for candidate in candidates
            if _topic_fits_question(candidate, body.message, primary_agent)), "")
        topic = ((base[:5500] + "\nResearch question / سؤال البحث: " + body.message[:2400]).strip()
                 if base else body.message)
    return {"agent_ids": agents, "primary_agent_id": primary_agent, "report_ids": context_ids,
            "original_report_id": selected["report_id"] if selected else None, "topic": topic,
            "fresh": fresh}


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
                  answer_report_id: str | None, *, database: Store | None = None,
                  conversation_id: str | None = None) -> dict[str, Any]:
    ids = ([answer_report_id] if answer_report_id else []) + plan["report_ids"]
    ids = list(dict.fromkeys(ids))[:MAX_CONTEXT_REPORTS]
    shared = database.shared_workspace_context(owner_id, conversation_id, message) if database and conversation_id else None
    if shared:
        for previous in shared["conversations"]:
            report_id = previous.get("report_id")
            if report_id and report_id not in ids and len(ids) < MAX_CONTEXT_REPORTS and reports.owns(owner_id, report_id):
                ids.append(report_id)
    snapshots = [_scoped_report(reports.get_report(owner_id, report_id), message) for report_id in ids]
    context = {"report_id": answer_report_id, "report_ids": ids, "agent_ids": plan["agent_ids"],
        "research_cutoffs": [{"report_id": value["report_id"], "agent_id": value["agent_id"],
                              "cutoff": value["research_window"].get("end")} for value in snapshots],
        "fresh_research": bool(plan["fresh"]), "reports": snapshots,
        "truncated": any(value["truncated"] for value in snapshots),
        "evidence_note": "Only this visitor's selected dated reports. Older references are not fresh developments."}
    if shared:
        context["shared_memory"] = shared
        # The evidence budget already bounds each report. Prefer the current
        # report over distant chat excerpts when the combined window is full.
        while runtime._json_bytes(context) > MAX_CONTEXT_BYTES and shared["conversations"]:
            shared["conversations"].pop()
            shared["truncated"] = True
        while runtime._json_bytes(context) > MAX_CONTEXT_BYTES and shared["notes"]:
            shared["notes"].pop()
            shared["truncated"] = True
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
        "workflow": "Choose an agent card to open its saved workspace and its own conversation. Opening or switching agents never starts research. Select Refresh news or Generate fresh report for intentional retrieval. Discuss evidence directly in the agent chat; no manual attachment is required. History opens saved snapshots without research.",
        "news": {"coverage_hours": 12, "maximum_items_after_validation_and_dedup": research.MAX_NEWS_ITEMS,
                 "source_scope": "NewsData and OpenAI live web search are available to every active agent. NewsData collection is capped at 50 raw articles per intentional refresh; web search supplements empty, insufficient or unavailable coverage and retrieves official documents and scholarship.",
                 "refresh": "On demand; exact cutoff is frozen at job creation. Not continuous monitoring."},
        "chat": "Each agent has its own restored conversation. Relevant conversations, reviewed memory and cited reports are automatically shared across agents for this visitor only. Questions about now or explicit requests for research can start a fresh research job; saved snapshots remain unchanged. Responses stream as the model writes them.",
        "memory": "Visitors review preferences, corrections and knowledge notes before these become persistent shared memory. Approval records the visitor's decision; it does not turn a claim into independently verified evidence. Personal memory is retrieval context, not model training. Proposed or rejected notes are excluded.",
        "archives": "Saved reports from retired agents remain available for reading, PDF download and discussion. Retired agents cannot generate new reports or fresh research; discussions use the saved evidence and its original cutoff.",
        "language": "Use the Arabic/English switch in the header. UI switching does not generate or rewrite a report. New reports follow the chosen language. Chat can explain any saved report in either language.",
        "privacy": "Anonymous browser sessions isolate reports and conversations. Credentials, administration, costs and other visitors are unavailable.",
        "pdf": "Generated automatically from each structured report. A failed PDF can be retried without researching again."}


def response_view(value: dict[str, Any]) -> dict[str, Any]:
    result = public._pick(value, ("conversation_id", "turn_id", "idempotency_replayed"))
    result["conversation"] = _conversation_view(value.get("conversation") or {})
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


def _prepare_answer(conversation_id: str, body: WorkspaceMessage, request: Request,
                    idempotency_key: str, owner_id: str) -> tuple[Store, dict[str, Any], str | None] | JSONResponse:
    database = get_store()
    reports = get_report_store(database)
    binding, digest = _binding(database, reports, owner_id, conversation_id, idempotency_key, body)
    plan = json.loads(binding["plan_json"])
    answer_report_id = binding["answer_report_id"]
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
            raise HTTPException(502, detail={"code": "chat_research_failed", "cause_code": job.get("error_code"),
                "message": "Fresh research could not be completed. The older report remains available."})
        else:
            return JSONResponse(status_code=202, content={"status": "researching", "job": job_view(job),
                "conversation_id": conversation_id, "agent_id": plan["primary_agent_id"]})
    elif body.fresh_job_id:
        raise StoreError("chat_research_mismatch", "This question did not request fresh research.", 409)
    context = build_context(reports, owner_id, plan, body.message, answer_report_id,
                            database=database, conversation_id=conversation_id)
    return database, context, answer_report_id


_SAFE_MESSAGES = {
    "conversation_agent_mismatch": "This conversation belongs to a different agent. Open that agent's chat before sending.",
    "chat_source_mismatch": "The reply contained a source link that could not be matched to the saved evidence. The reply was not saved.",
    "chat_research_failed": "Fresh research failed. Check the specific research error; your saved reports remain available.",
    "chat_research_mismatch": "This research job belongs to a different question.",
    "daily_budget_limit": "AI HUB's application daily spending limit cannot cover this request. This is separate from provider quotas.",
    "monthly_budget_limit": "AI HUB's application monthly spending limit cannot cover this request. This is separate from provider quotas.",
    "public_daily_chat_limit": "This visitor has reached AI HUB's daily conversation allowance.",
    "openai_rate_limited": "OpenAI has rate-limited the request. Wait before trying again.",
    "openai_quota_exceeded": "OpenAI reported an account quota or credit limit. The owner must review the provider account.",
    "openai_timeout": "OpenAI timed out. Your message is saved; no automatic retry was made.",
    "openai_connection_error": "AI HUB could not reach OpenAI. No automatic retry was made.",
    "incomplete_model_response": "The response stopped before it was complete. Your message is saved; no automatic retry was made.",
    "stream_interrupted": "The connection ended before the reply completed. The partial reply was not saved as a complete answer.",
    "runtime_not_configured": "The AI provider is not configured on the server. Contact the platform owner.",
    "chat_in_progress": "This message is still being answered. Reopen the conversation to see its saved status.",
    "chat_already_attempted": "This message was already attempted. Check its saved status before starting a new attempt.",
    "conversation_busy": "Another message in this agent's conversation is still being answered.",
    "memory_not_found": "This memory note was not found in your workspace.",
    "memory_source_unverified": "A memory source must already appear in one of your saved reports.",
    "memory_limit": "Review or remove a saved memory note before adding another.",
}


def _safe_workspace_error(error: HTTPException | StoreError, database: Store,
                          session_id: str | None) -> HTTPException:
    raw = error.detail if isinstance(error, HTTPException) and isinstance(error.detail, dict) else {"code": error.code} if isinstance(error, StoreError) else {}
    if session_id:
        safe = public._safe_error(error, database, session_id)
    else:
        code = raw.get("code", "workspace_request_failed")
        safe = HTTPException(error.status_code, detail={"code": code if re.fullmatch(r"[a-z0-9_]{1,80}", str(code)) else "workspace_request_failed"})
    code = safe.detail.get("code", "workspace_request_failed")
    safe.detail["message"] = _SAFE_MESSAGES.get(code, "The request could not be completed. Check its error code and saved status before trying again.")
    if isinstance(raw.get("cause_code"), str) and re.fullmatch(r"[a-z0-9_]{1,80}", raw["cause_code"]):
        safe.detail["cause_code"] = raw["cause_code"]
    return safe


def _conversation_view(value: dict[str, Any]) -> dict[str, Any]:
    result = public._conversation(value)
    result["agent_id"] = value.get("agent_id")
    return result


def _answer(conversation_id: str, body: WorkspaceMessage, request: Request, key: str,
            owner_id: str, session_id: str | None, *, stream: bool = False):
    database = get_store()
    try:
        prepared = _prepare_answer(conversation_id, body, request, key, owner_id)
        if isinstance(prepared, JSONResponse):
            return prepared
        database, context, answer_report_id = prepared
        kwargs = dict(owner_id=owner_id,
            idempotency_key=public._scoped_key(session_id, "workspace:" + key) if session_id else "workspace:" + key,
            report_id=answer_report_id, report_selected=True,
            store=public._SessionStore(database, session_id) if session_id else database,
            platform_context=platform_context(), workspace_context=context)
        if not stream:
            return response_view(chat.execute_chat_message(conversation_id, body.message, body.language, **kwargs))
        def events():
            for event, value in chat.stream_chat_message(conversation_id, body.message, body.language, **kwargs):
                if event == "complete":
                    value = response_view(value)
                elif event == "error":
                    failure = _safe_workspace_error(HTTPException(value.pop("status_code", 502), detail=value), database, session_id)
                    value = {**failure.detail, "status_code": failure.status_code}
                yield "event: " + event + "\ndata: " + _dump(value) + "\n\n"
        return StreamingResponse(events(), media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"})
    except (HTTPException, StoreError) as error:
        raise _safe_workspace_error(error, database, session_id) from None


@router.post("/conversations/{conversation_id}/messages", response_model=None)
def send_message(conversation_id: str, body: WorkspaceMessage, request: Request,
                 idempotency_key: str = Header(min_length=1, max_length=200),
                 session_id: str = Depends(public.require_session)):
    return _answer(conversation_id, body, request, idempotency_key, owner(session_id), session_id)


@agent_router.get("/agents/{agent_id}/conversation")
def agent_conversation(agent_id: str, language: Literal["ar", "en"] = "ar",
                       session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    profile = require_agent(agent_id)
    return _conversation_view(get_store().agent_conversation(owner(session_id), agent_id, language=language,
        title=profile["name_" + language]))


@owner_router.get("/agents/{agent_id}/conversation")
def owner_agent_conversation(agent_id: str, language: Literal["ar", "en"] = "ar",
                             owner_id: str = Depends(require_owner)) -> dict[str, Any]:
    profile = require_agent(agent_id)
    return _conversation_view(get_store().agent_conversation(owner_id, agent_id, language=language,
        title=profile["name_" + language]))


@agent_router.get("/conversations/{conversation_id}")
def agent_conversation_detail(conversation_id: str, limit: int = Query(default=100, ge=1, le=100),
                              before: int | None = Query(default=None, ge=1),
                              session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    database = get_store()
    try:
        return _conversation_view(database.get_chat_conversation(conversation_id, owner(session_id), limit=limit, before=before))
    except StoreError as error:
        raise _safe_workspace_error(error, database, session_id) from None


@owner_router.get("/conversations/{conversation_id}")
def owner_agent_conversation_detail(conversation_id: str, limit: int = Query(default=100, ge=1, le=100),
                                    before: int | None = Query(default=None, ge=1),
                                    owner_id: str = Depends(require_owner)) -> dict[str, Any]:
    database = get_store()
    try:
        return _conversation_view(database.get_chat_conversation(conversation_id, owner_id, limit=limit, before=before))
    except StoreError as error:
        raise _safe_workspace_error(error, database, None) from None


@agent_router.post("/conversations/{conversation_id}/messages/stream", response_model=None)
def stream_message(conversation_id: str, body: WorkspaceMessage, request: Request,
                   idempotency_key: str = Header(min_length=1, max_length=200),
                   session_id: str = Depends(public.require_session)):
    return _answer(conversation_id, body, request, idempotency_key, owner(session_id), session_id, stream=True)


@owner_router.post("/conversations/{conversation_id}/messages/stream", response_model=None)
def stream_owner_message(conversation_id: str, body: WorkspaceMessage, request: Request,
                         idempotency_key: str = Header(min_length=1, max_length=200),
                         owner_id: str = Depends(require_owner)):
    return _answer(conversation_id, body, request, idempotency_key, owner_id, None, stream=True)


def _propose_memory(database: Store, owner_id: str, body: MemoryProposal) -> dict[str, Any]:
    if body.source_urls:
        reports = get_report_store(database)
        allowed = set()
        for summary in reports.list_reports(owner_id, limit=100):
            saved = reports.get_report(owner_id, summary["report_id"])
            allowed.update(runtime._safe_source_url(source.get("url")) for source in saved["result"].get("sources", []) if isinstance(source, dict))
        if any(runtime._safe_source_url(url) not in allowed or not runtime._safe_source_url(url) for url in body.source_urls):
            raise StoreError("memory_source_unverified", "A memory source must already appear in your saved reports.", 422)
    return database.propose_workspace_memory(owner_id, kind=body.kind, content=body.content,
        source_urls=[runtime._safe_source_url(url) for url in body.source_urls])


@agent_router.get("/memory")
def memories(session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    return {"items": get_store().list_workspace_memory(owner(session_id))}


@owner_router.get("/memory")
def owner_memories(owner_id: str = Depends(require_owner)) -> dict[str, Any]:
    return {"items": get_store().list_workspace_memory(owner_id)}


@agent_router.post("/memory", status_code=201)
def propose_memory(body: MemoryProposal, session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    database = get_store()
    try:
        return _propose_memory(database, owner(session_id), body)
    except StoreError as error:
        raise _safe_workspace_error(error, database, session_id) from None


@owner_router.post("/memory", status_code=201)
def propose_owner_memory(body: MemoryProposal, owner_id: str = Depends(require_owner)) -> dict[str, Any]:
    database = get_store()
    try:
        return _propose_memory(database, owner_id, body)
    except StoreError as error:
        raise _safe_workspace_error(error, database, None) from None


@agent_router.patch("/memory/{memory_id}")
def review_memory(memory_id: str, body: MemoryReview, session_id: str = Depends(public.require_session)) -> dict[str, Any]:
    database = get_store()
    try:
        return database.review_workspace_memory(owner(session_id), memory_id, body.action)
    except StoreError as error:
        raise _safe_workspace_error(error, database, session_id) from None


@owner_router.patch("/memory/{memory_id}")
def review_owner_memory(memory_id: str, body: MemoryReview, owner_id: str = Depends(require_owner)) -> dict[str, Any]:
    database = get_store()
    try:
        return database.review_workspace_memory(owner_id, memory_id, body.action)
    except StoreError as error:
        raise _safe_workspace_error(error, database, None) from None
