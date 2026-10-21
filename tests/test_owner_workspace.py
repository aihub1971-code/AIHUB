"""The authenticated agent UI shares behavior, never the public visitor identity."""
import pytest

from app import news_feed
from app.registry import RETIRED_AGENT_IDS
from app.storage import StoreError
from tests.test_report_store import AGENT
from tests.test_workspace import (
    PREFIX as PUBLIC_PREFIX, assert_workspace_public, complete, workspace,
)


PREFIX = "/v1/workspace"
OWNER_AUTH = ("test-owner", "test-password-not-for-production")


@pytest.fixture
def owner_workspace(workspace):
    # A public cookie remains present deliberately: Basic auth must be the sole
    # owner identity even when the owner has also used the public application.
    workspace["client"].auth = OWNER_AUTH
    with workspace["store"].base_store._connect() as connection:
        session_id = connection.execute("SELECT session_id FROM public_sessions").fetchone()[0]
    workspace["public_owner"] = "public:" + session_id
    return workspace


def owner_submit(state, *, key="owner-request", **body):
    return state["client"].post(f"{PREFIX}/agents/{AGENT}/reports",
        json={"topic": "Synthetic scope", "language": "en", **body},
        headers={"Idempotency-Key": key})


def owner_complete(state, **options):
    submitted = owner_submit(state, **options)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job"]["job_id"]
    assert state["worker"].run_once()
    response = state["client"].get(f"{PREFIX}/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("auth", [None, ("wrong-owner", "wrong-password")])
@pytest.mark.parametrize(("method", "path", "body"), [
    ("GET", "/admin/agents", None),
    ("GET", PREFIX + "/bootstrap", None),
    ("GET", PREFIX + "/config", None),
    ("GET", f"{PREFIX}/agents/{AGENT}", None),
    ("GET", f"{PREFIX}/agents/{AGENT}/news", None),
    ("POST", f"{PREFIX}/agents/{AGENT}/news/refresh", {}),
    ("POST", f"{PREFIX}/agents/{AGENT}/reports", {}),
    ("GET", PREFIX + "/jobs/job_missing", None),
    ("GET", PREFIX + "/reports", None),
    ("GET", PREFIX + "/reports/rpt_missing", None),
    ("GET", PREFIX + "/reports/rpt_missing/pdf", None),
    ("POST", PREFIX + "/reports/rpt_missing/pdf/retry", {}),
])
def test_owner_workspace_routes_reject_missing_or_wrong_auth_before_work(client, monkeypatch, auth, method, path, body):
    def forbidden(*args, **kwargs):
        pytest.fail("An unauthenticated request must not read or refresh an owner feed.")

    monkeypatch.setattr(news_feed, "get_feed", forbidden)
    client.auth = auth
    assert client.get("/v1/public/bootstrap").status_code == 200
    options = {"json": body} if body is not None else {}
    response = client.request(method, path, headers={"Idempotency-Key": "unauthenticated"}, **options)
    assert response.status_code == 401, response.text
    assert response.headers["www-authenticate"].startswith("Basic")
    assert "test-password-not-for-production" not in response.text


def test_admin_agents_serves_authenticated_workspace_without_exposing_credentials(client):
    page = client.get("/admin/agents")
    assert page.status_code == 200 and "AI HUB" in page.text
    assert page.text.count('data-workspace-mode="owner"') == 1
    assert "private" in page.headers["cache-control"] and "no-store" in page.headers["cache-control"]
    assert OWNER_AUTH[0] not in page.text and OWNER_AUTH[1] not in page.text
    public = client.get("/", auth=None)
    assert public.status_code == 200 and 'data-workspace-mode="owner"' not in public.text
    assert client.get(PREFIX + "/bootstrap").json() == {"authenticated": True, "workspace": "owner"}


def test_owner_open_and_agent_switch_are_passive_even_with_empty_feeds(owner_workspace, monkeypatch):
    state, client = owner_workspace, owner_workspace["client"]

    def forbidden(*args, **kwargs):
        pytest.fail("Passive owner navigation must not start provider retrieval.")

    monkeypatch.setattr(news_feed, "_refresh", forbidden)
    configuration = client.get(PREFIX + "/config")
    assert configuration.status_code == 200
    data = configuration.json()
    assert data["agent_count"] == 7 and data["maximum_newsdata_articles_per_refresh"] == 50
    assert data["coverage_hours"] == 12 and data["display_timezone"] == "Asia/Dubai"
    assert data["refresh_mode"] == "manual"
    for agent in data["agents"]:
        assert agent["retrieval_options"] == ["newsdata", "web_search"]
        selected = client.get(f"{PREFIX}/agents/{agent['id']}")
        assert selected.status_code == 200
        assert selected.json()["last_successful_report"] is None
        assert selected.json()["active_job"] is None
        feed = client.get(f"{PREFIX}/agents/{agent['id']}/news?language=en")
        assert feed.status_code == 200, feed.text
        assert feed.json()["refresh_started"] is False and feed.json()["items"] == []
    assert client.get(PREFIX + "/reports").json() == {"reports": []}
    assert state["store"].list_jobs("owner", active_only=False) == []
    assert state["worker"].run_once() is False
    assert not state["estimate"] and not state["generate"] and not state["pdf"]
    assert state["store"].base_store.usage_summary()["daily"]["attempts"] == 0


def test_owner_news_refresh_is_an_explicit_mutation_and_preserves_specific_errors(owner_workspace, monkeypatch):
    state, client, calls = owner_workspace, owner_workspace["client"], []

    def feed(agent_id, language, *, refresh, force, database):
        assert database.path == state["store"].base_store.path
        calls.append((agent_id, language, refresh, force))
        return {"agent_id": agent_id, "items": [], "refresh_started": refresh,
                "error": {"code": "newsdata_daily_credit_limit", "retry_after_seconds": 600}}

    monkeypatch.setattr(news_feed, "get_feed", feed)
    agents = client.get(PREFIX + "/config").json()["agents"]
    for agent in agents:
        identifier = agent["id"]
        passive = client.get(f"{PREFIX}/agents/{identifier}/news?language=en")
        assert passive.status_code == 200 and passive.json()["refresh_started"] is False
        refreshed = client.post(f"{PREFIX}/agents/{identifier}/news/refresh", json={"language": "ar"})
        assert refreshed.status_code == 200 and refreshed.json()["refresh_started"] is True
        assert refreshed.json()["error"]["code"] == "newsdata_daily_credit_limit"
        assert calls[-2:] == [(identifier, "en", False, False), (identifier, "ar", True, True)]
    before = len(calls)
    assert client.get(f"{PREFIX}/agents/{AGENT}/news?language=fr").status_code == 422
    assert client.post(f"{PREFIX}/agents/{AGENT}/news/refresh", json={"owner_id": "public:forged"}).status_code == 422
    assert len(calls) == before


def test_owner_identity_is_fixed_and_cannot_be_replaced_by_cookie_query_or_request_body(owner_workspace):
    state, client = owner_workspace, owner_workspace["client"]
    path = f"{PREFIX}/agents/{AGENT}/reports"
    for key in ("owner_id", "session_id"):
        denied = client.post(path, json={key: state["public_owner"]}, headers={"Idempotency-Key": "forged"})
        assert denied.status_code == 422
    assert client.post(path, json={}).status_code == 422
    response = client.post(path, params={"owner_id": state["public_owner"], "session_id": state["public_owner"]},
        json={"topic": "Synthetic scope", "language": "en"}, headers={"Idempotency-Key": "fixed-owner"})
    assert response.status_code == 202, response.text
    job_id = response.json()["job"]["job_id"]
    assert state["store"].get_job("owner", job_id)["job_id"] == job_id
    with pytest.raises(StoreError) as foreign:
        state["store"].get_job(state["public_owner"], job_id)
    assert foreign.value.status_code == 404
    with state["store"].base_store._connect() as connection:
        assert connection.execute("SELECT owner_id FROM research_jobs WHERE job_id=?", (job_id,)).fetchone()[0] == "owner"
    replay = owner_submit(state, key="fixed-owner")
    assert replay.status_code == 202 and replay.json()["job"]["job_id"] == job_id
    assert not state["estimate"] and not state["generate"]
    assert state["store"].base_store.usage_summary()["daily"]["attempts"] == 0


def test_owner_and_public_jobs_reports_and_pdfs_are_mutually_isolated(owner_workspace):
    state, client = owner_workspace, owner_workspace["client"]
    owned = owner_complete(state, key="same-request")
    owner_job, owner_report = owned["job"]["job_id"], owned["report"]["report_id"]
    own_pdf = client.get(f"{PREFIX}/reports/{owner_report}/pdf")
    assert own_pdf.status_code == 200 and own_pdf.content.startswith(b"%PDF-")
    assert "no-store" in own_pdf.headers["cache-control"]
    assert_workspace_public(owned)

    client.auth = None
    for suffix in (f"/jobs/{owner_job}", f"/reports/{owner_report}", f"/reports/{owner_report}/pdf"):
        response = client.get(PUBLIC_PREFIX + suffix)
        assert response.status_code == 404, response.text
        assert owner_report not in response.text and owner_job not in response.text
    assert client.post(f"{PUBLIC_PREFIX}/reports/{owner_report}/pdf/retry", json={}).status_code == 404
    assert client.get(PUBLIC_PREFIX + "/reports").json()["reports"] == []
    assert client.get(f"{PUBLIC_PREFIX}/agents/{AGENT}").json()["last_successful_report"] is None

    public = complete(state, key="same-request")
    public_job, public_report = public["job"]["job_id"], public["report"]["report_id"]
    assert public_job != owner_job and public_report != owner_report
    client.auth = OWNER_AUTH
    before = state["store"].base_store.usage_summary()
    for suffix in (f"/jobs/{public_job}", f"/reports/{public_report}", f"/reports/{public_report}/pdf"):
        response = client.get(PREFIX + suffix, params={"owner_id": state["public_owner"]})
        assert response.status_code == 404, response.text
        assert public_report not in response.text and public_job not in response.text
    assert client.post(f"{PREFIX}/reports/{public_report}/pdf/retry", json={}).status_code == 404
    owner_history = client.get(PREFIX + "/reports", params={"owner_id": state["public_owner"]}).json()["reports"]
    assert [item["report_id"] for item in owner_history] == [owner_report]
    assert client.get(f"{PREFIX}/agents/{AGENT}").json()["last_successful_report"]["report_id"] == owner_report
    public_history = client.get(PUBLIC_PREFIX + "/reports").json()["reports"]
    assert [item["report_id"] for item in public_history] == [public_report]
    assert state["store"].base_store.usage_summary() == before
    assert len(state["generate"]) == 2 and state["worker"].run_once() is False


@pytest.mark.parametrize("agent_id", sorted(RETIRED_AGENT_IDS))
def test_owner_retired_agents_cannot_be_opened_refreshed_or_run(owner_workspace, monkeypatch, agent_id):
    state, client = owner_workspace, owner_workspace["client"]

    def forbidden(*args, **kwargs):
        pytest.fail("A retired agent must never reach news retrieval.")

    monkeypatch.setattr(news_feed, "get_feed", forbidden)
    assert client.get(f"{PREFIX}/agents/{agent_id}").status_code == 404
    assert client.get(f"{PREFIX}/agents/{agent_id}/news").status_code == 404
    assert client.post(f"{PREFIX}/agents/{agent_id}/news/refresh", json={}).status_code == 404
    assert client.post(f"{PREFIX}/agents/{agent_id}/reports", json={},
        headers={"Idempotency-Key": "retired"}).status_code == 404
    assert not state["estimate"] and not state["generate"]
    assert state["store"].list_jobs("owner", active_only=False) == []


def test_owner_pdf_retry_reuses_saved_evidence_without_another_research_call(owner_workspace):
    state, client = owner_workspace, owner_workspace["client"]
    state["pdf_failure"] = True
    result = owner_complete(state)
    report_id = result["report"]["report_id"]
    assert result["report"]["pdf"]["status"] == "failed"
    assert client.get(f"{PREFIX}/reports/{report_id}/pdf").status_code == 409
    before = state["store"].base_store.usage_summary()
    retry = client.post(f"{PREFIX}/reports/{report_id}/pdf/retry", json={})
    assert retry.status_code == 202 and retry.json()["job"]["job_id"] == result["job"]["job_id"]
    state["pdf_failure"] = False
    assert state["worker"].run_once()
    saved = client.get(f"{PREFIX}/reports/{report_id}").json()
    assert saved["result"] == result["report"]["result"] and saved["pdf"]["status"] == "ready"
    assert len(state["estimate"]) == len(state["generate"]) == 1 and len(state["pdf"]) == 2
    assert state["store"].base_store.usage_summary() == before
