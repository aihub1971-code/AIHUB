"""Manual refresh boundaries using synthetic provider responses only."""
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from app import news_feed
from tests.test_news_feed import article, mock_http, page, refresh
from tests.test_report_store import NOW
from tests.test_workspace import PREFIX, workspace


@pytest.fixture
def inline_feed_threads(monkeypatch):
    """Finish a requested feed fetch deterministically, without racing GETs."""
    started = []

    class InlineThread:
        def __init__(self, *, target, args, name, daemon, kwargs=None):
            self.target, self.args, self.name, self.kwargs = target, args, name, kwargs or {}

        def start(self):
            started.append(self.name)
            self.target(*self.args, **self.kwargs)

    # Replace this module's binding, not threading.Thread used by TestClient.
    monkeypatch.setattr(news_feed, "threading", SimpleNamespace(Thread=InlineThread))
    return started


def credits(database):
    with database._connect() as connection:
        return news_feed.accounted_credits(connection,
            local_date=database._now().astimezone(news_feed.DUBAI).date().isoformat())


@pytest.mark.parametrize("cache_age_hours", [None, 0, .5, 13],
                         ids=["no-cache", "recent", "refresh-due", "articles-expired"])
def test_passive_visits_news_reads_and_language_changes_never_fetch(
        workspace, monkeypatch, inline_feed_threads, cache_age_hours):
    base = workspace["store"].base_store
    clock = [NOW]
    base.clock = lambda: clock[0]
    monkeypatch.setenv("NEWSDATA_API_KEY", "synthetic-manual-refresh-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article()]))

    mock_http(monkeypatch, handler)
    saved = refresh(base) if cache_age_hours is not None else None
    clock[0] += timedelta(hours=cache_age_hours or 0)
    feed_store = news_feed.FeedStore(base)
    previous_row = feed_store.read("iran-news")
    previous_provider = feed_store.provider_state()
    previous_calls, previous_credits = len(calls), credits(base)

    client = workspace["client"]
    for path in ("/", "/admin", "/v1/public/bootstrap", PREFIX + "/agents/iran-news"):
        assert client.get(path).status_code == 200
    config = client.get(PREFIX + "/config")
    assert config.status_code == 200
    assert config.json()["refresh_mode"] == "manual"
    for path in ("/v1/system", "/v1/usage", "/v1/reports", "/v1/schedules"):
        response = client.get(path, auth=("test-owner", "test-password-not-for-production"))
        assert response.status_code == 200
        if path == "/v1/system":
            assert response.json()["news_refresh_mode"] == "manual"
            assert response.json()["scheduler_enabled"] is False
            assert response.json()["scheduler_running"] is False

    # Include all topic feeds: choosing another topic must not create its cache.
    for agent_id in news_feed.QUERIES:
        for language in ("en", "ar", "en"):
            response = client.get(PREFIX + f"/agents/{agent_id}/news", params={"language": language})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["refresh_mode"] == "manual"
            assert result["refresh_started"] is False
            if agent_id == "iran-news" and saved is not None:
                assert result["snapshot_id"] == saved["snapshot_id"]
                assert result["last_successful_refresh_at"] == saved["last_successful_refresh_at"]
                assert result["actual_count"] == (0 if cache_age_hours == 13 else 1)
                assert result["stale"] is (cache_age_hours >= .5)
            else:
                assert result["snapshot_id"] is None
                assert result["items"] == []

    assert inline_feed_threads == []
    assert len(calls) == previous_calls
    assert credits(base) == previous_credits
    assert feed_store.read("iran-news") == previous_row
    assert feed_store.provider_state() == previous_provider
    assert base.usage_summary()["daily"]["attempts"] == 0


def test_feed_default_is_cache_only_even_without_a_saved_snapshot(
        workspace, monkeypatch, inline_feed_threads):
    base = workspace["store"].base_store
    monkeypatch.setenv("NEWSDATA_API_KEY", "synthetic-manual-refresh-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article()]))

    mock_http(monkeypatch, handler)
    for language in ("en", "ar"):
        result = news_feed.get_feed("iran-news", language, database=base)
        assert result["snapshot_id"] is None and result["items"] == []
        assert result["refresh_started"] is False
    assert calls == inline_feed_threads == []
    assert credits(base) == 0


def test_each_completed_manual_refresh_can_be_followed_by_an_immediate_fresh_request(
        workspace, monkeypatch, inline_feed_threads):
    base = workspace["store"].base_store
    clock = [NOW]
    base.clock = lambda: clock[0]
    monkeypatch.setenv("NEWSDATA_API_KEY", "synthetic-manual-refresh-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article(len(calls))]))

    mock_http(monkeypatch, handler)
    client = workspace["client"]
    path = PREFIX + "/agents/iran-news/news"
    assert client.get(path).json()["snapshot_id"] is None
    assert calls == []
    first = client.post(path + "/refresh", json={"language": "en"})
    assert first.status_code == 200, first.text
    assert first.json()["refresh_started"] is True
    assert first.json()["actual_count"] == 1
    assert len(calls) == len(inline_feed_threads) == credits(base) == 1

    repeated = client.post(path + "/refresh", json={"language": "ar"}).json()
    assert repeated["snapshot_id"] != first.json()["snapshot_id"]
    assert repeated["refresh_started"] is True
    assert len(calls) == len(inline_feed_threads) == credits(base) == 2

    clock[0] += timedelta(seconds=news_feed.REFRESH_INTERVAL_SECONDS + 1)
    passive = client.get(path, params={"language": "ar"}).json()
    assert passive["snapshot_id"] == repeated["snapshot_id"]
    assert passive["stale"] and len(calls) == 2
    updated = client.post(path + "/refresh", json={"language": "ar"}).json()
    assert updated["refresh_started"] is True
    assert updated["snapshot_id"] != repeated["snapshot_id"]
    assert len(calls) == len(inline_feed_threads) == credits(base) == 3
    assert base.usage_summary()["daily"]["attempts"] == 0


def test_a_new_generation_fetches_again_even_inside_the_previous_cache_interval(workspace, monkeypatch):
    base = workspace["store"].base_store
    clock = [NOW]
    base.clock = lambda: clock[0]
    monkeypatch.setenv("NEWSDATA_API_KEY", "synthetic-manual-refresh-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article(len(calls))]))

    mock_http(monkeypatch, handler)
    first = news_feed.get_report_feed("iran-news", language="en", database=base)
    assert first["actual_count"] == 1 and len(calls) == 1
    clock[0] += timedelta(seconds=1)
    second = news_feed.get_report_feed("iran-news", language="ar", database=base)
    assert second["snapshot_id"] != first["snapshot_id"] and len(calls) == 2
    assert second["freshness"]["checked_after_request"] is True
    clock[0] += timedelta(seconds=60)
    updated = news_feed.get_report_feed("iran-news", language="en", database=base)
    assert updated["snapshot_id"] != second["snapshot_id"]
    assert updated["actual_count"] == 1 and len(calls) == credits(base) == 3
