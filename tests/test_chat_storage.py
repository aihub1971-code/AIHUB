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
                 expected=0, owner="owner", language="ar", research=False, context=None):
    return store.reserve_chat_turn(conversation_id, owner, message, language, amount,
                                   idempotency_key=key, expected_sequence=expected,
                                   research_attempt=research, context=context)


def reserve_report(store, amount=".1", **kwargs):
    return store.reserve_run("iran-news", "Synthetic report task", amount, **kwargs)


def test_internal_evidence_notice_is_zero_cost_without_relaxing_paid_reservations(store):
    cid = conversation(store)
    with pytest.raises(StoreError, match="positive upfront"):
        reserve_chat(store, cid, amount="0")
    for provider, model, amount in [("openai", "evidence_notice", "0"), ("local", "paid_model", "0"),
                                    ("local", "evidence_notice", ".1")]:
        with pytest.raises(StoreError) as error:
            store.reserve_chat_turn(cid, "owner", "Synthetic question", "en", amount,
                idempotency_key="local-notice", expected_sequence=0,
                provider=provider, model=model, local_response=True)
        assert error.value.code == "invalid_cost_configuration"
    with pytest.raises(StoreError) as error:
        store.reserve_chat_turn(cid, "owner", "Synthetic question", "en", "0",
            idempotency_key="local-notice", expected_sequence=0,
            provider="local", model="evidence_notice", local_response=True, research_attempt=True)
    assert error.value.code == "invalid_cost_configuration"
    reservation = store.reserve_chat_turn(cid, "owner", "Synthetic question", "en", "0",
        idempotency_key="local-notice", expected_sequence=0,
        provider="local", model="evidence_notice", local_response=True)
    record = store.finish_chat_turn(reservation.turn["turn_id"], "No supported answer was retrieved.",
        estimated_cost_usd="0", cost_tracking_complete=True,
        usage={"input_tokens": 0, "output_tokens": 0, "usage_reported": True})
    assert record["status"] == "succeeded" and record["estimated_cost_usd"] == 0
    summary = store.usage_summary()["daily"]
    assert summary["chat_estimated_cost_usd"] == 0 and summary["chat_research_attempts"] == 0
    assert summary["attempts"] == 0 and summary["chat_turns"] == 1
    replay = store.reserve_chat_turn(cid, "owner", "Synthetic question", "en", "0",
        idempotency_key="local-notice", expected_sequence=0,
        provider="local", model="evidence_notice", local_response=True)
    assert not replay.created and replay.turn["turn_id"] == record["turn_id"]
    assert len(store.get_chat_conversation(cid, "owner")["messages"]) == 2


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


def test_existing_unbound_chat_schema_is_migrated_without_losing_history(store):
    cid = conversation(store)
    turn = reserve_chat(store, cid)
    store.finish_chat_turn(turn.turn["turn_id"], "An existing saved answer.", estimated_cost_usd=".001", cost_tracking_complete=True)
    with store._connect() as connection:
        connection.execute("DROP INDEX chat_owner_agent_idx")
        connection.execute("ALTER TABLE chat_conversations DROP COLUMN agent_id")
    store.initialize()
    saved = store.get_chat_conversation(cid, "owner")
    assert saved["agent_id"] is None
    assert [message["role"] for message in saved["messages"]] == ["user", "assistant"]
    agent = store.agent_conversation("owner", "ubi-research")
    assert agent["conversation_id"] != cid and agent["messages"] == []


def test_concurrent_agent_bootstrap_has_one_thread_per_owner_and_agent(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: store.agent_conversation("owner", "ubi-research")["conversation_id"], range(16)))
    assert len(set(ids)) == 1
    assert store.agent_conversation("visitor", "ubi-research")["conversation_id"] != ids[0]
    assert store.agent_conversation("owner", "education-observatory")["conversation_id"] != ids[0]


def test_memory_proposals_require_owner_review_and_keep_other_visitors_isolated(store):
    cid = store.agent_conversation("alice", "ubi-research")["conversation_id"]
    note = store.propose_workspace_memory("alice", kind="correction", content="Our institution is in Abu Dhabi, not Dubai.")
    assert store.shared_workspace_context("alice", cid, "institution")["notes"] == []
    with pytest.raises(StoreError) as foreign:
        store.review_workspace_memory("bob", note["memory_id"], "approve")
    assert foreign.value.code == "memory_not_found"
    store.review_workspace_memory("alice", note["memory_id"], "approve")
    shared = store.shared_workspace_context("alice", cid, "institution")
    assert shared["notes"][0]["content"] == note["content"]
    assert shared["notes"][0]["evidence_status"] == "visitor_approved"
    assert store.list_workspace_memory("bob") == []
    store.review_workspace_memory("alice", note["memory_id"], "reject")
    assert store.shared_workspace_context("alice", cid, "institution")["notes"] == []


def checked_source(name, **changes):
    return {"id": "src_" + name, "url": f"https://publisher.example/{name}",
        "title": "Synthetic original expedition study " + name, "publisher": "Fixture Publisher",
        "published_at": "2010-03-04", "publication_precision": "day", "retrieved_at": NOW.isoformat(),
        "role": "reference", "evidence_level": "publisher_page_verified", "publisher_text_verified": True,
        "excerpt": "This synthetic checked source describes the original expedition's methods.", **changes}


def checked_context(name):
    return {"sources": [checked_source(name)], "web_research": {"status": "complete",
        "searched_at": NOW.isoformat(), "source_count": 1, "limitations": []}}


def test_report_feed_discovery_and_web_chat_share_attempts_but_local_chat_does_not(store):
    store._limits = QuotaLimits(3, "100", "1000")
    report = reserve_report(store, idempotency_key="explicit-report")
    feed = store.reserve_run("iran-news", "Shared public news feed discovery: synthetic fixture", ".1",
        idempotency_key="feed-web:synthetic-fixture", trigger="manual", provider="openai")
    researched = reserve_chat(store, conversation(store), research=True)
    store.fail_run(report.report["report_id"], "fixture_failure")
    store.fail_run(feed.report["report_id"], "fixture_failure")
    store.fail_chat_turn(researched.turn["turn_id"], "openai_timeout")
    for operation in (
        lambda: reserve_report(store),
        lambda: store.reserve_run("iran-news", "Another feed discovery", ".1", idempotency_key="feed-web:another"),
        lambda: reserve_chat(store, conversation(store), research=True),
    ):
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.code == "daily_report_limit"
    local = reserve_chat(store, conversation(store))
    assert local.created and local.turn["research_attempt"] == 0
    summary = store.usage_summary()
    assert summary["daily"]["attempts"] == 3 and summary["daily"]["remaining"] == 0
    assert summary["daily"]["failed"] == 3 and summary["daily"]["pending"] == 0
    assert summary["daily"]["chat_research_attempts"] == 1 and summary["daily"]["chat_turns"] == 2
    assert summary["daily"]["estimated_cost_usd"] == .4
    assert len(store.list_reports(limit=100)) == 2


@pytest.mark.parametrize("status", ["pending", "succeeded", "failed"])
def test_researched_chat_replay_does_not_recount_attempts_or_costs_at_the_limit(store, status):
    store._limits = QuotaLimits(1, "100", "1000")
    cid = conversation(store)
    original = reserve_chat(store, cid, research=True)
    if status == "succeeded":
        store.finish_chat_turn(original.turn["turn_id"], "A sourced answer.",
            estimated_cost_usd=".03", cost_tracking_complete=True)
    elif status == "failed":
        store.fail_chat_turn(original.turn["turn_id"], "openai_timeout")
    before = store.usage_summary()
    replay = reserve_chat(store, cid, research=True)
    assert not replay.created and replay.turn["turn_id"] == original.turn["turn_id"]
    assert replay.turn["status"] == status and store.usage_summary() == before
    assert before["daily"]["attempts"] == before["daily"]["chat_research_attempts"] == 1
    assert before["daily"]["estimated_cost_usd"] == (.03 if status == "succeeded" else .1)
    assert len(store.get_chat_conversation(cid, "owner")["messages"]) == (2 if status == "succeeded" else 1)


def test_concurrent_report_and_web_chat_reservations_cannot_overadmit_shared_attempts(store):
    store._limits = QuotaLimits(3, "100", "1000")
    conversations = [conversation(store) for _ in range(8)]
    def attempt(index):
        try:
            if index % 2:
                return reserve_report(store, idempotency_key=f"bounded-report-{index}").created
            return reserve_chat(store, conversations[index // 2], key=f"bounded-web-{index}", research=True).created
        except StoreError as error:
            assert error.code == "daily_report_limit"
            return False
    with ThreadPoolExecutor(max_workers=8) as executor:
        accepted = list(executor.map(attempt, range(16)))
    assert sum(accepted) == 3
    summary = store.usage_summary()
    assert summary["daily"]["attempts"] == summary["daily"]["pending"] == 3
    assert summary["daily"]["estimated_cost_usd"] == .3
    assert len(store.list_reports(limit=100)) + summary["daily"]["chat_research_attempts"] == 3


@pytest.mark.parametrize("budget", ["daily", "monthly"])
@pytest.mark.parametrize("first", ["report", "researched_chat"])
def test_researched_chat_reserves_the_same_usd_budget_before_other_paid_work(store, budget, first):
    store._limits = QuotaLimits(50, ".15" if budget == "daily" else "100",
        ".15" if budget == "monthly" else "1000")
    if first == "report":
        reserve_report(store)
        blocked = lambda: reserve_chat(store, conversation(store), research=True)
    else:
        reserve_chat(store, conversation(store), research=True)
        blocked = lambda: reserve_report(store)
    with pytest.raises(StoreError) as error:
        blocked()
    assert error.value.code == budget + "_budget_limit"
    summary = store.usage_summary()
    assert summary["daily"]["attempts"] == 1 and summary["daily"]["estimated_cost_usd"] == .1


def test_shared_research_attempts_reset_at_dubai_midnight_without_erasing_monthly_history(store):
    store._limits = QuotaLimits(1, "100", "1000")
    before = datetime(2026, 10, 20, 19, 59, 59, tzinfo=timezone.utc)
    store.clock = lambda: before
    researched = reserve_chat(store, conversation(store), research=True)
    store.fail_chat_turn(researched.turn["turn_id"], "openai_timeout")
    with pytest.raises(StoreError) as error:
        reserve_report(store)
    assert error.value.code == "daily_report_limit"
    store.clock = lambda: before + timedelta(seconds=1)
    report = reserve_report(store)
    assert report.created
    summary = store.usage_summary()
    assert summary["date"] == "2026-10-21"
    assert summary["daily"]["attempts"] == 1 and summary["daily"]["chat_research_attempts"] == 0
    assert summary["monthly"]["attempts"] == 2 and summary["monthly"]["chat_research_attempts"] == 1
    assert summary["monthly"]["estimated_cost_usd"] == .2
    assert store.lookup_chat_idempotency(researched.turn["conversation_id"], "owner", "chat-request",
        "Synthetic question", "ar", None, False)["status"] == "failed"


def test_research_attempt_column_migrates_additively_with_saved_evidence_and_costs_intact(store):
    cid = conversation(store)
    old = reserve_chat(store, cid, context=checked_context("existing"))
    store.finish_chat_turn(old.turn["turn_id"], "Existing saved answer.",
        estimated_cost_usd=".0123", cost_tracking_complete=True)
    before = store.get_chat_conversation(cid, "owner")
    with store._connect() as connection:
        connection.execute("ALTER TABLE chat_turns DROP COLUMN research_attempt")
    store.initialize()
    store.initialize()
    after = store.get_chat_conversation(cid, "owner")
    assert after == before
    migrated = store.lookup_chat_idempotency(cid, "owner", "chat-request", "Synthetic question", "ar", None, False)
    assert migrated["research_attempt"] == 0 and migrated["estimated_cost_usd"] == .0123
    assert store.usage_summary()["daily"]["attempts"] == 0
    fresh = reserve_chat(store, cid, key="new-web", expected=2, research=True)
    assert fresh.created and fresh.turn["research_attempt"] == 1
    assert store.usage_summary()["daily"]["attempts"] == 1
    assert store.usage_summary()["daily"]["estimated_cost_usd"] == .1123


def test_checked_evidence_updates_require_the_owner_and_a_pending_turn_and_restore_per_answer(store):
    cid = conversation(store, "alice")
    first = reserve_chat(store, cid, owner="alice", research=True)
    turn_id = first.turn["turn_id"]
    first_context, second_context = checked_context("first"), checked_context("second")
    with pytest.raises(StoreError) as foreign:
        store.update_chat_context(turn_id, "bob", first_context)
    assert foreign.value.code == "chat_turn_not_found" and foreign.value.status_code == 404
    with pytest.raises(StoreError) as oversized:
        store.update_chat_context(turn_id, "alice", {"excerpt": "ع" * 40001})
    assert oversized.value.code == "chat_context_too_large"
    assert store.lookup_chat_idempotency(cid, "alice", "chat-request", "Synthetic question", "ar", None, False)["context"] == {}
    store.update_chat_context(turn_id, "alice", first_context)
    store.finish_chat_turn(turn_id, "First checked answer.")
    with pytest.raises(StoreError) as finished:
        store.update_chat_context(turn_id, "alice", second_context)
    assert finished.value.code == "chat_already_attempted"
    second = reserve_chat(store, cid, owner="alice", expected=2, key="second", research=True)
    store.update_chat_context(second.turn["turn_id"], "alice", second_context)
    store.finish_chat_turn(second.turn["turn_id"], "Second checked answer.")
    saved = store.get_chat_conversation(cid, "alice")
    answers = [message for message in saved["messages"] if message["role"] == "assistant"]
    assert [message["context"] for message in answers] == [first_context, second_context]
    assert saved["context"] == second_context
    assert all("context" not in message for message in saved["messages"] if message["role"] == "user")
    with pytest.raises(StoreError) as foreign_read:
        store.get_chat_conversation(cid, "bob")
    assert foreign_read.value.code == "chat_conversation_not_found"


def test_foreign_pending_failed_and_unchecked_sources_never_enter_owned_shared_evidence(store):
    target = store.agent_conversation("alice", "education-observatory")["conversation_id"]
    original = store.agent_conversation("alice", "ubi-research")["conversation_id"]
    failed = store.agent_conversation("alice", "iran-news")["conversation_id"]
    pending = store.agent_conversation("alice", "geopolitical-morning")["conversation_id"]
    foreign = store.agent_conversation("bob", "ubi-research")["conversation_id"]
    contexts = {
        "kept": {"sources": [checked_source("kept"), checked_source("metadata", publisher_text_verified=False),
            checked_source("untrusted", evidence_level="model_notes"), checked_source("private", url="https://127.0.0.1/private")]},
        "failed": checked_context("failed"), "pending": checked_context("pending"), "foreign": checked_context("foreign")}
    for cid, label, owner in ((original, "kept", "alice"), (failed, "failed", "alice"),
                              (pending, "pending", "alice"), (foreign, "foreign", "bob")):
        turn = reserve_chat(store, cid, owner=owner, message="Original expedition evidence", research=True)
        store.update_chat_context(turn.turn["turn_id"], owner, contexts[label])
        if label == "failed":
            store.fail_chat_turn(turn.turn["turn_id"], "openai_timeout")
            with pytest.raises(StoreError) as ended:
                store.update_chat_context(turn.turn["turn_id"], owner, checked_context("late"))
            assert ended.value.code == "chat_already_attempted"
        elif label != "pending":
            store.finish_chat_turn(turn.turn["turn_id"],
                "The expedition context is saved. An unattested prose URL is https://prose.example/not-evidence")
    assert store.owned_chat_source_urls("alice") == {checked_source("kept")["url"]}
    assert store.owned_chat_source_urls("bob") == {checked_source("foreign")["url"]}
    shared = store.shared_workspace_context("alice", target, "original expedition")
    assert len(shared["conversations"]) == 1
    evidence = shared["conversations"][0]["sources"]
    assert [source["url"] for source in evidence] == [checked_source("kept")["url"]]
    assert evidence[0]["published_at"] == "2010-03-04" and evidence[0]["publication_precision"] == "day"
    assert evidence[0]["retrieved_at"] == NOW.isoformat() and evidence[0]["publisher_text_verified"] is True
    with pytest.raises(StoreError) as foreign_context:
        store.shared_workspace_context("bob", target, "original expedition")
    assert foreign_context.value.code == "chat_conversation_not_found"


@pytest.mark.parametrize("research_flag", [1, 0, "true", None])
def test_research_attempt_flag_cannot_be_truthy_coerced_into_a_different_quota_path(store, research_flag):
    cid = conversation(store)
    with pytest.raises(StoreError) as error:
        reserve_chat(store, cid, research=research_flag)
    assert error.value.code == "invalid_chat_research_mode"
    assert store.usage_summary()["daily"]["attempts"] == store.usage_summary()["daily"]["chat_turns"] == 0
