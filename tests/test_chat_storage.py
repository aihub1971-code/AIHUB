"""Atomic chat/report budgets, durable message order and additive compatibility."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest

from app.storage import QuotaLimits, Store, StoreError


NOW = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    database = Store(tmp_path / "chat_storage.sqlite3", QuotaLimits(50, "100", "1000"), clock=lambda: NOW)
    database.initialize()
    return database


def conversation(store, owner="owner"):
    return store.create_chat_conversation(owner)["conversation_id"]


def reserve_chat(store, conversation_id, *, key="chat-request", message="Synthetic question", amount=".1",
                 expected=0, owner="owner", language="ar"):
    return store.reserve_chat_turn(conversation_id, owner, message, language, amount,
                                   idempotency_key=key, expected_sequence=expected)


def reserve_report(store, amount=".1", **kwargs):
    return store.reserve_run("iran-news", "Synthetic report task", amount, **kwargs)


def test_fifty_reports_does_not_prevent_chat_or_add_to_report_archive(store):
    for _ in range(50):
        report = reserve_report(store, ".001")
        store.fail_run(report.report["report_id"], "fixture_failure")
    with pytest.raises(StoreError) as limit:
        reserve_report(store)
    assert limit.value.code == "daily_report_limit"
    chat = reserve_chat(store, conversation(store))
    assert chat.created
    store.finish_chat_turn(chat.turn["turn_id"], "Synthetic answer", estimated_cost_usd=".00075", cost_tracking_complete=True)
    summary = store.usage_summary()
    assert summary["daily"]["attempts"] == 50 and summary["daily"]["remaining"] == 0
    assert summary["daily"]["chat_turns"] == 1 and summary["daily"]["chat_succeeded"] == 1
    assert summary["daily"]["report_estimated_cost_usd"] == .05
    assert summary["daily"]["chat_estimated_cost_usd"] == .00075
    assert summary["daily"]["estimated_cost_usd"] == .05075
    assert len(store.list_reports(limit=100)) == 50


@pytest.mark.parametrize("first_kind", ["chat", "report"])
@pytest.mark.parametrize("budget_kind", ["daily", "monthly"])
def test_both_ledgers_share_the_same_budget_gate(store, first_kind, budget_kind):
    store._limits = QuotaLimits(50, ".15" if budget_kind == "daily" else "100",
                                ".15" if budget_kind == "monthly" else "100")
    cid = conversation(store)
    if first_kind == "chat":
        reserve_chat(store, cid)
        blocked = lambda: reserve_report(store)
    else:
        reserve_report(store)
        blocked = lambda: reserve_chat(store, cid)
    with pytest.raises(StoreError) as error:
        blocked()
    assert error.value.code == f"{budget_kind}_budget_limit"
    assert store.usage_summary()["daily"]["estimated_cost_usd"] == .1


def test_mixed_report_chat_concurrency_cannot_overspend_shared_budget(store):
    store._limits = QuotaLimits(50, ".3", "1")
    conversations = [conversation(store) for _ in range(12)]

    def attempt(index):
        try:
            if index % 2:
                return reserve_report(store, ".1", idempotency_key=f"report-{index}").created
            return reserve_chat(store, conversations[index // 2], key=f"chat-{index}").created
        except StoreError as error:
            assert error.code == "daily_budget_limit"
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        accepted = list(executor.map(attempt, range(24)))
    assert sum(accepted) == 3
    summary = store.usage_summary()
    assert summary["daily"]["estimated_cost_usd"] == .3
    assert summary["daily"]["attempts"] + summary["daily"]["chat_turns"] == 3


def test_concurrent_duplicate_chat_reservations_charge_once_and_keep_one_user_message(store):
    cid = conversation(store)
    with ThreadPoolExecutor(max_workers=10) as executor:
        attempts = list(executor.map(lambda _: reserve_chat(store, cid), range(20)))
    assert sum(result.created for result in attempts) == 1
    assert len({result.turn["turn_id"] for result in attempts}) == 1
    assert len(store.get_chat_conversation(cid, "owner")["messages"]) == 1
    assert store.usage_summary()["daily"]["chat_turns"] == 1
    assert store.usage_summary()["daily"]["estimated_cost_usd"] == .1


def test_different_concurrent_chat_sends_keep_single_pending_turn(store):
    cid = conversation(store)

    def attempt(index):
        try:
            return reserve_chat(store, cid, key=f"send-{index}").created
        except StoreError as error:
            assert error.code == "conversation_busy"
            return False

    with ThreadPoolExecutor(max_workers=10) as executor:
        accepted = list(executor.map(attempt, range(20)))
    assert sum(accepted) == 1
    assert store.get_chat_conversation(cid, "owner")["message_count"] == 1


def test_owner_scope_is_enforced_for_reads_replays_and_reservation(store):
    owner_conversation = conversation(store, "alice")
    other_conversation = conversation(store, "bob")
    reserve_chat(store, owner_conversation, owner="alice")
    assert [item["conversation_id"] for item in store.list_chat_conversations("bob")] == [other_conversation]
    operations = [
        lambda: store.get_chat_conversation(owner_conversation, "bob"),
        lambda: store.chat_snapshot(owner_conversation, "bob"),
        lambda: store.lookup_chat_idempotency(owner_conversation, "bob", "chat-request", "Synthetic question", "ar", None, False),
        lambda: reserve_chat(store, owner_conversation, owner="bob"),
    ]
    for operation in operations:
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.code == "chat_conversation_not_found" and error.value.status_code == 404
    assert store.usage_summary()["daily"]["chat_turns"] == 1


def test_stale_turn_keeps_reserve_and_late_completion_cannot_corrupt_newer_history(store):
    cid = conversation(store)
    old = reserve_chat(store, cid)
    store.clock = lambda: NOW + timedelta(minutes=16)
    old_view = store.get_chat_conversation(cid, "owner")
    assert old_view["pending_turn_id"] is None
    assert old_view["messages"][0]["status"] == "interrupted"
    assert old_view["messages"][0]["error_code"] == "execution_interrupted"
    replay = reserve_chat(store, cid)
    assert not replay.created and replay.turn["status"] == "interrupted"
    assert replay.turn["estimated_cost_usd"] == .1 and replay.turn["cost_basis"] == "reservation_retained"
    new = reserve_chat(store, cid, key="new-request", message="New question", expected=1)
    with pytest.raises(StoreError) as late:
        store.finish_chat_turn(old.turn["turn_id"], "Late reply")
    assert late.value.code == "chat_already_attempted"
    store.finish_chat_turn(new.turn["turn_id"], "New answer", estimated_cost_usd=".01", cost_tracking_complete=True)
    snapshot = store.chat_snapshot(cid, "owner")
    assert [item["content"] for item in snapshot["history"]] == ["New question", "New answer"]
    assert snapshot["history_version"] == 3
    assert store.usage_summary()["daily"]["estimated_cost_usd"] == .11


def test_chat_daily_and_monthly_cost_boundaries_use_dubai_midnight(store):
    store._limits = QuotaLimits(50, ".1", ".2")
    before = datetime(2026, 10, 30, 19, 59, 59, tzinfo=timezone.utc)
    store.clock = lambda: before
    first = reserve_chat(store, conversation(store))
    assert first.turn["local_date"] == "2026-10-30"
    store.clock = lambda: before + timedelta(seconds=1)
    second = reserve_chat(store, conversation(store))
    assert second.turn["local_date"] == "2026-10-31"
    summary = store.usage_summary()
    assert summary["daily"]["chat_turns"] == 1 and summary["monthly"]["chat_turns"] == 2
    assert summary["daily"]["estimated_cost_usd"] == .1 and summary["monthly"]["estimated_cost_usd"] == .2
    store.clock = lambda: datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
    third = reserve_chat(store, conversation(store))
    assert third.turn["local_date"] == "2026-11-01" and third.turn["local_month"] == "2026-11"
    assert store.usage_summary()["monthly"]["chat_turns"] == 1


def test_chat_cost_rounding_and_failure_retention_use_integer_microdollars(store):
    first = reserve_chat(store, conversation(store), amount=".0123451")
    assert first.turn["reserved_cost_usd"] == .012346
    finished = store.finish_chat_turn(first.turn["turn_id"], "Answer", estimated_cost_usd=".0000011", cost_tracking_complete=True)
    assert finished["estimated_cost_usd"] == .000002
    second = reserve_chat(store, conversation(store), amount=".1")
    store.fail_chat_turn(second.turn["turn_id"], "unsafe raw provider error with a secret")
    retained = store.lookup_chat_idempotency(second.turn["conversation_id"], "owner", "chat-request", "Synthetic question", "ar", None, False)
    assert retained["error_code"] == "execution_failed"
    assert retained["estimated_cost_usd"] == .1
    assert store.usage_summary()["daily"]["estimated_cost_usd"] == .100002


def test_idempotency_language_extension_preserves_exact_legacy_arabic_hash(store):
    agent, task = "iran-news", "تقرير اختبار"
    legacy_hash = hashlib.sha256(json.dumps([agent, task], ensure_ascii=False, allow_nan=False,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
    assert store._fingerprint(agent, task) == store._fingerprint(agent, task, "ar") == legacy_hash
    assert store._fingerprint(agent, task, "en") != legacy_hash
    existing = store.reserve_run(agent, task, ".1", idempotency_key="legacy-key")
    assert store.lookup_idempotency("legacy-key", agent, task, "ar")["report_id"] == existing.report["report_id"]
    for operation in (
        lambda: store.lookup_idempotency("legacy-key", agent, task, "en"),
        lambda: store.reserve_run(agent, task, ".1", idempotency_key="legacy-key", language="en"),
    ):
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.code == "idempotency_conflict"
    english = store.reserve_run(agent, task, ".1", idempotency_key="english-key", language="en")
    assert store.lookup_idempotency("english-key", agent, task, "en")["report_id"] == english.report["report_id"]


def test_existing_database_migrates_schedules_additively_and_retains_report_rows(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    # A real pre-1.4 schedule schema, with a row that must retain its behavior.
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE schedules (
            schedule_id TEXT PRIMARY KEY,name TEXT NOT NULL,agent_id TEXT NOT NULL,task TEXT NOT NULL,
            times_json TEXT NOT NULL,enabled INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
        connection.execute("INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?)",
                           ("sch_legacy", "Legacy Arabic", "iran-news", "تقرير", '["10:30"]', 1,
                            NOW.isoformat(), NOW.isoformat()))
    database = Store(path, QuotaLimits(50, "100", "1000"), clock=lambda: NOW)
    database.initialize()
    first = reserve_report(database, idempotency_key="persisted-before-reinitialize")
    database.initialize()
    legacy = database.get_schedule("sch_legacy")
    assert legacy["language"] == "ar" and legacy["times"] == ["10:30"] and legacy["enabled"] is True
    assert database.get_report(first.report["report_id"])["task"] == "Synthetic report task"
    conversation_id = conversation(database)
    assert reserve_chat(database, conversation_id).created


def test_schedule_language_create_update_and_claim_persist(store):
    english = store.create_schedule("iran-news", "Synthetic task", ["10:30"], enabled=True, language="en", now=NOW)
    arabic = store.create_schedule("iran-news", "تقرير", ["10:31"], enabled=False, now=NOW)
    assert english["language"] == "en" and arabic["language"] == "ar"
    assert store.update_schedule(arabic["schedule_id"], language="en")["language"] == "en"
    assert store.update_schedule(arabic["schedule_id"], name="New name")["language"] == "en"
    claimed = store.claim_due_occurrences(now=NOW + timedelta(minutes=30))
    assert len(claimed) == 1 and claimed[0]["language"] == "en"
    assert claimed[0]["schedule_id"] == english["schedule_id"]
    with pytest.raises(StoreError) as invalid:
        store.update_schedule(english["schedule_id"], language="fr")
    assert invalid.value.code == "invalid_language"
