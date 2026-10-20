"""Provider failure classification and diagnostic privacy; synthetic HTTP only."""
from copy import deepcopy
import json

import httpx
import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator

from app import research, runtime
from app.agent_profiles import get_profile
from app.report_errors import public_error, safe_diagnostics


KEY = "sk-proj-synthetic-secret-not-a-real-credential"
PAYLOAD = {"model": "fixture-model", "max_output_tokens": 128, "input": "Synthetic request"}


@pytest.fixture
def provider(monkeypatch):
    original_client = httpx.Client
    calls = []

    def install(response):
        def dispatch(request):
            calls.append(request)
            if isinstance(response, Exception):
                raise response
            return response
        monkeypatch.setattr(research.httpx, "Client", lambda **kwargs:
                            original_client(transport=httpx.MockTransport(dispatch), **kwargs))
        return calls

    return install


def completed(*native, **overrides):
    return {"id": "resp_synthetic_123", "status": "completed", "service_tier": "default",
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "output": [*native, {"type": "message", "role": "assistant", "status": "completed",
                              "content": [{"type": "output_text", "text": "Synthetic findings."}]}], **overrides}


def native(action):
    return {"id": "ws_synthetic_" + action, "type": "web_search_call", "status": "completed",
            "action": {"type": action, "query": "synthetic topic", "url": "https://fixture.example/article"}}


@pytest.mark.parametrize("caller", [research._call_response, runtime._call_model])
@pytest.mark.parametrize("http_status,error_type,provider_code,expected,status", [
    (401, "invalid_request_error", "invalid_api_key", "openai_auth_failed", 502),
    (403, "permission_error", "permission_denied", "openai_access_denied", 502),
    (429, "insufficient_quota", "insufficient_quota", "openai_quota_exceeded", 429),
    (429, "invalid_request_error", "credit_balance_exhausted", "openai_quota_exceeded", 429),
    (429, None, "organization_spend_limit_exceeded", "openai_quota_exceeded", 429),
    (429, "rate_limit_error", "project_spend_limit_exceeded", "openai_quota_exceeded", 429),
    (429, "invalid_request_error", "organization_usage_limit_exceeded", "openai_quota_exceeded", 429),
    (429, "invalid_request_error", "billing_hard_limit_reached", "openai_quota_exceeded", 429),
    (429, "rate_limit_error", "rate_limit_exceeded", "openai_rate_limited", 429),
    (429, "rate_limit_error", "slow_down", "openai_rate_limited", 429),
    (503, "service_unavailable_error", "server_is_overloaded", "openai_unavailable", 503),
    (500, "server_error", "server_error", "openai_unavailable", 503),
    (502, None, None, "openai_unavailable", 503),
    (408, None, None, "openai_timeout", 504),
    (400, "invalid_request_error", "unsupported_parameter", "openai_request_rejected", 502),
    (404, "invalid_request_error", "model_not_found", "openai_request_rejected", 502),
    (422, "invalid_request_error", None, "openai_request_rejected", 502),
    (302, None, None, "openai_request_rejected", 502),
])
def test_http_failures_are_classified_once_without_provider_prose(
        provider, http_status, error_type, provider_code, expected, status, caller):
    calls = provider(httpx.Response(http_status, json={"error": {
        "type": error_type, "code": provider_code, "param": "tools[0].type",
        "message": "Private provider prose " + KEY}},
        headers={"x-request-id": "req_synthetic_123", "retry-after": "12",
                 "location": "https://must-not-follow.example/"}))
    with pytest.raises(HTTPException) as captured:
        caller(KEY, PAYLOAD)
    error = captured.value
    assert error.status_code == status and error.detail["code"] == expected
    assert error.detail["provider_status"] == http_status
    assert error.detail["request_id"] == "req_synthetic_123"
    assert error.detail["provider_param"] == "tools[0].type"
    assert error.detail.get("provider_type") == error_type
    assert error.detail.get("provider_code") == provider_code
    assert error.detail["retry_after_seconds"] == 12
    assert len(calls) == 1
    serialized = json.dumps(error.detail)
    assert KEY not in serialized and "Private provider prose" not in serialized
    diagnostics = safe_diagnostics(error, "searching")
    assert diagnostics["request_id"] == "req_synthetic_123"
    public = public_error(expected, "job_synthetic", "searching", diagnostics)
    assert set(public) == {"code", "reference", "stage", "retryable", "retry_after_seconds"}
    assert public["retryable"] is (expected in {"openai_rate_limited", "openai_unavailable", "openai_timeout"})


@pytest.mark.parametrize("body", ["not JSON: private provider prose", "[]", '{"error": "private prose"}'])
def test_non_object_error_bodies_keep_safe_header_diagnostics(provider, body):
    calls = provider(httpx.Response(429, text=body, headers={"x-request-id": "req_plain"}))
    with pytest.raises(HTTPException) as captured:
        research._call_response(KEY, PAYLOAD)
    assert captured.value.detail["code"] == "openai_rate_limited"
    assert captured.value.detail["request_id"] == "req_plain"
    assert "private" not in json.dumps(captured.value.detail)
    assert len(calls) == 1


def test_allowlisted_diagnostics_reject_credentials_urls_prose_and_oversized_tokens(provider):
    provider(httpx.Response(400, json={"error": {"message": "Private message " + KEY,
        "type": "sk-proj-another-synthetic-secret-key", "code": KEY,
        "param": "input contains the owner's private notes"}, "id": "r" * 161},
        headers={"x-request-id": "https://private.example/?key=" + KEY,
                 "retry-after": "private provider prose"}))
    with pytest.raises(HTTPException) as captured:
        research._call_response(KEY, PAYLOAD)
    assert set(captured.value.detail) == {"code", "message", "provider_status"}
    assert KEY not in json.dumps(safe_diagnostics(captured.value, "searching"))


@pytest.mark.parametrize("retry,expected", [
    ("0", 0), ("1.25", 2), ("60", 60), ("86400", 86400),
    ("86400.01", None), ("-1", None), ("999999999999999", None), ("NaN", None), ("", None),
])
def test_retry_after_is_a_bounded_nonnegative_integer(provider, retry, expected):
    provider(httpx.Response(503, headers={"retry-after": retry}))
    with pytest.raises(HTTPException) as captured:
        research._call_response(KEY, PAYLOAD)
    assert captured.value.detail.get("retry_after_seconds") == expected


def test_retry_after_http_date_uses_provider_date_without_retaining_headers(provider):
    provider(httpx.Response(503, headers={"date": "Tue, 20 Oct 2026 12:00:00 GMT",
                                          "retry-after": "Tue, 20 Oct 2026 12:02:00 GMT"}))
    with pytest.raises(HTTPException) as captured:
        research._call_response(KEY, PAYLOAD)
    assert captured.value.detail["retry_after_seconds"] == 120
    assert "Tue," not in json.dumps(captured.value.detail)


@pytest.mark.parametrize("status,expected", [(200, "invalid_model_response"), (503, "openai_unavailable")])
def test_oversized_provider_body_is_not_parsed_or_persisted(provider, monkeypatch, status, expected):
    monkeypatch.setattr(research, "MAX_MODEL_RESPONSE_BYTES", 64)
    calls = provider(httpx.Response(status, text=KEY * 5, headers={"x-request-id": "req_oversized"}))
    with pytest.raises(HTTPException) as captured:
        research._call_response(KEY, PAYLOAD)
    assert captured.value.detail["code"] == expected
    assert captured.value.detail["request_id"] == "req_oversized"
    assert KEY not in json.dumps(captured.value.detail) and len(calls) == 1


def test_native_action_overflow_captures_counts_without_relaxing_the_bound(provider):
    calls = provider(httpx.Response(200, json=completed(native("search"), native("open_page"), native("find_in_page")),
                                   headers={"x-request-id": "req_overflow"}))
    with pytest.raises(HTTPException) as captured:
        research._call_response(KEY, {**PAYLOAD, "max_tool_calls": 2}, actions=2)
    diagnostics = safe_diagnostics(captured.value, "searching")
    assert captured.value.detail["code"] == "search_call_limit_exceeded"
    assert diagnostics == {"stage": "searching", "exception_type": "HTTPException", "provider_status": 200,
        "request_id": "req_overflow", "response_id": "resp_synthetic_123", "native_action_count": 3,
        "search_call_count": 1, "allowed_native_actions": 2}
    assert len(calls) == 1


def test_allowed_search_open_and_find_keep_separate_native_and_search_counts(provider):
    calls = provider(httpx.Response(200, json=completed(native("search"), native("open_page"), native("find_in_page"))))
    payload = {**PAYLOAD, "max_tool_calls": 3}
    original = deepcopy(payload)
    result = research._call_response(KEY, payload, actions=3)
    assert len(result["native_calls"]) == 3 and result["search_calls"] == 1
    assert result["usage_bound_matches"] is True
    assert payload == original and json.loads(calls[0].content)["max_tool_calls"] == 3
    assert len(calls) == 1


def test_synthesis_cannot_gain_a_native_tool_call(provider):
    provider(httpx.Response(200, json=completed(native("search"))))
    with pytest.raises(HTTPException) as captured:
        research._call_response(KEY, PAYLOAD)
    assert captured.value.detail["code"] == "search_call_limit_exceeded"
    assert captured.value.detail["allowed_native_actions"] == 0


def test_incomplete_response_retains_safe_reason_and_request_reference(provider):
    provider(httpx.Response(200, json=completed(status="incomplete",
        incomplete_details={"reason": "max_output_tokens", "message": "private prose " + KEY}),
        headers={"x-request-id": "req_incomplete"}))
    with pytest.raises(HTTPException) as captured:
        research._call_response(KEY, PAYLOAD)
    assert captured.value.detail["code"] == "incomplete_model_response"
    assert captured.value.detail["reason"] == "max_output_tokens"
    assert captured.value.detail["request_id"] == "req_incomplete"
    assert KEY not in json.dumps(captured.value.detail)


@pytest.mark.parametrize("failure,code,status", [
    (httpx.ReadTimeout("private timeout " + KEY), "openai_timeout", 504),
    (httpx.ConnectError("private connection " + KEY), "openai_connection_error", 502),
])
def test_transport_failures_never_echo_exception_text_or_retry(provider, failure, code, status):
    calls = provider(failure)
    with pytest.raises(HTTPException) as captured:
        research._call_response(KEY, PAYLOAD)
    assert captured.value.status_code == status and captured.value.detail["code"] == code
    assert KEY not in json.dumps(captured.value.detail) and len(calls) == 1


@pytest.mark.parametrize("caller", [research._call_response, runtime._call_model])
def test_schema_rejection_retains_safe_context_without_provider_or_source_prose(provider, caller):
    message = ("Invalid schema for response_format 'ai_hub_research_report': "
        "In context=('properties', 'sections', 'items'), 'required' is required to include every key. "
        "Missing 'source_ids'. Private source excerpt and credential " + KEY)
    calls = provider(httpx.Response(400, json={"error": {
        "type": "invalid_request_error", "code": "invalid_json_schema",
        "param": "text.format.schema", "message": message}},
        headers={"x-request-id": "req_schema_fixture"}))
    with pytest.raises(HTTPException) as captured:
        caller(KEY, PAYLOAD)
    diagnostics = safe_diagnostics(captured.value, "analyzing")
    assert captured.value.detail["code"] == "openai_request_rejected"
    assert diagnostics["provider_status"] == 400
    assert diagnostics["request_id"] == "req_schema_fixture"
    assert diagnostics["provider_param"] == "text.format.schema"
    assert diagnostics["schema_path"] == "properties.sections.items"
    assert diagnostics["schema_keyword"] == "required"
    assert diagnostics["schema_property"] == "source_ids"
    assert diagnostics["schema_reason"] == "missing_required"
    assert len(calls) == 1
    serialized = json.dumps(captured.value.detail)
    assert KEY not in serialized and "Private source" not in serialized and message not in serialized
    assert public_error("openai_request_rejected", "job_fixture", "analyzing", diagnostics) == {
        "code": "openai_request_rejected", "reference": "job_fixture", "stage": "analyzing", "retryable": False}


@pytest.mark.parametrize("message,expected", [
    ("In context=(), 'additionalProperties' is required to be supplied and to be false.",
     {"schema_path": "root", "schema_keyword": "additionalProperties", "schema_reason": "additional_properties"}),
    ("In context=('properties', 'visuals'), 'allOf' is not permitted.",
     {"schema_path": "properties.visuals", "schema_keyword": "allOf", "schema_reason": "unsupported_keyword"}),
    ("In context=('properties', 'sections', 'items'), Extra required key 'attribution' supplied.",
     {"schema_path": "properties.sections.items", "schema_property": "attribution", "schema_reason": "extra_required"}),
    ("In context=('properties', 'items', 'items', 'properties', 'evidence_quote'), 'enum' values must be unique: ['Private quote', 'Private quote'].",
     {"schema_path": "properties.items.items.properties.evidence_quote", "schema_keyword": "enum", "schema_reason": "duplicate_enum"}),
    ("'enum' must contain at least one value.",
     {"schema_keyword": "enum", "schema_reason": "empty_enum"}),
    ("In context=('properties', 'items', 'items', 'properties', 'evidence_quote', 'enum'), ['Private quote', 'Private quote'] has non-unique elements.",
     {"schema_path": "properties.items.items.properties.evidence_quote.enum", "schema_keyword": "enum", "schema_reason": "duplicate_enum"}),
    ("Too many enum values: maximum of 1,000, got 1001.",
     {"schema_reason": "enum_limit", "schema_limit": 1000, "schema_actual": 1001}),
    ("Total string length of enum values exceeds the limit of 120000; received 120001.",
     {"schema_reason": "schema_size_limit", "schema_limit": 120000, "schema_actual": 120001}),
    ("Schema nesting depth must not exceed 10; found 11.",
     {"schema_reason": "nesting_limit", "schema_limit": 10, "schema_actual": 11}),
    ("In context=('properties', 'private_source_title', 'enum', 0), unknown validation error.",
     {"schema_path": "properties.redacted", "schema_reason": "invalid_schema"}),
    ("In context=('properties', 'sections', 'anyOf', 0), unknown validation error.",
     {"schema_path": "properties.sections.anyOf.0", "schema_reason": "invalid_schema"}),
    ("'enum' value ['Private excerpt maximum 500000 got 700000'] is invalid.",
     {"schema_keyword": "enum", "schema_reason": "enum_limit"}),
    ("'enum' value 'Private excerpt maximum 500000 got 700000' is invalid.",
     {"schema_keyword": "enum", "schema_reason": "enum_limit"}),
    ("Schema size too large: maximum 1000001, received 2000000.",
     {"schema_reason": "schema_size_limit"}),
])
def test_schema_error_categories_paths_and_limits_are_allowlisted(message, expected):
    assert research._schema_error_diagnostics({"code": "invalid_json_schema", "message": message}) == expected


@pytest.mark.parametrize("error", [
    {"code": "rate_limit_exceeded", "message": "Private notes: enum maximum 1000 got 1001"},
    {"code": "invalid_json_schema", "message": {"private": "source text"}},
    {"code": "invalid_json_schema"},
])
def test_schema_message_parser_does_not_handle_unrelated_or_malformed_messages(error):
    assert research._schema_error_diagnostics(error) == {}


def test_schema_limit_diagnostics_survive_internal_sanitization_only():
    error = HTTPException(502, {"schema_reason": "schema_size_limit", "schema_limit": 120000,
        "schema_actual": 120001, "schema_path": "properties.items.items.properties.evidence_quote",
        "message": "Private provider message", "schema_keyword": "https://private.example/",
        "schema_property": "Private source title"})
    diagnostics = safe_diagnostics(error, "analyzing")
    assert diagnostics == {"stage": "analyzing", "exception_type": "HTTPException",
        "schema_reason": "schema_size_limit", "schema_limit": 120000, "schema_actual": 120001,
        "schema_path": "properties.items.items.properties.evidence_quote"}
    assert not set(diagnostics) & set(public_error("openai_request_rejected", "job_fixture", "analyzing", diagnostics)) - {"stage"}


@pytest.mark.parametrize("agent", ["historical-reading-companion", "story-moodboard"])
@pytest.mark.parametrize("source_count", [0, 1, 40])
def test_native_profile_schemas_obey_strict_contract_with_full_source_bound(agent, source_count):
    sources = [{"id": f"src_{index:016x}", "title": (f"Synthetic title {index} " + "a" * 499)[:499],
        "excerpt": (f"Synthetic excerpt {index} " + "b" * 392)[:392], "role": "reference"}
        for index in range(source_count)]
    schema = research._schema(get_profile(agent), sources)
    Draft202012Validator.check_schema(schema)
    objects, enums = [], []

    def inspect(node):
        if isinstance(node, dict):
            assert not set(node) & {"allOf", "not", "if", "then", "else", "dependentRequired", "dependentSchemas"}
            if node.get("type") == "object":
                objects.append(node)
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            if "enum" in node:
                assert node["enum"] and len(node["enum"]) == len(set(node["enum"]))
                enums.append(node["enum"])
            for value in node.values():
                inspect(value)
        elif isinstance(node, list):
            for value in node:
                inspect(value)

    inspect(schema)
    assert len(objects) == 5
    assert sum(map(len, enums)) <= 1000
    assert sum(len(value) for choices in enums for value in choices) < 120000
    assert len(schema["properties"]["items"]["items"]["properties"]["evidence_quote"]["enum"]) == max(1, source_count * 2)
