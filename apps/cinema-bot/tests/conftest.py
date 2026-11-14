from __future__ import annotations

import pytest

from cinebot.app import Container, build_container
from cinebot.config import Settings
from cinebot.db.session import init_db
from cinebot.domain.models import MediaType, Person, TitleMeta, TitleRef


def _m(tmdb_id, title, year, genres, directors=(), countries=("US",), lang="en", tv=False, runtime=120, votes=5000, avg=7.8, kw=()):
    return TitleMeta(
        tmdb_id=tmdb_id,
        media_type=MediaType.TV if tv else MediaType.MOVIE,
        title=title,
        original_title=title,
        year=year,
        original_language=lang,
        countries=list(countries),
        genre_ids=list(genres),
        overview=f"{title} overview",
        runtime=runtime,
        vote_average=avg,
        vote_count=votes,
        directors=[Person(tmdb_id=i, name=n) for i, n in directors],
        keywords=list(kw),
    )


NOLAN = (525, "Christopher Nolan")
VILLENEUVE = (137427, "Denis Villeneuve")

CATALOG = [
    _m(27205, "Inception", 2010, [28, 878, 12], [NOLAN], ("US", "GB"), kw=["dream", "heist"]),
    _m(157336, "Interstellar", 2014, [12, 18, 878], [NOLAN], ("US", "GB"), kw=["space"]),
    _m(374720, "Dunkirk", 2017, [28, 18, 36, 10752], [NOLAN], ("US", "GB")),
    _m(329865, "Arrival", 2016, [18, 878, 9648], [VILLENEUVE], ("US", "CA"), kw=["alien"]),
    _m(335984, "Blade Runner 2049", 2017, [878, 18], [VILLENEUVE], ("US", "GB", "CA")),
    _m(1668, "Friends", 1994, [35], [(1, "David Crane")], tv=True, runtime=25),
    _m(2316, "The Office", 2005, [35], [(2, "Greg Daniels")], tv=True, runtime=22),
    _m(8363, "Superbad", 2007, [35], [(3, "Greg Mottola")]),
    _m(194, "Amélie", 2001, [35, 10749], [(4, "Jean-Pierre Jeunet")], ("FR",), lang="fr"),
]
BY_KEY = {m.key: m for m in CATALOG}


class FakeProvider:
    """In-memory stand-in for TMDB."""

    def __init__(self) -> None:
        self.calls = 0

    async def search_title(self, title, year, media_hint=None, original_title=None):
        for m in CATALOG:
            if m.title.lower() in (title.lower(), (original_title or "").lower()) and (not year or m.year == year):
                return TitleRef(tmdb_id=m.tmdb_id, media_type=m.media_type, title=m.title, year=m.year)
        return None

    async def get_title(self, tmdb_id, media_type):
        self.calls += 1
        return BY_KEY[f"{media_type.value}:{tmdb_id}"].model_copy()

    async def search_person(self, name):
        for p in (NOLAN, VILLENEUVE):
            if name.lower() in p[1].lower():
                return Person(tmdb_id=p[0], name=p[1])
        return None

    async def director_filmography(self, person_id):
        return [
            TitleRef(tmdb_id=m.tmdb_id, media_type=m.media_type, title=m.title, year=m.year, vote_count=m.vote_count or 0)
            for m in CATALOG
            if any(d.tmdb_id == person_id for d in m.directors)
        ]

    async def related_titles(self, tmdb_id, media_type):
        if tmdb_id == 27205:  # Inception -> Arrival, The Office
            picks = [BY_KEY["movie:329865"], BY_KEY["tv:2316"]]
            return [TitleRef(tmdb_id=m.tmdb_id, media_type=m.media_type, title=m.title, vote_count=m.vote_count or 0) for m in picks]
        return []


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        tmdb_api_key="test",
        export_salt="pepper",
        export_min_group_size=2,
        export_min_ratings=2,
        owner_telegram_ids="999",
    )


@pytest.fixture
async def container(settings) -> Container:
    c = build_container(settings, provider=FakeProvider())
    await init_db(c.engine)
    yield c
    await c.engine.dispose()


RATINGS_CSV = (
    "Название;Год;Моя оценка\n"
    "Inception;2010;10\n"
    "Interstellar;2014;9\n"
    "Dunkirk;2017;8\n"
    "Friends;1994;3\n"
    "Unknown Movie;2001;7\n"
).encode()
