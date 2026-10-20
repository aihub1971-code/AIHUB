"""Offline tests: isolated database, test-only credentials, no paid API requests."""
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in (
        "OPENAI_API_KEY", "OPENAI_MODEL_NAME",
        "NEWSDATA_API_KEY", "NEWSDATA_PAGE_SIZE", "NEWSDATA_PLAN", "NEWSDATA_DAILY_CREDIT_LIMIT",
        "OPENAI_PRICING_MODEL", "OPENAI_INPUT_USD_PER_MILLION", "OPENAI_OUTPUT_USD_PER_MILLION",
        "OPENAI_WEB_SEARCH_PRICE_USD_PER_CALL", "OPENAI_RESEARCH_MAX_OUTPUT_TOKENS",
        "OPENAI_REASONING_EFFORT", "MAX_OUTPUT_TOKENS",
        "NEWS_MAX_OUTPUT_TOKENS", "MAX_REPORTS_PER_DAY", "DAILY_BUDGET_USD", "MONTHLY_BUDGET_USD",
        "APP_USERNAME", "APP_PASSWORD", "APP_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_HUB_DATABASE_PATH", str(tmp_path / "ai_hub_test.sqlite3"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("REPORT_WORKER_ENABLED", "false")
    def no_external_requests(*args, **kwargs):
        raise AssertionError("External HTTP is disabled in tests; use a synthetic fixture.")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_external_requests)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "test-owner")
    monkeypatch.setenv("APP_PASSWORD", "test-password-not-for-production")
    with TestClient(app) as c:
        c.auth = ("test-owner", "test-password-not-for-production")
        yield c
