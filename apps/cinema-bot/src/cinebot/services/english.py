"""'Good for American English practice' heuristic. Pure functions, no I/O.

Hard gate: original language English AND produced in the US. Everything else
is a soft score favouring contemporary, dialogue-driven, clearly spoken
material and penalizing period/fantasy/war language and non-American accents.
The LLM reranker can refine this later; this keeps the mode useful without it.
"""

from __future__ import annotations

from ..domain.models import EnglishFit, MediaType, TitleMeta

# genre id -> adjustment (movie and TV genre ids)
_GENRE_ADJUST = {
    35: +0.15,     # comedy: everyday speech, slang
    18: +0.10,     # drama
    10749: +0.05,  # romance
    10751: +0.05,  # family: slower, clearer
    99: +0.05,     # documentary: clear narration
    80: 0.0,       # crime
    16: -0.05,     # animation
    878: -0.10,    # sci-fi: technical vocabulary
    10765: -0.10,
    14: -0.15,     # fantasy: invented/archaic speech
    36: -0.15,     # history
    10752: -0.15,  # war
    10768: -0.10,
    37: -0.20,     # western: dialect
}


def english_fit(meta: TitleMeta) -> EnglishFit:
    if meta.original_language != "en":
        return EnglishFit(eligible=False)
    if "US" not in meta.countries:
        return EnglishFit(eligible=False)

    score = 0.5
    tags: list[str] = []

    for g in meta.genre_ids:
        score += _GENRE_ADJUST.get(g, 0.0)
    if 35 in meta.genre_ids:
        tags.append("разговорная речь и юмор")
    if 18 in meta.genre_ids:
        tags.append("бытовые диалоги")

    if meta.year:
        if meta.year >= 2000:
            score += 0.10
            tags.append("современный язык")
        elif meta.year < 1980:
            score -= 0.20
            tags.append("устаревшая лексика")

    if meta.media_type is MediaType.TV:
        score += 0.10
        tags.append("много серий — одни и те же голоса")
        if meta.runtime and meta.runtime <= 30:
            score += 0.05
            tags.append("короткие серии")

    # Co-productions may carry non-American accents.
    non_us = [c for c in meta.countries if c != "US"]
    if "GB" in non_us:
        score -= 0.15
        tags.append("возможен британский акцент")
    elif any(c in non_us for c in ("AU", "IE", "NZ", "ZA", "IN")):
        score -= 0.10
        tags.append("возможны другие акценты")

    return EnglishFit(eligible=True, score=max(0.0, min(1.0, score)), tags=tags)
