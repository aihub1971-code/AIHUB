"""Application boundaries for the shared feed, archive access and job diagnostics."""
from datetime import timedelta
import json

import pytest
from fastapi import HTTPException

from app import news_feed, newsdata, workspace as api
from app.registry import RETIRED_AGENT_IDS
from app.report_jobs import ReportWorker
from app.storage import QuotaLimits, StoreError
from tests.test_news_feed import article, mock_http, page, refresh
from tests.test_report_store import AGENT, NOW, OWNER, fake_pdf, synthetic_result
from tests.test_workspace import PREFIX, complete, submit, workspace
from tests.test_report_jobs import reports


def test_workspace_exposes_all_seven_topic_feeds_and_the_100_item_target(workspace):
    config = workspace["client"].get(PREFIX + "/config").json()
    assert config["agent_count"] == 9
    assert set(config["news_enabled_agents"]) == set(news_feed.QUERIES)
    assert config["maximum_news_items"] == 100
    assert config["news_refresh_interval_seconds"] == 900
    assert all(p["news_feed_enabled"] == (p["id"] in news_feed.QUERIES) for p in config["agents"])


def test_manual_and_dashboard_news_share_provider_cache_without_starting_reports(workspace, monkeypatch):
    import httpx
    base = workspace["store"].base_store
    monkeypatch.setenv("NEWSDATA_API_KEY", "synthetic-key-not-real")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article()]))
    mock_http(monkeypatch, handler)
    seeded = refresh(base)
    assert seeded["actual_count"] == 1
    client = workspace["client"]
    path = PREFIX + "/agents/iran-news/news"
    first = client.get(path, params={"language": "en"})
    second = client.post(path + "/refresh", json={"language": "ar"})
    assert first.status_code == second.status_code == 200
    assert first.json()["snapshot_id"] == second.json()["snapshot_id"]
    assert first.json()["actual_count"] == second.json()["actual_count"] == 1
    assert first.json()["items"][0]["published_at"].endswith("+00:00")
    assert second.json()["items"][0]["translation_status"] == "original_only"
    assert len(calls) == 1
    assert workspace["store"].list_jobs(api.owner("not-used")) == []
    assert base.usage_summary()["daily"]["attempts"] == 0
    assert base.usage_summary()["provider_credits"]["newsdata"]["daily_accounted"] == 1


@pytest.mark.parametrize("agent", sorted(RETIRED_AGENT_IDS))
def test_retired_agents_have_no_news_launch_route_but_owned_archive_stays_readable(workspace, agent):
    record = complete(workspace)["report"]
    base = workspace["store"].base_store
    with base._transaction() as connection:
        connection.execute("UPDATE research_reports SET agent_id=? WHERE report_id=?", (agent, record["report_id"]))
    client = workspace["client"]
    for suffix in ("", "/news"):
        assert client.get(PREFIX + "/agents/" + agent + suffix).status_code == 404
    assert client.post(PREFIX + "/agents/" + agent + "/news/refresh", json={"language": "en"}).status_code == 404
    assert submit(workspace, agent_id=agent, key="retired").status_code == 404
    history = client.get(PREFIX + "/reports", params={"agent_id": agent})
    assert history.status_code == 200
    assert history.json()["reports"][0]["report_id"] == record["report_id"]
    assert client.get(PREFIX + "/reports/" + record["report_id"] + "/pdf").content.startswith(b"%PDF-")


def test_news_routes_validate_language_and_support_without_provider_access(workspace):
    client = workspace["client"]
    assert client.get(PREFIX + "/agents/iran-news/news?language=xx").status_code == 422
    assert client.post(PREFIX + "/agents/iran-news/news/refresh", json={"language": "xx"}).status_code == 422
    assert client.get(PREFIX + "/agents/historical-reading-companion/news").status_code == 404


def test_dashboard_page_credit_blocks_an_over_budget_legacy_request(workspace):
    base = workspace["store"].base_store
    base._limits = QuotaLimits(newsdata_daily_credit_limit=1)
    feed = news_feed.FeedStore(base)
    claim = feed.claim("iran-news")
    feed.charge("iran-news", claim[0])
    with pytest.raises(StoreError) as error:
        base.reserve_run("iran-news", "Synthetic old client", 0, provider="newsdata", newsdata_credits=1,
                         newsdata_context=newsdata.initial_context(NOW), allow_zero_cost=True)
    assert error.value.code == "newsdata_daily_credit_limit"
    usage = base.usage_summary()
    assert usage["provider_credits"]["newsdata"]["daily_remaining"] == 0
    assert usage["provider_credits"]["newsdata"]["monthly_accounted"] == 1
    assert usage["daily"]["attempts"] == 0


def test_public_projection_keeps_100_articles_and_provider_metadata_without_internal_diagnostics():
    value = {"items": [{"id": str(i), "headline": f"Fixture {i}", "published_at": NOW.isoformat(),
                        "url": f"https://fixture.example/{i}", "private": "private-sentinel"} for i in range(100)],
        "sources": [], "actual_article_count": 100, "analyzed_article_count": 100, "requested_article_target": 100,
        "news_feed": {"status": "cached", "pagination": {"requests": 10, "raw_count": 100, "accepted_count": 100},
                      "provider_plan": {"configured_plan": "unknown", "plan_verified": False, "api_key": "private-sentinel"},
                      "private": "private-sentinel"}, "freshness": {"coverage_complete": False}}
    result = api.result_view(value)
    assert len(result["items"]) == 100
    assert result["actual_article_count"] == result["analyzed_article_count"] == 100
    assert result["news_feed"]["pagination"]["raw_count"] == 100
    assert "private-sentinel" not in json.dumps(result)


def test_real_worker_accepts_bounded_second_discovery_then_persists_generation_timestamp(reports):
    stages = []
    def generate(agent_id, topic, *, progress, **options):
        for stage in ("searching", "checking_sources", "searching", "checking_sources", "analyzing"):
            progress(stage)
            stages.append(stage)
        return synthetic_result({"agent_id": agent_id, "topic": topic, "language": options["language"],
                                 "research_window": options["research_window"]})
    worker = ReportWorker(reports,
        estimate=lambda *a, **kw: {"estimated_cost_usd": .1, "max_input_tokens": 100, "max_output_tokens": 100,
                                  "max_web_search_calls": 1},
        generator=generate, pdf_renderer=lambda result, path: fake_pdf(path))
    job = reports.submit(OWNER, AGENT, "Bounded follow-up discovery", "en")["job"]
    reports.base_store.clock = lambda: NOW + timedelta(seconds=45)
    assert worker.run_once()
    done = reports.get_job(OWNER, job["job_id"])
    assert done["status"] == "ready"
    report = reports.get_report(OWNER, done["report_id"])
    assert report["result"]["created_at"] == report["created_at"]
    assert stages.count("searching") == 2


def test_persistence_cannot_transition_back_to_provider_work(reports):
    reports.submit(OWNER, AGENT, "No provider replay", "en")
    job = reports.claim("synthetic-worker")
    reports.set_stage(job["job_id"], job["lease_token"], "saving")
    with pytest.raises(StoreError) as error:
        reports.set_stage(job["job_id"], job["lease_token"], "searching")
    assert error.value.code == "invalid_report_stage"


def test_failed_job_preserves_safe_stage_reference_and_provider_diagnostics(workspace, caplog):
    previous = complete(workspace)["report"]
    def fail(*args, progress, **kwargs):
        progress("analyzing")
        raise HTTPException(429, detail={"code": "openai_quota_exceeded", "provider_status": 429,
            "provider_code": "insufficient_quota", "request_id": "req_fixture", "message": "secret-body-sentinel",
            "api_key": "secret-key-sentinel"})
    workspace["worker"].generator = fail
    queued = submit(workspace, key="diagnostic-attempt").json()["job"]
    assert workspace["worker"].run_once()
    value = workspace["client"].get(PREFIX + "/jobs/" + queued["job_id"]).json()
    assert value["job"]["error"] == {"code": "openai_quota_exceeded", "reference": queued["job_id"],
                                     "stage": "analyzing", "retryable": False}
    selected = workspace["client"].get(PREFIX + "/agents/" + AGENT).json()
    assert selected["last_job"]["job_id"] == queued["job_id"]
    assert selected["last_successful_report"]["report_id"] == previous["report_id"]
    with workspace["store"].base_store._connect() as connection:
        row = connection.execute("SELECT error_diagnostics_json FROM research_jobs WHERE job_id=?", (queued["job_id"],)).fetchone()
    diagnostics = json.loads(row[0])
    assert diagnostics["request_id"] == "req_fixture" and diagnostics["provider_status"] == 429
    assert queued["job_id"] in caplog.text and "req_fixture" in caplog.text
    assert "secret-" not in caplog.text + row[0] + json.dumps(value)
