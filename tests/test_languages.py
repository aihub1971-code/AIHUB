"""Bilingual output and persistence checks; all news and provider data are synthetic."""
import json
from datetime import datetime, timezone

import pytest

from app import runtime
from app.schemas import ScheduleCreate, TaskRequest
from app.scheduler import Scheduler
from app.storage import get_store


def english_fixture():
    phrase = "A fictional meeting took place on 2026-10-19."
    source = {"source_id": "s1", "title": "Synthetic Iran diplomacy article",
              "url": "https://www.ft.com/content/ai-hub-fictional-language-test",
              "content": f"Published: 2026-10-20. {phrase}"}
    item = {"headline_en": "A fictional diplomatic meeting described in a test article",
            "summary_en": "This synthetic report describes a meeting from the previous day; it is not real news.",
            "source_id": "s1", "research_note_quote": phrase,
            "published_date": "2026-10-20", "published_date_note": "Published: 2026-10-20.",
            "event_date": "2026-10-19", "event_date_note": phrase}
    return source, item


def test_english_news_schema_does_not_mutate_arabic_contract():
    english = runtime._news_report_schema("en")["properties"]["items"]["items"]
    arabic = runtime._news_report_schema("ar")["properties"]["items"]["items"]
    assert "headline_en" in english["required"] and "headline_ar" not in english["properties"]
    assert "headline_ar" in arabic["required"] and "headline_en" not in arabic["properties"]
    assert TaskRequest(task="Synthetic task").language == "ar"
    assert ScheduleCreate(agent_id="iran-news", task="Synthetic task", times=["08:00"], language="en").language == "en"


def test_english_news_keeps_official_link_and_publication_event_distinction():
    source, item = english_fixture()
    as_of = datetime(2026, 10, 20, 9, 0, tzinfo=runtime.DUBAI)
    window = runtime._coverage_window("iran-news", "today", as_of)
    report = runtime._validate_news(json.dumps({"items": [item], "background_items": []}),
                                    [source], window, as_of, language="en")
    text = runtime._render_news("iran-news", report, window, as_of, "used", 6, language="en")
    assert report["item_count"] == 1 and report["fresh_event_count"] == 0
    assert report["items"][0]["event_timing_relative_to_window"] == "before_window"
    assert "Iran news briefing" in text and "Selected reports: 1" in text
    assert "[Open the official article](<" + source["url"] + ">)" in text
    assert "Publication date does not establish event date" in text


def test_english_news_rejects_wrong_fields_and_unsupported_publication():
    source, item = english_fixture()
    as_of = datetime(2026, 10, 20, 9, 0, tzinfo=runtime.DUBAI)
    window = runtime._coverage_window("iran-news", "today", as_of)
    wrong = {**item, "headline_ar": item["headline_en"]}
    wrong.pop("headline_en")
    forged = {**item, "published_date": "2026-10-19", "published_date_note": "Published: 2026-10-19."}
    report = runtime._validate_news(json.dumps({"items": [wrong, forged], "background_items": []}),
                                    [source], window, as_of, language="en")
    assert report["item_count"] == 0
    assert report["discarded_item_reasons"] == {"invalid_structure": 1, "publication_evidence": 1}


def test_english_run_uses_english_schema_and_returns_english_markdown(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key-no-network")
    monkeypatch.setattr(runtime, "_now_dubai", lambda: datetime(2026, 10, 20, 9, tzinfo=runtime.DUBAI))
    source, item = english_fixture()
    payloads = []

    def provider(_key, payload, *, research=False):
        payloads.append(payload)
        if research:
            text = "No eligible synthetic articles for this publisher."
            sources = []
            if payload["tools"][0]["filters"]["allowed_domains"] == ["ft.com"]:
                text = f"Title: {source['title']} — {source['url']} {source['content']}"
                sources = [{"type": "url", "url": source["url"], "title": source["title"]}]
            return {"content": text, "parts": [{"text": text, "annotations": []}],
                    "native_calls": [{"action": {"type": "search", "sources": sources}}],
                    "usage": runtime._normalise_usage({"input_tokens": 100, "output_tokens": 100}),
                    "tier_matches": True, "usage_bound_matches": True}
        assert "headline_en" in payload["text"]["format"]["schema"]["properties"]["items"]["items"]["required"]
        assert "neutral English" in payload["input"][0]["content"][0]["text"]
        return {"content": json.dumps({"items": [item], "background_items": []}),
                "parts": [], "native_calls": [],
                "usage": runtime._normalise_usage({"input_tokens": 100, "output_tokens": 100}),
                "tier_matches": True, "usage_bound_matches": True}

    monkeypatch.setattr(runtime, "_call_model", provider)
    result = runtime.run_agent("iran-news", "أخبار اليوم", language="en")
    assert result["language"] == "en" and result["item_count"] == 1
    assert len(payloads) == 7 and result["model_call_count"] == 7
    assert result["response"].startswith("## Iran news briefing")


def test_language_persists_and_is_part_of_report_idempotency(client, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "runtime_is_ready", lambda: True)
    monkeypatch.setattr(runtime, "estimate_reservation", lambda *args, **kwargs: {
        "provider": "openai", "model": "fixture", "estimated_cost_usd": .2})

    def provider(*args, **kwargs):
        calls.append(kwargs.get("language", "ar"))
        return {"response": "## Synthetic English report", "usage": {},
                "estimated_cost_usd": .01, "cost_tracking_complete": True}

    monkeypatch.setattr(runtime, "run_agent", provider)
    headers = {"Idempotency-Key": "bilingual-report-fixture"}
    body = {"task": "Synthetic task", "language": "en"}
    first = client.post("/v1/agents/decision-briefing/run", json=body, headers=headers)
    assert first.status_code == 200, first.text
    report_id = first.json()["report_id"]
    assert first.json()["language"] == "en"
    replay = client.post("/v1/agents/decision-briefing/run", json=body, headers=headers)
    assert replay.json()["idempotency_replayed"] is True
    conflict = client.post("/v1/agents/decision-briefing/run", json={**body, "language": "ar"}, headers=headers)
    assert conflict.status_code == 409
    assert calls == ["en"]
    record = client.get(f"/v1/reports/{report_id}").json()
    assert record["result"]["language"] == record["pricing_snapshot"]["language"] == "en"
    assert "Synthetic English report" in client.get(f"/v1/reports/{report_id}/download").text


def test_invalid_language_rejected_before_report_attempt(client):
    reply = client.post("/v1/agents/iran-news/run", json={"task": "Synthetic", "language": "fr"})
    assert reply.status_code == 422
    assert client.get("/v1/usage").json()["daily"]["attempts"] == 0


def test_schedule_preserves_selected_language_through_execution(client):
    reply = client.post("/v1/schedules", json={"agent_id": "iran-news", "task": "Synthetic task",
                        "times": ["09:00"], "enabled": True, "language": "en"})
    assert reply.status_code == 201, reply.text
    assert reply.json()["language"] == "en"
    assert client.get("/v1/schedules").json()["schedules"][0]["language"] == "en"
    calls = []
    moment = datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc)
    scheduler = Scheduler(store=get_store(), clock=lambda: moment,
                          execute_fn=lambda *args, **kwargs: calls.append(kwargs) or {"report_id": "rpt_fixture"})
    assert scheduler.tick(moment) == 1
    assert calls[0]["language"] == "en"
