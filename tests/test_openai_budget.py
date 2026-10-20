"""Native OpenAI news discovery, all-stage usage and the spending gate.

All article text and HTTP replies below are synthetic fixtures. No network or
paid request is permitted by the test suite.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json

from fastapi import HTTPException
import httpx
import pytest

from app import runtime, service
from app.storage import QuotaLimits, Store


TASK = "أهم أخبار إيران في 2026-10-19"
# Synthetic path on a permitted publisher host; tests never fetch the article.
ARTICLE_URL = "https://www.ft.com/content/ai-hub-synthetic-test"
ADDITIONAL_URL = "https://www.aljazeera.com/news/2026/10/19/iran-synthetic-test"
ARTICLE_TITLE = "Iran fictional test article"
EVENT_NOTE = "A fictional project was announced on 2026-10-19."
PUBLICATION_NOTE = "Published: 2026-10-19T07:00:00Z."
RESEARCH_TEXT = f"{ARTICLE_TITLE}. {PUBLICATION_NOTE} {EVENT_NOTE} [1]"


def research_reply(url=ARTICLE_URL):
    return {
        "status": "completed", "service_tier": "default",
        "output": [
            {"type": "web_search_call", "id": "fixture_search", "status": "completed",
             "action": {"type": "search", "sources": [{"type": "url", "url": url}]}},
            {"type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": RESEARCH_TEXT,
                          "annotations": [{"type": "url_citation", "url": url,
                                           "title": ARTICLE_TITLE,
                                           "start_index": RESEARCH_TEXT.index("[1]"),
                                           "end_index": len(RESEARCH_TEXT)}]}]},
        ],
        "usage": {"input_tokens": 300, "output_tokens": 100, "total_tokens": 400},
    }


def report_reply():
    report = {
        "items": [{
            "headline_ar": "إيران تعرض مشروعًا افتراضيًا في بيانات الاختبار",
            "summary_ar": "هذه مادة اختبار اصطناعية منسوبة إلى مصدرها ولا تمثل خبرًا حقيقيًا.",
            "source_id": "s1", "research_note_quote": EVENT_NOTE,
            "published_date": "2026-10-19T07:00:00Z", "published_date_note": PUBLICATION_NOTE,
            "event_date": "2026-10-19", "event_date_note": EVENT_NOTE,
        }],
        "background_items": [],
    }
    return {
        "status": "completed", "service_tier": "default",
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(report, ensure_ascii=False)}]}],
        "usage": {"input_tokens": 1200, "output_tokens": 900, "total_tokens": 2100},
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai-key-not-a-credential")
    monkeypatch.setattr(runtime, "_now_dubai", lambda: datetime(2026, 10, 19, 15, 30, tzinfo=runtime.DUBAI))
    database = Store(tmp_path / "native_search_budget.sqlite3", QuotaLimits(50, "100", "1000"),
                     clock=lambda: datetime(2026, 10, 19, 11, 30, tzinfo=timezone.utc))
    database.initialize()
    return database


@pytest.fixture
def mock_openai(monkeypatch):
    original_client = httpx.Client

    def install(replies, before_request=None):
        requests = []

        def dispatch(request):
            assert request.url == "https://api.openai.com/v1/responses"
            if before_request:
                before_request()
            index = len(requests)
            requests.append(request)
            assert index < len(replies), "The runtime made an unreserved extra request."
            reply = replies[index]
            if reply == "timeout":
                raise httpx.ReadTimeout("Synthetic timeout", request=request)
            return httpx.Response(200, json=reply)

        monkeypatch.setattr(runtime.httpx, "Client", lambda *args, **kwargs:
                            original_client(transport=httpx.MockTransport(dispatch), **kwargs))
        return requests

    return install


def test_news_success_reconciles_all_usage_records_and_both_search_fees(store, mock_openai):
    requests = mock_openai([research_reply(), research_reply(ADDITIONAL_URL), report_reply()])
    result = service.execute_report("iran-news", TASK, idempotency_key="three-stage", store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 3
    assert result["item_count"] == 1
    assert result["usage"]["input_tokens"] == 1800
    assert result["usage"]["output_tokens"] == 1100
    assert result["usage"]["web_search_calls"] == 2
    assert [stage["stage"] for stage in result["usage_stages"]] == ["research", "research_additional", "report"]
    # 1,800 input at conservative $2.50/M + 1,100 output at $10/M + two $0.01 searches.
    assert result["accounting"]["estimated_cost_usd"] == .0355
    assert record["cost_basis"] == "usage_estimate"
    assert record["reserved_cost_usd"] > record["estimated_cost_usd"]
    assert record["usage"] == result["usage"]
    assert record["pricing_snapshot"]["max_model_calls"] == 3
    assert store.usage_summary()["daily"]["attempts"] == 1
    replay = service.execute_report("iran-news", TASK, idempotency_key="three-stage", store=store)
    assert replay["report_id"] == result["report_id"] and replay["idempotency_replayed"]
    assert len(requests) == 3


def test_rejected_publishers_still_account_for_both_paid_discovery_requests(store, mock_openai):
    research = research_reply()
    rejected_url = "https://unapproved.example.test/article/synthetic-test"
    research["output"][0]["action"]["sources"][0]["url"] = rejected_url
    research["output"][1]["content"][0]["annotations"][0]["url"] = rejected_url
    requests = mock_openai([research, research])
    result = service.execute_report("iran-news", TASK, store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 2
    assert result["status"] == "no_evidence" and result["item_count"] == 0
    assert result["report_model_called"] is False
    assert result["usage"]["input_tokens"] == 600
    assert result["usage"]["output_tokens"] == 200
    assert result["usage"]["web_search_calls"] == 2
    assert result["accounting"]["estimated_cost_usd"] == .0235
    assert record["cost_basis"] == "usage_estimate"
    assert record["reserved_cost_usd"] > record["estimated_cost_usd"] > 0
    assert store.usage_summary()["daily"]["attempts"] == 1


def test_bound_covers_each_search_context_and_enforces_three_stateless_requests(store, mock_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_RESEARCH_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "8192")
    estimate = runtime.estimate_reservation("iran-news", TASK)
    requests = mock_openai([research_reply(), research_reply(ADDITIONAL_URL), report_reply()])
    service.execute_report("iran-news", TASK, store=store)
    first, second, report = [json.loads(request.content) for request in requests]
    for research in (first, second):
        assert research["max_tool_calls"] == 1
        assert research["parallel_tool_calls"] is False
        assert len(research["tools"]) == 1
        assert research["tools"][0]["type"] == "web_search"
        assert research["tools"][0]["return_token_budget"] == "default"
        assert research["tools"][0]["search_context_size"] == "low"
    first_domains = set(first["tools"][0]["filters"]["allowed_domains"])
    second_domains = set(second["tools"][0]["filters"]["allowed_domains"])
    assert second_domains == {"aljazeera.com", "aljazeera.net"}
    assert first_domains.isdisjoint(second_domains)
    assert first_domains | second_domains == set(runtime.ALLOWED_SOURCE_DOMAINS)
    assert report["tools"] == [] and report["tool_choice"] == "none"
    for payload in (first, second, report):
        assert "previous_response_id" not in payload and "conversation" not in payload
        assert payload["service_tier"] == "default"
        assert payload["max_output_tokens"] == 8192
    assert estimate["research_request_count"] == 2
    bounds = estimate["research_request_input_tokens_bounds"]
    assert len(bounds) == 2
    for request, bound in zip(requests[:2], bounds):
        assert len(request.content) <= 50000
        assert bound >= 2 * (len(request.content) + 4096) + 131072 + 8192
        assert bound <= 247456 < 272000
    assert len(requests[2].content) <= 200000
    assert estimate["research_input_tokens_bound"] == sum(bounds)
    assert estimate["report_input_tokens_bound"] >= len(requests[2].content) + 4096
    assert estimate["report_input_tokens_bound"] <= 204096 < 272000
    assert estimate["research_request_output_tokens_bounds"] == [8192, 8192]
    assert estimate["research_output_tokens_bound"] == 16384
    assert estimate["report_output_tokens_bound"] == 8192
    assert estimate["max_web_search_calls"] == 2 and estimate["max_model_calls"] == 3
    assert estimate["estimated_cost_usd"] <= 2.01328


def test_configured_native_search_fee_changes_both_reservation_and_saved_estimate(store, mock_openai, monkeypatch):
    baseline = runtime.estimate_reservation("iran-news", TASK)
    monkeypatch.setenv("OPENAI_WEB_SEARCH_PRICE_USD_PER_CALL", ".037")
    configured = runtime.estimate_reservation("iran-news", TASK)
    assert Decimal(str(configured["estimated_cost_usd"])) - Decimal(str(baseline["estimated_cost_usd"])) == Decimal(".054")
    requests = mock_openai([research_reply(), research_reply(ADDITIONAL_URL), report_reply()])
    result = service.execute_report("iran-news", TASK, store=store)
    assert len(requests) == 3
    assert result["accounting"]["estimated_cost_usd"] == .0895
    assert store.get_report(result["report_id"])["reserved_cost_usd"] == configured["estimated_cost_usd"]


@pytest.mark.parametrize("timeout_stage", ["research_additional", "report"])
def test_later_timeout_retains_whole_reservation_and_never_repeats_research(store, mock_openai, timeout_stage):
    estimate = runtime.estimate_reservation("iran-news", TASK)
    store._limits = QuotaLimits(50, str(estimate["estimated_cost_usd"]), "1000")

    def reservation_precedes_every_paid_request():
        summary = store.usage_summary()
        assert summary["daily"]["attempts"] == summary["daily"]["pending"] == 1
        assert summary["daily"]["estimated_cost_usd"] == estimate["estimated_cost_usd"]

    replies = [research_reply(), "timeout"] if timeout_stage == "research_additional" else [
        research_reply(), research_reply(ADDITIONAL_URL), "timeout"]
    requests = mock_openai(replies, reservation_precedes_every_paid_request)
    with pytest.raises(HTTPException) as error:
        service.execute_report("iran-news", TASK, idempotency_key="partial-payment", store=store)
    assert error.value.status_code == 504
    record = store.get_report(error.value.detail["report_id"])
    assert record["status"] == "failed"
    assert record["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] == estimate["estimated_cost_usd"]
    with pytest.raises(HTTPException) as replay:
        service.execute_report("iran-news", TASK, idempotency_key="partial-payment", store=store)
    assert replay.value.status_code == 409
    with pytest.raises(HTTPException) as blocked:
        service.execute_report("iran-news", TASK, idempotency_key="new-attempt", store=store)
    assert blocked.value.detail["code"] == "daily_budget_limit"
    assert len(requests) == len(replies)
    assert store.usage_summary()["daily"]["attempts"] == 1


@pytest.mark.parametrize("missing_stage", ["research", "research_additional", "report"])
@pytest.mark.parametrize("missing_field", ["usage", "service_tier"])
def test_missing_accounting_metadata_in_any_stage_preserves_the_entire_reservation(
        store, mock_openai, missing_stage, missing_field):
    replies = [research_reply(), research_reply(ADDITIONAL_URL), report_reply()]
    replies[["research", "research_additional", "report"].index(missing_stage)].pop(missing_field)
    requests = mock_openai(replies)
    result = service.execute_report("iran-news", TASK, store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 3
    assert result["cost_tracking_complete"] is False
    assert result["estimated_cost_usd"] is None
    assert record["status"] == "succeeded"
    assert record["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] > 0
    assert result["accounting"]["estimated_cost_usd"] == record["reserved_cost_usd"]


def test_combined_usage_does_not_change_independent_request_pricing_tier(store, mock_openai):
    first, second = research_reply(), research_reply(ADDITIONAL_URL)
    for response in (first, second):
        response["usage"]["input_tokens"] = 140000
        response["usage"]["total_tokens"] = 140100
    requests = mock_openai([first, second, report_reply()])
    result = service.execute_report("iran-news", TASK, store=store)
    assert len(requests) == 3
    assert result["usage"]["input_tokens"] == 281200 > 272000
    assert result["cost_tracking_complete"] is True
    # Each research request is below272k; the aggregate is not a single prompt.
    assert result["accounting"]["estimated_cost_usd"] == .734
    assert result["accounting"]["cost_basis"] == "usage_estimate"


def test_full_news_reservation_is_required_before_the_first_provider_request(store, mock_openai):
    estimate = runtime.estimate_reservation("iran-news", TASK)
    store._limits = QuotaLimits(50, str(Decimal(str(estimate["estimated_cost_usd"])) - Decimal(".000001")), "1000")
    requests = mock_openai([])
    with pytest.raises(HTTPException) as error:
        service.execute_report("iran-news", TASK, store=store)
    assert error.value.detail["code"] == "daily_budget_limit"
    assert requests == []
    assert store.usage_summary()["daily"]["attempts"] == 0
