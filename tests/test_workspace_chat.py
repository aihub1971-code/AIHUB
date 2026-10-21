"""Offline report discussion: real sessions, SQLite snapshots and mocked answers."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

from fastapi import HTTPException
import pytest

from app import public, report_store, runtime, workspace_chat
from app.registry import RETIRED_AGENT_IDS
from app.report_store import get_report_store
from app.storage import QuotaLimits, get_store
from tests.test_report_store import NOW, fake_pdf, reserve_for_job, synthetic_result
from tests.test_workspace import assert_workspace_public, second_visitor


PREFIX = "/v1/public/workspace/chat/conversations"
IRAN = "iran-news"
WORLD = "geopolitical-morning"


def session_owner(client, database):
    digest = hashlib.sha256(client.cookies.get(public.SESSION_COOKIE).encode()).hexdigest()
    with database._connect() as connection:
        row = connection.execute("SELECT session_id FROM public_sessions WHERE token_hash=?", (digest,)).fetchone()
    return "public:" + row["session_id"]


@pytest.fixture
def discussion(client):
    client.auth = None
    client.headers["Origin"] = "http://testserver"
    assert client.get("/v1/public/bootstrap").status_code == 200
    database = get_store()
    database._limits = QuotaLimits(50, "100", "1000")
    database.clock = lambda: NOW
    reports = get_report_store(database)
    return {"client": client, "database": database, "reports": reports,
            "owner": session_owner(client, database)}


def conversation(client):
    response = client.post("/v1/public/chat/conversations", json={"language": "en"})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def send(client, conversation_id, message="Explain the assessment", *, key="question-one", **body):
    return client.post(f"{PREFIX}/{conversation_id}/messages", headers={"Idempotency-Key": key},
                       json={"message": message, "language": "en", **body})


def complete_job(state, job_id, *, label="saved", pdf_failed=False, no_evidence=False):
    reports = state["reports"]
    job = reports.claim("fixture-worker", concurrency=2)
    assert job is not None and job["job_id"] == job_id
    report_id = reserve_for_job(reports, job)
    result = synthetic_result(job)
    result["title"] = f"Synthetic {label} report"
    result["sections"][0].update(id="executive_summary", body=f"Synthetic {label} finding; not live research.")
    result["sources"][0].update(url=f"https://fixture.example/{label}", excerpt=f"Synthetic {label} source evidence.")
    result["citation_mappings"]["s1"]["url"] = f"https://fixture.example/{label}"
    if no_evidence:
        # Mirror a completed empty result that still has an unused bibliography
        # entry. A source list alone must not authorize a researched answer.
        result["status"] = "no_evidence"
        result["items"] = []
        result["sections"] = [{"id": "executive_summary", "title": "Executive summary",
                               "body": "No relevant evidence was retrieved.", "source_ids": []}]
    result.update(api_key="private-result-sentinel", _research_provenance={"text": "private-research-sentinel"})
    reports.base_store.finish_run(report_id, result, estimated_cost_usd=".01", cost_tracking_complete=True)
    saved = reports.save_report(job_id, job["lease_token"], report_id, result)
    if pdf_failed:
        reports.fail(job_id, job["lease_token"], "pdf_generation_failed", pdf=True)
    else:
        metadata = fake_pdf(reports.pdf_attempt_path(report_id, job["lease_token"]))
        reports.finish_pdf(job_id, job["lease_token"], metadata)
    return saved


def save_report(state, agent_id=IRAN, *, label="saved", moment=None, owner_id=None, language="en"):
    submitted = state["reports"].submit(owner_id or state["owner"], agent_id, "Synthetic scope " + label,
                                         language, idempotency_key="seed-" + label, now=moment or NOW)
    return complete_job(state, submitted["job"]["job_id"], label=label)


def save_retired_report(state, monkeypatch, agent_id):
    """Seed a historical snapshot while simulating its former admission rules."""
    original_agent = report_store.get_agent
    original_capability = report_store.require_capability
    with monkeypatch.context() as legacy:
        legacy.setattr(report_store, "get_agent", lambda identifier:
                       {"id": agent_id, "enabled": True} if identifier == agent_id else original_agent(identifier))
        legacy.setattr(report_store, "require_capability", lambda identifier, *args:
                       None if identifier == agent_id else original_capability(identifier, *args))
        return save_report(state, agent_id, label="archived-" + agent_id)


@pytest.fixture
def provider(monkeypatch):
    calls = []
    state = {"calls": calls, "answer": "A synthetic explanation of the selected evidence.", "error": None}
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-workspace-key-not-a-credential")

    def model(key, payload):
        calls.append(deepcopy(payload))
        if state["error"]:
            raise state["error"]
        return {"content": state["answer"], "usage": {"input_tokens": 100, "output_tokens": 50,
                "usage_reported": True}, "tier_matches": True, "usage_bound_matches": True}

    monkeypatch.setattr(runtime, "_call_model", model)
    return state


def evidence(payload):
    for message in payload["input"]:
        content = message["content"]
        if "<UNTRUSTED_REPORT_JSON>" in content:
            return json.loads(content.split("<UNTRUSTED_REPORT_JSON>", 1)[1].split("</UNTRUSTED_REPORT_JSON>", 1)[0])
    raise AssertionError("The selected report evidence did not reach the answer request.")


def response_language_setting(payload):
    system = payload["input"][0]
    assert system["role"] == "system"
    return json.loads(system["content"].split("TRUSTED_RESPONSE_LANGUAGE_JSON:\n", 1)[1]
                      .split("\nCONTEXT_WINDOW_JSON:", 1)[0])


@pytest.mark.parametrize("selected_language,report_language,question,answer", [
    ("ar", "en", "Summarize this Iran report and explain its UAE implications using its saved evidence.",
     "هذه إجابة عربية اختبارية عن الأدلة المحفوظة."),
    ("en", "ar", "اشرح تقدير التقرير المحفوظ وآثاره على الإمارات.",
     "This is a synthetic English explanation of the saved evidence."),
    ("ar", "ar", "Explain this saved report. Please answer in English.",
     "This is a synthetic explicitly requested English explanation."),
    ("en", "en", "Explain this saved report. أجب باللغة العربية.",
     "هذه إجابة اختبارية بالعربية التي طلبها المستخدم صراحة."),
])
def test_request_language_reaches_trusted_model_context_independently_of_question_and_report(
        discussion, provider, selected_language, report_language, question, answer):
    """Exercise the actual route and saved key; offline mocks do not prove model language compliance."""
    selected = save_report(discussion, label="language-contract", language=report_language)
    provider["answer"] = answer
    client, cid = discussion["client"], conversation(discussion["client"])
    body = {"language": selected_language, "agent_id": IRAN, "report_id": selected["report_id"]}
    response = send(client, cid, question, **body)
    assert response.status_code == 200, response.text
    payload = provider["calls"][0]
    setting = response_language_setting(payload)
    assert setting == {"selected_language": selected_language,
                       "selected_language_name": "Modern Standard Arabic" if selected_language == "ar" else "English",
                       "override_policy": "explicit_current_user_request_only", "quoted_originals_allowed": True}
    assert payload["input"][-1] == {"role": "user", "content": question}
    assert evidence(payload)["reports"][0]["language"] == report_language
    assert payload["tools"] == [] and response.json()["context"]["fresh_research"] is False
    assert response.json()["message"]["content"] == answer
    replay = send(client, cid, question, **body)
    assert replay.status_code == 200 and replay.json()["idempotency_replayed"] is True
    assert len(provider["calls"]) == 1
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []


def test_selected_agent_automatically_uses_its_latest_report_and_returns_dated_context(discussion, provider):
    saved = save_report(discussion)
    client = discussion["client"]
    response = send(client, conversation(client), agent_id=IRAN)
    assert response.status_code == 200, response.text
    body = response.json()
    context = body["context"]
    assert context["report_id"] == saved["report_id"]
    assert context["report_ids"] == [saved["report_id"]]
    assert context["agent_ids"] == [IRAN]
    assert context["research_cutoffs"] == [{"report_id": saved["report_id"], "agent_id": IRAN,
                                           "cutoff": saved["research_window"]["end"]}]
    assert context["fresh_research"] is False
    payload = provider["calls"][0]
    assert payload["tools"] == [] and payload["tool_choice"] == "none"
    attached = evidence(payload)
    assert attached["report_ids"] == [saved["report_id"]]
    assert attached["reports"][0]["sections"][0]["body"].startswith("Synthetic saved finding")
    assert "automatically" in payload["input"][0]["content"].lower()
    for secret in ("private-result-sentinel", "private-research-sentinel", "synthetic-workspace-key"):
        assert secret not in json.dumps(payload) and secret not in response.text
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []
    assert_workspace_public(body)


def test_main_chat_routes_to_existing_relevant_domain_without_client_report_selection(discussion, provider):
    selected = save_report(discussion, "ubi-research", label="ubi")
    unrelated = save_report(discussion, IRAN, label="private-unrelated")
    client = discussion["client"]
    response = send(client, conversation(client), "Explain the basic income evidence and its limits")
    assert response.status_code == 200, response.text
    attached = evidence(provider["calls"][0])
    assert attached["agent_ids"] == ["ubi-research"]
    assert attached["report_ids"] == [selected["report_id"]]
    assert unrelated["report_id"] not in json.dumps(provider["calls"])


@pytest.mark.parametrize(("label", "age"), [
    ("Canada-programme-costs-employment-effects", timedelta(hours=1)),
    ("Finland-programme-costs-employment-effects", timedelta(days=3)),
])
def test_independent_evidence_question_does_not_reuse_unrelated_or_stale_agent_report(
        discussion, provider, label, age):
    previous = save_report(discussion, "ubi-research", label=label, moment=NOW - age)
    question = "What are Finland programme costs and employment effects?"
    response = send(discussion["client"], conversation(discussion["client"]), question,
                    agent_id="ubi-research", report_id=previous["report_id"])
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "researching"
    assert response.json()["job"]["topic"] == question
    assert response.json()["job"]["research_window"]["end"] == NOW.isoformat(timespec="microseconds")
    assert provider["calls"] == []
    assert len(discussion["reports"].list_jobs(discussion["owner"], active_only=True)) == 1


def test_current_relevant_evidence_can_answer_without_duplicate_research(discussion, provider):
    selected = save_report(discussion, "ubi-research", label="Finland-programme-costs-employment-effects")
    question = "What are Finland programme costs and employment effects?"
    response = send(discussion["client"], conversation(discussion["client"]), question, agent_id="ubi-research")
    assert response.status_code == 200, response.text
    assert response.json()["context"]["fresh_research"] is False
    assert evidence(provider["calls"][0])["report_ids"] == [selected["report_id"]]
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []


@pytest.mark.parametrize("with_report", [False, True])
@pytest.mark.parametrize("language,question", [
    ("en", "Who is the president of the European Central Bank?"),
    ("ar", "من يرأس البنك المركزي الأوروبي؟"),
    ("en", "Tell me about the European Central Bank's governing council."),
    ("ar", "أخبرني عن مجلس إدارة البنك المركزي الأوروبي."),
    ("en", "Has the European Central Bank changed its rate?"),
    ("ar", "هل غير البنك المركزي الأوروبي سعر الفائدة؟"),
])
def test_ordinary_factual_question_researches_beyond_displayed_report(
        discussion, provider, with_report, language, question):
    """A normal question must not require specialist keywords or a manual attachment."""
    agent_id = "economic-cultural-briefing"
    previous = save_report(discussion, agent_id, label="unrelated-tourism") if with_report else None
    response = send(discussion["client"], conversation(discussion["client"]), question,
                    agent_id=agent_id, language=language,
                    report_id=previous["report_id"] if previous else None,
                    topic=previous["topic"] if previous else "")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "researching"
    assert response.json()["job"]["topic"] == question
    assert provider["calls"] == []
    assert len(discussion["reports"].list_jobs(discussion["owner"], active_only=True)) == 1


@pytest.mark.parametrize("language,question", [
    ("en", "What is the latest on that?"),
    ("ar", "ما آخر مستجداته الآن؟"),
])
def test_current_followup_retains_displayed_subject_for_new_research(discussion, provider, language, question):
    previous = save_report(discussion, "economic-cultural-briefing", label="European-Central-Bank")
    response = send(discussion["client"], conversation(discussion["client"]), question,
                    agent_id="economic-cultural-briefing", language=language, report_id=previous["report_id"])
    assert response.status_code == 202, response.text
    topic = response.json()["job"]["topic"]
    assert previous["topic"] in topic and question in topic
    assert provider["calls"] == []


@pytest.mark.parametrize("question", [
    "Who is the president of the European Central Bank? Do not search the web.",
    "Without doing any new research, explain the latest information in this report.",
    "Don't browse; explain the current report's conclusion.",
    "Don't use the web. Who is the president of the European Central Bank?",
    "Do not use the internet. Who is the president of the European Central Bank?",
    "No need to search. Who is the president of the European Central Bank?",
    "من يرأس البنك المركزي الأوروبي؟ أجب بدون بحث جديد.",
])
def test_explicit_no_search_keeps_factual_questions_in_saved_context(discussion, provider, question):
    previous = save_report(discussion, "economic-cultural-briefing", label="saved-background")
    response = send(discussion["client"], conversation(discussion["client"]), question,
                    agent_id="economic-cultural-briefing", report_id=previous["report_id"])
    assert response.status_code == 200, response.text
    assert response.json()["context"]["fresh_research"] is False
    assert evidence(provider["calls"][0])["report_ids"] == [previous["report_id"]]
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []


@pytest.mark.parametrize("question", [
    "What are Finland programme costs and employment effects?",
    "What are Finland programme costs and their employment effects?",
    "I am preparing a briefing: what are the latest Finland basic-income evaluation findings?",
])
def test_chat_payload_from_old_ui_cannot_reapply_archived_topic_to_new_subject(discussion, provider, question):
    previous = save_report(discussion, "ubi-research", label="Canada-programme-costs-employment-effects",
                           moment=NOW - timedelta(days=3))
    # Older clients automatically sent both the displayed report and its form
    # topic, even when the new chat question introduced another country.
    response = send(discussion["client"], conversation(discussion["client"]), question,
                    agent_id="ubi-research", report_id=previous["report_id"], topic=previous["topic"])
    assert response.status_code == 202, response.text
    assert response.json()["job"]["topic"] == question
    assert "Canada" not in response.json()["job"]["topic"]
    assert provider["calls"] == []


def test_relevant_explicit_custom_topic_remains_part_of_new_research_request(discussion, provider):
    previous = save_report(discussion, "ubi-research", label="Canada-programme-costs-employment-effects")
    question = "Research Finland programme costs and employment effects."
    topic = "Finland 2017–2018 basic-income trial, with original evaluation and fiscal accounts"
    response = send(discussion["client"], conversation(discussion["client"]), question,
                    agent_id="ubi-research", report_id=previous["report_id"], topic=topic)
    assert response.status_code == 202, response.text
    task = response.json()["job"]["topic"]
    assert task.startswith(topic) and question in task and "Canada" not in task
    assert provider["calls"] == []


def test_pronoun_followup_can_discuss_relevant_dated_evidence_without_research(discussion, provider):
    previous = save_report(discussion, "ubi-research", label="Canada-programme-costs-employment-effects",
                           moment=NOW - timedelta(days=3))
    response = send(discussion["client"], conversation(discussion["client"]),
        "What were their programme costs and employment effects?", agent_id="ubi-research",
        report_id=previous["report_id"], topic=previous["topic"])
    assert response.status_code == 200, response.text
    assert response.json()["context"]["fresh_research"] is False
    assert evidence(provider["calls"][0])["report_ids"] == [previous["report_id"]]
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []


def test_explicit_dated_background_finds_relevant_older_owned_report(discussion, provider):
    background = save_report(discussion, "ubi-research",
        label="Finland-programme-costs-employment-effects-2017", moment=NOW - timedelta(days=3))
    latest = save_report(discussion, "ubi-research", label="Canada-programme-costs-employment-effects")
    question = "Explain the dated background on Finland programme costs and employment effects from 2017."
    response = send(discussion["client"], conversation(discussion["client"]), question, agent_id="ubi-research",
                    report_id=latest["report_id"])
    assert response.status_code == 200, response.text
    attached = evidence(provider["calls"][0])
    assert attached["report_ids"] == [background["report_id"]]
    assert attached["research_cutoffs"][0]["cutoff"] == background["research_window"]["end"]
    assert latest["report_id"] not in attached["report_ids"]
    assert response.json()["context"]["fresh_research"] is False
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []


@pytest.mark.parametrize("question", [
    "Hello", "What did we discuss about research costs and employment effects?",
    "Summarize the saved report and explain its employment effects.",
    "What does this report say about Iran?",
    "What are the findings in this report about Iran?",
    "ماذا يقول هذا التقرير عن إيران؟",
    "Remember that I prefer clear comparisons of programme costs.",
    "I am an adviser. What did we discuss about research costs?",
    "ما الذي ناقشنا بشأن الأبحاث وتكاليف البرامج؟",
])
def test_old_report_does_not_turn_greetings_memory_or_snapshot_discussion_into_research(
        discussion, provider, question):
    previous = save_report(discussion, "ubi-research", label="old-background", moment=NOW - timedelta(days=3))
    response = send(discussion["client"], conversation(discussion["client"]), question, agent_id="ubi-research")
    assert response.status_code == 200, response.text
    assert response.json()["context"]["fresh_research"] is False
    assert previous["report_id"] in response.json()["context"]["report_ids"]
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []


def test_explicit_background_question_cannot_use_another_visitors_matching_archive(discussion, provider):
    foreign = save_report(discussion, "ubi-research", label="Finland-programme-costs-employment-effects-2017",
                          moment=NOW - timedelta(days=3), owner_id="public:another-visitor")
    save_report(discussion, "ubi-research", label="Canada-programme-costs-employment-effects")
    question = "Explain the dated background on Finland programme costs and employment effects from 2017."
    response = send(discussion["client"], conversation(discussion["client"]), question, agent_id="ubi-research")
    assert response.status_code == 202, response.text
    assert foreign["report_id"] not in response.text and provider["calls"] == []


def test_follow_up_inherits_its_owned_snapshot_without_an_attachment_prompt(discussion, provider):
    selected = save_report(discussion)
    client, cid = discussion["client"], conversation(discussion["client"])
    assert send(client, cid, agent_id=IRAN).status_code == 200
    response = send(client, cid, "Explain that conclusion in Arabic", key="follow-up", language="ar")
    assert response.status_code == 200, response.text
    attached = evidence(provider["calls"][1])
    assert attached["report_ids"] == [selected["report_id"]]
    assert response.json()["context"]["history_messages_included"] == 2
    assert len(provider["calls"][1]["input"]) > len(provider["calls"][0]["input"])


@pytest.mark.parametrize("agent_id", sorted(RETIRED_AGENT_IDS))
def test_retired_owned_snapshot_discussion_and_followup_do_not_require_active_profile(
        discussion, provider, monkeypatch, agent_id):
    archived = save_retired_report(discussion, monkeypatch, agent_id)
    original_profile = workspace_chat.get_profile
    def active_profile(identifier):
        assert identifier not in RETIRED_AGENT_IDS, "Archive discussion requested a retired active profile."
        return original_profile(identifier)
    monkeypatch.setattr(workspace_chat, "get_profile", active_profile)
    client, cid = discussion["client"], conversation(discussion["client"])
    response = send(client, cid, "Explain the saved assessment", agent_id=agent_id,
                    report_id=archived["report_id"])
    assert response.status_code == 200, response.text
    assert response.json()["context"]["report_ids"] == [archived["report_id"]]
    assert response.json()["context"]["agent_ids"] == [agent_id]
    assert response.json()["context"]["fresh_research"] is False
    followup = send(client, cid, "Search for the latest developments since this report", key="follow-up",
                    agent_id=agent_id)
    assert followup.status_code == 200, followup.text
    assert followup.json()["context"]["report_id"] == archived["report_id"]
    assert followup.json()["context"]["fresh_research"] is False
    assert evidence(provider["calls"][1])["reports"][0]["agent_id"] == agent_id
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []
    assert discussion["database"].usage_summary()["daily"]["attempts"] == 1
    with second_visitor() as other:
        denied = send(other, conversation(other), report_id=archived["report_id"], agent_id=agent_id)
        assert denied.status_code == 404
    assert len(provider["calls"]) == 2


@pytest.mark.parametrize("agent_id", sorted(RETIRED_AGENT_IDS))
def test_retired_assistant_selection_without_an_owned_snapshot_is_rejected(discussion, provider, agent_id):
    client = discussion["client"]
    response = send(client, conversation(client), "Search for the latest information", agent_id=agent_id)
    assert response.status_code == 404
    assert provider["calls"] == []
    assert discussion["reports"].list_jobs(discussion["owner"]) == []
    assert discussion["database"].usage_summary()["daily"]["attempts"] == 0


def test_persisted_fresh_plan_from_retired_assistant_uses_owned_archive_without_new_work(
        discussion, provider, monkeypatch):
    agent_id = "pre-meeting-briefing"
    archived = save_retired_report(discussion, monkeypatch, agent_id)
    client, cid = discussion["client"], conversation(discussion["client"])
    body = workspace_chat.WorkspaceMessage(message="Refresh this report with current developments",
        language="en", agent_id=agent_id, report_id=archived["report_id"])
    binding, digest = workspace_chat._binding(discussion["database"], discussion["reports"], discussion["owner"],
                                              cid, "before-retirement", body)
    old_plan = {**json.loads(binding["plan_json"]), "fresh": True}
    with discussion["database"]._transaction() as connection:
        connection.execute("UPDATE workspace_chat_requests SET plan_json=? WHERE owner_id=? AND conversation_id=? AND key_hash=?",
                           (json.dumps(old_plan), discussion["owner"], cid, digest))
    response = send(client, cid, body.message, key="before-retirement", agent_id=agent_id,
                    report_id=archived["report_id"])
    assert response.status_code == 200, response.text
    assert response.json()["context"]["fresh_research"] is False
    assert response.json()["context"]["report_id"] == archived["report_id"]
    assert len(provider["calls"]) == 1
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []


def test_comparison_uses_previous_same_agent_and_latest_other_agent_snapshots(discussion, provider):
    previous = save_report(discussion, label="prior", moment=NOW - timedelta(hours=2))
    selected = save_report(discussion, label="selected", moment=NOW - timedelta(hours=1))
    other = save_report(discussion, WORLD, label="world", moment=NOW)
    client = discussion["client"]
    response = send(client, conversation(client), "Compare this Iran assessment with the previous report and global assessment",
                    agent_id=IRAN, report_id=selected["report_id"])
    assert response.status_code == 200, response.text
    attached = evidence(provider["calls"][0])
    assert attached["report_ids"] == [selected["report_id"], other["report_id"], previous["report_id"]]
    assert len({item["cutoff"] for item in attached["research_cutoffs"]}) == 3
    assert attached["fresh_research"] is False


def test_preflight_retry_keeps_pinned_snapshot_when_a_newer_report_appears(discussion, provider, monkeypatch):
    original = save_report(discussion, label="original")
    client, cid = discussion["client"], conversation(discussion["client"])
    monkeypatch.delenv("OPENAI_API_KEY")
    pending = send(client, cid, agent_id=IRAN)
    assert pending.status_code == 503 and not provider["calls"]
    newer = save_report(discussion, label="newer", moment=NOW + timedelta(minutes=10))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-workspace-key-not-a-credential")
    answer = send(client, cid, agent_id=IRAN)
    assert answer.status_code == 200, answer.text
    assert evidence(provider["calls"][0])["report_ids"] == [original["report_id"]]
    assert newer["report_id"] not in json.dumps(provider["calls"])
    replay = send(client, cid, agent_id=IRAN)
    assert replay.status_code == 200 and replay.json()["idempotency_replayed"] is True
    assert replay.json()["turn_id"] == answer.json()["turn_id"]
    conflict = send(client, cid, "A different question", agent_id=IRAN)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert len(provider["calls"]) == 1


@pytest.mark.parametrize("pdf_failed", [False, True])
@pytest.mark.parametrize("language", ["en", "ar"])
def test_fresh_question_waits_then_answers_from_new_cutoff_and_saved_old_snapshot(discussion, provider, pdf_failed, language):
    previous = save_report(discussion, label="older", moment=NOW - timedelta(hours=1))
    client, cid = discussion["client"], conversation(discussion["client"])
    question = "What has changed in Iran since this report?"
    first = send(client, cid, question, agent_id=IRAN, report_id=previous["report_id"], language=language)
    assert first.status_code == 202, first.text
    job = first.json()["job"]
    assert first.json()["status"] == "researching" and not provider["calls"]
    assert discussion["database"].usage_summary()["daily"]["chat_turns"] == 0
    assert_workspace_public(first.json())
    duplicate = send(client, cid, question, agent_id=IRAN, report_id=previous["report_id"], fresh_job_id=job["job_id"], language=language)
    assert duplicate.status_code == 202 and duplicate.json()["job"]["job_id"] == job["job_id"]
    assert len(discussion["reports"].list_jobs(discussion["owner"], active_only=True)) == 1
    fresh = complete_job(discussion, job["job_id"], label="fresh", pdf_failed=pdf_failed)
    answer = send(client, cid, question, agent_id=IRAN, report_id=previous["report_id"], fresh_job_id=job["job_id"], language=language)
    assert answer.status_code == 200, answer.text
    attached = evidence(provider["calls"][0])
    assert attached["report_ids"] == [fresh["report_id"], previous["report_id"]]
    assert attached["fresh_research"] is True
    assert attached["research_cutoffs"][0]["cutoff"] > attached["research_cutoffs"][1]["cutoff"]
    assert answer.json()["context"]["report_id"] == fresh["report_id"]
    assert response_language_setting(provider["calls"][0])["selected_language"] == language
    before = discussion["database"].usage_summary()
    replay = send(client, cid, question, agent_id=IRAN, report_id=previous["report_id"], fresh_job_id=job["job_id"], language=language)
    assert replay.status_code == 200 and replay.json()["idempotency_replayed"] is True
    assert len(provider["calls"]) == 1 and discussion["database"].usage_summary() == before
    assert discussion["reports"].get_report(discussion["owner"], previous["report_id"])["research_window"] == previous["research_window"]


def test_unavailable_current_report_enqueues_main_domain_research_without_paid_answer(discussion, provider):
    client = discussion["client"]
    response = send(client, conversation(client), "Explain the education policy evidence")
    assert response.status_code == 202, response.text
    assert response.json()["agent_id"] == "education-observatory"
    assert response.json()["job"]["agent_id"] == "education-observatory"
    assert not provider["calls"]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_fresh_empty_research_returns_persisted_free_notice_without_a_provider_call(
        discussion, provider, monkeypatch, language):
    client, cid = discussion["client"], conversation(discussion["client"])
    question = "What is the European Central Bank's inflation target?"
    first = send(client, cid, question, agent_id="economic-cultural-briefing", language=language)
    assert first.status_code == 202
    job = first.json()["job"]
    saved = complete_job(discussion, job["job_id"], label="unrelated-metadata", no_evidence=True)
    monkeypatch.delenv("OPENAI_API_KEY")
    response = send(client, cid, question, agent_id="economic-cultural-briefing", language=language,
                    fresh_job_id=job["job_id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["context"]["fresh_research"] is True
    assert body["context"]["report_id"] == saved["report_id"]
    assert ("couldn't verify" if language == "en" else "لم أتمكن من التحقق") in body["message"]["content"]
    assert "2%" not in body["message"]["content"] and "fixture.example" not in body["message"]["content"]
    replay = send(client, cid, question, agent_id="economic-cultural-briefing", language=language,
                  fresh_job_id=job["job_id"])
    assert replay.status_code == 200 and replay.json()["idempotency_replayed"] is True
    assert replay.json()["message"] == body["message"]
    assert provider["calls"] == []
    assert discussion["database"].usage_summary()["daily"]["chat_estimated_cost_usd"] == 0
    assert len(discussion["database"].get_chat_conversation(cid, discussion["owner"])["messages"]) == 2
    assert_workspace_public(body)


@pytest.mark.parametrize("cited", [False, True])
def test_fresh_answer_needs_a_citation_to_supported_retrieved_evidence(discussion, provider, cited):
    client, cid = discussion["client"], conversation(discussion["client"])
    question = "What is happening in Iran now?"
    first = send(client, cid, question, agent_id=IRAN)
    job = first.json()["job"]
    complete_job(discussion, job["job_id"], label="fresh-support")
    provider["answer"] = "A synthetic factual finding." + (
        " [Source](https://fixture.example/fresh-support)" if cited else "")
    response = send(client, cid, question, agent_id=IRAN, fresh_job_id=job["job_id"])
    assert response.status_code == 200, response.text
    content = response.json()["message"]["content"]
    assert content == provider["answer"] if cited else "couldn't verify" in content
    assert len(provider["calls"]) == 1


def test_failed_fresh_job_keeps_previous_report_and_never_retries_research_or_answer(discussion, provider):
    old = save_report(discussion, label="old")
    client, cid = discussion["client"], conversation(discussion["client"])
    question = "What is happening in Iran now?"
    first = send(client, cid, question, agent_id=IRAN).json()
    claim = discussion["reports"].claim("fixture-worker")
    assert claim["job_id"] == first["job"]["job_id"]
    discussion["reports"].fail(claim["job_id"], claim["lease_token"], "provider_timeout")
    for _ in range(2):
        response = send(client, cid, question, agent_id=IRAN, fresh_job_id=claim["job_id"])
        assert response.status_code == 502 and response.json()["detail"]["code"] == "chat_research_failed"
    assert not provider["calls"]
    assert discussion["reports"].latest_report(discussion["owner"], IRAN)["report_id"] == old["report_id"]
    assert len(discussion["reports"].list_jobs(discussion["owner"])) == 2


def test_foreign_reports_and_conversations_are_rejected_before_context_or_provider_access(discussion, provider):
    saved = save_report(discussion, label="alice-private")
    cid = conversation(discussion["client"])
    with second_visitor() as stranger:
        foreign_conversation = send(stranger, cid, "Explain the report", report_id=saved["report_id"])
        assert foreign_conversation.status_code == 404
        own_cid = conversation(stranger)
        foreign_report = send(stranger, own_cid, "Explain the report", report_id=saved["report_id"])
        assert foreign_report.status_code == 404
        assert saved["report_id"] not in foreign_report.text
        assert discussion["database"].get_chat_conversation(own_cid, session_owner(stranger, discussion["database"]))["messages"] == []
    assert not provider["calls"]


def test_same_client_message_key_is_isolated_by_session_and_conversation(discussion, provider):
    client = discussion["client"]
    first = send(client, conversation(client), "How do I use AI HUB?", key="shared-key")
    assert first.status_code == 200, first.text
    with second_visitor() as stranger:
        second = send(stranger, conversation(stranger), "How do I use AI HUB?", key="shared-key")
    assert second.status_code == 200, second.text
    assert first.json()["turn_id"] != second.json()["turn_id"] and len(provider["calls"]) == 2


def test_selected_report_agent_mismatch_and_unbound_fresh_job_are_rejected(discussion, provider):
    selected = save_report(discussion)
    client, cid = discussion["client"], conversation(discussion["client"])
    mismatch = send(client, cid, report_id=selected["report_id"], agent_id=WORLD)
    assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "report_agent_mismatch"
    unbound = send(client, cid, report_id=selected["report_id"], fresh_job_id="job_" + "f" * 32)
    assert unbound.status_code == 409 and unbound.json()["detail"]["code"] == "chat_research_mismatch"
    assert not provider["calls"]


def test_verified_links_are_allowed_but_unsupported_links_never_save_an_assistant_answer(discussion, provider):
    selected = save_report(discussion)
    client, cid = discussion["client"], conversation(discussion["client"])
    provider["answer"] = "See the [saved source](https://fixture.example/saved)."
    good = send(client, cid, report_id=selected["report_id"])
    assert good.status_code == 200, good.text
    provider["answer"] = "See [unverified material](https://unverified.example/invented)."
    bad = send(client, cid, key="bad-link", report_id=selected["report_id"])
    assert bad.status_code == 502 and bad.json()["detail"]["code"] == "chat_source_mismatch"
    assert "unverified.example" not in bad.text
    saved = discussion["database"].get_chat_conversation(cid, discussion["owner"])
    assert len(saved["messages"]) == 3 and saved["messages"][-1]["status"] == "failed"
    assert "unverified.example" not in json.dumps(saved)
    replay = send(client, cid, key="bad-link", report_id=selected["report_id"])
    assert replay.status_code == 409 and replay.json()["detail"]["code"] == "chat_already_attempted"
    assert len(provider["calls"]) == 2
    assert discussion["database"].usage_summary()["daily"]["chat_failed"] == 1


@pytest.mark.parametrize("message", ["Summarize today's report", "Explain the current report's assessment",
                                     "Summarize the latest report", "لخص تقرير اليوم", "اشرح تقييم التقرير الحالي",
                                     "لخص أحدث تقرير"])
def test_questions_about_saved_report_do_not_trigger_unrequested_research(message):
    assert workspace_chat.needs_fresh_research(message, IRAN, True) is False


@pytest.mark.parametrize("message", ["What is happening in Iran now?", "What changed since this report?",
                                     "What do recent reports say about Iran?",
                                     "ما آخر المستجدات منذ التقرير؟", "ابحث عن تطورات إيران اليوم",
                                     "What are the new developments?", "Summarize the latest developments", "ما الجديد؟"])
def test_genuinely_fresh_questions_request_new_research(message):
    assert workspace_chat.needs_fresh_research(message, IRAN, True) is True


@pytest.mark.parametrize("message,expected", [
    ("Explain the history reading guide", ["historical-reading-companion"]),
    ("Explain the education report from Tirana", ["education-observatory"]),
    ("Explain geopolitical and economic risks", [WORLD, "economic-cultural-briefing"]),
    ("Explain the Iran assessment", [IRAN]),
    ("قارن تقييم إيران بالتقييم الجيوسياسي العالمي", [IRAN, WORLD]),
])
def test_domain_routing_distinguishes_whole_terms_from_intentional_stems(message, expected):
    assert workspace_chat.route_agents(message) == expected


@pytest.mark.parametrize("agent_id,message", [
    ("mom-agent", "Prepare meeting minutes"), ("whatsapp-briefing", "Summarize WhatsApp messages"),
    ("literary-translation", "Translate this passage"), ("pre-meeting-briefing", "Prepare for a meeting"),
])
def test_retired_assistants_are_never_selected_by_main_chat_routing(agent_id, message):
    assert workspace_chat.route_agents(message, agent_id) == []
    assert workspace_chat.needs_fresh_research("Search for the latest developments", agent_id, True) is False
    assert workspace_chat.needs_fresh_research("Search for the latest developments", agent_id, False) is False


def test_workspace_platform_help_uses_active_profiles_and_current_news_cap(monkeypatch):
    monkeypatch.setattr(workspace_chat.research, "MAX_NEWS_ITEMS", 100)
    context = workspace_chat.platform_context()
    assert len(context["agents"]) == 7
    assert RETIRED_AGENT_IDS.isdisjoint(agent["id"] for agent in context["agents"])
    assert context["news"]["maximum_items_after_validation_and_dedup"] == 100
    assert "Retired agents cannot generate" in context["archives"]


def test_platform_help_has_no_research_job_and_does_not_claim_old_manual_attachment_flow(discussion, provider):
    client = discussion["client"]
    response = send(client, conversation(client), "How can I generate a fresh report?", agent_id=IRAN)
    assert response.status_code == 200, response.text
    assert not discussion["reports"].list_jobs(discussion["owner"], active_only=True)
    payload = json.dumps(provider["calls"][0], ensure_ascii=False)
    assert "workspace_version" in payload and "Generate fresh report" in payload
    assert "No manual" in payload or "automatic" in payload
    assert "NewsData" in payload and "48 hours" not in payload
    assert_workspace_public(response.json())


def test_arbitrary_verification_fields_are_not_sent_as_model_context(discussion):
    saved = save_report(discussion)
    record = deepcopy(saved)
    record["result"]["sources"][0]["publication_verification"] = {
        "method": "fixture_timestamp", "verified": True, "timestamp": NOW.isoformat(),
        "api_key": "private-verification-sentinel", "internal_path": "/var/private/provider-key",
    }
    scoped = workspace_chat._scoped_report(record, "Explain the report")
    assert scoped["sources"][0]["publication_verification"] == {
        "method": "fixture_timestamp", "verified": True, "timestamp": NOW.isoformat()}
    assert "private-verification" not in json.dumps(scoped) and "internal_path" not in json.dumps(scoped)


def test_unbound_fresh_job_id_cannot_create_a_research_job(discussion, provider):
    client = discussion["client"]
    response = send(client, conversation(client), "What is happening in Iran now?", agent_id=IRAN,
                    fresh_job_id="job_" + "f" * 32)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "chat_research_mismatch"
    assert discussion["reports"].list_jobs(discussion["owner"]) == [] and not provider["calls"]


@pytest.mark.parametrize("answer", [
    "See [the saved paper](https://fixture.example/news_(briefing)).",
    "The source is https://fixture.example/news_(briefing).",
])
def test_verified_url_with_literal_parentheses_survives_markdown_closing_punctuation(answer):
    context = {"reports": [{"sources": [{"url": "https://fixture.example/news_(briefing)"}]}]}
    workspace_chat.validate_answer_links(answer, context)


@pytest.mark.parametrize("answer", [
    "See [unverified](https://fixture.example/news_(briefing)/invented).",
    "See [unsafe](javascript:alert(1)).",
    "See [unverified](//unverified.example/article).",
])
def test_unsupported_markdown_destinations_are_rejected(answer):
    context = {"reports": [{"sources": [{"url": "https://fixture.example/news_(briefing)"}]}]}
    with pytest.raises(HTTPException) as error:
        workspace_chat.validate_answer_links(answer, context)
    assert error.value.status_code == 502 and error.value.detail["code"] == "chat_source_mismatch"
