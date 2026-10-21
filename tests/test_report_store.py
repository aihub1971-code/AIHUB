"""Durable snapshot ownership, queue admission, ordering and restart boundaries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.report_store import ReportStore, get_report_store
from app.storage import QuotaLimits, Store, StoreError


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
AGENT = "geopolitical-morning"
OWNER = "public:synthetic-alice"


@pytest.fixture
def reports(tmp_path):
    base = Store(tmp_path / "reports.sqlite3", QuotaLimits(50, "5", "100"), clock=lambda: NOW)
    result = ReportStore(base)
    result.initialize()
    return result


def synthetic_result(job, *, topic=None):
    cutoff = job.get("window_end") or job["research_window"]["end"]
    start = job.get("window_start") or job["research_window"]["start"]
    return {"schema_version": 2, "report_kind": "research", "agent_id": job["agent_id"],
            "agent_name": "Synthetic Agent", "topic": topic or job["topic"], "language": job["language"],
            "kind": "briefing", "title": "Synthetic report", "research_window": {"start": start, "end": cutoff},
            "items": [], "sources": [{"id": "s1", "title": "Synthetic source", "publisher": "Fixture Publisher",
                "url": "https://fixture.example/synthetic", "published_at": cutoff, "role": "news",
                "publication_verification": "synthetic fixture", "citation_annotations": [{"type": "url_citation", "url": "https://fixture.example/synthetic"}]}],
            "sections": [{"id": "summary", "title": "Executive summary", "body": "Synthetic finding.", "source_ids": ["s1"]}],
            "citation_mappings": {"s1": {"url": "https://fixture.example/synthetic", "published_at": cutoff}},
            "limitations": ["Offline fixture"], "response": "Synthetic report text.",
            "estimated_cost_usd": .01, "cost_tracking_complete": True, "usage": {"input_tokens": 10, "output_tokens": 5},
            "model": "private-fixture-model", "provider": "openai"}


def reserve_for_job(reports, job):
    base = reports.base_store
    reservation = base.reserve_run(job["agent_id"], job["topic"], ".1", language=job["language"],
                                   idempotency_key=reports.ledger_key(job["job_id"]), provider="openai")
    reports.link_run(job["job_id"], job["lease_token"], reservation.report["report_id"])
    return reservation.report["report_id"]


def save_for_job(reports, job):
    report_id = reserve_for_job(reports, job)
    result = synthetic_result(job)
    reports.base_store.finish_run(report_id, result, estimated_cost_usd=".01", cost_tracking_complete=True)
    return reports.save_report(job["job_id"], job["lease_token"], report_id, result)


def fake_pdf(path, marker="fixture"):
    data = ("%PDF-1.7\n" + marker + "\n%%EOF\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
            "page_count": 1, "mime_type": "application/pdf"}


def test_concurrent_clicks_and_new_keys_join_one_durable_job_and_freeze_twelve_hours(reports):
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda index: reports.submit(OWNER, AGENT, "Synthetic topic", idempotency_key=f"key-{index}"), range(24)))
    assert len({item["job"]["job_id"] for item in results}) == 1
    assert sum(not item["joined_existing"] for item in results) == 1
    job = results[0]["job"]
    assert job["status"] == job["stage"] == "queued"
    window = job["research_window"]
    assert datetime.fromisoformat(window["end"]) == NOW
    assert datetime.fromisoformat(window["end"]) - datetime.fromisoformat(window["start"]) == timedelta(hours=12)
    reopened = get_report_store(reports.base_store)
    assert reopened.submit(OWNER, AGENT, "Synthetic topic", idempotency_key="key-23")["job"]["job_id"] == job["job_id"]
    assert len(reopened.list_jobs(OWNER, active_only=True)) == 1
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 0


def test_same_client_key_is_scoped_by_owner_and_conflicts_for_changed_content(reports):
    alice = reports.submit(OWNER, AGENT, "Same topic", idempotency_key="same-key")
    bob = reports.submit("public:synthetic-bob", AGENT, "Same topic", idempotency_key="same-key")
    assert alice["job"]["job_id"] != bob["job"]["job_id"]
    with pytest.raises(StoreError) as conflict:
        reports.submit(OWNER, AGENT, "Changed topic", idempotency_key="same-key")
    assert conflict.value.code == "idempotency_conflict"
    with pytest.raises(StoreError) as invisible:
        reports.get_job("public:synthetic-bob", alice["job"]["job_id"])
    assert invisible.value.status_code == 404


def test_queue_limits_allow_reconnect_but_bound_distinct_topics_languages_and_sessions(reports):
    reports.max_active_per_owner, reports.max_active_global = 2, 3
    first = reports.submit(OWNER, AGENT, "Topic", language="en")
    assert not reports.submit(OWNER, AGENT, "Topic", language="ar")["joined_existing"]
    assert reports.submit(OWNER, AGENT, "Topic", language="en", idempotency_key="alias")["job"]["job_id"] == first["job"]["job_id"]
    with pytest.raises(StoreError) as owned_limit:
        reports.submit(OWNER, AGENT, "Another topic")
    assert owned_limit.value.code == "report_queue_session_limit"
    reports.submit("public:bob", AGENT, "Third")
    with pytest.raises(StoreError) as total_limit:
        reports.submit("public:charlie", AGENT, "Fourth")
    assert total_limit.value.code == "report_queue_full"


def test_default_queue_admits_ten_per_visitor_and_fifty_globally(reports, monkeypatch):
    monkeypatch.delenv("REPORT_MAX_ACTIVE_PER_OWNER", raising=False)
    monkeypatch.delenv("REPORT_MAX_ACTIVE_GLOBAL", raising=False)
    reports = ReportStore(reports.base_store)
    assert (reports.max_active_per_owner, reports.max_active_global) == (10, 50)
    for visitor in range(5):
        owner = f"public:visitor-{visitor}"
        for index in range(10):
            reports.submit(owner, AGENT, f"Independent question {index}", idempotency_key=f"click-{index}")
        with pytest.raises(StoreError) as owned:
            reports.submit(owner, AGENT, "Eleventh question")
        assert owned.value.code == "report_queue_session_limit"
        assert "10" in owned.value.message
    with pytest.raises(StoreError) as global_limit:
        reports.submit("public:visitor-6", AGENT, "Another question")
    assert global_limit.value.code == "report_queue_full"
    assert "50" in global_limit.value.message
    # Reconnection and duplicate active work still join when every slot is full.
    joined = reports.submit("public:visitor-0", AGENT, "Independent question 0", idempotency_key="reconnect")
    assert joined["joined_existing"]
    assert len(reports.list_jobs("public:visitor-0", active_only=True)) == 10
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 0


def test_queue_configuration_is_environment_backed_with_explicit_override(reports, monkeypatch):
    monkeypatch.setenv("REPORT_MAX_ACTIVE_PER_OWNER", "8")
    monkeypatch.setenv("REPORT_MAX_ACTIVE_GLOBAL", "40")
    configured = get_report_store(reports.base_store)
    assert (configured.max_active_per_owner, configured.max_active_global) == (8, 40)
    overridden = ReportStore(reports.base_store, max_active_per_owner=2, max_active_global=3)
    assert (overridden.max_active_per_owner, overridden.max_active_global) == (2, 3)


@pytest.mark.parametrize("name,value", [
    ("REPORT_MAX_ACTIVE_PER_OWNER", "zero"), ("REPORT_MAX_ACTIVE_PER_OWNER", "0"),
    ("REPORT_MAX_ACTIVE_PER_OWNER", "21"), ("REPORT_MAX_ACTIVE_GLOBAL", "0"),
    ("REPORT_MAX_ACTIVE_GLOBAL", "101"), ("REPORT_MAX_ACTIVE_GLOBAL", "1.5"),
])
def test_invalid_queue_environment_fails_with_specific_configuration_name(reports, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(StoreError) as failure:
        ReportStore(reports.base_store)
    assert failure.value.code == "invalid_job_configuration"
    assert failure.value.status_code == 503 and name in failure.value.message


def test_queue_position_tracks_fifo_without_revealing_other_visitor_jobs(reports):
    jobs = [reports.submit(owner, AGENT, topic, now=NOW + timedelta(seconds=index))["job"]
            for index, (owner, topic) in enumerate(((OWNER, "Alice first"),
                ("public:bob", "Private Bob topic"), (OWNER, "Alice second")))]
    assert [job["queue_position"] for job in jobs] == [1, 2, 3]
    running = reports.claim("worker", concurrency=2)
    assert running["job_id"] == jobs[0]["job_id"]
    assert reports.get_job(OWNER, jobs[0]["job_id"])["queue_position"] is None
    assert reports.get_job("public:bob", jobs[1]["job_id"])["queue_position"] == 1
    last = reports.get_job(OWNER, jobs[2]["job_id"])
    assert last["queue_position"] == 2
    assert "public:bob" not in json.dumps(last) and "Private Bob topic" not in json.dumps(last)
    assert jobs[1]["job_id"] not in json.dumps(reports.list_jobs(OWNER))
    with pytest.raises(StoreError) as denied:
        reports.get_job(OWNER, jobs[1]["job_id"])
    assert denied.value.status_code == 404


def test_new_manual_key_after_completion_creates_fresh_generation_but_same_key_replays(reports):
    first = reports.submit(OWNER, AGENT, "Same intentional topic", idempotency_key="first-click")["job"]
    claim = reports.claim("worker")
    saved = save_for_job(reports, claim)
    reports.finish_pdf(claim["job_id"], claim["lease_token"],
        fake_pdf(reports.pdf_attempt_path(saved["report_id"], claim["lease_token"])))
    reports.base_store.clock = lambda: NOW + timedelta(minutes=10)
    replay = reports.submit(OWNER, AGENT, "Same intentional topic", idempotency_key="first-click")
    assert replay["joined_existing"] and replay["job"]["job_id"] == first["job_id"]
    assert replay["job"]["status"] == "ready" and replay["job"]["queue_position"] is None
    fresh = reports.submit(OWNER, AGENT, "Same intentional topic", idempotency_key="second-click")
    assert not fresh["joined_existing"]
    assert fresh["job"]["job_id"] != first["job_id"]
    assert fresh["job"]["generation_seq"] == first["generation_seq"] + 1
    assert datetime.fromisoformat(fresh["job"]["research_cutoff"]) == NOW + timedelta(minutes=10)
    assert fresh["job"]["previous_report_id"] == saved["report_id"]
    assert reports.submit(OWNER, AGENT, "Same intentional topic", idempotency_key="second-click-reconnect")["job"]["job_id"] == fresh["job"]["job_id"]
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 1


def test_expired_reserved_job_is_recovered_before_new_queue_admission(reports):
    reports.max_active_per_owner = 1
    reports.submit(OWNER, AGENT, "Interrupted attempt")
    expired = reports.claim("dead-worker", lease_seconds=30)
    reserved = reserve_for_job(reports, expired)
    reports.base_store.clock = lambda: NOW + timedelta(seconds=31)
    next_job = reports.submit(OWNER, AGENT, "New manual attempt", idempotency_key="new-click")["job"]
    assert next_job["status"] == "queued" and next_job["queue_position"] == 1
    assert reports.get_job(OWNER, expired["job_id"])["status"] == "interrupted"
    assert reports.base_store.get_report(reserved)["status"] == "interrupted"
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 1
    assert reports.claim("new-worker")["job_id"] == next_job["job_id"]


def test_expired_unspent_work_retains_its_queue_slot_and_original_cutoff(reports):
    reports.max_active_per_owner = 1
    first = reports.submit(OWNER, AGENT, "Still authorized work")["job"]
    reports.claim("dead-worker", lease_seconds=30)
    reports.base_store.clock = lambda: NOW + timedelta(seconds=31)
    with pytest.raises(StoreError) as full:
        reports.submit(OWNER, AGENT, "Additional work")
    assert full.value.code == "report_queue_session_limit"
    resumed = reports.get_job(OWNER, first["job_id"])
    assert resumed["status"] == "queued" and resumed["research_cutoff"] == first["research_cutoff"]
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 0


def test_existing_database_adds_private_local_recovery_counter_without_losing_jobs(reports):
    original = reports.submit(OWNER, AGENT, "Saved queue entry")["job"]
    with reports.base_store._connect() as connection:
        connection.execute("ALTER TABLE research_jobs DROP COLUMN local_recovery_attempts")
    reopened = get_report_store(reports.base_store)
    with reports.base_store._connect() as connection:
        row = connection.execute("SELECT local_recovery_attempts FROM research_jobs WHERE job_id=?",
                                 (original["job_id"],)).fetchone()
    assert row["local_recovery_attempts"] == 0
    assert reopened.get_job(OWNER, original["job_id"])["status"] == "queued"
    assert "local_recovery_attempts" not in reopened.get_job(OWNER, original["job_id"])


def test_newer_generation_remains_current_when_older_job_finishes_later(reports):
    first = reports.submit(OWNER, AGENT, "Old scope")["job"]
    second = reports.submit(OWNER, AGENT, "New scope")["job"]
    claims = [reports.claim("worker-a", concurrency=2), reports.claim("worker-b", concurrency=2)]
    by_id = {job["job_id"]: job for job in claims}
    newer = save_for_job(reports, by_id[second["job_id"]])
    older = save_for_job(reports, by_id[first["job_id"]])
    assert reports.latest_report(OWNER, AGENT)["report_id"] == newer["report_id"]
    assert older["generation_seq"] < newer["generation_seq"]
    assert len(reports.list_reports(OWNER, AGENT)) == 2
    assert reports.latest_report("public:bob", AGENT) is None
    for operation in (lambda: reports.get_report("public:bob", newer["report_id"]),
                      lambda: reports.get_evidence("public:bob", newer["report_id"]),
                      lambda: reports.get_pdf_path("public:bob", newer["report_id"]),
                      lambda: reports.retry_pdf("public:bob", newer["report_id"])):
        with pytest.raises(StoreError) as denied:
            operation()
        assert denied.value.status_code == 404


def test_persisted_snapshot_and_citations_hide_private_accounting(reports):
    reports.submit(OWNER, AGENT, "Synthetic topic")
    job = reports.claim("worker")
    saved = save_for_job(reports, job)
    payload = saved["result"]
    assert payload["report_id"] == saved["report_id"] and payload["schema_version"] == 2
    assert payload["citation_mappings"]["s1"]["url"] == payload["sources"][0]["url"]
    assert reports.get_evidence(OWNER, saved["report_id"], ["s1"])[0]["citation_annotations"]
    for private in ("estimated_cost_usd", "usage", "private-fixture-model", "lease_token", "owner_id"):
        assert private not in json.dumps(saved)
        assert private not in json.dumps(reports.get_job(OWNER, job["job_id"]))
    assert reports.base_store.get_report(saved["report_id"])["estimated_cost_usd"] == .01


def test_expired_unreserved_job_can_resume_but_reserved_ambiguity_never_reexecutes(reports):
    reports.submit(OWNER, AGENT, "Unspent")
    unspent = reports.claim("old-worker", lease_seconds=30)
    reports.base_store.clock = lambda: NOW + timedelta(seconds=31)
    assert reports.recover_expired() == 1
    again = reports.claim("new-worker", lease_seconds=30)
    assert again["job_id"] == unspent["job_id"] and again["lease_token"] != unspent["lease_token"]
    assert again["window_end"] == unspent["window_end"]
    reserved = reserve_for_job(reports, again)
    reports.set_stage(again["job_id"], again["lease_token"], "searching")
    reports.base_store.clock = lambda: NOW + timedelta(seconds=62)
    assert reports.recover_expired() == 1
    assert reports.get_job(OWNER, again["job_id"])["status"] == "interrupted"
    assert reports.claim("third-worker") is None
    ledger = reports.base_store.get_report(reserved)
    assert ledger["status"] == "interrupted" and ledger["estimated_cost_usd"] == .1
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 1


def test_restart_recovers_paid_result_before_snapshot_commit_without_relabeling_cutoff(reports):
    reports.submit(OWNER, AGENT, "Recover saved paid result")
    job = reports.claim("old-worker", lease_seconds=30)
    report_id = reserve_for_job(reports, job)
    reports.base_store.finish_run(report_id, synthetic_result(job), estimated_cost_usd=".01", cost_tracking_complete=True)
    finished_at = reports.base_store.get_report(report_id)["finished_at"]
    reports.base_store.clock = lambda: NOW + timedelta(minutes=5)
    restarted = get_report_store(reports.base_store)
    assert restarted.recover_expired() == 1
    assert restarted.get_job(OWNER, job["job_id"])["stage"] == "preparing_pdf"
    saved = restarted.get_report(OWNER, report_id)
    assert saved["created_at"] == finished_at
    assert datetime.fromisoformat(saved["research_cutoff"]) == NOW
    assert restarted.get_evidence(OWNER, report_id)[0]["id"] == "s1"


def test_restart_finds_unlinked_reservation_and_never_requeues_ambiguous_paid_work(reports):
    reports.submit(OWNER, AGENT, "Crash between reservation and ownership link")
    job = reports.claim("old-worker", lease_seconds=30)
    reservation = reports.base_store.reserve_run(AGENT, job["topic"], ".1", language=job["language"],
                                                 idempotency_key=reports.ledger_key(job["job_id"]))
    assert reports.get_job(OWNER, job["job_id"])["report_id"] is None
    reports.base_store.clock = lambda: NOW + timedelta(seconds=31)
    assert reports.recover_expired() == 1
    recovered = reports.get_job(OWNER, job["job_id"])
    assert recovered["status"] == "interrupted"
    assert recovered["report_id"] == reservation.report["report_id"]
    assert reports.owns(OWNER, recovered["report_id"])
    assert reports.claim("new-worker") is None
    assert reports.base_store.usage_summary()["daily"]["estimated_cost_usd"] == .1


def test_pdf_retry_is_atomic_does_not_create_generation_or_overwrite_newer_head(reports):
    reports.submit(OWNER, AGENT, "First")
    old = reports.claim("worker-a")
    first = save_for_job(reports, old)
    reports.fail(old["job_id"], old["lease_token"], "fixture_pdf_failure", pdf=True)
    reports.submit(OWNER, AGENT, "Newer")
    newer = reports.claim("worker-b")
    second = save_for_job(reports, newer)
    reports.fail(newer["job_id"], newer["lease_token"], "fixture_pdf_failure", pdf=True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        retries = list(executor.map(lambda _: reports.retry_pdf(OWNER, first["report_id"]), range(16)))
    assert sum(not result["joined_existing"] for result in retries) == 1
    assert {result["job"]["job_id"] for result in retries} == {old["job_id"]}
    assert reports.latest_report(OWNER, AGENT)["report_id"] == second["report_id"]
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 2


def test_stale_pdf_worker_cannot_replace_current_published_file(reports):
    reports.submit(OWNER, AGENT, "PDF lease race")
    old = reports.claim("worker-a", lease_seconds=30)
    saved = save_for_job(reports, old)
    stale_path = reports.pdf_attempt_path(saved["report_id"], old["lease_token"])
    old_metadata = fake_pdf(stale_path, "old-worker")
    reports.base_store.clock = lambda: NOW + timedelta(seconds=31)
    reports.recover_expired()
    newer = reports.claim("worker-b", lease_seconds=30)
    new_path = reports.pdf_attempt_path(saved["report_id"], newer["lease_token"])
    new_metadata = fake_pdf(new_path, "new-worker")
    reports.finish_pdf(newer["job_id"], newer["lease_token"], new_metadata)
    with pytest.raises(StoreError) as stale:
        reports.finish_pdf(old["job_id"], old["lease_token"], old_metadata)
    assert stale.value.code == "report_job_lease_lost"
    assert b"new-worker" in reports.get_pdf_path(OWNER, saved["report_id"]).read_bytes()
