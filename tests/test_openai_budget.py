"""Unified OpenAI discovery, optional evidence recovery and the spending gate.

All article text and HTTP replies below are synthetic fixtures. No network or
paid request is permitted by the test suite. One research response includes the
complete publisher allowlist; one reserved recovery may precede synthesis.
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
NEWS_SOURCE_DOMAINS = [
    "ft.com", "theatlantic.com", "politico.com", "politico.eu",
    "wsj.com", "telegraph.co.uk", "aljazeera.com", "aljazeera.net",
]
# Fictional article path on a permitted publisher; no article is fetched.
ARTICLE_URL = "https://www.ft.com/content/ai-hub-synthetic-test"
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


def six_publisher_reply():
    """Every family has a distinct, dated, fictional article in the first pass."""
    sources = [(ARTICLE_URL, ARTICLE_TITLE),
               ("https://www.theatlantic.com/world/ai-hub-fixture", "Iran fictional university programme"),
               ("https://www.politico.eu/article/ai-hub-fixture", "Tehran synthetic diplomatic talks"),
               ("https://www.wsj.com/world/ai-hub-fixture", "Iranian test-only agricultural review"),
               ("https://www.telegraph.co.uk/world-news/ai-hub-fixture", "Hormuz mock navigation bulletin"),
               ("https://www.aljazeera.net/news/ai-hub-fixture", "Iran hypothetical cultural initiative")]
    paragraphs, annotations = [], []
    for index, (url, title) in enumerate(sources):
        marker = f"[fixture-{index}]"
        paragraph = f"Title: {title}. {url} {PUBLICATION_NOTE} {EVENT_NOTE} {marker}"
        start = sum(len(item) + 2 for item in paragraphs)
        annotations.append({"type": "url_citation", "url": url, "title": title,
                            "start_index": start + paragraph.index(marker),
                            "end_index": start + len(paragraph)})
        paragraphs.append(paragraph)
    reply = research_reply()
    reply["output"][0]["action"]["sources"] = [{"url": url, "title": title} for url, title in sources]
    reply["output"][1]["content"][0] = {"type": "output_text", "text": "\n\n".join(paragraphs),
                                       "annotations": annotations}
    return reply


def all_replies(*, full_coverage=False):
    if full_coverage:
        return [six_publisher_reply(), report_reply()]
    return [research_reply(), empty_native_discovery(), report_reply()]


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
            payload = json.loads(request.content)
            index = len(requests)
            if payload["tools"]:
                scope = payload["tools"][0]["filters"]["allowed_domains"]
                assert scope and set(scope) <= set(NEWS_SOURCE_DOMAINS)
                if index == 0:
                    assert scope == NEWS_SOURCE_DOMAINS
            assert index < 3, "The runtime exceeded the reserved stage limit."
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


def test_news_success_reconciles_discovery_breadth_report_and_two_search_fees(store, mock_openai):
    requests = mock_openai(all_replies())
    result = service.execute_report("iran-news", TASK, idempotency_key="three-stage", store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 3
    assert result["item_count"] == 1
    assert result["usage"]["input_tokens"] == 1800
    assert result["usage"]["output_tokens"] == 1100
    assert result["usage"]["web_search_calls"] == 2
    assert [stage["stage"] for stage in result["usage_stages"]] == ["research", "research_recovery", "report"]
    # 1,800 input at conservative $2.50/M + 1,100 output at $10/M + two $0.01 searches.
    assert result["accounting"]["estimated_cost_usd"] == .0355
    assert record["cost_basis"] == "usage_estimate"
    assert record["reserved_cost_usd"] > record["estimated_cost_usd"]
    assert record["usage"] == result["usage"]
    assert record["pricing_snapshot"]["max_model_calls"] == 3
    payloads = [json.loads(request.content) for request in requests]
    assert payloads[0]["tools"][0]["filters"]["allowed_domains"] == NEWS_SOURCE_DOMAINS
    assert payloads[1]["tools"][0]["filters"]["allowed_domains"] == NEWS_SOURCE_DOMAINS[1:]
    assert result["web_search"]["research_passes"][1]["allowed_source_domains"] == NEWS_SOURCE_DOMAINS[1:]
    assert store.usage_summary()["daily"]["attempts"] == 1
    replay = service.execute_report("iran-news", TASK, idempotency_key="three-stage", store=store)
    assert replay["report_id"] == result["report_id"] and replay["idempotency_replayed"]
    assert len(requests) == 3


def test_rejected_publisher_evidence_still_accounts_for_discovery_and_empty_breadth(store, mock_openai):
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


def empty_native_discovery():
    reply = research_reply()
    reply["output"][1]["content"][0] = {
        "type": "output_text", "text": "No usable article notes were returned in this synthetic discovery.",
        "annotations": [],
    }
    return reply


@pytest.mark.parametrize("agent_id", ["iran-news", "geopolitical-morning"])
def test_missing_native_titles_recover_once_with_one_reservation_and_idempotent_replay(
        store, mock_openai, agent_id):
    estimate = runtime.estimate_reservation(agent_id, TASK)

    def reserved_before_request():
        usage = store.usage_summary()["daily"]
        assert usage["attempts"] == 1 and usage["pending"] == 1
        assert usage["estimated_cost_usd"] == estimate["estimated_cost_usd"]

    requests = mock_openai([empty_native_discovery(), research_reply(), report_reply()],
                           reserved_before_request)
    result = service.execute_report(agent_id, TASK, idempotency_key="recover-empty", store=store)
    assert result["item_count"] == 1 and len(requests) == 3
    assert result["model_call_count"] == 3
    assert result["web_search"]["request_count"] == 2
    assert result["web_search"]["evidence_recovery_request_count"] == 1
    assert result["web_search"]["evidence_recovery_status"] == "used"
    assert [stage["stage"] for stage in result["usage_stages"]] == [
        "research", "research_recovery", "report"]
    assert result["usage"]["input_tokens"] == 1800
    assert result["usage"]["output_tokens"] == 1100
    assert result["usage"]["web_search_calls"] == 2
    assert result["accounting"]["estimated_cost_usd"] == .0355
    payloads = [json.loads(request.content) for request in requests]
    for payload in payloads:
        assert "previous_response_id" not in payload and "conversation" not in payload
    for payload in payloads[:2]:
        assert payload["max_tool_calls"] == 1 and payload["parallel_tool_calls"] is False
        assert payload["tools"][0]["filters"]["allowed_domains"] == NEWS_SOURCE_DOMAINS
    assert "UNTRUSTED_NATIVE_URL_CANDIDATES" in json.dumps(payloads[1])
    assert estimate["research_request_input_tokens_bounds"][1] >= (
        2 * (len(requests[1].content) + 4096) + 131072 + payloads[1]["max_output_tokens"])
    repeated = service.execute_report(agent_id, TASK, idempotency_key="recover-empty", store=store)
    assert repeated["report_id"] == result["report_id"] and repeated["idempotency_replayed"]
    assert len(requests) == 3 and store.usage_summary()["daily"]["attempts"] == 1


def test_empty_recovery_stops_without_a_third_search_or_report(store, mock_openai):
    requests = mock_openai([empty_native_discovery(), empty_native_discovery()])
    result = service.execute_report("iran-news", TASK, store=store)
    assert len(requests) == 2 and result["item_count"] == 0
    assert result["report_model_called"] is False
    assert result["web_search"]["evidence_recovery_status"] == "empty"
    assert result["accounting"]["estimated_cost_usd"] == .0235


def test_recovery_timeout_retains_full_reservation_and_cannot_retry_same_attempt(store, mock_openai):
    estimate = runtime.estimate_reservation("iran-news", TASK)
    requests = mock_openai([empty_native_discovery(), "timeout"])
    with pytest.raises(HTTPException) as error:
        service.execute_report("iran-news", TASK, idempotency_key="recovery-timeout", store=store)
    assert error.value.detail["code"] == "openai_timeout" and len(requests) == 2
    assert store.usage_summary()["daily"]["estimated_cost_usd"] == estimate["estimated_cost_usd"]
    with pytest.raises(HTTPException):
        service.execute_report("iran-news", TASK, idempotency_key="recovery-timeout", store=store)
    assert len(requests) == 2 and store.usage_summary()["daily"]["attempts"] == 1


def test_breadth_timeout_preserves_first_article_with_full_reservation_and_idempotent_replay(store, mock_openai):
    estimate = runtime.estimate_reservation("iran-news", TASK)
    requests = mock_openai([research_reply(), "timeout", report_reply()])
    result = service.execute_report("iran-news", TASK, idempotency_key="breadth-partial", store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 3 and result["model_call_count"] == 3
    assert result["item_count"] == 1 and result["items"][0]["url"] == ARTICLE_URL
    assert result["web_search"]["research_incomplete"] is True
    assert result["web_search"]["failed_request_count"] == 1
    assert result["web_search"]["evidence_recovery_request_count"] == 1
    assert [stage["stage"] for stage in result["usage_stages"]] == [
        "research", "research_recovery", "report"]
    assert result["usage_stages"][1]["usage"]["usage_reported"] is False
    assert result["cost_tracking_complete"] is False
    assert record["status"] == "succeeded" and record["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] == estimate["estimated_cost_usd"]
    assert result["accounting"]["estimated_cost_usd"] == estimate["estimated_cost_usd"]
    payloads = [json.loads(request.content) for request in requests]
    assert [bool(payload["tools"]) for payload in payloads] == [True, True, False]
    assert payloads[0]["tools"][0]["filters"]["allowed_domains"] == NEWS_SOURCE_DOMAINS
    assert payloads[1]["tools"][0]["filters"]["allowed_domains"] == NEWS_SOURCE_DOMAINS[1:]
    replay = service.execute_report("iran-news", TASK, idempotency_key="breadth-partial", store=store)
    assert replay["report_id"] == result["report_id"] and replay["idempotency_replayed"] is True
    assert len(requests) == 3 and store.usage_summary()["daily"]["attempts"] == 1


def test_bound_covers_optional_recovery_but_all_six_eligible_families_skip_it(store, mock_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_RESEARCH_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "8192")
    estimate = runtime.estimate_reservation("iran-news", TASK)
    requests = mock_openai(all_replies(full_coverage=True))
    service.execute_report("iran-news", TASK, store=store)
    assert len(requests) == 2
    payloads = [json.loads(request.content) for request in requests]
    research, report = payloads
    research_request, report_request = requests
    assert research["tools"][0]["filters"]["allowed_domains"] == NEWS_SOURCE_DOMAINS
    assert report["tools"] == [] and report["tool_choice"] == "none"
    for payload in payloads:
        assert "previous_response_id" not in payload and "conversation" not in payload
        assert payload["service_tier"] == "default"
        assert payload["max_output_tokens"] == 8192
    assert estimate["research_request_count"] == 2
    assert estimate["initial_research_request_count"] == 1
    assert estimate["max_evidence_recovery_requests"] == 1
    bounds = estimate["research_request_input_tokens_bounds"]
    assert len(bounds) == 2
    assert research["max_tool_calls"] == 1 and research["parallel_tool_calls"] is False
    assert len(research["tools"]) == 1 and research["tools"][0]["type"] == "web_search"
    assert research["tools"][0]["return_token_budget"] == "default"
    assert research["tools"][0]["search_context_size"] == "high"
    assert research["tools"][0]["external_web_access"] is True
    assert len(research_request.content) <= 50000
    assert bounds[0] >= 2 * (len(research_request.content) + 4096) + 131072 + 8192
    assert bounds[0] <= 247456 < 272000
    assert bounds[1] == 2 * (50000 + 4096) + 131072 + 8192
    assert len(report_request.content) <= 200000
    assert estimate["research_input_tokens_bound"] == sum(bounds)
    assert estimate["report_input_tokens_bound"] >= len(report_request.content) + 4096
    assert estimate["report_input_tokens_bound"] <= 204096 < 272000
    assert estimate["research_request_output_tokens_bounds"] == [8192, 8192]
    assert estimate["research_output_tokens_bound"] == 16384
    assert estimate["report_output_tokens_bound"] == 8192
    assert estimate["max_web_search_calls"] == 2 and estimate["max_model_calls"] == 3
    assert estimate["estimated_cost_usd"] <= 2.01328


def test_configured_search_fee_reserves_two_possible_actions_but_charges_only_actual_use(store, mock_openai, monkeypatch):
    baseline = runtime.estimate_reservation("iran-news", TASK)
    monkeypatch.setenv("OPENAI_WEB_SEARCH_PRICE_USD_PER_CALL", ".037")
    configured = runtime.estimate_reservation("iran-news", TASK)
    assert Decimal(str(configured["estimated_cost_usd"])) - Decimal(str(baseline["estimated_cost_usd"])) == Decimal(".054")
    requests = mock_openai(all_replies(full_coverage=True))
    result = service.execute_report("iran-news", TASK, store=store)
    assert len(requests) == 2
    assert result["accounting"]["estimated_cost_usd"] == .05075
    assert store.get_report(result["report_id"])["reserved_cost_usd"] == configured["estimated_cost_usd"]


def test_synthesis_timeout_retains_whole_reservation_after_successful_unified_research(store, mock_openai):
    estimate = runtime.estimate_reservation("iran-news", TASK)
    store._limits = QuotaLimits(50, str(estimate["estimated_cost_usd"]), "1000")

    def reservation_precedes_every_paid_request():
        summary = store.usage_summary()
        assert summary["daily"]["attempts"] == summary["daily"]["pending"] == 1
        assert summary["daily"]["estimated_cost_usd"] == estimate["estimated_cost_usd"]

    replies = all_replies()
    replies[-1] = "timeout"
    requests = mock_openai(replies, reservation_precedes_every_paid_request)
    with pytest.raises(HTTPException) as error:
        service.execute_report("iran-news", TASK, idempotency_key="partial-payment", store=store)
    assert error.value.status_code == 504
    record = store.get_report(error.value.detail["report_id"])
    assert record["status"] == "failed" and record["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] == estimate["estimated_cost_usd"]
    with pytest.raises(HTTPException) as replay:
        service.execute_report("iran-news", TASK, idempotency_key="partial-payment", store=store)
    assert replay.value.status_code == 409
    with pytest.raises(HTTPException) as blocked:
        service.execute_report("iran-news", TASK, idempotency_key="new-attempt", store=store)
    assert blocked.value.detail["code"] == "daily_budget_limit"
    assert len(requests) == 3 and store.usage_summary()["daily"]["attempts"] == 1


@pytest.mark.parametrize("failure_stage", ["research", "report"])
def test_complete_failure_retains_whole_reservation_and_never_repeats_a_stage(store, mock_openai, failure_stage):
    replies = ["timeout"] if failure_stage == "research" else [research_reply(), empty_native_discovery(), "timeout"]
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


@pytest.mark.parametrize("missing_stage", range(3))
@pytest.mark.parametrize("missing_field", ["usage", "service_tier"])
def test_missing_accounting_metadata_in_any_stage_preserves_the_entire_reservation(
        store, mock_openai, missing_stage, missing_field):
    replies = all_replies()
    replies[missing_stage].pop(missing_field)
    requests = mock_openai(replies)
    result = service.execute_report("iran-news", TASK, store=store)
    record = store.get_report(result["report_id"])
    assert len(requests) == 3
    assert result["cost_tracking_complete"] is False and result["estimated_cost_usd"] is None
    assert record["status"] == "succeeded" and record["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] > 0
    assert result["accounting"]["estimated_cost_usd"] == record["reserved_cost_usd"]


def test_usage_beyond_the_actual_synthesis_request_bound_retains_the_full_reservation(store, mock_openai):
    replies = all_replies()
    # A short source pack cannot account for 140k synthesis input tokens, even
    # though this is below the model's standard-context pricing threshold.
    replies[-1]["usage"]["input_tokens"] = 140000
    replies[-1]["usage"]["total_tokens"] = 140000 + replies[-1]["usage"]["output_tokens"]
    requests = mock_openai(replies)
    result = service.execute_report("iran-news", TASK, store=store)
    assert len(requests) == 3
    assert result["usage"]["input_tokens"] == 140600
    assert result["usage_stages"][0]["usage_bound_matches"] is True
    assert result["usage_stages"][1]["usage_bound_matches"] is True
    assert result["usage_stages"][-1]["usage_bound_matches"] is False
    assert result["cost_tracking_complete"] is False and result["estimated_cost_usd"] is None
    record = store.get_report(result["report_id"])
    assert result["accounting"]["cost_basis"] == "reservation_retained"
    assert record["estimated_cost_usd"] == record["reserved_cost_usd"] > 0


def test_full_news_reservation_is_required_before_the_first_provider_request(store, mock_openai):
    estimate = runtime.estimate_reservation("iran-news", TASK)
    store._limits = QuotaLimits(50, str(Decimal(str(estimate["estimated_cost_usd"])) - Decimal(".000001")), "1000")
    requests = mock_openai([])
    with pytest.raises(HTTPException) as error:
        service.execute_report("iran-news", TASK, store=store)
    assert error.value.detail["code"] == "daily_budget_limit"
    assert requests == []
    assert store.usage_summary()["daily"]["attempts"] == 0
