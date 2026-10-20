"""Regression coverage for evidence-copy failures using synthetic article notes."""
from copy import deepcopy
from datetime import datetime
import json

from app import runtime
from app.news_evidence import source_evidence_choices


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=runtime.DUBAI)
WINDOW = (NOW.replace(hour=0, minute=0), NOW.replace(hour=23, minute=59))


def source_fixture(index=1, publication="20/10/2026"):
    title = f"برنامج ثقافي افتراضي جديد في طهران ضمن الاختبار {index}"
    facts = "وتضم متابعة للأنشطة التعليمية والبرامج الثقافية الافتراضية في المدينة."
    return {
        "source_id": f"s{index}", "title": title,
        "url": f"https://www.ft.com/content/ai-hub-evidence-fixture-{index}",
        "content": (f"**Title:** {title}. **Published:** {publication}؛ "
                    f"آخر تحديث ظاهر: 04:00 (توقيت مكة). {facts}"),
    }


def candidate(source, quote, publication):
    return {
        "headline_ar": "تقرير افتراضي عن الأنشطة الثقافية في طهران",
        "summary_ar": "هذه مادة اختبار اصطناعية منسوبة إلى ملاحظات المصدر عن برنامج ثقافي افتراضي.",
        "source_id": source["source_id"], "research_note_quote": quote,
        "published_date": publication["value"], "published_date_note": publication["note"],
        "event_date": None, "event_date_note": None,
    }


def test_strict_schema_prevents_paraphrased_evidence_and_mixed_date_field():
    source = source_fixture()
    payload = runtime._build_payload("gpt-6.1-sol", "Synthetic test instructions", "أخبار اليوم",
                                     [source], "used", True)
    fields = payload["text"]["format"]["schema"]["properties"]["items"]["items"]["properties"]
    assert payload["text"]["format"]["strict"] is True
    assert "تتضمن متابعة للأنشطة التعليمية والبرامج الثقافية الافتراضية في المدينة." not in fields["research_note_quote"]["enum"]
    assert "20/10/2026" in fields["published_date"]["enum"]
    assert "20/10/2026؛ آخر تحديث ظاهر: 04:00 (توقيت مكة)" not in fields["published_date"]["enum"]
    choices = source_evidence_choices(source)
    accepted = runtime._validate_news(json.dumps({
        "items": [candidate(source, choices["quotes"][0], choices["publications"][0])],
        "background_items": [],
    }, ensure_ascii=False), [source], WINDOW, NOW)
    assert accepted["item_count"] == 1
    assert accepted["items"][0]["published_day_dubai"] == "2026-10-20"
    assert accepted["items"][0]["event_date"] is None
    assert accepted["items"][0]["publisher_text_verified"] is False


def test_evidence_enum_does_not_mutate_shared_schema_or_authorize_another_sources_quote():
    before = deepcopy(runtime.NEWS_REPORT_SCHEMA)
    first, second = source_fixture(1), source_fixture(2)
    second["title"] = "خبر اختباري مختلف عن معرض تعليمي مستقل في طهران"
    schema = runtime._news_evidence_schema("en", [first, second])
    assert "headline_en" in schema["properties"]["items"]["items"]["properties"]
    assert runtime.NEWS_REPORT_SCHEMA == before
    choices = source_evidence_choices(first)
    invalid = candidate(first, second["title"], choices["publications"][0])
    report = runtime._validate_news(json.dumps({"items": [invalid], "background_items": []}),
                                    [first, second], WINDOW, NOW)
    assert report["item_count"] == 0
    assert report["discarded_item_reasons"] == {"unsupported_note_quote": 1}


def test_completed_native_open_page_can_supply_explicit_title_without_source_array():
    source = source_fixture()
    text = f"Title: [{source['title']}]({source['url']}). Published: 2026-10-20. Synthetic Tehran news."
    response = {"parts": [{"text": text, "annotations": []}], "native_calls": [
        {"type": "web_search_call", "status": "completed",
         "action": {"type": "open_page", "url": source["url"]}},
    ]}
    search = runtime._native_research_sources(response, "iran-news")
    assert len(search["results"]) == 1
    assert search["results"][0]["title"] == source["title"]
    assert search["search_call_count"] == 0 and search["tool_call_count"] == 1


def test_enriched_source_context_and_evidence_schema_fit_reserved_payload_ceiling():
    sources = [source_fixture(index) for index in range(1, runtime.MAX_NEWS_ITEMS + 1)]
    for source in sources:
        source["content"] += " " + ("Synthetic source-specific cultural evidence. " * 45)
    payload = runtime._build_payload("gpt-6.1-sol", "Synthetic instructions", "أخبار اليوم",
                                     sources, "used", True)
    assert runtime._json_bytes(payload) <= runtime.MAX_MODEL_INPUT_BYTES
    text = payload["input"][1]["content"][0]["text"]
    context = json.loads(text.split("<UNTRUSTED_SOURCE_DATA>\n")[1].split("\n</UNTRUSTED_SOURCE_DATA>")[0])
    assert runtime._json_bytes(context) <= runtime.MAX_SEARCH_CONTEXT_BYTES
    schema = payload["text"]["format"]["schema"]
    fields = schema["properties"]["items"]["items"]["properties"]
    assert fields["source_id"]["enum"] == [source["source_id"] for source in context]
    enums = [value["enum"] for section in schema["properties"].values()
             for value in section["items"]["properties"].values() if "enum" in value]
    assert sum(len(values) for values in enums) < 1000
    assert sum(len(value) for values in enums for value in values if isinstance(value, str)) < 120000
