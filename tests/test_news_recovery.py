"""Offline, fictional fixtures for native-source evidence recovery; no API calls."""
import json
from copy import deepcopy
from datetime import datetime
from urllib.parse import quote

import pytest

from app import news_recovery as recovery
from app import runtime


AS_OF = datetime(2026, 10, 20, 12, 0, tzinfo=runtime.DUBAI)
WINDOW = (datetime(2026, 10, 20), datetime(2026, 10, 20, 23, 59))
ARTICLE = "https://www.ft.com/content/ai-hub-synthetic-fixture"
REASON = "missing_native_source_evidence"
BREADTH_REASON = "limited_publisher_breadth"
PUBLISHERS = {
    "financial_times": "www.ft.com",
    "the_atlantic": "www.theatlantic.com",
    "politico": "www.politico.eu",
    "wall_street_journal": "www.wsj.com",
    "daily_telegraph": "www.telegraph.co.uk",
    "al_jazeera": "www.aljazeera.com",
}


@pytest.fixture(autouse=True)
def no_paid_helpers(monkeypatch):
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Planning and payload construction must not call a provider.")
    monkeypatch.setattr(runtime, "_call_model", unexpected_call)


def response(*, sources=None, annotations=None, text="", action_type="search", action_url=None,
             queries=None):
    """Use the real response normalizer with a synthetic completed provider reply."""
    action = {"type": action_type, "sources": sources if sources is not None else [{"url": ARTICLE}]}
    if action_url is not None:
        action["url"] = action_url
    if queries is not None:
        action["queries"] = queries
    raw = {"status": "completed", "service_tier": "default",
           "output": [
               {"type": "web_search_call", "status": "completed", "action": action},
               {"type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text,
                             "annotations": annotations or []}]}],
           "usage": {"input_tokens": 100, "output_tokens": 30}}
    return runtime._extract_model_response(raw, research=True)


def empty_search(**changes):
    return {"status": "empty", "results": [],
            "excluded": {"missing_native_title": 1, "unassociated_note": 0}, **changes}


def make_plan(urls=None, *, reason=REASON, covered=None, queries=None):
    urls = [ARTICLE] if urls is None else urls
    return {"reason": reason, "candidate_urls": urls, "candidate_count": len(urls),
            "omitted_candidate_count": 0, "covered_publisher_families": covered or [],
            "previous_queries": queries or []}


def dated_sources(hosts, *, publication="2026-10-20T07:00:00Z", overrides=None):
    """Build real attributed source packs from fictional native replies."""
    titles = ["Iran announces a fictional education programme",
              "Tehran opens a synthetic cultural archive",
              "Iranian parliament debates a test-only budget",
              "Hormuz shipping study illustrates a fictional navigation project",
              "Iranian university presents an imaginary astronomy curriculum",
              "Tehran researchers describe synthetic wetland restoration",
              "Iranian farmers examine a fictional drought experiment",
              "Iran introduces a synthetic railway timetable"]
    paragraphs, citations, metadata = [], [], []
    for index, host in enumerate(hosts):
        url = f"https://{host}/article/ai-hub-synthetic-{index}"
        value = (overrides or {}).get(index, publication)
        paragraph = (f"Title: {titles[index]}. {url} Published: {value}. "
                     "These are fictional Iranian research notes used only in an offline test. [citation]")
        start = sum(len(item) + 2 for item in paragraphs)
        metadata.append({"url": url, "title": titles[index]})
        citations.append({"type": "url_citation", "url": url, "title": titles[index],
                          "start_index": start + paragraph.index("[citation]"),
                          "end_index": start + len(paragraph)})
        paragraphs.append(paragraph)
    reply = response(sources=metadata, annotations=citations, text="\n\n".join(paragraphs))
    return reply, runtime._native_research_sources(reply, "iran-news")


def plan_for(reply=None, search=None, agent="geopolitical-morning"):
    return recovery.plan_evidence_recovery(
        agent, response() if reply is None else reply,
        empty_search() if search is None else search, WINDOW, AS_OF)


def build(plan=None, task="أخبار إيران المنشورة اليوم", agent="geopolitical-morning"):
    return recovery.build_evidence_recovery_payload(
        runtime.DEFAULT_OPENAI_MODEL, agent, task, WINDOW, AS_OF,
        make_plan() if plan is None else plan)


def payload_text(payload):
    return "\n".join(part["text"] for message in payload["input"] for part in message["content"])


def candidate_state(payload):
    for message in payload["input"]:
        for part in message["content"]:
            if part["text"].startswith("<UNTRUSTED_NATIVE_URL_CANDIDATES>"):
                return json.loads(part["text"].split("\n", 1)[1].rsplit("\n", 1)[0])
    raise AssertionError("Expected a separately labeled untrusted candidate registry.")


@pytest.mark.parametrize("agent", sorted(runtime.NEWS_REPORT_AGENT_IDS - {"iran-news"}))
def test_empty_news_with_missing_native_title_can_plan_observed_source(agent):
    reply = response()
    search = runtime._native_research_sources(reply, agent)
    assert search["results"] == []
    assert search["excluded"]["missing_native_title"] == 1
    plan = plan_for(reply, search, agent)
    assert plan == make_plan()


def test_iran_profile_can_use_openai_research_after_workspace_migration():
    assert plan_for(agent="iran-news") is not None
    assert build(agent="iran-news") is not None


def test_missing_notes_and_single_publisher_choose_their_distinct_followup_reasons():
    title = "Iran announces a fictional education pilot"
    reply = response(sources=[{"url": ARTICLE, "title": title}])
    search = runtime._native_research_sources(reply, "iran-news")
    assert search["excluded"]["unassociated_note"] == 1
    assert plan_for(reply, search)["candidate_urls"] == [ARTICLE]

    text = (f"Title: {title}. {ARTICLE} Published: 2026-10-20. "
            "Synthetic fixture: an imaginary Iranian education project was announced. [citation]")
    complete = response(sources=[{"url": ARTICLE}], text=text, annotations=[
        {"type": "url_citation", "url": ARTICLE, "title": title,
         "start_index": text.index("[citation]"), "end_index": len(text)}])
    extracted = runtime._native_research_sources(complete, "iran-news")
    assert len(extracted["results"]) == 1
    assert extracted["results"][0]["url"] == ARTICLE
    assert extracted["results"][0]["publisher_text_verified"] is False
    plan = plan_for(complete, extracted)
    assert plan["reason"] == BREADTH_REASON
    assert plan["covered_publisher_families"] == ["financial_times"]


@pytest.mark.parametrize("excluded", [
    {}, {"missing_native_title": 0, "unassociated_note": 0},
    {"missing_native_title": True}, {"unassociated_note": "1"},
    {"missing_native_title": -1}, {"irrelevant": 4}, {"invalid_source": 3},
])
def test_without_specific_missing_evidence_reason_empty_success_can_request_breadth(excluded):
    assert plan_for(search=empty_search(excluded=excluded))["reason"] == BREADTH_REASON


def test_failed_or_malformed_search_results_never_request_a_followup():
    for search in [empty_search(status="failed"), empty_search(status="unknown"),
                   empty_search(results=None)]:
        assert plan_for(search=search) is None


def test_empty_success_without_native_article_seeds_can_make_one_breadth_plan():
    plan = plan_for(response(sources=[]), empty_search(excluded={}))
    assert plan == make_plan([], reason=BREADTH_REASON)
    payload = build(plan)
    assert payload is not None and candidate_state(payload) == plan


def test_all_six_current_publisher_families_skip_optional_research():
    reply, search = dated_sources(list(PUBLISHERS.values()))
    assert len(search["results"]) == 6
    assert plan_for(reply, search) is None


def test_aljazeera_and_politico_language_domains_count_as_two_families_not_four():
    reply, search = dated_sources(["www.aljazeera.com", "www.aljazeera.net",
                                   "www.politico.com", "www.politico.eu"])
    plan = plan_for(reply, search)
    assert plan["reason"] == BREADTH_REASON
    assert set(plan["covered_publisher_families"]) == {"al_jazeera", "politico"}


@pytest.mark.parametrize("publication", [
    "unknown", "2026-10-19T07:00:00Z", "2026-10-21", "2026-10-20T13:00:00Z",
    "2026-10-20T25:99:00Z", "2026-10-20 13:00:00 UTC", "2026-10-20 13:00:00 GMT",
])
def test_missing_old_future_or_malformed_publication_does_not_satisfy_family_coverage(publication):
    reply, search = dated_sources(list(PUBLISHERS.values()), overrides={5: publication})
    assert len(search["results"]) == 6
    plan = plan_for(reply, search)
    assert plan["reason"] == BREADTH_REASON
    assert set(plan["covered_publisher_families"]) == set(PUBLISHERS) - {"al_jazeera"}
    assert build(plan)["tools"][0]["filters"]["allowed_domains"] == ["aljazeera.com", "aljazeera.net"]


def test_url_date_and_article_title_cannot_substitute_for_explicit_publication():
    reply, search = dated_sources(["www.aljazeera.com"], publication="unknown")
    source = search["results"][0]
    source["url"] = "https://www.aljazeera.com/news/2026/10/20/synthetic-current-date"
    source["title"] += " Published: 2026-10-20"
    plan = plan_for(reply, search)
    assert plan["covered_publisher_families"] == []


@pytest.mark.parametrize("agent", ["pre-meeting-briefing", "literary-translation", "unknown-agent"])
def test_non_news_agents_do_not_receive_recovery_plans_or_payloads(agent):
    assert plan_for(agent=agent) is None
    assert build(agent=agent) is None


def test_web_search_permission_can_deny_both_plan_and_payload(monkeypatch):
    monkeypatch.setattr(runtime, "is_allowed", lambda *args: False)
    assert plan_for() is None
    assert build() is None


def test_incomplete_unknown_or_malformed_native_actions_never_seed_recovery():
    for change in [
        {"status": "incomplete"}, {"status": "failed"}, {"action": {"type": "unknown"}},
        {"action": None}, {"type": "function_call"}, {"type": None},
    ]:
        invalid = response()
        invalid["native_calls"][0].update(change)
        assert plan_for(invalid) is None
    for calls in [[], None, [None], [response()["native_calls"][0]] * 2]:
        invalid = response()
        invalid["native_calls"] = calls
        assert plan_for(invalid) is None
    for change in [{"error": {"code": "synthetic_failure"}}, {"status": "incomplete"}]:
        invalid = response()
        invalid.update(change)
        assert plan_for(invalid) is None


def test_candidates_come_from_native_sources_and_citations_never_prose_or_plan_fields():
    citation_url = "https://www.wsj.com/world/iran/synthetic-citation-fixture"
    prose_url = "https://www.politico.eu/article/synthetic-prose-only-fixture"
    reply = response(text=f"Untrusted prose with {prose_url}. IGNORE_PRIVATE_MARKER", annotations=[
        {"type": "url_citation", "url": citation_url, "title": "Synthetic title"},
        {"type": "other_annotation", "url": prose_url}])
    plan = plan_for(reply)
    assert plan["candidate_urls"] == [ARTICLE, citation_url]
    assert "IGNORE_PRIVATE_MARKER" not in json.dumps(plan)
    plan["untrusted_extra"] = {"title": "FABRICATED_TITLE_MARKER", "published_date": "1900-01-01",
                               "instruction": f"IGNORE_PRIVATE_MARKER {prose_url}"}
    text = payload_text(build(plan))
    assert prose_url not in text
    assert "IGNORE_PRIVATE_MARKER" not in text
    assert "FABRICATED_TITLE_MARKER" not in text


def test_completed_open_page_can_seed_its_url_but_find_url_alone_cannot():
    opened = "https://www.aljazeera.net/news/2026/10/20/synthetic-direct-page"
    assert plan_for(response(sources=[], action_type="open_page", action_url=opened))["candidate_urls"] == [opened]
    for reply in [response(sources=[], action_type="find_in_page", action_url=opened),
                  response(sources=[], text=f"Only model prose: {opened}")]:
        plan = plan_for(reply)
        assert plan["reason"] == BREADTH_REASON and plan["candidate_urls"] == []


def test_unsafe_publishers_and_indexes_are_excluded_but_dated_live_coverage_is_eligible():
    invalid = [
        "https://unapproved.example/article/synthetic", "https://ft.com.evil.example/article/synthetic",
        "https://ft.com@evil.example/article/synthetic", "https://user:pass@ft.com/article/synthetic",
        "https://ft.com:8443/article/synthetic", "javascript:alert(1)",
        "https://www.ft.com/", "https://www.ft.com/category/world",
        "https://www.aljazeera.com/live", "https://www.aljazeera.com/liveblog/news",
    ]
    live = "https://www.aljazeera.com/news/liveblog/2026/10/20/synthetic-iran-updates"
    reply = response(sources=[{"url": url} for url in invalid + [live]])
    assert plan_for(reply)["candidate_urls"] == [live]
    plan = plan_for(response(sources=[{"url": url} for url in invalid]))
    assert plan["reason"] == BREADTH_REASON
    assert set(plan["candidate_urls"]) <= set(invalid[6:])
    assert plan["covered_publisher_families"] == []


def test_breadth_index_hints_are_native_only_and_cannot_claim_article_coverage():
    hints = ["https://www.politico.eu/category/politics/",
             "https://www.aljazeera.com/topics/country/iran.html"]
    excluded = ["https://www.ft.com/content/synthetic-document.pdf",
                "https://www.ft.com/sitemap.xml", "https://www.ft.com/sitemaps/news.xml",
                "https://ft.com.evil.example/world", "https://www.ft.com/"]
    prose = "https://www.wsj.com/world/iran/prose-only-synthetic-hint"
    plan = plan_for(response(sources=[{"url": url} for url in hints + excluded], text=prose),
                    empty_search(excluded={}))
    assert plan["reason"] == BREADTH_REASON
    assert plan["candidate_urls"] == hints
    assert plan["covered_publisher_families"] == []
    assert prose not in payload_text(build(plan))


def test_equivalent_unicode_percent_and_tracking_urls_dedupe_without_guessing_paths():
    unicode_url = "https://www.aljazeera.net/news/2026/10/20/اختبار-إيران"
    encoded_url = quote(unicode_url, safe=":/-")
    distinct_reserved = "https://www.ft.com/content/synthetic%2Fpath"
    distinct_slash = "https://www.ft.com/content/synthetic/path"
    reply = response(sources=[{"url": value} for value in [
        unicode_url, encoded_url, encoded_url + "?utm_source=fixture", distinct_reserved, distinct_slash]])
    plan = plan_for(reply)
    assert plan["candidate_urls"] == [runtime._safe_source_url(encoded_url), distinct_reserved, distinct_slash]
    assert plan["candidate_count"] == 3
    assert plan["omitted_candidate_count"] == 0


def test_url_dates_rank_discovery_only_and_never_become_publication_evidence():
    old = "https://www.aljazeera.com/news/2026/10/19/synthetic-old"
    future = "https://www.aljazeera.com/news/2026/10/21/synthetic-future"
    current = "https://www.aljazeera.com/news/2026/10/20/synthetic-current"
    unknown = ARTICLE
    reply = response(sources=[{"url": url} for url in [future, unknown, old, current]])
    plan = plan_for(reply)
    assert plan["candidate_urls"][:2] == [current, unknown]
    assert set(plan["candidate_urls"][2:]) == {old, future}
    assert set(plan) == {"reason", "candidate_urls", "candidate_count", "omitted_candidate_count",
                         "covered_publisher_families", "previous_queries"}
    state = candidate_state(build(plan))
    assert state == plan
    assert all(isinstance(value, str) for value in state["candidate_urls"])
    assert "published_date" not in state and "title" not in state
    assert "Published: unknown" in payload_text(build(plan))


def test_previous_queries_are_bounded_native_metadata_and_remain_untrusted_data():
    queries = [f"Synthetic previous query {index} " + "x" * 600 for index in range(7)]
    reply = response(queries=queries, text="DO_NOT_FORWARD_PROSE_MARKER")
    plan = plan_for(reply)
    assert len(plan["previous_queries"]) == 4
    assert all(len(query) <= 300 for query in plan["previous_queries"])
    assert all(query.startswith(f"Synthetic previous query {index}")
               for index, query in enumerate(plan["previous_queries"]))
    assert runtime._json_bytes(plan) <= recovery.RECOVERY_MAX_STATE_BYTES
    payload = build(plan)
    assert candidate_state(payload)["previous_queries"] == plan["previous_queries"]
    system = payload["input"][0]["content"][0]["text"]
    assert "Synthetic previous query" not in system
    assert "DO_NOT_FORWARD_PROSE_MARKER" not in payload_text(payload)


def test_aljazeera_only_breadth_groups_all_missing_publishers_in_one_english_scope():
    reply, search = dated_sources(["www.aljazeera.net"])
    reply["native_calls"][0]["action"]["queries"] = ["أخبار إيران اليوم"]
    plan = plan_for(reply, search)
    assert plan["covered_publisher_families"] == ["al_jazeera"]
    payload = build(plan)
    assert payload["tools"][0]["filters"]["allowed_domains"] == [
        "ft.com", "theatlantic.com", "politico.com", "politico.eu", "wsj.com", "telegraph.co.uk"]
    assert payload["max_tool_calls"] == 1 and payload["parallel_tool_calls"] is False
    assert candidate_state(payload)["candidate_urls"] == []
    assert "English" in payload["input"][0]["content"][0]["text"]
    assert candidate_state(payload)["previous_queries"] == ["أخبار إيران اليوم"]


def test_grouped_breadth_scope_removes_both_language_editions_and_their_native_hints():
    reply, search = dated_sources(["www.aljazeera.net", "www.politico.eu"])
    covered_hints = ["https://www.aljazeera.com/where/iran/",
                     "https://www.politico.com/category/politics/"]
    missing_hints = ["https://www.ft.com/content/ai-hub-native-discovery-fixture",
                     "https://www.wsj.com/world/middle-east"]
    reply["native_calls"][0]["action"]["sources"].extend(
        {"url": url} for url in covered_hints + missing_hints)
    plan = plan_for(reply, search)
    assert set(plan["covered_publisher_families"]) == {"al_jazeera", "politico"}
    assert plan["candidate_urls"] == missing_hints
    payload = build(plan)
    assert payload["tools"][0]["filters"]["allowed_domains"] == [
        "ft.com", "theatlantic.com", "wsj.com", "telegraph.co.uk"]
    assert candidate_state(payload)["candidate_urls"] == missing_hints
    assert payload["max_tool_calls"] == 1


def test_breadth_builder_refilters_covered_family_hints_without_widening_tool_scope():
    urls = ["https://www.aljazeera.net/news/2026/10/20/synthetic-covered-hint", ARTICLE]
    plan = make_plan(urls, reason=BREADTH_REASON, covered=["al_jazeera"])
    original = deepcopy(plan)
    payload = build(plan)
    assert payload is not None
    state = candidate_state(payload)
    assert state["candidate_urls"] == [ARTICLE]
    assert state["candidate_count"] == 1
    assert state["candidate_count"] + state["omitted_candidate_count"] == 2
    assert set(payload["tools"][0]["filters"]["allowed_domains"]) == {
        "ft.com", "theatlantic.com", "politico.com", "politico.eu", "wsj.com", "telegraph.co.uk"}
    assert plan == original


def test_one_missing_publisher_family_still_uses_one_grouped_pass_with_both_its_domains():
    plan = make_plan([], reason=BREADTH_REASON,
                     covered=[name for name in PUBLISHERS if name != "politico"])
    payload = build(plan)
    assert payload["tools"][0]["filters"]["allowed_domains"] == ["politico.com", "politico.eu"]
    assert payload["max_tool_calls"] == 1 and payload["parallel_tool_calls"] is False
    assert candidate_state(payload)["candidate_urls"] == []


def test_candidate_count_and_complete_serialized_state_are_bounded():
    assert recovery.RECOVERY_MAX_CANDIDATES == 8
    assert recovery.RECOVERY_MAX_STATE_BYTES == 12000
    many = [f"https://www.ft.com/content/synthetic-{index}" for index in range(12)]
    plan = plan_for(response(sources=[{"url": url} for url in many]))
    assert plan["candidate_urls"] == many[:8]
    assert plan["candidate_count"] == 8 and plan["omitted_candidate_count"] == 4
    long_urls = [f"https://www.ft.com/content/synthetic-{index}-" + "x" * 1500 for index in range(8)]
    plan = plan_for(response(sources=[{"url": url} for url in long_urls]))
    assert 0 < plan["candidate_count"] < 8
    assert plan["candidate_count"] + plan["omitted_candidate_count"] == 8
    assert runtime._json_bytes(plan) <= recovery.RECOVERY_MAX_STATE_BYTES


def test_payload_is_one_fresh_native_request_with_all_publishers_and_untrusted_url_state():
    task = "Fictional Iran news published today"
    payload = build(task=task)
    assert recovery.RECOVERY_MAX_INPUT_BYTES == 50000
    assert payload["model"] == runtime.DEFAULT_OPENAI_MODEL
    assert payload["tools"] == [{"type": "web_search", "search_context_size": "high",
                                 "external_web_access": True,
                                 "return_token_budget": "default",
                                 "filters": {"allowed_domains": list(runtime.ALLOWED_SOURCE_DOMAINS)}}]
    assert payload["tool_choice"] == "required"
    assert payload["max_tool_calls"] == 1 and payload["parallel_tool_calls"] is False
    assert payload["store"] is False and payload["service_tier"] == "default"
    assert payload["include"] == ["web_search_call.action.sources"]
    assert payload["max_output_tokens"] == runtime._research_output_tokens()
    assert "previous_response_id" not in payload and "conversation" not in payload
    assert payload["input"][1]["content"][0]["text"] == task
    assert candidate_state(payload) == make_plan()
    text = payload_text(payload)
    assert "Title:" in text and "Published: unknown" in text and "native URL citation" in text
    assert "full timestamps" in text and "untrusted" in text.lower()
    assert runtime._json_bytes(payload) <= 50000


def test_large_original_task_drops_trailing_seeds_without_truncating_task_or_json():
    urls = [f"https://www.ft.com/content/synthetic-{index}-" + "x" * 1450 for index in range(8)]
    plan = plan_for(response(sources=[{"url": url} for url in urls]))
    # JSON escaping expands this valid-length fixture beyond its character count.
    task = "Synthetic task: " + "\x00" * 6500 + "a" * 500
    original = deepcopy(plan)
    payload = build(plan, task=task)
    assert payload is not None
    state = candidate_state(payload)
    assert 0 < len(state["candidate_urls"]) < len(plan["candidate_urls"])
    assert state["candidate_urls"] == plan["candidate_urls"][:state["candidate_count"]]
    assert state["candidate_count"] + state["omitted_candidate_count"] == 8
    assert payload["input"][1]["content"][0]["text"] == task
    assert runtime._json_bytes(payload) <= 50000
    assert plan == original


def test_invalid_plans_oversized_tasks_or_unfittable_requests_are_skipped_without_calls():
    for plan in [{}, make_plan([]), make_plan(["https://unapproved.example/article/fixture"]),
                 {**make_plan(), "reason": "provider_error"},
                 {**make_plan(), "candidate_count": 2},
                 {**make_plan(), "omitted_candidate_count": -1}]:
        assert build(plan) is None
    assert build(task=" ") is None
    assert build(task="x" * 8001) is None
    assert build(task="\x00" * 8000) is None
