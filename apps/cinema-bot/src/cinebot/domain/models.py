"""Plain data types shared by adapters and services. No I/O here."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class MediaType(StrEnum):
    MOVIE = "movie"
    TV = "tv"


class Mode(StrEnum):
    TASTE = "taste"
    ENGLISH = "english"


class ConsentPurpose(StrEnum):
    # Using my anonymous data in aggregate statistics / product analytics.
    ANALYTICS = "analytics_aggregate"
    # Sharing my pseudonymized taste data with third parties (the monetizable one).
    DATA_EXPORT = "data_export"


class Person(BaseModel):
    tmdb_id: int
    name: str


class TitleRef(BaseModel):
    """Light pointer to a title, as returned by search/filmography/related lists."""

    tmdb_id: int
    media_type: MediaType
    title: str = ""
    year: int | None = None
    vote_count: int = 0

    @property
    def key(self) -> str:
        return f"{self.media_type.value}:{self.tmdb_id}"


class TitleMeta(BaseModel):
    tmdb_id: int
    media_type: MediaType
    title: str
    original_title: str = ""
    year: int | None = None
    original_language: str | None = None
    countries: list[str] = Field(default_factory=list)
    genre_ids: list[int] = Field(default_factory=list)
    overview: str = ""
    runtime: int | None = None  # minutes; episode length for TV
    vote_average: float | None = None
    vote_count: int | None = None
    directors: list[Person] = Field(default_factory=list)  # TV: creators
    cast: list[Person] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    poster_path: str | None = None
    db_id: int | None = None  # set once persisted in the shared catalog

    @property
    def key(self) -> str:
        return f"{self.media_type.value}:{self.tmdb_id}"


class RatingRow(BaseModel):
    """One line from a Kinopoisk export, before it is matched to the catalog."""

    title: str
    original_title: str | None = None
    year: int | None = None
    rating: int | None = Field(default=None, ge=1, le=10)
    rated_at: date | None = None
    media_hint: MediaType | None = None

    @property
    def external_key(self) -> str:
        return f"{self.title.strip().lower()}|{self.year or ''}"


class TasteProfile(BaseModel):
    """Per-user, derived. weights[feature] in roughly [-1.5, 1.5]."""

    weights: dict[str, float] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    n_ratings: int = 0
    version: int = 1


class EnglishFit(BaseModel):
    eligible: bool
    score: float = 0.0
    tags: list[str] = Field(default_factory=list)


class VocabItem(BaseModel):
    phrase: str
    meaning_ru: str


class Pick(BaseModel):
    """A recommendation ready to show."""

    recommendation_id: int | None = None
    meta: TitleMeta
    score: float
    reason: str
    source_note: str = ""
    english: EnglishFit | None = None
    vocab: list[VocabItem] = Field(default_factory=list)
