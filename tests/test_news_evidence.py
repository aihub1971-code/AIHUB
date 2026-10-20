"""Fictional, offline fixtures for exact research-note evidence choices."""
from datetime import date, datetime

import pytest

from app import news_evidence as evidence
from app import runtime


AS_OF = datetime(2026, 10, 20, 12, 0, tzinfo=runtime.DUBAI)


@pytest.fixture(autouse=True)
def no_provider_calls(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Evidence choices must be computed without provider calls.")
    monkeypatch.setattr(runtime, "_call_model", unexpected)


def source(content="", title="Synthetic Iran evidence fixture", **overrides):
    return {"source_id": "s1", "title": title, "content": content,
            "url": "https://www.aljazeera.net/news/2026/10/20/synthetic-fixture",
            "source_kind": "article", "evidence_level": "openai_research_notes",
            "publisher_text_verified": False, **overrides}


def publication_choice(value, note):
    return {"published_date": value, "published_date_note": note}


def assert_exact_choices(choices, original):
    assert set(choices) == {"quotes", "publications"}
    assert isinstance(choices["quotes"], list) and len(choices["quotes"]) <= 3
    assert isinstance(choices["publications"], list) and len(choices["publications"]) <= 3
    assert len(choices["quotes"]) == len(set(choices["quotes"]))
    for quote in choices["quotes"]:
        assert isinstance(quote, str)
        assert 15 <= len(quote) <= 240 and len(quote.split()) <= 35
        assert quote in original["title"] or quote in original["content"]
        assert runtime._quote_in_source(runtime._clean_text(quote), original)
    for publication in choices["publications"]:
        assert set(publication) == {"value", "note"}
        assert publication["value"] in original["content"]
        assert publication["note"] in original["content"]
        assert publication["value"] in publication["note"]
        assert runtime._validated_publication(
            publication_choice(publication["value"], publication["note"]), original)


def test_exact_arabic_clause_is_available_and_paraphrasing_does_not_match_source():
    exact = "وتضم متابعة للتطورات الاقتصادية والاجتماعية في إيران ضمن هذا المثال الاصطناعي"
    paraphrase = "تتضمن متابعة للتطورات الاقتصادية والاجتماعية في إيران ضمن هذا المثال الاصطناعي"
    original = source(exact, title="")
    choices = evidence.source_evidence_choices(original)
    assert exact in choices["quotes"]
    assert paraphrase not in choices["quotes"]
    assert runtime._quote_in_source(exact, original)
    assert not runtime._quote_in_source(paraphrase, original)
    assert_exact_choices(choices, original)


def test_title_can_supply_an_exact_quote_but_never_a_publication_choice():
    title = "Published: 20/10/2026 — a synthetic Iran headline"
    original = source("No publication metadata is provided in this fictional note.", title=title)
    choices = evidence.source_evidence_choices(original)
    assert title in choices["quotes"]
    assert choices["publications"] == []
    assert_exact_choices(choices, original)


def test_mixed_live_note_keeps_publication_date_separate_from_time_only_update():
    clause = "**Published:** 20/10/2026"
    original = source(clause + "؛ آخر تحديث ظاهر: 04:00 (توقيت مكة). "
                      "وتضم متابعة للتطورات في إيران ضمن مثال اصطناعي.", source_kind="live_coverage")
    choices = evidence.source_evidence_choices(original)
    publication = next(item for item in choices["publications"] if item["value"] == "20/10/2026")
    assert publication["note"] == clause
    assert "04:00" not in publication["value"] and "آخر تحديث" not in publication["note"]
    assert runtime._validated_publication(publication_choice(**publication), original) == (
        "20/10/2026", date(2026, 10, 20))
    assert_exact_choices(choices, original)


@pytest.mark.parametrize("value", [
    "2026-10-20T07:00:00Z", "2026-10-20T07:00:00+04:00",
    "2026-10-20T07:00:00.123Z", "2026-10-20 07:00:00 UTC",
    "2026-10-20 07:00:00 GMT", "Tue, 20 Oct 2026 07:00:00 GMT",
    "٢٠٢٦-١٠-٢٠T٠٧:٠٠:٠٠Z", "Tue, ٢٠ Oct ٢٠٢٦ ٠٧:٠٠:٠٠ GMT",
])
def test_supported_publication_timestamps_remain_complete_exact_values(value):
    original = source(f"Published: {value}. Synthetic attribution for a fictional Iran story.")
    choices = evidence.source_evidence_choices(original)
    assert [item["value"] for item in choices["publications"]] == [value]
    assert_exact_choices(choices, original)


@pytest.mark.parametrize("value", ["20 أكتوبر 2026", "٢٠ أكتوبر ٢٠٢٦", "٢٠/١٠/٢٠٢٦", "۲۰/۱۰/۲۰۲۶"])
def test_arabic_and_persian_date_tokens_keep_the_original_digits_and_words(value):
    original = source(f"تاريخ النشر: {value}؛ ملاحظات عربية اصطناعية عن إيران.")
    choices = evidence.source_evidence_choices(original)
    assert [item["value"] for item in choices["publications"]] == [value]
    assert_exact_choices(choices, original)


@pytest.mark.parametrize("note", [
    "**Published:** **2026-10-20T13:00:00Z**.",
    "__Published:__ __2026-10-20T13:00:00Z__.",
    "Published: 2026-10-20 13:00:00 UTC.",
    "Published: Tue, 20 Oct 2026 13:00:00 GMT.",
])
def test_future_timestamp_is_retained_in_full_for_the_existing_future_gate(note):
    original = source(note)
    choices = evidence.source_evidence_choices(original)
    assert len(choices["publications"]) == 1
    publication = choices["publications"][0]
    assert "13:00:00" in publication["value"]
    assert publication["value"].endswith(("Z", "UTC", "GMT"))
    assert runtime._publication_is_future(publication["value"], AS_OF)
    assert_exact_choices(choices, original)


@pytest.mark.parametrize("note", [
    "Published: 2026-10-20T25:99:00Z.",
    "Published: ٢٠٢٦-١٠-٢٠T٢٥:٩٩:٠٠Z.",
    "Published: 2026-10-20**T13:00:00Z.",
    "Published: **2026-10-20**T13:00:00Z.",
    "Published: 2026-10-20T13:00:00**+00:00.",
    "Published: 2026-10-20T13:00:00** UTC.",
    "Published: 2026-10-20T13:00:00** GMT.",
])
def test_malformed_or_marker_hidden_timestamps_never_become_shorter_publications(note):
    original = source(note)
    choices = evidence.source_evidence_choices(original)
    assert choices["publications"] == []
    assert_exact_choices(choices, original)


@pytest.mark.parametrize("content", [
    "Published: unknown. A fictional event occurred on 20/10/2026.",
    "تاريخ النشر: غير معروف. وقع الحدث الافتراضي في ٢٠ أكتوبر ٢٠٢٦.",
    "A fictional event occurred on 2026-10-20 with no publication date supplied.",
    "Synthetic direct URL: https://www.aljazeera.net/news/2026/10/20/example.",
    "Published: https://www.aljazeera.net/news/2026/10/20/example.",
    "Last updated: 04:00 (Mecca time). A fictional event occurred on 2026-10-20.",
])
def test_occurrence_dates_url_dates_and_unknown_metadata_are_not_publication_evidence(content):
    original = source(content, published_date="2026-10-20")
    choices = evidence.source_evidence_choices(original)
    assert choices["publications"] == []
    assert_exact_choices(choices, original)


def test_validation_always_receives_the_original_complete_source(monkeypatch):
    original = source("Prefix about a fictional report. Published: 2026-10-20T07:00:00Z. "
                      "Published: **2026-10-20** 13:00:00 UTC. "
                      "A second fictional event occurred on 2026-10-19.")
    validator = runtime._validated_publication
    observed = []

    def inspect(candidate, associated_source):
        observed.append(candidate)
        assert associated_source["content"] == original["content"]
        assert associated_source["title"] == original["title"]
        return validator(candidate, associated_source)

    monkeypatch.setattr(runtime, "_validated_publication", inspect)
    choices = evidence.source_evidence_choices(original)
    assert observed, "Publication choices must pass the existing full-source validator."
    assert [item["value"] for item in choices["publications"]] == ["2026-10-20T07:00:00Z"]


def test_choices_are_deterministic_bounded_and_copied_from_source_strings_only():
    notes = [f"Synthetic evidence clause number {index} describes a fictional Iranian project."
             for index in range(8)]
    dates = [f"Published: 2026-10-{day:02d}." for day in range(16, 21)]
    original = source(" ".join(notes + dates),
                      extra={"headline": "UNTRUSTED_EXTRA_FIELD_MARKER", "published_date": "2099-12-31"})
    choices = evidence.source_evidence_choices(original)
    assert choices == evidence.source_evidence_choices(original)
    assert 1 <= len(choices["quotes"]) <= 3
    assert 1 <= len(choices["publications"]) <= 3
    assert "UNTRUSTED_EXTRA_FIELD_MARKER" not in str(choices)
    assert "2099-12-31" not in str(choices)
    assert_exact_choices(choices, original)


def test_quote_limits_also_hold_for_long_clauses_and_repeated_titles():
    title = "Synthetic research note about an imaginary Iranian project"
    content = title + ". " + " ".join(["fictional"] * 90) + ". " + title + "."
    original = source(content, title=title)
    choices = evidence.source_evidence_choices(original)
    assert title in choices["quotes"]
    assert_exact_choices(choices, original)


def test_empty_source_strings_cannot_supply_evidence():
    assert evidence.source_evidence_choices(source("", title="")) == {"quotes": [], "publications": []}
