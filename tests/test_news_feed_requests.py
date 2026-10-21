"""Explicit feed intents, concurrent report cutoffs and conservative credit reconciliation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from types import SimpleNamespace

import httpx
import pytest

from app import news_feed, retrieval
from app.storage import StoreError
from tests.test_news_feed import NOW, article, database, mock_http, page, refresh


def window(moment):
    return {"start": (moment - timedelta(hours=12)).isoformat(), "end": moment.isoformat(), "timezone": "UTC"}


def diverse_articles():
    return [
        article(1, title="Iran and the United States begin nuclear negotiations", source_name="Publisher One"),
        article(2, title="Iranian forces warn of military shipping risks in Hormuz", source_name="Publisher Two"),
        article(3, title="Tehran foreign minister meets envoys for sanctions talks", source_name="Publisher Three"),
    ]


def deferred_threads(monkeypatch):
    threads = []
    class DeferredThread:
        def __init__(self, *, target, args=(), kwargs=None, **options):
            self.target, self.args, self.kwargs = target, args, kwargs or {}
        def start(self):
            threads.append(self)
        def run(self):
            self.target(*self.args, **self.kwargs)
    monkeypatch.setattr(news_feed, "threading", SimpleNamespace(Thread=DeferredThread))
    return threads


def empty_discovery(monkeypatch):
    calls = []
    def discover(database, agent_id, refresh_id, research_window):
        calls.append((agent_id, refresh_id, research_window))
        return {"sources": [], "excluded": [], "discovered_count": 0, "verified_source_count": 0,
                "stages": [], "web_search": {}}
    monkeypatch.setattr(retrieval, "run_feed_discovery", discover)
    return calls


def captured_snapshots(monkeypatch):
    snapshots = []
    original_finish = news_feed.FeedStore.finish
    def finish(self, agent_id, refresh_id, snapshot, error):
        snapshots.append(snapshot)
        return original_finish(self, agent_id, refresh_id, snapshot, error)
    monkeypatch.setattr(news_feed.FeedStore, "finish", finish)
    return snapshots


def test_explicit_clicks_queue_across_agents_join_duplicates_and_passive_reads_launch_nothing(monkeypatch, database):
    threads = deferred_threads(monkeypatch)
    empty_discovery(monkeypatch)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page(diverse_articles()))
    mock_http(monkeypatch, handler)
    first = news_feed.get_feed("iran-news", "en", refresh=True, database=database)
    duplicate = news_feed.get_feed("iran-news", "ar", force=True, database=database)
    second = news_feed.get_feed("geopolitical-morning", "en", refresh=True, database=database)
    assert first["refresh_accepted"] and first["refresh_state"] == "queued" and first["queue_position"] == 1
    assert duplicate["refresh_joined"] and duplicate["refresh_request_id"] == first["refresh_request_id"]
    assert second["refresh_state"] == "queued" and second["queue_position"] == 2
    assert len(threads) == 2 and calls == []
    with database._connect() as connection:
        before = [tuple(row) for row in connection.execute("SELECT * FROM workspace_news_requests ORDER BY rowid")]
    for agent in news_feed.QUERIES:
        for language in ("en", "ar"):
            passive = news_feed.get_feed(agent, language, database=database)
            assert not passive["refresh_accepted"]
    with database._connect() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM workspace_news_requests ORDER BY rowid")] == before
    assert len(threads) == 2 and calls == []
    threads[0].run()
    assert news_feed.get_feed("geopolitical-morning", "en", database=database)["queue_position"] == 1
    threads[1].run()
    assert len(calls) == 2
    assert news_feed.get_feed("iran-news", "en", database=database)["actual_count"] == 3
    # A separate deliberate click bypasses the old successful-cache timer.
    fresh = news_feed.get_feed("iran-news", "en", refresh=True, database=database)
    assert fresh["refresh_request_id"] != first["refresh_request_id"]
    threads[2].run()
    assert len(calls) == 3


def test_explicit_action_recovers_orphaned_manual_queue_head_without_passive_research(monkeypatch, database):
    threads = deferred_threads(monkeypatch)
    empty_discovery(monkeypatch)
    calls = []
    mock_http(monkeypatch, lambda request: (calls.append(request) or httpx.Response(200, json=page(diverse_articles()))))
    store = news_feed.FeedStore(database)
    orphan, _, _ = store.request("iran-news", background=True, allow_web_fallback=True)
    # Simulate a persisted accepted request whose process exited before its waiter started.
    news_feed.get_feed("iran-news", database=database)
    news_feed.get_feed("geopolitical-morning", database=database)
    assert threads == [] and calls == []
    second = news_feed.get_feed("geopolitical-morning", refresh=True, database=database)
    assert len(threads) == 2 and second["queue_position"] == 2
    news_feed.get_feed("geopolitical-morning", refresh=True, database=database)
    assert len(threads) == 2  # Existing local waiters are not duplicated.
    threads[0].run()
    threads[1].run()
    assert len(calls) == 2
    assert store.request_info("iran-news", orphan["request_id"])["state"] == "completed"
    assert store.request_info("geopolitical-morning", second["refresh_request_id"])["state"] == "completed"


def test_concurrent_duplicate_intents_share_one_persisted_request(database):
    store = news_feed.FeedStore(database)
    with ThreadPoolExecutor(max_workers=8) as executor:
        requests = list(executor.map(lambda _: store.request("iran-news", background=True), range(16)))
    assert len({request[0]["request_id"] for request in requests}) == 1
    assert sum(not request[2] for request in requests) == 1
    assert sum(request[1] for request in requests) == 1
    with database._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspace_news_requests").fetchone()[0] == 1


def test_queue_capacity_fifo_and_concurrent_claims_are_atomic(monkeypatch, database):
    monkeypatch.setattr(news_feed, "MAX_QUEUED_REFRESHES", 2)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page([])))
    store = news_feed.FeedStore(database)
    first, _, _ = store.request("iran-news")
    second, _, _ = store.request("geopolitical-morning")
    with pytest.raises(StoreError) as full:
        store.request("education-observatory")
    assert full.value.code == "newsdata_refresh_queue_full"
    assert store.request("iran-news")[0]["request_id"] == first["request_id"]
    assert store.claim_request(second["request_id"]) is None
    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(lambda _: store.claim_request(first["request_id"]), range(16)))
    accepted = [claim for claim in claims if claim]
    assert len(accepted) == 1
    assert store.claim_request(second["request_id"]) is None
    news_feed._refresh(store, "iran-news", accepted[0])
    assert store.request_info("iran-news", first["request_id"])["state"] == "completed"
    assert store.claim_request(second["request_id"])[0] == second["request_id"]


def test_new_report_does_not_reuse_a_recent_snapshot_from_before_its_cutoff(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article(len(calls))]))
    mock_http(monkeypatch, handler)
    old = refresh(database)
    database.test_clock[0] += timedelta(seconds=10)
    requested = window(database.test_clock[0])
    fresh = news_feed.get_report_feed("iran-news", research_window=requested, language="en", database=database)
    assert len(calls) == 2 and fresh["snapshot_id"] != old["snapshot_id"]
    assert fresh["items"][0]["article_id"] == "synthetic-2"
    assert fresh["freshness"]["checked_after_request"] is True
    # Reconnecting the same frozen generation can use the work it already did.
    replay = news_feed.get_report_feed("iran-news", research_window=requested, language="en", database=database)
    assert len(calls) == 2 and replay["snapshot_id"] == fresh["snapshot_id"]
    database.test_clock[0] += timedelta(seconds=1)
    newest = news_feed.get_report_feed("iran-news", research_window=window(database.test_clock[0]),
                                      language="en", database=database)
    assert len(calls) == 3 and newest["snapshot_id"] != fresh["snapshot_id"]


@pytest.mark.parametrize("fetch_started_after_cutoff", [False, True])
def test_report_joins_only_running_retrieval_started_after_its_request(monkeypatch, database, fetch_started_after_cutoff):
    calls = []
    mock_http(monkeypatch, lambda request: (calls.append(request) or httpx.Response(200, json=page([article(len(calls))]))))
    store = news_feed.FeedStore(database)
    cutoff = NOW if fetch_started_after_cutoff else NOW + timedelta(seconds=1)
    database.test_clock[0] = NOW + timedelta(seconds=2) if fetch_started_after_cutoff else NOW
    pending, _, _ = store.request("iran-news", window(database.test_clock[0]))
    running = store.claim_request(pending["request_id"])
    assert running is not None
    database.test_clock[0] = NOW + timedelta(seconds=2)
    elapsed = [0.0]
    released = []
    def sleep(seconds):
        elapsed[0] += seconds
        if not released:
            released.append(True)
            news_feed._refresh(store, "iran-news", running)
        database.test_clock[0] += timedelta(seconds=seconds)
    monkeypatch.setattr(news_feed, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep))
    result = news_feed.get_report_feed("iran-news", research_window=window(cutoff), language="en", database=database)
    assert result["freshness"]["checked_after_request"] is True
    assert len(calls) == (1 if fetch_started_after_cutoff else 2)
    assert (result["snapshot_id"] == running[0]) is fetch_started_after_cutoff
    assert result["items"][0]["article_id"] == ("synthetic-1" if fetch_started_after_cutoff else "synthetic-2")


@pytest.mark.parametrize("completed", [False, True])
def test_later_started_fetch_with_older_frozen_cutoff_cannot_satisfy_new_report(monkeypatch, database, completed):
    calls = []
    mock_http(monkeypatch, lambda request: (calls.append(request) or httpx.Response(200, json=page([article(len(calls))]))))
    store = news_feed.FeedStore(database)
    pending, _, _ = store.request("iran-news", window(NOW))
    database.test_clock[0] = NOW + timedelta(seconds=2)
    older_window_claim = store.claim_request(pending["request_id"])
    elapsed = [0.0]
    released = []
    if completed:
        news_feed._refresh(store, "iran-news", older_window_claim)
        released.append(True)
    def sleep(seconds):
        elapsed[0] += seconds
        if not released:
            released.append(True)
            news_feed._refresh(store, "iran-news", older_window_claim)
        database.test_clock[0] += timedelta(seconds=seconds)
    monkeypatch.setattr(news_feed, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep))
    result = news_feed.get_report_feed("iran-news", research_window=window(NOW + timedelta(seconds=1)),
                                      language="en", database=database)
    assert len(calls) == 2 and result["snapshot_id"] != older_window_claim[0]
    assert result["items"][0]["article_id"] == "synthetic-2"
    assert result["freshness"]["checked_after_request"] is True
    assert result["research_window"]["end"] >= news_feed._stamp(NOW + timedelta(seconds=1))


def test_failed_request_keeps_its_own_error_when_another_refresh_replaces_shared_status(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=page())
        if len(calls) == 2:
            return httpx.Response(200, json={"status": "success", "results": {}, "totalResults": 0})
        return httpx.Response(503, json={"status": "error", "results": {"code": "ServiceUnavailable"}})
    mock_http(monkeypatch, handler)
    old = refresh(database)
    database.test_clock[0] += timedelta(seconds=1)
    requested = window(database.test_clock[0])
    original_refresh = news_feed._refresh
    own_error = []
    def finish_then_replace(store, agent_id, claim, **options):
        original_refresh(store, agent_id, claim, **options)
        own_error.append(json.loads(store.request_info(agent_id, claim[0])["error_json"]))
        database.test_clock[0] += timedelta(seconds=1)
        later, _, _ = store.request(agent_id)
        other_claim = store.claim_request(later["request_id"])
        assert other_claim is not None
        original_refresh(store, agent_id, other_claim)
    monkeypatch.setattr(news_feed, "_refresh", finish_then_replace)
    result = news_feed.get_report_feed("iran-news", research_window=requested, language="en", database=database)
    assert len(calls) == 3
    assert own_error[0]["code"] == "newsdata_invalid_response"
    assert result["error"]["code"] == own_error[0]["code"]
    assert result["error"]["reference"] == own_error[0]["reference"]
    assert result["snapshot_id"] == old["snapshot_id"] and result["partial"]
    assert result["freshness"]["checked_after_request"] is False


def test_report_timeout_cleans_sole_foreground_intent_even_when_request_setup_is_delayed(monkeypatch, database):
    store = news_feed.FeedStore(database)
    store.claim("geopolitical-morning")  # Another explicit operation owns the provider lease.
    monkeypatch.setattr(news_feed, "MAX_REFRESH_SECONDS", 5)
    elapsed = [0.0]
    def advance(seconds):
        elapsed[0] += seconds
        database.test_clock[0] = NOW + timedelta(seconds=elapsed[0])
    original_request = news_feed.FeedStore.request
    def delayed_request(self, *args, **kwargs):
        advance(2)
        return original_request(self, *args, **kwargs)
    monkeypatch.setattr(news_feed.FeedStore, "request", delayed_request)
    monkeypatch.setattr(news_feed, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=advance))
    with pytest.raises(StoreError) as timeout:
        news_feed.get_report_feed("iran-news", research_window=window(NOW), database=database)
    assert timeout.value.code in {"newsdata_timeout", "newsdata_refresh_queue_timeout"}
    request = store.request_info("iran-news")
    assert request["state"] == "failed"
    with database._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspace_news_requests WHERE cache_key=? AND state='queued'",
                                  (news_feed._key("iran-news"),)).fetchone()[0] == 0


@pytest.mark.parametrize("manual_upgrade", [False, True])
def test_timed_out_caller_does_not_cancel_a_later_join_or_manual_upgrade(database, manual_upgrade):
    store = news_feed.FeedStore(database)
    first, _, _ = store.request("iran-news")
    database.test_clock[0] += timedelta(seconds=30)
    joined, launch, reused = store.request("iran-news", background=manual_upgrade)
    assert reused and joined["request_id"] == first["request_id"]
    assert launch is manual_upgrade
    database.test_clock[0] = NOW + timedelta(seconds=91)
    store.fail_queued(first["request_id"], foreground_only=True)
    assert store.request_info("iran-news", first["request_id"])["state"] == "queued"
    database.test_clock[0] = NOW + timedelta(seconds=121)
    store.fail_queued(first["request_id"], foreground_only=True)
    assert store.request_info("iran-news", first["request_id"])["state"] == ("queued" if manual_upgrade else "failed")


def test_manual_join_upgrades_running_report_retrieval_before_fallback_decision(monkeypatch, database):
    store = news_feed.FeedStore(database)
    initial, _, _ = store.request("iran-news", allow_web_fallback=False)
    claim = store.claim_request(initial["request_id"])
    web_calls = empty_discovery(monkeypatch)
    joined = []
    def handler(request):
        joined.append(store.request("iran-news", background=True, allow_web_fallback=True))
        return httpx.Response(200, json=page([]))
    mock_http(monkeypatch, handler)
    news_feed._refresh(store, "iran-news", claim, allow_web_fallback=False)
    assert len(joined) == 1 and joined[0][2] is True and joined[0][1] is False
    assert joined[0][0]["request_id"] == initial["request_id"]
    assert len(web_calls) == 1 and web_calls[0][1] == initial["request_id"]


def test_manual_join_after_closed_fallback_decision_queues_one_followup(monkeypatch, database):
    store = news_feed.FeedStore(database)
    initial, _, _ = store.request("iran-news", allow_web_fallback=False)
    claim = store.claim_request(initial["request_id"])
    web_calls = empty_discovery(monkeypatch)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page([])))
    original_finish = store.finish
    followup = []
    def finish(agent_id, refresh_id, snapshot, error):
        if refresh_id == initial["request_id"]:
            followup.append(store.request(agent_id, background=True, allow_web_fallback=True))
        return original_finish(agent_id, refresh_id, snapshot, error)
    monkeypatch.setattr(store, "finish", finish)
    news_feed._refresh(store, "iran-news", claim, allow_web_fallback=False)
    assert web_calls == [] and len(followup) == 1
    pending, launch, joined = followup[0]
    assert launch and not joined and pending["request_id"] != initial["request_id"]
    assert pending["state"] == "queued"
    duplicate, launch_again, reused = store.request("iran-news", background=True, allow_web_fallback=True)
    assert reused and not launch_again and duplicate["request_id"] == pending["request_id"]
    news_feed._run_queued_refresh(store, "iran-news", pending["request_id"])
    assert len(web_calls) == 1 and web_calls[0][1] == pending["request_id"]


@pytest.mark.parametrize("status,data,expected_credits", [
    (200, page([]), 0),
    (400, {"status": "error", "results": {"code": "UnsupportedFilter"}}, 0),
    (422, {"status": "error", "results": {"code": "InvalidRequest"}}, 0),
    (400, {"status": "error", "results": {"code": "UnrecognizedError"}}, 1),
    (401, {"status": "error", "results": {"code": "Unauthorized"}}, 1),
    (429, {"status": "error", "results": {"code": "ApiLimitExceeded"}}, 1),
    (503, {"status": "error", "results": {"code": "ServiceUnavailable"}}, 1),
    (200, page([], totalResults=3), 1),
    (200, {"status": "success", "results": {}, "totalResults": 0}, 1),
])
def test_credit_is_reserved_before_http_and_only_confirmed_nonchargeable_response_releases_it(
        monkeypatch, database, status, data, expected_credits):
    snapshots = captured_snapshots(monkeypatch)
    observed = []
    def handler(request):
        with database._connect() as connection:
            observed.append(news_feed.accounted_credits(connection, local_date="2026-10-20"))
        return httpx.Response(status, json=data)
    mock_http(monkeypatch, handler)
    refresh(database)
    assert observed == [1]
    assert snapshots[0]["pagination"]["credits_reserved"] == 1
    assert snapshots[0]["pagination"]["accounted_credits"] == expected_credits
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == expected_credits
        assert connection.execute("SELECT COUNT(*) FROM workspace_news_credits").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM workspace_news_credit_adjustments").fetchone()[0] == 1 - expected_credits


def test_credit_adjustments_are_idempotent_bound_to_charge_and_original_dubai_day(database):
    store = news_feed.FeedStore(database)
    claim = store.claim("iran-news")
    first = store.charge("iran-news", claim[0])
    second = store.charge("iran-news", claim[0])
    store.reconcile(first, "another-refresh", "empty_success")
    store.reconcile(first, claim[0], "empty_success")
    store.reconcile(first, claim[0], "invalid_request")
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 1
    database.test_clock[0] = NOW + timedelta(hours=12)  # Next Dubai date.
    next_claim = store.claim("iran-news", explicit_retry=True)
    store.charge("iran-news", next_claim[0])
    # A late confirmed response releases its original day's charge, not today's.
    store.reconcile(second, claim[0], "invalid_request")
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 0
        assert news_feed.accounted_credits(connection, local_date="2026-10-21") == 1
        assert news_feed.accounted_credits(connection, local_month="2026-10") == 1
        assert connection.execute("SELECT COUNT(*) FROM workspace_news_credits").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM workspace_news_credit_adjustments").fetchone()[0] == 2


def test_new_explicit_manual_request_during_provider_cooldown_is_web_only_and_keeps_retry_after(monkeypatch, database):
    snapshots = captured_snapshots(monkeypatch)
    http_calls = []
    def handler(request):
        http_calls.append(request)
        return httpx.Response(429, json={"status": "error", "results": {"code": "RateLimitExceeded"}},
                              headers={"Retry-After": "1800"})
    mock_http(monkeypatch, handler)
    refresh(database)
    store = news_feed.FeedStore(database)
    original_deadline = store.provider_state()["cooldown_until"]
    threads = deferred_threads(monkeypatch)
    web_calls = empty_discovery(monkeypatch)
    database.test_clock[0] += timedelta(seconds=30)
    queued = news_feed.get_feed("iran-news", "en", refresh=True, database=database)
    assert queued["refresh_accepted"] and queued["refresh_state"] == "queued"
    threads[0].run()
    assert len(http_calls) == 1 and len(web_calls) == 1
    assert store.provider_state()["cooldown_until"] == original_deadline
    result = news_feed.get_feed("iran-news", "en", database=database)
    assert result["provider_retry_at"] == original_deadline
    assert snapshots[-1]["pagination"]["requests"] == 0
    assert result["retrieval"]["web_search"]["attempted"] is True
