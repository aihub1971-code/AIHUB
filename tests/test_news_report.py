"""Production NewsData report contract, using wholly synthetic offline data."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest
from fastapi import HTTPException

from app import news_feed, news_report, newsdata_translation, research, runtime
from app.agent_profiles import get_profile
from app.storage import StoreError


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
WINDOW = research._window(cutoff=NOW)


def snapshot(count=1, *, language="en", **overrides):
    items, sources = [], []
    for number in range(count):
        title = f"Synthetic Iran nuclear talks test number {number}" if language == "en" else f"خبر اصطناعي عن مفاوضات إيران النووية رقم {number}"
        description = "Fictional description supplied solely for this automated test." if language == "en" else "وصف اصطناعي أنشئ للاختبار ولا يمثل خبراً حقيقياً."
        item = {"id": f"newsdata:fixture-{number}", "article_id": f"fixture-{number}",
            "headline": title, "summary": description, "original_headline": title, "original_summary": description,
            "original_language": language, "language": language,
            "headline_en": title if language == "en" else None, "summary_en": description if language == "en" else None,
            "headline_ar": title if language == "ar" else None, "summary_ar": description if language == "ar" else None,
            "translation_status": "original", "publisher": "Synthetic Publisher", "url": f"https://fixture.example/story/{number}",
            "published_at": "2026-10-20T09:00:00+00:00", "source_ids": [f"newsdata:fixture-{number}"],
            "source_kind": "article", "section": "conflict_diplomacy", "classification": "within_window",
            "publisher_text_verified": False,
            "publication_verification": {"method": "newsdata_pubDate_pubDateTZ", "verified": False, "provider_metadata_validated": True}}
        items.append(item)
        sources.append({"id": item["id"], "title": title, "publisher": item["publisher"], "url": item["url"],
            "published_at": item["published_at"], "excerpt": description, "description": description,
            "original_language": language, "role": "fresh", "source_kind": "article",
            "publication_verification": item["publication_verification"], "publisher_text_verified": False})
    return {"status": "fresh" if count else "empty", "items": items, "sources": sources,
        "actual_count": count, "snapshot_id": "synthetic-snapshot", "research_window": WINDOW,
        "requested_research_window": WINDOW, "provider_plan": {"configured_plan": "unknown", "plan_verified": False},
        "freshness": {"window_start": WINDOW["start"], "window_end": WINDOW["end"], "snapshot_window": WINDOW,
            "coverage_complete": False, "publisher_text_verified": False},
        "pagination": {"requests": 5 if count == 50 else 1, "stop_reason": "article_limit" if count == 50 else "provider_end"},
        "excluded": {}, "limitation_codes": ["provider_metadata_only"], **overrides}


def analysis_response(profile, ids, **changes):
    value = {"title": "Synthetic metadata assessment",
        "sections": [{"id": section["id"], "body": "This is an attributed synthetic fixture, with limited evidence.",
                      "source_ids": ids[:2]} for section in profile["sections"]], "limitations": []}
    value.update(changes)
    return value


def model_response(content, *, known=True):
    return {"content": json.dumps(content, ensure_ascii=False),
        "usage": runtime._normalise_usage({"input_tokens": 500, "output_tokens": 200} if known else None),
        "tier_matches": True, "usage_bound_matches": known}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-only-not-a-real-key")
    monkeypatch.setenv("NEWSDATA_API_KEY", "synthetic-provider-only")


def mock_feed(monkeypatch, value):
    calls = []
    def retrieve(agent_id, topic, window, language, progress=None):
        calls.append((agent_id, topic, window, language))
        if progress:
            progress("searching")
        return deepcopy(value)
    monkeypatch.setattr(news_feed, "get_report_feed", retrieve)
    return calls


def test_estimate_reserves_analysis_and_ten_translation_batches_with_zero_search(configured):
    estimate = news_report.estimate_reservation("iran-news", language="ar", cutoff=NOW)
    assert estimate["max_model_calls"] == 11
    assert estimate["translation"]["max_model_calls"] == 10
    assert estimate["max_web_search_calls"] == estimate["max_native_actions"] == estimate["max_publisher_pages"] == 0
    assert estimate["max_feed_page_requests"] == news_feed.MAX_PAGES
    assert estimate["newsdata_credit_accounting"] == "shared_feed_page_ledger"
    translation = estimate["translation"]
    expected = (Decimal(news_report.MAX_ANALYSIS_INPUT_BYTES + runtime.PROVIDER_INPUT_OVERHEAD_TOKENS) * Decimal("2.5")
        + Decimal(news_report.MAX_ANALYSIS_OUTPUT_TOKENS) * Decimal("10")) / Decimal(1000000)
    assert Decimal(str(estimate["estimated_cost_usd"])) >= expected + Decimal(str(translation["estimated_cost_usd"]))
    assert estimate["research_window"] == WINDOW


def test_positive_fifty_article_inventory_and_sources_survive_analysis(configured, monkeypatch):
    value = snapshot(50)
    feed_calls = mock_feed(monkeypatch, value)
    model_calls = []
    def call(key, payload):
        model_calls.append(payload)
        assert payload["tools"] == [] and payload["tool_choice"] == "none"
        assert payload["store"] is False
        data = json.loads(payload["input"][1]["content"][0]["text"])
        assert data["factual_basis"] == "newsdata_structured_metadata"
        assert data["publisher_text_verified"] is False
        assert len(data["sources"]) == 50 and data["article_inventory_count"] == 50
        assert value["sources"][0]["url"] not in json.dumps(payload)
        return model_response(analysis_response(get_profile("iran-news"), [item["id"] for item in data["sources"]]))
    monkeypatch.setattr(runtime, "_call_model", call)
    stages = []
    report = news_report.generate_report("iran-news", language="en", cutoff=NOW, progress=stages.append)
    assert len(feed_calls) == len(model_calls) == 1
    assert stages == ["searching", "checking_sources", "analyzing"]
    assert report["status"] == "complete" and report["actual_article_count"] == report["analyzed_article_count"] == 50
    assert report["requested_article_target"] == 50
    assert len(report["items"]) == len(report["sources"]) == len(report["citation_mappings"]) == 50
    assert {item["url"] for item in report["items"]} == {item["url"] for item in value["items"]}
    assert report["factual_basis"] == "newsdata_structured_metadata" and not report["publisher_text_verified"]
    assert report["source_coverage"]["verified_source_count"] == 0
    assert report["usage"]["web_search_calls"] == report["usage"]["native_actions"] == 0
    assert report["cost_tracking_complete"] and report["estimated_cost_usd"] == pytest.approx(.00325)
    assert "Verified news" not in report["response"]
    assert all("why_it_matters" in item for item in report["items"])


def test_empty_delayed_provider_report_is_complete_zero_usage_without_model(configured, monkeypatch):
    value = snapshot(0, suspected_provider_delay=True, observed_latest_provider_publication_at="2026-10-19T23:59:50+00:00",
        provider_age_seconds=43210, limitation_codes=["provider_delay"])
    mock_feed(monkeypatch, value)
    monkeypatch.setattr(runtime, "_call_model", lambda *_: pytest.fail("An empty feed must not run any model."))
    report = news_report.generate_report("iran-news", language="en", cutoff=NOW)
    assert report["status"] == "complete" and report["items"] == report["sources"] == []
    assert report["actual_article_count"] == report["analyzed_article_count"] == 0
    assert report["analysis_status"] == "no_articles"
    assert report["estimated_cost_usd"] == 0 and report["cost_tracking_complete"]
    assert report["usage"]["total_tokens"] == 0 and report["usage_stages"] == []
    assert report["news_feed"]["suspected_provider_delay"]
    assert any("Real-time provider access is needed" in text for text in report["limitations"])
    assert any("account tier has not been confirmed" in text for text in report["limitations"])
    assert "No qualifying articles" in report["title"]


def test_provider_failure_is_not_converted_to_successful_empty_report(configured, monkeypatch):
    def failed(*args, **kwargs):
        raise StoreError("newsdata_rate_limited", "Synthetic safe rate limit", 429)
    monkeypatch.setattr(news_feed, "get_report_feed", failed)
    monkeypatch.setattr(runtime, "_call_model", lambda *_: pytest.fail("No model after provider failure."))
    with pytest.raises(StoreError) as error:
        news_report.generate_report("iran-news", language="en", cutoff=NOW)
    assert error.value.code == "newsdata_rate_limited"


@pytest.mark.parametrize("language,original", [("ar", "en"), ("en", "ar")])
def test_translation_batches_are_budgeted_in_both_directions_and_cached(configured, monkeypatch, language, original):
    value = snapshot(11, language=original)
    mock_feed(monkeypatch, value)
    calls, cached = [], []
    def call(key, payload):
        calls.append(payload)
        schema_name = payload["text"]["format"]["name"]
        if schema_name == "ai_hub_newsdata_assessment":
            return model_response(analysis_response(get_profile("iran-news"), [item["id"] for item in value["sources"]]))
        text = payload["input"][1]["content"][0]["text"]
        data = json.loads(text.split("<UNTRUSTED_ARTICLE_FIELDS>\n")[1].split("\n</UNTRUSTED_ARTICLE_FIELDS>")[0])
        assert 1 <= len(data) <= 5
        assert all(set(item) == {"article_id", "headline_" + original, "summary_" + original} for item in data)
        assert payload["tools"] == [] and payload["tool_choice"] == "none"
        translated = [{"article_id": item["article_id"], "headline_" + language: "عنوان اصطناعي مترجم" if language == "ar" else "Synthetic translated heading",
            "summary_" + language: "وصف اصطناعي مترجم" if language == "ar" else "Synthetic translated description"} for item in data]
        return model_response({"translations": translated})
    monkeypatch.setattr(runtime, "_call_model", call)
    monkeypatch.setattr(news_feed, "apply_translations", lambda agent_id, items, lang: cached.append((agent_id, deepcopy(items), lang)))
    report = news_report.generate_report("iran-news", language=language, cutoff=NOW)
    assert len(calls) == 4 and len(cached) == 1 and len(cached[0][1]) == 11
    assert report["translation"]["batch_count"] == 3 and report["translation"]["translated_item_count"] == 11
    assert all(item["language"] == language and item["translation_status"] == "translated" for item in report["items"])
    assert all(item["original_language"] == original for item in report["items"])
    assert report["cost_tracking_complete"] and report["estimated_cost_usd"] == pytest.approx(.013)
    assert [item["url"] for item in report["items"]] == [item["url"] for item in value["items"]]


def test_failed_translation_preserves_originals_and_unknown_usage_retains_allowance(configured, monkeypatch):
    value = snapshot(1)
    mock_feed(monkeypatch, value)
    calls = []
    def call(key, payload):
        calls.append(payload)
        if len(calls) == 1:
            return model_response(analysis_response(get_profile("iran-news"), [value["sources"][0]["id"]]))
        raise HTTPException(504, detail={"code": "openai_timeout"})
    monkeypatch.setattr(runtime, "_call_model", call)
    report = news_report.generate_report("iran-news", language="ar", cutoff=NOW)
    assert len(calls) == 2 and report["actual_article_count"] == 1
    assert report["items"][0]["headline"] == value["items"][0]["headline"]
    assert report["items"][0]["translation_status"] == "original_only"
    assert not report["cost_tracking_complete"] and report["estimated_cost_usd"] is None and report["billing_uncertain"]
    assert report["translation"]["failed_batch_count"] == 1


def test_cached_translations_are_reused_without_retranslation(configured, monkeypatch):
    value = snapshot(2)
    for item in value["items"]:
        item.update(headline="عنوان مخزن", summary="وصف مخزن", language="ar", translation_status="translated")
    mock_feed(monkeypatch, value)
    calls = []
    def call(key, payload):
        calls.append(payload)
        assert payload["text"]["format"]["name"] == "ai_hub_newsdata_assessment"
        return model_response(analysis_response(get_profile("iran-news"), [item["id"] for item in value["sources"]]))
    monkeypatch.setattr(runtime, "_call_model", call)
    report = news_report.generate_report("iran-news", language="ar", cutoff=NOW)
    assert len(calls) == 1 and all(item["headline"] == "عنوان مخزن" for item in report["items"])


def test_articles_without_required_analysis_configuration_fail_report(monkeypatch):
    mock_feed(monkeypatch, snapshot(3))
    monkeypatch.setattr(runtime, "_call_model", lambda *_: pytest.fail("No configured model."))
    with pytest.raises(HTTPException) as error:
        news_report.generate_report("iran-news", language="en", cutoff=NOW)
    assert error.value.detail["code"] == "openai_not_configured"


def test_empty_feed_without_analysis_configuration_still_produces_truthful_zero_report(monkeypatch):
    mock_feed(monkeypatch, snapshot(0))
    monkeypatch.setattr(runtime, "_call_model", lambda *_: pytest.fail("Empty report must not call OpenAI."))
    report = news_report.generate_report("iran-news", language="en", cutoff=NOW)
    assert report["status"] == "complete" and report["actual_article_count"] == 0
    assert report["estimated_cost_usd"] == 0 and report["usage"]["total_tokens"] == 0


def test_large_arabic_metadata_is_compressed_under_input_bound_without_dropping_inventory(configured):
    value = snapshot(50, language="ar")
    for item in value["sources"]:
        item["title"] = "عنوان اصطناعي عن إيران " * 23
        item["excerpt"] = "وصف اصطناعي مطول يوضح حدود المعلومات " * 60
    payload, context = news_report._analysis_payload(runtime.DEFAULT_OPENAI_MODEL, get_profile("iran-news"),
        "موضوع اختبار", "ar", value["sources"], WINDOW, None)
    assert runtime._json_bytes(payload) <= news_report.MAX_ANALYSIS_INPUT_BYTES
    assert context["analyzed_article_count"] == 50
    assert context["metadata_compressed"]
    assert len(value["sources"]) == len(value["items"]) == 50


@pytest.mark.parametrize("padding", ["عنوان اصطناعي عن إيران ", "😀"], ids=["long_arabic", "four_byte_utf8"])
def test_maximum_length_fifty_article_report_fits_saved_and_pdf_input_bounds(configured, monkeypatch, padding):
    from app import report_pdf
    from app.report_store import MAX_REPORT_BYTES, ReportStore

    value = snapshot(50, language="ar")
    for index, (item, source) in enumerate(zip(value["items"], value["sources"])):
        title, description, publisher = (padding * 500)[:500], (padding * 2000)[:2000], (padding * 200)[:200]
        url = "https://fixture.example/" + str(index) + "/" + "a" * 1500
        item.update(headline=title, original_headline=title, headline_ar=title,
            summary=description, original_summary=description, summary_ar=description, publisher=publisher, url=url)
        source.update(title=title, excerpt=description, description=description, publisher=publisher, url=url)
    mock_feed(monkeypatch, value)
    def call(key, payload):
        data = json.loads(payload["input"][1]["content"][0]["text"])
        return model_response(analysis_response(get_profile("iran-news"), [item["id"] for item in data["sources"]]))
    monkeypatch.setattr(runtime, "_call_model", call)
    report = news_report.generate_report("iran-news", language="ar", cutoff=NOW)
    job = {"agent_id": "iran-news", "language": "ar", "job_id": "job_fixture", "topic": report["topic"],
        "generation_seq": 1, "window_start": "2026-10-20T00:00:00.000000+00:00", "window_end": "2026-10-20T12:00:00.000000+00:00"}
    prepared = ReportStore.prepare_result(job, "rpt_fixture", report)
    saved_bytes = len(json.dumps(prepared, ensure_ascii=False, separators=(",", ":")).encode())
    pdf_input_bytes = len(json.dumps(prepared, ensure_ascii=False).encode())
    assert saved_bytes <= MAX_REPORT_BYTES and pdf_input_bytes <= report_pdf.MAX_REPORT_BYTES
    assert len(prepared["items"]) == len(prepared["sources"]) == len(prepared["citation_mappings"]) == 50
    assert {item["url"] for item in prepared["items"]} == {item["url"] for item in value["items"]}
    assert all("original_summary" not in item and "summary_ar" not in item and "headline_en" not in item for item in prepared["items"])
    assert all("description" not in source and len(source["excerpt"]) == 2000 for source in prepared["sources"])
    assert "fixture.example/49/" in prepared["response"]


@pytest.mark.parametrize("invalid", ["unknown_source", "missing_section", "duplicate_section", "url", "extra_root"])
def test_analysis_contract_rejects_unsourced_or_invalid_prose(invalid):
    profile = get_profile("iran-news")
    value = analysis_response(profile, ["newsdata:fixture-0"])
    if invalid == "unknown_source":
        value["sections"][0]["source_ids"] = ["invented"]
    elif invalid == "missing_section":
        value["sections"].pop()
    elif invalid == "duplicate_section":
        value["sections"][-1] = deepcopy(value["sections"][0])
    elif invalid == "url":
        value["sections"][0]["body"] = "Visit https://unapproved.example/"
    else:
        value["articles"] = []
    with pytest.raises(HTTPException) as error:
        news_report._validated_analysis(json.dumps(value), profile, ["newsdata:fixture-0"])
    assert error.value.detail["code"] == "invalid_news_analysis"


def test_hybrid_reservation_covers_both_paths_before_any_fetch(configured):
    for agent in ("iran-news", "geopolitical-morning", "uae-eu-trade-talks"):
        native = research._estimate_native_reservation(agent, language="en", cutoff=NOW)
        metadata = news_report.estimate_reservation(agent, language="en", cutoff=NOW)
        combined = research.estimate_reservation(agent, language="en", cutoff=NOW)
        assert combined["max_model_calls"] == native["max_model_calls"] + metadata["max_model_calls"]
        assert combined["max_web_search_calls"] == native["max_web_search_calls"]
        assert combined["estimated_cost_usd"] >= native["estimated_cost_usd"] + metadata["estimated_cost_usd"] - .000001
        assert combined["retrieval_options"] == ["newsdata", "web_search"]
    assert research.estimate_reservation("historical-reading-companion", cutoff=NOW) == research._estimate_native_reservation("historical-reading-companion", cutoff=NOW)
