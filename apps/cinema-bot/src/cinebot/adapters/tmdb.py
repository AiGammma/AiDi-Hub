"""TMDB (themoviedb.org) metadata adapter."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..domain.models import MediaType, Person, TitleMeta, TitleRef

log = logging.getLogger(__name__)


def _year(date_str: str | None) -> int | None:
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None


def pick_best_match(
    results: list[dict[str, Any]],
    title: str,
    original_title: str | None,
    year: int | None,
) -> dict[str, Any] | None:
    """Choose the TMDB search hit that best matches a Kinopoisk row.

    Pure function so the matching rules are unit-tested without the network.
    """
    wanted = {title.strip().lower()}
    if original_title:
        wanted.add(original_title.strip().lower())

    best: tuple[float, dict[str, Any]] | None = None
    for r in results:
        r_year = _year(r.get("release_date") or r.get("first_air_date"))
        names = {
            (r.get("title") or "").lower(),
            (r.get("original_title") or "").lower(),
            (r.get("name") or "").lower(),
            (r.get("original_name") or "").lower(),
        }
        score = 0.0
        if wanted & names:
            score += 2
        if year and r_year:
            diff = abs(year - r_year)
            score += 3 if diff == 0 else 1 if diff == 1 else -3
        score += min(r.get("popularity", 0) or 0, 100) / 1000  # tie-breaker
        if best is None or score > best[0]:
            best = (score, r)

    if best is None:
        return None
    score, hit = best
    # With a known year we need the year or the name to agree; otherwise accept the top hit.
    if year and score < 1:
        return None
    return hit


class TmdbClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.themoviedb.org/3",
        language: str = "ru-RU",
        http: httpx.AsyncClient | None = None,
        max_concurrency: int = 8,
    ) -> None:
        if not api_key:
            raise ValueError("TMDB api key is empty (set CINEBOT_TMDB_API_KEY)")
        self._language = language
        self._base = base_url.rstrip("/")
        headers = {"Accept": "application/json"}
        self._params: dict[str, str] = {}
        if api_key.startswith("eyJ"):  # v4 read-access token (JWT)
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            self._params["api_key"] = api_key
        self._http = http or httpx.AsyncClient(timeout=20, headers=headers)
        self._sem = asyncio.Semaphore(max_concurrency)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        query = {**self._params, "language": self._language, **params}
        url = f"{self._base}{path}"
        for attempt in range(4):
            async with self._sem:
                try:
                    resp = await self._http.get(url, params=query)
                except httpx.TransportError:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(1 + attempt)
                    continue
            if resp.status_code == 429:
                await asyncio.sleep(float(resp.headers.get("Retry-After", 2)))
                continue
            if resp.status_code >= 500 and attempt < 3:
                await asyncio.sleep(1 + attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"TMDB request kept failing: {path}")

    # ---- MetadataProvider -------------------------------------------------

    async def search_title(
        self,
        title: str,
        year: int | None,
        media_hint: MediaType | None = None,
        original_title: str | None = None,
    ) -> TitleRef | None:
        kinds = [media_hint] if media_hint else [MediaType.MOVIE, MediaType.TV]
        found: list[tuple[MediaType, dict[str, Any]]] = []
        for kind in kinds:
            for query in dict.fromkeys(q for q in (title, original_title) if q):
                year_key = "year" if kind is MediaType.MOVIE else "first_air_date_year"
                params: dict[str, Any] = {"query": query}
                if year:
                    params[year_key] = year
                data = await self._get(f"/search/{kind.value}", **params)
                hit = pick_best_match(data.get("results", []), title, original_title, year)
                if hit:
                    found.append((kind, hit))
                    break
        if not found:
            return None
        # Prefer the most popular among the kinds that matched.
        kind, hit = max(found, key=lambda kh: kh[1].get("popularity", 0) or 0)
        return self._ref(hit, kind)

    async def get_title(self, tmdb_id: int, media_type: MediaType) -> TitleMeta:
        d = await self._get(f"/{media_type.value}/{tmdb_id}", append_to_response="credits,keywords")
        credits = d.get("credits", {})
        if media_type is MediaType.MOVIE:
            directors = [
                Person(tmdb_id=c["id"], name=c["name"])
                for c in credits.get("crew", [])
                if c.get("job") == "Director"
            ]
            countries = [c["iso_3166_1"] for c in d.get("production_countries", [])]
            runtime = d.get("runtime") or None
            kw = d.get("keywords", {}).get("keywords", [])
            title, original = d.get("title", ""), d.get("original_title", "")
            year = _year(d.get("release_date"))
        else:
            directors = [Person(tmdb_id=c["id"], name=c["name"]) for c in d.get("created_by", [])]
            countries = list(d.get("origin_country", []))
            ert = d.get("episode_run_time") or []
            runtime = ert[0] if ert else None
            kw = d.get("keywords", {}).get("results", [])
            title, original = d.get("name", ""), d.get("original_name", "")
            year = _year(d.get("first_air_date"))
        return TitleMeta(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title=title,
            original_title=original,
            year=year,
            original_language=d.get("original_language"),
            countries=countries,
            genre_ids=[g["id"] for g in d.get("genres", [])],
            overview=d.get("overview", "") or "",
            runtime=runtime,
            vote_average=d.get("vote_average"),
            vote_count=d.get("vote_count"),
            directors=directors,
            cast=[Person(tmdb_id=c["id"], name=c["name"]) for c in credits.get("cast", [])[:5]],
            keywords=[k["name"].lower() for k in kw][:15],
            poster_path=d.get("poster_path"),
        )

    async def search_person(self, name: str) -> Person | None:
        data = await self._get("/search/person", query=name)
        results = data.get("results", [])
        if not results:
            return None
        directing = [r for r in results if r.get("known_for_department") == "Directing"]
        hit = (directing or results)[0]
        return Person(tmdb_id=hit["id"], name=hit["name"])

    async def director_filmography(self, person_id: int) -> list[TitleRef]:
        data = await self._get(f"/person/{person_id}/combined_credits")
        refs: dict[str, TitleRef] = {}
        for c in data.get("crew", []):
            if c.get("job") != "Director" or c.get("media_type") not in ("movie", "tv"):
                continue
            ref = self._ref(c, MediaType(c["media_type"]))
            refs[ref.key] = ref
        return list(refs.values())

    async def related_titles(self, tmdb_id: int, media_type: MediaType) -> list[TitleRef]:
        data = await self._get(f"/{media_type.value}/{tmdb_id}/recommendations")
        return [self._ref(r, media_type) for r in data.get("results", [])]

    @staticmethod
    def _ref(r: dict[str, Any], kind: MediaType) -> TitleRef:
        return TitleRef(
            tmdb_id=r["id"],
            media_type=kind,
            title=r.get("title") or r.get("name") or "",
            year=_year(r.get("release_date") or r.get("first_air_date")),
            vote_count=r.get("vote_count") or 0,
        )
