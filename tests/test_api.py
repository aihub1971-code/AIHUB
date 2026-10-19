from fastapi.testclient import TestClient
from app.main import app
from app import runtime
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
    assert any(a["id"] == "iran-news" and a["name"] == "أخبار إيران" for a in response.json()["agents"])


def test_unknown_agent_returns_404(client):
    assert client.get("/v1/agents/no-such-agent").status_code == 404


def test_run_fails_closed_until_provider_is_configured(client):
    response = client.post("/v1/agents/iran-news/run", json={"task": "أخبار إيران اليوم"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] in {"runtime_not_configured", "agent_runtime_not_configured"}
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
    monkeypatch.setattr(runtime, "estimate_reservation", lambda *_: {"estimated_cost_usd": .1})
    response = client.get("/v1/system")
    assert response.status_code == 200, response.text
    assert response.json()["model_configured"] is True
    assert response.json()["search_configured"] is True
    assert response.json()["provider"] == "openai"
    assert response.json()["search_provider"] == "openai"
    assert response.json()["configuration_check_only"] is True
    assert response.json()["version"] == "1.2.1"
    assert response.json()["news_items_per_report"] is None
    assert response.json()["news_count_mode"] == "available"
    assert response.json()["news_item_limit"] == 20
    assert set(response.json()["allowed_source_domains"]) == {
        "ft.com", "theatlantic.com", "politico.com", "politico.eu", "wsj.com",
        "telegraph.co.uk", "aljazeera.com", "aljazeera.net",
    }
    assert "synthetic-api-key" not in response.text


def test_system_status_requires_only_the_openai_key_for_model_and_search(client):
    response = client.get("/v1/system")
    assert response.status_code == 200
    assert response.json()["model_configured"] is False
    assert response.json()["search_configured"] is False
    assert response.json()["provider"] is None
    assert response.json()["search_provider"] is None


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
    response = client.post("/v1/agents/iran-news/run", json={"task": "أهم أخبار إيران اليوم"})
    assert response.status_code == 502
    assert response.json()["detail"]["provider_status"] == 401
    assert "private-provider-response" not in response.text
    assert client.get("/v1/usage").json()["daily"]["estimated_cost_usd"] == .2
