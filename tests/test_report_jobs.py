"""Offline end-to-end worker execution, quota gates and local PDF recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import sqlite3
import threading

from fastapi import HTTPException
import pytest

from app.report_jobs import ReportWorker
from app.report_store import ReportStore
from app.storage import QuotaLimits, Store, StoreError
from tests.test_report_store import AGENT, NOW, OWNER, fake_pdf, reserve_for_job, synthetic_result


@pytest.fixture
def reports(tmp_path):
    base = Store(tmp_path / "worker.sqlite3", QuotaLimits(50, "5", "100"), clock=lambda: NOW)
    result = ReportStore(base)
    result.initialize()
    return result


@pytest.fixture
def engine(reports):
    calls = {"estimate": [], "generate": [], "pdf": [], "pdf_failure": False, "generation_failure": False,
             "hook": None, "invalid_result": False}
    def estimate(agent_id, topic, **options):
        calls["estimate"].append((agent_id, topic, options))
        return {"provider": "openai", "model": "synthetic-model", "estimated_cost_usd": .1,
                "max_input_tokens": 100, "max_output_tokens": 100, "max_web_search_calls": 1}
    def generate(agent_id, topic, *, progress, **options):
        calls["generate"].append((agent_id, topic, options))
        if calls["hook"]:
            calls["hook"]()
        progress("checking_sources")
        if calls["generation_failure"]:
            raise HTTPException(504, detail={"code": "provider_timeout", "raw": "must-not-leak"})
        progress("analyzing")
        result = synthetic_result({"agent_id": agent_id, "topic": topic, "language": options["language"],
                                   "research_window": options["research_window"]})
        if calls["invalid_result"]:
            result["items"] = [{} for _ in range(101)]
        return result
    def render(result, path):
        calls["pdf"].append(result)
        if calls["pdf_failure"]:
            raise RuntimeError("Private renderer diagnostic must not leak")
        return fake_pdf(path)
    calls["worker"] = ReportWorker(reports, estimate=estimate, generator=generate, pdf_renderer=render, lease_seconds=30)
    return calls


def test_worker_runs_all_stages_saves_same_payload_for_pdf_and_retains_frozen_window(reports, engine):
    job = reports.submit(OWNER, AGENT, "Synthetic topic", language="en")["job"]
    reports.base_store.clock = lambda: NOW + timedelta(minutes=10)
    assert engine["worker"].run_once()
    done = reports.get_job(OWNER, job["job_id"])
    assert done["status"] == done["stage"] == "ready"
    assert [len(engine[part]) for part in ("estimate", "generate", "pdf")] == [1, 1, 1]
    assert engine["generate"][0][2]["research_window"] == {"start": job["research_window"]["start"],
        "end": job["research_window"]["end"], "timezone": "UTC"}
    saved = reports.get_report(OWNER, done["report_id"])
    assert saved["result"] == engine["pdf"][0]
    assert datetime.fromisoformat(saved["research_cutoff"]) == NOW
    assert reports.get_pdf_path(OWNER, done["report_id"]).is_file()
    assert reports.base_store.get_report(done["report_id"])["result"]["schema_version"] == 2
    assert reports.base_store.usage_summary()["daily"]["estimated_cost_usd"] == .01


def test_pdf_failure_leaves_snapshot_readable_and_pdf_retry_never_calls_paid_stages(reports, engine):
    engine["pdf_failure"] = True
    job = reports.submit(OWNER, AGENT, "PDF retry", idempotency_key="one-generation")["job"]
    engine["worker"].run_once()
    failed = reports.get_job(OWNER, job["job_id"])
    assert failed["status"] == "pdf_failed" and failed["stage"] == "preparing_pdf"
    saved = reports.get_report(OWNER, failed["report_id"])
    assert saved["result"]["response"] and saved["pdf"]["status"] == "failed"
    assert reports.latest_report(OWNER, AGENT)["report_id"] == failed["report_id"]
    before = reports.base_store.usage_summary()
    reports.retry_pdf(OWNER, failed["report_id"])
    engine["pdf_failure"] = False
    engine["worker"].run_once()
    assert [len(engine[part]) for part in ("estimate", "generate", "pdf")] == [1, 1, 2]
    assert reports.base_store.usage_summary() == before
    assert reports.get_report(OWNER, failed["report_id"])["created_at"] == saved["created_at"]
    assert reports.get_job(OWNER, job["job_id"])["status"] == "ready"


@pytest.mark.parametrize("blocked_by", ["daily_report_limit", "daily_budget_limit", "monthly_budget_limit"])
def test_existing_shared_quota_blocks_generator_and_job_failure_is_not_retried(reports, engine, blocked_by):
    base = reports.base_store
    if blocked_by == "daily_report_limit":
        base._limits = QuotaLimits(1, "5", "100")
        base.reserve_run(AGENT, "Earlier manual report", ".1")
    elif blocked_by == "daily_budget_limit":
        base._limits = QuotaLimits(50, ".05", "100")
    else:
        base._limits = QuotaLimits(50, "5", ".05")
    job = reports.submit(OWNER, AGENT, "Blocked generation", idempotency_key="blocked")["job"]
    engine["worker"].run_once()
    failed = reports.get_job(OWNER, job["job_id"])
    assert failed["status"] == "failed" and failed["error_code"] == blocked_by
    assert not engine["generate"] and not engine["pdf"]
    assert reports.submit(OWNER, AGENT, "Blocked generation", idempotency_key="blocked")["job"]["job_id"] == job["job_id"]
    assert engine["worker"].run_once() is False


def test_failed_paid_research_is_sanitized_retained_and_not_reexecuted_on_restart(reports, engine):
    engine["generation_failure"] = True
    job = reports.submit(OWNER, AGENT, "Provider timeout")["job"]
    engine["worker"].run_once()
    failed = reports.get_job(OWNER, job["job_id"])
    assert failed["status"] == "failed" and failed["error_code"] == "provider_timeout"
    assert "must-not-leak" not in str(failed)
    assert reports.base_store.usage_summary()["daily"]["estimated_cost_usd"] == .1
    reports.recover_expired(now=NOW + timedelta(hours=1))
    assert not engine["worker"].run_once() and len(engine["generate"]) == 1


def test_invalid_over_one_hundred_result_fails_before_snapshot_or_pdf_and_keeps_reservation(reports, engine):
    engine["invalid_result"] = True
    job = reports.submit(OWNER, AGENT, "Malformed provider result")["job"]
    engine["worker"].run_once()
    assert reports.get_job(OWNER, job["job_id"])["error_code"] == "invalid_research_report"
    assert reports.latest_report(OWNER, AGENT) is None
    assert not engine["pdf"] and reports.base_store.usage_summary()["daily"]["estimated_cost_usd"] == .1


def test_second_worker_cannot_exceed_global_database_concurrency(reports, engine):
    reports.submit(OWNER, AGENT, "First")
    reports.submit(OWNER, AGENT, "Second")
    started, release = threading.Event(), threading.Event()
    def hold():
        started.set()
        assert release.wait(5)
    engine["hook"] = hold
    other = ReportWorker(reports, estimate=engine["worker"].estimate, generator=engine["worker"].generator,
                         pdf_renderer=engine["worker"].pdf_renderer)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(engine["worker"].run_once)
        assert started.wait(5)
        try:
            assert other.run_once() is False
            assert len(engine["generate"]) == 1
        finally:
            release.set()
        assert future.result()
    engine["hook"] = None
    assert other.run_once()
    assert len(engine["generate"]) == 2


def test_restart_after_saved_paid_result_only_renders_pdf(reports, engine):
    job = reports.submit(OWNER, AGENT, "Paid result already saved")["job"]
    claim = reports.claim("dead-worker", lease_seconds=30)
    report_id = reserve_for_job(reports, claim)
    reports.base_store.finish_run(report_id, synthetic_result(claim), estimated_cost_usd=".01", cost_tracking_complete=True)
    reports.base_store.clock = lambda: NOW + timedelta(seconds=31)
    assert engine["worker"].run_once()
    assert not engine["estimate"] and not engine["generate"] and len(engine["pdf"]) == 1
    assert reports.get_job(OWNER, job["job_id"])["status"] == "ready"
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 1


def test_transient_snapshot_save_failure_recovers_locally_without_repeating_research(reports, engine, monkeypatch):
    job = reports.submit(OWNER, AGENT, "Recover local persistence")["job"]
    original_save = reports.save_report
    attempts = []
    def save(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_save(*args, **kwargs)
    monkeypatch.setattr(reports, "save_report", save)
    assert engine["worker"].run_once()
    waiting = reports.get_job(OWNER, job["job_id"])
    assert waiting["status"] == "queued" and waiting["stage"] == "preparing_pdf"
    assert waiting["queue_position"] == 1 and waiting["pdf_status"] is None
    assert reports.base_store.get_report(waiting["report_id"])["status"] == "succeeded"
    assert engine["worker"].run_once()
    assert reports.get_job(OWNER, job["job_id"])["status"] == "ready"
    assert [len(engine[part]) for part in ("estimate", "generate", "pdf")] == [1, 1, 1]
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 1
    assert len(attempts) == 2


@pytest.mark.parametrize("failure", [sqlite3.OperationalError("database is locked"), OSError("disk temporarily unavailable")])
def test_persistent_local_save_failure_is_bounded_and_frees_active_queue_slot(reports, engine, monkeypatch, failure):
    job = reports.submit(OWNER, AGENT, "Bound local recovery", idempotency_key="original-click")["job"]
    def save(*args, **kwargs):
        raise failure
    monkeypatch.setattr(reports, "save_report", save)
    for _ in range(2):
        assert engine["worker"].run_once()
        assert reports.get_job(OWNER, job["job_id"])["status"] == "queued"
    assert engine["worker"].run_once()
    failed = reports.get_job(OWNER, job["job_id"])
    assert failed["status"] == "failed" and failed["error_code"] == "report_recovery_failed"
    assert failed["pdf_status"] is None and failed["queue_position"] is None
    assert failed["error"]["retryable"] is True
    assert not engine["worker"].run_once()
    assert [len(engine[part]) for part in ("estimate", "generate", "pdf")] == [1, 1, 0]
    ledger = reports.base_store.get_report(failed["report_id"])
    assert ledger["status"] == "succeeded" and ledger["result"]["response"]
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 1
    assert reports.submit(OWNER, AGENT, "Bound local recovery", idempotency_key="original-click")["job"]["status"] == "failed"
    fresh = reports.submit(OWNER, AGENT, "Bound local recovery", idempotency_key="new-click")
    assert fresh["job"]["job_id"] != job["job_id"] and fresh["job"]["status"] == "queued"


def test_permanent_snapshot_validation_failure_is_not_requeued_or_mislabelled_pdf_failure(reports, engine, monkeypatch):
    job = reports.submit(OWNER, AGENT, "Invalid snapshot persistence")["job"]
    def invalid(*args, **kwargs):
        raise StoreError("invalid_research_report", "The stored snapshot is invalid.", 502)
    monkeypatch.setattr(reports, "save_report", invalid)
    assert engine["worker"].run_once()
    failed = reports.get_job(OWNER, job["job_id"])
    assert failed["status"] == "failed" and failed["error_code"] == "invalid_research_report"
    assert failed["pdf_status"] is None
    assert not engine["worker"].run_once()
    assert reports.base_store.get_report(failed["report_id"])["status"] == "succeeded"
    assert [len(engine[part]) for part in ("estimate", "generate", "pdf")] == [1, 1, 0]


@pytest.mark.parametrize("workers", [0, 3, True])
def test_larger_waiting_queue_does_not_raise_sqlite_worker_concurrency(reports, workers):
    with pytest.raises(StoreError) as invalid:
        ReportWorker(reports, workers=workers)
    assert invalid.value.code == "invalid_job_configuration"
    with pytest.raises(StoreError):
        reports.claim("worker", concurrency=workers)


def test_lifecycle_worker_completes_without_an_open_browser_and_start_is_idempotent(reports, engine, monkeypatch):
    monkeypatch.setenv("REPORT_WORKER_ENABLED", "true")
    started = threading.Event()
    engine["hook"] = started.set
    job = reports.submit(OWNER, AGENT, "Detached job")["job"]
    worker = engine["worker"]
    worker.start()
    worker.start()
    worker.wake()
    assert started.wait(5)
    assert worker.stop(timeout=5)
    assert reports.get_job(OWNER, job["job_id"])["status"] == "ready"
    assert len(engine["generate"]) == 1


@pytest.mark.parametrize("agent", ["mom-agent", "whatsapp-briefing", "literary-translation", "pre-meeting-briefing"])
def test_retired_assistants_cannot_submit_or_reserve_new_jobs(reports, agent):
    with pytest.raises(StoreError) as error:
        reports.submit(OWNER, agent, "Retired launch must not run", language="en")
    assert error.value.status_code == 404
    assert reports.list_jobs(OWNER) == []
    assert reports.base_store.usage_summary()["daily"]["attempts"] == 0
