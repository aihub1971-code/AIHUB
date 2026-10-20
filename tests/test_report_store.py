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
    assert reports.submit(OWNER, AGENT, "Topic", language="en", idempotency_key="alias")["job"] == first["job"]
    with pytest.raises(StoreError) as owned_limit:
        reports.submit(OWNER, AGENT, "Another topic")
    assert owned_limit.value.code == "report_queue_session_limit"
    reports.submit("public:bob", AGENT, "Third")
    with pytest.raises(StoreError) as total_limit:
        reports.submit("public:charlie", AGENT, "Fourth")
    assert total_limit.value.code == "report_queue_full"


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
