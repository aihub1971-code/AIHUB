"""Task-aware provider selection, dated background and source provenance."""
from copy import deepcopy
import json

import httpx
import pytest
from fastapi import HTTPException

from app import news_feed, news_report, research
from app.agent_profiles import get_profile
from app.storage import StoreError
from tests.test_news_report import snapshot, NOW
from tests.test_research import (WINDOW, page_record, source_record, native_call,
    provider_response, structured_pack, install_transport)


def native_result():
    return {"items": [], "sources": [], "limitations": [], "title": "Synthetic evidence", "language": "en",
        "agent_name": "Synthetic agent", "research_window": research._window(WINDOW), "sections": [],
        "citation_mappings": {}, "visuals": {}}


@pytest.mark.parametrize("case,expected", [
    ("empty", "newsdata_empty"), ("partial", "newsdata_insufficient"),
    ("unavailable", "newsdata_unavailable"), ("one_publisher", "newsdata_insufficient"),
    ("custom_topic", "task_specific_research"),
])
def test_newsdata_shortfalls_and_specific_tasks_use_reserved_web_path(monkeypatch, case, expected):
    feed = snapshot(0 if case == "empty" else 3)
    if case == "partial":
        feed["partial"] = True
    calls = []
    def fetch(*args, **kwargs):
        calls.append("newsdata")
        if case == "unavailable":
            raise StoreError("newsdata_daily_credit_limit", "Application daily NewsData allowance used", 429)
        return feed
    def native(*args, **kwargs):
        calls.append("web")
        assert kwargs["seed_sources"] == ([] if case == "unavailable" else feed["sources"])
        return native_result()
    monkeypatch.setattr(news_feed, "get_report_feed", fetch)
    monkeypatch.setattr(research, "_generate_native_report", native)
    monkeypatch.setattr(news_report, "generate_report", lambda *a, **k: pytest.fail("No metadata-only branch"))
    result = research.generate_report("iran-news", "A specific requested issue" if case == "custom_topic" else "",
        "en", cutoff=NOW)
    assert calls == ["newsdata", "web"]
    assert result["retrieval"]["selection_reason"] == expected
    assert result["retrieval"]["providers_attempted"] == ["newsdata", "web_search"]


def test_sufficient_feed_uses_single_preloaded_inventory_without_second_fetch(monkeypatch):
    feed = snapshot(3)
    feed["sources"][1]["publisher"] = "Independent second publisher"
    calls = []
    monkeypatch.setattr(news_feed, "get_report_feed", lambda *a, **k: calls.append("feed") or feed)
    def metadata(*args, **kwargs):
        calls.append("assessment")
        assert kwargs["preloaded_feed"] is feed
        return native_result()
    monkeypatch.setattr(news_report, "generate_report", metadata)
    monkeypatch.setattr(research, "_generate_native_report", lambda *a, **k: pytest.fail("Sufficient default feed needs no web call"))
    result = research.generate_report("iran-news", language="en", cutoff=NOW)
    assert calls == ["feed", "assessment"]
    assert result["retrieval"]["selection_reason"] == "sufficient_current_inventory"


@pytest.mark.parametrize("agent", ["ubi-research", "education-observatory", "uae-eu-trade-talks"])
def test_policy_agents_supplement_news_with_original_documents(monkeypatch, agent):
    feed = snapshot(3)
    feed["sources"][1]["publisher"] = "Independent second publisher"
    monkeypatch.setattr(news_feed, "get_report_feed", lambda *a, **k: feed)
    monkeypatch.setattr(research, "_generate_native_report", lambda *a, **k: native_result())
    result = research.generate_report(agent, language="en", cutoff=NOW)
    assert result["retrieval"]["selection_reason"] == "primary_evidence_required"
    assert get_profile(agent)["preferred_domains"]


def test_web_failure_retains_usable_feed_and_uncertain_charge(monkeypatch):
    feed = snapshot(1)
    monkeypatch.setattr(news_feed, "get_report_feed", lambda *a, **k: feed)
    def fail(*args, **kwargs):
        raise HTTPException(504, detail={"code": "openai_timeout"})
    monkeypatch.setattr(research, "_generate_native_report", fail)
    result = native_result()
    result.update(actual_article_count=1, analyzed_article_count=1)
    monkeypatch.setattr(news_report, "generate_report", lambda *a, **k: result)
    report = research.generate_report("iran-news", language="en", cutoff=NOW)
    assert report["retrieval"]["web_search_error"]["code"] == "openai_timeout"
    assert report["cost_tracking_complete"] is False and report["estimated_cost_usd"] is None
    assert report["billing_uncertain"] is True
    assert any("metadata only" in value for value in report["limitations"])


@pytest.mark.parametrize("language", ["en", "ar"])
def test_dated_background_survives_complete_native_pipeline(monkeypatch, language):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    url = "https://policy.trade.ec.europa.eu/uae-trade-negotiations"
    title = "European Union and United Arab Emirates trade negotiations: official background"
    page = page_record(url=url, title=title, published="2025-05-28", article_type="Report",
        body="The European Union and the United Arab Emirates discussed trade negotiations in this dated official document. It records a negotiating stage, not implementation or a new development in 2026.")
    monkeypatch.setattr(research, "_download_page", lambda *a: page)
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["tools"]:
            return httpx.Response(200, json=provider_response("Observed official background.", native=[native_call(url=url)],
                annotations=[{"type": "url_citation", "url": url, "title": title, "start_index": 0, "end_index": 9}]))
        supplied = json.loads(payload["input"][1]["content"])["sources"]
        assert len(supplied) == 1 and supplied[0]["role"] == "background"
        assert supplied[0]["published_at"] == "2025-05-28"
        assert supplied[0]["source_category"] == "official_statement"
        pack = structured_pack(get_profile("uae-eu-trade-talks"), supplied, items=False)
        pack["sections"][0]["body"] = ("خلفية منشورة في 28 مايو 2025: لا تثبت هذه الوثيقة حدوث تقدم جديد. يفصل التحليل بين التفاوض والتنفيذ الفعلي."
            if language == "ar" else "Background published on 28 May 2025: this document does not establish fresh progress. The analysis distinguishes negotiation from implementation.")
        return httpx.Response(200, json=provider_response(json.dumps(pack, ensure_ascii=False)))
    install_transport(monkeypatch, handler)
    report = research._generate_native_report("uae-eu-trade-talks", language=language, research_window=WINDOW)
    assert report["status"] == "background_only" and report["items"] == []
    assert report["fresh_item_count"] == 0 and len(report["sections"]) > 3
    assert "2025" in report["sections"][0]["body"]
    assert report["sources"][0]["published_at"] == "2025-05-28"
    assert report["sources"][0]["publication_verification"]["precision"] == "day"
    assert report["source_coverage"]["background_source_count"] == 1
    assert report["source_coverage"]["fresh_source_count"] == 0
    assert "Synthetic fixtures are used for this test." in report["limitations"]
    assert len(requests) == 3  # Initial discovery, planned focused discovery, synthesis.
    assert "UTC+04" in report["response"]


def test_uae_eu_aliases_and_official_claims_are_preserved():
    profile = get_profile("uae-eu-trade-talks")
    assert research._relevant(profile, "Abu Dhabi and Brussels discuss free trade", "", "", "trade")
    assert not research._relevant(profile, "Brussels announces domestic housing grants", "", "", "trade")
    page = page_record(url="https://www.moet.gov.ae/trade-eu", title="Abu Dhabi and Brussels discuss free trade",
        published="2026-10-20T10:00:00Z", article_type="PressRelease")
    source, reason = research._verify_page(source_record(page["url"], page["url"]), page, profile, "trade", research._window(WINDOW))
    assert reason is None and source["source_category"] == "official_statement"
    assert source["publisher_text_verified"] and source["role"] == "fresh"


def test_dubai_format_keeps_calendar_dates_without_inventing_a_clock():
    assert research._dubai_time("2026-10-20T10:00:00Z") == "20 Oct 2026, 14:00 (UTC+04)"
    assert research._dubai_time("2025-05-28") == "2025-05-28"
    assert research._dubai_time(None, "ar") == "غير مؤرخ"


def test_fresh_seeds_are_not_crowded_out_by_forty_background_documents():
    profile = get_profile("education-observatory")
    backgrounds = [{"id": f"background_{number}", "title": f"Evaluated education programme {number}",
        "publisher": "Research institute", "published_at": "2020-05-28", "role": "background",
        "source_kind": "reference", "excerpt": "A dated evaluation reports observed educational outcomes.",
        "publisher_text_verified": True, "source_category": "research"} for number in range(40)]
    fresh = snapshot(1)["sources"][0]
    _, selected = research._synthesis_payload("gpt-6.1-sol", profile, "education", "en", research._window(WINDOW),
        [*backgrounds, fresh], None)
    assert len(selected) == 40 and selected[0]["id"] == fresh["id"]
    assert any(source["role"] == "background" for source in selected)
    many_fresh = [dict(fresh, id=f"fresh_{number}") for number in range(50)]
    _, selected = research._synthesis_payload("gpt-6.1-sol", profile, "education", "en", research._window(WINDOW),
        [*many_fresh, backgrounds[0]], None)
    assert backgrounds[0] in selected and any(source["role"] == "fresh" for source in selected)


def test_cached_web_evidence_keeps_provenance_without_a_second_search(monkeypatch):
    from tests.test_news_report import model_response, analysis_response
    from app import runtime
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    feed = snapshot(3, cached=True)
    feed["sources"][1]["publisher"] = "Second inspected publisher"
    for source in feed["sources"]:
        source.update(publisher_text_verified=True, evidence_level="publisher_page_verified",
            source_category="official_statement", factual_basis="publisher_page", content_sha256="a" * 64,
            retrieved_at="2026-10-20T12:01:00Z", citation_annotations=[{"type": "url_citation", "url": source["url"]}])
    monkeypatch.setattr(news_feed, "get_report_feed", lambda *a, **k: feed)
    monkeypatch.setattr(research, "_generate_native_report", lambda *a, **k: pytest.fail("Cached sufficient evidence needs no fresh search"))
    def assess(key, payload):
        data = json.loads(payload["input"][1]["content"][0]["text"])
        assert data["factual_basis"] == "source_specific_evidence_levels" and data["publisher_text_verified"]
        assert all(source["source_category"] == "official_statement" for source in data["sources"])
        return model_response(analysis_response(get_profile("iran-news"), [source["id"] for source in data["sources"]]))
    monkeypatch.setattr(runtime, "_call_model", assess)
    report = research.generate_report("iran-news", language="en", cutoff=NOW)
    assert report["retrieval"]["providers_attempted"] == []
    assert report["retrieval"]["cached_evidence_providers"] == ["web_search"]
    assert report["source_coverage"]["verified_source_count"] == 3
    assert report["factual_basis"] == "publisher_page"
    assert report["sources"][0]["content_sha256"] == "a" * 64
    assert report["sources"][0]["citation_annotations"]
    assert not any("Publisher pages were not independently checked" in value for value in report["limitations"])


def test_rejected_fresh_items_are_not_relabelled_background(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    title = "A new evaluated education programme publishes learning outcomes"
    page = page_record(title=title)
    monkeypatch.setattr(research, "_download_page", lambda *a: page)
    def handler(request):
        payload = json.loads(request.content)
        if payload["tools"]:
            return httpx.Response(200, json=provider_response("Education evaluation.", native=[native_call()],
                annotations=[{"type": "url_citation", "url": page["url"], "title": title, "start_index": 0, "end_index": 9}]))
        supplied = json.loads(payload["input"][1]["content"])["sources"]
        pack = structured_pack(get_profile("education-observatory"), supplied)
        pack["items"][0]["evidence_quote"] = "Invented quotation not present in the evidence."
        pack["sections"][0]["body"] = "Unvalidated fresh claim that must not survive as background."
        return httpx.Response(200, json=provider_response(json.dumps(pack)))
    install_transport(monkeypatch, handler)
    report = research._generate_native_report("education-observatory", language="en", research_window=WINDOW)
    assert report["status"] == "no_evidence" and report["items"] == []
    assert "Unvalidated fresh claim" not in report["response"]


def mixed_education_report(monkeypatch, language, *, item_state="rejected", section_evidence="background"):
    """Real discovery/validation/synthesis flow with synthetic publisher and API transport."""
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    profile = get_profile("education-observatory")
    background_url = "https://education-research.example/teacher-evaluation-2025"
    background_title = "Teacher development evaluation: learning outcomes measured in 2025"
    background_page = page_record(url=background_url, title=background_title, published="2025-03-10",
        article_type="Report", body="The 2025 teacher development evaluation compared learning outcomes and teacher workload. "
            "Its dated findings do not establish a new policy announcement or universal effects in other school systems.")
    monkeypatch.setattr(research, "_download_page", lambda *args: background_page)
    seed = dict(snapshot(1)["sources"][0], title="New education technology programme announced",
        url="https://news-fixture.example/fresh-education-programme",
        excerpt="An education ministry announced a school technology programme. Learning gains were not evaluated.",
        description="An education ministry announced a school technology programme. Learning gains were not evaluated.")
    requests, supplied_packs = [], []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["tools"]:
            return httpx.Response(200, json=provider_response("Dated education evaluation.",
                native=[native_call(url=background_url)], annotations=[{"type": "url_citation", "url": background_url,
                    "title": background_title, "start_index": 0, "end_index": 9}]))
        supplied = json.loads(payload["input"][1]["content"])["sources"]
        fresh = next(source for source in supplied if source["role"] == "fresh")
        background = next(source for source in supplied if source["role"] == "background")
        pack = structured_pack(profile, [fresh], items=item_state != "omitted")
        pack["title"] = "تحسن جديد في نتائج المدارس أُعلن اليوم" if language == "ar" else "New school gains announced today"
        pack["limitations"] = ["ادعاء حديث غير معتمد داخل حدود التغطية" if language == "ar"
                               else "Unaccepted fresh claim repeated in model limitations"]
        if item_state == "rejected":
            pack["items"][0]["evidence_quote"] = "Invented quotation absent from the supplied education evidence."
        for section in pack["sections"]:
            section["source_ids"] = [background["id"]]
            section["body"] = ("بحث منشور في 10 مارس 2025 يقارن نتائج التعلم وعبء عمل المعلمين. تتطلب الاستفادة منه في الإمارات "
                "مقارنة ظروف التطبيق وتصميم التقييم؛ ولا تثبت هذه الخلفية وقوع تطور جديد."
                if language == "ar" else "Research published on 10 March 2025 compares learning outcomes and teacher workload. "
                    "UAE adaptation requires comparing implementation conditions and evaluation design; this background does not establish a new development.")
        unsafe = "ادعاء حديث غير معتمد عن مكاسب المدارس" if language == "ar" else "Unaccepted fresh claim about school gains"
        if section_evidence == "mixed":
            pack["sections"][0].update(source_ids=[fresh["id"]], body=unsafe)
            pack["sections"][1].update(source_ids=[background["id"], fresh["id"]], body=unsafe)
        elif section_evidence == "fresh":
            for section in pack["sections"]:
                section.update(source_ids=[fresh["id"]], body=unsafe)
        supplied_packs.append((supplied, deepcopy(pack)))
        return httpx.Response(200, json=provider_response(json.dumps(pack, ensure_ascii=False)))
    install_transport(monkeypatch, handler)
    report = research._generate_native_report("education-observatory", language=language,
        research_window=WINDOW, seed_sources=[seed])
    return report, profile, seed, requests, supplied_packs[0]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_rejected_fresh_candidate_preserves_independently_cited_background_analysis(monkeypatch, language):
    report, profile, seed, requests, (supplied, original) = mixed_education_report(monkeypatch, language)
    background = next(source for source in supplied if source["role"] == "background")
    assert report["status"] == "background_only" and report["items"] == []
    assert report["verification_diagnostics"]["excluded_items"] == {"unsupported_evidence_quote": 1}
    assert report["verification_diagnostics"]["withheld_sections"] == []
    assert [section["id"] for section in report["sections"]] == [section["id"] for section in profile["sections"]]
    assert all(section["source_ids"] == [background["id"]] and "2025" in section["body"] for section in report["sections"])
    assert report["title"] == ("خلفية وتحليل: " + profile["name_ar"] if language == "ar" else "Background and analysis: " + profile["name"])
    assert original["title"] not in report["response"]
    assert original["limitations"][0] not in report["response"]
    assert [source["id"] for source in report["sources"]] == [background["id"]]
    assert set(report["citation_mappings"]) == {background["id"]}
    assert seed["role"] == "fresh" and seed["id"] not in report["citation_mappings"]
    assert seed["url"] not in report["response"]
    assert report["source_coverage"]["fresh_source_count"] == 0
    assert report["source_coverage"]["background_source_count"] == report["source_coverage"]["verified_source_count"] == 1
    assert report["factual_basis"] == "publisher_page"
    assert report["cost_tracking_complete"] is True
    assert len(requests) == 3 and sum(not request["tools"] for request in requests) == 1
    assert "even when unused fresh candidates appear in the source pack" in requests[-1]["input"][0]["content"]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_unselected_fresh_candidate_withholds_only_fresh_dependent_sections(monkeypatch, language):
    report, profile, seed, requests, (supplied, original) = mixed_education_report(monkeypatch, language,
        item_state="omitted", section_evidence="mixed")
    assert report["status"] == "background_only" and report["items"] == []
    assert report["verification_diagnostics"]["excluded_items"] == {}
    withheld = [section["id"] for section in profile["sections"][:2]]
    assert report["verification_diagnostics"]["withheld_sections"] == withheld
    assert [section["id"] for section in report["sections"]] == [section["id"] for section in profile["sections"][2:]]
    assert all(section["source_ids"] and seed["id"] not in section["source_ids"] for section in report["sections"])
    assert "Unaccepted fresh claim" not in report["response"] and "ادعاء حديث غير معتمد" not in report["response"]
    expected_prefix = "حُجبت الأقسام التالية" if language == "ar" else "The following sections could not be supported"
    limitation = next(value for value in report["limitations"] if value.startswith(expected_prefix))
    assert all(section["title_ar" if language == "ar" else "title_en"] in limitation for section in profile["sections"][:2])
    assert report["title"] == ("خلفية وتحليل: " + profile["name_ar"] if language == "ar" else "Background and analysis: " + profile["name"])
    assert original["title"] not in report["response"] and seed["url"] not in report["response"]
    assert original["limitations"][0] not in report["response"]
    assert all(source["role"] == "background" for source in report["sources"])
    assert set(report["citation_mappings"]) == {source["id"] for source in report["sources"]}
    assert len(requests) == 3 and sum(not request["tools"] for request in requests) == 1


def test_unused_background_sources_do_not_rescue_sections_supported_only_by_rejected_fresh_material(monkeypatch):
    report, profile, seed, requests, (supplied, original) = mixed_education_report(monkeypatch, "en", section_evidence="fresh")
    assert any(source["role"] == "background" for source in supplied)
    assert report["status"] == "no_evidence" and report["items"] == []
    assert [section["id"] for section in report["sections"]] == ["executive_summary"]
    assert report["sections"][0]["source_ids"] == []
    assert report["verification_diagnostics"]["withheld_sections"] == [section["id"] for section in profile["sections"]]
    assert "Unaccepted fresh claim" not in report["response"] and original["title"] not in report["response"]
    assert len(requests) == 3


def test_accepted_fresh_item_keeps_its_report_title_sections_and_source_classification(monkeypatch):
    report, profile, seed, requests, (supplied, original) = mixed_education_report(monkeypatch, "en",
        item_state="accepted", section_evidence="mixed")
    assert report["status"] == "complete" and len(report["items"]) == 1
    assert report["title"] == original["title"]
    assert original["limitations"][0] in report["limitations"]
    assert report["verification_diagnostics"]["excluded_items"] == {}
    assert report["verification_diagnostics"]["withheld_sections"] == []
    assert len(report["sections"]) == len(profile["sections"])
    assert report["citation_mappings"][seed["id"]]["role"] == "fresh"
    assert report["source_coverage"]["fresh_source_count"] == 1
    assert report["source_coverage"]["background_source_count"] == 1
    assert len(requests) == 3 and sum(not request["tools"] for request in requests) == 1
