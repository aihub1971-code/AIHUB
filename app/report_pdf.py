"""Local, deterministic PDF presentation of the saved research-report schema.

This module does no retrieval, translation, model inference, or remote image loading.
It uses fpdf2's Unicode bidi algorithm and HarfBuzz, with original OFL Noto fonts.
The caller owns authorization, the report snapshot, and the durable destination.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
# Match report_store: 100 long multilingual articles plus source references.
MAX_REPORT_BYTES = 5_000_000
MAX_PDF_BYTES = 32_000_000
DUBAI = ZoneInfo("Asia/Dubai")

INK = (25, 42, 57)
ACCENT = (18, 94, 92)
MUTED = (85, 101, 113)
RULE = (218, 226, 228)
PALE = (242, 247, 246)

_LABELS = {
    "briefing": ("حزمة تحليلية", "Briefing pack"),
    "reading": ("دليل القراءة", "Reading pack"),
    "moodboard": ("لوحة القصة", "Story moodboard"),
    "workspace": ("تقرير المساعد", "Agent report"),
    "cutoff": ("وقت البحث", "Research cutoff"),
    "generated": ("وقت إنشاء التقرير", "Generated"),
    "report_id": ("معرّف التقرير", "Report ID"),
    "article_count": ("الأخبار الحديثة المستوفية للشروط", "Fresh qualifying news items"),
    "analyzed_count": ("المصادر التي شملها التحليل", "Sources analyzed"),
    "window": ("فترة التغطية بتوقيت دبي", "Coverage window - Dubai time"),
    "snapshot": ("تقرير مؤرخ يُحدّث عند الطلب", "Dated snapshot - refreshed on demand"),
    "news": ("ملحق الأخبار", "News appendix"),
    "sources": ("المصادر والمراجع", "Sources and references"),
    "limitations": ("حدود الأدلة", "Evidence limitations"),
    "why": ("لماذا يهم", "Why it matters"),
    "open": ("فتح المصدر الأصلي", "Open original source"),
    "published": ("النشر", "Published"),
    "unknown_date": ("وقت النشر غير مثبت", "Publication time unverified"),
    "fresh": ("خبر ضمن الفترة", "News within the window"),
    "background": ("خلفية تاريخية", "Background material"),
    "reference": ("مرجع", "Reference material"),
    "event": ("تاريخ الحدث كما ورد", "Reported event date"),
    "conflict_and_diplomacy": ("الصراع والدبلوماسية", "Conflict and diplomacy"),
    "conflict_diplomacy": ("الصراع والدبلوماسية", "Conflict and diplomacy"),
    "major_consequences": ("التداعيات الرئيسية", "Major consequences"),
    "empty": ("لا توجد تطورات موثقة خلال الساعات الاثنتي عشرة الماضية.", "No verified developments in the last 12 hours."),
    "palette": ("لوحة الألوان", "Colour palette"),
    "visual_studies": ("دراسات التكوين البصري", "Composition studies"),
    "visual_caption": (
        "تكوينات متجهية أصلية مرسومة برمجيًا بألوان التقرير. تفسير إبداعي، وليست صورًا توثيقية أو صورًا مسترجعة.",
        "Original code-drawn vector studies using the report palette. Creative interpretation, not documentary or retrieved imagery.",
    ),
    "space": ("المساحة", "Space"),
    "rhythm": ("الإيقاع", "Rhythm"),
    "contrast": ("التباين", "Contrast"),
    "table_continued": ("تابع", "continued"),
}


class ReportPDFError(RuntimeError):
    """Safe stage-local error: never include report text, credentials, or file paths."""

    def __init__(self, code: str = "pdf_render_failed") -> None:
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    value = unicodedata.normalize("NFC", str(value))
    value = value.replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", " - ")
    # Keep ordinary bidi marks for mixed-script text, but reject other control data.
    return "".join(c for c in value if c in "\n\t" or unicodedata.category(c) not in {"Cc", "Cs"}).strip()


def _plain_markdown(value: Any, *, rtl: bool = False) -> str:
    text = _text(value)
    # PDF links are created from the saved source mapping, never from arbitrary HTML.
    text = re.sub(r"!?\[([^\]]+)\]\([^\n)]*\)", r"\1", text)
    text = re.sub(r"(?:\*\*|__|`)", "", text)
    if rtl:
        # Treat complete numeric expressions as LTR units. Otherwise bidi can
        # reverse the endpoints of 80-100% or move a percentage/sign away from
        # its number. Protect existing isolates and URLs before matching tokens
        # so the presentation never nests controls or changes link identities.
        number = r"[+\-\u2212]?\d+(?:[.,\u066b\u066c]\d+)*"
        percent = r"[ \t]*[%\u066a]"
        numeric = (rf"(?<![\w./:+\-\u2212])(?:{number}(?:{percent})?[ \t]*-[ \t]*{number}(?:{percent})?"
                   rf"|{number}{percent})(?![\w/%\u066a:+\-\u2212]|[.,\u066b\u066c]\d)")
        token = (r"(?P<protected>[\u2066-\u2068][^\u2069]*\u2069|https?://[^\s<>]+)"
                 r"|(?<![\w\u2066])#[0-9a-fA-F]{6}(?!\w)"
                 r"|(?<![\w\u2066])[0-9]{4}-[0-9]{2}-[0-9]{2}"
                 r"(?:[Tt][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?"
                 r"(?:[Zz]|[+-][0-9]{2}:[0-9]{2})?)?(?![\w:])|" + numeric)
        def isolate(match: re.Match[str]) -> str:
            value = match[0]
            if match.group("protected") is not None:
                return value
            if any(unicodedata.bidirectional(char) == "AN" for char in value):
                # Arabic-Indic numbers resolve adjacent punctuation as RTL
                # even inside an LTR isolate; LRM separates the numeric runs.
                value = re.sub(number, lambda part: "\u200e" + part[0] + "\u200e", value)
            return "\u2066" + value + "\u2069"
        text = re.sub(token, isolate, text)
    return text


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 8192 or any(
        c.isspace() or unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value
    ):
        return None
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"https", "http"} or not hostname or parsed.username or parsed.password:
            return None
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return None
        except ValueError:
            if "." not in hostname:
                return None
            hostname.encode("idna")
        _ = parsed.port  # Reject malformed ports without following the URL.
    except (ValueError, UnicodeError):
        return None
    return value


def _date(value: Any, *, date_only: bool = False) -> str:
    value = _text(value)
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            # Do not attach an invented timezone or publication clock time.
            return value
        moment = moment.astimezone(DUBAI)
        return moment.strftime("%d %b %Y" if date_only else "%d %b %Y, %H:%M") + ("" if date_only else " (UTC+04)")
    except ValueError:
        return value


def _blocks(body: Any) -> list[tuple[str, Any]]:
    """Small presentation-only Markdown reader; never evaluates HTML or fetches URLs."""
    lines = _text(body).splitlines()
    blocks: list[tuple[str, Any]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(("paragraph", " ".join(paragraph)))
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            flush()
        elif line.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"[\s|:\-]+", lines[i + 1]):
            flush()
            rows = [[c.strip() for c in line.strip("|").split("|")]]
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            blocks.append(("table", rows))
            continue
        elif re.match(r"^#{1,6}\s+", line):
            flush()
            blocks.append(("heading", re.sub(r"^#{1,6}\s+", "", line)))
        elif re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", line):
            flush()
            blocks.append(("bullet", re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line)))
        elif line.startswith(">"):
            flush()
            blocks.append(("quote", line.lstrip("> ")))
        elif line.startswith("```"):
            flush()
        elif re.fullmatch(r"[-*_]{3,}", line):
            flush()
        else:
            paragraph.append(line)
        i += 1
    flush()
    return blocks


def _build_pdf(report: dict[str, Any]) -> tuple[bytes, int]:
    # Lazy import lets the worker preserve the screen report even if PDF deps fail.
    try:
        from fpdf import FPDF
        from fpdf.enums import MethodReturnValue
        from fpdf.fonts import FontFace
        import uharfbuzz  # noqa: F401 - fail early, before touching the destination
    except ImportError as exc:
        raise ReportPDFError("pdf_dependency_unavailable") from exc

    ar = report.get("language") == "ar"
    align = "R" if ar else "L"
    direction = "rtl" if ar else "ltr"
    source_list = [s for s in report.get("sources", []) if isinstance(s, dict)]
    sources = {_text(s.get("id")): s for s in source_list if _text(s.get("id"))}
    # Stable source IDs remain in the saved report; readers see compact numbers
    # matching the source appendix, including real src_<hash> identifiers.
    source_numbers = {_text(source.get("id")): index for index, source in enumerate(source_list, 1)
                      if _text(source.get("id"))}
    mappings = report.get("citation_mappings") or {}
    if not isinstance(mappings, dict):
        mappings = {}
    kind = report.get("kind", "briefing")
    if kind not in {"briefing", "reading", "moodboard", "workspace"}:
        kind = "briefing"

    def label(key: str) -> str:
        pair = _LABELS.get(key)
        return pair[0 if ar else 1] if pair else key

    class BriefPDF(FPDF):
        def __init__(self) -> None:
            super().__init__(orientation="P", unit="mm", format="A4")
            self.set_margins(20, 28, 20)
            self.set_auto_page_break(True, 22)
            self.c_margin = 0
            for family in ("NotoSans", "NotoSansArabic"):
                for style, weight in (("", "Regular"), ("B", "Bold")):
                    self.add_font(family, style, FONT_DIR / f"{family}-{weight}.ttf")
            self.body_size = 11.0 if ar else 10.4
            self.leading = 6.3 if ar else 5.25
            self.set_lang("ar-AE" if ar else "en")
            self.set_title(_text(report.get("title")) or _text(report.get("topic")) or "AI HUB")
            self.set_author("AI HUB")
            self.set_subject(_text(report.get("agent_name")))
            self.set_creator("AI HUB research workspace")
            self.set_compression(True)

        def font(self, *, size: float | None = None, bold: bool = False, latin: bool = False) -> None:
            family = "NotoSans" if latin or not ar else "NotoSansArabic"
            self.set_font(family, "B" if bold else "", size or self.body_size)
            self.set_fallback_fonts(("NotoSansArabic", "NotoSans"), exact_match=True)
            self.set_text_shaping(True, direction="ltr" if latin else direction)

        def header(self) -> None:
            self.font(size=10, bold=True, latin=True)
            self.set_text_color(*ACCENT)
            self.set_xy(20, 10)
            self.cell(50, 5, "AI HUB")
            self.font(size=8.2)
            self.set_text_color(*MUTED)
            self.set_xy(78, 10)
            self.cell(112, 5, label(kind), align="R")
            self.set_draw_color(*RULE)
            self.set_line_width(0.22)
            self.line(20, 19, 190, 19)
            self.set_y(28)

        def footer(self) -> None:
            self.set_draw_color(*RULE)
            self.set_line_width(0.2)
            self.line(20, 280, 190, 280)
            self.font(size=7.4)
            self.set_text_color(*MUTED)
            self.set_xy(20, 283)
            self.cell(154, 4.8, label("snapshot"), align=align)
            self.font(size=8.2, latin=True)
            self.set_xy(177, 283)
            self.cell(13, 4.8, str(self.page_no()), align="R")

        def ensure(self, height: float) -> None:
            if self.get_y() + min(height, 220) > self.page_break_trigger:
                self.add_page()

        def paragraph(self, content: Any, *, size: float | None = None, bold: bool = False,
                      color: tuple[int, int, int] = INK, indent: float = 0,
                      latin: bool = False, link: str | None = None, gap: float = 2.3) -> None:
            content = _plain_markdown(content, rtl=ar and not latin)
            if not content:
                return
            self.font(size=size, bold=bold, latin=latin)
            self.set_text_color(*color)
            self.set_x(self.l_margin + (indent if not ar else 0))
            width = self.epw - indent
            height = self.leading * ((size or self.body_size) / self.body_size)
            lines = self.multi_cell(width, height, content, align=align, dry_run=True,
                                    output=MethodReturnValue.LINES)
            self.ensure(min(len(lines), 2) * height)
            self.set_x(self.l_margin + (indent if not ar else 0))
            self.multi_cell(width, height, content, align=align, new_x="LMARGIN", new_y="NEXT", link=link)
            self.ln(gap)

        def text_height(self, content: Any, *, size: float | None = None, bold: bool = False) -> float:
            content = _plain_markdown(content, rtl=ar)
            if not content:
                return 0
            self.font(size=size, bold=bold)
            return float(self.multi_cell(self.epw, self.leading * ((size or self.body_size) / self.body_size),
                                         content, dry_run=True, output=MethodReturnValue.HEIGHT))

        def heading(self, title: Any, *, level: int = 1, number: int | None = None) -> None:
            title = _plain_markdown(title)
            if not title:
                return
            display_title = f"{number:02d}  {title}" if number is not None else title
            size = 14 if level == 1 else 11.5
            before = 2.5 if level == 1 else 1
            # Keep the measured heading and the following paragraph's first two
            # lines together, including the rule and existing vertical gaps.
            self.ensure(before + 3 + self.text_height(display_title, size=size, bold=True) + 2.5 + 2 * self.leading)
            self.ln(before)
            self.start_section(title, level=0 if level == 1 else 1, strict=False)
            self.set_draw_color(*ACCENT)
            self.set_line_width(0.75 if level == 1 else 0.35)
            x = 190 if ar else 20
            self.line(x, self.get_y(), x - 12 if ar else x + 12, self.get_y())
            self.ln(3)
            self.paragraph(display_title, size=size, bold=True, gap=2.5)

        def references(self, ids: Any) -> None:
            if not isinstance(ids, list):
                return
            seen: set[str] = set()
            for source_id in ids:
                source_id = _text(source_id)
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                source = sources.get(source_id) or mappings.get(source_id)
                if not isinstance(source, dict):
                    continue
                url = _safe_url(source.get("url"))
                name = _text(source.get("publisher")) or urlsplit(url).hostname if url else _text(source.get("publisher"))
                number = source_numbers.get(source_id)
                display = (f"[{number}] " if number is not None else "") + (name or _text(source.get("title")))
                self.paragraph(display, size=8.1, color=ACCENT, link=url, gap=0.7)
            if seen:
                self.ln(1)

        def table_block(self, rows: list[list[str]]) -> None:
            if not rows or not rows[0]:
                return
            columns = len(rows[0])
            if columns > 5:
                # A narrow A4 grid would be unreadable: preserve every cell in labelled rows.
                headings = rows[0]
                for row in rows[1:]:
                    for i, cell in enumerate(row):
                        self.paragraph(f"{headings[i] if i < len(headings) else i + 1}: {cell}")
                    self.ln(2)
                return
            rows = [[_plain_markdown(c, rtl=ar) for c in row] + [""] * max(0, columns - len(row)) for row in rows]
            if any(len(row) > columns for row in rows):
                # Malformed tables remain readable; no cell is silently discarded.
                for row in rows:
                    self.paragraph(" | ".join(row))
                return
            self.font(size=9.4 if ar else 9)
            self.set_text_color(*INK)
            self.set_draw_color(*RULE)
            self.set_fill_color(255, 255, 255)
            self.set_line_width(0.2)
            width = self.epw / columns - 5
            prepared = [rows[0]]
            # A provider may put a whole essay in one cell. Split its visual row into
            # safe continuations so it cannot create an unsplittable full-page cell.
            for row in rows[1:]:
                cells = [self.multi_cell(width, 5.2, c, dry_run=True, output=MethodReturnValue.LINES) or [""] for c in row]
                for offset in range(0, max(len(c) for c in cells), 20):
                    prepared.append(["\n".join(c[offset:offset + 20]) for c in cells])
            if ar:
                prepared = [list(reversed(row)) for row in prepared]
            self.ensure(24)
            with self.table(
                width=self.epw, line_height=5.3 if ar else 4.8, padding=(2.5, 2.5),
                text_align=align, v_align="TOP", borders_layout="HORIZONTAL_LINES",
                headings_style=FontFace(emphasis="BOLD", color=INK, fill_color=PALE),
                repeat_headings="ON_TOP_OF_EVERY_PAGE",
            ) as table:
                for cells in prepared:
                    row = table.row()
                    for cell in cells:
                        row.cell(cell)
            self.ln(4)

        def body(self, content: Any, *, timeline: bool = False) -> None:
            for block_kind, block in _blocks(content):
                if block_kind == "table":
                    self.table_block(block)
                elif block_kind == "heading":
                    self.heading(block, level=2)
                elif block_kind in {"bullet", "quote"} or timeline:
                    self.ensure(13)
                    x = 187.7 if ar else 22.3
                    y = self.get_y() + 2.3
                    self.set_fill_color(*ACCENT)
                    self.ellipse(x - 0.75, y - 0.75, 1.5, 1.5, style="F")
                    if timeline:
                        self.set_draw_color(*RULE)
                        self.line(x, y + 1.8, x, y + 5.8)
                    self.paragraph(block, indent=7, color=MUTED if block_kind == "quote" else INK)
                else:
                    self.paragraph(block)

        def moodboard(self, visuals: Any) -> None:
            if not isinstance(visuals, dict):
                return
            entries = visuals.get("palette") or []
            palette = [p for p in entries if isinstance(p, dict) and re.fullmatch(r"#[0-9a-fA-F]{6}", _text(p.get("hex")))]
            if not palette:
                return
            # The saved-report schema permits six colours. Keep that complete
            # palette on one row instead of stretching its sixth swatch across
            # a second page. Measure labels before placing the section heading.
            columns = min(len(palette), 6)
            w = (self.epw - 4 * (columns - 1)) / columns
            groups = []
            for offset in range(0, len(palette), columns):
                group = palette[offset:offset + columns]
                self.font(size=8.3, bold=True)
                label_height = max(float(self.multi_cell(
                    w, 4.5,
                    _text(entry.get("name")), dry_run=True, output=MethodReturnValue.HEIGHT,
                )) for entry in group)
                panel_height = 25 + max(label_height, 5) + 13
                groups.append((group, label_height, panel_height))
            self.ensure(24 + groups[0][2])
            self.heading(label("palette"))
            for group, label_height, panel_height in groups:
                self.ensure(panel_height)
                y = self.get_y()
                for i, entry in enumerate(group):
                    position = columns - 1 - i if ar else i
                    x = self.l_margin + position * (w + 4)
                    rgb = tuple(int(entry["hex"][j:j + 2], 16) for j in (1, 3, 5))
                    self.set_fill_color(*rgb)
                    self.rect(x, y, w, 22, style="F")
                    self.font(size=8.3, bold=True)
                    self.set_text_color(*INK)
                    self.set_xy(x, y + 25)
                    self.multi_cell(w, 4.5, _text(entry.get("name")), align=align)
                    self.font(size=8, latin=True)
                    self.set_text_color(*MUTED)
                    self.set_xy(x, y + 28 + max(label_height, 5))
                    self.cell(w, 4, entry["hex"].upper(), align=align)
                self.set_xy(self.l_margin, y + panel_height)
            self.ensure(83)
            self.heading(label("visual_studies"))
            self.ensure(62)
            y = self.get_y()
            colors = [tuple(int(p["hex"][j:j + 2], 16) for j in (1, 3, 5)) for p in palette]
            while len(colors) < 3:
                colors.append(colors[0])
            w = (self.epw - 8) / 3
            for i, name in enumerate(("space", "rhythm", "contrast")):
                position = 2 - i if ar else i
                x = self.l_margin + position * (w + 4)
                self.set_fill_color(*colors[0])
                self.rect(x, y, w, 37, style="F")
                self.set_fill_color(*colors[1])
                if i == 0:
                    self.ellipse(x + w * .50, y + 5, 18, 18, style="F")
                    self.set_fill_color(*colors[2])
                    self.polygon([(x, y + 37), (x + w * .62, y + 15), (x + w, y + 37)], style="F")
                elif i == 1:
                    for j in range(6):
                        self.set_fill_color(*colors[j % len(colors)])
                        self.rect(x + 4 + j * (w - 8) / 6, y + 4 + (j % 3) * 4, 4, 28 - (j % 3) * 4, style="F")
                else:
                    self.rect(x + w * .5, y, w * .5, 37, style="F")
                    self.set_fill_color(*colors[2])
                    self.ellipse(x + w * .5 - 10, y + 8.5, 20, 20, style="F")
                self.font(size=9, bold=True)
                self.set_text_color(*INK)
                self.set_xy(x, y + 40)
                self.cell(w, 5.5, label(name), align=align)
            self.set_xy(self.l_margin, y + 48)
            self.paragraph(label("visual_caption"), size=8.1, color=MUTED, gap=3)
            if visuals.get("direction"):
                self.body(visuals["direction"])
            self.references(visuals.get("reference_source_ids"))

    try:
        pdf = BriefPDF()
    except FileNotFoundError as exc:
        raise ReportPDFError("pdf_font_unavailable") from exc
    pdf.add_page()
    pdf.paragraph(_text(report.get("agent_name")) or "AI HUB", size=9.2, bold=True, color=ACCENT, gap=3)
    pdf.paragraph(report.get("title") or report.get("topic") or "AI HUB", size=23 if not ar else 21, bold=True, gap=4)
    topic = _text(report.get("topic"))
    if topic and topic != _text(report.get("title")):
        pdf.paragraph(topic, size=10.2, color=MUTED, gap=3)
    window = report.get("research_window") or {}
    cutoff = _date(window.get("cutoff") or window.get("end"))
    if cutoff:
        pdf.paragraph(label("cutoff"), size=8, bold=True, color=MUTED, gap=0)
        pdf.paragraph(cutoff, size=9, latin=True, color=MUTED, gap=2)
    if window.get("start") and window.get("end"):
        pdf.paragraph(label("window"), size=8, bold=True, color=MUTED, gap=0)
        pdf.paragraph(f"{_date(window['start'])}  -  {_date(window['end'])}", size=8.5, latin=True, color=MUTED, gap=3.5)
    generated = _date(report.get("created_at"))
    if generated:
        pdf.paragraph(label("generated"), size=8, bold=True, color=MUTED, gap=0)
        pdf.paragraph(generated, size=8.5, latin=True, color=MUTED, gap=2)
    if type(report.get("actual_article_count")) is int:
        target = report.get("requested_article_target", 100)
        article_count = f"{report['actual_article_count']} / {target}"
        if ar:
            article_count = "\u2066" + article_count + "\u2069"
        pdf.paragraph(f"{label('article_count')}: {article_count}",
                      size=9, bold=True, color=ACCENT, gap=1)
        if type(report.get("analyzed_article_count")) is int:
            pdf.paragraph(f"{label('analyzed_count')}: {report['analyzed_article_count']}",
                          size=8.5, color=MUTED, gap=2)
    report_id = _text(report.get("report_id"))
    if report_id:
        pdf.paragraph(label("report_id"), size=7.4, color=MUTED, gap=0)
        pdf.paragraph(report_id, size=7.4, latin=True, color=MUTED, gap=2)
    pdf.set_draw_color(*RULE)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(4)

    sections = [s for s in report.get("sections", []) if isinstance(s, dict)]
    if not sections:
        fallback = report.get("response") or (label("empty") if kind == "briefing" else "")
        pdf.body(fallback)
    for index, section in enumerate(sections, 1):
        pdf.heading(section.get("title") or section.get("id"), number=index)
        pdf.body(section.get("body"), timeline=kind == "reading" and section.get("id") in {"chronology", "timeline"})
        pdf.references(section.get("source_ids"))
    if kind == "moodboard":
        pdf.moodboard(report.get("visuals"))
    limitations = report.get("limitations") or []
    if limitations:
        pdf.heading(label("limitations"), level=2)
        for limitation in limitations:
            pdf.paragraph(limitation, size=8.7, color=MUTED)

    items = [i for i in report.get("items", []) if isinstance(i, dict)]
    if items:
        pdf.add_page()
        pdf.heading(label("news"))
        group = None
        for index, item in enumerate(items, 1):
            section = _text(item.get("section"))
            normalized = re.sub(r"[^a-z]+", "_", section.lower()).strip("_")
            if report.get("agent_id") == "iran-news" and section and section != group:
                pdf.heading(label(normalized) if normalized in _LABELS else section, level=2)
                group = section
            # Keep an ordinary news entry, including its links, on one page. Long
            # entries can still split safely instead of shrinking or losing text.
            item_height = 39 + pdf.text_height(item.get("headline"), size=12.2, bold=True)
            item_height += pdf.text_height(item.get("summary"))
            if item.get("why_it_matters"):
                item_height += 9 + pdf.text_height(item["why_it_matters"])
            item_height += 5 * len(item.get("source_ids") or [])
            if item.get("event_date"):
                item_height += 9
            pdf.ensure(item_height if item_height < 225 else 31)
            pdf.paragraph(f"{index:02d}  {_text(item.get('headline'))}", size=12.2, bold=True, gap=1.5)
            pdf.paragraph(item.get("publisher"), size=8.8, bold=True, color=ACCENT, gap=.5)
            publication = _date(item.get("published_at") or item.get("published_date"))
            pdf.paragraph(publication or label("unknown_date"), size=8.2, latin=bool(publication), color=MUTED, gap=2.8)
            pdf.body(item.get("summary"))
            if item.get("why_it_matters"):
                pdf.paragraph(label("why"), size=9, bold=True, gap=.4)
                pdf.body(item["why_it_matters"])
            if item.get("event_date"):
                pdf.paragraph(f"{label('event')}: {_text(item['event_date'])}", size=8.4, color=MUTED)
            url = _safe_url(item.get("url"))
            if url:
                pdf.paragraph(label("open"), size=8.8, bold=True, color=ACCENT, link=url, gap=1)
            pdf.references(item.get("source_ids"))
            pdf.set_draw_color(*RULE)
            pdf.line(20, pdf.get_y() + 1, 190, pdf.get_y() + 1)
            pdf.ln(6)

    if source_list:
        # A short report need not acquire a mostly blank extra page.
        if pdf.get_y() > 170:
            pdf.add_page()
        pdf.heading(label("sources"))
        for index, source in enumerate(source_list, 1):
            pdf.ensure(27)
            pdf.paragraph(f"[{index}] {_text(source.get('title'))}", size=10.2, bold=True, gap=1)
            meta = [_text(source.get("publisher"))]
            role = source.get("role")
            if role in {"fresh", "background", "reference"}:
                meta.append(label(role))
            pdf.paragraph(" | ".join(m for m in meta if m), size=8.2, color=MUTED, gap=.5)
            publication = _date(source.get("published_at"))
            pdf.paragraph(publication or label("unknown_date"), size=8.1, latin=bool(publication), color=MUTED, gap=.8)
            url = _safe_url(source.get("url"))
            if url:
                pdf.paragraph(url, size=7.5, latin=True, color=ACCENT, link=url, gap=3)
            else:
                pdf.ln(2)
    count = pdf.page_no()
    data = bytes(pdf.output())
    if len(data) > MAX_PDF_BYTES:
        raise ReportPDFError("pdf_output_too_large")
    return data, count


def render_report_pdf(report: dict[str, Any], output_path: Path | str) -> dict[str, Any]:
    """Write the saved report atomically and return private file metadata.

    The worker must not expose ``path`` in a public serializer. A failed call leaves
    an existing destination untouched; retry this stage with the same saved report.
    """
    if not isinstance(report, dict):
        raise ReportPDFError("pdf_invalid_report")
    try:
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ReportPDFError("pdf_invalid_report") from exc
    if len(encoded) > MAX_REPORT_BYTES:
        raise ReportPDFError("pdf_report_too_large")
    if not isinstance(report.get("sections", []), list) or not isinstance(report.get("sources", []), list) or not isinstance(report.get("items", []), list):
        raise ReportPDFError("pdf_invalid_report")
    if len(report.get("items", [])) > 100:
        raise ReportPDFError("pdf_news_limit_exceeded")
    target = Path(output_path)
    if target.suffix.lower() != ".pdf":
        raise ReportPDFError("pdf_invalid_destination")
    temporary: Path | None = None
    try:
        data, count = _build_pdf(report)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".aihub-pdf-", suffix=".tmp", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        return {"path": str(target), "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data), "page_count": count, "mime_type": "application/pdf"}
    except ReportPDFError:
        raise
    except Exception as exc:
        raise ReportPDFError() from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
