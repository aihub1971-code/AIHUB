"""Shared, bounded NewsData feeds for the current research workspace.

This service collects at most 50 raw provider articles in a frozen 12-hour
window, using a shared SQLite cache and the existing daily credit allowance.
Explicit refreshes may add bounded, separately reserved native web discovery.
Cache reads never run a model. Every attempted provider page is reserved before
I/O; only confirmed nonchargeable responses release that reservation. Metadata
and publisher-verified sources stay distinct.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app import newsdata, runtime
from app.agent_profiles import get_profile
from app.config import get_setting
from app.permissions import require_capability
from app.storage import DUBAI, Store, StoreError, get_store


UTC = timezone.utc
MAX_ITEMS = 50
MAX_PAGES = 50
MAX_RAW_ITEMS = 50
MAX_ARTICLE_URL_CHARS = 2000
REFRESH_INTERVAL_SECONDS = 900
REFRESH_MODE = "manual"
MAX_REFRESH_SECONDS = 90
LEASE_SECONDS = 420
MAX_QUEUED_REFRESHES = 7
MAX_QUEUE_WAIT_SECONDS = MAX_QUEUED_REFRESHES * LEASE_SECONDS
QUERY_VERSION = "workspace-news-v2-raw50"
# NewsData's documented validation errors, refundable only with a structured
# HTTP 400/422 response. Auth, quota, pagination, transport and 5xx are excluded.
_NONCHARGEABLE_VALIDATION_CODES = frozenset({"InvalidRequest", "UnsupportedFilter", "FilterLimitExceed",
    "UnsupportedParameter", "TooManyQueryFilter", "TooManyFilter", "TooManyLogicalOperator",
    "UnsupportedDateFormat", "UnsupportedQueryLength", "UnsupportedQInTitleLength", "UnsupportedQInMetaLength",
    "MalformedUrl", "MalformedQuery"})
# Inherit Uvicorn's configured INFO handler without enabling HTTP-client logs.
LOGGER = logging.getLogger("uvicorn.error.aihub.news_feed")
_WAITER_LOCK = threading.Lock()
_WAITERS: dict[tuple[str, str], object] = {}

# The filters are server-owned, bilingual and fit the documented 100-character
# query allowance. Provider search supplies candidates; local checks decide
# relevance. No user task prose or credentials is persisted in this shared cache.
QUERIES = {
    "iran-news": "Iran OR Tehran OR Hormuz OR IRGC OR إيران OR طهران OR هرمز",
    "geopolitical-morning": "war OR conflict OR diplomacy OR sanctions OR election OR حرب OR صراع OR دبلوماسية OR عقوبات",
    "ubi-research": '"basic income" OR "guaranteed income" OR "الدخل الأساسي" OR "دخل مضمون"',
    "uae-eu-trade-talks": '(UAE OR Emirates OR "Abu Dhabi" OR أبوظبي OR الإمارات) AND (EU OR European OR Brussels OR الأوروبي)',
    "education-observatory": "education OR university OR school OR تعليم OR جامعة OR مدارس",
    "economic-cultural-briefing": "economy OR inflation OR trade OR tourism OR culture OR اقتصاد OR ثقافة OR سياحة",
    "historical-reading-companion": 'history OR archaeology OR historian OR archive OR تاريخ OR "علم الآثار" OR مؤرخ',
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_news_cache (
    cache_key TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    snapshot_json TEXT,
    checked_at TEXT,
    next_refresh_at TEXT,
    last_successful_refresh_at TEXT,
    refresh_id TEXT,
    lease_until TEXT,
    error_json TEXT
);
CREATE TABLE IF NOT EXISTS workspace_news_credits (
    charge_id TEXT PRIMARY KEY,
    refresh_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    local_month TEXT NOT NULL,
    accounted_credits INTEGER NOT NULL CHECK(accounted_credits = 1)
);
CREATE INDEX IF NOT EXISTS workspace_news_credits_date_idx
    ON workspace_news_credits(local_date);
CREATE INDEX IF NOT EXISTS workspace_news_credits_month_idx
    ON workspace_news_credits(local_month);
CREATE TABLE IF NOT EXISTS workspace_news_credit_adjustments (
    charge_id TEXT PRIMARY KEY REFERENCES workspace_news_credits(charge_id),
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(reason IN ('empty_success','invalid_request'))
);
CREATE TABLE IF NOT EXISTS workspace_news_requests (
    request_id TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    window_json TEXT NOT NULL,
    allow_web_fallback INTEGER NOT NULL CHECK(allow_web_fallback IN (0,1)),
    fallback_decided INTEGER NOT NULL DEFAULT 0 CHECK(fallback_decided IN (0,1)),
    background INTEGER NOT NULL CHECK(background IN (0,1)),
    queued_until TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed')),
    started_at TEXT,
    lease_until TEXT,
    finished_at TEXT,
    error_json TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS workspace_news_requests_pending_agent_idx
    ON workspace_news_requests(cache_key) WHERE state='queued';
CREATE INDEX IF NOT EXISTS workspace_news_requests_queue_idx
    ON workspace_news_requests(state,requested_at);
CREATE TABLE IF NOT EXISTS workspace_news_provider (
    provider TEXT PRIMARY KEY,
    refresh_id TEXT,
    lease_until TEXT,
    cooldown_until TEXT,
    error_json TEXT
);
CREATE TABLE IF NOT EXISTS workspace_news_translations (
    cache_key TEXT NOT NULL,
    article_id TEXT NOT NULL,
    language TEXT NOT NULL CHECK(language IN ('ar','en')),
    fingerprint TEXT NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(cache_key,article_id,language)
);
"""


def supports_agent(agent_id: str) -> bool:
    return agent_id in QUERIES


def _stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone is required")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _decode(value: str | None, default: Any = None) -> Any:
    return json.loads(value) if value else default


def _key(agent_id: str) -> str:
    if not supports_agent(agent_id):
        raise StoreError("news_feed_not_supported", "This assistant has no current-news feed.", 404)
    return QUERY_VERSION + ":" + agent_id


def _language(language: str) -> None:
    if language not in {"ar", "en"}:
        raise StoreError("invalid_language", "Use ar or en.", 422)


def _window(value: dict[str, Any] | None, now: datetime) -> dict[str, str]:
    if value is None:
        end, start = now, now - timedelta(hours=12)
    else:
        try:
            start = newsdata._moment(value.get("start") or value.get("window_start"))
            end = newsdata._moment(value.get("end") or value.get("cutoff") or value.get("window_end"))
            if end - start != timedelta(hours=12):
                raise ValueError("invalid_window")
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise StoreError("invalid_research_window", "A frozen 12-hour window with explicit timezones is required.", 422) from None
    return {"start": _stamp(start), "end": _stamp(end), "cutoff": _stamp(end), "timezone": "UTC"}


def plan_metadata() -> dict[str, Any]:
    plan = newsdata.configured_plan()
    return {"configured_plan": plan, "plan_verified": False,
            "configured_delivery_delay_hours": 12 if plan == "free" else None,
            "free_plan_delay_hours": 12, "paid_plan_realtime_supported": True,
            "plan_note": "Plan is operator configuration, not an account entitlement check."}


def _page_size() -> int:
    # Unknown plans use a universally valid size. The application does not
    # infer entitlement from a key, results count, or provider marketing copy.
    try:
        return newsdata.page_size_setting()
    except HTTPException:
        raise StoreError("newsdata_page_size_invalid", "Set NEWSDATA_PAGE_SIZE to an integer from 1 to 50.", 503) from None


def accounted_credits(connection: sqlite3.Connection, *, local_date: str | None = None,
                      local_month: str | None = None) -> int:
    """Shared-ledger hook; safe before the additive feed schema exists."""
    if (local_date is None) == (local_month is None):
        raise ValueError("Choose one accounting period")
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_news_credits'").fetchone():
        return 0
    field, value = ("local_date", local_date) if local_date is not None else ("local_month", local_month)
    reserved = int(connection.execute(
        f"SELECT COALESCE(SUM(accounted_credits),0) FROM workspace_news_credits WHERE {field}=?", (value,)).fetchone()[0])
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_news_credit_adjustments'").fetchone():
        return reserved
    released = int(connection.execute(f"""SELECT COUNT(*) FROM workspace_news_credit_adjustments AS adjustment
        JOIN workspace_news_credits AS charge ON charge.charge_id=adjustment.charge_id
        WHERE charge.{field}=?""", (value,)).fetchone()[0])
    return reserved - released


def _safe_error(error: Exception, *, retry_after: int = REFRESH_INTERVAL_SECONDS) -> dict[str, Any]:
    code = getattr(error, "code", None)
    if isinstance(error, HTTPException) and isinstance(error.detail, dict):
        code = error.detail.get("code")
        retry_after = error.detail.get("retry_after_seconds", retry_after)
    if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", code):
        code = "newsdata_unavailable"
    retry_after = min(86400, max(1, retry_after)) if type(retry_after) is int else REFRESH_INTERVAL_SECONDS
    retryable = code not in {"newsdata_not_configured", "newsdata_auth_failed", "newsdata_access_denied",
                             "newsdata_page_size_invalid"}
    result = {"code": code, "reference": "news_" + uuid4().hex[:16], "retryable": retryable,
              "retry_after_seconds": retry_after}
    local_limits = {"newsdata_daily_credit_limit", "daily_report_limit", "daily_budget_limit", "monthly_budget_limit"}
    local_config = {"newsdata_not_configured", "newsdata_page_size_invalid", "runtime_not_configured",
        "newsdata_refresh_queue_full", "newsdata_refresh_queue_timeout", "newsdata_refresh_interrupted"}
    result["scope"] = "application" if code in local_limits | local_config else "provider" if code.startswith(("newsdata_", "openai_", "provider_")) else "application"
    result["limit_kind"] = ({"newsdata_daily_credit_limit": "application_daily_credit",
        "daily_report_limit": "application_daily_reports", "daily_budget_limit": "application_daily_budget",
        "monthly_budget_limit": "application_monthly_budget", "newsdata_provider_credit_limit": "provider_credit",
        "newsdata_rate_limited": "provider_rate", "openai_quota_exceeded": "provider_credit",
        "openai_rate_limited": "provider_rate"}).get(code)
    result["retry_after_source"] = "application"
    if isinstance(error, HTTPException) and isinstance(error.detail, dict):
        status, request_id = error.detail.get("provider_status"), error.detail.get("provider_request_id")
        if type(status) is int and 100 <= status <= 599:
            result["provider_status"] = status
        if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9-]{1,80}", request_id):
            result["provider_request_id"] = request_id
        if error.detail.get("retry_after_source") == "provider":
            result["retry_after_source"] = "provider"
        provider_code = error.detail.get("provider_code")
        if isinstance(provider_code, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", provider_code):
            result["provider_code"] = provider_code
    return result


def _retry_after(headers: httpx.Headers) -> int | None:
    value = headers.get("Retry-After", "").strip()
    if re.fullmatch(r"\d{1,6}", value):
        return min(86400, max(1, int(value)))
    try:
        until = parsedate_to_datetime(value)
        if until.tzinfo is not None:
            return min(86400, max(1, math.ceil((until - datetime.now(UTC)).total_seconds())))
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _contains(text: str, terms: list[str]) -> bool:
    folded = runtime._headline_key(text)
    for term in terms:
        needle = runtime._headline_key(term)
        if not needle:
            continue
        if re.fullmatch(r"[a-z ]+", needle):
            pattern = r"\b" + re.escape(needle)
            if len(needle.replace(" ", "")) <= 3 or " " in needle:
                pattern += r"\b"
            if re.search(pattern, folded):
                return True
        elif needle in folded:
            return True
    return False


_IRAN_CONFLICT = ["war", "conflict", "military", "strike", "attack", "drone", "sanction", "nuclear",
    "missile", "diploma", "ceasefire", "negotiat", "blockade", "حرب", "صراع", "عسكري", "قصف", "ضرب",
    "هجوم", "عقوب", "نووي", "صاروخ", "صواريخ", "دبلوماس", "مفاوض", "حصار", "جنگ"]
_IRAN_CONSEQUENCES = ["oil", "crude", "brent", "fuel", "energy", "shipping", "freight", "aviation",
    "airline", "tanker", "trade", "نفط", "خام", "برنت", "وقود", "طاقة", "ملاح", "شحن", "طيران", "تجار"]


def _global_relevant(title: str, description: str) -> bool:
    # Classify the reported event, not an incidental keyword in a long story.
    # Roles, institutions and actions keep the scope independent of country lists.
    lead, subject = title + " " + description[:900], title + " " + description[:250]
    security = ["airstrike", "air strike", "drone strike", "drone attack", "missile attack", "military action",
        "military operation", "military operations", "military offensive", "military exercise", "military satellite", "naval blockade",
        "armed conflict", "civil war", "peacekeeper", "ceasefire", "peace talks", "nuclear talks", "nuclear arsenal",
        "nuclear weapon", "border conflict", "crossborder attack", "cross border attack", "war crime", "disarmament",
        "غارة جوية", "غارات", "ضربة جوية", "ضربات جوية", "هجوم صاروخي", "هجمات صاروخية", "عمليات عسكرية",
        "عملية عسكرية", "عمل عسكري", "مناورات عسكرية", "حرب أهلية", "حرب اهلية", "نزاع مسلح", "حفظ السلام",
        "وقف إطلاق النار", "وقف اطلاق النار", "مفاوضات السلام", "مفاوضات نووية", "سلاح نووي", "نزع سلاح", "جرائم حرب",
        "security arrangements", "military talks", "الترتيبات الأمنية", "ترتيبات أمنية", "المباحثات العسكرية"]
    diplomacy = ["diplomac", "diplomatic", "foreign minister", "foreign affairs", "external affairs", "foreign policy",
        "envoy", "ambassador", "embassy", "treaty", "security council", "united nations", "nato", "state visit",
        "defense secretary", "defense minister", "defence minister", "وزير الدفاع",
        "bilateral", "multilateral", "international relations", "دبلوماس", "وزير الخارجية", "وزراء الخارجية",
        "وزارة الخارجية", "السياسة الخارجية", "سفير", "سفارة", "سفارات", "مبعوث", "معاهدة",
        "الأمم المتحدة", "الامم المتحدة", "زيارة دولة", "متعدد الأطراف", "العلاقات الدولية"]
    action = ["talks", "negotiat", "meet", "agreement", "sign", "withdraw", "expel", "recall", "broker", "summit",
        "cooperation", "security", "peace", "relations", "dispute", "visit", "مفاوض", "مباحث", "لقاء", "يلتقي", "التقى",
        "اتفاق", "توقيع", "انسحاب", "سحب", "طرد", "استدعاء", "تعاون", "سلام", "أمن", "أمني", "علاقات", "زيارة"]
    conflict = ["war", "conflict", "hostilities", "fighting", "invasion", "sanction", "blockade", "attack", "حرب", "نزاع",
        "عدوان", "قتال", "قصف", "ضربات", "هجمات", "عقوبات", "حصار", "اشتباك", "معارك"]
    actors = ["army", "military", "troops", "armed forces", "navy", "rebels", "militia", "government", "president",
        "prime minister", "defence minister", "defense secretary", "international", "crossborder", "cross border",
        "جيش", "الجيش", "قوات", "عسكري", "حكومة", "الرئيس", "رئيس", "مسلح", "ميليش", "مليش", "دولي", "حشد"]
    economics = ["trade", "tariff", "energy", "oil", "gas", "diesel", "shipping", "supply chain", "تجار", "رسوم جمركية",
        "تعرف", "طاقة", "نفط", "غاز", "شحن", "إمدادات", "امدادات"]
    economic_policy = ["sanction", "tariff", "embargo", "blockade", "export control", "trade agreement", "trade deal",
        "trade war", "energy deal", "security", "عقوبات", "رسوم جمركية", "حظر", "حصار", "اتفاق تجاري", "اتفاقية تجارية",
        "حرب تجارية", "اتفاق طاقة", "أمن", "أمني"]
    national = ["president", "prime minister", "head of state", "parliament", "congress", "national assembly",
        "national election", "federal government", "house majority", "presidential", "monarch", "الرئيس", "رئيس",
        "رئاسة", "برلمان", "الكونغرس", "الكونجرس", "الجمعية الوطنية", "انتخابات رئاسية", "الملك"]
    change = ["resign", "oust", "overthrow", "coup", "no confidence", "noconfidence", "dissolv", "government collapse",
        "government formation", "forms government", "form a government", "sworn in", "election result", "election results",
        "wins", "won", "majority", "control of congress", "control of parliament", "top post",
        "constitutional crisis", "election interference", "election disruption", "election disruptions", "abdica", "assassinat",
        "اغتيال", "اغتيل", "استقال", "يستقيل", "إقالة",
        "إطاحة", "انقلاب", "سحب الثقة", "حل البرلمان", "تشكيل حكومة", "تشكيل الحكومة", "سقوط الحكومة", "فوز",
        "يفوز", "فاز", "أغلبية", "اغلبية", "نتائج الانتخابات", "تنصيب", "انتقال السلطة", "أزمة دستورية", "تدخل انتخابي"]
    local = ["municipal", "city council", "county", "governorship", "gubernatorial", "state assembly", "local election",
        "zoning arrangement", "party primary", "primary race", "polling location", "polling place", "early voting",
        "grassroots", "campaign vehicle", "party stalwart", "senate candidate", "candidate debate", "campaign rally",
        "tribal conflict", "provincial task force", "road ambush",
        "robbery", "انتخابات بلدية", "انتخابات محلية", "مجلس بلدي", "دائرة انتخابية", "توزيع الترشيحات", "نزاع قبلي"]
    unrelated = ["hollywood", "film", "movie", "cinema", "celebrity", "football", "soccer", "cricket",
        "basketball", "karate", "championship", "tournament", "restaurant", "restaurateur", "suv", "church", "centennial",
        "youth conference", "editorial cartoon", "company president", "university president", "president of the university",
        "club president", "هوليوود", "فيلم", "أفلام", "افلام", "سينما", "رئيس الجامعة", "رئيس الشركة", "رئيس النادي",
        "مسلسل", "مشاهير", "كرة القدم", "دوري أبطال", "قمة كروية", "مباراة", "الأندية", "الاندية", "تشكيل مشترك"]
    fiction = ["film", "movie", "cinema", "novel", "fiction", "video game", "documentary", "فيلم", "أفلام", "افلام",
        "سينما", "مسلسل", "رواية", "لعبة"]
    def diplomatic(text: str) -> bool:
        # Security Council must not match the Arabic "board of trustees".
        return _contains(text, diplomacy) or bool(re.search(r"مجلس\s+(?:الأمن|الامن)\b", runtime._headline_key(text)))
    rhetorical_war = _contains(title, ["go to war with", "goes to war with", "going to war with", "went to war with",
        "go to war against", "goes to war against", "going to war against", "war of words", "wage war on", "wages war on",
        "حرب كلامية", "حرب تصريحات", "حرب قضائية", "معركة قضائية", "يدخل في حرب مع", "تدخل في حرب مع"])
    # A legal dispute about a military base is not warfare. Explicit rhetoric
    # needs concrete security, diplomacy or strategic economic context.
    if rhetorical_war and not (_contains(lead, security + ["declaration of war", "invasion", "bombardment",
            "إعلان الحرب", "اعلان الحرب", "غزو"])
            or (diplomatic(lead) and _contains(lead, action))
            or (_contains(title, economics) and _contains(lead, economic_policy))):
        return False
    non_national_role = _contains(subject, ["company president", "club president", "university president",
        "president of the university", "رئيس الشركة", "رئيس النادي", "رئيس الجامعة"])
    institutional_change = (not non_national_role and _contains(lead, national) and _contains(lead, change)
        and _contains(title, national + change + ["election", "midterm", "house races", "government", "انتخاب", "حكومة"]))
    headline_security = _contains(title, security)
    headline_diplomacy = diplomatic(title) and _contains(lead, action)
    # A treaty signed at a stadium is diplomacy; a war film or football "summit"
    # is not. Local campaign logistics do not become national news by naming a president.
    treaty_event = _contains(title, ["treaty", "security pact", "defence pact", "defense pact", "معاهدة", "اتفاق دفاعي"])
    # Venues and victims do not turn a real state/security event into entertainment.
    assassination = (not non_national_role and _contains(title, ["assassinat", "اغتيال", "اغتيل"])
        and _contains(lead, national))
    literal_event = (headline_security or headline_diplomacy or assassination) and not _contains(subject, fiction)
    if _contains(subject, unrelated) and not (literal_event or (treaty_event and _contains(lead, action))):
        return False
    if _contains(subject, local) and not (headline_security or headline_diplomacy):
        return False
    if institutional_change:
        return True
    if headline_security or headline_diplomacy:
        return True
    headline_focus = _contains(title, conflict + diplomacy + economics + ["security", "peace", "stability", "agreement",
        "deal", "relations", "negotiat", "talks", "أمن", "أمني", "سلام", "استقرار", "اتفاق", "علاقات", "تعاون", "زيارة", "ترتيبات", "مفاوض", "مباحث"])
    if headline_focus:
        if _contains(lead, conflict) and _contains(lead, actors):
            return True
        if diplomatic(lead) and _contains(lead, action):
            return True
        if _contains(title, economics) and _contains(lead, economic_policy) and _contains(lead, actors + conflict + ["imports", "exports", "واردات", "صادرات"]):
            return True
    # Broad headlines can still describe an explicit ministerial/state visit in
    # their lead. Do not use unrelated background later in the provider summary.
    return (_contains(lead, ["foreign minister", "foreign affairs", "external affairs", "state visit",
        "وزير الخارجية", "وزراء الخارجية", "زيارة دولة"]) and _contains(lead, action))


def _relevant(agent_id: str, title: str, description: str) -> bool:
    profile = get_profile(agent_id)
    lead = title + " " + description
    if agent_id == "geopolitical-morning":
        return _global_relevant(title, description)
    if agent_id == "iran-news":
        # Iran must be the story's focus, not a closing market aside appended to
        # an unrelated war report. Consequences use the same opening context.
        opening = description[:400]
        lead = title + " " + opening
        iran = _contains(lead, profile["terms"])
        conflict = _contains(lead, _IRAN_CONFLICT)
        # US politics alone and unrelated Iranian sport/entertainment do not
        # qualify. Consequences require an explicit Iranian-conflict connection.
        return iran and conflict and (_contains(title, profile["terms"] + _IRAN_CONFLICT)
            or (_contains(title, _IRAN_CONSEQUENCES) and _contains(opening, profile["terms"])))
    if agent_id == "uae-eu-trade-talks":
        lead = title + " " + description[:900]
        return (_contains(lead, profile["terms"] + ["uae", "emirat", "abu dhabi", "الإمارات", "الامارات", "أبوظبي", "أبو ظبي", "ابوظبي"])
            and _contains(lead, ["european union", "european commission", "eu", "european", "brussels", "الاتحاد الأوروبي", "الاتحاد الاوروبي", "أوروب", "اوروب", "بروكسل"])
            and _contains(lead, ["trade", "negotiat", "investment", "tariff", "cepa", "fta", "market access", "standards", "customs", "economic partnership", "تجار", "مفاوض", "استثمار", "تعرف", "معايير", "جمرك", "شراكة اقتصادية"]))
    if agent_id == "historical-reading-companion":
        return _contains(lead, ["history", "historian", "archaeolog", "archive", "manuscript", "antiquit", "abbasid",
            "تاريخ", "مؤرخ", "علم الآثار", "علم الاثار", "أثري", "أثرية", "أرشيف", "ارشيف", "مخطوط", "عباسي"])
    return _contains(lead, profile["terms"])


def _fingerprint(item: dict[str, Any]) -> str:
    return hashlib.sha256(_json([item["original_headline"], item["original_summary"],
                                item["original_language"]]).encode()).hexdigest()


def _near_duplicate(key: str, previous: list[str]) -> bool:
    numbers = re.findall(r"\d+", key)
    return any(key == old or (len(key) >= 50 and len(old) >= 50
        and re.findall(r"\d+", old) == numbers
        and SequenceMatcher(None, key, old, autojunk=False).ratio() >= .97) for old in previous)


def _accept(source: Any, agent_id: str, window: dict[str, str], seen: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(source, dict):
        return None, "invalid_article"
    identifier = source.get("article_id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[^\s\x00-\x1f\x7f]{1,200}", identifier):
        return None, "invalid_article"
    title = newsdata._text(source.get("title"), newsdata.MAX_HEADLINE_CHARS)
    if not title:
        return None, "missing_title"
    url = runtime._safe_source_url(source.get("link"))
    if not url or len(url) > MAX_ARTICLE_URL_CHARS:
        return None, "invalid_link"
    publication = newsdata._publication(source.get("pubDate"), source.get("pubDateTZ"))
    if publication is None:
        return None, "invalid_publication"
    published, zone = publication
    if published > newsdata._moment(window["end"]):
        return None, "future_publication"
    if published < newsdata._moment(window["start"]):
        return None, "outside_window"
    language = {"english": "en", "en": "en", "arabic": "ar", "ar": "ar"}.get(source.get("language"))
    if not language:
        return None, "unsupported_language"
    description = newsdata._text(source.get("description"), newsdata.MAX_DESCRIPTION_CHARS)
    if newsdata._PLAN_NOTICE.match(description):
        description = ""
    if not _relevant(agent_id, title, description):
        return None, "irrelevant"
    canonical, headline_key = runtime._canonical_url(url), runtime._headline_key(title)
    if (identifier in seen["ids"] or canonical in seen["urls"] or source.get("duplicate") is True
            or _near_duplicate(headline_key, seen["titles"])):
        return None, "duplicate"
    seen["ids"].add(identifier)
    seen["urls"].add(canonical)
    seen["titles"].append(headline_key)
    # Provider IDs can be 200 characters, whereas persisted source IDs have a
    # 200-character total limit including our namespace. Preserve the provider
    # ID internally and hash only the exceptional overlong public identifier.
    source_id = "newsdata:" + identifier
    if len(source_id) > 200:
        source_id = "newsdata:sha256-" + hashlib.sha256(identifier.encode()).hexdigest()
    item = {"id": source_id, "article_id": identifier, "source_id": source_id,
        "headline": title, "summary": description, "publisher": newsdata._text(source.get("source_name"), 200) or urlsplit(url).hostname,
        "url": url, "published_at": _stamp(published), "published_date": _stamp(published), "language": language,
        "published_at_dubai": published.astimezone(DUBAI).isoformat(),
        "original_headline": title, "original_summary": description, "original_language": language,
        "headline_en": title if language == "en" else None, "summary_en": description if language == "en" else None,
        "headline_ar": title if language == "ar" else None, "summary_ar": description if language == "ar" else None,
        "summary_available": bool(description), "translation_status": "original", "classification": "within_window",
        "source_kind": runtime._news_source_kind(title, url), "source_ids": [source_id],
        "evidence_level": "newsdata_structured_metadata", "publisher_text_verified": False,
        "publication_verification": {"method": "newsdata_pubDate_pubDateTZ", "verified": False,
            "provider_metadata_validated": True, "timestamp": _stamp(published), "timezone": zone,
            "label": "Publication time supplied by NewsData; publisher page not independently checked."}}
    if agent_id == "iran-news":
        item["section"] = "major_consequences" if _contains(title, _IRAN_CONSEQUENCES) else "conflict_diplomacy"
    return item, None


async def _request_page(client: httpx.AsyncClient, *, agent_id: str, size: int,
                        cursor: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    key = get_setting("NEWSDATA_API_KEY", "") or ""
    if not key:
        raise StoreError("newsdata_not_configured", "NewsData is not configured.", 503)
    params: dict[str, Any] = {"q": QUERIES[agent_id], "language": "en,ar", "size": size,
        "removeduplicate": 1, "timezone": "UTC", "sort": "pubdatedesc"}
    if newsdata.configured_plan() == "paid":
        params["timeframe"] = 12
    if cursor is not None:
        params["page"] = cursor
    try:
        # Only configured paid plans send the paid-only timeframe parameter.
        # Every returned date is still validated against the frozen cutoff.
        async with client.stream("GET", newsdata.NEWSDATA_LATEST_URL,
                                 headers={"X-ACCESS-KEY": key}, params=params) as response:
            def provider_error(data: Any) -> HTTPException:
                error = newsdata._provider_error(response.status_code, data)
                if (response.status_code in {400, 422} and isinstance(data, dict) and data.get("status") == "error"
                        and error.detail.get("provider_code") in _NONCHARGEABLE_VALIDATION_CODES):
                    error.detail["confirmed_nonchargeable_validation"] = True
                delay = _retry_after(response.headers)
                error.detail["retry_after_seconds"] = delay or REFRESH_INTERVAL_SECONDS
                error.detail["retry_after_source"] = "provider" if delay else "application"
                identifier = response.headers.get("X-Request-Id", "")
                if identifier != key and re.fullmatch(r"[A-Za-z0-9-]{1,80}", identifier):
                    error.detail["provider_request_id"] = identifier
                return error
            chunks, length = [], 0
            async for chunk in response.aiter_bytes():
                length += len(chunk)
                if length > newsdata.MAX_NEWSDATA_RESPONSE_BYTES:
                    raise StoreError("newsdata_response_too_large", "The provider response exceeded its size bound.", 502)
                chunks.append(chunk)
            try:
                data = json.loads(b"".join(chunks))
            except (ValueError, TypeError, UnicodeError):
                if response.is_error:
                    raise provider_error(None) from None
                raise StoreError("newsdata_invalid_response", "NewsData returned invalid JSON.", 502) from None
            if response.is_error or not isinstance(data, dict) or data.get("status") != "success":
                raise provider_error(data)
            if (not isinstance(data.get("results"), list) or len(data["results"]) > size
                    or type(data.get("totalResults")) is not int or data["totalResults"] < 0
                    or not newsdata._cursor(data.get("nextPage"))):
                raise StoreError("newsdata_invalid_response", "NewsData returned an invalid page.", 502)
            return data, {"credits_remaining": newsdata._remaining(response.headers, "X-API-Limit-Remaining"),
                "rate_window_remaining": newsdata._remaining(response.headers, "X-RateLimit-Remaining")}
    except httpx.TimeoutException:
        raise StoreError("newsdata_timeout", "NewsData timed out; no retry was made.", 504) from None
    except httpx.RequestError:
        raise StoreError("newsdata_connection_error", "NewsData could not be reached; no retry was made.", 502) from None


class FeedStore:
    """Persistent cache and cross-process claims, using the existing database."""

    def __init__(self, database: Store | None = None):
        self.database = database or get_store()
        with self.database._connect() as connection:
            connection.executescript(_SCHEMA)

    def now(self) -> datetime:
        return self.database._now()

    def read(self, agent_id: str) -> dict[str, Any] | None:
        with self.database._connect() as connection:
            row = connection.execute("SELECT * FROM workspace_news_cache WHERE cache_key=?", (_key(agent_id),)).fetchone()
        return dict(row) if row else None

    def provider_state(self) -> dict[str, Any]:
        with self.database._connect() as connection:
            row = connection.execute("SELECT * FROM workspace_news_provider WHERE provider='newsdata'").fetchone()
        return dict(row) if row else {}

    def claim(self, agent_id: str, window: dict[str, Any] | None = None, *,
              explicit_retry: bool = False, allow_web_fallback: bool = False) -> tuple[str, dict[str, str]] | None:
        moment = self.now()
        frozen = _window(window, moment)
        with self.database._transaction() as connection:
            return self._claim(connection, agent_id, frozen, moment,
                explicit_retry=explicit_retry, allow_web_fallback=allow_web_fallback)

    def _claim(self, connection: sqlite3.Connection, agent_id: str, frozen: dict[str, str], moment: datetime, *,
               explicit_retry: bool, allow_web_fallback: bool, request_id: str | None = None) -> tuple[str, dict[str, str]] | None:
        key = _key(agent_id)
        stamp = _stamp(moment)
        connection.execute("INSERT OR IGNORE INTO workspace_news_cache(cache_key,agent_id) VALUES (?,?)", (key, agent_id))
        row = connection.execute("SELECT * FROM workspace_news_cache WHERE cache_key=?", (key,)).fetchone()
        if row["refresh_id"] and row["lease_until"] and row["lease_until"] > stamp:
            return None
        # Cache age is useful display information, never a gate for an
        # intentional click. The provider's independent cooldown remains below.
        if row["next_refresh_at"] and row["next_refresh_at"] > stamp and not explicit_retry:
            return None
        connection.execute("INSERT OR IGNORE INTO workspace_news_provider(provider) VALUES ('newsdata')")
        provider = connection.execute("SELECT * FROM workspace_news_provider WHERE provider='newsdata'").fetchone()
        provider_error = _decode(provider["error_json"], {})
        blocked = (provider["cooldown_until"] and provider["cooldown_until"] > stamp
            and provider_error.get("code") != "newsdata_daily_credit_limit")
        if blocked and not allow_web_fallback:
            return None
        if provider["refresh_id"] and provider["lease_until"] and provider["lease_until"] > stamp:
            return None
        refresh_id = request_id or "feed_" + uuid4().hex
        until = _stamp(moment + timedelta(seconds=LEASE_SECONDS))
        connection.execute("""UPDATE workspace_news_cache SET checked_at=?,next_refresh_at=?,
            refresh_id=?,lease_until=?,error_json=NULL WHERE cache_key=?""",
            (stamp, _stamp(moment + timedelta(seconds=REFRESH_INTERVAL_SECONDS)), refresh_id, until, key))
        connection.execute("UPDATE workspace_news_provider SET refresh_id=?,lease_until=? WHERE provider='newsdata'",
                           (refresh_id, until))
        if request_id is None:
            connection.execute("""INSERT INTO workspace_news_requests
                (request_id,cache_key,agent_id,requested_at,window_json,allow_web_fallback,background,
                 queued_until,state,started_at,lease_until) VALUES (?,?,?,?,?,?,0,?,'running',?,?)""",
                (refresh_id, key, agent_id, stamp, _json(frozen), int(allow_web_fallback), until, stamp, until))
        return refresh_id, frozen

    def _expire_requests(self, connection: sqlite3.Connection, moment: datetime) -> None:
        stamp = _stamp(moment)
        for state, deadline, code in (("queued", "queued_until", "newsdata_refresh_queue_timeout"),
                                     ("running", "lease_until", "newsdata_refresh_interrupted")):
            error = _safe_error(StoreError(code, "The requested refresh did not complete.", 503), retry_after=1)
            connection.execute(f"""UPDATE workspace_news_requests SET state='failed',finished_at=?,error_json=?
                WHERE state=? AND {deadline}<=?""", (stamp, _json(error), state, stamp))

    def request(self, agent_id: str, window: dict[str, Any] | None = None, *,
                after: str | None = None, background: bool = False,
                allow_web_fallback: bool = False, wait_until: str | None = None) -> tuple[dict[str, Any] | None, bool, bool]:
        """Accept one explicit intent; return (request, launch_waiter, joined).

        One queued intent per agent coalesces duplicate clicks. A newer report
        may queue behind an already running older fetch. No read calls this.
        """
        key, moment = _key(agent_id), self.now()
        stamp, frozen = _stamp(moment), _window(window, moment)
        with self.database._transaction() as connection:
            self._expire_requests(connection, moment)
            connection.execute("INSERT OR IGNORE INTO workspace_news_cache(cache_key,agent_id) VALUES (?,?)", (key, agent_id))
            row = connection.execute("SELECT * FROM workspace_news_cache WHERE cache_key=?", (key,)).fetchone()
            running = connection.execute("SELECT * FROM workspace_news_requests WHERE request_id=?", (row["refresh_id"],)).fetchone() if row["refresh_id"] else None
            can_join = not (running and allow_web_fallback and not running["allow_web_fallback"] and running["fallback_decided"])
            running_window = _decode(running["window_json"], {}) if running else {}
            if (row["refresh_id"] and row["lease_until"] and row["lease_until"] > stamp
                    and can_join and (after is None or (row["checked_at"] and row["checked_at"] >= after
                        and running_window.get("end", "") >= frozen["end"]))):
                # Also register a legacy/direct claim so its completion can be
                # followed by request id without guessing from a retained cache.
                connection.execute("""INSERT OR IGNORE INTO workspace_news_requests
                    (request_id,cache_key,agent_id,requested_at,window_json,allow_web_fallback,background,
                     queued_until,state,started_at,lease_until) VALUES (?,?,?,?,?,?,?,?,'running',?,?)""",
                    (row["refresh_id"], key, agent_id, row["checked_at"], _json(frozen), int(allow_web_fallback),
                     0, row["lease_until"], row["checked_at"], row["lease_until"]))
                if allow_web_fallback:
                    connection.execute("UPDATE workspace_news_requests SET allow_web_fallback=1 WHERE request_id=?", (row["refresh_id"],))
                request = connection.execute("SELECT * FROM workspace_news_requests WHERE request_id=?", (row["refresh_id"],)).fetchone()
                return dict(request), False, True
            queued = connection.execute("SELECT * FROM workspace_news_requests WHERE cache_key=? AND state='queued'", (key,)).fetchone()
            if queued:
                # A fetch not yet started can cover the latest joined cutoff.
                latest_window = frozen if frozen["end"] > _decode(queued["window_json"])["end"] else _decode(queued["window_json"])
                launch = background and not queued["background"]
                own_deadline = (_stamp(moment + timedelta(seconds=MAX_QUEUE_WAIT_SECONDS)) if background
                    else wait_until or _stamp(moment + timedelta(seconds=MAX_REFRESH_SECONDS)))
                until = max(queued["queued_until"], own_deadline)
                connection.execute("""UPDATE workspace_news_requests SET window_json=?,allow_web_fallback=?,background=?,queued_until=?
                    WHERE request_id=?""", (_json(latest_window), int(allow_web_fallback or queued["allow_web_fallback"]),
                    int(background or queued["background"]), until, queued["request_id"]))
                request = connection.execute("SELECT * FROM workspace_news_requests WHERE request_id=?", (queued["request_id"],)).fetchone()
                return dict(request), launch, True
            connection.execute("INSERT OR IGNORE INTO workspace_news_provider(provider) VALUES ('newsdata')")
            provider = connection.execute("SELECT * FROM workspace_news_provider WHERE provider='newsdata'").fetchone()
            provider_error = _decode(provider["error_json"], {})
            if (provider["cooldown_until"] and provider["cooldown_until"] > stamp
                    and provider_error.get("code") != "newsdata_daily_credit_limit" and not allow_web_fallback):
                return None, False, False
            if connection.execute("SELECT COUNT(*) FROM workspace_news_requests WHERE state='queued'").fetchone()[0] >= MAX_QUEUED_REFRESHES:
                raise StoreError("newsdata_refresh_queue_full", "The manual news queue is full. Try again when a refresh completes.", 429)
            request_id = "feed_" + uuid4().hex
            until = (_stamp(moment + timedelta(seconds=MAX_QUEUE_WAIT_SECONDS)) if background
                else wait_until or _stamp(moment + timedelta(seconds=MAX_REFRESH_SECONDS)))
            connection.execute("""INSERT INTO workspace_news_requests
                (request_id,cache_key,agent_id,requested_at,window_json,allow_web_fallback,background,queued_until,state)
                VALUES (?,?,?,?,?,?,?,?,'queued')""", (request_id, key, agent_id, stamp, _json(frozen),
                int(allow_web_fallback), int(background), until))
            request = connection.execute("SELECT * FROM workspace_news_requests WHERE request_id=?", (request_id,)).fetchone()
        return dict(request), background, False

    def request_info(self, agent_id: str, request_id: str | None = None) -> dict[str, Any] | None:
        stamp = _stamp(self.now())
        with self.database._connect() as connection:
            if request_id:
                row = connection.execute("SELECT rowid AS queue_order,* FROM workspace_news_requests WHERE request_id=? AND cache_key=?",
                    (request_id, _key(agent_id))).fetchone()
            else:
                row = connection.execute("""SELECT rowid AS queue_order,* FROM workspace_news_requests WHERE cache_key=?
                    ORDER BY CASE WHEN state='queued' AND queued_until>? THEN 0
                        WHEN state='running' AND lease_until>? THEN 1 ELSE 2 END,
                        requested_at DESC,rowid DESC LIMIT 1""", (_key(agent_id), stamp, stamp)).fetchone()
            if not row:
                return None
            result = dict(row)
            if result["state"] in {"queued", "running"}:
                deadline = result["queued_until"] if result["state"] == "queued" else result["lease_until"]
                if deadline <= stamp:
                    code = "newsdata_refresh_queue_timeout" if result["state"] == "queued" else "newsdata_refresh_interrupted"
                    result.update(state="failed", error_json=_json(_safe_error(StoreError(code, "The requested refresh expired.", 503), retry_after=1)))
            result["queue_position"] = int(connection.execute("""SELECT COUNT(*) FROM workspace_news_requests
                WHERE state='queued' AND queued_until>? AND (requested_at<? OR (requested_at=? AND rowid<=?))""",
                (stamp, result["requested_at"], result["requested_at"], result["queue_order"])).fetchone()[0]) if result["state"] == "queued" else None
        return result

    def claim_request(self, request_id: str, *, foreground_only: bool = False) -> tuple[str, dict[str, str]] | None:
        moment = self.now()
        with self.database._transaction() as connection:
            self._expire_requests(connection, moment)
            row = connection.execute("SELECT rowid AS queue_order,* FROM workspace_news_requests WHERE request_id=?", (request_id,)).fetchone()
            if not row or row["state"] != "queued" or (foreground_only and row["background"]):
                return None
            first = connection.execute("SELECT request_id FROM workspace_news_requests WHERE state='queued' ORDER BY requested_at,rowid LIMIT 1").fetchone()
            if not first or first[0] != request_id:
                return None
            provider = connection.execute("SELECT * FROM workspace_news_provider WHERE provider='newsdata'").fetchone()
            provider_error = _decode(provider["error_json"], {}) if provider else {}
            if (provider and provider["cooldown_until"] and provider["cooldown_until"] > _stamp(moment)
                    and provider_error.get("code") != "newsdata_daily_credit_limit" and not row["allow_web_fallback"]):
                connection.execute("UPDATE workspace_news_requests SET state='failed',finished_at=?,error_json=? WHERE request_id=?",
                    (_stamp(moment), _json(provider_error), request_id))
                return None
            claim = self._claim(connection, row["agent_id"], _decode(row["window_json"]), moment,
                explicit_retry=True, allow_web_fallback=bool(row["allow_web_fallback"]), request_id=request_id)
            if claim:
                connection.execute("UPDATE workspace_news_requests SET state='running',started_at=?,lease_until=? WHERE request_id=?",
                    (_stamp(moment), _stamp(moment + timedelta(seconds=LEASE_SECONDS)), request_id))
            return claim

    def fail_queued(self, request_id: str, *, foreground_only: bool = False) -> None:
        error = _safe_error(StoreError("newsdata_refresh_queue_timeout", "The queued refresh exceeded its wait limit.", 504), retry_after=1)
        with self.database._transaction() as connection:
            connection.execute("""UPDATE workspace_news_requests SET state='failed',finished_at=?,error_json=?
                WHERE request_id=? AND state='queued' AND (?=0 OR (background=0 AND queued_until<=?))""",
                (_stamp(self.now()), _json(error), request_id, int(foreground_only), _stamp(self.now())))

    def pending_background(self) -> list[dict[str, Any]]:
        with self.database._connect() as connection:
            return [dict(row) for row in connection.execute("""SELECT request_id,agent_id FROM workspace_news_requests
                WHERE state='queued' AND background=1 AND queued_until>? ORDER BY requested_at,rowid LIMIT ?""",
                (_stamp(self.now()), MAX_QUEUED_REFRESHES)).fetchall()]

    def decide_fallback(self, agent_id: str, request_id: str, requested: bool) -> bool:
        """Seal the decision atomically with manual joins of a running report."""
        with self.database._transaction() as connection:
            cache = connection.execute("SELECT refresh_id,lease_until FROM workspace_news_cache WHERE cache_key=?", (_key(agent_id),)).fetchone()
            if not cache or cache["refresh_id"] != request_id or not cache["lease_until"] or cache["lease_until"] <= _stamp(self.now()):
                return False
            row = connection.execute("SELECT allow_web_fallback FROM workspace_news_requests WHERE request_id=?", (request_id,)).fetchone()
            enabled = requested or bool(row and row["allow_web_fallback"])
            connection.execute("UPDATE workspace_news_requests SET allow_web_fallback=?,fallback_decided=1 WHERE request_id=?",
                (int(enabled), request_id))
            return enabled

    def charge(self, agent_id: str, refresh_id: str) -> str:
        now = self.now()
        day, month = now.astimezone(DUBAI).date().isoformat(), now.astimezone(DUBAI).strftime("%Y-%m")
        with self.database._transaction() as connection:
            row = connection.execute("SELECT refresh_id,lease_until FROM workspace_news_cache WHERE cache_key=?", (_key(agent_id),)).fetchone()
            if not row or row["refresh_id"] != refresh_id or row["lease_until"] <= _stamp(now):
                raise StoreError("newsdata_refresh_interrupted", "The feed refresh lost its claim.", 409)
            old = connection.execute("SELECT COALESCE(SUM(accounted_newsdata_credits),0) FROM runs WHERE local_date=?", (day,)).fetchone()[0]
            if old + accounted_credits(connection, local_date=day) + 1 > self.database.limits.newsdata_daily_credit_limit:
                raise StoreError("newsdata_daily_credit_limit", "The shared daily NewsData credit allowance is exhausted.", 429)
            charge_id = "credit_" + uuid4().hex
            connection.execute("""INSERT INTO workspace_news_credits
                (charge_id,refresh_id,created_at,local_date,local_month,accounted_credits) VALUES (?,?,?,?,?,1)""",
                (charge_id, refresh_id, _stamp(now), day, month))
        return charge_id

    def reconcile(self, charge_id: str, refresh_id: str, reason: str) -> None:
        if reason not in {"empty_success", "invalid_request"}:
            raise ValueError("Unknown nonchargeable response")
        with self.database._transaction() as connection:
            connection.execute("""INSERT OR IGNORE INTO workspace_news_credit_adjustments(charge_id,created_at,reason)
                SELECT charge_id,?,? FROM workspace_news_credits WHERE charge_id=? AND refresh_id=?""",
                (_stamp(self.now()), reason, charge_id, refresh_id))

    def finish(self, agent_id: str, refresh_id: str, snapshot: dict[str, Any], error: dict[str, Any] | None) -> None:
        moment = self.now()
        with self.database._transaction() as connection:
            row = connection.execute("SELECT * FROM workspace_news_cache WHERE cache_key=?", (_key(agent_id),)).fetchone()
            if not row or row["refresh_id"] != refresh_id:
                return
            # Preserve useful successful cache contents when a failed refresh
            # has no accepted items. An honest successful empty result replaces
            # an older result so its age is never hidden.
            save_snapshot = bool(snapshot["items"]) or error is None
            complete = error is None
            next_at = max(row["next_refresh_at"], _stamp(moment + timedelta(seconds=error["retry_after_seconds"]))) if error else row["next_refresh_at"]
            if error and error["code"] == "newsdata_daily_credit_limit":
                # Local credits reset on the same Dubai date as legacy runs.
                # Do not repeatedly attempt an exhausted allowance every 15m.
                next_at = _stamp(moment + timedelta(seconds=error["retry_after_seconds"]))
            connection.execute("""UPDATE workspace_news_cache SET snapshot_json=?,
                last_successful_refresh_at=?,next_refresh_at=?,refresh_id=NULL,lease_until=NULL,error_json=?
                WHERE cache_key=?""", (_json(snapshot) if save_snapshot else row["snapshot_json"],
                _stamp(moment) if complete else row["last_successful_refresh_at"], next_at,
                _json({**error, "retrieval": snapshot.get("retrieval", {})}) if error else None, _key(agent_id)))
            connection.execute("""UPDATE workspace_news_requests SET state=?,finished_at=?,error_json=?
                WHERE request_id=? AND state='running'""", ("completed" if complete else "failed", _stamp(moment),
                _json(error) if error else None, refresh_id))
            news_error = snapshot.get("retrieval", {}).get("newsdata", {}).get("error") or error
            cooldown = news_error and (news_error["code"] in {"newsdata_rate_limited", "newsdata_provider_credit_limit",
                "newsdata_auth_failed", "newsdata_access_denied", "newsdata_unavailable", "newsdata_timeout", "newsdata_connection_error"}
                or news_error.get("retry_after_source") == "provider")
            quota = snapshot.get("quota", {})
            provider_error = news_error if cooldown else None
            if not provider_error and (quota.get("credits_remaining") == 0 or quota.get("rate_window_remaining") == 0):
                code = "newsdata_provider_credit_limit" if quota.get("credits_remaining") == 0 else "newsdata_rate_limited"
                provider_error = _safe_error(StoreError(code, "The provider reports no remaining allowance.", 429))
                # Exhausting the last allowance limits the next request; it
                # does not invalidate the page that completed successfully.
                if error is None:
                    provider_error["after_snapshot_id"] = snapshot["snapshot_id"]
                next_at = max(next_at, _stamp(moment + timedelta(seconds=REFRESH_INTERVAL_SECONDS)))
                LOGGER.warning("news_feed_event %s", _json({"event": "news_feed_provider_cooldown",
                    "refresh_id": refresh_id, "agent_id": agent_id,
                    "error_reference": provider_error["reference"], "error_code": provider_error["code"],
                    "accepted_count": len(snapshot["items"]), "requests": snapshot["pagination"]["requests"]}))
            connection.execute("""UPDATE workspace_news_provider SET refresh_id=NULL,lease_until=NULL,
                cooldown_until=?,error_json=? WHERE provider='newsdata' AND refresh_id=?""",
                ((snapshot.get("provider_retry_at") or _stamp(moment + timedelta(seconds=provider_error["retry_after_seconds"])))
                    if provider_error else None, _json(provider_error) if provider_error else None, refresh_id))

    def translations(self, agent_id: str, language: str) -> dict[str, dict[str, Any]]:
        with self.database._connect() as connection:
            rows = connection.execute("SELECT * FROM workspace_news_translations WHERE cache_key=? AND language=?",
                                      (_key(agent_id), language)).fetchall()
        return {row["article_id"]: dict(row) for row in rows}


async def _collect(store: FeedStore, agent_id: str, refresh_id: str, state: dict[str, Any]) -> None:
    size, cursor, cursors = _page_size(), None, set()
    seen = {"ids": set(), "urls": set(), "titles": []}
    previous_date, descending = None, True
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False) as client:
        for _ in range(MAX_PAGES):
            remaining = MAX_RAW_ITEMS - state["pagination"]["raw_count"]
            if remaining <= 0:
                state["pagination"]["stop_reason"] = "raw_item_limit"
                return
            charge_id = store.charge(agent_id, refresh_id)
            state["pagination"]["requests"] += 1
            state["pagination"]["credits_reserved"] += 1
            try:
                data, quota = await _request_page(client, agent_id=agent_id, size=min(size, remaining), cursor=cursor)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                if (detail.get("confirmed_nonchargeable_validation") is True and detail.get("provider_status") in {400, 422}
                        and detail.get("provider_code") in _NONCHARGEABLE_VALIDATION_CODES):
                    store.reconcile(charge_id, refresh_id, "invalid_request")
                    state["pagination"]["credits_reconciled"] += 1
                raise
            # Both fields must agree that this was a successful no-results
            # response. A malformed/contradictory empty page is not evidence
            # that the provider charged nothing.
            if data["totalResults"] == 0 and data["results"] == [] and not data.get("nextPage"):
                store.reconcile(charge_id, refresh_id, "empty_success")
                state["pagination"]["credits_reconciled"] += 1
            state["quota"] = quota
            state["pagination"]["provider_total_results"] = data["totalResults"]
            raw = data["results"]
            state["pagination"]["raw_count"] += len(raw)
            dates, invalid_dates = [], False
            for source in raw:
                publication = newsdata._publication(source.get("pubDate"), source.get("pubDateTZ")) if isinstance(source, dict) else None
                if publication:
                    current = publication[0]
                    dates.append(current)
                    latest = state.get("observed_latest_provider_publication_at")
                    if latest is None or current > newsdata._moment(latest):
                        state["observed_latest_provider_publication_at"] = _stamp(current)
                    earliest = state.get("observed_earliest_provider_publication_at")
                    if earliest is None or current < newsdata._moment(earliest):
                        state["observed_earliest_provider_publication_at"] = _stamp(current)
                    if previous_date is not None and current > previous_date:
                        descending = False
                    previous_date = current
                else:
                    invalid_dates = True
            for source in raw:
                item, reason = _accept(source, agent_id, state["research_window"], seen)
                if item is not None:
                    state["items"].append(item)
                else:
                    state["excluded"][reason] += 1
            if state["pagination"]["raw_count"] >= MAX_RAW_ITEMS:
                state["pagination"]["stop_reason"] = "raw_item_limit"
                return
            if (dates and not invalid_dates and descending
                    and max(dates) < newsdata._moment(state["research_window"]["start"])):
                state["pagination"]["stop_reason"] = "window_exhausted"
                return
            if not raw or not data.get("nextPage"):
                state["pagination"]["stop_reason"] = "provider_end"
                return
            next_cursor = data["nextPage"]
            if next_cursor == cursor or next_cursor in cursors:
                state["pagination"]["stop_reason"] = "repeated_cursor"
                return
            if quota["credits_remaining"] == 0 or quota["rate_window_remaining"] == 0:
                code = "newsdata_provider_credit_limit" if quota["credits_remaining"] == 0 else "newsdata_rate_limited"
                raise StoreError(code, "The provider reports no remaining allowance.", 429)
            cursors.add(next_cursor)
            cursor = next_cursor
        state["pagination"]["stop_reason"] = "page_limit"


def _fallback_reason(state: dict[str, Any], error: dict[str, Any] | None) -> str | None:
    if error:
        return "newsdata_unavailable"
    if state["pagination"]["stop_reason"] in {"page_limit", "repeated_cursor", "deadline"}:
        return "newsdata_partial"
    if not state["items"]:
        return "newsdata_empty"
    if len(state["items"]) < 3:
        return "insufficient_articles"
    if len({item["publisher"].strip().casefold() for item in state["items"]}) < 2:
        return "insufficient_publishers"
    return None


def _web_item(source: dict[str, Any], agent_id: str, window: dict[str, str]) -> dict[str, Any] | None:
    """Accept actual publisher evidence, never a search snippet as fresh news."""
    verification = source.get("publication_verification", {})
    if (source.get("role") != "fresh" or source.get("publisher_text_verified") is not True
            or not isinstance(verification, dict) or verification.get("verified") is not True):
        return None
    try:
        published = newsdata._moment(source.get("published_at"))
    except (ValueError, TypeError, OverflowError):
        return None
    if not newsdata._moment(window["start"]) <= published <= newsdata._moment(window["end"]):
        return None
    title = newsdata._text(source.get("title"), newsdata.MAX_HEADLINE_CHARS)
    summary = newsdata._text(source.get("description") or source.get("excerpt"), newsdata.MAX_DESCRIPTION_CHARS)
    url = runtime._safe_source_url(source.get("url"))
    if (not title or not url or len(url) > MAX_ARTICLE_URL_CHARS
            or (agent_id != "historical-reading-companion" and not _relevant(agent_id, title, summary))):
        return None
    identifier = "web:" + hashlib.sha256(runtime._canonical_url(url).encode()).hexdigest()[:32]
    letters = re.findall(r"[A-Za-z\u0600-\u06ff]", title + " " + summary)
    arabic = sum("\u0600" <= letter <= "\u06ff" for letter in letters)
    language = "ar" if letters and arabic > len(letters) / 2 else "en"
    return {**source, "id": identifier, "source_id": identifier, "article_id": identifier,
        "source_ids": [identifier], "headline": title, "summary": summary,
        "publisher": newsdata._text(source.get("publisher"), 200) or urlsplit(url).hostname,
        "url": url, "published_at": _stamp(published), "published_date": _stamp(published),
        "published_at_dubai": published.astimezone(DUBAI).isoformat(),
        "original_headline": title, "original_summary": summary, "original_language": language,
        "headline_en": title if language == "en" else None, "summary_en": summary if language == "en" else None,
        "headline_ar": title if language == "ar" else None, "summary_ar": summary if language == "ar" else None,
        "language": language, "summary_available": bool(summary), "translation_status": "original",
        "classification": "within_window", "source_kind": source.get("source_kind") or runtime._news_source_kind(title, url),
        "evidence_level": "publisher_page_verified", "factual_basis": "publisher_page",
        "publisher_text_verified": True, "publication_verification": verification}


def _add_web_sources(state: dict[str, Any], agent_id: str, sources: list[dict[str, Any]]) -> int:
    accepted = 0
    for source in sources:
        item = _web_item(source, agent_id, state["research_window"])
        if item is None:
            state["excluded"]["web_not_fresh_or_relevant"] += 1
            continue
        canonical = runtime._canonical_url(item["url"])
        same_url = next((index for index, old in enumerate(state["items"])
            if runtime._canonical_url(old["url"]) == canonical), None)
        if same_url is not None:
            state["items"][same_url] = item
            accepted += 1
            continue
        titles = [runtime._headline_key(old["original_headline"]) for old in state["items"]]
        if _near_duplicate(runtime._headline_key(item["original_headline"]), titles):
            state["excluded"]["web_duplicate"] += 1
        elif len(state["items"]) < MAX_ITEMS:
            state["items"].append(item)
            accepted += 1
    return accepted


def _refresh(store: FeedStore, agent_id: str, claim: tuple[str, dict[str, str]], *,
             timeout_seconds: float | None = None, allow_web_fallback: bool = False) -> None:
    refresh_id, window = claim
    row = store.read(agent_id) or {}
    state: dict[str, Any] = {"snapshot_id": refresh_id, "agent_id": agent_id, "provider": "newsdata",
        "started_at": row.get("checked_at") if row.get("refresh_id") == refresh_id else _stamp(store.now()),
        "research_window": window, "items": [], "excluded": Counter(), "quota": {},
        "pagination": {"requests": 0, "raw_count": 0, "accepted_count": 0, "page_limit": MAX_PAGES,
            "credits_reserved": 0, "credits_reconciled": 0,
            "raw_item_limit": MAX_RAW_ITEMS, "stop_reason": None, "provider_total_results": None},
        "provider_plan": plan_metadata(), "query_version": QUERY_VERSION,
        "retrieval": {"options": ["newsdata", "web_search"], "newsdata": {},
            "web_search": {"attempted": False, "status": "not_requested", "error": None}}}
    error, exception_type = None, None
    try:
        provider = store.provider_state()
        blocked_error = _decode(provider.get("error_json"), {})
        if (provider.get("cooldown_until") and provider["cooldown_until"] > _stamp(store.now())
                and blocked_error.get("code") != "newsdata_daily_credit_limit"):
            error = {**blocked_error, "retry_after_seconds": max(1, math.ceil(
                (newsdata._moment(provider["cooldown_until"]) - store.now()).total_seconds()))}
            error.pop("after_snapshot_id", None)
            state["provider_retry_at"] = provider["cooldown_until"]
            state["pagination"]["stop_reason"] = "provider_cooldown"
            raise StoreError("newsdata_provider_cooldown", "The provider is cooling down; no NewsData request was made.", 429)
        if not newsdata.is_ready():
            raise StoreError("newsdata_not_configured", "NewsData is not configured.", 503)
        # asyncio cancellation closes in-flight HTTP work at the total refresh
        # deadline; socket phase timeouts alone are not a total wall-clock cap.
        async def bounded() -> None:
            await asyncio.wait_for(_collect(store, agent_id, refresh_id, state),
                timeout=MAX_REFRESH_SECONDS if timeout_seconds is None else min(MAX_REFRESH_SECONDS, timeout_seconds))
        asyncio.run(bounded())
    except TimeoutError:
        exception_type = "TimeoutError"
        error = _safe_error(StoreError("newsdata_timeout", "The total refresh deadline was reached.", 504))
        state["pagination"]["stop_reason"] = "deadline"
    except Exception as exc:
        exception_type = type(exc).__name__
        retry_after = REFRESH_INTERVAL_SECONDS
        if isinstance(exc, StoreError) and exc.code == "newsdata_daily_credit_limit":
            local = store.now().astimezone(DUBAI)
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            retry_after = max(1, math.ceil((midnight - local).total_seconds()))
        if not error:
            error = _safe_error(exc, retry_after=retry_after)
            state["pagination"]["stop_reason"] = error["code"]
    state["pagination"]["accepted_count"] = len(state["items"])
    if error and error.get("scope") == "provider" and not state.get("provider_retry_at"):
        state["provider_retry_at"] = _stamp(store.now() + timedelta(seconds=error["retry_after_seconds"]))
    state["retrieval"]["newsdata"] = {"status": "error" if error else "complete", "error": error,
        "raw_count": state["pagination"]["raw_count"], "accepted_count": len(state["items"])}
    allow_web_fallback = store.decide_fallback(agent_id, refresh_id, allow_web_fallback)
    fallback_reason = _fallback_reason(state, error)
    if allow_web_fallback and fallback_reason:
        from app.retrieval import run_feed_discovery
        web = {"attempted": True, "status": "error", "reason": fallback_reason, "error": None,
            "accepted_count": 0, "verified_source_count": 0}
        try:
            discovered = run_feed_discovery(store.database, agent_id, refresh_id, window)
            web.update(status="complete", accepted_count=_add_web_sources(state, agent_id, discovered["sources"]),
                verified_source_count=discovered["verified_source_count"], excluded=discovered["excluded"],
                stages=discovered["stages"], usage=discovered["web_search"])
            if web["accepted_count"]:
                state["provider"] = "newsdata+web_search" if state["pagination"]["accepted_count"] else "web_search"
        except Exception as exc:
            web["error"] = _safe_error(exc)
            if not error and not state["items"]:
                error = web["error"]
        state["retrieval"]["web_search"] = web
    elif allow_web_fallback:
        state["retrieval"]["web_search"]["status"] = "not_needed"
    state["items"].sort(key=lambda item: (item["published_at"], item["id"]), reverse=True)
    state["excluded"] = dict(state["excluded"])
    latest = state.get("observed_latest_provider_publication_at")
    provider_age = max(0, math.floor((newsdata._moment(window["end"]) - newsdata._moment(latest)).total_seconds())) if latest else None
    state["provider_age_seconds"] = provider_age
    # The newest returned article is evidence of this query's availability,
    # not proof of the account tier or of comprehensive provider coverage.
    state["suspected_provider_delay"] = bool(provider_age is not None and 12 * 3600 <= provider_age <= 14 * 3600
        and not state["pagination"]["accepted_count"] and state["pagination"]["stop_reason"] == "window_exhausted")
    state["partial"] = bool(error or state["retrieval"]["web_search"].get("error")) or state["pagination"]["stop_reason"] in {"page_limit", "repeated_cursor", "deadline"}
    state["completed_at"] = _stamp(store.now())
    state["pagination"]["accounted_credits"] = state["pagination"]["credits_reserved"] - state["pagination"]["credits_reconciled"]
    store.finish(agent_id, refresh_id, state, error)
    event = {"event": "news_feed_refresh_failed" if error else "news_feed_refresh_completed",
        "refresh_id": refresh_id, "agent_id": agent_id, "requests": state["pagination"]["requests"],
        "raw_count": state["pagination"]["raw_count"], "accepted_count": len(state["items"]),
        "stop_reason": state["pagination"]["stop_reason"], "partial": state["partial"],
        "observed_latest_provider_publication_at": state.get("observed_latest_provider_publication_at"),
        "observed_earliest_provider_publication_at": state.get("observed_earliest_provider_publication_at")}
    if error:
        event.update(error_reference=error["reference"], error_code=error["code"], exception_type=exception_type)
        event.update({key: error[key] for key in ("provider_status", "provider_request_id") if key in error})
    (LOGGER.warning if error else LOGGER.info)("news_feed_event %s", _json(event))


def _display_items(store: FeedStore, agent_id: str, items: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    translations = store.translations(agent_id, language)
    result = []
    for original in items[:MAX_ITEMS]:
        item = dict(original)
        translation = translations.get(item["article_id"])
        if language == item["original_language"]:
            item["translation_status"] = "original"
        elif translation and translation["fingerprint"] == _fingerprint(item):
            item.update(headline=translation["headline"], summary=translation["summary"], language=language,
                        translation_status="translated")
            item["headline_" + language], item["summary_" + language] = translation["headline"], translation["summary"]
        else:
            item["translation_status"] = "original_only"
        item["requested_language"] = language
        result.append(item)
    return result


def _view(store: FeedStore, agent_id: str, language: str, *, started: bool = False,
          requested_window: dict[str, Any] | None = None, request_id: str | None = None,
          accepted: bool = False, joined: bool = False) -> dict[str, Any]:
    now, row = store.now(), store.read(agent_id) or {}
    now_text = _stamp(now)
    provider = store.provider_state()
    provider_saved_error = _decode(provider.get("error_json"), {})
    provider_blocked = bool(provider.get("cooldown_until") and provider["cooldown_until"] > now_text
        and provider_saved_error.get("code") != "newsdata_daily_credit_limit")
    active = bool(row.get("refresh_id") and row.get("lease_until") and row["lease_until"] > now_text)
    request = store.request_info(agent_id, request_id)
    queued = bool(request and request["state"] == "queued")
    snapshot = _decode(row.get("snapshot_json"), {})
    completed = snapshot.get("completed_at")
    age = max(0, (now - newsdata._moment(completed)).total_seconds()) if completed else None
    provider_error = provider_saved_error if provider_blocked else None
    if (provider_error and provider_error.get("after_snapshot_id") == snapshot.get("snapshot_id")
            and age is not None and age < REFRESH_INTERVAL_SECONDS):
        provider_error = None
    error = _decode(row.get("error_json")) or provider_error
    if (request and request["state"] == "failed" and not request.get("started_at") and request.get("error_json")
            and request["requested_at"] >= (row.get("checked_at") or "")):
        error = _decode(request["error_json"])
    retrieval = (error.get("retrieval") if error else None) or snapshot.get("retrieval", {})
    if error:
        error = {key: value for key, value in error.items() if key not in {"after_snapshot_id", "retrieval"}}
    expired_claim = bool(row.get("refresh_id") and not active)
    if expired_claim and not error:
        error = _safe_error(StoreError("newsdata_refresh_interrupted", "The previous refresh was interrupted.", 503))
        error["reference"] = "news_interrupted_" + hashlib.sha256(row["refresh_id"].encode()).hexdigest()[:16]
        LOGGER.warning("news_feed_event %s", _json({"event": "news_feed_refresh_interrupted",
            "refresh_id": row["refresh_id"], "agent_id": agent_id,
            "error_reference": error["reference"], "error_code": error["code"]}))
    stale = bool(snapshot) and (age >= REFRESH_INTERVAL_SECONDS or bool(error))
    # Dashboard reads use a current 12-hour window even while reusing an older
    # snapshot. A report explicitly supplies its frozen cutoff, so an article
    # cannot disappear merely because the worker starts a little later.
    display_window = _window(requested_window, now)
    start, end = newsdata._moment(display_window["start"]), newsdata._moment(display_window["end"])
    eligible = [item for item in snapshot.get("items", []) if start <= newsdata._moment(item["published_at"]) <= end]
    window_excluded = len(snapshot.get("items", [])) - len(eligible)
    relevant = [item for item in eligible if (agent_id == "historical-reading-companion" and item.get("publisher_text_verified") is True) or _relevant(
        agent_id, item.get("original_headline") or item["headline"],
        item.get("original_summary") or item.get("summary", ""))]
    relevance_excluded = len(eligible) - len(relevant)
    excluded = dict(snapshot.get("excluded", {}))
    if relevance_excluded:
        excluded["irrelevant_cached"] = relevance_excluded
    items = _display_items(store, agent_id, relevant, language)
    status = ("loading" if active or queued else "provider_error" if error else
              "empty" if snapshot and not items else "fresh" if snapshot and age < 2 else "cached" if snapshot else "empty")
    next_at = row.get("next_refresh_at")
    if provider_blocked:
        next_at = max(next_at or "", provider["cooldown_until"])
    if error:
        retry_at = provider["cooldown_until"] if provider_blocked else None if error.get("scope") == "provider" else next_at
        error = {**error, "retry_after_seconds": max(0, math.ceil((newsdata._moment(retry_at) - now).total_seconds())) if retry_at else 0}
    verified_count = sum(item.get("publisher_text_verified") is True for item in items)
    limitations = ["coverage_not_guaranteed"]
    if verified_count < len(items) or not items:
        limitations.append("provider_metadata_only" if not verified_count else "some_sources_metadata_only")
    plan = snapshot.get("provider_plan") or plan_metadata()
    if plan["configured_plan"] == "free":
        limitations.append("free_plan_12_hour_delay")
    elif plan["configured_plan"] == "unknown":
        limitations.append("provider_plan_unverified")
    if any(item["translation_status"] == "original_only" for item in items):
        limitations.append("translation_unavailable_for_some_items")
    if snapshot.get("partial"):
        limitations.append("partial_retrieval")
    if snapshot.get("suspected_provider_delay"):
        limitations.append("provider_delay")
    return {"agent_id": agent_id, "provider": snapshot.get("provider", "newsdata"), "status": status, "items": items,
        "actual_count": len(items), "maximum_items": MAX_ITEMS, "coverage_hours": 12,
        "refresh_mode": REFRESH_MODE,
        "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS, "last_successful_refresh_at": row.get("last_successful_refresh_at"),
        "checked_at": row.get("checked_at"), "next_refresh_at": next_at, "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_started_at": snapshot.get("started_at"),
        "research_window": snapshot.get("research_window"), "display_window": display_window,
        "cache_age_seconds": math.floor(age) if age is not None else None,
        "cached_article_count": len(snapshot.get("items", [])), "window_excluded_from_cache": window_excluded,
        "relevance_excluded_from_cache": relevance_excluded,
        "cached": bool(snapshot), "stale": stale, "partial": bool(snapshot.get("partial")), "error": error,
        "refresh_started": started, "refresh_accepted": accepted, "refresh_joined": joined,
        "refresh_state": "queued" if queued else "running" if active else "idle", "refresh_queued": queued,
        "refresh_method": (("web_search" if provider_blocked else "newsdata_with_web_fallback")
            if request and request["allow_web_fallback"] else "newsdata") if queued or active else None,
        "refresh_request_id": request["request_id"] if request else row.get("refresh_id"),
        "refresh_requested_at": request["requested_at"] if request else row.get("checked_at"),
        "queue_position": request.get("queue_position") if queued else None,
        "provider_retry_at": provider["cooldown_until"] if provider_blocked else None,
        "provider_cooldown_scope": "newsdata" if provider_blocked else None,
        "pagination": snapshot.get("pagination", {}), "excluded": excluded,
        "retrieval": retrieval, "retrieval_options": ["newsdata", "web_search"],
        "provider_plan": plan, "limitation_codes": limitations, "requested_language": language,
        "observed_latest_provider_publication_at": snapshot.get("observed_latest_provider_publication_at"),
        "observed_earliest_provider_publication_at": snapshot.get("observed_earliest_provider_publication_at"),
        "provider_age_seconds": snapshot.get("provider_age_seconds"),
        "suspected_provider_delay": bool(snapshot.get("suspected_provider_delay")),
        "publication_basis": ("publisher_page" if items and verified_count == len(items)
            else "mixed_provider_metadata_and_publisher_pages" if verified_count else "newsdata_pubDate_pubDateTZ"),
        "publisher_text_verified": bool(items) and verified_count == len(items),
        "publisher_verified_count": verified_count, "display_timezone": "Asia/Dubai",
        "query": QUERIES[agent_id]}


def _run_queued_refresh(store: FeedStore, agent_id: str, request_id: str) -> None:
    """Finish one accepted click, without creating or scheduling more work."""
    deadline = time.monotonic() + MAX_QUEUE_WAIT_SECONDS
    while time.monotonic() < deadline:
        request = store.request_info(agent_id, request_id)
        if not request or request["state"] != "queued":
            return
        claim = store.claim_request(request_id)
        if claim:
            # Read after the atomic claim: a report/manual join may have
            # enabled fallback while this request was still queued.
            request = store.request_info(agent_id, request_id)
            _refresh(store, agent_id, claim, allow_web_fallback=bool(request["allow_web_fallback"]))
            return
        time.sleep(.2)
    store.fail_queued(request_id)


def _start_queued_refresh(store: FeedStore, agent_id: str, request_id: str) -> bool:
    """Start at most one local waiter; an explicit click can recover an orphan.

    A persisted background flag is intent, not proof of a living thread. Across
    processes the existing SQLite claim still permits only one provider worker.
    """
    key, token = (str(store.database.path), request_id), object()
    with _WAITER_LOCK:
        if key in _WAITERS:
            return False
        _WAITERS[key] = token

    def run() -> None:
        try:
            _run_queued_refresh(store, agent_id, request_id)
        finally:
            with _WAITER_LOCK:
                if _WAITERS.get(key) is token:
                    _WAITERS.pop(key, None)

    try:
        threading.Thread(target=run, args=(), name="aihub-news-" + agent_id, daemon=True).start()
    except Exception:
        with _WAITER_LOCK:
            if _WAITERS.get(key) is token:
                _WAITERS.pop(key, None)
        raise
    return True


def _start_pending_refreshes(store: FeedStore) -> set[str]:
    """An explicit action may resume accepted manual intents after a restart.

    Waking all pending manual entries prevents an orphaned FIFO head from
    blocking a different agent. This neither creates requests nor replays an
    active/ambiguous provider attempt, and is never called from a passive read.
    """
    return {request["request_id"] for request in store.pending_background()
        if _start_queued_refresh(store, request["agent_id"], request["request_id"])}


def get_feed(agent_id: str, language: str = "ar", *, refresh: bool = False, force: bool = False,
             database: Store | None = None) -> dict[str, Any]:
    """Read the saved feed without provider work unless explicitly requested.

    Empty and expired snapshots stay cache-only on passive reads. ``refresh``
    or ``force`` expresses an explicit action and bypasses successful cache age.
    Concurrent requests join or enter a bounded queue. Provider cooldowns and
    daily credits remain enforced. ``next_refresh_at`` describes cache age or a
    previous failure; it never schedules provider work.
    """
    _key(agent_id)
    _language(language)
    require_capability(agent_id, "tools", "newsdata")
    store = FeedStore(database)
    request, _, joined = store.request(agent_id, background=True, allow_web_fallback=True) if refresh or force else (None, False, False)
    launched = _start_pending_refreshes(store) if request else set()
    launch = bool(request and request["request_id"] in launched)
    current = store.request_info(agent_id, request["request_id"]) if request else None
    return _view(store, agent_id, language, started=bool(launch and current and current.get("started_at")),
        request_id=request["request_id"] if request else None, accepted=bool(request), joined=joined)


def _topic_matches(item: dict[str, Any], topic: str, agent_id: str) -> bool:
    profile = get_profile(agent_id)
    if not topic.strip() or topic.strip() in {profile["default_topic_ar"], profile["default_topic_en"]}:
        return True
    # Task wording is not subject evidence: an article saying "answer the
    # question" must not match a request to answer a central-bank question.
    ignored = {"about", "latest", "news", "with", "from", "this", "that", "what", "please", "report", "briefing",
               "give", "today", "analysis", "answer", "answers", "question", "questions", "briefly", "brief",
               "explain", "explanation", "tell", "describe", "summarize", "summary", "details", "detail",
               "could", "would", "should", "does", "have", "your", "which", "where", "when", "research",
               "the", "and", "for", "how", "why", "can", "you", "are", "was", "has", "its",
               "أخبار", "اخبار", "تقرير", "اليوم", "أحدث", "احدث", "حول", "تحليل", "أجب", "اجب",
               "الإجابة", "الاجابة", "السؤال", "بإيجاز", "باختصار", "اشرح", "وضح", "يرجى", "أخبرني", "اخبرني",
               "فضلا", "فضلاً", "رجاء", "رجاءً", "كيف", "ماذا", "لماذا", "التي", "الذي", "لدى", "هذا", "هذه"}
    short_subjects = {value.casefold() for value in re.findall(r"\b[A-Z]{2}\b", topic)}
    terms = [term for term in re.findall(r"[^\W_]+", topic.casefold())
             if (len(term) >= 3 or term in short_subjects) and term not in ignored]
    if not terms:
        return True
    return _contains(item["original_headline"] + " " + item["original_summary"], terms[:30])


def get_report_feed(agent_id: str, topic: str = "", research_window: dict[str, Any] | None = None,
                    language: str = "ar", progress: Callable[[str], None] | None = None, *,
                    database: Store | None = None, allow_web_fallback: bool = False) -> dict[str, Any]:
    """Fetch for this generation, or join a fetch begun after its frozen cutoff.

    A recent snapshot predating this report's request is insufficient. The
    shared queue and synchronous fetch share one wait deadline. Provider failures
    stay explicit even when qualifying older cached evidence is retained.
    """
    _key(agent_id)
    _language(language)
    require_capability(agent_id, "tools", "newsdata")
    store = FeedStore(database)
    requested = _window(research_window, store.now())
    if progress:
        progress("searching")
    deadline = time.monotonic() + MAX_REFRESH_SECONDS
    wait_until = _stamp(store.now() + timedelta(seconds=MAX_REFRESH_SECONDS))
    view = _view(store, agent_id, language, requested_window=requested)
    fresh_for_request = bool(view["snapshot_started_at"] and view["snapshot_started_at"] >= requested["end"]
        and (view["research_window"] or {}).get("end", "") >= requested["end"] and not view["error"])
    request = None
    if not fresh_for_request:
        request, _, _ = store.request(agent_id, requested, after=requested["end"],
            allow_web_fallback=allow_web_fallback, wait_until=wait_until)
        if request:
            _start_pending_refreshes(store)
        if request is None:
            # A valid previous empty snapshot does not turn a newly blocked
            # request into a successful fresh check. Preserve that distinction
            # for the report's own independently reserved web-search fallback.
            provider_error = _decode(store.provider_state().get("error_json"), {})
            if provider_error:
                view["error"] = {key: value for key, value in provider_error.items() if key != "after_snapshot_id"}
                view["partial"] = True
        while request is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                store.fail_queued(request["request_id"], foreground_only=True)
                raise StoreError("newsdata_timeout", "The shared news refresh did not finish within the report's wait limit.", 504)
            # A report may follow a manual request but must not take over its
            # background work with the report's shorter remaining deadline.
            claim = store.claim_request(request["request_id"], foreground_only=True)
            if claim:
                current = store.request_info(agent_id, request["request_id"])
                _refresh(store, agent_id, claim, timeout_seconds=max(0.0, deadline - time.monotonic()),
                    **({"allow_web_fallback": True} if current["allow_web_fallback"] else {}))
            current = store.request_info(agent_id, request["request_id"])
            view = _view(store, agent_id, language, requested_window=requested, request_id=request["request_id"])
            if current and current["state"] in {"completed", "failed"}:
                if current["state"] == "failed" and current.get("error_json"):
                    view["error"] = _decode(current["error_json"])
                    view["partial"] = True
                break
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(.2, remaining))
    if not view["snapshot_id"] and (view["error"] or view["status"] == "loading"):
        code = view["error"]["code"] if view["error"] else "newsdata_refresh_in_progress"
        error = StoreError(code, "No usable shared news snapshot is available; see the feed status.", 503)
        if view["error"]:
            error.feed_error_reference = view["error"]["reference"]
        raise error
    start, end = newsdata._moment(requested["start"]), newsdata._moment(requested["end"])
    items = [item for item in view["items"] if start <= newsdata._moment(item["published_at"]) <= end
             and _topic_matches(item, topic, agent_id)]
    if view["error"] and not items:
        error = StoreError(view["error"]["code"], "The provider refresh failed and no cached articles qualify for the requested window.", 503)
        error.reference = view["error"]["reference"]
        error.feed_error_reference = view["error"]["reference"]
        raise error
    sources = [{"id": item["id"], "title": item["original_headline"], "headline": item["headline"],
        "publisher": item["publisher"], "url": item["url"], "published_at": item["published_at"],
        "excerpt": item["original_summary"], "description": item["original_summary"], "role": "fresh",
        "source_kind": item["source_kind"], "publication_verification": item["publication_verification"],
        "publisher_text_verified": item.get("publisher_text_verified") is True,
        "evidence_level": item.get("evidence_level", "newsdata_structured_metadata"),
        "factual_basis": item.get("factual_basis", "newsdata_structured_metadata"),
        **{key: item[key] for key in ("citation_annotations", "content_sha256", "retrieved_at", "source_category",
            "syndicated_sources", "syndication_agency") if key in item},
        "original_language": item["original_language"]} for item in items]
    return {**view, "items": items, "sources": sources, "actual_count": len(items),
        "requested_research_window": requested, "freshness": {"window_start": requested["start"],
            "window_end": requested["end"], "snapshot_window": view["research_window"],
            "retrieval_started_at": view["snapshot_started_at"],
            "checked_after_request": bool(view["snapshot_started_at"] and view["snapshot_started_at"] >= requested["end"]
                and (view["research_window"] or {}).get("end", "") >= requested["end"] and not view["error"]),
            "coverage_complete": False, "timestamp_basis": view["publication_basis"],
            "publisher_text_verified": bool(items) and all(item.get("publisher_text_verified") is True for item in items),
            "observed_latest_provider_publication_at": view["observed_latest_provider_publication_at"],
            "observed_earliest_provider_publication_at": view["observed_earliest_provider_publication_at"],
            "provider_age_seconds": view["provider_age_seconds"], "suspected_provider_delay": view["suspected_provider_delay"]},
        "diagnostics": {"pagination": view["pagination"], "excluded": view["excluded"],
            "topic_or_window_excluded": view["actual_count"] - len(items), "limitation_codes": view["limitation_codes"]}}


def apply_translations(agent_id: str, items: list[dict[str, Any]], language: str, *,
                       database: Store | None = None) -> int:
    """Cache safe translations produced by an already-budgeted report.

    This helper never calls a model. Identity, link, date, original text and
    relevance remain server-owned. Caller must have accounted for translation
    in its report's existing dollar reservation before passing these fields.
    """
    _language(language)
    store = FeedStore(database)
    row = store.read(agent_id)
    snapshot = _decode(row.get("snapshot_json"), {}) if row else {}
    originals = {item["id"]: item for item in snapshot.get("items", [])}
    accepted = 0
    with store.database._transaction() as connection:
        for value in items[:MAX_ITEMS]:
            if not isinstance(value, dict):
                continue
            original = originals.get(value.get("id") or value.get("source_id"))
            if not original or original["original_language"] == language:
                continue
            if value.get("url") and runtime._safe_source_url(value["url"]) != original["url"]:
                continue
            if value.get("original_headline") and value["original_headline"] != original["original_headline"]:
                continue
            if "original_summary" in value and value["original_summary"] != original["original_summary"]:
                continue
            headline = value.get("headline_" + language) or value.get("headline")
            summary = value.get("summary_" + language)
            if summary is None:
                summary = value.get("summary")
            if (not isinstance(headline, str) or not headline.strip() or len(headline) > 700
                    or not isinstance(summary, str) or len(summary) > 2600
                    or (not original["original_summary"] and summary.strip())
                    or re.search(r"https?://|<[^>]+>|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", headline + summary, re.IGNORECASE)):
                continue
            connection.execute("""INSERT INTO workspace_news_translations
                (cache_key,article_id,language,fingerprint,headline,summary,created_at) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(cache_key,article_id,language) DO UPDATE SET fingerprint=excluded.fingerprint,
                headline=excluded.headline,summary=excluded.summary,created_at=excluded.created_at""",
                (_key(agent_id), original["article_id"], language, _fingerprint(original), headline.strip(), summary.strip(), _stamp(store.now())))
            accepted += 1
        # Only the current bounded snapshot needs translations. Old immutable
        # reports retain their own text independently of this live cache.
        identifiers = [item["article_id"] for item in originals.values()]
        if identifiers:
            placeholders = ",".join("?" for _ in identifiers)
            connection.execute(f"DELETE FROM workspace_news_translations WHERE cache_key=? AND article_id NOT IN ({placeholders})",
                               (_key(agent_id), *identifiers))
    return accepted
