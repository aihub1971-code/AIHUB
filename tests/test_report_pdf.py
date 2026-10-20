"""Offline contract tests for actual PDF content, embedded fonts and atomic retries."""

import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from app import report_pdf
from pdf_fixtures import report_fixture


def _uris(reader):
    return [a.get_object()["/A"]["/URI"] for p in reader.pages for a in p.get("/Annots", [])
            if a.get_object().get("/A", {}).get("/S") == "/URI"]


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
    report["items"] = report["items"] * 7
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
