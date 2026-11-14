"""Content-based taste model. Pure functions, no I/O.

A title is a bag of features (genres, directors, keywords, cast, country,
decade, language, media type). A user's profile is a weight per feature learned
from their ratings: titles rated above the user's own average pull their
features up, titles rated below pull them down. Scoring a candidate sums the
weights of its features.

This is intentionally simple and explainable. Embeddings of overviews or a
learned model can replace it behind the same two functions (build_profile,
score) once there is enough data to justify it.
"""

from __future__ import annotations

import math
from collections import defaultdict

from ..domain.models import TasteProfile, TitleMeta

GENRES_RU = {
    28: "боевик", 12: "приключения", 16: "анимация", 35: "комедия", 80: "криминал",
    99: "документальное", 18: "драма", 10751: "семейное", 14: "фэнтези", 36: "история",
    27: "ужасы", 10402: "музыка", 9648: "детектив", 10749: "мелодрама", 878: "фантастика",
    10770: "ТВ-фильм", 53: "триллер", 10752: "военное", 37: "вестерн",
    10759: "боевик и приключения", 10762: "детское", 10763: "новости", 10764: "реалити",
    10765: "фантастика и фэнтези", 10766: "мыльная опера", 10767: "ток-шоу",
    10768: "война и политика",
}

# How much each feature family matters for taste.
CATEGORY_WEIGHT = {
    "director": 1.5,
    "genre": 1.0,
    "lang": 0.5,
    "keyword": 0.4,
    "decade": 0.4,
    "cast": 0.3,
    "country": 0.3,
    "media": 0.3,
}

SHRINKAGE = 2.0  # pseudo-observations: one lucky title should not define a taste


def features_for(meta: TitleMeta) -> dict[str, str]:
    """feature key -> human label."""
    f: dict[str, str] = {}
    for p in meta.directors:
        f[f"director:{p.tmdb_id}"] = p.name
    for g in meta.genre_ids:
        f[f"genre:{g}"] = GENRES_RU.get(g, f"жанр {g}")
    for k in meta.keywords:
        f[f"keyword:{k}"] = k
    for p in meta.cast:
        f[f"cast:{p.tmdb_id}"] = p.name
    for c in meta.countries:
        f[f"country:{c}"] = c
    if meta.year:
        decade = meta.year // 10 * 10
        f[f"decade:{decade}"] = f"{decade}-е"
    if meta.original_language:
        f[f"lang:{meta.original_language}"] = f"язык: {meta.original_language}"
    f[f"media:{meta.media_type.value}"] = "сериалы" if meta.media_type.value == "tv" else "фильмы"
    return f


def _category(key: str) -> str:
    return key.split(":", 1)[0]


def build_profile(items: list[tuple[TitleMeta, int]]) -> TasteProfile:
    """items: (title, rating 1..10). Unrated entries must be filtered out by the caller."""
    if not items:
        return TasteProfile()

    n = len(items)
    mean = sum(r for _, r in items) / n
    # Shrink the personal mean toward a neutral 6.5 when we have few ratings.
    center = (mean * n + 6.5 * 10) / (n + 10)

    acc: dict[str, float] = defaultdict(float)
    cnt: dict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    for meta, rating in items:
        w = max(-1.0, min(1.0, (rating - center) / 3.0))
        for key, label in features_for(meta).items():
            acc[key] += w * CATEGORY_WEIGHT.get(_category(key), 0.3)
            cnt[key] += 1
            labels[key] = label

    weights = {k: round(acc[k] / (cnt[k] + SHRINKAGE), 4) for k in acc}
    # Keep the profile small: drop features with near-zero signal.
    weights = {k: w for k, w in weights.items() if abs(w) >= 0.02}
    return TasteProfile(
        weights=weights,
        labels={k: labels[k] for k in weights},
        n_ratings=n,
    )


def score(profile: TasteProfile, meta: TitleMeta) -> float:
    keys = features_for(meta)
    if not keys:
        return 0.0
    total = sum(profile.weights.get(k, 0.0) for k in keys)
    return total / math.sqrt(len(keys))


def matching_labels(profile: TasteProfile, meta: TitleMeta, top: int = 3) -> list[str]:
    """Which of the user's liked features does this title hit? Used for explanations."""
    hits = [(profile.weights.get(k, 0.0), lbl) for k, lbl in features_for(meta).items()]
    hits = [h for h in hits if h[0] > 0.05]
    hits.sort(reverse=True)
    return [lbl for _, lbl in hits[:top]]


def describe(profile: TasteProfile, top: int = 6) -> tuple[list[str], list[str]]:
    """(likes, dislikes) as readable labels, skipping low-information families."""
    ranked = [
        (w, profile.labels.get(k, k))
        for k, w in profile.weights.items()
        if _category(k) not in ("lang", "country", "media")
    ]
    ranked.sort(reverse=True)
    likes = [lbl for w, lbl in ranked if w > 0.05][:top]
    dislikes = [lbl for w, lbl in reversed(ranked) if w < -0.05][:top]
    return likes, dislikes
