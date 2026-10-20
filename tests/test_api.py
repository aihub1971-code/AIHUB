from fastapi.testclient import TestClient
from app.main import app
from app import main, runtime
from fastapi import HTTPException
import pytest


def test_health_and_static_ui_are_available_without_credentials():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["app"] == "AI HUB"
    page = client.get("/")
    assert "AI HUB" in page.text
    assert client.get("/static/app.js").status_code == 200


def test_agents_endpoint_preserves_all_13_assistants(client):
    response = client.get("/v1/agents")
    assert response.status_code == 200
    assert response.json()["count"] == 13
    assert any(a["id"] == "iran-news" and a["name"] == "أخبار حرب إيران" for a in response.json()["agents"])


def test_unknown_agent_returns_404(client):
    assert client.get("/v1/agents/no-such-agent").status_code == 404


def test_run_fails_closed_until_provider_is_configured(client):
    response = client.post("/v1/agents/iran-news/run", json={"task": "أخبار إيران اليوم"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "newsdata_not_configured"
    assert client.get("/v1/usage").json()["daily"]["attempts"] == 0


def test_task_validation_rejects_blank_and_oversized_tasks(client):
    for task in ("   ", "x" * 8001):
        assert client.post("/v1/agents/iran-news/run", json={"task": task}).status_code == 422


def test_report_persists_replays_and_downloads_without_duplicate_call(client, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "runtime_is_ready", lambda: True)
    monkeypatch.setattr(runtime, "estimate_reservation", lambda *_: {"provider": "openai", "model": "fixture", "estimated_cost_usd": 0.20})
    def fake_run(*args):
        calls.append(args)
        return {"response": "## تقرير تجريبي اصطناعي\n\nنجح اختبار التخزين.", "provider": "openai", "model": "fixture", "usage": {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75}, "estimated_cost_usd": 0.01, "cost_tracking_complete": True}
    monkeypatch.setattr(runtime, "run_agent", fake_run)
    headers = {"Idempotency-Key": "api-fixture-report-1"}
    first = client.post("/v1/agents/decision-briefing/run", json={"task": "اختبار اصطناعي"}, headers=headers)
    assert first.status_code == 200, first.text
    report_id = first.json()["report_id"]
    replay = client.post("/v1/agents/decision-briefing/run", json={"task": "اختبار اصطناعي"}, headers=headers)
    assert replay.json()["report_id"] == report_id
    assert replay.json()["idempotency_replayed"] is True
    assert len(calls) == 1
    record = client.get(f"/v1/reports/{report_id}").json()
    assert record["result"]["response"].startswith("## تقرير")
    assert len(client.get("/v1/reports").json()["reports"]) == 1
    assert client.get("/v1/usage").json()["daily"]["attempts"] == 1
    download = client.get(f"/v1/reports/{report_id}/download?format=md")
    assert download.status_code == 200 and "تقرير تجريبي" in download.text
    assert "attachment" in download.headers["content-disposition"]
    assert client.get(f"/v1/reports/{report_id}/download?format=json").json()["report_id"] == report_id
    assert client.get(f"/v1/reports/{report_id}", auth=None).status_code == 401


def test_schedule_crud_and_time_validation(client):
    data = {"name": "نشرة يومية", "agent_id": "iran-news", "task": "أهم الأخبار اليوم", "times": ["08:00", "18:00"]}
    created = client.post("/v1/schedules", json=data)
    assert created.status_code == 201, created.text
    row = created.json()
    schedule_id = row.get("schedule_id", row.get("id"))
    assert row["enabled"] is False
    changed = client.patch(f"/v1/schedules/{schedule_id}", json={"enabled": True})
    assert changed.status_code == 200 and changed.json()["enabled"] is True
    assert len(client.get("/v1/schedules").json()["schedules"]) == 1
    assert client.post("/v1/schedules", json={**data, "times": ["25:00"]}).status_code == 422
    assert client.post("/v1/schedules", json={**data, "times": ["08:00", "08:00"]}).status_code == 422
    assert client.post("/v1/schedules", json={**data, "agent_id": "missing"}).status_code == 404
    assert client.delete(f"/v1/schedules/{schedule_id}").status_code == 204
    assert client.get("/v1/schedules").json()["schedules"] == []


def test_system_status_exposes_configuration_flags_only(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-do-not-output")
    monkeypatch.setenv("NEWSDATA_API_KEY", "synthetic-newsdata-key-do-not-output")
    monkeypatch.setattr(runtime, "estimate_reservation", lambda *_: {"estimated_cost_usd": .1})
    response = client.get("/v1/system")
    assert response.status_code == 200, response.text
    assert response.json()["model_configured"] is True
    assert response.json()["search_configured"] is True
    assert response.json()["provider"] == "openai"
    assert response.json()["search_provider"] == "newsdata"
    assert response.json()["configuration_check_only"] is True
    assert response.json()["version"] == "1.5.0"
    assert response.json()["news_items_per_report"] is None
    assert response.json()["news_count_mode"] == "provider_page"
    assert response.json()["news_item_limit"] is None
    assert response.json()["allowed_source_domains"] == []
    assert response.json()["other_news"]["processing_limit"] == 20
    assert set(response.json()["other_news"]["allowed_source_domains"]) == {
        "ft.com", "theatlantic.com", "politico.com", "politico.eu", "wsj.com",
        "telegraph.co.uk", "aljazeera.com", "aljazeera.net",
    }
    assert "synthetic-api-key" not in response.text
    assert "synthetic-newsdata-key" not in response.text
    assert response.json()["iran_news"]["provider"] == "newsdata"
    assert response.json()["iran_news"]["translation_configured"] is True


def test_system_status_reports_both_providers_unconfigured_without_keys(client):
    response = client.get("/v1/system")
    assert response.status_code == 200
    assert response.json()["model_configured"] is False
    assert response.json()["search_configured"] is False
    assert response.json()["provider"] is None
    assert response.json()["search_provider"] is None
    assert response.json()["iran_news"]["configured"] is False
    assert response.json()["iran_news"]["configuration_issue"] == "newsdata_not_configured"
    assert response.json()["other_news"]["configured"] is False


def test_system_status_checks_news_budget_configuration_too(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-do-not-output")
    def estimate(agent_id, task):
        if agent_id == "geopolitical-morning":
            raise HTTPException(status_code=503, detail={"code": "pricing_not_configured"})
        return {"estimated_cost_usd": .1}
    monkeypatch.setattr(runtime, "estimate_reservation", estimate)
    response = client.get("/v1/system")
    assert response.status_code == 200
    assert response.json()["pricing_configured"] is False
    assert response.json()["configuration_issue"] == "pricing_not_configured"


def test_provider_rejection_keeps_safe_status_without_secret_response_body(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-do-not-output")
    monkeypatch.setattr(runtime, "estimate_reservation", lambda *_: {"provider": "openai", "model": "fixture", "estimated_cost_usd": .2})
    def rejected(*args):
        raise HTTPException(status_code=502, detail={"code": "openai_api_error", "provider_status": 401,
                                                   "message": "private-provider-response-must-not-leak"})
    monkeypatch.setattr(runtime, "run_agent", rejected)
    response = client.post("/v1/agents/geopolitical-morning/run", json={"task": "أهم الأخبار الدولية اليوم"})
    assert response.status_code == 502
    assert response.json()["detail"]["provider_status"] == 401
    assert "private-provider-response" not in response.text
    assert client.get("/v1/usage").json()["daily"]["estimated_cost_usd"] == .2


@pytest.mark.parametrize("key,iran_ready,other_ready", [
    ("NEWSDATA_API_KEY", True, False),
    ("OPENAI_API_KEY", False, True),
])
def test_agent_readiness_requires_its_own_provider_key(client, monkeypatch, key, iran_ready, other_ready):
    monkeypatch.setenv(key, "readiness-fixture-key-not-real")
    reply = client.get("/v1/agents")
    assert reply.status_code == 200
    agents = {agent["id"]: agent for agent in reply.json()["agents"]}
    assert agents["iran-news"]["runtime_ready"] is iran_ready
    assert agents["geopolitical-morning"]["runtime_ready"] is other_ready
    assert client.get("/v1/agents/iran-news").json()["runtime_ready"] is iran_ready
    assert client.get("/v1/agents/geopolitical-morning").json()["runtime_ready"] is other_ready
    assert "readiness-fixture-key" not in reply.text


def test_openai_key_alone_cannot_start_iran_newsdata_report(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-fixture-key")
    response = client.post("/v1/agents/iran-news/run", json={"task": "Iran war news"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "newsdata_not_configured"
    assert client.get("/v1/usage").json()["daily"]["attempts"] == 0


def test_system_newsdata_configuration_without_openai_is_read_only_and_secret_free(client, monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "private-newsdata-fixture-must-not-appear")
    monkeypatch.setenv("NEWSDATA_PAGE_SIZE", "7")
    monkeypatch.setenv("NEWSDATA_PLAN", "free")
    response = client.get("/v1/system")
    assert response.status_code == 200
    result = response.json()
    assert result["model_configured"] is False and result["provider"] is None
    assert result["search_configured"] is True and result["search_provider"] == "newsdata"
    assert result["configuration_check_only"] is True
    assert result["iran_news"] == {
        "provider": "newsdata", "configured": True, "query": "iran war",
        "query_semantics": "iran AND war", "window_hours": 48, "source_language": "en",
        "page_size": 7, "plan": "free", "free_plan_delay_hours": 12,
        "translation_provider": "openai", "translation_configured": False, "configuration_issue": None,
    }
    assert "private-newsdata-fixture" not in response.text
    assert client.get("/v1/usage").json()["daily"]["attempts"] == 0


def test_run_endpoint_forwards_saved_parent_identity_language_and_idempotency(client, monkeypatch):
    calls = []
    def execute(agent_id, task, **kwargs):
        calls.append((agent_id, task, kwargs))
        return {"report_id": "rpt_synthetic_next", "provider": "newsdata"}
    monkeypatch.setattr(main, "execute_report", execute)
    response = client.post("/v1/agents/iran-news/run",
        json={"task": "Iran war news", "language": "en", "continue_from_report_id": "rpt_saved_parent"},
        headers={"Idempotency-Key": "synthetic-next-page-identity"})
    assert response.status_code == 200
    assert calls == [("iran-news", "Iran war news", {
        "idempotency_key": "synthetic-next-page-identity", "trigger": "manual", "language": "en",
        "continue_from_report_id": "rpt_saved_parent"})]


@pytest.mark.parametrize("extra", [
    {"cursor": "provider-cursor"}, {"page": "provider-cursor"},
    {"q": "change-provider-query"}, {"allowed_domains": ["example.test"]},
    {"newsdata_context": {"cursor": "provider-cursor"}},
])
def test_run_endpoint_rejects_client_provider_cursor_and_filter_overrides(client, monkeypatch, extra):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid API fields must never reach execution")
    monkeypatch.setattr(main, "execute_report", forbidden)
    response = client.post("/v1/agents/iran-news/run", json={"task": "Iran war news", **extra})
    assert response.status_code == 422
    assert client.get("/v1/usage").json()["daily"]["attempts"] == 0


def test_schedule_create_rejects_saved_page_continuation(client):
    response = client.post("/v1/schedules", json={
        "agent_id": "iran-news", "task": "Iran war news", "times": ["09:00"],
        "continue_from_report_id": "rpt_saved_parent",
    })
    assert response.status_code == 422
    assert client.get("/v1/schedules").json()["schedules"] == []


def test_saved_newsdata_download_uses_requested_stored_language_without_execution(client, monkeypatch):
    record = {"report_id": "rpt_bilingual_fixture", "result": {
        "provider": "newsdata", "language": "en", "response": "## Stored English fixture",
        "response_en": "## Stored English fixture", "response_ar": "## تقرير عربي اختباري محفوظ",
    }}
    class Archive:
        def get_report(self, report_id):
            return record if report_id == record["report_id"] else None
    monkeypatch.setattr(main, "get_store", lambda: Archive())
    def forbidden(*args, **kwargs):
        raise AssertionError("Reading saved language variants must never execute a report")
    monkeypatch.setattr(main, "execute_report", forbidden)
    base = "/v1/reports/rpt_bilingual_fixture/download"
    english = client.get(base + "?language=en")
    arabic = client.get(base + "?language=ar")
    assert english.status_code == arabic.status_code == 200
    assert english.text == "# AI HUB\n\n" + record["result"]["response_en"]
    assert arabic.text == "# AI HUB\n\n" + record["result"]["response_ar"]
    assert client.get(base).text == english.text
    assert "attachment" in arabic.headers["content-disposition"]
    assert client.get(base + "?format=json&language=ar").json() == record
    assert client.get(base + "?language=fr").status_code == 422
    assert client.get(base + "?language=ar", auth=None).status_code == 401
