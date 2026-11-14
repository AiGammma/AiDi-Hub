"""Shared title catalog: DB cache in front of the metadata provider."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import Title, utcnow
from ..domain.models import MediaType, Person, RatingRow, TitleMeta
from ..domain.ports import MetadataProvider


def row_to_meta(t: Title) -> TitleMeta:
    return TitleMeta(
        db_id=t.id,
        tmdb_id=t.tmdb_id,
        media_type=MediaType(t.media_type),
        title=t.title,
        original_title=t.original_title,
        year=t.year,
        original_language=t.original_language,
        countries=list(t.countries or []),
        genre_ids=list(t.genre_ids or []),
        overview=t.overview or "",
        runtime=t.runtime,
        vote_average=t.vote_average,
        vote_count=t.vote_count,
        directors=[Person(**p) for p in t.directors or []],
        cast=[Person(**p) for p in t.cast or []],
        keywords=list(t.keywords or []),
        poster_path=t.poster_path,
    )


class CatalogService:
    def __init__(self, sm: async_sessionmaker, provider: MetadataProvider, ttl_days: int = 60) -> None:
        self._sm = sm
        self._provider = provider
        self._ttl = timedelta(days=ttl_days)
        self._write_lock = asyncio.Lock()  # keeps SQLite happy under concurrent imports

    async def get(self, tmdb_id: int, media_type: MediaType) -> TitleMeta:
        async with self._sm() as s:
            row = (
                await s.execute(
                    select(Title).where(Title.tmdb_id == tmdb_id, Title.media_type == media_type.value)
                )
            ).scalar_one_or_none()
            if row and utcnow() - row.fetched_at < self._ttl:
                return row_to_meta(row)
        meta = await self._provider.get_title(tmdb_id, media_type)
        return await self._store(meta)

    async def get_many_by_db_id(self, ids: list[int]) -> dict[int, TitleMeta]:
        if not ids:
            return {}
        async with self._sm() as s:
            rows = (await s.execute(select(Title).where(Title.id.in_(ids)))).scalars().all()
            return {r.id: row_to_meta(r) for r in rows}

    async def resolve(self, row: RatingRow) -> TitleMeta | None:
        """Match a Kinopoisk row to a catalog title (None if TMDB has no match)."""
        ref = await self._provider.search_title(
            row.title, row.year, row.media_hint, row.original_title
        )
        if ref is None:
            return None
        return await self.get(ref.tmdb_id, ref.media_type)

    async def _store(self, meta: TitleMeta) -> TitleMeta:
        async with self._write_lock, self._sm() as s, s.begin():
            row = (
                await s.execute(
                    select(Title).where(
                        Title.tmdb_id == meta.tmdb_id, Title.media_type == meta.media_type.value
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = Title(tmdb_id=meta.tmdb_id, media_type=meta.media_type.value)
                s.add(row)
            row.title = meta.title
            row.original_title = meta.original_title
            row.year = meta.year
            row.original_language = meta.original_language
            row.countries = meta.countries
            row.genre_ids = meta.genre_ids
            row.overview = meta.overview
            row.runtime = meta.runtime
            row.vote_average = meta.vote_average
            row.vote_count = meta.vote_count
            row.directors = [p.model_dump() for p in meta.directors]
            row.cast = [p.model_dump() for p in meta.cast]
            row.keywords = meta.keywords
            row.poster_path = meta.poster_path
            row.fetched_at = utcnow()
            await s.flush()
            return row_to_meta(row)
