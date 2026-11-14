from __future__ import annotations

import asyncio
import re

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import FavoriteDirector
from ..domain.models import Person
from ..domain.ports import MetadataProvider


def split_names(text: str) -> list[str]:
    names = [n.strip() for n in re.split(r"[,;\n]+", text) if n.strip()]
    return list(dict.fromkeys(names))[:30]


class DirectorService:
    def __init__(self, sm: async_sessionmaker, provider: MetadataProvider) -> None:
        self._sm = sm
        self._provider = provider

    async def set_directors(self, user_id: int, names: list[str]) -> tuple[list[Person], list[str]]:
        found = await asyncio.gather(*(self._provider.search_person(n) for n in names), return_exceptions=True)
        resolved: list[Person] = []
        unresolved: list[str] = []
        for name, res in zip(names, found, strict=True):
            if isinstance(res, Person):
                resolved.append(res)
            else:
                unresolved.append(name)
        async with self._sm() as s, s.begin():
            await s.execute(delete(FavoriteDirector).where(FavoriteDirector.user_id == user_id))
            for p in {p.tmdb_id: p for p in resolved}.values():
                s.add(FavoriteDirector(user_id=user_id, name=p.name, tmdb_person_id=p.tmdb_id))
        return resolved, unresolved

    async def list(self, user_id: int) -> list[Person]:
        async with self._sm() as s:
            rows = (
                await s.execute(select(FavoriteDirector).where(FavoriteDirector.user_id == user_id))
            ).scalars().all()
            return [Person(tmdb_id=r.tmdb_person_id, name=r.name) for r in rows if r.tmdb_person_id]
