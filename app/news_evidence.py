"""Bounded, exact choices copied from already associated model research notes.

These choices prevent a later model from paraphrasing a required quote or mixing
a publication date with a separately labelled update. They do not establish
publisher-text verification, article freshness, or source authorization. The
ordinary runtime validators still decide whether an item can enter a report.
"""
from __future__ import annotations

import re
from datetime import datetime


MAX_EVIDENCE_QUOTES = 3
MAX_PUBLICATION_CHOICES = 3
_MAX_NOTE_SCAN_CHARS = 12000
_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_PUBLICATION_FILLER = re.compile(
    r"[\s:：,؛-]*(?:(?:date|time|on|at|في|بتاريخ|يوم)\b[\s:：,؛-]*)*",
    re.IGNORECASE,
)
_ISO_CLOCK = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}[Tt ]\d{1,2}:\d{2}"
    r"(?::\d{2}(?:[.,]\d+)?)?"
    r"(?:[Zz]|[+-]\d{2}(?::?\d{2})?|\s+(?:UTC|GMT))?",
    re.IGNORECASE,
)
_RFC_CLOCK = re.compile(
    r"(?:(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|"
    r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?),?\s+)?"
    r"\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?"
    r"(?:\s+(?:[+-]\d{4}|UT|UTC|GMT|EST|EDT|CST|CDT|MST|MDT|PST|PDT))?",
    re.IGNORECASE,
)


def _plain_offsets(text: str) -> tuple[str, list[int], list[tuple[int, int]]]:
    """Remove only paired strong markers while retaining original positions."""
    removed: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for match in _STRONG.finditer(text):
        pairs.append(match.span())
        removed.update((match.start(), match.start() + 1, match.end() - 2, match.end() - 1))
    offsets = [index for index in range(len(text)) if index not in removed]
    return "".join(text[index] for index in offsets), offsets, pairs


def _complete_publication_value(value: str, runtime: object) -> bool:
    """Accept a complete known token, never the leading date of unknown prose."""
    normalized = re.sub(r"\s+", " ", runtime._normalize_digits(value).strip())
    if runtime._parse_date_phrase(normalized):
        return True
    for date_format in ("%Y/%m/%d", "%a %b %d %Y", "%A, %B %d, %Y"):
        try:
            datetime.strptime(normalized, date_format)
            return True
        except ValueError:
            pass
    if not (_ISO_CLOCK.fullmatch(normalized) or _RFC_CLOCK.fullmatch(normalized)):
        return False
    return runtime._published_day_in_dubai(value) is not None


def _clause_end(text: str, start: int) -> int:
    """Keep decimal clocks and dotted numeric dates within their whole clause."""
    for index in range(start, min(len(text), start + 301)):
        char = text[index]
        if char in ";؛\n!?؟":
            return index
        if char == "." and not (
            index > start and text[index - 1].isdigit()
            and index + 1 < len(text) and text[index + 1].isdigit()
        ):
            return index
    return min(len(text), start + 301)


def _publication_choices(content: str, source: dict[str, object], runtime: object) -> list[dict[str, str]]:
    plain, offsets, pairs = _plain_offsets(content)
    choices: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for cue in runtime._PUBLICATION_CUE.finditer(plain):
        filler = _PUBLICATION_FILLER.match(plain, cue.end())
        start = filler.end()
        end = _clause_end(plain, start)
        while end > start and plain[end - 1].isspace():
            end -= 1
        if not 1 <= end - start <= 100:
            continue
        value = plain[start:end]
        if not _complete_publication_value(value, runtime):
            continue
        raw_value = content[offsets[start]:offsets[end - 1] + 1]
        # A timestamp assembled across internal emphasis markers is not an
        # exact original value. Never turn its date prefix into an alternative.
        if raw_value != value:
            continue
        raw_start, raw_end = offsets[cue.start()], offsets[end - 1] + 1
        # Include both markers if the note intersects a paired strong span.
        # This keeps the original note usable by the existing provenance gate.
        for pair_start, pair_end in pairs:
            if pair_start < raw_end and pair_end > raw_start:
                raw_start, raw_end = min(raw_start, pair_start), max(raw_end, pair_end)
        note = content[raw_start:raw_end]
        if not note or len(note) > 300:
            continue
        candidate = {"published_date": value, "published_date_note": note}
        if not runtime._validated_publication(candidate, source):
            continue
        key = (value, note)
        if key not in seen:
            seen.add(key)
            choices.append({"value": value, "note": note})
        if len(choices) == MAX_PUBLICATION_CHOICES:
            break
    return choices


def source_evidence_choices(source: dict[str, object]) -> dict[str, object]:
    """Return up to three exact quotes and three explicit publication choices.

    Source titles can supply a quote; only attributed ``content`` can supply a
    publication choice. Values retain original digits, words, clocks and zones.
    The caller must still apply the report's date window and future-time check.
    The helper performs no external calls and reads no fields outside title and
    content. Passing the complete original source to publication validation
    prevents a shorter note from concealing a later clock or timezone.
    """
    from . import runtime

    if not isinstance(source, dict):
        return {"quotes": [], "publications": []}
    title = source.get("title") if isinstance(source.get("title"), str) else ""
    content = source.get("content") if isinstance(source.get("content"), str) else ""
    scanned = content[:_MAX_NOTE_SCAN_CHARS]
    quotes: list[str] = []
    seen: set[str] = set()

    def add_quote(raw: str) -> None:
        value = raw.strip()
        identity = value.rstrip(".!?؟؛;")
        if (15 <= len(value) <= 240 and len(value.split()) <= 35 and identity not in seen
                and not re.search(r"https?://", value, re.IGNORECASE)
                and runtime._quote_in_source(runtime._clean_text(value), source)):
            seen.add(identity)
            quotes.append(value)

    add_quote(title)
    url_spans = [match.span() for match in re.finditer(r"https?://\S+", scanned, re.IGNORECASE)]
    for clause in re.finditer(r"[^\n.!?؟؛;]+(?:[.!?؟؛;]+|$)", scanned):
        if len(quotes) == MAX_EVIDENCE_QUOTES:
            break
        if any(start < clause.end() and end > clause.start() for start, end in url_spans):
            continue
        text = clause.group().strip()
        if runtime._PUBLICATION_CUE.search(text):
            continue
        # Trimming presentation-only edges still returns an exact substring;
        # no words are replaced or joined across separate clauses.
        text = text.strip("*_#> ")
        add_quote(text)
    return {"quotes": quotes, "publications": _publication_choices(scanned, source, runtime)}
