"""Persistent Dubai daily schedules, missed-work policy and safe dispatch."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest

from app.scheduler import Scheduler
from app import service
from app.storage import QuotaLimits, Store, StoreError


NOW = datetime(2026, 10, 19, 3, 1, tzinfo=timezone.utc)  # 07:01 Dubai


@pytest.fixture
def store(tmp_path):
    database = Store(tmp_path / "schedule.sqlite3", QuotaLimits(), clock=lambda: NOW)
    database.initialize()
    return database


def schedule(store, times=None, **kwargs):
    options = {"agent_id": "iran-news", "task": "Today's six news items", "times": times or ["07:00"], "enabled": True, "now": NOW - timedelta(days=1)}
    options.update(kwargs)
    return store.create_schedule(**options)


def test_claim_uses_dubai_time_and_survives_restart(store):
    definition = schedule(store)
    claims = store.claim_due_occurrences(now=NOW)
    assert len(claims) == 1
    assert claims[0]["occurrence_key"] == f"schedule:{definition['schedule_id']}:2026-10-19:07:00"
    assert claims[0]["due_at"].startswith("2026-10-19T03:00:00")
    restarted = Store(store.path, clock=lambda: NOW)
    restarted.initialize()
    assert restarted.claim_due_occurrences(now=NOW) == []


def test_only_one_scheduler_claims_a_due_occurrence(store):
    schedule(store)
    with ThreadPoolExecutor(max_workers=10) as executor:
        claims = list(executor.map(lambda _: store.claim_due_occurrences(now=NOW), range(20)))
    assert sum(len(batch) for batch in claims) == 1


def test_backlog_over_five_minutes_is_skipped_and_not_retried(store):
    definition = schedule(store)
    late = NOW + timedelta(minutes=5)
    assert store.claim_due_occurrences(now=late) == []
    occurrences = store.list_occurrences(definition["schedule_id"])
    current = next(item for item in occurrences if "2026-10-19" in item["occurrence_key"])
    assert current["status"] == "skipped_missed"
    assert current["error_code"] == "missed_grace_window"
    assert store.claim_due_occurrences(now=late + timedelta(seconds=10)) == []


def test_midnight_grace_can_claim_previous_dubai_day(store):
    schedule(store, times=["23:59"])
    midnight = datetime(2026, 10, 19, 20, 1, tzinfo=timezone.utc)
    claim = store.claim_due_occurrences(now=midnight)
    assert len(claim) == 1
    assert ":2026-10-19:23:59" in claim[0]["occurrence_key"]


def test_new_schedule_does_not_retroactively_run_before_creation(store):
    schedule(store, now=NOW)
    assert store.claim_due_occurrences(now=NOW) == []
    assert len(store.claim_due_occurrences(now=NOW + timedelta(days=1))) == 1


def test_reenabling_schedule_does_not_run_a_time_that_passed_while_disabled(store):
    definition = schedule(store, enabled=False)
    store.update_schedule(definition["schedule_id"], enabled=True, now=NOW)
    assert store.claim_due_occurrences(now=NOW) == []
    assert len(store.claim_due_occurrences(now=NOW + timedelta(days=1))) == 1


def test_editing_schedule_applies_only_to_future_occurrences(store):
    definition = schedule(store)
    store.update_schedule(definition["schedule_id"], times=["07:00", "07:02"], task="Updated briefing", now=NOW)
    assert store.claim_due_occurrences(now=NOW) == []
    claim = store.claim_due_occurrences(now=NOW + timedelta(minutes=1))
    assert len(claim) == 1
    assert claim[0]["local_time"] == "07:02"
    assert claim[0]["task"] == "Updated briefing"


def test_global_opt_in_and_per_schedule_enabled_are_required(store, monkeypatch):
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    scheduler = Scheduler(store=store)
    assert scheduler.start() is False
    assert scheduler.is_running is False
    schedule(store, enabled=False)
    assert store.claim_due_occurrences(now=NOW) == []


def test_schedule_slot_cap_is_transactional_and_disabled_definitions_do_not_use_it(store):
    all_times = [f"{index // 60:02}:{index % 60:02}" for index in range(50)]
    enabled = schedule(store, times=all_times)
    disabled = schedule(store, enabled=False)
    with pytest.raises(StoreError) as error:
        store.update_schedule(disabled["schedule_id"], enabled=True)
    assert error.value.code == "schedule_slot_limit"
    assert store.get_schedule(disabled["schedule_id"])["enabled"] is False
    store.update_schedule(enabled["schedule_id"], enabled=False)
    assert store.update_schedule(disabled["schedule_id"], enabled=True)["enabled"] is True
    with pytest.raises(StoreError):
        schedule(store, times=["25:00"])
    with pytest.raises(StoreError):
        schedule(store, times=["07:00", "07:00"])


def test_scheduler_persists_claim_before_execution_and_uses_stable_key(store):
    schedule(store)
    calls = []

    def execute(agent_id, task, **kwargs):
        claimed = next(item for item in store.list_occurrences() if item["status"] == "claimed")
        assert kwargs["idempotency_key"] == claimed["occurrence_key"]
        assert kwargs["trigger"] == "scheduled"
        assert kwargs["store"] is store
        calls.append((agent_id, task))
        return {"report_id": "rpt_example"}

    scheduler = Scheduler(store, execute_fn=execute, clock=lambda: NOW)
    assert scheduler.tick() == 1
    assert scheduler.tick() == 0
    assert len(calls) == 1
    occurrence = next(item for item in store.list_occurrences() if item["report_id"] == "rpt_example")
    assert occurrence["status"] == "succeeded"


def test_failed_tick_does_not_retry_or_prevent_next_scheduled_report(store):
    schedule(store, times=["07:00", "07:02"])
    calls = []

    def execute(*args, **kwargs):
        calls.append(kwargs["idempotency_key"])
        if len(calls) == 1:
            raise HTTPException(429, detail={"code": "daily_budget_limit"})
        return {"report_id": "rpt_second"}

    scheduler = Scheduler(store, execute_fn=execute, clock=lambda: NOW)
    assert scheduler.tick() == 1
    assert scheduler.tick() == 0
    assert scheduler.tick(now=NOW + timedelta(minutes=1)) == 1
    assert len(calls) == 2
    statuses = {item["status"] for item in store.list_occurrences()}
    assert {"blocked", "succeeded"} <= statuses


def test_restart_does_not_requeue_stale_claims(store):
    schedule(store)
    claim = store.claim_due_occurrences(now=NOW)[0]
    later = NOW + timedelta(minutes=16)
    store.recover_stale_runs(now=later)
    record = next(item for item in store.list_occurrences() if item["occurrence_key"] == claim["occurrence_key"])
    assert record["status"] == "interrupted"
    assert store.claim_due_occurrences(now=later) == []


def test_start_is_idempotent_and_stop_joins_worker(store):
    scheduler = Scheduler(store, enabled=True, poll_seconds=.1, clock=lambda: NOW)
    try:
        assert scheduler.start() is True
        original = scheduler._thread
        assert scheduler.start() is True
        assert scheduler._thread is original
        assert scheduler.is_running is True
    finally:
        scheduler.stop()
    assert scheduler.is_running is False


def test_manual_and_scheduled_execution_share_one_quota(store, monkeypatch):
    store._limits = QuotaLimits(1, "10", "100")
    schedule(store, agent_id="geopolitical-morning")
    calls = []
    monkeypatch.setattr(service.runtime, "runtime_is_ready", lambda: True)
    monkeypatch.setattr(service.runtime, "estimate_reservation", lambda *args: {"estimated_cost_usd": .1, "provider": "test", "model": "test"}, raising=False)

    def run(*args):
        calls.append(args)
        return {"response": "Saved", "cost_tracking_complete": False}

    monkeypatch.setattr(service.runtime, "run_agent", run, raising=False)
    service.execute_report("geopolitical-morning", "Manual first", store=store)
    scheduler = Scheduler(store=store, clock=lambda: NOW)
    assert scheduler.tick() == 1
    assert len(calls) == 1
    today = next(item for item in store.list_occurrences() if ":2026-10-19:" in item["occurrence_key"])
    assert today["status"] == "blocked"
    assert today["error_code"] == "daily_report_limit"
    assert store.usage_summary()["daily"]["attempts"] == 1
