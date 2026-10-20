"""Anonymous API isolation and accounting gates; synthetic providers only."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from app import public, runtime
from app.main import app
from app.registry import RETIRED_AGENT_IDS
from app.storage import QuotaLimits, StoreError, get_store


NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
FORBIDDEN = {"accounting", "usage", "usage_stages", "estimated_cost_usd", "reserved_cost_usd",
             "pricing_snapshot", "pricing_model", "cost_basis", "cost_is_estimate", "newsdata_context",
             "newsdata_credits_used", "credits_remaining", "cursor", "api_key", "model", "owner_id"}


def assert_public(value):
    if isinstance(value, dict):
        assert not FORBIDDEN.intersection(value)
        for child in value.values():
            assert_public(child)
    elif isinstance(value, list):
        for child in value:
            assert_public(child)


@pytest.fixture
def database():
    store = get_store()
    store._limits = QuotaLimits(50, "100", "1000")
    store.clock = lambda: NOW
    return store


@pytest.fixture
def visitor(client, database):
    client.auth = None
    client.headers["Origin"] = "http://testserver"
    response = client.get("/v1/public/bootstrap")
    assert response.status_code == 200
    return client


@contextmanager
def other_visitor():
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        assert client.get("/v1/public/bootstrap").status_code == 200
        yield client


@pytest.fixture
def provider(monkeypatch, database):
    calls = []
    state = {"fail": False}
    monkeypatch.setattr(runtime, "agent_runtime_is_ready", lambda agent_id: True)
    monkeypatch.setattr(runtime, "runtime_is_ready", lambda: True)

    def estimate(agent_id, task, *, language="ar", newsdata_context=None):
        result = {"provider": "newsdata" if newsdata_context is not None else "openai",
                  "model": "fixture-private-model", "estimated_cost_usd": .1,
                  "max_input_tokens": 1000, "max_output_tokens": 100, "max_web_search_calls": 0}
        if newsdata_context is not None:
            result.update(max_newsdata_calls=1, max_newsdata_credits=1, newsdata_context=newsdata_context)
        return result

    def run(agent_id, task, *, language="ar", newsdata_context=None):
        with database._connect() as connection:
            pending = connection.execute("SELECT r.report_id,p.session_id FROM runs r JOIN public_reports p USING(report_id) WHERE r.status='pending'").fetchall()
        assert pending, "Public ownership must be durable before provider execution."
        calls.append((agent_id, task, language, newsdata_context))
        if state["fail"]:
            raise HTTPException(504, detail={"code": "provider_timeout", "message": "private-provider-sentinel $999 configuration"})
        result = {"agent_id": agent_id, "response": "Synthetic public report", "language": language,
                  "provider": "openai", "estimated_cost_usd": .001, "cost_tracking_complete": True,
                  "usage": {"input_tokens": 17}, "model": "private-model-sentinel",
                  "api_key": "private-key-sentinel", "pagination": {"cursor": "private-cursor-sentinel"}}
        if newsdata_context is not None:
            context = dict(newsdata_context)
            result.update(provider="newsdata", report_kind="news", item_count=10,
                items=[{"headline_en": f"Synthetic article {index}", "summary_en": "English original.",
                        "headline_ar": f"خبر تجريبي {index}", "summary_ar": "ملخص تجريبي.",
                        "translation_status": "translated", "url": f"https://www.reuters.com/world/synthetic-{index}/",
                        "publisher": "Reuters", "api_key": "private-item-sentinel"} for index in range(10)],
                newsdata={"query": "iran war", "returned_item_count": 10, "consumed_item_count": 10,
                          "credits_remaining": 199, "provider_total_results": 123},
                newsdata_context={**context, "next_cursor": f"synthetic-cursor-{context['page_index'] + 1}"},
                newsdata_credits_used=1, credit_tracking_complete=True,
                response_en="Synthetic saved English page", response_ar="صفحة عربية تجريبية محفوظة")
        return result

    monkeypatch.setattr(runtime, "estimate_reservation", estimate)
    monkeypatch.setattr(runtime, "run_agent", run)
    state["calls"] = calls
    return state


def run(client, *, key="same-browser-key", agent_id="story-moodboard", **body):
    return client.post(f"/v1/public/agents/{agent_id}/run", json={"task": "Synthetic public task", **body},
                       headers={"Idempotency-Key": key})


def conversation(client):
    response = client.post("/v1/public/chat/conversations", json={"language": "en"})
    assert response.status_code == 201, response.text
    assert_public(response.json())
    return response.json()["conversation_id"]


def message(client, conversation_id, *, key="message-one", **body):
    return client.post(f"/v1/public/chat/conversations/{conversation_id}/messages",
                       json={"message": "Explain this platform", **body}, headers={"Idempotency-Key": key})


def test_bootstrap_is_public_cookie_is_opaque_and_private_metadata_is_omitted(client, database, monkeypatch):
    client.auth = None
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-key-sentinel")
    monkeypatch.setenv("NEWSDATA_API_KEY", "private-newsdata-key-sentinel")
    response = client.get("/v1/public/bootstrap")
    assert response.status_code == 200 and len(response.json()["agents"]) == 9
    assert_public(response.json())
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert "Domain=" not in cookie
    token = client.cookies.get(public.SESSION_COOKIE)
    assert token and len(token) == 43
    with database._connect() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM public_sessions")]
    assert token not in json.dumps(rows) and len(rows) == 1
    assert "private-openai-key-sentinel" not in response.text and "private-newsdata-key-sentinel" not in response.text
    assert client.get("/v1/public/bootstrap").headers.get("set-cookie") is None
    assert database.usage_summary()["daily"]["attempts"] == 0


def test_https_and_render_forwarded_https_set_secure_cookie_and_accept_same_origin(database):
    with TestClient(app, base_url="https://ai-hub.example") as client:
        result = client.get("/v1/public/bootstrap")
        assert "Secure" in result.headers["set-cookie"]
        response = client.post("/v1/public/chat/conversations", json={}, headers={"Origin": "https://ai-hub.example"})
        assert response.status_code == 201
    with TestClient(app, base_url="http://ai-hub.example") as proxy:
        result = proxy.get("/v1/public/bootstrap", headers={"X-Forwarded-Proto": "https"})
        assert "Secure" in result.headers["set-cookie"]
        token = proxy.cookies.get(public.SESSION_COOKIE)
        response = proxy.post("/v1/public/chat/conversations", json={}, headers={
            "Origin": "https://ai-hub.example", "X-Forwarded-Proto": "https",
            "Cookie": f"{public.SESSION_COOKIE}={token}"})
        assert response.status_code == 201


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": "http://testserver.evil.example"},
    {"Origin": "http://testserver:8080"}, {"Origin": ""},
    {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
])
def test_cross_origin_mutations_fail_before_provider_or_reservation(visitor, database, provider, headers):
    response = visitor.post("/v1/public/agents/story-moodboard/run", json={"task": "Synthetic request"},
                            headers={**headers, "Idempotency-Key": "csrf-fixture"})
    assert response.status_code == 403 and response.json()["detail"]["code"] == "public_origin_rejected"
    assert provider["calls"] == [] and database.usage_summary()["daily"]["attempts"] == 0


def test_missing_unknown_and_expired_sessions_do_not_authorize_requests(client, database, provider, monkeypatch):
    client.auth = None
    client.headers["Origin"] = "http://testserver"
    assert run(client).status_code == 401
    client.cookies.set(public.SESSION_COOKIE, "A" * 43)
    assert run(client).status_code == 401
    client.cookies.clear()
    assert client.get("/v1/public/bootstrap").status_code == 200
    monkeypatch.setattr(public.time, "time", lambda: 9999999999)
    assert run(client).status_code == 401
    assert provider["calls"] == []


def test_public_runs_are_owned_redacted_and_isolated_from_other_sessions_and_admin(visitor, database, provider):
    response = run(visitor, agent_id="iran-news")
    assert response.status_code == 200, response.text
    value = response.json()
    report_id = value["report_id"]
    assert value["items"][0]["url"].startswith("https://www.reuters.com/")
    assert value["response_ar"] == "صفحة عربية تجريبية محفوظة"
    assert_public(value)
    assert "private-" not in response.text
    own = visitor.get(f"/v1/public/reports/{report_id}")
    assert own.status_code == 200 and own.json()["status"] == "succeeded"
    assert own.json()["result"]["newsdata"]["provider_total_results"] == 123
    assert_public(own.json())
    with other_visitor() as other:
        assert other.get(f"/v1/public/reports/{report_id}").status_code == 404
        assert run(other, agent_id="iran-news", continue_from_report_id=report_id).status_code == 404
    admin = database.reserve_run("literary-translation", "Private admin report", ".1").report
    database.finish_run(admin["report_id"], {"response": "Private admin content"})
    assert visitor.get(f"/v1/public/reports/{admin['report_id']}").status_code == 404
    assert run(visitor, agent_id="iran-news", key="admin-continuation", continue_from_report_id=admin["report_id"]).status_code == 404
    for path in ("/v1/usage", "/v1/system", "/v1/agents", "/v1/reports", "/v1/schedules", "/v1/chat/conversations"):
        assert visitor.get(path).status_code == 401
    assert len(provider["calls"]) == 1


def test_idempotency_is_session_scoped_and_replay_never_calls_provider_twice(visitor, database, provider):
    first = run(visitor).json()
    replay = run(visitor)
    assert replay.status_code == 200 and replay.json()["idempotency_replayed"] is True
    assert replay.json()["report_id"] == first["report_id"]
    changed = run(visitor, task="Changed request")
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "idempotency_conflict"
    with other_visitor() as other:
        second = run(other).json()
        assert second["report_id"] != first["report_id"]
    assert len(provider["calls"]) == 2
    assert database.usage_summary()["daily"]["attempts"] == 2


def test_owned_continuation_uses_same_gate_and_replays_without_an_extra_page(visitor, database, provider):
    first = run(visitor, agent_id="iran-news").json()
    second = run(visitor, agent_id="iran-news", key="page-two", continue_from_report_id=first["report_id"])
    assert second.status_code == 200, second.text
    second = second.json()
    assert second["pagination"]["page_index"] == 2
    assert second["pagination"]["parent_report_id"] == first["report_id"]
    assert visitor.get(f"/v1/public/reports/{second['report_id']}").status_code == 200
    replay = run(visitor, agent_id="iran-news", key="another-key", continue_from_report_id=first["report_id"])
    assert replay.status_code == 200 and replay.json()["report_id"] == second["report_id"]
    assert len(provider["calls"]) == 2


def test_failed_request_remains_owned_and_safe_and_keeps_shared_reservation(visitor, database, provider):
    provider["fail"] = True
    failed = run(visitor)
    assert failed.status_code == 504
    assert failed.json()["detail"]["code"] == "provider_timeout"
    report_id = failed.json()["detail"]["report_id"]
    assert "private-provider-sentinel" not in failed.text and "$999" not in failed.text
    saved = visitor.get(f"/v1/public/reports/{report_id}")
    assert saved.status_code == 200 and saved.json()["status"] == "failed"
    assert_public(saved.json())
    assert run(visitor).status_code == 409 and len(provider["calls"]) == 1
    assert database.get_report(report_id)["estimated_cost_usd"] == .1


def test_reservation_association_interruption_recovers_only_original_request_without_provider_retry(visitor, database, provider, monkeypatch):
    original = public._bind_report
    def interrupted(*args, **kwargs):
        raise StoreError("public_association_unavailable", "Private storage detail", 503)
    monkeypatch.setattr(public, "_bind_report", interrupted)
    first = run(visitor)
    assert first.status_code == 503 and provider["calls"] == []
    assert "Private storage detail" not in first.text
    monkeypatch.setattr(public, "_bind_report", original)
    replay = run(visitor)
    assert replay.status_code == 409 and replay.json()["detail"]["code"] == "report_in_progress"
    assert visitor.get(f"/v1/public/reports/{replay.json()['detail']['report_id']}").status_code == 200
    assert provider["calls"] == [] and database.usage_summary()["daily"]["attempts"] == 1


def test_idempotency_alias_cannot_claim_an_unowned_admin_report(visitor, database, provider):
    admin = database.reserve_run("story-moodboard", "Synthetic public task", ".1",
                                 idempotency_key="original-admin-key").report
    database.finish_run(admin["report_id"], {"response": "Private admin report content"})
    with database._transaction() as connection:
        session_id = connection.execute("SELECT session_id FROM public_sessions").fetchone()[0]
        row = connection.execute("SELECT * FROM runs WHERE report_id=?", (admin["report_id"],)).fetchone()
        database._bind_key(connection, public._scoped_key(session_id, "public-alias"), row)
    denied = run(visitor, key="public-alias")
    assert denied.status_code == 404 and "Private admin report content" not in denied.text
    assert visitor.get(f"/v1/public/reports/{admin['report_id']}").status_code == 404
    assert provider["calls"] == []
    with database._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM public_reports").fetchone()[0] == 0


@pytest.mark.parametrize("agent_id", sorted(RETIRED_AGENT_IDS))
def test_retired_public_assistants_deny_launch_without_consuming_attempts(visitor, database, provider, agent_id):
    response = run(visitor, agent_id=agent_id)
    assert response.status_code == 404
    assert provider["calls"] == []
    assert database.usage_summary()["daily"]["attempts"] == 0


def test_owned_retired_saved_report_remains_readable_and_cannot_be_relaunched(visitor, database, provider):
    archived = database.reserve_run("literary-translation", "Historical saved passage", ".1").report
    database.finish_run(archived["report_id"], {"response": "Historical translated passage"})
    with database._connect() as connection:
        session_id = connection.execute("SELECT session_id FROM public_sessions").fetchone()[0]
    public._bind_report(database, session_id, archived["report_id"])
    response = visitor.get(f"/v1/public/reports/{archived['report_id']}")
    assert response.status_code == 200
    assert response.json()["agent_id"] == "literary-translation"
    assert response.json()["result"]["response"] == "Historical translated passage"
    with other_visitor() as other:
        assert other.get(f"/v1/public/reports/{archived['report_id']}").status_code == 404
    assert run(visitor, agent_id="literary-translation").status_code == 404
    assert provider["calls"] == []
    assert database.usage_summary()["daily"]["attempts"] == 1


def test_public_errors_hide_budget_amounts_and_credential_configuration(visitor, database, provider, monkeypatch):
    database._limits = QuotaLimits(50, "0", "0")
    response = run(visitor)
    assert response.status_code == 429 and response.json()["detail"]["code"] == "daily_budget_limit"
    assert "USD" not in response.text and "reservation" not in response.text
    assert provider["calls"] == []
    monkeypatch.setattr(runtime, "runtime_is_ready", lambda: False)
    unavailable = run(visitor, key="unavailable")
    assert unavailable.status_code == 503 and "OPENAI_API_KEY" not in unavailable.text


@pytest.fixture
def chat_provider(monkeypatch):
    original = httpx.Client
    requests = []
    monkeypatch.setenv("OPENAI_API_KEY", "private-public-chat-key-sentinel")
    def dispatch(request):
        assert request.url == "https://api.openai.com/v1/responses"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "service_tier": "default",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "Synthetic public chat answer"}]}]})
    monkeypatch.setattr(runtime.httpx, "Client", lambda *args, **kwargs:
                        original(transport=httpx.MockTransport(dispatch), **kwargs))
    return requests


def test_public_chat_is_persistent_isolated_redacted_and_uses_only_public_platform_context(visitor, database, chat_provider):
    admin = database.create_chat_conversation("owner", title="Private admin conversation")
    conv = conversation(visitor)
    first = message(visitor, conv)
    assert first.status_code == 200, first.text
    assert first.json()["message"]["content"] == "Synthetic public chat answer"
    assert_public(first.json())
    assert message(visitor, conv).json()["idempotency_replayed"] is True
    second = message(visitor, conv, key="second", message="A follow-up question")
    assert second.status_code == 200 and second.json()["context"]["history_messages_included"] == 2
    payload = json.dumps(chat_provider[0], ensure_ascii=False)
    for private in ("USD", "daily_budget", "monthly_budget", '"usage":', '"accounting":',
                    "private-public-chat-key-sentinel", "Private admin conversation", "scheduler_enabled"):
        assert private not in payload
    assert "press Enter or Search" in payload and "visitors" in payload
    assert chat_provider[0]["tools"] == [] and len(chat_provider) == 2
    assert visitor.get(f"/v1/public/chat/conversations/{admin['conversation_id']}").status_code == 404
    with other_visitor() as other:
        assert other.get("/v1/public/chat/conversations").json()["conversations"] == []
        assert other.get(f"/v1/public/chat/conversations/{conv}").status_code == 404
        assert message(other, conv).status_code == 404
    saved = visitor.get(f"/v1/public/chat/conversations/{conv}")
    assert saved.status_code == 200 and len(saved.json()["messages"]) == 4
    assert_public(saved.json())
    assert database.usage_summary()["daily"]["attempts"] == 0
    assert database.usage_summary()["daily"]["chat_turns"] == 2


def test_public_chat_rejects_foreign_explicit_and_inherited_report_before_provider(visitor, database, chat_provider):
    conv = conversation(visitor)
    admin = database.reserve_run("literary-translation", "Private admin task", ".1").report
    database.finish_run(admin["report_id"], {"response": "Private admin report body"})
    denied = message(visitor, conv, report_id=admin["report_id"])
    assert denied.status_code == 404 and chat_provider == []
    with database._transaction() as connection:
        connection.execute("UPDATE chat_conversations SET report_id=? WHERE conversation_id=?", (admin["report_id"], conv))
    inherited = message(visitor, conv, key="inherit")
    assert inherited.status_code == 404 and chat_provider == []
    assert database.usage_summary()["daily"]["chat_turns"] == 0


def test_public_chat_can_discuss_owned_report_without_receiving_accounting(visitor, database, provider, chat_provider):
    report = run(visitor, agent_id="iran-news").json()
    conv = conversation(visitor)
    response = message(visitor, conv, report_id=report["report_id"], language="ar")
    assert response.status_code == 200, response.text
    assert response.json()["context"]["report_id"] == report["report_id"]
    payload = json.dumps(chat_provider[0], ensure_ascii=False)
    assert "https://www.reuters.com/world/synthetic-0/" in payload
    assert "خبر تجريبي 0" in payload
    assert "private-item-sentinel" not in payload and "credits_remaining" not in payload
    assert_public(response.json())


@pytest.mark.parametrize("body", [{"task": "x", "cursor": "client-cursor"},
    {"task": "x", "q": "different query"}, {"task": "x", "platform_context": {"usage": "injected"}}])
def test_public_cannot_supply_provider_filters_or_trusted_context(visitor, provider, body):
    response = visitor.post("/v1/public/agents/iran-news/run", json=body, headers={"Idempotency-Key": "invalid"})
    assert response.status_code == 422 and provider["calls"] == []
