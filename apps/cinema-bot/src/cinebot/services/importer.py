from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import UserTitle
from ..domain.models import RatingRow, TitleMeta
from .catalog import CatalogService
from .profile import ProfileService

log = logging.getLogger(__name__)

Progress = Callable[[int, int], Awaitable[None]]


@dataclass
class ImportReport:
    total: int = 0
    matched: int = 0
    unmatched: list[str] = field(default_factory=list)


class ImportService:
    def __init__(self, sm: async_sessionmaker, catalog: CatalogService, profiles: ProfileService) -> None:
        self._sm = sm
        self._catalog = catalog
        self._profiles = profiles

    async def import_rows(
        self,
        user_id: int,
        rows: list[RatingRow],
        kind: Literal["ratings", "wishlist"],
        progress: Progress | None = None,
        concurrency: int = 6,
    ) -> ImportReport:
        report = ImportReport(total=len(rows))
        sem = asyncio.Semaphore(concurrency)
        resolved: list[tuple[RatingRow, TitleMeta | None]] = []
        done = 0

        async def work(row: RatingRow) -> None:
            nonlocal done
            async with sem:
                try:
                    meta = await self._catalog.resolve(row)
                except Exception:  # one bad title must not sink the whole import
                    log.exception("resolve failed for %r", row.title)
                    meta = None
            resolved.append((row, meta))
            done += 1
            if progress and (done % 10 == 0 or done == len(rows)):
                await progress(done, len(rows))

        await asyncio.gather(*(work(r) for r in rows))

        async with self._sm() as s, s.begin():
            existing = {
                (u.external_key): u
                for u in (
                    await s.execute(
                        select(UserTitle).where(
                            UserTitle.user_id == user_id, UserTitle.source == "kinopoisk"
                        )
                    )
                ).scalars()
            }
            # title_id -> status for 'rated/watched' so a wishlist never overrides a rating
            seen_title_ids = {
                u.title_id
                for u in (
                    await s.execute(select(UserTitle).where(UserTitle.user_id == user_id))
                ).scalars()
                if u.status in ("rated", "watched") and u.title_id
            }
            for row, meta in resolved:
                if meta is None:
                    report.unmatched.append(f"{row.title} ({row.year})" if row.year else row.title)
                else:
                    report.matched += 1
                if kind == "ratings":
                    status = "rated" if row.rating else "watched"
                else:
                    if meta and meta.db_id in seen_title_ids:
                        continue
                    status = "wishlist"
                u = existing.get(row.external_key)
                if u is None:
                    u = UserTitle(user_id=user_id, source="kinopoisk", external_key=row.external_key)
                    s.add(u)
                    existing[row.external_key] = u
                u.title_id = meta.db_id if meta else None
                u.raw_title = row.title
                u.raw_year = row.year
                u.status = status
                u.rating = row.rating if kind == "ratings" else None
                u.rated_at = (
                    datetime(row.rated_at.year, row.rated_at.month, row.rated_at.day, tzinfo=UTC)
                    if row.rated_at
                    else None
                )

        if kind == "ratings":
            await self._profiles.rebuild(user_id)
        return report
