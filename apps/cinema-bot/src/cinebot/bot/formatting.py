from __future__ import annotations

from html import escape

from ..domain.models import Pick

POSTER_BASE = "https://image.tmdb.org/t/p/w500"
CAPTION_LIMIT = 1000


def poster_url(path: str | None) -> str | None:
    return f"{POSTER_BASE}{path}" if path else None


def pick_caption(p: Pick, index: int) -> str:
    m = p.meta
    head = f"<b>{index}. {escape(m.title)}</b>"
    if m.year:
        head += f" ({m.year})"
    kind = "сериал" if m.media_type.value == "tv" else "фильм"
    meta_line = kind
    if m.vote_average:
        meta_line += f" · ★ {m.vote_average:.1f}"
    if m.directors:
        label = "создатель" if m.media_type.value == "tv" else "реж."
        meta_line += f" · {label} {escape(', '.join(d.name for d in m.directors[:2]))}"
    text = f"{head}\n<i>{meta_line}</i>\n\n{escape(p.reason)}"
    if p.english and p.english.tags:
        text += f"\n\n🇺🇸 {escape(', '.join(p.english.tags[:3]))}"
    return text[:CAPTION_LIMIT]


def vocab_message(p: Pick) -> str | None:
    if not p.vocab:
        return None
    lines = [f"🗣 <b>Лексика жанра для «{escape(p.meta.title)}»</b>"]
    lines += [f"• <code>{escape(v.phrase)}</code> — {escape(v.meaning_ru)}" for v in p.vocab]
    lines.append("<i>Это типичные выражения для такого жанра и сеттинга, а не цитаты из фильма.</i>")
    return "\n".join(lines)
