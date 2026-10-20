"""Public workspace integration: persistent reads, scoped PDFs, and no paid replay."""
from contextlib import contextmanager
from datetime import timedelta

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app import public
from app.agent_profiles import get_profile
from app.main import app
from app.report_jobs import ReportWorker
from app.report_store import get_report_store
from app.storage import QuotaLimits, get_store
from tests.test_public import FORBIDDEN, assert_public
from tests.test_report_store import AGENT, NOW, fake_pdf, synthetic_result


PREFIX = "/v1/public/workspace"
PRIVATE = FORBIDDEN | {"lease_token", "lease_owner", "key_hash", "request_hash", "pdf_path",
                       "path", "_research_provenance", "scope", "research_strategy"}


def assert_workspace_public(value):
    assert_public(value)
    if isinstance(value, dict):
        assert not PRIVATE.intersection(value)
        for child in value.values():
            assert_workspace_public(child)
    elif isinstance(value, list):
        for child in value:
            assert_workspace_public(child)


@pytest.fixture
def workspace(client):
    client.auth = None
    client.headers["Origin"] = "http://testserver"
    assert client.get("/v1/public/bootstrap").status_code == 200
    database = get_store()
    database.clock = lambda: NOW
    reports = get_report_store(database)
    state = {"client": client, "store": reports, "estimate": [], "generate": [], "pdf": [],
             "pdf_failure": False, "generation_failure": False, "private_fields": False}

    def estimate(agent_id, topic, **options):
        state["estimate"].append((agent_id, topic, options))
        return {"provider": "openai", "model": "private-fixture-model", "estimated_cost_usd": .1,
                "max_input_tokens": 100, "max_output_tokens": 100, "max_web_search_calls": 1}

    def generate(agent_id, topic, *, progress, **options):
        state["generate"].append((agent_id, topic, options))
        progress("searching")
        progress("checking_sources")
        if state["generation_failure"]:
            raise HTTPException(504, detail={"code": "provider_timeout", "api_key": "private-error-sentinel"})
        progress("analyzing")
        result = synthetic_result({"agent_id": agent_id, "topic": topic, "language": options["language"],
                                   "research_window": options["research_window"]})
        if state["private_fields"]:
            result.update(api_key="private-result-sentinel", _research_provenance={"text": "private-discovery-sentinel"})
            result["sources"][0].update(api_key="private-source-sentinel",
                publication_verification={"method": "fixture", "verified": True, "api_key": "private-verification-sentinel"})
            result["sources"][0]["citation_annotations"][0]["api_key"] = "private-annotation-sentinel"
            result["sections"][0]["usage"] = {"input_tokens": 999}
            result["citation_mappings"]["s1"]["api_key"] = "private-map-sentinel"
            result["sources"].append({"id": "unsafe", "title": "Rejected navigation", "url": "javascript:alert(1)"})
        return result

    def render(result, path):
        state["pdf"].append(result)
        if state["pdf_failure"]:
            raise RuntimeError("private-pdf-diagnostic-sentinel")
        return fake_pdf(path)

    state["worker"] = ReportWorker(reports, estimate=estimate, generator=generate, pdf_renderer=render)
    return state


def submit(workspace, *, key="request-one", agent_id=AGENT, **body):
    return workspace["client"].post(f"{PREFIX}/agents/{agent_id}/reports",
        json={"topic": "Synthetic scope", "language": "en", **body}, headers={"Idempotency-Key": key})


def complete(workspace, **options):
    submitted = submit(workspace, **options)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job"]["job_id"]
    assert workspace["worker"].run_once()
    response = workspace["client"].get(f"{PREFIX}/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


@contextmanager
def second_visitor():
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        assert client.get("/v1/public/bootstrap").status_code == 200
        yield client


def test_configuration_and_default_selection_queue_one_job_with_frozen_window(workspace):
    client = workspace["client"]
    config = client.get(PREFIX + "/config")
    assert config.status_code == 200
    data = config.json()
    assert data["agent_count"] == len(data["agents"]) == 13
    assert data["coverage_hours"] == 12 and data["maximum_news_items"] == 20
    assert len({profile["id"] for profile in data["agents"]}) == 13
    assert_workspace_public(data)

    selected = client.get(f"{PREFIX}/agents/{AGENT}")
    assert selected.json()["last_successful_report"] is None and selected.json()["active_job"] is None
    queued = submit(workspace, topic="   ")
    assert queued.status_code == 202, queued.text
    value = queued.json()
    job = value["job"]
    assert job["topic"] == get_profile(AGENT)["default_topic_en"]
    assert job["stage"] == job["status"] == "queued"
    assert job["research_cutoff"] == NOW.isoformat(timespec="microseconds")
    assert value["last_successful_report"] is None
    assert_workspace_public(value)
    assert workspace["store"].base_store.usage_summary()["daily"]["attempts"] == 0
    assert not workspace["generate"]


def test_duplicate_requests_and_reload_reconnect_without_reserving_or_generating_again(workspace):
    client = workspace["client"]
    first = submit(workspace).json()
    duplicate = submit(workspace, key="second-click").json()
    assert duplicate["joined_existing"] is True
    assert duplicate["job"]["job_id"] == first["job"]["job_id"]
    for _ in range(3):
        selected = client.get(f"{PREFIX}/agents/{AGENT}").json()
        assert selected["active_job"]["job_id"] == first["job"]["job_id"]
        assert client.get(f"{PREFIX}/jobs/{first['job']['job_id']}").json()["report"] is None
        assert client.get(PREFIX + "/reports").json()["reports"] == []
    assert not workspace["estimate"] and not workspace["generate"]
    assert workspace["worker"].run_once()
    completed = client.get(f"{PREFIX}/jobs/{first['job']['job_id']}").json()
    report_id = completed["report"]["report_id"]
    before = workspace["store"].base_store.usage_summary()
    for _ in range(3):
        assert client.get(f"{PREFIX}/agents/{AGENT}").json()["last_successful_report"]["report_id"] == report_id
        assert client.get(f"{PREFIX}/reports/{report_id}").status_code == 200
        history = client.get(PREFIX + "/reports", params={"agent_id": AGENT}).json()["reports"]
        assert len(history) == 1 and "result" not in history[0]
        assert submit(workspace, key="second-click").json()["job"]["job_id"] == first["job"]["job_id"]
    assert workspace["worker"].run_once() is False
    assert len(workspace["generate"]) == 1 and workspace["store"].base_store.usage_summary() == before


def test_newer_update_preserves_last_successful_report_even_when_generation_fails(workspace):
    client = workspace["client"]
    successful = complete(workspace)["report"]
    workspace["store"].base_store.clock = lambda: NOW + timedelta(hours=1)
    newer = submit(workspace, key="update-request").json()
    assert newer["job"]["generation_seq"] == successful["generation_seq"] + 1
    assert newer["last_successful_report"]["report_id"] == successful["report_id"]
    selected = client.get(f"{PREFIX}/agents/{AGENT}").json()
    assert selected["last_successful_report"]["research_cutoff"] == successful["research_cutoff"]
    assert selected["active_job"]["job_id"] == newer["job"]["job_id"]
    workspace["generation_failure"] = True
    assert workspace["worker"].run_once()
    status = client.get(f"{PREFIX}/jobs/{newer['job']['job_id']}")
    assert status.status_code == 200
    assert status.json()["job"]["status"] == "failed" and status.json()["report"] is None
    assert "private-error-sentinel" not in status.text
    selected = client.get(f"{PREFIX}/agents/{AGENT}").json()
    assert selected["active_job"] is None
    assert selected["last_successful_report"]["report_id"] == successful["report_id"]
    assert len(client.get(PREFIX + "/reports").json()["reports"]) == 1
    previous = workspace["generate"][-1][2]["previous_report"]
    assert previous["report_id"] == successful["report_id"]


def test_nested_public_projection_preserves_sources_but_removes_private_data_and_unsafe_urls(workspace):
    workspace["private_fields"] = True
    response = complete(workspace)
    assert_workspace_public(response)
    result = response["report"]["result"]
    assert result["sources"][0]["publication_verification"] == {"method": "fixture", "verified": True}
    assert result["sources"][0]["citation_annotations"][0]["url"] == "https://fixture.example/synthetic"
    assert result["sources"][1]["url"] is None and "unsafe" not in result["citation_mappings"]
    for path in (f"/reports/{response['report']['report_id']}", "/reports", f"/agents/{AGENT}"):
        fetched = workspace["client"].get(PREFIX + path)
        assert fetched.status_code == 200
        assert_workspace_public(fetched.json())
        assert "private-" not in fetched.text


def test_foreign_session_cannot_read_join_or_retry_another_sessions_jobs_or_pdf(workspace):
    result = complete(workspace)
    job_id, report_id = result["job"]["job_id"], result["report"]["report_id"]
    own_pdf = workspace["client"].get(f"{PREFIX}/reports/{report_id}/pdf")
    assert own_pdf.status_code == 200 and own_pdf.content.startswith(b"%PDF-")
    assert own_pdf.headers["content-type"] == "application/pdf"
    assert "no-store" in own_pdf.headers["cache-control"]
    assert report_id in own_pdf.headers["content-disposition"]
    before = workspace["store"].base_store.usage_summary()
    with second_visitor() as stranger:
        for path in (f"/jobs/{job_id}", f"/reports/{report_id}", f"/reports/{report_id}/pdf"):
            denied = stranger.get(PREFIX + path)
            assert denied.status_code == 404, denied.text
            assert report_id not in denied.text and job_id not in denied.text
        assert stranger.post(f"{PREFIX}/reports/{report_id}/pdf/retry", json={}).status_code == 404
        assert stranger.get(PREFIX + "/reports").json()["reports"] == []
        assert stranger.get(f"{PREFIX}/agents/{AGENT}").json()["last_successful_report"] is None
        own_job = stranger.post(f"{PREFIX}/agents/{AGENT}/reports", json={"topic": "Synthetic scope", "language": "en"},
                               headers={"Idempotency-Key": "request-one"})
        assert own_job.status_code == 202 and own_job.json()["job"]["job_id"] != job_id
        assert own_job.json()["joined_existing"] is False
    assert workspace["store"].base_store.usage_summary() == before


def test_pdf_failure_is_readable_then_retried_locally_without_changing_report_or_cost(workspace):
    workspace["pdf_failure"] = True
    completed = complete(workspace)
    client = workspace["client"]
    job, report = completed["job"], completed["report"]
    assert job["status"] == "pdf_failed" and report["pdf"]["status"] == "failed"
    assert report["result"]["response"]
    assert client.get(f"{PREFIX}/reports/{report['report_id']}/pdf").status_code == 409
    before = workspace["store"].base_store.usage_summary()
    for _ in range(2):
        retry = client.post(f"{PREFIX}/reports/{report['report_id']}/pdf/retry", json={})
        assert retry.status_code == 202, retry.text
        assert retry.json()["job"]["job_id"] == job["job_id"]
    workspace["pdf_failure"] = False
    assert workspace["worker"].run_once()
    saved = client.get(f"{PREFIX}/reports/{report['report_id']}").json()
    assert saved["result"] == report["result"] and saved["research_cutoff"] == report["research_cutoff"]
    assert saved["pdf"]["status"] == "ready"
    assert len(workspace["generate"]) == len(workspace["estimate"]) == 1 and len(workspace["pdf"]) == 2
    assert workspace["store"].base_store.usage_summary() == before
    assert client.post(f"{PREFIX}/reports/{report['report_id']}/pdf/retry", json={}).status_code == 202
    assert workspace["worker"].run_once() is False


def test_key_body_agent_and_queue_bounds_reject_without_provider_work(workspace):
    client = workspace["client"]
    assert client.post(f"{PREFIX}/agents/{AGENT}/reports", json={}).status_code == 422
    assert submit(workspace, owner_id="public:someone-else").status_code == 422
    assert submit(workspace, agent_id="unknown-agent").status_code == 404
    assert submit(workspace, topic="x" * 8001).status_code == 422
    assert submit(workspace, language="fr").status_code == 422
    assert client.get(PREFIX + "/reports", params={"limit": 101}).status_code == 422
    assert submit(workspace).status_code == 202
    assert submit(workspace, topic="Different content same key").status_code == 409
    assert submit(workspace, key="second", topic="Second").status_code == 202
    assert submit(workspace, key="third", topic="Third").status_code == 202
    assert submit(workspace, key="fourth", topic="Fourth").status_code == 429
    assert submit(workspace, key="join-at-cap").json()["joined_existing"] is True
    assert not workspace["estimate"] and not workspace["generate"]
    assert workspace["store"].base_store.usage_summary()["daily"]["attempts"] == 0


def test_budget_admission_failure_is_a_saved_readable_job_and_replay_does_not_retry(workspace):
    workspace["store"].base_store._limits = QuotaLimits(50, ".05", "100")
    submitted = submit(workspace)
    assert submitted.status_code == 202
    job_id = submitted.json()["job"]["job_id"]
    assert workspace["worker"].run_once()
    response = workspace["client"].get(f"{PREFIX}/jobs/{job_id}")
    assert response.status_code == 200
    value = response.json()
    assert value["job"]["status"] == "failed" and value["job"]["error_code"] == "daily_budget_limit"
    assert value["report"] is None and value["job"]["report_id"] is None
    assert_workspace_public(value)
    assert submit(workspace).json()["job"]["job_id"] == job_id
    assert workspace["worker"].run_once() is False
    assert not workspace["generate"] and not workspace["pdf"]


def test_session_and_origin_checks_apply_to_all_workspace_mutations(workspace):
    client = workspace["client"]
    for path in ("/config", "/reports", f"/agents/{AGENT}"):
        assert client.get(PREFIX + path, headers={"Origin": "https://foreign.example"}).status_code == 403
    assert submit(workspace).status_code == 202
    mutation = f"{PREFIX}/agents/{AGENT}/reports"
    assert client.post(mutation, json={}, headers={"Origin": "https://foreign.example", "Idempotency-Key": "cross"}).status_code == 403
    del client.headers["Origin"]
    assert client.post(mutation, json={}, headers={"Idempotency-Key": "no-origin"}).status_code == 403
    assert client.post(mutation, json={}, headers={"Sec-Fetch-Site": "cross-site", "Idempotency-Key": "cross-site"}).status_code == 403
    client.cookies.clear()
    assert client.get(PREFIX + "/reports").status_code == 401
    assert client.post(mutation, json={}, headers={"Origin": "http://testserver", "Idempotency-Key": "no-session"}).status_code == 401
    assert not workspace["generate"]


def test_expired_session_cannot_reopen_saved_report_with_old_cookie(workspace):
    result = complete(workspace)
    client, database = workspace["client"], workspace["store"].base_store
    original_cookie = client.cookies.get(public.SESSION_COOKIE)
    with database._transaction() as connection:
        connection.execute("UPDATE public_sessions SET expires_at=0")
    assert client.get(f"{PREFIX}/reports/{result['report']['report_id']}").status_code == 401
    assert client.get("/v1/public/bootstrap").status_code == 200
    assert client.cookies.get(public.SESSION_COOKIE) != original_cookie
    assert client.get(f"{PREFIX}/reports/{result['report']['report_id']}").status_code == 404
    assert client.get(PREFIX + "/reports").json()["reports"] == []
