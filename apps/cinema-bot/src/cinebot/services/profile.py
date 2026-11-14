from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import Recommendation, TasteProfileRow, Title, UserTitle, utcnow
from ..domain.models import TasteProfile, TitleMeta
from . import taste
from .catalog import CatalogService, row_to_meta

REPEAT_WINDOW = timedelta(days=45)  # do not re-recommend something shown recently


class ProfileService:
    def __init__(self, sm: async_sessionmaker, catalog: CatalogService) -> None:
        self._sm = sm
        self._catalog = catalog

    async def rated_items(self, user_id: int) -> list[tuple[TitleMeta, int]]:
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(Title, UserTitle.rating)
                    .join(UserTitle, UserTitle.title_id == Title.id)
                    .where(
                        UserTitle.user_id == user_id,
                        UserTitle.status == "rated",
                        UserTitle.rating.is_not(None),
                    )
                )
            ).all()
            return [(row_to_meta(t), r) for t, r in rows]

    async def rebuild(self, user_id: int) -> TasteProfile:
        profile = taste.build_profile(await self.rated_items(user_id))
        async with self._sm() as s, s.begin():
            row = await s.get(TasteProfileRow, user_id)
            if row is None:
                row = TasteProfileRow(user_id=user_id)
                s.add(row)
            row.version = profile.version
            row.weights = profile.weights
            row.labels = profile.labels
            row.n_ratings = profile.n_ratings
            row.updated_at = utcnow()
        return profile

    async def load(self, user_id: int) -> TasteProfile:
        async with self._sm() as s:
            row = await s.get(TasteProfileRow, user_id)
            if row is None:
                return TasteProfile()
            return TasteProfile(
                weights=dict(row.weights or {}),
                labels=dict(row.labels or {}),
                n_ratings=row.n_ratings,
                version=row.version,
            )

    async def seen_keys(self, user_id: int) -> set[str]:
        """Titles to exclude: rated/watched/dismissed, plus recently recommended."""
        async with self._sm() as s:
            seen = (
                await s.execute(
                    select(Title.media_type, Title.tmdb_id)
                    .join(UserTitle, UserTitle.title_id == Title.id)
                    .where(
                        UserTitle.user_id == user_id,
                        UserTitle.status.in_(("rated", "watched", "dismissed")),
                    )
                )
            ).all()
            recent = (
                await s.execute(
                    select(Title.media_type, Title.tmdb_id)
                    .join(Recommendation, Recommendation.title_id == Title.id)
                    .where(
                        Recommendation.user_id == user_id,
                        Recommendation.created_at >= utcnow() - REPEAT_WINDOW,
                    )
                )
            ).all()
        return {f"{m}:{i}" for m, i in (*seen, *recent)}

    async def wishlist(self, user_id: int) -> list[TitleMeta]:
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(Title)
                    .join(UserTitle, UserTitle.title_id == Title.id)
                    .where(UserTitle.user_id == user_id, UserTitle.status == "wishlist")
                )
            ).scalars().all()
            return [row_to_meta(t) for t in rows]

    async def summary_for_llm(self, user_id: int, profile: TasteProfile) -> str:
        likes, dislikes = taste.describe(profile, top=8)
        loved = sorted(await self.rated_items(user_id), key=lambda x: -x[1])[:10]
        lines = [
            f"Likes: {', '.join(likes) or 'n/a'}",
            f"Dislikes: {', '.join(dislikes) or 'n/a'}",
            "Highest rated: " + "; ".join(f"{m.title} ({m.year}) = {r}/10" for m, r in loved),
        ]
        return "\n".join(lines)
