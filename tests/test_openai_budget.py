"""Native OpenAI search, two-stage usage and the persistent spending gate.

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
ARTICLE_TITLE = "Iran fictional test article"
EVENT_NOTE = "A fictional project was announced on 2026-10-19."
PUBLICATION_NOTE = "Published: 2026-10-19T07:00:00Z."
RESEARCH_TEXT = f"{ARTICLE_TITLE}. {PUBLICATION_NOTE} {EVENT_NOTE} [1]"


def research_reply():
    return {
        "status": "completed", "service_tier": "default",
        "output": [
            {"type": "web_search_call", "id": "fixture_search", "status": "completed",
             "action": {"type": "search", "sources": [{"type": "url", "url": ARTICLE_URL}]}},
            {"type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": RESEARCH_TEXT,
                          "annotations": [{"type": "url_citation", "url": ARTICLE_URL,
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


def test_two_stage_success_reconciles_both_usage_records_and_one_search_fee(store, mock_openai):
    requests = mock_openai([research_reply(), report_reply()])
    result = service.execute_report("iran-news", TASK, idempotency_key="two-stage", store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 2
    assert result["item_count"] == 1
    assert result["usage"]["input_tokens"] == 1500
    assert result["usage"]["output_tokens"] == 1000
    assert result["usage"]["web_search_calls"] == 1
    assert [stage["stage"] for stage in result["usage_stages"]] == ["research", "report"]
    # 1,500 input at conservative $2.50/M + 1,000 output at $10/M + one $0.01 search.
    assert result["accounting"]["estimated_cost_usd"] == .02375
    assert record["cost_basis"] == "usage_estimate"
    assert record["reserved_cost_usd"] > record["estimated_cost_usd"]
    assert record["usage"] == result["usage"]
    assert record["pricing_snapshot"]["max_model_calls"] == 2
    assert store.usage_summary()["daily"]["attempts"] == 1
    replay = service.execute_report("iran-news", TASK, idempotency_key="two-stage", store=store)
    assert replay["report_id"] == result["report_id"] and replay["idempotency_replayed"]
    assert len(requests) == 2


def test_rejected_publishers_still_account_for_paid_research_without_another_request(store, mock_openai):
    research = research_reply()
    rejected_url = "https://unapproved.example.test/article/synthetic-test"
    research["output"][0]["action"]["sources"][0]["url"] = rejected_url
    research["output"][1]["content"][0]["annotations"][0]["url"] = rejected_url
    requests = mock_openai([research])
    result = service.execute_report("iran-news", TASK, store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 1
    assert result["status"] == "no_evidence" and result["item_count"] == 0
    assert result["report_model_called"] is False
    assert result["usage"]["input_tokens"] == 300
    assert result["usage"]["output_tokens"] == 100
    assert result["usage"]["web_search_calls"] == 1
    assert result["accounting"]["estimated_cost_usd"] == .01175
    assert record["cost_basis"] == "usage_estimate"
    assert record["reserved_cost_usd"] > record["estimated_cost_usd"] > 0
    assert store.usage_summary()["daily"]["attempts"] == 1


def test_bound_covers_hidden_search_context_and_enforces_two_stateless_requests(store, mock_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_RESEARCH_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "8192")
    estimate = runtime.estimate_reservation("iran-news", TASK)
    requests = mock_openai([research_reply(), report_reply()])
    service.execute_report("iran-news", TASK, store=store)
    research, report = [json.loads(request.content) for request in requests]
    assert research["max_tool_calls"] == 1
    assert research["parallel_tool_calls"] is False
    assert len(research["tools"]) == 1
    assert research["tools"][0]["type"] == "web_search"
    assert research["tools"][0]["return_token_budget"] == "default"
    assert research["tools"][0]["search_context_size"] == "low"
    assert report["tools"] == [] and report["tool_choice"] == "none"
    for payload in (research, report):
        assert "previous_response_id" not in payload and "conversation" not in payload
        assert payload["service_tier"] == "default"
        assert payload["max_output_tokens"] == 8192
    assert len(requests[0].content) <= 50000
    assert len(requests[1].content) <= 200000
    assert estimate["research_input_tokens_bound"] >= 2 * (len(requests[0].content) + 4096) + 131072 + 8192
    assert estimate["report_input_tokens_bound"] >= len(requests[1].content) + 4096
    assert estimate["research_input_tokens_bound"] <= 247456 < 272000
    assert estimate["report_input_tokens_bound"] <= 204096 < 272000
    assert estimate["research_output_tokens_bound"] == estimate["report_output_tokens_bound"] == 8192
    assert estimate["max_web_search_calls"] == 1 and estimate["max_model_calls"] == 2
    assert estimate["estimated_cost_usd"] <= 1.30272


def test_configured_native_search_fee_changes_both_reservation_and_saved_estimate(store, mock_openai, monkeypatch):
    baseline = runtime.estimate_reservation("iran-news", TASK)
    monkeypatch.setenv("OPENAI_WEB_SEARCH_PRICE_USD_PER_CALL", ".037")
    configured = runtime.estimate_reservation("iran-news", TASK)
    assert Decimal(str(configured["estimated_cost_usd"])) - Decimal(str(baseline["estimated_cost_usd"])) == Decimal(".027")
    requests = mock_openai([research_reply(), report_reply()])
    result = service.execute_report("iran-news", TASK, store=store)
    assert len(requests) == 2
    assert result["accounting"]["estimated_cost_usd"] == .05075
    assert store.get_report(result["report_id"])["reserved_cost_usd"] == configured["estimated_cost_usd"]


def test_second_stage_timeout_retains_whole_reservation_and_never_repeats_research(store, mock_openai):
    estimate = runtime.estimate_reservation("iran-news", TASK)
    store._limits = QuotaLimits(50, str(estimate["estimated_cost_usd"]), "1000")

    def reservation_precedes_every_paid_request():
        summary = store.usage_summary()
        assert summary["daily"]["attempts"] == summary["daily"]["pending"] == 1
        assert summary["daily"]["estimated_cost_usd"] == estimate["estimated_cost_usd"]

    requests = mock_openai([research_reply(), "timeout"], reservation_precedes_every_paid_request)
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
    assert len(requests) == 2
    assert store.usage_summary()["daily"]["attempts"] == 1


@pytest.mark.parametrize("missing_stage", ["research", "report"])
@pytest.mark.parametrize("missing_field", ["usage", "service_tier"])
def test_missing_accounting_metadata_in_either_stage_preserves_the_entire_reservation(
        store, mock_openai, missing_stage, missing_field):
    replies = [research_reply(), report_reply()]
    replies[0 if missing_stage == "research" else 1].pop(missing_field)
    requests = mock_openai(replies)
    result = service.execute_report("iran-news", TASK, store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 2
    assert result["cost_tracking_complete"] is False
    assert result["estimated_cost_usd"] is None
    assert record["status"] == "succeeded"
    assert record["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] > 0
    assert result["accounting"]["estimated_cost_usd"] == record["reserved_cost_usd"]
