"""Native OpenAI publisher discovery, all-stage usage and the spending gate.

All article text and HTTP replies below are synthetic fixtures. No network or
paid request is permitted by the test suite. Fixtures are selected by publisher
filters, so concurrent completion order never changes which stage is tested.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
from threading import Lock

from fastapi import HTTPException
import httpx
import pytest

from app import runtime, service
from app.storage import QuotaLimits, Store


TASK = "أهم أخبار إيران في 2026-10-19"
DOMAIN_GROUPS = [
    ("ft.com",), ("theatlantic.com",), ("politico.com", "politico.eu"),
    ("wsj.com",), ("telegraph.co.uk",), ("aljazeera.com", "aljazeera.net"),
]
# Fictional article paths on the six permitted publishers; no article is fetched.
ARTICLE_URLS = ["https://www." + domains[0] + "/content/ai-hub-synthetic-test" for domains in DOMAIN_GROUPS]
ARTICLE_TITLE = "Iran fictional test article"
EVENT_NOTE = "A fictional project was announced on 2026-10-19."
PUBLICATION_NOTE = "Published: 2026-10-19T07:00:00Z."
RESEARCH_TEXT = f"{ARTICLE_TITLE}. {PUBLICATION_NOTE} {EVENT_NOTE} [1]"


def research_reply(url=ARTICLE_URLS[0]):
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


def all_replies():
    return [research_reply(url) for url in ARTICLE_URLS] + [report_reply()]


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
        requests, seen_stages = [], set()
        lock = Lock()

        def dispatch(request):
            assert request.url == "https://api.openai.com/v1/responses"
            if before_request:
                before_request()
            payload = json.loads(request.content)
            index = DOMAIN_GROUPS.index(tuple(payload["tools"][0]["filters"]["allowed_domains"])) if payload["tools"] else 6
            with lock:
                assert index not in seen_stages, "The runtime repeated a paid stage."
                seen_stages.add(index)
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


def test_news_success_reconciles_all_seven_stages_and_six_search_fees(store, mock_openai):
    requests = mock_openai(all_replies())
    result = service.execute_report("iran-news", TASK, idempotency_key="seven-stage", store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 7
    assert result["item_count"] == 1
    assert result["usage"]["input_tokens"] == 3000
    assert result["usage"]["output_tokens"] == 1500
    assert result["usage"]["web_search_calls"] == 6
    assert [stage["stage"] for stage in result["usage_stages"]] == ["research"] + ["research_additional"] * 5 + ["report"]
    # 3,000 input at conservative $2.50/M + 1,500 output at $10/M + six $0.01 searches.
    assert result["accounting"]["estimated_cost_usd"] == .0825
    assert record["cost_basis"] == "usage_estimate"
    assert record["reserved_cost_usd"] > record["estimated_cost_usd"]
    assert record["usage"] == result["usage"]
    assert record["pricing_snapshot"]["max_model_calls"] == 7
    assert store.usage_summary()["daily"]["attempts"] == 1
    replay = service.execute_report("iran-news", TASK, idempotency_key="seven-stage", store=store)
    assert replay["report_id"] == result["report_id"] and replay["idempotency_replayed"]
    assert len(requests) == 7


def test_rejected_publishers_still_account_for_all_six_paid_discovery_requests(store, mock_openai):
    replies = []
    for _ in DOMAIN_GROUPS:
        research = research_reply()
        rejected_url = "https://unapproved.example.test/article/synthetic-test"
        research["output"][0]["action"]["sources"][0]["url"] = rejected_url
        research["output"][1]["content"][0]["annotations"][0]["url"] = rejected_url
        replies.append(research)
    requests = mock_openai(replies)
    result = service.execute_report("iran-news", TASK, store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 6
    assert result["status"] == "no_evidence" and result["item_count"] == 0
    assert result["report_model_called"] is False
    assert result["usage"]["input_tokens"] == 1800
    assert result["usage"]["output_tokens"] == 600
    assert result["usage"]["web_search_calls"] == 6
    assert result["accounting"]["estimated_cost_usd"] == .0705
    assert record["cost_basis"] == "usage_estimate"
    assert record["reserved_cost_usd"] > record["estimated_cost_usd"] > 0
    assert store.usage_summary()["daily"]["attempts"] == 1


def test_bound_covers_each_publisher_context_and_enforces_seven_stateless_requests(store, mock_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_RESEARCH_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "8192")
    estimate = runtime.estimate_reservation("iran-news", TASK)
    requests = mock_openai(all_replies())
    service.execute_report("iran-news", TASK, store=store)
    payloads = [json.loads(request.content) for request in requests]
    research_requests = {tuple(payload["tools"][0]["filters"]["allowed_domains"]): (request, payload)
                         for request, payload in zip(requests, payloads) if payload["tools"]}
    assert set(research_requests) == set(DOMAIN_GROUPS)
    assert set().union(*map(set, research_requests)) == set(runtime.ALLOWED_SOURCE_DOMAINS)
    report_request = next(request for request, payload in zip(requests, payloads) if not payload["tools"])
    report = json.loads(report_request.content)
    assert report["tools"] == [] and report["tool_choice"] == "none"
    for payload in payloads:
        assert "previous_response_id" not in payload and "conversation" not in payload
        assert payload["service_tier"] == "default"
        assert payload["max_output_tokens"] == 8192
    assert estimate["research_request_count"] == 6
    bounds = estimate["research_request_input_tokens_bounds"]
    assert len(bounds) == 6
    for domains, bound in zip(DOMAIN_GROUPS, bounds):
        request, payload = research_requests[domains]
        assert payload["max_tool_calls"] == 1 and payload["parallel_tool_calls"] is False
        assert len(payload["tools"]) == 1 and payload["tools"][0]["type"] == "web_search"
        assert payload["tools"][0]["return_token_budget"] == "default"
        assert payload["tools"][0]["search_context_size"] == "low"
        assert len(request.content) <= 50000
        assert bound >= 2 * (len(request.content) + 4096) + 131072 + 8192
        assert bound <= 247456 < 272000
    assert len(report_request.content) <= 200000
    assert estimate["research_input_tokens_bound"] == sum(bounds)
    assert estimate["report_input_tokens_bound"] >= len(report_request.content) + 4096
    assert estimate["report_input_tokens_bound"] <= 204096 < 272000
    assert estimate["research_request_output_tokens_bounds"] == [8192] * 6
    assert estimate["research_output_tokens_bound"] == 49152
    assert estimate["report_output_tokens_bound"] == 8192
    assert estimate["max_web_search_calls"] == 6 and estimate["max_model_calls"] == 7
    assert estimate["estimated_cost_usd"] <= 4.85552


def test_configured_search_fee_changes_every_reserved_and_accounted_action(store, mock_openai, monkeypatch):
    baseline = runtime.estimate_reservation("iran-news", TASK)
    monkeypatch.setenv("OPENAI_WEB_SEARCH_PRICE_USD_PER_CALL", ".037")
    configured = runtime.estimate_reservation("iran-news", TASK)
    assert Decimal(str(configured["estimated_cost_usd"])) - Decimal(str(baseline["estimated_cost_usd"])) == Decimal(".162")
    requests = mock_openai(all_replies())
    result = service.execute_report("iran-news", TASK, store=store)
    assert len(requests) == 7
    assert result["accounting"]["estimated_cost_usd"] == .2445
    assert store.get_report(result["report_id"])["reserved_cost_usd"] == configured["estimated_cost_usd"]


def test_partial_publisher_timeout_keeps_eligible_report_but_retains_whole_reservation(store, mock_openai):
    estimate = runtime.estimate_reservation("iran-news", TASK)
    store._limits = QuotaLimits(50, str(estimate["estimated_cost_usd"]), "1000")

    def reservation_precedes_every_paid_request():
        summary = store.usage_summary()
        assert summary["daily"]["attempts"] == summary["daily"]["pending"] == 1
        assert summary["daily"]["estimated_cost_usd"] == estimate["estimated_cost_usd"]

    replies = all_replies()
    replies[5] = "timeout"
    requests = mock_openai(replies, reservation_precedes_every_paid_request)
    result = service.execute_report("iran-news", TASK, idempotency_key="partial-payment", store=store)
    record = store.get_report(result["report_id"])
    assert result["item_count"] == 1 and result["web_search"]["research_incomplete"] is True
    assert result["web_search"]["failed_request_count"] == 1 and result["web_search"]["billing_uncertain"] is True
    assert result["cost_tracking_complete"] is False and result["estimated_cost_usd"] is None
    assert record["status"] == "succeeded" and record["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] == estimate["estimated_cost_usd"]
    replay = service.execute_report("iran-news", TASK, idempotency_key="partial-payment", store=store)
    assert replay["report_id"] == result["report_id"] and replay["idempotency_replayed"]
    with pytest.raises(HTTPException) as blocked:
        service.execute_report("iran-news", TASK, idempotency_key="new-attempt", store=store)
    assert blocked.value.detail["code"] == "daily_budget_limit"
    assert len(requests) == 7 and store.usage_summary()["daily"]["attempts"] == 1


@pytest.mark.parametrize("failure_stage", ["all_research", "report"])
def test_complete_failure_retains_whole_reservation_and_never_repeats_a_stage(store, mock_openai, failure_stage):
    replies = ["timeout"] * 6 if failure_stage == "all_research" else all_replies()[:-1] + ["timeout"]
    requests = mock_openai(replies)
    with pytest.raises(HTTPException) as error:
        service.execute_report("iran-news", TASK, idempotency_key="failed-stages", store=store)
    assert error.value.status_code == 504
    record = store.get_report(error.value.detail["report_id"])
    assert record["status"] == "failed" and record["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] > 0
    with pytest.raises(HTTPException) as replay:
        service.execute_report("iran-news", TASK, idempotency_key="failed-stages", store=store)
    assert replay.value.status_code == 409
    assert len(requests) == len(replies)
    assert store.usage_summary()["daily"]["attempts"] == 1


@pytest.mark.parametrize("missing_stage", range(7))
@pytest.mark.parametrize("missing_field", ["usage", "service_tier"])
def test_missing_accounting_metadata_in_any_stage_preserves_the_entire_reservation(
        store, mock_openai, missing_stage, missing_field):
    replies = all_replies()
    replies[missing_stage].pop(missing_field)
    requests = mock_openai(replies)
    result = service.execute_report("iran-news", TASK, store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 7
    assert result["cost_tracking_complete"] is False and result["estimated_cost_usd"] is None
    assert record["status"] == "succeeded" and record["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] > 0
    assert result["accounting"]["estimated_cost_usd"] == record["reserved_cost_usd"]


def test_combined_usage_does_not_change_independent_request_pricing_tier(store, mock_openai):
    replies = all_replies()
    for response in replies[:-1]:
        response["usage"]["input_tokens"] = 140000
        response["usage"]["total_tokens"] = 140100
    requests = mock_openai(replies)
    result = service.execute_report("iran-news", TASK, store=store)
    assert len(requests) == 7
    assert result["usage"]["input_tokens"] == 841200 > 272000
    assert result["cost_tracking_complete"] is True
    # Each research request is below272k; the aggregate is not a single prompt.
    assert result["accounting"]["estimated_cost_usd"] == 2.178
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
