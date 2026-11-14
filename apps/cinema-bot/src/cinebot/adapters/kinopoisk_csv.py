"""Parse Kinopoisk exports (ratings, wishlist) into RatingRow objects.

Kinopoisk has no official export API, and third-party exporters differ in
columns, delimiters and encoding. The parser is deliberately tolerant:
it sniffs the delimiter, tries utf-8 then cp1251, and matches headers by
aliases (Russian or English). Plain text lists ("Название (2010)") work too.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from ..domain.models import MediaType, RatingRow

_ALIASES = {
    "title": {"название", "название фильма", "русское название", "фильм", "title", "name", "film"},
    "original": {"оригинальное название", "оригинал", "original title", "original_title", "originaltitle"},
    "year": {"год", "год выпуска", "year"},
    "rating": {"моя оценка", "оценка", "мой рейтинг", "rating", "my rating", "vote", "score"},
    "date": {"дата оценки", "дата", "дата просмотра", "date", "rated", "rated at", "watched date"},
}

_TITLE_YEAR = re.compile(r"^(?P<t>.*?)\s*\((?P<y>(?:19|20)\d{2})\)\s*$")
_TV_MARK = re.compile(r"\s*\((?:мини-)?сериал\)\s*$", re.IGNORECASE)
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-•*])\s+")
_NOISE = re.compile(r"\s*\((?:ТВ|TV|видео|короткометражка)\)\s*$", re.IGNORECASE)


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _split_title(raw: str) -> tuple[str, int | None, MediaType | None]:
    raw = raw.strip()
    hint = None
    if _TV_MARK.search(raw):
        hint = MediaType.TV
        raw = _TV_MARK.sub("", raw)
    raw = _NOISE.sub("", raw)
    year = None
    m = _TITLE_YEAR.match(raw)
    if m:
        raw, year = m.group("t").strip(), int(m.group("y"))
    return raw, year, hint


def _parse_year(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"(?:19|20)\d{2}", value)
    return int(m.group()) if m else None


def _parse_rating(value: str | None) -> int | None:
    if not value:
        return None
    try:
        n = int(float(value.replace(",", ".").strip()))
    except ValueError:
        return None
    return n if 1 <= n <= 10 else None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _column_map(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, name in enumerate(header):
        norm = name.strip().lower().lstrip("﻿")
        for key, aliases in _ALIASES.items():
            if norm in aliases and key not in mapping:
                mapping[key] = idx
    return mapping


def _looks_tabular(text: str) -> bool:
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    return any(d in first for d in (";", "\t")) or ("," in first and _column_map(first.split(",")).get("title") is not None)


def _parse_table(text: str, want_rating: bool) -> list[RatingRow]:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []
    cols = _column_map(rows[0])
    if "title" not in cols:
        raise ValueError(
            "Не нашла колонку с названием. Ожидаю заголовок вроде «Название;Год;Моя оценка»."
        )
    out: list[RatingRow] = []
    for r in rows[1:]:
        def cell(key: str) -> str | None:
            i = cols.get(key)
            return r[i] if i is not None and i < len(r) else None

        raw_title = (cell("title") or "").strip()
        if not raw_title:
            continue
        title, year_in_title, hint = _split_title(raw_title)
        out.append(
            RatingRow(
                title=title,
                original_title=(cell("original") or "").strip() or None,
                year=_parse_year(cell("year")) or year_in_title,
                rating=_parse_rating(cell("rating")) if want_rating else None,
                rated_at=_parse_date(cell("date")),
                media_hint=hint,
            )
        )
    return out


def _parse_lines(text: str) -> list[RatingRow]:
    out: list[RatingRow] = []
    for line in text.splitlines():
        line = _LIST_MARKER.sub("", line).strip()
        if not line:
            continue
        title, year, hint = _split_title(line)
        if not year and "," in title:  # "Название, 2010"
            head, _, tail = title.rpartition(",")
            if _parse_year(tail) and len(tail.strip()) == 4:
                title, year = head.strip(), int(tail.strip())
        if title:
            out.append(RatingRow(title=title, year=year, media_hint=hint))
    return out


def parse_ratings(data: bytes) -> list[RatingRow]:
    """Rows with a rating. Unrated rows are kept as 'seen' (rating=None)."""
    return _parse_table(decode(data), want_rating=True)


def parse_wishlist(data: bytes | str) -> list[RatingRow]:
    text = decode(data) if isinstance(data, bytes) else data
    if _looks_tabular(text):
        return _parse_table(text, want_rating=False)
    return _parse_lines(text)
