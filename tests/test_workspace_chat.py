"""Offline report discussion: real sessions, SQLite snapshots and mocked answers."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

from fastapi import HTTPException
import pytest

from app import public, runtime, workspace_chat
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


def complete_job(state, job_id, *, label="saved", pdf_failed=False):
    reports = state["reports"]
    job = reports.claim("fixture-worker", concurrency=2)
    assert job is not None and job["job_id"] == job_id
    report_id = reserve_for_job(reports, job)
    result = synthetic_result(job)
    result["title"] = f"Synthetic {label} report"
    result["sections"][0].update(id="executive_summary", body=f"Synthetic {label} finding; not live research.")
    result["sources"][0].update(url=f"https://fixture.example/{label}", excerpt=f"Synthetic {label} source evidence.")
    result["citation_mappings"]["s1"]["url"] = f"https://fixture.example/{label}"
    result.update(api_key="private-result-sentinel", _research_provenance={"text": "private-research-sentinel"})
    reports.base_store.finish_run(report_id, result, estimated_cost_usd=".01", cost_tracking_complete=True)
    saved = reports.save_report(job_id, job["lease_token"], report_id, result)
    if pdf_failed:
        reports.fail(job_id, job["lease_token"], "pdf_generation_failed", pdf=True)
    else:
        metadata = fake_pdf(reports.pdf_attempt_path(report_id, job["lease_token"]))
        reports.finish_pdf(job_id, job["lease_token"], metadata)
    return saved


def save_report(state, agent_id=IRAN, *, label="saved", moment=None, owner_id=None):
    submitted = state["reports"].submit(owner_id or state["owner"], agent_id, "Synthetic scope " + label,
                                         "en", idempotency_key="seed-" + label, now=moment or NOW)
    return complete_job(state, submitted["job"]["job_id"], label=label)


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
def test_fresh_question_waits_then_answers_from_new_cutoff_and_saved_old_snapshot(discussion, provider, pdf_failed):
    previous = save_report(discussion, label="older", moment=NOW - timedelta(hours=1))
    client, cid = discussion["client"], conversation(discussion["client"])
    question = "What has changed in Iran since this report?"
    first = send(client, cid, question, agent_id=IRAN, report_id=previous["report_id"])
    assert first.status_code == 202, first.text
    job = first.json()["job"]
    assert first.json()["status"] == "researching" and not provider["calls"]
    assert discussion["database"].usage_summary()["daily"]["chat_turns"] == 0
    assert_workspace_public(first.json())
    duplicate = send(client, cid, question, agent_id=IRAN, report_id=previous["report_id"], fresh_job_id=job["job_id"])
    assert duplicate.status_code == 202 and duplicate.json()["job"]["job_id"] == job["job_id"]
    assert len(discussion["reports"].list_jobs(discussion["owner"], active_only=True)) == 1
    fresh = complete_job(discussion, job["job_id"], label="fresh", pdf_failed=pdf_failed)
    answer = send(client, cid, question, agent_id=IRAN, report_id=previous["report_id"], fresh_job_id=job["job_id"])
    assert answer.status_code == 200, answer.text
    attached = evidence(provider["calls"][0])
    assert attached["report_ids"] == [fresh["report_id"], previous["report_id"]]
    assert attached["fresh_research"] is True
    assert attached["research_cutoffs"][0]["cutoff"] > attached["research_cutoffs"][1]["cutoff"]
    assert answer.json()["context"]["report_id"] == fresh["report_id"]
    before = discussion["database"].usage_summary()
    replay = send(client, cid, question, agent_id=IRAN, report_id=previous["report_id"], fresh_job_id=job["job_id"])
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


def test_platform_help_has_no_research_job_and_does_not_claim_old_manual_attachment_flow(discussion, provider):
    client = discussion["client"]
    response = send(client, conversation(client), "How can I generate a fresh report?", agent_id=IRAN)
    assert response.status_code == 200, response.text
    assert not discussion["reports"].list_jobs(discussion["owner"], active_only=True)
    payload = json.dumps(provider["calls"][0], ensure_ascii=False)
    assert "workspace_version" in payload and "Generate fresh report" in payload
    assert "No manual" in payload or "automatic" in payload
    assert "NewsData" not in payload and "48 hours" not in payload
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
