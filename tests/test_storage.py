"""Money, request deduplication and concurrency gates run without external APIs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

from fastapi import HTTPException
import pytest

from app import service
from app.storage import QuotaLimits, Store, StoreError


NOW = datetime(2026, 10, 19, 11, 24, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    database = Store(tmp_path / "test.sqlite3", QuotaLimits(50, "100", "1000"), clock=lambda: NOW)
    database.initialize()
    return database


def reserve(store, **kwargs):
    options = {"agent_id": "iran-news", "task": "Test report", "reservation_usd": "0.1"}
    options.update(kwargs)
    return store.reserve_run(**options)


def test_failed_attempts_count_toward_fifty_report_limit(store):
    for _ in range(50):
        attempt = reserve(store)
        store.fail_run(attempt.report["report_id"], "provider_timeout")
    with pytest.raises(StoreError, match="daily report attempt") as error:
        reserve(store)
    assert error.value.code == "daily_report_limit"
    summary = store.usage_summary()
    assert summary["daily"]["attempts"] == 50
    assert summary["daily"]["failed"] == 50
    assert summary["daily"]["estimated_cost_usd"] == 5
    assert summary["daily"]["remaining"] == 0


def test_transactional_daily_quota_under_concurrent_requests(tmp_path):
    database = Store(tmp_path / "race.sqlite3", QuotaLimits(7, "100", "1000"), clock=lambda: NOW)
    database.initialize()

    def attempt(index):
        try:
            return reserve(database, idempotency_key=f"request-{index}").created
        except StoreError as error:
            assert error.code == "daily_report_limit"
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(attempt, range(30)))
    assert sum(outcomes) == 7
    assert database.usage_summary()["daily"]["attempts"] == 7


def test_budget_reservation_is_atomic_and_uses_exact_decimal_units(tmp_path):
    database = Store(tmp_path / "budget.sqlite3", QuotaLimits(50, ".3", "10"), clock=lambda: NOW)
    database.initialize()

    def attempt(_):
        try:
            return reserve(database).created
        except StoreError as error:
            assert error.code == "daily_budget_limit"
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(20)))
    assert sum(outcomes) == 3
    assert database.usage_summary()["daily"]["estimated_cost_usd"] == .3


def test_concurrent_idempotency_key_creates_only_one_attempt(store):
    with ThreadPoolExecutor(max_workers=12) as executor:
        reservations = list(executor.map(lambda _: reserve(store, idempotency_key="same-request"), range(24)))
    assert sum(item.created for item in reservations) == 1
    assert len({item.report["report_id"] for item in reservations}) == 1
    assert store.usage_summary()["daily"]["attempts"] == 1
    with pytest.raises(StoreError) as error:
        reserve(store, task="Different task", idempotency_key="same-request")
    assert error.value.code == "idempotency_conflict"


def test_daily_quota_rolls_over_at_dubai_midnight(tmp_path):
    database = Store(tmp_path / "calendar.sqlite3", QuotaLimits(1, "100", "1000"))
    database.initialize()
    before = datetime(2026, 10, 19, 19, 59, 59, tzinfo=timezone.utc)
    after = before + timedelta(seconds=1)
    first = reserve(database, now=before)
    second = reserve(database, now=after)
    assert first.report["local_date"] == "2026-10-19"
    assert second.report["local_date"] == "2026-10-20"
    assert database.usage_summary(now=after)["daily"]["attempts"] == 1
    assert database.usage_summary(now=after)["monthly"]["attempts"] == 2


def test_monthly_budget_survives_day_rollover_and_resets_in_dubai(tmp_path):
    database = Store(tmp_path / "month.sqlite3", QuotaLimits(50, "5", ".15"))
    database.initialize()
    reserve(database, now=NOW)
    with pytest.raises(StoreError) as error:
        reserve(database, now=NOW + timedelta(days=1))
    assert error.value.code == "monthly_budget_limit"
    next_month = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
    assert reserve(database, now=next_month).created
    assert database.usage_summary(now=next_month)["month"] == "2026-11"
    assert database.usage_summary(now=next_month)["monthly"]["attempts"] == 1


def test_success_reconciles_only_complete_cost_tracking(store):
    first = reserve(store)
    finished = store.finish_run(first.report["report_id"], {"response": "Saved"}, estimated_cost_usd=".0123451", cost_tracking_complete=True, usage={"input_tokens": 100})
    assert finished["estimated_cost_usd"] == .012346
    assert finished["cost_basis"] == "usage_estimate"
    assert store.get_report(first.report["report_id"])["result"]["response"] == "Saved"
    second = reserve(store)
    incomplete = store.finish_run(second.report["report_id"], {"response": "No usage"}, estimated_cost_usd="0", cost_tracking_complete=False)
    assert incomplete["estimated_cost_usd"] == .1
    assert incomplete["cost_basis"] == "reservation_retained"
    assert "result" not in store.list_reports()[0]


def test_stale_pending_work_is_not_retried_and_keeps_reservation(store):
    reservation = reserve(store, idempotency_key="old-run")
    assert store.recover_stale_runs(now=NOW + timedelta(minutes=14)) == 0
    assert store.recover_stale_runs(now=NOW + timedelta(minutes=16)) == 1
    record = store.get_report(reservation.report["report_id"])
    assert record["status"] == "interrupted"
    assert record["estimated_cost_usd"] == .1
    assert not reserve(store, idempotency_key="old-run").created


def test_errors_are_sanitized_and_invalid_limits_fail_closed(store):
    record = reserve(store).report
    store.fail_run(record["report_id"], "provider secret=must-not-be-stored")
    assert store.get_report(record["report_id"])["error_code"] == "execution_failed"
    with pytest.raises(StoreError):
        QuotaLimits(51)
    for value in ("nan", "inf", "-0.1", "0"):
        with pytest.raises(StoreError):
            reserve(store, reservation_usd=value)
    assert store.usage_summary()["daily"]["attempts"] == 1


@pytest.fixture
def fake_runtime(monkeypatch):
    monkeypatch.setattr(service.runtime, "runtime_is_ready", lambda: True)
    monkeypatch.setattr(service.runtime, "estimate_reservation", lambda agent_id, task: {"estimated_cost_usd": .1, "provider": "test", "model": "test-model", "input_price_usd_per_million": 2}, raising=False)
    calls = []

    def run(agent_id, task):
        calls.append((agent_id, task))
        return {"response": "Report text", "provider": "test", "model": "test-model", "usage": {"input_tokens": 50, "output_tokens": 10}, "estimated_cost_usd": .005, "cost_tracking_complete": True}

    monkeypatch.setattr(service.runtime, "run_agent", run, raising=False)
    return calls


def test_service_gate_does_not_call_provider_after_quota(store, fake_runtime):
    store._limits = QuotaLimits(1, "100", "1000")
    response = service.execute_report("iran-news", "Create report", store=store)
    assert response["report_id"].startswith("rpt_")
    assert response["accounting"]["estimated_cost_usd"] == .005
    with pytest.raises(HTTPException) as error:
        service.execute_report("iran-news", "Another report", store=store)
    assert error.value.status_code == 429
    assert len(fake_runtime) == 1


def test_service_replays_success_even_if_provider_config_changes(store, fake_runtime, monkeypatch):
    response = service.execute_report("iran-news", "Create report", idempotency_key="request", store=store)
    monkeypatch.setattr(service.runtime, "runtime_is_ready", lambda: False)
    replay = service.execute_report("iran-news", "Create report", idempotency_key="request", store=store)
    assert replay["report_id"] == response["report_id"]
    assert replay["idempotency_replayed"] is True
    assert len(fake_runtime) == 1


def test_failed_service_attempt_retains_reservation_and_never_retries_key(store, fake_runtime, monkeypatch):
    def timeout(*args):
        fake_runtime.append(args)
        raise HTTPException(504, detail={"code": "provider_timeout", "debug": "secret-provider-body"})

    monkeypatch.setattr(service.runtime, "run_agent", timeout)
    with pytest.raises(HTTPException) as first:
        service.execute_report("iran-news", "Create report", idempotency_key="timeout", store=store)
    assert first.value.status_code == 504
    assert "secret-provider-body" not in str(first.value.detail)
    with pytest.raises(HTTPException) as second:
        service.execute_report("iran-news", "Create report", idempotency_key="timeout", store=store)
    assert second.value.status_code == 409
    assert len(fake_runtime) == 1
    assert store.usage_summary()["daily"]["estimated_cost_usd"] == .1


def test_service_concurrent_same_key_makes_one_external_call(store, fake_runtime, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def held_run(*args):
        fake_runtime.append(args)
        started.set()
        assert release.wait(5)
        return {"response": "Saved", "cost_tracking_complete": False}

    monkeypatch.setattr(service.runtime, "run_agent", held_run)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.execute_report, "iran-news", "Create report", idempotency_key="race", store=store)
        assert started.wait(5)
        try:
            with pytest.raises(HTTPException) as error:
                service.execute_report("iran-news", "Create report", idempotency_key="race", store=store)
            assert error.value.detail["code"] == "report_in_progress"
        finally:
            release.set()
        assert future.result()["report_id"]
    assert len(fake_runtime) == 1


def test_usage_read_recovers_pending_work_without_running_scheduler(store):
    reservation = reserve(store)
    summary = store.usage_summary(now=NOW + timedelta(minutes=16))
    assert summary["daily"]["pending"] == 0
    assert summary["daily"]["failed"] == 1
    assert summary["daily"]["estimated_cost_usd"] == .1
    assert store.get_report(reservation.report["report_id"])["status"] == "interrupted"


def test_openai_key_alone_enables_native_search_with_positive_reservation(monkeypatch):
    # Preflight must require no secondary search-service credential and must
    # reserve native tool/model exposure before making any external request.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-credential")
    assert service.runtime.runtime_is_ready() is True
    estimate = service.runtime.estimate_reservation("iran-news", "أهم أخبار إيران اليوم")
    assert estimate["provider"] == "openai"
    assert estimate["estimated_cost_usd"] > 0
    assert estimate["max_web_search_calls"] == 1
    assert estimate["max_model_calls"] == 2


def test_zero_estimate_with_billable_bound_is_blocked_before_runtime(store, fake_runtime, monkeypatch):
    monkeypatch.setattr(service.runtime, "estimate_reservation", lambda *args: {"estimated_cost_usd": 0, "max_input_tokens": 100, "max_output_tokens": 100, "max_web_search_calls": 0})
    with pytest.raises(HTTPException) as error:
        service.execute_report("iran-news", "Create report", store=store)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "invalid_cost_configuration"
    assert not fake_runtime
    assert store.usage_summary()["daily"]["attempts"] == 0
