"""Offline contract tests for actual PDF content, embedded fonts and atomic retries."""

import hashlib
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader

from app import report_pdf
from pdf_fixtures import report_fixture


def _uris(reader):
    return [a.get_object()["/A"]["/URI"] for p in reader.pages for a in p.get("/Annots", [])
            if a.get_object().get("/A", {}).get("/S") == "/URI"]


def hundred_article_fixture(language):
    """A full saved inventory with distinct article and source sentinels."""
    ar = language == "ar"
    report = report_fixture(language)
    report.update(report_id="rpt_" + "8" * 32, agent_id="iran-news", agent_name="أخبار إيران - اختبار اصطناعي" if ar else "Iran news - synthetic QA",
        title="تقرير اصطناعي لاختبار مئة خبر" if ar else "Synthetic report with one hundred articles",
        created_at="2026-10-20T12:07:45Z", requested_article_target=100,
        actual_article_count=100, analyzed_article_count=25, items=[], sources=[], citation_mappings={})
    for index in range(1, 101):
        source_id = "src_" + hashlib.sha256(str(index).encode()).hexdigest()[:16]
        url = f"https://publisher.example/news/article-{index:03d}"
        published = (datetime(2026, 10, 20, tzinfo=timezone.utc) + timedelta(minutes=index)).isoformat()
        publisher = f"ناشر اصطناعي {index:03d} / Synthetic publisher" if ar else f"Synthetic publisher {index:03d}"
        source = {"id": source_id, "title": f"SOURCE{index:03d} " + ("مرجع اصطناعي موثق لأغراض الاختبار" if ar else "Synthetic reference for inventory validation"),
                  "publisher": publisher, "url": url, "published_at": published, "role": "fresh"}
        report["sources"].append(source)
        report["citation_mappings"][source_id] = {key: source[key] for key in ("url", "title", "published_at")}
        report["items"].append({"id": f"article-{index:03d}", "headline": f"ARTICLE{index:03d} " +
            ("عنوان عربي تجريبي عن التعاون والدبلوماسية الإقليمية" if ar else "Synthetic regional cooperation and diplomacy headline"),
            "publisher": publisher, "url": url, "published_at": published,
            "summary": ("هذه مادة اصطناعية لاختبار القراءة فقط. يعرض الخبر تاريخ النشر والمصدر والرابط، ويميز بين وصف الواقعة والتحليل. لا يمثل هذا النص خبرًا حقيقيًا أو نتيجة بحث مباشر."
                        if ar else "This synthetic item tests readable publication times, source attribution and links. It distinguishes a reported event from analysis and makes no claim about a real development."),
            "why_it_matters": "مثال تحليلي مشروط لاختبار ظهور الأثر على الإمارات UAE." if ar else "A conditional assessment used to test the presentation of UAE implications.",
            "source_ids": [source_id], "section": "conflict_diplomacy" if index <= 60 else "major_consequences"})
    report["sections"] = [{"id": "executive_summary", "title": "الملخص التنفيذي" if ar else "Executive summary",
        "body": ("يختبر هذا التقرير حفظ مئة خبر ومئة رابط مصدر دون حذف. جرى تحليل خمسة وعشرين خبرًا في هذا المثال الاصطناعي، وتبقى بقية الأخبار متاحة في الملحق."
                 if ar else "This synthetic report checks that all one hundred articles and their source links survive export. Twenty-five articles contributed to this example's analysis; the complete inventory remains in the appendix."),
        "source_ids": [source["id"] for source in report["sources"][:3]]}]
    return report


def provider_delay_fixture(language):
    ar = language == "ar"
    report = report_fixture(language)
    message = ("لم يكتمل البحث في هذا المثال بسبب تأخر مزود الخدمة. لم تُسترجع أخبار لهذه المحاولة، ولا يعني ذلك عدم وقوع أحداث. يبقى التقرير السابق محفوظًا؛ هذه بيانات اختبار اصطناعية فقط."
               if ar else "The search in this synthetic example did not finish because the provider was delayed. No articles were retrieved for this attempt. This does not establish that no events occurred; the previous saved report remains available.")
    report.update(report_id="rpt_" + "0" * 32, title="اختبار اصطناعي لتأخر مزود الخدمة" if ar else "Synthetic provider-delay example",
        created_at="2026-10-20T12:07:45Z", requested_article_target=100,
        actual_article_count=0, analyzed_article_count=0, items=[], sources=[], citation_mappings={},
        sections=[{"id": "status", "title": "حالة المحاولة" if ar else "Attempt status", "body": message, "source_ids": []}])
    return report


@pytest.mark.parametrize("language", ["en", "ar"])
def test_exactly_one_hundred_articles_and_sources_keep_links_counts_and_generation_time(tmp_path, language):
    report = hundred_article_fixture(language)
    original = json.dumps(report, ensure_ascii=False, sort_keys=True)
    path = tmp_path / f"hundred-{language}.pdf"
    metadata = report_pdf.render_report_pdf(report, path)
    reader = PdfReader(path)
    text = "\n".join(page.extract_text() for page in reader.pages)
    first_page = reader.pages[0].extract_text()
    links = _uris(reader)
    assert len(report["items"]) == len(report["sources"]) == 100
    assert all(text.count(f"ARTICLE{index:03d}") == 1 for index in range(1, 101))
    assert all(text.count(f"SOURCE{index:03d}") == 1 for index in range(1, 101))
    assert all(links.count(source["url"]) >= 3 for source in report["sources"])
    assert len(set(links)) == 100
    assert "100 / 100" in first_page and "25" in first_page
    assert "20 Oct 2026, 16:07" in first_page and "20 Oct 2026, 16:00" in first_page
    assert report["report_id"] in first_page
    assert "conflict_diplomacy" not in text
    if language == "en":
        assert "Articles retrieved: 100 / 100" in first_page
        assert "Articles included in analysis: 25" in first_page and "Generated" in first_page
        assert "Conflict and diplomacy" in text and "Major consequences" in text
    assert reader.metadata.title == report["title"]
    assert len(reader.pages) == metadata["page_count"]
    assert json.dumps(report, ensure_ascii=False, sort_keys=True) == original


@pytest.mark.parametrize("language", ["en", "ar"])
def test_empty_provider_delay_pdf_preserves_status_and_zero_counts_without_links(tmp_path, language):
    report = provider_delay_fixture(language)
    path = tmp_path / f"provider-delay-{language}.pdf"
    metadata = report_pdf.render_report_pdf(report, path)
    reader = PdfReader(path)
    text = reader.pages[0].extract_text()
    assert metadata["page_count"] == len(reader.pages) == 1
    assert "0 / 100" in text and "20 Oct 2026, 16:07" in text
    assert report["report_id"] in text
    assert not _uris(reader)
    if language == "en":
        assert "provider was delayed" in text
        assert "No verified developments in the last 12 hours." not in text


@pytest.mark.parametrize("language,kind", [("en", "briefing"), ("ar", "briefing"), ("en", "reading"), ("ar", "moodboard")])
def test_real_pdf_embeds_fonts_preserves_report_and_clickable_sources(tmp_path, language, kind):
    report = report_fixture(language, kind)
    # The renderer must use compact display numbering with production-shaped
    # source IDs, not just the short S1 labels used in the basic fixtures.
    renames = {source["id"]: "src_" + hashlib.sha256(source["url"].encode()).hexdigest()[:16]
               for source in report["sources"]}
    for source in report["sources"]:
        source["id"] = renames[source["id"]]
    for block in report["sections"] + report["items"]:
        block["source_ids"] = [renames.get(sid, sid) for sid in block.get("source_ids", [])]
    report["citation_mappings"] = {renames.get(sid, sid): value for sid, value in report["citation_mappings"].items()}
    if report.get("visuals"):
        report["visuals"]["reference_source_ids"] = [renames.get(sid, sid) for sid in report["visuals"].get("reference_source_ids", [])]
    original = json.dumps(report, ensure_ascii=False, sort_keys=True)
    path = tmp_path / "durable" / "report.pdf"
    meta = report_pdf.render_report_pdf(report, path)
    assert json.dumps(report, ensure_ascii=False, sort_keys=True) == original
    assert meta["page_count"] >= 2
    assert meta["size_bytes"] == path.stat().st_size
    assert meta["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert meta["mime_type"] == "application/pdf"
    reader = PdfReader(path)
    assert len(reader.pages) == meta["page_count"]
    assert reader.metadata.title == report["title"]
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert len(text) > 200
    assert "AI HUB" in text
    assert "2026" in text
    assert "src_" not in text
    if language == "en":
        assert report["sections"][0]["title"] in text
        assert report["sources"][0]["title"] in text
        assert "[1]" in text and "[2]" in text
    else:
        assert any("\u0600" <= c <= "\u06ff" for c in text)
    assert set(s["url"] for s in report["sources"]).issubset(set(_uris(reader)))
    font_count = 0
    for page in reader.pages:
        for font_ref in page["/Resources"]["/Font"].values():
            font = font_ref.get_object()
            assert "/ToUnicode" in font
            for descendant in font.get("/DescendantFonts", []):
                descriptor = descendant.get_object()["/FontDescriptor"].get_object()
                assert "/FontFile2" in descriptor or "/FontFile3" in descriptor
                font_count += 1
    assert font_count >= 2
    assert not list(path.parent.glob(".aihub-pdf-*"))


@pytest.mark.parametrize("language", ["ar", "en"])
def test_long_table_rows_and_source_urls_are_not_lost(tmp_path, language):
    report = report_fixture(language, long=True)
    target = tmp_path / f"long-{language}.pdf"
    result = report_pdf.render_report_pdf(report, target)
    reader = PdfReader(target)
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "FINAL SENTINEL" in text
    assert report["sources"][0]["url"] in _uris(reader)
    assert result["page_count"] > 2
    assert all(len(p.extract_text().strip()) > 35 for p in reader.pages)


@pytest.mark.parametrize("language", ["ar", "en"])
def test_six_colour_palette_stays_on_one_equal_width_row(tmp_path, language):
    report = report_fixture(language, "moodboard")
    report["visuals"]["palette"].extend([
        {"name": "أخضر الزجاج المبتل" if language == "ar" else "Rain on green glass", "hex": "#719D98"},
        {"name": "كهرمان الذاكرة" if language == "ar" else "Memory in amber", "hex": "#C98B57"},
    ])
    path = tmp_path / "six-colours.pdf"
    report_pdf.render_report_pdf(report, path)
    reader = PdfReader(path)
    swatches = []
    for page_index, page in enumerate(reader.pages):
        for args, operator in page.get_contents().operations:
            if operator == b"re" and abs(abs(float(args[3])) - 22 * 72 / 25.4) < .02:
                swatches.append((page_index, tuple(map(float, args))))
    assert len(swatches) == 6
    assert len({index for index, _ in swatches}) == 1
    assert len({rectangle[1] for _, rectangle in swatches}) == 1
    assert len({rectangle[2] for _, rectangle in swatches}) == 1
    # Compare actual PDF geometry, including the printable bounds, rather than
    # relying on a layout helper that could share the renderer's mistake.
    assert all(56.68 <= x and x + width <= 538.60 and y + height >= 62.35
               for _, (x, y, width, height) in swatches)
    page_text = reader.pages[swatches[0][0]].extract_text()
    assert all(colour["hex"] in page_text for colour in report["visuals"]["palette"])


def test_arabic_paragraph_and_table_preserve_left_to_right_colour_codes(tmp_path):
    report = report_fixture("ar")
    codes = ["#E8E5DA", "#BDA98A", "#C98B57", "#173F4A", "#283039"]
    report.update(items=[], sources=[], citation_mappings={}, limitations=[], sections=[{
        "id": "palette", "title": "الألوان", "source_ids": [],
        "body": "ألوان مرجعية: " + "، ".join(codes)
                + "\n\n| اللون | الرمز |\n|---|---|\n"
                + "\n".join("| لون | " + code + " |" for code in codes),
    }])
    original = json.dumps(report, ensure_ascii=False, sort_keys=True)
    path = tmp_path / "arabic-colour-codes.pdf"
    report_pdf.render_report_pdf(report, path)
    assert json.dumps(report, ensure_ascii=False, sort_keys=True) == original
    # Extract the selectable text. Invisible bidi controls are presentation
    # metadata; every visible code must still have its leading hash in both uses.
    text = "".join(char for page in PdfReader(path).pages for char in page.extract_text()
                   if not char.isspace() and unicodedata.category(char) != "Cf")
    assert all(text.count(code) == 2 for code in codes)


def test_no_evidence_stays_concise_and_does_not_invent_sources(tmp_path):
    report = report_fixture()
    report.update(items=[], sources=[], sections=[], citation_mappings={}, limitations=[])
    path = tmp_path / "empty.pdf"
    result = report_pdf.render_report_pdf(report, path)
    reader = PdfReader(path)
    assert result["page_count"] == 1
    assert "No verified developments in the last 12 hours." in reader.pages[0].extract_text()
    assert _uris(reader) == []


def test_unsafe_links_are_plain_content_not_clickable_actions(tmp_path):
    report = report_fixture()
    for i, url in enumerate(["javascript:alert(1)", "file:///etc/passwd", "https://127.0.0.1/private"]):
        report["sources"][i]["url"] = url
        report["items"][i]["url"] = url
        report["citation_mappings"][f"S{i + 1}"]["url"] = url
    path = tmp_path / "safe.pdf"
    report_pdf.render_report_pdf(report, path)
    assert _uris(PdfReader(path)) == []


def test_failed_pdf_stage_preserves_existing_file_then_retries_saved_data(tmp_path, monkeypatch):
    report = report_fixture()
    path = tmp_path / "snapshot.pdf"
    path.write_bytes(b"previous-version")
    original = report_pdf._build_pdf

    def fail(_):
        raise OSError("private-path-or-content-must-not-be-public")

    monkeypatch.setattr(report_pdf, "_build_pdf", fail)
    with pytest.raises(report_pdf.ReportPDFError, match="^pdf_render_failed$"):
        report_pdf.render_report_pdf(report, path)
    assert path.read_bytes() == b"previous-version"
    monkeypatch.setattr(report_pdf, "_build_pdf", original)
    report_pdf.render_report_pdf(report, path)
    assert path.read_bytes().startswith(b"%PDF-")


def test_rejects_extra_news_and_oversized_payload_instead_of_silent_truncation(tmp_path):
    report = report_fixture()
    report["items"] = report["items"] * 34
    with pytest.raises(report_pdf.ReportPDFError, match="pdf_news_limit_exceeded"):
        report_pdf.render_report_pdf(report, tmp_path / "bad.pdf")
    report = {"title": "x" * (report_pdf.MAX_REPORT_BYTES + 1)}
    with pytest.raises(report_pdf.ReportPDFError, match="pdf_report_too_large"):
        report_pdf.render_report_pdf(report, tmp_path / "big.pdf")
    assert list(tmp_path.iterdir()) == []


def test_bundled_fonts_are_unmodified_and_licensed():
    manifest = json.loads((report_pdf.FONT_DIR / "SOURCES.json").read_text())
    assert manifest["license"] == "SIL Open Font License 1.1"
    for entry in manifest["files"]:
        raw = (report_pdf.FONT_DIR / entry["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
    assert "SIL OPEN FONT LICENSE" in (report_pdf.FONT_DIR / "OFL.txt").read_text()
