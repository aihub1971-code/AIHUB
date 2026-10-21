"""Explicit discovery is reserved, bounded and separate from passive cache reads."""
from copy import deepcopy
from datetime import timedelta
import json

from fastapi import HTTPException
import httpx
import pytest

from app import news_feed, research, retrieval, runtime
from app.storage import QuotaLimits, StoreError
from tests.test_news_feed import NOW, article, database, mock_http, page, refresh
from tests.test_research import install_transport, native_call, page_record, provider_response


def source(number=1, **changes):
    return {"id": f"source-{number}", "url": f"https://original-fixture.example/story/{number}",
        "title": f"Iran nuclear diplomacy receives a new proposal {number}",
        "description": "A fictional Iran nuclear negotiation fixture.", "publisher": "Original Fixture",
        "published_at": (NOW - timedelta(hours=1)).isoformat(), "role": "fresh", "source_kind": "article",
        "source_category": "reporting", "retrieved_at": NOW.isoformat(),
        "publisher_text_verified": True, "factual_basis": "publisher_page", "content_sha256": "a" * 64,
        "citation_annotations": [{"type": "url_citation", "url": f"https://original-fixture.example/story/{number}"}],
        "publication_verification": {"verified": True, "method": "publisher_jsonld_datePublished",
            "value": (NOW - timedelta(hours=1)).isoformat(), "path": "article.datePublished",
            "fetched_url": f"https://original-fixture.example/story/{number}", "page_sha256": "a" * 64}, **changes}


def mock_discovery(monkeypatch, sources=None, error=None):
    calls = []
    monkeypatch.setattr(retrieval, "estimate_discovery", lambda *a, **kw: {
        "estimated_cost_usd": .2, "model": runtime.DEFAULT_OPENAI_MODEL})
    def discover(agent, estimate):
        calls.append(agent)
        if error:
            raise error
        values = deepcopy(sources if sources is not None else [source()])
        return {"sources": values, "excluded": {}, "verified_source_count": len(values), "discovered_count": len(values),
            "stages": [{"stage": "web_discovery", "native_actions": 1, "search_calls": 1}],
            "web_search": {"request_count": 1, "search_call_count": 1, "tool_call_count": 1},
            "estimated_cost_usd": .01, "cost_tracking_complete": True,
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20, "usage_reported": True}}
    monkeypatch.setattr(retrieval, "discover_sources", discover)
    return calls


def explicit(database):
    feed = news_feed.FeedStore(database)
    claim = feed.claim("iran-news", explicit_retry=True, allow_web_fallback=True)
    assert claim is not None
    news_feed._refresh(feed, "iran-news", claim, allow_web_fallback=True)
    return news_feed.get_feed("iran-news", "en", database=database)


@pytest.mark.parametrize("primary", ["empty", "insufficient", "provider_error"])
def test_explicit_feed_falls_back_once_with_real_reservation_and_passive_reads_never_repeat(monkeypatch, database, primary):
    calls = mock_discovery(monkeypatch)
    provider_calls = []
    def handler(request):
        provider_calls.append(request)
        return (httpx.Response(429, json={"status": "error", "results": {"code": "ApiKeyLimitExceeded"}})
            if primary == "provider_error" else httpx.Response(200, json=page([article()] if primary == "insufficient" else [])))
    mock_http(monkeypatch, handler)
    result = explicit(database)
    assert len(calls) == len(provider_calls) == 1
    assert result["retrieval"]["web_search"]["status"] == "complete"
    assert result["publisher_verified_count"] == 1
    assert result["actual_count"] == (2 if primary == "insufficient" else 1)
    assert result["items"][0]["published_at_dubai"].endswith("+04:00")
    summary = database.usage_summary()
    assert summary["daily"]["attempts"] == 1 and summary["daily"]["estimated_cost_usd"] == .01
    assert summary["provider_credits"]["newsdata"]["daily_accounted"] == 1
    if primary == "provider_error":
        assert result["retrieval"]["newsdata"]["error"]["code"] == "newsdata_provider_credit_limit"
        assert result["error"]["scope"] == "provider"
    for language in ("ar", "en", "ar"):
        cached = news_feed.get_feed("iran-news", language, database=database)
        assert cached["snapshot_id"] == result["snapshot_id"]
    report_feed = news_feed.get_report_feed("iran-news", language="en", database=database)
    verified = next(item for item in report_feed["sources"] if item["publisher_text_verified"])
    assert verified["source_category"] == "reporting" and verified["factual_basis"] == "publisher_page"
    assert verified["content_sha256"] == "a" * 64 and verified["retrieved_at"] == NOW.isoformat()
    assert verified["citation_annotations"] and verified["publication_verification"]["verified"] is True
    if primary == "insufficient":
        assert any(item["publisher_text_verified"] is False for item in report_feed["sources"])
    assert len(calls) == len(provider_calls) == 1 and database.usage_summary() == summary


def test_report_feed_default_does_not_start_or_reserve_separate_web_fallback(monkeypatch, database):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page([])))
    monkeypatch.setattr(retrieval, "run_feed_discovery", lambda *a, **kw: pytest.fail("Reports own their hybrid reservation"))
    result = news_feed.get_report_feed("iran-news", database=database)
    assert result["retrieval"]["web_search"]["attempted"] is False
    assert database.usage_summary()["daily"]["attempts"] == 0


def test_low_publisher_diversity_uses_web_but_adequate_feed_does_not(monkeypatch, database):
    calls = mock_discovery(monkeypatch)
    diverse = [False]
    def handler(request):
        return httpx.Response(200, json=page([article(n, source_name=f"Fixture {n}" if diverse[0] else "One publisher") for n in range(3)]))
    mock_http(monkeypatch, handler)
    first = explicit(database)
    assert first["retrieval"]["web_search"]["reason"] == "insufficient_publishers" and len(calls) == 1
    database.test_clock[0] += timedelta(seconds=901)
    diverse[0] = True
    second = explicit(database)
    assert second["retrieval"]["web_search"]["status"] == "not_needed" and len(calls) == 1


def test_web_budget_gate_preserves_newsdata_without_a_provider_call_or_hidden_spend(monkeypatch, database):
    calls = mock_discovery(monkeypatch)
    database._limits = QuotaLimits(max_reports_per_day=100, daily_budget_usd=".01", monthly_budget_usd="1")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page()))
    result = explicit(database)
    assert result["actual_count"] == 1 and not calls
    error = result["retrieval"]["web_search"]["error"]
    assert error["code"] == "daily_budget_limit" and error["scope"] == "application"
    assert result["partial"] and database.usage_summary()["daily"]["attempts"] == 0


def test_failed_web_call_retains_reservation_and_existing_metadata_without_automatic_retry(monkeypatch, database):
    calls = mock_discovery(monkeypatch, error=HTTPException(429, {"code": "openai_rate_limited"}))
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page()))
    result = explicit(database)
    assert result["actual_count"] == 1 and result["items"][0]["publisher_text_verified"] is False
    assert result["retrieval"]["web_search"]["error"]["limit_kind"] == "provider_rate"
    assert database.usage_summary()["daily"]["estimated_cost_usd"] == .2
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"published_at": (NOW - timedelta(hours=13)).isoformat(), "role": "background"},
    {"published_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"published_at": None}, {"publisher_text_verified": False},
    {"publication_verification": {"verified": False}},
])
def test_web_fallback_does_not_promote_old_undated_future_or_unattested_sources_to_fresh(monkeypatch, database, changes):
    mock_discovery(monkeypatch, [source(**changes)])
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page([])))
    result = explicit(database)
    assert result["actual_count"] == 0 and result["publisher_verified_count"] == 0
    assert result["retrieval"]["web_search"]["verified_source_count"] == 1
    assert result["excluded"]["web_not_fresh_or_relevant"] == 1


def test_same_feed_discovery_id_cannot_issue_a_second_paid_call(monkeypatch, database):
    calls = mock_discovery(monkeypatch)
    window = news_feed._window(None, NOW)
    retrieval.run_feed_discovery(database, "iran-news", "fixture-refresh", window)
    with pytest.raises(StoreError) as error:
        retrieval.run_feed_discovery(database, "iran-news", "fixture-refresh", window)
    assert error.value.code == "retrieval_already_attempted" and calls == ["iran-news"]
    assert database.usage_summary()["daily"]["attempts"] == 1


def test_discovery_estimate_removes_synthesis_and_enforces_one_bounded_native_stage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-research-key-not-sent")
    window = news_feed._window(None, NOW)
    full = research._estimate_native_reservation("iran-news", research_window=window)
    estimate = retrieval.estimate_discovery("iran-news", research_window=window)
    assert estimate["max_model_calls"] == 1 and estimate["max_native_actions"] == 2
    assert estimate["max_publisher_pages"] == 12 and estimate["report_input_tokens_bound"] == 0
    assert 0 < estimate["estimated_cost_usd"] < full["estimated_cost_usd"]
    observed = []
    response = {"native_calls": [{"action": {"type": "search"}}], "search_calls": 1,
        "usage": {"usage_reported": True, "input_tokens": 10, "output_tokens": 10},
        "tier_matches": True, "usage_bound_matches": True}
    def call(key, payload, *, actions):
        observed.append((payload, actions))
        return response
    monkeypatch.setattr(research, "_call_response", call)
    monkeypatch.setattr(research, "_discovered", lambda value: [{"url": source()["url"]}])
    def verify(discovered, profile, topic, window, *, page_limit):
        assert page_limit == 12 and discovered == [{"url": source()["url"]}]
        return [source()], {}
    monkeypatch.setattr(research, "_verified_sources", verify)
    result = retrieval.discover_sources("iran-news", estimate)
    assert len(observed) == 1 and observed[0][1] == observed[0][0]["max_tool_calls"] == 2
    request = json.loads(observed[0][0]["input"][1]["content"])
    assert request["fresh_only"] is True and request["native_tool_action_limit"] == 2
    assert observed[0][0]["tool_choice"] == "auto"
    assert result["sources"][0]["publisher_text_verified"] is True
    assert result["cost_tracking_complete"] and result["web_search"]["request_count"] == 1


@pytest.mark.parametrize("agent", ["education-observatory", "historical-reading-companion", "uae-eu-trade-talks"])
def test_feed_discovery_does_not_spend_its_small_budget_on_report_background(monkeypatch, agent):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-research-key-not-sent")
    estimate = retrieval.estimate_discovery(agent, research_window=news_feed._window(None, NOW))
    observed = []
    def call(key, payload, *, actions):
        observed.append(payload)
        raise HTTPException(503, {"code": "openai_unavailable"})
    monkeypatch.setattr(research, "_call_response", call)
    with pytest.raises(HTTPException):
        retrieval.discover_sources(agent, estimate)
    assert len(observed) == 1
    instructions = observed[0]["input"][0]["content"]
    assert "FRESH FEED DISCOVERY" in instructions and "Do not research older background" in instructions
    assert "BACKGROUND EVIDENCE" not in instructions and "STRATEGY:" not in instructions
    assert "Do not use open_page or find_in_page" in instructions


def test_real_bounded_discovery_response_is_independently_verified_and_accounted_once(monkeypatch, database):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-research-key-not-sent")
    database._limits = QuotaLimits(max_reports_per_day=100, daily_budget_usd="100", monthly_budget_usd="1000")
    fresh_url, old_url = source()["url"], "https://original-fixture.example/story/old"
    requests, downloads = [], []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["max_tool_calls"] == 2 and payload["tool_choice"] == "auto"
        return httpx.Response(200, json=provider_response("Observed publisher candidates.", native=[
            native_call(url=fresh_url), {**native_call(url=old_url), "id": "ws_second_search"}]))
    install_transport(monkeypatch, handler)
    def download(url, allowed):
        assert url in allowed
        downloads.append(url)
        return page_record(url=url, published=(NOW - timedelta(hours=1 if url == fresh_url else 13)).isoformat())
    monkeypatch.setattr(research, "_download_page", download)
    result = retrieval.run_feed_discovery(database, "iran-news", "bounded-search-fixture", news_feed._window(None, NOW))
    assert len(requests) == 1 and set(downloads) == {fresh_url, old_url}
    assert result["verified_source_count"] == 1 and result["sources"][0]["url"] == fresh_url
    assert result["sources"][0]["publication_verification"]["verified"] is True
    assert result["excluded"]["outside_window"] == 1
    assert result["web_search"]["request_count"] == 1 and result["web_search"]["search_call_count"] == 2
    assert result["cost_tracking_complete"] is True
    with pytest.raises(StoreError) as captured:
        retrieval.run_feed_discovery(database, "iran-news", "bounded-search-fixture", news_feed._window(None, NOW))
    assert captured.value.code == "retrieval_already_attempted" and len(requests) == 1


@pytest.mark.parametrize("native,code", [([], "native_search_not_used"),
    ([native_call(), native_call("open_page"), native_call("find_in_page")], "search_call_limit_exceeded")])
def test_actual_native_validation_failure_retains_feed_reservation_without_retry(monkeypatch, database, native, code):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-research-key-not-sent")
    database._limits = QuotaLimits(max_reports_per_day=100, daily_budget_usd="100", monthly_budget_usd="1000")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=provider_response("No acceptable discovery.", native=native))
    install_transport(monkeypatch, handler)
    monkeypatch.setattr(research, "_download_page", lambda *a, **kw: pytest.fail("Rejected response cannot reach publisher verification"))
    window = news_feed._window(None, NOW)
    estimate = retrieval.estimate_discovery("iran-news", research_window=window)
    with pytest.raises(HTTPException) as captured:
        retrieval.run_feed_discovery(database, "iran-news", "failed-bounded-search-fixture", window)
    assert captured.value.detail["code"] == code and len(requests) == 1
    failed = database.list_reports(status="failed")
    assert len(failed) == 1 and failed[0]["error_code"] == code
    assert failed[0]["cost_basis"] == "reservation_retained"
    assert failed[0]["estimated_cost_usd"] == estimate["estimated_cost_usd"]
    with pytest.raises(StoreError) as duplicate:
        retrieval.run_feed_discovery(database, "iran-news", "failed-bounded-search-fixture", window)
    assert duplicate.value.code == "retrieval_already_attempted" and len(requests) == 1
