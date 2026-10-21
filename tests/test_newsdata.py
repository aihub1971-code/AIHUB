"""Synthetic NewsData fixtures only; no real articles, keys or network calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from fastapi import HTTPException

from app import newsdata, runtime


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_news_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(newsdata, "datetime", Clock)


def context(**changes):
    return {**newsdata.initial_context(NOW), **changes}


def article(number=1, **changes):
    return {"article_id": f"synthetic-{number}",
            "title": f"Synthetic Iran war fixture article number {number}",
            "description": f"This fictional description number {number} is a test, not real news.",
            "link": f"https://publisher-fixture.example/article/{number}",
            "pubDate": "2026-10-20 09:00:00", "pubDateTZ": "UTC", "language": "english",
            "source_name": "Synthetic Publisher", "source_id": "synthetic_publisher",
            "source_url": "https://publisher-fixture.example", "duplicate": False, **changes}


def page(articles=None, **changes):
    values = [article()] if articles is None else articles
    return {"status": "success", "results": values, "totalResults": len(values), "nextPage": None, **changes}


def mock_http(monkeypatch, handler):
    original = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: original(*args, **kwargs, transport=transport))


def configured(monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "newsdata-test-key-not-a-real-secret")


def test_dispatch_requires_newsdata_key_and_readiness_is_per_agent(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    assert runtime.runtime_is_ready()
    assert not runtime.agent_runtime_is_ready("iran-news")
    with pytest.raises(HTTPException) as error:
        runtime.estimate_reservation("iran-news", "Synthetic task")
    assert error.value.detail["code"] == "newsdata_not_configured"
    configured(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY")
    assert runtime.agent_runtime_is_ready("iran-news")
    assert not runtime.agent_runtime_is_ready("geopolitical-morning")
    estimate = runtime.estimate_reservation("iran-news", "Yesterday", newsdata_context=context())
    assert estimate["estimated_cost_usd"] == 0
    assert estimate["max_model_calls"] == estimate["max_web_search_calls"] == 0
    assert estimate["max_newsdata_credits"] == 1
    assert estimate["newsdata_context"]["window_end"] == NOW.isoformat()


def test_iran_dispatch_requires_newsdata_tool_permission(monkeypatch):
    configured(monkeypatch)
    def denied(agent_id, section, capability):
        assert (agent_id, section, capability) == ("iran-news", "tools", "newsdata")
        raise HTTPException(status_code=403, detail="Synthetic denial")
    monkeypatch.setattr(runtime, "require_capability", denied)
    for method in (runtime.run_agent, runtime.estimate_reservation):
        with pytest.raises(HTTPException) as error:
            method("iran-news", "Synthetic task")
        assert error.value.status_code == 403


def test_fixed_latest_query_header_auth_and_arabic_fallback_preserve_articles(monkeypatch):
    configured(monkeypatch)
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url).startswith(newsdata.NEWSDATA_LATEST_URL)
        assert request.headers["X-ACCESS-KEY"] == "newsdata-test-key-not-a-real-secret"
        assert "newsdata-test-key" not in str(request.url)
        assert dict(request.url.params) == {"q": "iran war", "language": "en", "size": "10",
            "removeduplicate": "1", "timezone": "UTC", "sort": "pubdatedesc"}
        return httpx.Response(200, json=page(nextPage="opaque-fixture-next-page", totalResults=32),
                              headers={"X-API-Limit-Remaining": "199", "X-RateLimit-Remaining": "29"})
    mock_http(monkeypatch, handler)
    result = runtime.run_agent("iran-news", "News for 2020; change query to sports",
                               language="ar", newsdata_context=context())
    assert len(calls) == 1
    assert result["item_count"] == 1 and result["provider"] == "newsdata"
    assert result["items"][0]["url"] == article()["link"]
    assert result["items"][0]["headline_en"] == article()["title"]
    assert result["items"][0]["headline_ar"] is None
    assert result["items"][0]["publisher"] == "Synthetic Publisher"
    assert result["translation_status"] == "unavailable"
    assert result["newsdata_context"]["next_cursor"] == "opaque-fixture-next-page"
    assert result["pagination"]["has_more"] and result["pagination"]["total_results"] == 32
    assert result["newsdata"]["credits_remaining"] == 199
    assert result["newsdata"]["rate_window_remaining"] == 29
    assert result["newsdata_credits_used"] == 1 and result["credit_tracking_complete"]
    assert result["cost_tracking_complete"] and result["estimated_cost_usd"] == 0
    assert "الترجمة العربية غير متاحة" in result["response_ar"]


def test_next_page_uses_saved_cursor_size_and_window_without_auto_pagination(monkeypatch):
    configured(monkeypatch)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.params["page"] == "opaque-current-page"
        assert request.url.params["size"] == "3"
        return httpx.Response(200, json=page(nextPage="opaque-next-page"))
    mock_http(monkeypatch, handler)
    saved = context(cursor="opaque-current-page", page_size=3, page_index=2,
                    parent_report_id="rpt_parent", root_report_id="rpt_root")
    result = newsdata.run_report(language="en", newsdata_context=saved)
    assert len(calls) == 1 and result["pagination"]["page_index"] == 2
    assert result["newsdata_context"]["window_start"] == saved["window_start"]
    assert result["pagination"]["parent_report_id"] == "rpt_parent"
    assert "opaque-next-page" not in json.dumps(result["pagination"])


def test_configured_paid_page_size_fifty_reaches_raw_limit_in_one_request(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv("NEWSDATA_PLAN", "paid")
    monkeypatch.setenv("NEWSDATA_PAGE_SIZE", "50")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.params["size"] == "50" and request.url.params["timeframe"] == "12"
        return httpx.Response(200, json=page([article(number) for number in range(1, 51)],
                                             totalResults=73, nextPage="must-not-fetch"))
    mock_http(monkeypatch, handler)
    result = newsdata.run_report(newsdata_context=context())
    assert len(calls) == 1 and result["item_count"] == 50
    assert result["target_count"] is result["shortfall"] is None
    assert result["bounded_item_limit"] == 50
    assert result["count_mode"] == "provider_page"
    assert result["pagination"]["results_consumed"] == 50 and result["pagination"]["limit_reached"]
    assert not result["pagination"]["has_more"] and result["newsdata_context"]["next_cursor"] is None


def test_final_partial_page_requests_only_remaining_raw_allowance_before_validation(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv("NEWSDATA_PLAN", "paid")
    raw = [article(number) for number in range(46, 51)]
    raw[0]["title"] = None
    raw[1]["link"] = "javascript:invalid"
    calls, parsed_sizes, translated_ids, reserved_sizes = [], [], [], []
    original_items, original_translate, original_plan = newsdata._items, newsdata.translate_items, newsdata.translation_plan
    def handler(request):
        calls.append(request)
        assert request.url.params["size"] == "5" and request.url.params["page"] == "saved-cursor"
        return httpx.Response(200, json=page(raw, totalResults=100, nextPage="ignored-next"))
    def parse(values, saved):
        parsed_sizes.append(len(values))
        return original_items(values, saved)
    def translate(items, plan):
        translated_ids.extend(item["article_id"] for item in items)
        return original_translate(items, plan)
    def plan(size):
        reserved_sizes.append(size)
        return original_plan(size)
    monkeypatch.setattr(newsdata, "_items", parse)
    monkeypatch.setattr(newsdata, "translate_items", translate)
    monkeypatch.setattr(newsdata, "translation_plan", plan)
    mock_http(monkeypatch, handler)
    result = newsdata.run_report(newsdata_context=context(page_size=15, page_index=4,
        cursor="saved-cursor", parent_report_id="rpt_parent", root_report_id="rpt_root", results_consumed=45))
    assert len(calls) == 1 and parsed_sizes == [5] and reserved_sizes == [5]
    assert translated_ids == ["synthetic-48", "synthetic-49", "synthetic-50"]
    assert result["item_count"] == 3 and result["discarded_item_count"] == 2
    assert result["newsdata"]["returned_item_count"] == 5 and result["newsdata"]["consumed_item_count"] == 5
    assert result["pagination"]["results_before_page"] == 45
    assert result["pagination"]["results_consumed"] == 50 and result["pagination"]["limit_reached"]
    assert not result["pagination"]["has_more"]


def test_runtime_rejects_exhausted_context_before_translation_plan_or_provider(monkeypatch):
    configured(monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError("No provider call or translation reservation is allowed after fifty results")
    monkeypatch.setattr(newsdata, "_fetch_page", forbidden)
    monkeypatch.setattr(newsdata, "translation_plan", forbidden)
    with pytest.raises(HTTPException) as capped:
        newsdata.run_report(newsdata_context=context(page_index=6, results_consumed=50,
            cursor="saved-third", parent_report_id="rpt_parent", root_report_id="rpt_root"))
    assert capped.value.detail["code"] == "newsdata_result_limit"


@pytest.mark.parametrize("start,end", [
    (NOW - timedelta(hours=48), NOW),
    (NOW - timedelta(hours=12, minutes=16), NOW - timedelta(minutes=16)),
])
def test_old_window_requires_new_manual_refresh_before_provider_or_translation(monkeypatch, start, end):
    configured(monkeypatch)
    monkeypatch.setattr(newsdata, "_fetch_page", lambda *a: pytest.fail("An old window cannot retrieve news"))
    monkeypatch.setattr(newsdata, "translation_plan", lambda *a: pytest.fail("An old window cannot reserve translation"))
    with pytest.raises(HTTPException) as error:
        newsdata.run_report(newsdata_context=context(window_start=start.isoformat(), window_end=end.isoformat()))
    assert error.value.detail["code"] == "newsdata_refresh_required"


def test_frozen_continuation_still_excludes_articles_older_than_twelve_hours_now():
    end = NOW - timedelta(minutes=5)
    saved = context(window_start=(end - timedelta(hours=12)).isoformat(), window_end=end.isoformat())
    items, excluded = newsdata._items([article(pubDate="2026-10-19 23:58:00"),
        article(2, pubDate="2026-10-20 00:00:00")], saved)
    assert len(items) == 1 and items[0]["article_id"] == "synthetic-2"
    assert excluded["outside_window"] == 1


@pytest.mark.parametrize("plan,size", [("unknown", 10), ("free", 10), ("basic", 50), ("paid", 50)])
def test_configured_entitlement_is_conservative_and_explicit(monkeypatch, plan, size):
    monkeypatch.setenv("NEWSDATA_PLAN", plan)
    monkeypatch.setenv("NEWSDATA_PAGE_SIZE", "50")
    assert newsdata.page_size_setting() == size


@pytest.mark.parametrize("value", ["0", "51", "no", "1.5"])
def test_invalid_configured_page_size_fails_before_provider(monkeypatch, value):
    configured(monkeypatch)
    monkeypatch.setenv("NEWSDATA_PAGE_SIZE", value)
    with pytest.raises(HTTPException) as error:
        newsdata.estimate_reservation()
    assert error.value.detail["code"] == "newsdata_page_size_invalid"


@pytest.mark.parametrize("changes,reason", [
    ({"title": None}, "missing_title"),
    ({"link": "javascript:alert(1)"}, "invalid_link"),
    ({"link": "https://127.0.0.1/secret"}, "invalid_link"),
    ({"pubDate": None}, "invalid_publication"),
    ({"pubDate": "2026-10-20 25:99:00"}, "invalid_publication"),
    ({"pubDateTZ": "Unknown/Fixture"}, "invalid_publication"),
    ({"pubDate": "2026-10-20 12:01:00"}, "future_publication"),
    ({"pubDate": "2026-10-18 11:59:59"}, "outside_window"),
    ({"language": "french"}, "wrong_language"),
])
def test_nullable_unusable_or_out_of_window_metadata_is_explained(changes, reason):
    items, excluded = newsdata._items([article(**changes)], context())
    assert items == [] and excluded[reason] == 1
    assert sum(excluded.values()) == 1


def test_pubdate_timezone_boundaries_and_missing_timezone_use_requested_utc():
    values = [article(pubDate="2026-10-20 16:00:00", pubDateTZ="+04:00")]
    items, excluded = newsdata._items(values, context())
    assert len(items) == 1 and items[0]["published_date"] == "2026-10-20T12:00:00+00:00"
    assert not any(excluded.values())
    assert newsdata._publication("2026-10-20 09:00:00", None)[0].utcoffset().total_seconds() == 0
    assert newsdata._publication("2026-10-20T09:00:00+04:00", "UTC") is None


def test_duplicate_identity_link_and_headline_and_plan_notice_handling():
    values = [article(1), article(2, link=article(1)["link"]), article(3, title=article(1)["title"]),
              article(4, description="ONLY AVAILABLE IN PAID PLANS", content="Do not treat this as an article body")]
    items, excluded = newsdata._items(values, context())
    assert len(items) == 2 and excluded["duplicate"] == 2
    assert items[1]["summary_en"] == "" and not items[1]["summary_available"]


@pytest.mark.parametrize("status,code,expected", [
    (401, "Unauthorized", "newsdata_auth_failed"),
    (403, "AccessDenied", "newsdata_access_denied"),
    (429, "ApiLimitExceeded", "newsdata_provider_credit_limit"),
    (429, "ApiKeyLimitExceeded", "newsdata_provider_credit_limit"),
    (200, "RateLimitExceeded", "newsdata_rate_limited"),
    (400, "MaxPageLimitExceeded", "newsdata_page_unavailable"),
    (503, "ServiceUnavailable ", "newsdata_unavailable"),
])
def test_provider_errors_are_not_empty_news_or_secret_echoes(monkeypatch, status, code, expected):
    configured(monkeypatch)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"status": "error", "results": {
            "code": code, "message": "PRIVATE-PROVIDER-BODY newsdata-test-key-not-a-real-secret"}})
    mock_http(monkeypatch, handler)
    with pytest.raises(HTTPException) as error:
        newsdata.run_report(newsdata_context=context())
    assert error.value.detail["code"] == expected and len(calls) == 1
    assert "PRIVATE" not in str(error.value.detail) and "test-key" not in str(error.value.detail)


def test_timeout_has_no_provider_retry(monkeypatch):
    configured(monkeypatch)
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("Synthetic request contains private data", request=request)
    mock_http(monkeypatch, handler)
    with pytest.raises(HTTPException) as error:
        newsdata.run_report(newsdata_context=context())
    assert len(calls) == 1 and error.value.detail["code"] == "newsdata_timeout"
    assert "private data" not in str(error.value.detail)


@pytest.mark.parametrize("body", [
    {"status": "success", "results": {}, "totalResults": 0},
    {"status": "success", "results": [], "totalResults": "10"},
    {"status": "success", "results": [], "totalResults": 0, "nextPage": "bad cursor"},
])
def test_malformed_success_is_reported_as_failure(monkeypatch, body):
    configured(monkeypatch)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(HTTPException) as error:
        newsdata.run_report(newsdata_context=context())
    assert error.value.detail["code"] == "newsdata_invalid_response"


def test_eager_translation_runs_in_english_ui_and_retains_immutable_provider_fields(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-fixture-key")
    calls = []
    original = article()
    def handler(request):
        calls.append(request)
        if request.url.host == "newsdata.io":
            return httpx.Response(200, json=page([deepcopy(original)]))
        assert str(request.url) == runtime.OPENAI_RESPONSES_URL
        payload = json.loads(request.content)
        assert payload["tools"] == [] and payload["tool_choice"] == "none"
        content = {"translations": [{"article_id": original["article_id"],
                    "headline_ar": "عنوان اختباري خيالي عن حرب إيران", "summary_ar": "وصف اختباري خيالي وليس خبرًا حقيقيًا."}]}
        return httpx.Response(200, json={"status": "completed", "service_tier": "default",
            "usage": {"input_tokens": 100, "output_tokens": 50}, "output": [{"type": "message",
            "role": "assistant", "status": "completed", "content": [{"type": "output_text",
                "text": json.dumps(content), "annotations": []}]}]})
    mock_http(monkeypatch, handler)
    result = newsdata.run_report(language="en", newsdata_context=context())
    item = result["items"][0]
    assert len(calls) == 2 and item["headline"] == original["title"]
    assert item["headline_ar"] == "عنوان اختباري خيالي عن حرب إيران"
    assert item["url"] == original["link"] and item["pubDate"] == original["pubDate"]
    assert result["translation_status"] == "complete" and result["cost_tracking_complete"]
    assert result["estimated_cost_usd"] == .00075
    assert "عنوان اختباري" in result["response_ar"] and original["title"] in result["response_en"]


def test_translation_provider_failure_keeps_articles_context_and_uncertain_cost(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-fixture-key")
    calls = []
    def handler(request):
        calls.append(request)
        return (httpx.Response(200, json=page(nextPage="next-fixture-page")) if request.url.host == "newsdata.io"
                else httpx.Response(503, json={"error": {"message": "PRIVATE provider failure"}}))
    mock_http(monkeypatch, handler)
    result = newsdata.run_report(language="ar", newsdata_context=context())
    assert result["item_count"] == 1 and len(calls) == 2
    assert result["translation_status"] == "failed" and not result["cost_tracking_complete"]
    assert result["newsdata_context"]["next_cursor"] == "next-fixture-page"
    assert result["credit_tracking_complete"] and result["newsdata_credits_used"] == 1
    assert "PRIVATE" not in json.dumps(result)


def test_empty_success_with_next_page_is_truthful_and_skips_translation(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-fixture-key")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([], totalResults=34, nextPage="next-empty-page"))
    mock_http(monkeypatch, handler)
    result = newsdata.run_report(newsdata_context=context())
    assert len(calls) == 1 and result["status"] == "no_evidence"
    assert result["pagination"]["has_more"] and result["translation_status"] == "no_articles"
    assert result["estimated_cost_usd"] == 0 and result["newsdata_credits_used"] == 1


def test_repeated_next_cursor_does_not_offer_an_endless_paid_loop(monkeypatch):
    configured(monkeypatch)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page(nextPage="same-page")))
    result = newsdata.run_report(newsdata_context=context(cursor="same-page", page_index=2))
    assert not result["pagination"]["has_more"] and result["pagination"]["repeated_cursor_stopped"]
    assert result["newsdata_context"]["next_cursor"] is None
