"""Agent threads, reviewed memory and real HTTP SSE transport (offline fixtures)."""
from copy import deepcopy
import json

import httpx
import pytest

from app import chat, workspace_chat
from app.storage import Store, get_store
from tests.test_workspace import second_visitor
from tests.test_workspace_chat import (
    discussion, provider, evidence, save_report, session_owner, complete_job,
)

PREFIX = "/v1/public/workspace"


def agent_thread(client, agent_id="ubi-research", language="en"):
    response = client.get(f"{PREFIX}/agents/{agent_id}/conversation?language={language}")
    assert response.status_code == 200, response.text
    return response.json()


def send_stream(client, conversation, message="Hello", *, key="stream-one", **body):
    return client.post(f"{PREFIX}/conversations/{conversation}/messages/stream",
        headers={"Idempotency-Key": key}, json={"message": message, "language": "en", **body})


def events(response):
    result = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads("\n".join(line[6:] for line in lines if line.startswith("data: ")))
        result.append((name, data))
    return result


def finished(text):
    return {"type": "response.completed", "response": {
        "status": "completed", "service_tier": "default", "output": [{"type": "message", "role": "assistant",
        "status": "completed", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 120, "output_tokens": 20}}}


class ProviderChunks(httpx.SyncByteStream):
    def __init__(self, frames, observations):
        self.frames, self.observations = frames, observations

    def __iter__(self):
        for frame in self.frames:
            self.observations.append(frame["type"])
            # Exercise actual SSE framing across several byte chunks.
            data = ("event: " + frame["type"] + "\ndata: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode()
            middle = max(1, len(data) // 2)
            yield data[:middle]
            yield data[middle:]


@pytest.fixture
def streaming_provider(monkeypatch):
    original = httpx.Client
    requests, received = [], []
    state = {"requests": requests, "received": received,
             "frames": [{"type": "response.output_text.delta", "delta": "A clear "},
                        {"type": "response.output_text.delta", "delta": "answer."},
                        finished("A clear answer.")], "http_error": None}
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-stream-key-not-a-credential")

    def dispatch(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        requests.append(json.loads(request.content))
        if state["http_error"]:
            code, payload = state["http_error"]
            return httpx.Response(code, json=payload)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              stream=ProviderChunks(state["frames"], received))
    monkeypatch.setattr(chat.httpx, "Client", lambda *args, **kwargs:
                        original(transport=httpx.MockTransport(dispatch), **kwargs))
    return state


def test_passive_agent_open_restores_independent_threads_without_research_or_provider(discussion, provider):
    client, store = discussion["client"], discussion["database"]
    first = agent_thread(client)
    other = agent_thread(client, "education-observatory")
    reopened = agent_thread(client)
    assert first["conversation_id"] == reopened["conversation_id"] != other["conversation_id"]
    assert first["agent_id"] == "ubi-research" and other["agent_id"] == "education-observatory"
    assert first["messages"] == other["messages"] == []
    restarted = Store(store.path, store.limits, clock=store.clock)
    restarted.initialize()
    assert restarted.agent_conversation(discussion["owner"], "ubi-research")["conversation_id"] == first["conversation_id"]
    assert discussion["reports"].list_jobs(discussion["owner"]) == []
    assert store.usage_summary()["daily"]["chat_turns"] == 0
    assert provider["calls"] == []


def test_retired_agent_cannot_bootstrap_new_thread(discussion):
    for agent_id in ("story-moodboard", "decision-briefing"):
        assert discussion["client"].get(f"{PREFIX}/agents/{agent_id}/conversation").status_code == 404


def test_provider_delta_arrives_before_final_provider_frame_is_consumed(streaming_provider):
    stream = chat._stream_model("synthetic-stream-key-not-a-credential", {
        "model": "gpt-6-sol", "input": [{"role": "user", "content": "Hello"}], "max_output_tokens": 100,
        "tools": [], "tool_choice": "none", "service_tier": "default"})
    assert next(stream) == ("delta", {"delta": "A clear "})
    assert streaming_provider["received"] == ["response.output_text.delta"]
    remainder = list(stream)
    assert remainder[-1][0] == "result" and remainder[-1][1]["content"] == "A clear answer."
    assert streaming_provider["requests"][0]["stream"] is True


def test_streamed_reply_persists_and_replays_without_another_provider_request(discussion, streaming_provider):
    client = discussion["client"]
    thread = agent_thread(client)
    response = send_stream(client, thread["conversation_id"], agent_id="ubi-research")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    data = events(response)
    assert [name for name, _ in data] == ["status", "delta", "delta", "complete"]
    assert data[-1][1]["message"]["content"] == "A clear answer."
    assert data[-1][1]["conversation"]["agent_id"] == "ubi-research"
    restored = agent_thread(client)
    assert [message["role"] for message in restored["messages"]] == ["user", "assistant"]
    replay = send_stream(client, thread["conversation_id"], agent_id="ubi-research")
    assert events(replay)[0][0] == "complete" and events(replay)[0][1]["idempotency_replayed"] is True
    assert len(streaming_provider["requests"]) == 1
    assert discussion["reports"].list_jobs(discussion["owner"]) == []


def test_stream_error_never_saves_partial_answer_as_completed(discussion, streaming_provider):
    streaming_provider["frames"] = [{"type": "response.output_text.delta", "delta": "Unfinished"}]
    thread = agent_thread(discussion["client"])
    response = send_stream(discussion["client"], thread["conversation_id"])
    data = events(response)
    assert [name for name, _ in data] == ["status", "delta", "error"]
    assert data[-1][1]["code"] == "stream_interrupted"
    restored = agent_thread(discussion["client"])
    assert len(restored["messages"]) == 1 and restored["messages"][0]["role"] == "user"
    assert restored["messages"][0]["status"] == "failed" and restored["pending_turn_id"] is None
    assert events(send_stream(discussion["client"], thread["conversation_id"]))[-1][1]["code"] == "chat_already_attempted"
    assert len(streaming_provider["requests"]) == 1


def test_stream_provider_quota_error_is_distinct_and_does_not_leak_provider_body(discussion, streaming_provider):
    streaming_provider["http_error"] = (429, {"error": {"code": "insufficient_quota", "message": "private quota sentinel"}})
    thread = agent_thread(discussion["client"])
    result = send_stream(discussion["client"], thread["conversation_id"])
    assert events(result)[-1][1]["code"] == "openai_quota_exceeded"
    assert "private quota sentinel" not in result.text and "synthetic-stream-key" not in result.text


def test_stream_unattested_link_is_not_committed(discussion, streaming_provider):
    text = "Read [invented evidence](https://unverified.example/claim)."
    streaming_provider["frames"] = [{"type": "response.output_text.delta", "delta": text}, finished(text)]
    thread = agent_thread(discussion["client"])
    response = send_stream(discussion["client"], thread["conversation_id"])
    assert events(response)[-1][1]["code"] == "chat_source_mismatch"
    assert not any(message["role"] == "assistant" for message in agent_thread(discussion["client"])["messages"])


def test_agent_binding_and_visitor_isolation_checked_before_provider(discussion, streaming_provider):
    first = agent_thread(discussion["client"])
    mismatch = send_stream(discussion["client"], first["conversation_id"], agent_id="iran-news")
    assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "conversation_agent_mismatch"
    with second_visitor() as stranger:
        second = agent_thread(stranger)
        assert second["conversation_id"] != first["conversation_id"]
        assert stranger.get(f"{PREFIX}/conversations/{first['conversation_id']}").status_code == 404
        assert send_stream(stranger, first["conversation_id"]).status_code == 404
    assert streaming_provider["requests"] == []


def test_reviewed_memory_is_shared_only_after_approval_and_never_across_visitors(discussion, streaming_provider):
    client = discussion["client"]
    ubi = agent_thread(client)
    education = agent_thread(client, "education-observatory")
    proposed = client.post(f"{PREFIX}/memory", json={"kind": "preference", "content": "Focus on evaluated programmes and employment effects."})
    assert proposed.status_code == 201
    memory = proposed.json()
    assert memory["status"] == "proposed"
    before = discussion["database"].shared_workspace_context(discussion["owner"], ubi["conversation_id"], "employment programmes")
    assert before["notes"] == []
    approved = client.patch(f"{PREFIX}/memory/{memory['memory_id']}", json={"action": "approve"})
    assert approved.json()["status"] == "approved"
    result = send_stream(client, education["conversation_id"], "How should we work together?", agent_id="education-observatory")
    assert events(result)[-1][0] == "complete"
    attached = evidence(streaming_provider["requests"][0])
    assert attached["shared_memory"]["notes"][0]["content"] == memory["content"]
    assert attached["shared_memory"]["notes"][0]["evidence_status"] == "visitor_approved"
    with second_visitor() as stranger:
        other = agent_thread(stranger)
        assert stranger.get(f"{PREFIX}/memory").json()["items"] == []
        assert stranger.patch(f"{PREFIX}/memory/{memory['memory_id']}", json={"action": "approve"}).status_code == 404
        send_stream(stranger, other["conversation_id"], "How should we work together?", key="stranger")
        assert memory["content"] not in json.dumps(streaming_provider["requests"][-1])
    client.patch(f"{PREFIX}/memory/{memory['memory_id']}", json={"action": "reject"})
    assert client.get(f"{PREFIX}/memory").json()["items"] == []
    assert discussion["database"].shared_workspace_context(discussion["owner"], ubi["conversation_id"], "employment")["notes"] == []


def test_prior_agent_conversation_and_cited_report_are_shared_with_owner_checks(discussion, streaming_provider):
    client = discussion["client"]
    saved = save_report(discussion, "ubi-research", label="programmes")
    ubi = agent_thread(client)
    first = send_stream(client, ubi["conversation_id"], "Explain the saved report", agent_id="ubi-research")
    assert events(first)[-1][0] == "complete"
    education = agent_thread(client, "education-observatory")
    second = send_stream(client, education["conversation_id"], "What did we discuss about programmes?", key="cross-agent", agent_id="education-observatory")
    assert events(second)[-1][0] == "complete"
    attached = evidence(streaming_provider["requests"][-1])
    assert attached["shared_memory"]["conversations"][0]["agent_id"] == "ubi-research"
    assert saved["report_id"] in attached["report_ids"]
    assert "https://fixture.example/programmes" in json.dumps(attached["reports"])
    with second_visitor() as stranger:
        foreign = agent_thread(stranger, "education-observatory")
        send_stream(stranger, foreign["conversation_id"], "What did we discuss about programmes?", key="foreign")
        content = json.dumps(streaming_provider["requests"][-1])
        assert saved["report_id"] not in content and "fixture.example/programmes" not in content


def test_memory_sources_must_belong_to_this_visitors_reports(discussion):
    client = discussion["client"]
    saved = save_report(discussion, label="owned-source")
    invalid = client.post(f"{PREFIX}/memory", json={"kind": "knowledge", "content": "A claim", "source_urls": ["https://unverified.example/claim"]})
    assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "memory_source_unverified"
    valid = client.post(f"{PREFIX}/memory", json={"kind": "knowledge", "content": "Dated note", "source_urls": ["https://fixture.example/owned-source"]})
    assert valid.status_code == 201
    with second_visitor() as stranger:
        assert stranger.post(f"{PREFIX}/memory", json={"kind": "knowledge", "content": "Claim", "source_urls": ["https://fixture.example/owned-source"]}).status_code == 422


def test_current_question_queues_research_then_streams_same_owned_snapshot(discussion, streaming_provider):
    client = discussion["client"]
    thread = agent_thread(client, "iran-news")
    first = send_stream(client, thread["conversation_id"], "What is happening in Iran now?", agent_id="iran-news")
    assert first.status_code == 202 and first.json()["status"] == "researching"
    assert streaming_provider["requests"] == []
    job = first.json()["job"]
    saved = complete_job(discussion, job["job_id"], label="fresh-stream")
    response = send_stream(client, thread["conversation_id"], "What is happening in Iran now?", agent_id="iran-news", fresh_job_id=job["job_id"])
    final = events(response)[-1]
    assert final[0] == "complete" and final[1]["context"]["report_id"] == saved["report_id"]
    assert final[1]["context"]["fresh_research"] is True
    assert len(discussion["reports"].list_jobs(discussion["owner"])) == 1


@pytest.mark.parametrize("language", ["en", "ar"])
def test_empty_fresh_research_streams_persisted_notice_without_model_deltas(
        discussion, streaming_provider, monkeypatch, language):
    client = discussion["client"]
    thread = agent_thread(client, "iran-news", language=language)
    question = "What is happening in Iran now?"
    first = send_stream(client, thread["conversation_id"], question, agent_id="iran-news", language=language)
    assert first.status_code == 202
    job = first.json()["job"]
    complete_job(discussion, job["job_id"], label="unused-feed-source", no_evidence=True)
    monkeypatch.delenv("OPENAI_API_KEY")
    response = send_stream(client, thread["conversation_id"], question, agent_id="iran-news", language=language,
                           fresh_job_id=job["job_id"])
    received = events(response)
    assert [event for event, _ in received] == ["complete"]
    content = received[0][1]["message"]["content"]
    assert ("couldn't verify" if language == "en" else "لم أتمكن من التحقق") in content
    replay = send_stream(client, thread["conversation_id"], question, agent_id="iran-news", language=language,
                         fresh_job_id=job["job_id"])
    assert events(replay)[-1][1]["idempotency_replayed"] is True
    assert streaming_provider["requests"] == []
    assert discussion["database"].usage_summary()["daily"]["chat_estimated_cost_usd"] == 0


def test_uncited_fresh_stream_is_saved_as_a_limitation_not_as_a_researched_fact(discussion, streaming_provider):
    client = discussion["client"]
    thread = agent_thread(client, "iran-news")
    question = "What is happening in Iran now?"
    first = send_stream(client, thread["conversation_id"], question, agent_id="iran-news")
    job = first.json()["job"]
    complete_job(discussion, job["job_id"], label="fresh-evidence")
    response = send_stream(client, thread["conversation_id"], question, agent_id="iran-news", fresh_job_id=job["job_id"])
    final = events(response)[-1]
    assert final[0] == "complete" and "couldn't verify" in final[1]["message"]["content"]
    saved = discussion["database"].get_chat_conversation(thread["conversation_id"], discussion["owner"])
    assert saved["messages"][-1]["content"] == final[1]["message"]["content"]


def test_owner_workspace_requires_auth_and_keeps_public_threads_separate(client, streaming_provider):
    assert client.get("/v1/workspace/agents/ubi-research/conversation", auth=None).status_code == 401
    owner = client.get("/v1/workspace/agents/ubi-research/conversation?language=en").json()
    client.auth = None
    client.headers["Origin"] = "http://testserver"
    client.get("/v1/public/bootstrap")
    visitor = agent_thread(client)
    assert visitor["conversation_id"] != owner["conversation_id"]
    assert client.get(f"{PREFIX}/conversations/{owner['conversation_id']}").status_code == 404
    assert streaming_provider["requests"] == []


@pytest.mark.parametrize("message", ["Hello", "How should we work together?", "What can you do?", "كيف حالك؟", "من أنت؟",
    "ماذا تستطيع أن تفعل؟", "هل يمكنك مساعدتي؟", "كيف تساعدني؟",
    "Remember that I prefer short paragraphs.",
    "What did we discuss about employment programmes?", "What did we discuss about research costs?",
    "I work on education policy.", "I am preparing a briefing."])
def test_greeting_preference_and_memory_question_do_not_start_unrequested_research(message):
    assert workspace_chat.needs_fresh_research(message, "ubi-research", False) is False


@pytest.mark.parametrize("has_report", [False, True])
@pytest.mark.parametrize("message", [
    "ما التفضيل الذي اعتمدته مع الوكيل الآخر بشأن أسلوب الإحاطة؟",
    "اشرح تفضيلي المحفوظ دون إجراء بحث جديد.",
    "ما آخر المستجدات المتاحة في ذاكرتك؟ أجب بدون بحث جديد.",
    "لا تبحث، استخدم المعلومات التي ناقشناها.",
])
def test_arabic_memory_and_explicit_no_search_intents_remain_passive(message, has_report):
    assert workspace_chat.needs_fresh_research(message, "ubi-research", has_report) is False


@pytest.mark.parametrize("message", ["ما آخر المستجدات؟", "ابحث عن آخر المستجدات بشأن دخل أساسي مضمون."])
def test_arabic_latest_developments_still_requests_research(message):
    assert workspace_chat.needs_fresh_research(message, "ubi-research", False) is True


def test_arabic_other_agent_memory_question_streams_approved_memory_without_research(discussion, streaming_provider):
    client = discussion["client"]
    note = "أفضل إحاطة موجزة بفقرات مترابطة مع توضيح حدود الأدلة."
    proposed = client.post(PREFIX + "/memory", json={"kind": "preference", "content": note})
    assert proposed.status_code == 201
    assert client.patch(PREFIX + "/memory/" + proposed.json()["memory_id"],
                        json={"action": "approve"}).status_code == 200
    thread = agent_thread(client, "education-observatory", language="ar")
    question = "ما التفضيل الذي اعتمدته مع الوكيل الآخر بشأن أسلوب الإحاطة؟ أجب دون إجراء بحث جديد."
    response = send_stream(client, thread["conversation_id"], question, language="ar", agent_id="education-observatory")
    assert response.status_code == 200, response.text
    assert events(response)[-1][0] == "complete"
    assert events(response)[-1][1]["context"]["fresh_research"] is False
    assert discussion["reports"].list_jobs(discussion["owner"], active_only=True) == []
    assert len(streaming_provider["requests"]) == 1
    assert note in json.dumps(streaming_provider["requests"][0], ensure_ascii=False)


@pytest.mark.parametrize("agent_id,message", [
    ("ubi-research", "What are the employment effects and funding costs of evaluated income pilots?"),
    ("education-observatory", "Which AI interventions improve pupil outcomes and teacher development?"),
    ("historical-reading-companion", "Compare scholarship on the Abbasid translation movement and its books."),
])
def test_substantive_evidence_questions_retrieve_when_no_report_is_available(agent_id, message):
    assert workspace_chat.needs_fresh_research(message, agent_id, False) is True


@pytest.mark.parametrize("message", [
    "I am preparing a briefing: who is the president of the European Central Bank?",
    "I work on a briefing. Tell me about the European Central Bank's governing council.",
])
def test_self_description_does_not_hide_an_independent_factual_question(message):
    assert workspace_chat.needs_fresh_research(message, "economic-cultural-briefing", False) is True
