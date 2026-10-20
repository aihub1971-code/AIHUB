"""Structured Iran-war news from NewsData.io, one explicit provider page at a time.

Provider metadata supplies article identity, publication dates and links. OpenAI
may translate the original English headline/description, but never selects URLs
or manufactures the provider result list. Production uses service.execute_report
for the shared report quota, dollar reservation and separate NewsData credits.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException

from .config import get_setting
from .newsdata_translation import translate_items, translation_plan


NEWSDATA_LATEST_URL = "https://newsdata.io/api/1/latest"
NEWSDATA_QUERY = "iran war"
MAX_NEWSDATA_PAGE_SIZE = 50
MAX_NEWSDATA_RESULTS = 20
DEFAULT_NEWSDATA_PAGE_SIZE = 10
MAX_NEWSDATA_RESPONSE_BYTES = 2000000
MAX_CURSOR_CHARS = 2048
MAX_HEADLINE_CHARS = 500
MAX_DESCRIPTION_CHARS = 2000
_UTC = timezone.utc
_PLAN_NOTICE = re.compile(
    r"^(?:only\s+available\s+(?:in|on|for)\s+(?:paid|premium|professional)|"
    r"(?:full\s+)?content\s+(?:is\s+)?(?:only\s+)?available\s+(?:in|on|for)\s+(?:paid|premium)|"
    r"upgrade\s+(?:your\s+)?(?:plan|subscription))", re.IGNORECASE)


def _fail(code: str, message: str, status: int = 502) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def is_ready() -> bool:
    return bool(get_setting("NEWSDATA_API_KEY", ""))


def page_size_setting() -> int:
    try:
        size = int(get_setting("NEWSDATA_PAGE_SIZE", str(DEFAULT_NEWSDATA_PAGE_SIZE)) or DEFAULT_NEWSDATA_PAGE_SIZE)
    except (TypeError, ValueError):
        raise _fail("newsdata_page_size_invalid", "Set NEWSDATA_PAGE_SIZE to an integer from 1 to 50.", 503) from None
    if not 1 <= size <= MAX_NEWSDATA_PAGE_SIZE:
        raise _fail("newsdata_page_size_invalid", "Set NEWSDATA_PAGE_SIZE to an integer from 1 to 50.", 503)
    return min(size, MAX_NEWSDATA_RESULTS)


def initial_context(now: datetime | None = None) -> dict[str, object]:
    """Freeze latest-48-hour coverage; task prose cannot change provider filters."""
    moment = now or datetime.now(_UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise _fail("newsdata_context_invalid", "The news request time must include a timezone.", 422)
    moment = moment.astimezone(_UTC)
    return {"cursor": None, "window_start": (moment - timedelta(hours=48)).isoformat(),
            "window_end": moment.isoformat(), "page_size": page_size_setting(), "page_index": 1,
            "parent_report_id": None, "root_report_id": None, "results_consumed": 0}


def _moment(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid_datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("missing_timezone")
    return parsed.astimezone(_UTC)


def _cursor(value: object) -> bool:
    return value is None or (isinstance(value, str) and 0 < len(value) <= MAX_CURSOR_CHARS
                             and not re.search(r"[\x00-\x20\x7f]", value))


def _validated_context(context: dict[str, object] | None) -> dict[str, object]:
    if context is None:
        return initial_context()
    try:
        if not isinstance(context, dict):
            raise ValueError("invalid_context")
        start, end = _moment(context.get("window_start")), _moment(context.get("window_end"))
        size, index = context.get("page_size"), context.get("page_index")
        if (end - start != timedelta(hours=48) or type(size) is not int
                or not 1 <= size <= MAX_NEWSDATA_PAGE_SIZE or type(index) is not int
                or not 1 <= index <= 1000000 or not _cursor(context.get("cursor"))):
            raise ValueError("invalid_context")
        consumed = context.get("results_consumed", min(MAX_NEWSDATA_RESULTS, (index - 1) * size))
        if type(consumed) is not int or not 0 <= consumed <= MAX_NEWSDATA_RESULTS:
            raise ValueError("invalid_result_count")
        if index == 1:
            if consumed != 0:
                raise ValueError("invalid_initial_result_count")
            size = min(size, MAX_NEWSDATA_RESULTS)
        for field in ("parent_report_id", "root_report_id"):
            identifier = context.get(field)
            if identifier is not None and (not isinstance(identifier, str)
                    or not re.fullmatch(r"rpt_[A-Za-z0-9_-]{1,96}", identifier)):
                raise ValueError("invalid_report_id")
        return {"cursor": context.get("cursor"), "window_start": start.isoformat(),
                "window_end": end.isoformat(), "page_size": size, "page_index": index,
                "parent_report_id": context.get("parent_report_id"),
                "root_report_id": context.get("root_report_id"), "results_consumed": consumed}
    except (ValueError, TypeError, OverflowError):
        raise _fail("newsdata_context_invalid", "The saved news page context is invalid.", 422) from None


def estimate_reservation(*, language: str = "ar",
                         newsdata_context: dict[str, object] | None = None) -> dict[str, object]:
    if language not in {"ar", "en"}:
        raise _fail("invalid_language", "Use ar or en.", 422)
    if not is_ready():
        raise _fail("newsdata_not_configured", "Configure NEWSDATA_API_KEY to retrieve Iran-war news.", 503)
    context = _validated_context(newsdata_context)
    remaining = MAX_NEWSDATA_RESULTS - int(context["results_consumed"])
    if remaining <= 0:
        raise _fail("newsdata_result_limit", "This report has already consumed its first 20 provider results.", 409)
    translation = translation_plan(min(int(context["page_size"]), remaining))
    count = int(translation["max_model_calls"])
    return {"provider": "newsdata", "model": translation["model"], "language": language,
            "estimated_cost_usd": translation["estimated_cost_usd"], "cost_is_estimate": True,
            "max_input_tokens": count * int(translation["input_tokens_per_batch_bound"]),
            "max_output_tokens": count * int(translation["output_tokens_per_batch_bound"]),
            "max_model_calls": count, "max_web_search_calls": 0,
            "max_newsdata_calls": 1, "max_newsdata_credits": 1,
            "newsdata_credit_basis": "one_latest_api_page_credit_separate_from_usd",
            "newsdata_context": context, "translation": translation,
            "pricing_model": translation["model"],
            "pricing_basis": "optional_openai_translation_only_newsdata_subscription_not_converted_to_usd"}


def _provider_error(status: int, data: object) -> HTTPException:
    code = None
    if isinstance(data, dict):
        details = data.get("results")
        if isinstance(details, dict) and isinstance(details.get("code"), str):
            code = details["code"].strip()
    if status == 401 or code == "Unauthorized":
        error = _fail("newsdata_auth_failed", "NewsData rejected its API credential.", 503)
    elif status == 403 or code == "AccessDenied":
        error = _fail("newsdata_access_denied", "The NewsData plan does not permit this request.", 503)
    elif status == 429 or code in {"RateLimitExceeded", "ApiLimitExceeded"}:
        error = _fail("newsdata_rate_limited", "The NewsData rate or credit allowance was reached.", 429)
    elif code == "MaxPageLimitExceeded":
        error = _fail("newsdata_page_unavailable", "NewsData cannot continue this saved result page.", 409)
    elif status in {500, 503} or code == "ServiceUnavailable":
        error = _fail("newsdata_unavailable", "NewsData is temporarily unavailable; no retry was made.", 503)
    else:
        error = _fail("newsdata_request_failed", "NewsData rejected the request; no retry was made.")
    if 400 <= status <= 599:
        error.detail["provider_status"] = status
    return error


def _remaining(headers: httpx.Headers, name: str) -> int | None:
    value = headers.get(name, "")
    return int(value) if re.fullmatch(r"\d{1,12}", value) else None


def _fetch_page(context: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    key = get_setting("NEWSDATA_API_KEY", "") or ""
    if not key:
        raise _fail("newsdata_not_configured", "Configure NEWSDATA_API_KEY to retrieve Iran-war news.", 503)
    params: dict[str, object] = {"q": NEWSDATA_QUERY, "language": "en",
        "size": context["page_size"], "removeduplicate": 1, "timezone": "UTC", "sort": "pubdatedesc"}
    if context["cursor"] is not None:
        params["page"] = context["cursor"]
    try:
        # Credentials are headers, never URL query parameters. The standard
        # transport has no retries and redirects are not followed.
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False) as client:
            with client.stream("GET", NEWSDATA_LATEST_URL, headers={"X-ACCESS-KEY": key}, params=params) as response:
                chunks: list[bytes] = []
                length = 0
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if length > MAX_NEWSDATA_RESPONSE_BYTES:
                        raise _fail("newsdata_response_too_large", "NewsData exceeded the bounded response size.")
                    chunks.append(chunk)
                try:
                    data = json.loads(b"".join(chunks))
                except (ValueError, TypeError, UnicodeError):
                    if response.is_error:
                        raise _provider_error(response.status_code, None) from None
                    raise _fail("newsdata_invalid_response", "NewsData returned invalid JSON.") from None
                if response.is_error or not isinstance(data, dict) or data.get("status") != "success":
                    raise _provider_error(response.status_code, data)
                if (not isinstance(data.get("results"), list)
                        or len(data["results"]) > int(context["page_size"])
                        or type(data.get("totalResults")) is not int or data["totalResults"] < 0
                        or not _cursor(data.get("nextPage"))):
                    raise _fail("newsdata_invalid_response", "NewsData returned an invalid result page.")
                quota = {"credits_remaining": _remaining(response.headers, "X-API-Limit-Remaining"),
                         "rate_window_remaining": _remaining(response.headers, "X-RateLimit-Remaining")}
                return data, quota
    except httpx.TimeoutException:
        raise _fail("newsdata_timeout", "NewsData timed out; no retry was made.", 504) from None
    except httpx.RequestError:
        raise _fail("newsdata_connection_error", "Could not reach NewsData; no retry was made.") from None


def _text(raw: object, maximum: int) -> str:
    if not isinstance(raw, str):
        return ""
    value = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1\s*>", " ", raw, flags=re.IGNORECASE)
    value = html.unescape(re.sub(r"<[^>]*>", " ", value))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value)
    return re.sub(r"\s+", " ", value).strip()[:maximum]


def _publication(raw: object, zone_value: object) -> tuple[datetime, str] | None:
    if not isinstance(raw, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?", raw):
        return None
    if zone_value is not None and not isinstance(zone_value, str):
        return None
    zone_name = (zone_value or "UTC").strip()
    try:
        if zone_name in {"UTC", "GMT", "Etc/UTC", "Z"}:
            zone = _UTC
        elif re.fullmatch(r"(?:UTC|GMT)?[+-]\d{2}:?\d{2}", zone_name):
            offset = re.sub(r"^(?:UTC|GMT)", "", zone_name).replace(":", "")
            hours, minutes = int(offset[1:3]), int(offset[3:5])
            if hours > 23 or minutes > 59:
                return None
            zone = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if offset[0] == "+" else -1)))
        else:
            zone = ZoneInfo(zone_name)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Ambiguous/nonexistent daylight-saving times cannot be resolved
            # from a provider's timezone name alone.
            first, second = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
            if first.utcoffset() != second.utcoffset():
                return None
            parsed = first
            if parsed.astimezone(_UTC).astimezone(zone).replace(tzinfo=None) != parsed.replace(tzinfo=None):
                return None
        elif zone_value and parsed.utcoffset() != parsed.astimezone(zone).utcoffset():
            return None
        return parsed.astimezone(_UTC), zone_name
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return None


def _items(results: list[object], context: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, int]]:
    from . import runtime

    start, end = _moment(context["window_start"]), _moment(context["window_end"])
    excluded = {"invalid_article": 0, "missing_title": 0, "invalid_link": 0,
                "invalid_publication": 0, "outside_window": 0, "future_publication": 0,
                "duplicate": 0, "wrong_language": 0}
    items: list[dict[str, object]] = []
    ids: set[str] = set()
    urls: set[str] = set()
    titles: set[str] = set()
    for source in results:
        if not isinstance(source, dict):
            excluded["invalid_article"] += 1
            continue
        article_id = source.get("article_id")
        if (not isinstance(article_id, str) or not article_id or len(article_id) > 200
                or re.search(r"[\x00-\x20\x7f]", article_id)):
            excluded["invalid_article"] += 1
            continue
        title = _text(source.get("title"), MAX_HEADLINE_CHARS)
        if not title:
            excluded["missing_title"] += 1
            continue
        url = runtime._safe_source_url(source.get("link"))
        if not url:
            excluded["invalid_link"] += 1
            continue
        publication = _publication(source.get("pubDate"), source.get("pubDateTZ"))
        if publication is None:
            excluded["invalid_publication"] += 1
            continue
        published, original_zone = publication
        if published > end:
            excluded["future_publication"] += 1
            continue
        if published < start:
            excluded["outside_window"] += 1
            continue
        source_language = source.get("language")
        if source_language is not None and source_language not in {"english", "en"}:
            excluded["wrong_language"] += 1
            continue
        canonical, headline_key = runtime._canonical_url(url), runtime._headline_key(title)
        if article_id in ids or canonical in urls or headline_key in titles or source.get("duplicate") is True:
            excluded["duplicate"] += 1
            continue
        description = _text(source.get("description"), MAX_DESCRIPTION_CHARS)
        if _PLAN_NOTICE.match(description):
            description = ""
        ids.add(article_id)
        urls.add(canonical)
        titles.add(headline_key)
        publisher = _text(source.get("source_name"), 200) or (urlsplit(url).hostname or "")
        publisher_id = _text(source.get("source_id"), 200)
        publisher_url = runtime._safe_source_url(source.get("source_url"))
        items.append({"article_id": article_id, "source_id": "newsdata:" + article_id,
            "url": url, "publisher": publisher, "publisher_id": publisher_id,
            "publisher_url": publisher_url, "source_url": publisher_url,
            "headline_en": title, "summary_en": description, "title": title, "description": description,
            "original_language": "en", "summary_available": bool(description),
            "pubDate": source["pubDate"], "pubDateTZ": original_zone,
            "published_date": published.isoformat(),
            "published_day_dubai": published.astimezone(runtime.DUBAI).date().isoformat(),
            "classification": "within_window", "source_kind": runtime._news_source_kind(title, url),
            "event_date": None, "event_date_note": None, "event_timing_relative_to_window": "unknown",
            "evidence_level": "newsdata_structured_metadata", "publisher_text_verified": False,
            "date_basis": "newsdata_pubDate_pubDateTZ", "relevance_basis": "newsdata_iran_and_war_query"})
    return items, excluded


def _freshness(context: dict[str, object]) -> dict[str, object]:
    plan = (get_setting("NEWSDATA_PLAN", "unknown") or "unknown").lower()
    if plan not in {"free", "paid", "unknown"}:
        plan = "unknown"
    return {"mode": "latest_48_hours", "window_start": context["window_start"],
            "window_end": context["window_end"], "timezone": "UTC",
            "plan": plan, "configured_delivery_delay_hours": 12 if plan == "free" else None,
            "free_plan_delay_hours": 12, "coverage_complete": False,
            "note_ar": "تغطي النتائج أحدث 48 ساعة المتاحة لدى NewsData.io. تتأخر الخطة المجانية 12 ساعة؛ لا تمثل الصفحة حصرًا شاملًا للأخبار.",
            "note_en": "Results cover NewsData.io's available latest 48 hours. The free plan has a 12-hour delay; this page is not comprehensive coverage."}


def _render_report(report: dict[str, object], language: str) -> str:
    from . import runtime

    arabic = language == "ar"
    pagination = report["pagination"]
    heading = "أخبار حرب إيران" if arabic else "Iran war news"
    count = len(report["items"])
    lines = ["## " + heading, "",
             (f"الصفحة {pagination['page_index']} — الأخبار المعروضة: {count}." if arabic else
              f"Page {pagination['page_index']} — articles shown: {count}."), "",
             report["freshness"]["note_ar" if arabic else "note_en"], ""]
    for number, item in enumerate(report["items"], start=1):
        headline = item.get("headline_ar") if arabic else item["headline_en"]
        summary = item.get("summary_ar") if arabic else item["summary_en"]
        if not headline:
            headline, summary = item["headline_en"], item["summary_en"]
        lines += [f"{number}. **{runtime._md_safe(str(headline))}**", ""]
        if summary:
            lines += [runtime._md_safe(str(summary)), ""]
        else:
            lines += ["لم يوفّر المزود وصفًا لهذا الخبر." if arabic else "The provider supplied no description for this article.", ""]
        lines += [("الناشر: " if arabic else "Publisher: ") + runtime._md_safe(str(item["publisher"])),
                  ("تاريخ النشر وفق المزود: " if arabic else "Publication reported by the provider: ") + str(item["published_date"]),
                  (f"[فتح المقال الأصلي](<{item['url']}>)." if arabic else f"[Open the original article](<{item['url']}>)."), ""]
        if arabic and item["translation_status"] != "translated":
            lines += ["الترجمة العربية غير متاحة لهذا الخبر؛ يعرض النص الإنجليزي الأصلي.", ""]
    if not count:
        lines += ["لم تتضمن هذه الصفحة أخبارًا اجتازت التحقق من الحقول والوقت. لا يعني ذلك عدم وجود أخبار أخرى." if arabic else
                  "This page contained no articles passing the field and time checks. Other news may still be available.", ""]
    if pagination["has_more"]:
        lines += ["توجد صفحة تالية؛ جلبها طلب مستقل إلى مزود الأخبار." if arabic else
                  "Another page is available; fetching it makes a separate provider request.", ""]
    if pagination.get("limit_reached"):
        lines += ["وصل التقرير إلى حد أول 20 نتيجة من المزود؛ قد تقل الأخبار المعروضة بسبب التحقق من الحقول والوقت." if arabic else
                  "The report reached its first-20-provider-results limit; validation may leave fewer displayed articles.", ""]
    lines += ["العناوين والأوصاف والروابط والتواريخ من بيانات NewsData.io؛ الترجمة العربية آلية. لم يجلب الخادم نصوص الناشرين الكاملة للتحقق المستقل." if arabic else
              "Titles, descriptions, links and dates come from NewsData.io metadata; Arabic text is machine translated. This server has not independently fetched the publishers' complete articles."]
    return "\n".join(lines).strip()


def run_report(*, language: str = "ar",
               newsdata_context: dict[str, object] | None = None) -> dict[str, object]:
    """Run one pre-reserved page and eager translations, without paid retries."""
    estimate = estimate_reservation(language=language, newsdata_context=newsdata_context)
    context = estimate["newsdata_context"]
    data, quota = _fetch_page(context)
    before = int(context["results_consumed"])
    # Cursor requests retain their original size. Only the remaining prefix is
    # eligible for parsing or translation, including positions later excluded.
    prefix = data["results"][:MAX_NEWSDATA_RESULTS - before]
    consumed = before + len(prefix)
    limit_reached = consumed >= MAX_NEWSDATA_RESULTS
    items, excluded = _items(prefix, context)
    translated = translate_items(items, estimate["translation"])
    for item in items:
        available_arabic = language == "ar" and item["translation_status"] == "translated"
        item["headline"] = item["headline_ar"] if available_arabic else item["headline_en"]
        item["summary"] = item["summary_ar"] if available_arabic else item["summary_en"]
        item["display_language"] = "ar" if available_arabic else "en"
    next_cursor = data.get("nextPage")
    repeated_cursor = next_cursor is not None and next_cursor == context["cursor"]
    if repeated_cursor or limit_reached:
        next_cursor = None
    pagination = {"has_more": next_cursor is not None, "page_index": context["page_index"],
                  "page_size": context["page_size"], "parent_report_id": context["parent_report_id"],
                  "root_report_id": context["root_report_id"], "total_results": data["totalResults"],
                  "repeated_cursor_stopped": repeated_cursor,
                  "result_limit": MAX_NEWSDATA_RESULTS, "results_before_page": before,
                  "results_consumed": consumed, "limit_reached": limit_reached}
    result = {"agent_id": "iran-news", "report_kind": "news", "provider": "newsdata",
        "model": estimate["model"], "language": language, "generated_at": datetime.now(_UTC).isoformat(),
        "items": items, "background_items": [], "item_count": len(items),
        "target_count": None, "shortfall": None, "bounded_item_limit": MAX_NEWSDATA_RESULTS, "count_mode": "provider_page",
        "status": "complete" if items else "no_evidence", "fresh_event_count": 0,
        "discarded_item_count": sum(excluded.values()), "discarded_item_reasons": excluded,
        "pagination": pagination, "freshness": _freshness(context),
        "newsdata": {"query": NEWSDATA_QUERY, "query_semantics": "iran AND war",
                     "language": "en", "endpoint": "latest", "request_count": 1,
                     "returned_item_count": len(data["results"]), "consumed_item_count": len(prefix),
                     "provider_total_results": data["totalResults"],
                     "credits_used": 1, "credit_basis": "one_latest_api_page", **quota},
        "newsdata_context": {**context, "next_cursor": next_cursor},
        "newsdata_credits_used": 1, "credit_tracking_complete": True,
        "source_publishers": list(dict.fromkeys(str(item["publisher"]) for item in items)),
        "publisher_text_verified": False, "evidence_level": "newsdata_structured_metadata",
        "coverage_window": {"start": context["window_start"], "end": context["window_end"], "timezone": "UTC"},
        **translated}
    result["translation_status"] = result["translation"]["status"]
    result["usage"]["newsdata_credits"] = 1
    result["response_ar"] = _render_report(result, "ar")
    result["response_en"] = _render_report(result, "en")
    result["response"] = result["response_ar"] if language == "ar" else result["response_en"]
    return result
