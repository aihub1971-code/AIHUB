"""Open-web chat through real native-response parsing, SQLite and HTTP SSE.

Provider bytes and publisher HTML are synthetic. No paid network is allowed;
the tests verify orchestration, attestation and persistence, not model accuracy.
"""
from copy import deepcopy
import json
import threading

from fastapi import HTTPException
import httpx
import pytest

from app import chat, chat_research, research, runtime, workspace_chat
from app.storage import QuotaLimits, Store
from tests.test_agent_chat_memory import ProviderChunks, agent_thread, events, finished, send_stream
from tests.test_research import native_call, page_record, provider_response
from tests.test_workspace import assert_workspace_public, second_visitor
from tests.test_workspace_chat import discussion, evidence, save_report, send


URL = "https://original-scholarship.example/abbasid-translators"
TITLE = "The translators of Abbasid Baghdad"
QUESTION = "Who translated Greek scientific works in Abbasid Baghdad?"
PREFIX = "/v1/public/workspace"


@pytest.fixture
def online_provider(discussion, monkeypatch):
    original = httpx.Client
    state = {"requests": [], "frames": [], "pages": [], "answer": "A concise answer.",
             "search_error": None, "empty": False, "missing_usage": False, "on_request": None}
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-open-web-chat-key")

    def dispatch(request):
        payload = json.loads(request.content)
        state["requests"].append(deepcopy(payload))
        store = discussion["database"]
        with store._connect() as connection:
            pending = connection.execute("SELECT * FROM chat_turns WHERE status='pending'").fetchall()
        assert len(pending) == 1, "The complete turn must be reserved before either provider call"
        assert pending[0]["accounted_microusd"] == pending[0]["reserved_microusd"] > 0
        if state["on_request"]:
            state["on_request"](payload, pending[0])
        if payload["tools"]:
            assert pending[0]["research_attempt"] == 1
            assert payload["max_tool_calls"] <= 2
            assert store.usage_summary()["daily"]["chat_research_attempts"] >= 1
            if state["search_error"]:
                return httpx.Response(429, headers={"Retry-After": "23"}, json={"error": {"code": "insufficient_quota",
                                                          "message": "private billing sentinel"}})
            return httpx.Response(200, json=provider_response("Synthetic discovery notes.",
                native=[native_call(url=URL)], usage=not state["missing_usage"]))
        if payload.get("stream"):
            answer = state["answer"]
            frames = [{"type": "response.output_text.delta", "delta": answer[:12]},
                      {"type": "response.output_text.delta", "delta": answer[12:]}, finished(answer)]
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  stream=ProviderChunks(frames, state["frames"]))
        return httpx.Response(200, json=provider_response(state["answer"]))

    def publisher(url, authorized):
        state["pages"].append(url)
        assert url in authorized and url == URL
        if state["empty"]:
            raise ValueError("publisher_unavailable")
        return page_record(URL, TITLE, published="2010-03-04", article_type="ScholarlyArticle",
            body="This synthetic scholarly reference identifies named translators and qualifies competing historical interpretations. " * 3)

    monkeypatch.setattr(chat.httpx, "Client", lambda *args, **kwargs:
                        original(transport=httpx.MockTransport(dispatch), **kwargs))
    monkeypatch.setattr(research, "_download_page", publisher)
    return state


@pytest.mark.parametrize("language,question,answer", [
    ("en", QUESTION, "The checked historical reference discusses competing interpretations [study](" + URL + ")."),
    ("ar", "من ترجم المؤلفات العلمية اليونانية في بغداد العباسية؟",
     "تعرض الدراسة التاريخية تفسيرات متباينة، وهي مرجع مؤرخ وليست خبراً جديداً [الدراسة](" + URL + ")."),
])
def test_new_question_searches_beyond_agent_domain_without_report_and_restores_its_sources(
        discussion, online_provider, monkeypatch, language, question, answer):
    client, database = discussion["client"], discussion["database"]
    thread = agent_thread(client, "iran-news", language)
    assert online_provider["requests"] == []
    online_provider["answer"] = answer

    def before_answer(payload, pending):
        if not payload["tools"]:
            context = json.loads(pending["context_json"])
            assert context["sources"][0]["url"] == URL
            assert context["sources"][0]["publisher_text_verified"] is True
            assert context["web_research"]["status"] == "complete"
    online_provider["on_request"] = before_answer
    response = send_stream(client, thread["conversation_id"], question,
                           agent_id="iran-news", language=language)
    assert response.status_code == 200, response.text
    received = events(response)
    assert [value["status"] for event, value in received if event == "status"] == [
        "searching", "checking_sources", "answering"]
    assert [name for name, _ in received][-3:] == ["delta", "delta", "complete"]
    final = received[-1][1]
    assert final["message"]["content"] == answer
    context = final["message"]["context"]
    assert context == final["context"]
    assert context["report_ids"] == [] and context["report_id"] is None
    assert context["web_research"]["status"] == "complete" and context["web_research"]["source_count"] == 1
    source = context["sources"][0]
    assert source["url"] == URL and source["role"] == "reference"
    assert source["published_at"] == "2010-03-04" and source["publication_precision"] == "day"
    assert source["source_category"] == "research" and source["retrieved_at"] != source["published_at"]
    assert "excerpt" not in source and "pricing" not in response.text
    assert len(online_provider["requests"]) == 2 and online_provider["pages"] == [URL]
    assert online_provider["requests"][0]["tools"][0]["type"] == "web_search"
    answer_payload = online_provider["requests"][1]
    assert answer_payload["tools"] == [] and answer_payload["stream"] is True
    assert evidence(answer_payload)["sources"][0]["excerpt"]
    assert "subjects outside its usual specialty" in answer_payload["input"][0]["content"]
    assert discussion["reports"].list_jobs(discussion["owner"]) == []
    assert database.list_reports() == []
    summary = database.usage_summary()
    assert summary["daily"]["attempts"] == summary["daily"]["chat_research_attempts"] == 1
    with database._connect() as connection:
        record = connection.execute("SELECT * FROM chat_turns").fetchone()
    assert record["cost_basis"] == "usage_estimate"
    assert 0 < record["accounted_microusd"] < record["reserved_microusd"]
    usage = json.loads(record["usage_json"])
    assert usage["input_tokens"] == 1320 and usage["output_tokens"] == 920
    assert usage["web_search_calls"] == usage["native_actions"] == 1
    assert_workspace_public(final)

    restarted = Store(database.path, database.limits, clock=database.clock)
    restarted.initialize()
    restored = restarted.agent_conversation(discussion["owner"], "iran-news")
    assert restored["messages"][-1]["context"]["sources"][0]["url"] == URL
    public_restore = agent_thread(client, "iran-news", language)
    assert public_restore["messages"][-1]["context"] == context
    monkeypatch.delenv("OPENAI_API_KEY")
    replay = events(send_stream(client, thread["conversation_id"], question,
                               agent_id="iran-news", language=language))
    assert replay[0][0] == "complete" and replay[0][1]["idempotency_replayed"] is True
    assert len(online_provider["requests"]) == 2 and database.usage_summary() == summary


def test_followup_search_gets_only_necessary_same_thread_topic_not_shared_private_context(discussion, online_provider):
    client = discussion["client"]
    other = agent_thread(client, "education-observatory")
    assert events(send_stream(client, other["conversation_id"], "Remember that our internal codename is SECRET_ORCHID."))[-1][0] == "complete"
    note = client.post(PREFIX + "/memory", json={"kind": "preference", "content": "Use short paragraphs; PRIVATE_NOTE_SENTINEL."}).json()
    client.patch(PREFIX + "/memory/" + note["memory_id"], json={"action": "approve"})
    thread = agent_thread(client, "iran-news")
    assert events(send_stream(client, thread["conversation_id"], QUESTION, key="first"))[-1][0] == "complete"
    follow = "And what about their competing interpretations?"
    assert events(send_stream(client, thread["conversation_id"], follow, key="follow"))[-1][0] == "complete"
    searches = [payload for payload in online_provider["requests"] if payload["tools"]]
    assert len(searches) == 2
    last_query = json.loads(searches[-1]["input"][1]["content"])
    assert last_query["question"] == follow and last_query["same_thread_topic_context"] == QUESTION
    assert "PRIVATE_NOTE_SENTINEL" not in json.dumps(searches)
    assert "SECRET_ORCHID" not in json.dumps(searches)
    assert "PRIVATE_NOTE_SENTINEL" in json.dumps(online_provider["requests"][-1])
    assert discussion["reports"].list_jobs(discussion["owner"]) == []


@pytest.mark.parametrize("question", [
    "Hello", "How should we work together?", "Remember that I prefer short paragraphs.",
    "Please use clear paragraphs.", "What did we discuss about programme costs?",
    "Explain the latest findings without searching online.",
    "Only use this report to explain its conclusions.",
    "Translate into Arabic: 'The latest research is inconclusive.'",
    "Write a short poem about friendship.", "اكتب قصيدة قصيرة عن الصداقة.",
    "ما التفضيل الذي اعتمدته مع الوكيل الآخر؟", "ما آخر ما ناقشنا؟ أجب دون إجراء بحث جديد.",
])
def test_explicit_local_intent_streams_an_answer_without_web_or_report(discussion, online_provider, question):
    thread = agent_thread(discussion["client"])
    response = send_stream(discussion["client"], thread["conversation_id"], question)
    assert events(response)[-1][0] == "complete", response.text
    assert len(online_provider["requests"]) == 1 and online_provider["requests"][0]["tools"] == []
    assert events(response)[-1][1]["context"]["web_research"]["status"] == "not_requested"
    assert discussion["database"].usage_summary()["daily"]["attempts"] == 0
    assert discussion["reports"].list_jobs(discussion["owner"]) == []


@pytest.mark.parametrize("question", [
    "Who is responsible for lunar exploration?", "How do I use Python urllib safely?",
    "Explain the evidence for penguin navigation.", "اشرح هذا النظام الضريبي في فنلندا.",
    "Summarize the World Bank's latest report on AI and jobs.",
    "Explain the report about penguin navigation.", "لخص أحدث تقرير للبنك الدولي عن الذكاء الاصطناعي والعمل.",
    "كيف أستخدم مكتبة بايثون urllib بأمان؟", "ما آخر المستجدات؟",
    "I am preparing a briefing: what are the latest Finland evaluation findings?",
])
def test_substantive_questions_are_not_limited_to_freshness_keywords_reports_or_platform_verbs(question):
    assert workspace_chat._research_mode(question, "iran-news", False) == "web"
    assert workspace_chat._research_mode(question, "iran-news", True) == "web"


@pytest.mark.parametrize("question", [
    "Search online and use only the official NASA website to explain eclipses.",
    "Only use official primary sources to explain the current policy.",
    "Search online using only reports published by the OECD.",
    "ابحث على الإنترنت واستخدم فقط مصادر رسمية لشرح الكسوف.",
    "ابحث في التقارير المنشورة من البنك الدولي فقط.",
    "Write in Arabic about the current ECB inflation target.",
    "هل يمكنك مساعدتي في البحث عن سياسة البنك المركزي الأوروبي؟",
])
def test_source_quality_style_and_help_prefixes_preserve_substantive_online_tasks(question):
    assert workspace_chat._research_mode(question, "education-observatory", False) == "web"
    assert workspace_chat._research_mode(question, "education-observatory", True) == "web"


@pytest.mark.parametrize("question", [
    "Only use this report to explain its conclusions.",
    "Use only this report to explain its assessment.",
    "استخدم فقط هذا التقرير لشرح نتائجه.",
    "اشرح هذا التقرير فقط.",
])
def test_explicit_local_report_restrictions_remain_local(question):
    assert workspace_chat._research_mode(question, "education-observatory", True) == "conversation"


@pytest.mark.parametrize("question", [
    "Generate a fresh report on Finland.", "I need a detailed report about Finland.",
    "I would like a report about economic cooperation.", "أريد تقريراً عن مهارات المعلمين.",
    "أحتاج إلى تقرير عن نتائج التعلم.",
])
def test_only_explicit_report_generation_uses_the_report_job_path(question):
    assert workspace_chat._research_mode(question, "education-observatory", False) == "report"


def test_current_question_searches_independently_of_old_ui_report_topic(discussion, online_provider):
    saved = save_report(discussion, "ubi-research", label="Canada-programme-costs")
    thread = agent_thread(discussion["client"])
    question = "What are Finland programme costs and their employment effects?"
    response = send_stream(discussion["client"], thread["conversation_id"], question,
                           agent_id="ubi-research", report_id=saved["report_id"], topic=saved["topic"])
    assert events(response)[-1][0] == "complete"
    query = json.loads(online_provider["requests"][0]["input"][1]["content"])
    assert query["question"] == question and query["same_thread_topic_context"] == ""
    assert "Canada" not in json.dumps(query)
    assert len(discussion["reports"].list_jobs(discussion["owner"])) == 1


def test_pronoun_in_a_new_named_subject_does_not_import_the_previous_search_topic():
    previous = "What were Canada programme costs and employment effects?"
    snapshot = {"history": [{"role": "user", "content": previous}]}
    independent = "What are Finland programme costs and their employment effects?"
    assert workspace_chat._search_topic_context(independent, snapshot, "ubi-research") == ""
    assert workspace_chat._search_topic_context("What were their programme costs?", snapshot, "ubi-research") == previous
    assert workspace_chat._search_topic_context("And what about their employment effects?", snapshot, "ubi-research") == previous


def test_checked_chat_sources_can_support_owned_memory_but_never_another_visitor(discussion, online_provider):
    client = discussion["client"]
    thread = agent_thread(client)
    assert events(send_stream(client, thread["conversation_id"], QUESTION))[-1][0] == "complete"
    note = {"kind": "knowledge", "content": "A dated scholarly reference, not a newly verified claim.", "source_urls": [URL]}
    proposed = client.post(PREFIX + "/memory", json=note)
    assert proposed.status_code == 201 and proposed.json()["status"] == "proposed"
    other_agent = agent_thread(client, "education-observatory")
    online_provider["answer"] = "Our dated source is [the reference](" + URL + ")."
    followup = send_stream(client, other_agent["conversation_id"], "What did we discuss about Abbasid translators?", key="other-agent")
    final = events(followup)[-1]
    assert final[0] == "complete"
    assert final[1]["context"]["web_research"]["status"] == "not_requested"
    assert final[1]["context"]["sources"][0]["url"] == URL
    with second_visitor() as stranger:
        assert stranger.get(PREFIX + "/conversations/" + thread["conversation_id"]).status_code == 404
        assert stranger.post(PREFIX + "/memory", json=note).status_code == 422
        foreign_thread = agent_thread(stranger)
        assert foreign_thread["messages"] == [] and not foreign_thread["context"].get("sources")


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("language", ["en", "ar"])
@pytest.mark.parametrize("missing_usage", [False, True])
def test_no_verified_pages_discloses_empty_search_without_mislabeling_saved_evidence(
        discussion, online_provider, stream, language, missing_usage):
    selected = save_report(discussion, label="older-evidence")
    online_provider["empty"] = True
    online_provider["missing_usage"] = missing_usage
    client, database = discussion["client"], discussion["database"]
    thread = agent_thread(client, "iran-news", language)
    request = send_stream if stream else send
    args = {"agent_id": "iran-news", "report_id": selected["report_id"], "language": language}
    result = request(client, thread["conversation_id"], QUESTION, **args)
    assert result.status_code == 200, result.text
    if stream:
        received = events(result)
        assert received[-1][0] == "complete"
        assert not any(name == "delta" for name, _ in received)
        final = received[-1][1]
    else:
        final = result.json()
    assert final["context"]["web_research"]["status"] == "empty"
    assert final["context"]["web_research"]["source_count"] == 0
    assert final["context"]["web_research"]["limitations"]
    assert final["context"]["sources"] == []
    assert final["context"]["fresh_research"] is False
    assert ("couldn't verify" if language == "en" else "لم أتمكن من التحقق") in final["message"]["content"]
    assert "fixture.example" not in final["message"]["content"]
    # Empty retrieval is finished locally inside the existing paid turn. It
    # cannot call the answer model merely to rephrase an evidence limitation.
    assert len(online_provider["requests"]) == 1
    assert online_provider["requests"][0]["tools"] and online_provider["frames"] == []
    with database._connect() as connection:
        rows = connection.execute("SELECT * FROM chat_turns").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "succeeded" and row["research_attempt"] == 1
    usage = json.loads(row["usage_json"])
    assert usage["stages"][-1] == {"stage": "server_notice", "input_tokens": 0,
                                  "output_tokens": 0, "usage_reported": True}
    if missing_usage:
        assert row["cost_basis"] == "reservation_retained"
        assert row["accounted_microusd"] == row["reserved_microusd"]
        assert usage["usage_reported"] is False
    else:
        assert row["cost_basis"] == "usage_estimate"
        assert 0 < row["accounted_microusd"] < row["reserved_microusd"]
        assert usage["input_tokens"] == 1200 and usage["output_tokens"] == 900
    summary = database.usage_summary()
    assert summary["daily"]["chat_research_attempts"] == 1
    replay = request(client, thread["conversation_id"], QUESTION, **args)
    replayed = events(replay)[-1][1] if stream else replay.json()
    assert replayed["idempotency_replayed"] is True
    assert replayed["message"] == final["message"]
    assert len(online_provider["requests"]) == 1 and database.usage_summary() == summary


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("language", ["en", "ar"])
@pytest.mark.parametrize("citation", ["current", "old_report", "missing"])
def test_factual_web_answer_requires_a_current_checked_citation(
        discussion, online_provider, stream, language, citation):
    selected = save_report(discussion, label="older-evidence")
    answer = "A synthetic historical finding." if language == "en" else "هذه نتيجة تاريخية اختبارية."
    if citation != "missing":
        answer += " [Source](" + (URL if citation == "current" else "https://fixture.example/older-evidence") + ")."
    online_provider["answer"] = answer
    client = discussion["client"]
    thread = agent_thread(client, "iran-news", language)
    request = send_stream if stream else send
    result = request(client, thread["conversation_id"], QUESTION,
                     agent_id="iran-news", report_id=selected["report_id"], language=language)
    assert result.status_code == 200, result.text
    final = events(result)[-1][1] if stream else result.json()
    content = final["message"]["content"]
    if citation == "current":
        assert content == answer
    else:
        assert ("couldn't verify" if language == "en" else "لم أتمكن من التحقق") in content
        assert "synthetic historical finding" not in content and "نتيجة تاريخية اختبارية" not in content
    assert len(online_provider["requests"]) == 2
    attached = evidence(online_provider["requests"][-1])
    assert attached["web_research"]["source_urls"] == [URL]
    assert "web_research.source_urls" in online_provider["requests"][-1]["input"][0]["content"]
    restored = agent_thread(client, "iran-news", language)
    assert restored["messages"][-1]["content"] == content
    assert restored["messages"][-1]["context"]["web_research"]["source_count"] == 1


def test_previous_checked_reference_cannot_ground_a_different_current_discovery():
    old_url = "https://previously-checked.example/background"
    context = {"reports": [], "sources": [
        {"url": url, "publisher_text_verified": True, "evidence_level": "publisher_page_verified"}
        for url in (old_url, URL)],
        "web_research": {"status": "complete", "source_urls": [URL]}}
    with pytest.raises(HTTPException) as error:
        workspace_chat.validate_answer_links("A claim [previous reference](" + old_url + ").", context)
    assert error.value.detail["code"] == "chat_evidence_missing"
    workspace_chat.validate_answer_links("A claim [current reference](" + URL + ").", context)


def test_report_only_instruction_does_not_reuse_prior_web_references_or_search_again(discussion, online_provider):
    client = discussion["client"]
    thread = agent_thread(client)
    assert events(send_stream(client, thread["conversation_id"], QUESTION))[-1][0] == "complete"
    selected = save_report(discussion, "ubi-research", label="selected-only")
    response = send_stream(client, thread["conversation_id"], "Use only this report to explain its assessment.",
        key="report-only", report_id=selected["report_id"])
    assert events(response)[-1][0] == "complete"
    attached = evidence(online_provider["requests"][-1])
    assert attached["evidence_scope"] == "selected_reports_only"
    assert attached["report_ids"] == [selected["report_id"]] and attached["sources"] == []
    assert attached["shared_memory"]["conversations"] == []
    assert len([request for request in online_provider["requests"] if request["tools"]]) == 1


def test_discovery_failure_retains_the_one_attempt_and_reservation_without_replay(discussion, online_provider):
    online_provider["search_error"] = True
    thread = agent_thread(discussion["client"])
    failed = send_stream(discussion["client"], thread["conversation_id"], QUESTION)
    assert events(failed)[-1][1]["code"] == "openai_quota_exceeded"
    assert events(failed)[-1][1]["retry_after_seconds"] == 23
    assert "private billing" not in failed.text
    with discussion["database"]._connect() as connection:
        row = connection.execute("SELECT * FROM chat_turns").fetchone()
    assert row["status"] == "failed" and row["research_attempt"] == 1
    assert row["accounted_microusd"] == row["reserved_microusd"] > 0
    assert events(send_stream(discussion["client"], thread["conversation_id"], QUESTION))[-1][1]["code"] == "chat_already_attempted"
    assert len(online_provider["requests"]) == 1
    assert discussion["database"].usage_summary()["daily"]["attempts"] == 1


@pytest.mark.parametrize("limit", ["attempts", "daily_usd", "monthly_usd"])
def test_combined_reservation_blocks_both_provider_calls_when_allowance_is_insufficient(discussion, online_provider, limit):
    store = discussion["database"]
    if limit == "attempts":
        store._limits = QuotaLimits(1, "100", "1000")
        record = store.reserve_run("iran-news", "Prior attempt", ".001")
        store.fail_run(record.report["report_id"], "fixture_failure")
        code = "daily_report_limit"
    else:
        store._limits = QuotaLimits(50, ".001" if limit == "daily_usd" else "100",
                                    ".001" if limit == "monthly_usd" else "1000")
        code = "daily_budget_limit" if limit == "daily_usd" else "monthly_budget_limit"
    thread = agent_thread(discussion["client"])
    failed = send_stream(discussion["client"], thread["conversation_id"], QUESTION)
    assert events(failed)[-1][1]["code"] == code
    assert online_provider["requests"] == [] and online_provider["pages"] == []
    assert agent_thread(discussion["client"])["messages"] == []


def test_missing_research_usage_does_not_release_a_combined_reservation(discussion, online_provider):
    online_provider["missing_usage"] = True
    thread = agent_thread(discussion["client"])
    assert events(send_stream(discussion["client"], thread["conversation_id"], QUESTION))[-1][0] == "complete"
    with discussion["database"]._connect() as connection:
        row = connection.execute("SELECT * FROM chat_turns").fetchone()
    assert row["cost_basis"] == "reservation_retained"
    assert row["accounted_microusd"] == row["reserved_microusd"]
    assert json.loads(row["usage_json"])["usage_reported"] is False


def test_new_unattested_link_is_rejected_after_search_and_never_saved(discussion, online_provider):
    online_provider["answer"] = "See [a fabricated source](https://invented.example/claim)."
    thread = agent_thread(discussion["client"])
    result = send_stream(discussion["client"], thread["conversation_id"], QUESTION)
    assert events(result)[-1][1]["code"] == "chat_source_mismatch"
    restored = agent_thread(discussion["client"])
    assert len(restored["messages"]) == 1 and restored["messages"][0]["status"] == "failed"
    assert discussion["database"].owned_chat_source_urls(discussion["owner"]) == set()


def test_cancelled_research_stream_never_starts_answer_or_repeats_search(discussion, monkeypatch):
    database = discussion["database"]
    cid = database.agent_conversation(discussion["owner"], "iran-news")["conversation_id"]
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-cancellation-key")
    started, release, finished_work = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def discovery(plan, *, on_stage=None):
        calls.append(plan["question"])
        on_stage("searching")
        started.set()
        assert release.wait(5), "Release the synthetic worker after closing its consumer"
        finished_work.set()
        return {"sources": [], "searched_at": plan["as_of"]}
    monkeypatch.setattr(chat_research, "discover_chat_sources", discovery)
    monkeypatch.setattr(chat, "_stream_model", lambda *args: pytest.fail("A cancelled search must not synthesize an answer"))
    context = {"report_id": None, "truncated": False, "reports": [], "sources": [],
        "_web_request": {"agent_id": "iran-news", "question": QUESTION},
        "web_research": {"status": "not_requested"}}
    kwargs = dict(owner_id=discussion["owner"], idempotency_key="cancelled-web-turn", store=database,
        platform_context=workspace_chat.platform_context(), workspace_context=context)
    stream = chat.stream_chat_message(cid, QUESTION, "en", **kwargs)
    try:
        event, value = next(stream)
        assert event == "status" and value["status"] == "searching" and started.wait(1)
        stream.close()
    finally:
        release.set()
    assert finished_work.wait(2)
    saved = database.get_chat_conversation(cid, discussion["owner"])
    assert saved["messages"][0]["status"] == "failed" and saved["pending_turn_id"] is None
    replay = list(chat.stream_chat_message(cid, QUESTION, "en", **kwargs))
    assert replay[-1][0] == "error" and replay[-1][1]["code"] == "chat_already_attempted"
    assert calls == [QUESTION] and database.usage_summary()["daily"]["attempts"] == 1
