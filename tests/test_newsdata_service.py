"""NewsData pages spend one atomic credit and never expose or reuse cursors."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading

from fastapi import HTTPException
import pytest

from app import service
from app.newsdata import initial_context
from app.storage import QuotaLimits, Store, StoreError


NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
TASK = "Iran war report"


@pytest.fixture
def store(tmp_path):
    database = Store(tmp_path / "newsdata.sqlite3", QuotaLimits(50, "100", "1000"), clock=lambda: NOW)
    database.initialize()
    return database


@pytest.fixture
def fake_newsdata(monkeypatch):
    state = {"calls": [], "reserved_usd": 0, "actual_usd": 0, "cost_complete": True, "hook": None,
             "returned_count": 1}
    monkeypatch.setenv("NEWSDATA_PAGE_SIZE", "10")
    monkeypatch.setattr(service.runtime, "runtime_is_ready", lambda: False)
    monkeypatch.setattr(service.runtime, "agent_runtime_is_ready", lambda agent_id: agent_id == "iran-news")

    def estimate(agent_id, task, *, language="ar", newsdata_context=None):
        assert agent_id == "iran-news" and newsdata_context is not None
        return {"provider": "newsdata", "model": "fixture-model", "estimated_cost_usd": state["reserved_usd"],
                "max_newsdata_calls": 1, "max_newsdata_credits": 1, "newsdata_context": dict(newsdata_context),
                "max_input_tokens": 100 if state["reserved_usd"] else 0,
                "max_output_tokens": 100 if state["reserved_usd"] else 0, "max_web_search_calls": 0}

    def run(agent_id, task, *, language="ar", newsdata_context=None):
        context = dict(newsdata_context)
        state["calls"].append({"agent_id": agent_id, "task": task, "language": language, "context": context})
        if state["hook"] is not None:
            state["hook"](context)
        return {"provider": "newsdata", "response": "Synthetic English original and Arabic translation",
                "pagination": {"total_results": 100}, "language": language,
                "newsdata": {"returned_item_count": state["returned_count"], "consumed_item_count": state["returned_count"]},
                "newsdata_context": {**context, "next_cursor": f"private-next-cursor-{context['page_index'] + 1}"},
                "estimated_cost_usd": state["actual_usd"], "cost_tracking_complete": state["cost_complete"],
                "newsdata_credits_used": 1, "credit_tracking_complete": True, "usage": {"newsdata_credits": 1}}

    monkeypatch.setattr(service.runtime, "estimate_reservation", estimate)
    monkeypatch.setattr(service.runtime, "run_agent", run)
    return state


def execute(store, *, parent=None, key=None, task=TASK, language="ar"):
    return service.execute_report("iran-news", task, store=store, language=language,
                                  idempotency_key=key, continue_from_report_id=parent)


def reserve_page(store, *, amount="0", context=None, key=None, now=None):
    return store.reserve_run("iran-news", TASK, amount, provider="newsdata", newsdata_credits=1,
                             newsdata_context=context or initial_context(now or NOW), idempotency_key=key, now=now)


def test_zero_openai_cost_page_needs_only_newsdata_readiness_and_keeps_cursors_private(store, fake_newsdata):
    result = execute(store, key="first")
    report_id = result["report_id"]
    assert result["accounting"]["reserved_cost_usd"] == 0
    assert result["accounting"]["newsdata_credits"] == 1
    assert result["accounting"]["newsdata_credit_basis"] == "usage_reported"
    assert result["pagination"]["root_report_id"] == report_id
    assert result["pagination"]["has_more"] is True
    assert result["pagination"]["next_report_id"] is None
    assert len(fake_newsdata["calls"]) == 1
    for public in (result, store.get_report(report_id), store.list_reports(), store.usage_summary()):
        encoded = json.dumps(public)
        assert "private-next-cursor" not in encoded and "newsdata_context" not in encoded
    summary = store.usage_summary()
    assert summary["daily"]["attempts"] == 1 and summary["daily"]["estimated_cost_usd"] == 0
    assert summary["provider_credits"]["newsdata"] == {
        "daily_limit": 200, "daily_reserved": 1, "daily_accounted": 1,
        "daily_remaining": 199, "monthly_accounted": 1, "unit": "credits"}


def test_continuation_freezes_window_and_replays_existing_child_without_provider_configuration(store, fake_newsdata, monkeypatch):
    first = execute(store, key="root", language="en")
    assert first["task"] == TASK and first["language"] == "en"
    store.clock = lambda: NOW + timedelta(hours=4)
    monkeypatch.setenv("NEWSDATA_PAGE_SIZE", "50")
    second = execute(store, parent=first["report_id"], key="page-two", language="en")
    contexts = [item["context"] for item in fake_newsdata["calls"]]
    assert len(contexts) == 2
    assert contexts[1]["window_start"] == contexts[0]["window_start"]
    assert contexts[1]["window_end"] == contexts[0]["window_end"]
    assert contexts[1]["page_size"] == 10 and contexts[1]["page_index"] == 2
    assert contexts[1]["cursor"] == "private-next-cursor-2"
    assert contexts[1]["parent_report_id"] == first["report_id"]
    assert contexts[1]["root_report_id"] == first["report_id"]
    monkeypatch.setattr(service.runtime, "agent_runtime_is_ready", lambda agent_id: False)
    replay = execute(store, parent=first["report_id"], key="new-alias", language="en")
    assert replay["report_id"] == second["report_id"] and replay["idempotency_replayed"]
    assert len(fake_newsdata["calls"]) == 2
    parent = store.get_report(first["report_id"])
    assert parent["language"] == "en"
    assert parent["result"]["pagination"]["next_report_id"] == second["report_id"]
    assert parent["pagination"]["next_report_status"] == "succeeded"
    # The alias is permanently bound to page two; it cannot become page three.
    with pytest.raises(HTTPException) as conflict:
        execute(store, parent=second["report_id"], key="new-alias", language="en")
    assert conflict.value.detail["code"] == "idempotency_conflict"


def test_concurrent_continuations_with_different_keys_issue_one_provider_request(store, fake_newsdata):
    first = execute(store)
    started, release = threading.Event(), threading.Event()

    def hold(context):
        if context["page_index"] == 2:
            started.set()
            assert release.wait(5)

    fake_newsdata["hook"] = hold
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(execute, store, parent=first["report_id"], key="first-child-key")
        assert started.wait(5)
        try:
            with pytest.raises(HTTPException) as pending:
                execute(store, parent=first["report_id"], key="concurrent-alias")
            assert pending.value.detail["code"] == "report_in_progress"
            pending_id = pending.value.detail["report_id"]
            assert store.get_report(first["report_id"])["pagination"]["next_report_id"] == pending_id
        finally:
            release.set()
        second = future.result()
    assert second["report_id"] == pending_id
    assert execute(store, parent=first["report_id"], key="concurrent-alias")["report_id"] == pending_id
    assert len(fake_newsdata["calls"]) == 2
    assert store.usage_summary()["provider_credits"]["newsdata"]["daily_accounted"] == 2


def test_failed_continuation_keeps_credit_and_usd_and_cannot_be_recharged_by_new_key(store, fake_newsdata):
    fake_newsdata.update(reserved_usd=.2, actual_usd=.01)
    first = execute(store)

    def fail(context):
        raise HTTPException(504, detail={"code": "newsdata_timeout", "debug": "private-next-cursor-2"})

    fake_newsdata["hook"] = fail
    with pytest.raises(HTTPException) as failed:
        execute(store, parent=first["report_id"], key="timeout")
    assert failed.value.status_code == 504
    assert "private-next-cursor" not in str(failed.value.detail)
    with pytest.raises(HTTPException) as replay:
        execute(store, parent=first["report_id"], key="different-key")
    assert replay.value.detail["code"] == "report_already_attempted"
    assert replay.value.detail["report_id"] == failed.value.detail["report_id"]
    assert len(fake_newsdata["calls"]) == 2
    record = store.get_report(failed.value.detail["report_id"])
    assert record["estimated_cost_usd"] == .2 and record["accounted_newsdata_credits"] == 1
    assert record["newsdata_credit_basis"] == "reservation_retained"
    summary = store.usage_summary()
    assert summary["daily"]["attempts"] == 2 and summary["daily"]["estimated_cost_usd"] == .21
    assert summary["provider_credits"]["newsdata"]["daily_accounted"] == 2


def test_translation_failure_preserves_next_page_and_only_reconciles_known_credit(store, fake_newsdata):
    fake_newsdata.update(reserved_usd=.2, actual_usd=None, cost_complete=False)
    first = execute(store)
    assert first["accounting"]["estimated_cost_usd"] == .2
    assert first["accounting"]["cost_basis"] == "reservation_retained"
    assert first["accounting"]["newsdata_credit_basis"] == "usage_reported"
    assert first["pagination"]["has_more"]
    assert execute(store, parent=first["report_id"])["pagination"]["page_index"] == 2


@pytest.mark.parametrize("change", ["task", "language", "missing", "legacy", "other_agent"])
def test_invalid_continuations_are_rejected_before_any_new_call(store, fake_newsdata, change):
    first = execute(store)
    parent, task, language, agent_id = first["report_id"], TASK, "ar", "iran-news"
    if change == "task":
        task = "Changed task"
    elif change == "language":
        language = "en"
    elif change == "missing":
        parent = "rpt_missing"
    elif change == "legacy":
        old = store.reserve_run("iran-news", TASK, ".01")
        parent = old.report["report_id"]
        store.finish_run(parent, {"provider": "openai", "response": "Archived native-search report"})
    else:
        agent_id = "geopolitical-morning"
    with pytest.raises(HTTPException) as invalid:
        service.execute_report(agent_id, task, language=language, store=store, continue_from_report_id=parent)
    assert invalid.value.status_code in {409, 422}
    assert len(fake_newsdata["calls"]) == 1


def test_provider_cursor_cycle_stops_pagination_before_another_credit(store):
    first = reserve_page(store).report
    context = store.get_newsdata_context(first["report_id"])
    store.finish_run(first["report_id"], {"newsdata": {"returned_item_count": 1}}, newsdata_context={**context, "next_cursor": "opaque-A"})
    second = reserve_page(store, context=store.continuation_context(first["report_id"], "iran-news", TASK)).report
    context = store.get_newsdata_context(second["report_id"])
    done = store.finish_run(second["report_id"], {"newsdata": {"returned_item_count": 1}}, newsdata_context={**context, "next_cursor": "opaque-A"})
    assert done["pagination"]["has_more"] is False
    assert done["pagination"]["stop_reason"] == "repeated_cursor"
    with pytest.raises(StoreError) as stopped:
        store.continuation_context(second["report_id"], "iran-news", TASK)
    assert stopped.value.code == "newsdata_no_more_pages"
    assert store.usage_summary()["provider_credits"]["newsdata"]["daily_accounted"] == 2


def test_credit_budget_is_atomic_under_concurrency_and_rolls_at_dubai_midnight(store):
    store._limits = QuotaLimits(50, "100", "1000", 3)
    before = datetime(2026, 10, 20, 19, 59, 59, tzinfo=timezone.utc)

    def attempt(index):
        try:
            return reserve_page(store, key=f"credit-race-{index}", now=before).created
        except StoreError as error:
            assert error.code == "newsdata_daily_credit_limit"
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        accepted = list(executor.map(attempt, range(24)))
    assert sum(accepted) == 3
    assert store.usage_summary(now=before)["provider_credits"]["newsdata"]["daily_remaining"] == 0
    after = before + timedelta(seconds=1)
    assert reserve_page(store, now=after).created
    summary = store.usage_summary(now=after)
    assert summary["date"] == "2026-10-21"
    assert summary["provider_credits"]["newsdata"]["daily_accounted"] == 1
    assert summary["provider_credits"]["newsdata"]["monthly_accounted"] == 4


@pytest.mark.parametrize("blocked_by", ["daily", "monthly", "credits"])
def test_failed_atomic_budget_gate_charges_neither_ledger_nor_attempt(store, blocked_by):
    store._limits = QuotaLimits(50, ".1" if blocked_by == "daily" else "100",
                                ".1" if blocked_by == "monthly" else "1000", 0 if blocked_by == "credits" else 200)
    with pytest.raises(StoreError) as blocked:
        reserve_page(store, amount=".2")
    expected = "newsdata_daily_credit_limit" if blocked_by == "credits" else f"{blocked_by}_budget_limit"
    assert blocked.value.code == expected
    summary = store.usage_summary()
    assert summary["daily"]["attempts"] == 0 and summary["daily"]["estimated_cost_usd"] == 0
    assert summary["provider_credits"]["newsdata"]["daily_accounted"] == 0


def test_credit_exhaustion_blocks_provider_before_call_and_keeps_other_agents_independent(store, fake_newsdata):
    store._limits = QuotaLimits(50, "100", "1000", 1)
    first = execute(store)
    with pytest.raises(HTTPException) as blocked:
        execute(store, parent=first["report_id"])
    assert blocked.value.detail["code"] == "newsdata_daily_credit_limit"
    assert len(fake_newsdata["calls"]) == 1
    store._limits = QuotaLimits(50, "100", "1000", 0)
    assert store.reserve_run("geopolitical-morning", "Independent OpenAI report", ".1").created


def test_stale_newsdata_attempt_retains_credit_without_retry(store):
    first = reserve_page(store, amount=".2", key="interrupted")
    assert store.recover_stale_runs(now=NOW + timedelta(minutes=16)) == 1
    record = store.get_report(first.report["report_id"])
    assert record["status"] == "interrupted" and record["estimated_cost_usd"] == .2
    assert record["accounted_newsdata_credits"] == 1 and record["newsdata_credit_basis"] == "reservation_retained"
    assert not reserve_page(store, amount=".2", key="interrupted").created


def test_additive_credit_migration_preserves_legacy_report_and_idempotency(store):
    old = store.reserve_run("geopolitical-morning", TASK, ".1", idempotency_key="legacy-key")
    store.finish_run(old.report["report_id"], {"response": "Legacy report"}, estimated_cost_usd=".01", cost_tracking_complete=True)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE newsdata_pages")
        connection.execute("DROP TABLE report_request_keys")
        for column in ("reserved_newsdata_credits", "accounted_newsdata_credits", "newsdata_credit_basis"):
            connection.execute(f"ALTER TABLE runs DROP COLUMN {column}")
    store.initialize()
    replay = store.lookup_idempotency("legacy-key", "geopolitical-morning", TASK)
    assert replay["report_id"] == old.report["report_id"] and replay["result"]["response"] == "Legacy report"
    assert replay["estimated_cost_usd"] == .01 and replay["accounted_newsdata_credits"] == 0
    assert reserve_page(store).created


def test_real_newsdata_runtime_contract_saves_empty_page_without_openai(store, monkeypatch):
    from app import newsdata
    monkeypatch.setenv("NEWSDATA_API_KEY", "synthetic-key-never-sent")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = []

    def provider_page(context):
        calls.append(dict(context))
        return {"status": "success", "totalResults": 0, "results": [], "nextPage": "private-real-adapter-cursor"}, {}

    monkeypatch.setattr(newsdata, "_fetch_page", provider_page)
    result = execute(store, language="en")
    assert len(calls) == 1 and calls[0]["root_report_id"] == result["report_id"]
    assert result["accounting"]["estimated_cost_usd"] == 0
    assert result["accounting"]["newsdata_credits"] == 1
    assert result["pagination"]["has_more"]
    assert "private-real-adapter-cursor" not in json.dumps(store.get_report(result["report_id"]))


def test_two_default_pages_stop_at_twenty_with_no_third_charge_and_saved_second_replays(store, fake_newsdata, monkeypatch):
    fake_newsdata["returned_count"] = 10
    first = execute(store, key="capped-root")
    second = execute(store, parent=first["report_id"], key="capped-second")
    assert first["pagination"]["results_consumed"] == 10
    assert second["pagination"]["results_before_page"] == 10
    assert second["pagination"]["results_consumed"] == 20 and second["pagination"]["limit_reached"]
    assert second["pagination"]["result_limit"] == 20 and not second["pagination"]["has_more"]
    before = store.usage_summary()
    for key in (None, "new-third-key", "capped-second"):
        with pytest.raises(HTTPException) as capped:
            execute(store, parent=second["report_id"], key=key)
        assert capped.value.detail["code"] == "newsdata_result_limit"
    monkeypatch.setattr(service.runtime, "agent_runtime_is_ready", lambda agent_id: False)
    replay = execute(store, parent=first["report_id"], key="replay-second")
    assert replay["report_id"] == second["report_id"] and replay["idempotency_replayed"]
    assert len(fake_newsdata["calls"]) == 2 and store.usage_summary() == before


@pytest.mark.parametrize("saved_count", [10, None, "invalid"])
def test_legacy_saved_chain_reconstructs_cap_and_transaction_rejects_count_reset(store, saved_count):
    def finish(report, next_cursor):
        metadata = {} if saved_count is None else {"returned_item_count": saved_count}
        store.finish_run(report["report_id"], {"newsdata": metadata},
                         newsdata_context={**store.get_newsdata_context(report["report_id"]), "next_cursor": next_cursor})
    first = reserve_page(store).report
    finish(first, "legacy-next")
    second_context = store.continuation_context(first["report_id"], "iran-news", TASK)
    assert second_context["results_consumed"] == 10
    with pytest.raises(StoreError) as reset:
        reserve_page(store, context={**second_context, "results_consumed": 0})
    assert reset.value.code == "invalid_newsdata_context"
    second = reserve_page(store, context=second_context).report
    finish(second, "legacy-third")
    assert store.get_report(second["report_id"])["pagination"]["limit_reached"]
    # Bypass preflight and call the transactional gate directly with a forged
    # next-page counter: no attempt or credit may be added.
    with pytest.raises(StoreError) as capped:
        reserve_page(store, context={**second_context, "parent_report_id": second["report_id"],
                     "page_index": 3, "cursor": "legacy-third", "results_consumed": 0})
    assert capped.value.code == "newsdata_result_limit"
    assert store.usage_summary()["daily"]["attempts"] == 2
    assert store.usage_summary()["provider_credits"]["newsdata"]["daily_accounted"] == 2


def test_short_provider_pages_use_saved_raw_counts_instead_of_page_index_times_size(store, fake_newsdata):
    fake_newsdata["returned_count"] = 3
    first = execute(store)
    second = execute(store, parent=first["report_id"])
    third = execute(store, parent=second["report_id"])
    assert [item["context"]["results_consumed"] for item in fake_newsdata["calls"]] == [0, 3, 6]
    assert third["pagination"]["results_consumed"] == 9
    assert third["pagination"]["has_more"] and not third["pagination"]["limit_reached"]


def test_legacy_fifty_row_archive_remains_readable_but_cannot_continue(store):
    first = reserve_page(store).report
    report_id = first["report_id"]
    store.finish_run(report_id, {"newsdata": {"returned_item_count": 10}},
                     newsdata_context={**store.get_newsdata_context(report_id), "next_cursor": "legacy-after-fifty"})
    # Reproduce a durable page created before the first-20 rule existed.
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE newsdata_pages SET page_size=50 WHERE report_id=?", (report_id,))
        connection.execute("UPDATE runs SET result_json=? WHERE report_id=?",
                           (json.dumps({"newsdata": {"returned_item_count": 50}, "items": list(range(50))}), report_id))
    archived = store.get_report(report_id)
    assert len(archived["result"]["items"]) == 50
    assert archived["pagination"]["results_consumed"] == 20 and archived["pagination"]["limit_reached"]
    assert not archived["pagination"]["has_more"]
    with pytest.raises(StoreError) as capped:
        store.continuation_context(report_id, "iran-news", TASK)
    assert capped.value.code == "newsdata_result_limit"
    assert store.usage_summary()["daily"]["attempts"] == 1
