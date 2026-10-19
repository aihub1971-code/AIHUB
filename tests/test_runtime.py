"""Provider contract tests. All model/search traffic is synthetic and mocked."""
import json
from datetime import datetime
from decimal import Decimal

import httpx
import pytest
from fastapi import HTTPException

from app import runtime


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    for name in ("OPENAI_API_KEY", "GROQ_API_KEY", "TAVILY_API_KEY", "OPENAI_MODEL_NAME", "MODEL_NAME",
                 "OPENAI_PRICING_MODEL", "OPENAI_INPUT_USD_PER_MILLION", "OPENAI_OUTPUT_USD_PER_MILLION",
                 "GROQ_PRICING_MODEL", "GROQ_INPUT_USD_PER_MILLION", "GROQ_OUTPUT_USD_PER_MILLION",
                 "TAVILY_CREDIT_PRICE_USD", "MAX_OUTPUT_TOKENS", "OPENAI_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai-key-no-network")
    monkeypatch.setattr(runtime, "_now_dubai", lambda: datetime(2026, 10, 19, 15, 30, tzinfo=runtime.DUBAI))


@pytest.fixture
def mocked_http(monkeypatch):
    original_client = httpx.Client
    requests = []

    def install(handler):
        def dispatch(request):
            requests.append(request)
            return handler(request)
        monkeypatch.setattr(runtime.httpx, "Client", lambda *args, **kwargs:
                            original_client(transport=httpx.MockTransport(dispatch), **kwargs))
        return requests
    return install


def model_response(text="هذه نتيجة اصطناعية للاختبار فقط.", **overrides):
    return {
        "status": "completed", "service_tier": "default",
        "output": [{"type": "reasoning", "summary": []},
                   {"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 300, "output_tokens": 80, "total_tokens": 380}, **overrides,
    }


def test_openai_selected_when_both_keys_exist_no_model_tools(monkeypatch, mocked_http):
    monkeypatch.setenv("GROQ_API_KEY", "fixture-groq-key-no-network")
    calls = mocked_http(lambda req: httpx.Response(200, json=model_response()))
    result = runtime.run_agent("literary-translation", "ترجم هذا النص الأدبي التجريبي")
    assert len(calls) == 1 and str(calls[0].url) == runtime.OPENAI_RESPONSES_URL
    body = json.loads(calls[0].content)
    assert body["model"] == "gpt-6.1-sol"
    assert body["store"] is False and body["tools"] == [] and body["tool_choice"] == "none"
    assert body["reasoning"] == {"effort": "low"} and body["service_tier"] == "default"
    assert body["max_output_tokens"] == 4096
    assert result["provider"] == "openai" and result["usage"]["prompt_tokens"] == 300
    assert result["cost_tracking_complete"] is True
    assert result["estimated_cost_usd"] == pytest.approx(0.00155)


def test_legacy_groq_only_when_openai_key_absent(monkeypatch, mocked_http):
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("GROQ_API_KEY", "fixture-groq-key-no-network")
    monkeypatch.setenv("GROQ_PRICING_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_INPUT_USD_PER_MILLION", "0.10")
    monkeypatch.setenv("GROQ_OUTPUT_USD_PER_MILLION", "0.20")
    calls = mocked_http(lambda req: httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": "نص اصطناعي للاختبار"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10}}))
    result = runtime.run_agent("literary-translation", "ترجم النص المرفق")
    assert len(calls) == 1 and str(calls[0].url) == runtime.GROQ_CHAT_URL
    assert result["provider"] == "groq"
    assert result["usage"]["input_tokens"] == 20
    assert result["cost_tracking_complete"] is True


@pytest.mark.parametrize("status_code", [400, 401, 429, 500])
def test_no_fallback_or_retry_and_no_upstream_error_leak(monkeypatch, mocked_http, status_code):
    monkeypatch.setenv("GROQ_API_KEY", "fixture-legacy-not-to-be-called")
    sentinel = "credential-or-request-body-must-not-appear"
    calls = mocked_http(lambda req: httpx.Response(status_code, json={"error": {"message": sentinel}}))
    with pytest.raises(HTTPException) as exc:
        runtime.run_agent("literary-translation", "ترجم النص التجريبي")
    assert len(calls) == 1
    assert calls[0].url.host == "api.openai.com"
    assert sentinel not in str(exc.value.detail)
    assert exc.value.detail["provider_status"] == status_code
    assert exc.value.status_code == (429 if status_code == 429 else 502)


@pytest.mark.parametrize("reply,expected_code", [
    (model_response(status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
     "incomplete_model_response"),
    (model_response(status="failed", error={"message": "not leaked"}), "invalid_model_response"),
    (model_response(output=[{"type": "message", "role": "assistant", "content": [
        {"type": "refusal", "refusal": "fixture refusal"}]}]), "model_refusal"),
    (model_response(output=[{"type": "function_call", "name": "malicious_tool"}]), "unexpected_model_tool_call"),
    (model_response(output=[]), "empty_model_response"),
    (model_response(output="wrong shape"), "invalid_model_response"),
    (["not an object"], "invalid_model_response"),
])
def test_bad_model_outputs_never_become_reports(mocked_http, reply, expected_code):
    calls = mocked_http(lambda req: httpx.Response(200, json=reply))
    with pytest.raises(HTTPException) as exc:
        runtime.run_agent("literary-translation", "ترجم هذه المادة")
    assert len(calls) == 1
    assert exc.value.detail["code"] == expected_code


def test_invalid_json_response_is_controlled(mocked_http):
    mocked_http(lambda req: httpx.Response(200, text="not json"))
    with pytest.raises(HTTPException) as exc:
        runtime.run_agent("literary-translation", "ترجم النص")
    assert exc.value.detail["code"] == "invalid_model_response"


def test_missing_usage_is_not_treated_as_zero_cost(mocked_http):
    mocked_http(lambda req: httpx.Response(200, json=model_response(usage=None)))
    result = runtime.run_agent("literary-translation", "ترجم النص")
    assert result["usage"]["input_tokens"] is None
    assert result["cost_tracking_complete"] is False
    assert result["estimated_cost_usd"] is None


def test_unexpected_paid_tier_retains_conservative_reservation(mocked_http):
    mocked_http(lambda req: httpx.Response(200, json=model_response(service_tier="ultrafast")))
    result = runtime.run_agent("literary-translation", "ترجم النص")
    assert result["cost_tracking_complete"] is False


def test_reservation_requires_model_bound_prices_for_custom_model(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_NAME", "fixture-unknown-model")
    with pytest.raises(HTTPException) as exc:
        runtime.estimate_reservation("literary-translation", "ترجم النص")
    assert exc.value.detail["code"] == "pricing_not_configured"
    monkeypatch.setenv("OPENAI_PRICING_MODEL", "different-model")
    monkeypatch.setenv("OPENAI_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("OPENAI_OUTPUT_USD_PER_MILLION", "2")
    with pytest.raises(HTTPException):
        runtime.estimate_reservation("literary-translation", "ترجم النص")
    monkeypatch.setenv("OPENAI_PRICING_MODEL", "fixture-unknown-model")
    estimate = runtime.estimate_reservation("literary-translation", "ترجم النص")
    assert estimate["pricing_model"] == "fixture-unknown-model"
    assert estimate["conservative_input_usd_per_million"] == "1.25"


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-1", "junk"])
def test_invalid_prices_fail_before_network(monkeypatch, bad_value):
    monkeypatch.setenv("OPENAI_PRICING_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("OPENAI_INPUT_USD_PER_MILLION", bad_value)
    monkeypatch.setenv("OPENAI_OUTPUT_USD_PER_MILLION", "10")
    with pytest.raises(HTTPException) as exc:
        runtime.estimate_reservation("literary-translation", "ترجم النص")
    assert exc.value.detail["code"] == "pricing_not_configured"


def test_reservation_covers_whole_bounded_input_output_and_search(monkeypatch, mocked_http):
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-search-key-no-network")
    calls = mocked_http(lambda req: pytest.fail("Reservation must not issue paid network requests"))
    estimate = runtime.estimate_reservation("iran-news", "أخبار إيران ١٩ أكتوبر ٢٠٢٦")
    assert calls == []
    assert estimate["max_search_credits"] == 2
    assert estimate["max_input_tokens"] < 272000
    assert estimate["max_output_tokens"] == 4096
    expected = (Decimal(estimate["max_input_tokens"]) * Decimal("2.5")
                + Decimal(4096) * Decimal("10")) / Decimal(1000000) + Decimal(".016")
    assert Decimal(str(estimate["estimated_cost_usd"])) >= expected
    assert runtime.estimate_run_reservation("iran-news", "أخبار إيران ١٩ أكتوبر ٢٠٢٦") == estimate["estimated_cost_usd"]


def test_search_free_agents_never_search_even_when_task_asks(monkeypatch, mocked_http):
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-search-key-no-network")
    calls = mocked_http(lambda req: httpx.Response(200, json=model_response()))
    result = runtime.run_agent("story-moodboard", "ابحث عن أحدث الأخبار")
    assert len(calls) == 1 and calls[0].url.host == "api.openai.com"
    assert result["web_search"]["status"] == "disabled_agent"
    assert result["web_search"]["request_count"] == 0


def test_invalid_reasoning_effort_fails_in_preflight(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    with pytest.raises(HTTPException) as exc:
        runtime.estimate_reservation("iran-news", "أخبار اليوم")
    assert exc.value.detail["code"] == "invalid_reasoning_effort"


def test_empty_and_oversized_tasks_fail_without_network():
    for task in ("", " ", "أ" * 8001):
        with pytest.raises(HTTPException) as exc:
            runtime.estimate_reservation("iran-news", task)
        assert exc.value.status_code == 422
