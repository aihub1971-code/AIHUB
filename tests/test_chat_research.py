"""Bounded chat discovery uses attested, checked references without news gates."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import socket

from fastapi import HTTPException
import httpx
import pytest

from app import chat_research, research, runtime
from app.agent_profiles import get_profile
from tests.test_research import (DOWNLOAD_PAGE, install_transport, native_call,
    page_record, provider_response, source_record)


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
QUESTION = "What does the original study say about lunar geology?"
URL = "https://original-study.example/archive/lunar-geology"
TITLE = "Lunar geology from the original expedition"
AGENTS = ["iran-news", "geopolitical-morning", "ubi-research", "uae-eu-trade-talks",
          "education-observatory", "economic-cultural-briefing", "historical-reading-companion"]


@pytest.fixture(autouse=True)
def offline_discovery(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-chat-research-key-not-sent")
    def blocked(*args, **kwargs):
        raise AssertionError("Use a synthetic publisher fixture; no external page requests.")
    monkeypatch.setattr(research, "_download_page", blocked)


def plan(agent="iran-news", **kwargs):
    return chat_research.prepare_chat_research(agent, QUESTION, now=NOW, **kwargs)


def fake_response(monkeypatch, *, native=None, usage=True, text="Observed source notes."):
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=provider_response(text,
            native=[native_call(url=URL)] if native is None else native, usage=usage))
    install_transport(monkeypatch, handle)
    return calls


def test_preflight_prices_one_discovery_without_network_or_private_context(monkeypatch):
    monkeypatch.setattr(research, "_call_response", lambda *args, **kwargs: pytest.fail("Preflight cannot call providers"))
    prepared = plan(topic_context="The previous question concerned the original expedition.")
    payload = chat_research._payload(prepared["model"], prepared["question"], prepared["language"],
        prepared["topic_context"], prepared["as_of"], prepared["max_native_actions"])
    data = json.loads(payload["input"][1]["content"])
    assert set(data) == {"question", "same_thread_topic_context", "answer_language", "as_of",
                         "native_tool_action_limit", "discovery_actions"}
    assert data["question"] == QUESTION and data["same_thread_topic_context"] == prepared["topic_context"]
    assert "filters" not in payload["tools"][0] and payload["tool_choice"] == "auto"
    assert payload["max_tool_calls"] == prepared["max_native_actions"] == 2
    assert prepared["max_model_calls"] == 1 and prepared["max_publisher_pages"] == 8
    assert prepared["estimated_cost_usd"] > 0 and prepared["research_input_tokens_bound"] > 0
    assert "selected agent's usual specialty" in payload["input"][0]["content"]
    assert "do not use open_page or find_in_page" in payload["input"][0]["content"]
    assert "12-hour window" not in data and "window" not in data


@pytest.mark.parametrize("agent", AGENTS)
def test_every_active_agent_can_verify_a_reference_outside_its_news_domain(agent):
    prepared = plan(agent)
    page = page_record(URL, TITLE, published="1969-07-20", article_type="ScholarlyArticle")
    source, rejected = research._verify_page(source_record(URL, TITLE), page, get_profile(agent),
        QUESTION, research._window(cutoff=prepared["as_of"]), reference_mode=True)
    assert rejected is None and source["role"] == "reference"
    assert source["published_at"] == "1969-07-20" and source["publisher_text_verified"] is True
    assert source["source_category"] == "research"


@pytest.mark.parametrize("article_type,journal,category", [
    ("ScholarlyArticle", False, "research"),
    ("Article", True, "research"),
    ("NewsArticle", False, "official_statement"),
    ("OpinionNewsArticle", True, "opinion"),
])
def test_government_hosted_journal_evidence_is_not_an_official_policy_claim(article_type, journal, category):
    url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/"
    page = page_record(url, TITLE, published="2025-03-01", article_type=article_type)
    if journal:
        page["html"] = page["html"].replace("</head>",
            '<meta name="citation_journal_title" content="Synthetic Research Journal"></head>')
    source, rejected = research._verify_page(source_record(url, TITLE), page,
        get_profile("education-observatory"), QUESTION, research._window(cutoff=NOW.isoformat()),
        reference_mode=True)
    assert rejected is None and source["source_category"] == category


@pytest.mark.parametrize("published,precision,verified", [
    ("2010-03-04T10:30:00Z", "timestamp", True), ("2010-03-04", "day", False),
    (None, "unknown", False), ("2026-10-21T11:00:00Z", "timestamp", True),
])
def test_references_keep_publication_precision_without_fresh_news_label(monkeypatch, published, precision, verified):
    calls = fake_response(monkeypatch)
    monkeypatch.setattr(research, "_download_page", lambda url, authorized:
        page_record(URL, TITLE, published=published))
    stages = []
    result = chat_research.discover_chat_sources(plan(), on_stage=stages.append)
    source = result["sources"][0]
    assert source["role"] == "reference" and source["publication_precision"] == precision
    assert source["publication_verification"]["verified"] is verified
    assert source["publisher_text_verified"] and source["evidence_level"] == "publisher_page_verified"
    assert source["published_at"] != source["retrieved_at"]
    if published is None:
        assert source["published_at"] is None
    else:
        assert source["published_at"] is not None
    assert stages == ["searching", "checking_sources"] and len(calls) == 1
    assert result["cost_tracking_complete"] and result["estimated_cost_usd"] > 0
    assert result["search_call_count"] == result["native_action_count"] == 1
    assert result["web_search"]["request_count"] == 1


def test_chat_reference_mode_does_not_relax_report_article_page_guard():
    page = page_record(URL, TITLE, published="2010-03-04T10:30:00Z")
    common = (source_record(URL, TITLE), page, get_profile("iran-news"), QUESTION, research._window(cutoff=NOW))
    # The shared reference URL deliberately has an /archive/ path, which is
    # unsuitable for a news article even when the page carries article markup.
    assert research._verify_page(*common)[1] == "non_article_page"
    assert research._verify_page(*common, reference_mode=True)[0]["role"] == "reference"


def test_chat_reference_mode_does_not_relax_report_relevance_or_timestamp_guards():
    # Use an eligible article path so the news checks reach relevance and time.
    url = "https://original-study.example/articles/lunar-geology"
    profile, window = get_profile("iran-news"), research._window(cutoff=NOW)
    page = page_record(url, TITLE, published="2010-03-04T10:30:00Z")
    common = (source_record(url, TITLE), page, profile, QUESTION, window)
    assert research._verify_page(*common)[1] == "irrelevant"
    assert research._verify_page(*common, reference_mode=True)[0]["role"] == "reference"

    title = "Iran and US announce a new round of nuclear diplomacy"
    page = page_record(url, title, published="2010-03-04T10:30:00Z")
    common = (source_record(url, title), page, profile, "Iran US conflict", window)
    assert research._verify_page(*common)[1] == "outside_window"
    reference, rejected = research._verify_page(*common, reference_mode=True)
    assert rejected is None and reference["role"] == "reference"
    assert reference["published_at"] == "2010-03-04T10:30:00+00:00"

    future = page_record(url, title, published="2026-10-22T10:00:00Z")
    for reference_mode in (False, True):
        assert research._verify_page(source_record(url, title), future, profile,
            "Iran US conflict", window, reference_mode=reference_mode)[1] == "future_publication"


def test_date_only_conflicts_are_not_promoted_to_a_precise_reference_date():
    page = page_record(URL, TITLE, published="2010-03-04",
        html_suffix='<meta property="article:published_time" content="2012-05-06">')
    assert research._verify_page(source_record(URL, TITLE), page, get_profile("iran-news"),
        QUESTION, research._window(cutoff=NOW), reference_mode=True)[1] == "conflicting_publication_dates"


def test_older_live_entry_can_support_a_dated_reference_without_becoming_fresh():
    page = page_record(URL, TITLE, published=None, article_type="LiveBlogPosting", extra={
        "dateModified": "2026-10-21T11:59:00Z", "liveBlogUpdate": [{
            "@type": "BlogPosting", "datePublished": "2010-03-04T10:30:00Z",
            "headline": TITLE, "articleBody": "An archived original entry documents the expedition's geological observations."}]})
    source, reason = research._verify_page(source_record(URL, TITLE), page, get_profile("iran-news"),
        QUESTION, research._window(cutoff=NOW), reference_mode=True)
    assert reason is None and source["role"] == "reference"
    assert source["published_at"] == "2010-03-04T10:30:00+00:00"
    assert source["publication_verification"]["method"] == "publisher_jsonld_live_entry"


def test_only_attested_sources_are_inspected_and_eight_page_cap_is_independent_of_results(monkeypatch):
    urls = [f"https://original-study.example/archive/study-{number}" for number in range(12)]
    native = [native_call(sources=[{"url": url, "title": TITLE} for url in urls]),
              native_call(sources=[])]
    calls = fake_response(monkeypatch, native=native,
        text="A model-only URL is not attested: https://unattested.example/made-up")
    inspected = []
    def download(url, authorized):
        inspected.append(url)
        assert url in authorized and "https://unattested.example/made-up" not in authorized
        # Failed page verification still consumes the inspection bound.
        raise ValueError("publisher_unavailable")
    monkeypatch.setattr(research, "_download_page", download)
    result = chat_research.discover_chat_sources(plan())
    assert len(calls) == 1 and len(inspected) == 8 and set(inspected) == set(urls[:8])
    assert result["sources"] == [] and result["status"] == "no_verified_sources"
    assert result["diagnostics"]["excluded_source_counts"] == {"publisher_unavailable": 8}
    assert result["diagnostics"]["publisher_pages_attempted"] == 8
    assert result["cost_tracking_complete"] and result["estimated_cost_usd"] > 0
    assert any("No publisher page" in limitation for limitation in result["limitations"])
    assert "model-only" not in json.dumps(result)


def test_attested_host_resolving_to_private_ip_is_still_blocked(monkeypatch):
    fake_response(monkeypatch)
    monkeypatch.setattr(research, "_download_page", DOWNLOAD_PAGE)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs:
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 443))])
    monkeypatch.setattr(research, "_PinnedHTTPS", lambda *args, **kwargs: pytest.fail("Private destination must never connect"))
    result = chat_research.discover_chat_sources(plan())
    assert result["sources"] == []
    assert result["diagnostics"]["excluded_source_counts"] == {"unsafe_or_unresolved_destination": 1}


@pytest.mark.parametrize("changes", [
    {"max_native_actions": 3}, {"max_publisher_pages": 9}, {"max_model_calls": 2},
    {"question": "A different unreserved question"}, {"research_input_tokens_bound": 0},
])
def test_changed_plan_cannot_expand_paid_work(monkeypatch, changes):
    prepared = {**plan(), **changes}
    monkeypatch.setattr(research, "_call_response", lambda *args, **kwargs: pytest.fail("Invalid reservation cannot perform IO"))
    with pytest.raises(HTTPException) as error:
        chat_research.discover_chat_sources(prepared)
    assert error.value.detail["code"] == "chat_research_reservation_invalid"


def test_unknown_disabled_and_unpermitted_agents_cannot_prepare_research(monkeypatch):
    with pytest.raises(HTTPException) as error:
        plan("unknown-agent")
    assert error.value.status_code == 404
    original = get_profile("iran-news")
    monkeypatch.setattr(chat_research, "get_profile", lambda agent: {**original, "enabled": False})
    with pytest.raises(HTTPException) as error:
        plan()
    assert error.value.detail["code"] == "agent_disabled"
    monkeypatch.setattr(chat_research, "get_profile", lambda agent: original)
    def deny(*args):
        raise HTTPException(403, {"code": "capability_not_allowed"})
    monkeypatch.setattr(chat_research, "require_capability", deny)
    with pytest.raises(HTTPException) as error:
        plan()
    assert error.value.detail["code"] == "capability_not_allowed"


def test_configured_alternative_model_keeps_one_native_action(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_NAME", "fixture-priced-model")
    monkeypatch.setenv("OPENAI_PRICING_MODEL", "fixture-priced-model")
    monkeypatch.setenv("OPENAI_INPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("OPENAI_OUTPUT_USD_PER_MILLION", "10")
    prepared = plan()
    assert prepared["max_native_actions"] == prepared["max_web_search_calls"] == 1
    assert prepared["research_input_usd_per_million"] == "2.50"


@pytest.mark.parametrize("native,code", [
    ([], "native_search_not_used"),
    ([native_call(), native_call(), native_call()], "search_call_limit_exceeded"),
])
def test_missing_search_or_over_budget_provider_response_is_not_used_or_retried(monkeypatch, native, code):
    calls = fake_response(monkeypatch, native=native)
    with pytest.raises(HTTPException) as error:
        chat_research.discover_chat_sources(plan())
    assert error.value.detail["code"] == code and len(calls) == 1


def test_provider_failure_keeps_specific_code_and_never_retries(monkeypatch):
    calls, stages = [], []
    def fail(*args, **kwargs):
        calls.append(1)
        raise HTTPException(429, {"code": "openai_rate_limited", "retry_after_seconds": 60})
    monkeypatch.setattr(research, "_call_response", fail)
    with pytest.raises(HTTPException) as error:
        chat_research.discover_chat_sources(plan(), on_stage=stages.append)
    assert error.value.detail == {"code": "openai_rate_limited", "retry_after_seconds": 60}
    assert calls == [1] and stages == ["searching"]


def test_unreported_usage_retains_conservative_cost_uncertainty(monkeypatch):
    fake_response(monkeypatch, usage=False)
    monkeypatch.setattr(research, "_download_page", lambda url, allowed: page_record(URL, TITLE, published=None))
    result = chat_research.discover_chat_sources(plan())
    assert result["sources"] and not result["cost_tracking_complete"]
    assert result["estimated_cost_usd"] is None and result["usage"]["usage_reported"] is False


def test_arabic_limitations_describe_evidence_without_internal_accounting_terms(monkeypatch):
    fake_response(monkeypatch)
    monkeypatch.setattr(research, "_download_page", lambda url, allowed: page_record(URL, TITLE, published=None))
    result = chat_research.discover_chat_sources(plan(language="ar"))
    assert result["limitations"] == ["بعض المراجع بلا تاريخ نشر موثّق؛ وقت الاطلاع عليها لا يجعل معلوماتها حديثة."]
    assert result["sources"][0]["published_at_dubai"] == "غير مؤرخ"


def test_large_multilingual_evidence_is_bounded_before_answer_payload():
    sources = [{"id": f"source-{number}", "url": f"https://original-study.example/{number}",
        "excerpt": "نص موثّق طويل " * 1000, "description": "وصف موثّق " * 500,
        "published_at": None, "publisher_text_verified": True} for number in range(8)]
    original = deepcopy(sources)
    bounded, limited = chat_research._bounded_sources(sources)
    assert limited and 0 < len(bounded) <= 8
    assert runtime._json_bytes(bounded) <= chat_research.MAX_SOURCE_EVIDENCE_BYTES
    assert sources == original
    for source in bounded:
        assert source["publisher_text_verified"] is True and len(source["excerpt"]) <= 800


@pytest.mark.parametrize("context", [{"shared_memory": "private"}, "x" * 2001])
def test_discovery_rejects_unbounded_or_structured_private_context(context):
    with pytest.raises(HTTPException) as error:
        plan(topic_context=context)
    assert error.value.detail["code"] == "chat_research_context_invalid"
