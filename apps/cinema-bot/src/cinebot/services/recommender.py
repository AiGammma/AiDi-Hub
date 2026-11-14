"""Two-stage recommender.

1. Candidate generation: wishlist, filmographies of favorite directors, and
   TMDB "recommendations" for the titles the user rated highest.
2. Scoring: taste profile + source bonuses (+ English-practice fit in english
   mode), then an optional LLM rerank that picks the final N and explains them.
   If the LLM is unavailable the rule-based order is used.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import Recommendation, UserTitle
from ..domain.models import EnglishFit, Mode, Pick, TasteProfile, TitleMeta, TitleRef
from ..domain.ports import Explainer, MetadataProvider
from . import taste
from .catalog import CatalogService
from .directors import DirectorService
from .english import english_fit
from .profile import ProfileService

log = logging.getLogger(__name__)

MAX_FETCH = 45          # cap on TMDB detail lookups per request
MIN_VOTES_DIRECTOR = 150  # skip obscure shorts/TV appearances in a filmography
LOVED_RATING = 8

BONUS = {"wishlist": 0.35, "director": 0.6, "similar": 0.15}


@dataclass
class _Cand:
    meta: TitleMeta
    note: str
    bonus: float
    english: EnglishFit | None = None
    raw: float = 0.0
    final: float = 0.0


class Recommender:
    def __init__(
        self,
        sm: async_sessionmaker,
        catalog: CatalogService,
        provider: MetadataProvider,
        profiles: ProfileService,
        directors: DirectorService,
        explainer: Explainer | None = None,
    ) -> None:
        self._sm = sm
        self._catalog = catalog
        self._provider = provider
        self._profiles = profiles
        self._directors = directors
        self._explainer = explainer

    # ---- public ---------------------------------------------------------

    async def recommend(self, user_id: int, mode: Mode, n: int = 5, use_llm: bool = True) -> list[Pick]:
        profile = await self._profiles.load(user_id)
        seen = await self._profiles.seen_keys(user_id)
        cands = await self._gather(user_id, seen)
        if not cands:
            return []

        for c in cands:
            self._score(profile, c, mode)
        cands = [c for c in cands if mode is Mode.TASTE or (c.english and c.english.eligible)]
        cands.sort(key=lambda c: c.final, reverse=True)
        if not cands:
            return []

        shortlist = cands[: max(n * 3, 12)]
        picks = await self._rerank(user_id, profile, shortlist, mode, n) if use_llm else None
        if picks is None:
            picks = [self._rule_based_pick(profile, c) for c in shortlist[:n]]
        await self._persist(user_id, mode, picks)
        return picks

    async def record_feedback(self, user_id: int, recommendation_id: int, feedback: str) -> bool:
        """liked | disliked | watched | saved. Also feeds seen/wishlist state."""
        status = {"disliked": "dismissed", "watched": "watched", "saved": "wishlist"}.get(feedback)
        async with self._sm() as s, s.begin():
            rec = await s.get(Recommendation, recommendation_id)
            if rec is None or rec.user_id != user_id:
                return False
            rec.feedback = feedback
            if status:
                key = f"tmdb-title:{rec.title_id}"
                ut = (
                    await s.execute(
                        select(UserTitle).where(
                            UserTitle.user_id == user_id,
                            UserTitle.source == "bot",
                            UserTitle.external_key == key,
                        )
                    )
                ).scalar_one_or_none()
                if ut is None:
                    ut = UserTitle(user_id=user_id, source="bot", external_key=key, title_id=rec.title_id)
                    s.add(ut)
                ut.status = status
        return True

    # ---- stage 1: candidates ---------------------------------------------

    async def _gather(self, user_id: int, seen: set[str]) -> list[_Cand]:
        cands: dict[str, _Cand] = {}

        def add(meta: TitleMeta, note: str, bonus: float) -> None:
            if meta.key in seen:
                return
            cur = cands.get(meta.key)
            if cur is None or bonus > cur.bonus:
                cands[meta.key] = _Cand(meta=meta, note=note, bonus=bonus)

        for meta in await self._profiles.wishlist(user_id):
            add(meta, "из твоего вишлиста", BONUS["wishlist"])

        refs: dict[str, tuple[TitleRef, str, float]] = {}

        def add_ref(ref: TitleRef, note: str, bonus: float) -> None:
            if ref.key in seen:
                return
            cur = refs.get(ref.key)
            if cur is None or bonus > cur[2]:
                refs[ref.key] = (ref, note, bonus)

        directors = await self._directors.list(user_id)
        film_lists = await asyncio.gather(
            *(self._provider.director_filmography(d.tmdb_id) for d in directors), return_exceptions=True
        )
        for d, films in zip(directors, film_lists, strict=True):
            if isinstance(films, Exception):
                log.warning("filmography failed for %s: %s", d.name, films)
                continue
            top = sorted(
                (f for f in films if f.vote_count >= MIN_VOTES_DIRECTOR),
                key=lambda f: -f.vote_count,
            )[:25]
            for f in top:
                add_ref(f, f"режиссёр из твоего списка: {d.name}", BONUS["director"])

        loved = sorted(
            (m for m in await self._profiles.rated_items(user_id) if m[1] >= LOVED_RATING),
            key=lambda x: -x[1],
        )[:8]
        rel_lists = await asyncio.gather(
            *(self._provider.related_titles(m.tmdb_id, m.media_type) for m, _ in loved),
            return_exceptions=True,
        )
        for (m, _), rels in zip(loved, rel_lists, strict=True):
            if isinstance(rels, Exception):
                continue
            for r in rels[:8]:
                add_ref(r, f"похоже на «{m.title}», которое ты высоко оценила", BONUS["similar"])

        ordered = sorted(refs.values(), key=lambda t: (-t[2], -t[0].vote_count))[:MAX_FETCH]
        metas = await asyncio.gather(
            *(self._catalog.get(ref.tmdb_id, ref.media_type) for ref, _, _ in ordered),
            return_exceptions=True,
        )
        for (_, note, bonus), meta in zip(ordered, metas, strict=True):
            if isinstance(meta, TitleMeta):
                add(meta, note, bonus)
        return list(cands.values())

    # ---- stage 2: scoring / rerank ---------------------------------------

    @staticmethod
    def _score(profile: TasteProfile, c: _Cand, mode: Mode) -> None:
        m = c.meta
        prior = 0.0
        if m.vote_average and (m.vote_count or 0) >= 50:
            prior = 0.25 * max(-2.0, min(2.0, m.vote_average - 6.5)) / 2
        c.raw = taste.score(profile, m) + c.bonus + prior
        t = 0.5 + 0.5 * math.tanh(c.raw / 1.5)
        if mode is Mode.ENGLISH:
            c.english = english_fit(m)
            c.final = 0.6 * t + 0.4 * c.english.score if c.english.eligible else 0.0
        else:
            c.final = t

    def _rule_based_pick(self, profile: TasteProfile, c: _Cand) -> Pick:
        why = c.note[:1].upper() + c.note[1:]
        hits = taste.matching_labels(profile, c.meta)
        if hits:
            why += f". Совпадает с твоим вкусом: {', '.join(hits)}"
        if c.english and c.english.tags:
            why += f". Для английского: {', '.join(c.english.tags[:3])}"
        return Pick(meta=c.meta, score=c.final, reason=why + ".", source_note=c.note, english=c.english)

    async def _rerank(
        self, user_id: int, profile: TasteProfile, shortlist: list[_Cand], mode: Mode, n: int
    ) -> list[Pick] | None:
        if self._explainer is None:
            return None
        summary = await self._profiles.summary_for_llm(user_id, profile)
        items = await self._explainer.rerank(
            profile, summary, [c.meta for c in shortlist], [c.note for c in shortlist], mode, n
        )
        if not items:
            return None
        picks: list[Pick] = []
        for it in items:
            c = shortlist[it.index]
            picks.append(
                Pick(
                    meta=c.meta,
                    score=c.final,
                    reason=it.reason,
                    source_note=c.note,
                    english=c.english,
                    vocab=it.vocab,
                )
            )
        return picks

    async def _persist(self, user_id: int, mode: Mode, picks: list[Pick]) -> None:
        async with self._sm() as s, s.begin():
            rows = []
            for p in picks:
                if p.meta.db_id is None:
                    continue
                row = Recommendation(
                    user_id=user_id, title_id=p.meta.db_id, mode=mode.value, score=p.score, reason=p.reason
                )
                s.add(row)
                rows.append((p, row))
            await s.flush()
            for p, row in rows:
                p.recommendation_id = row.id
