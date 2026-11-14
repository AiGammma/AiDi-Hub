"""Database schema.

Design rules that make monetization and taste export possible later:

* Everything personal hangs off ``users.id`` (multi-user from day one).
* The catalog (``titles``) is shared and contains no personal data.
* Personal facts live in ``user_titles`` (ratings, wishlist, feedback) and
  derived ``taste_profiles``.
* ``users.public_id`` is the only identifier that may leave the system, and
  exports re-pseudonymize it with a salt (see services/export.py).
* ``consents`` gates any use of data beyond serving the user.
* ``usage_events`` + ``subscriptions`` + ``payments`` back the entitlement layer.
* ``export_log`` is an audit trail of every dataset ever produced.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Always timezone-aware UTC, also on SQLite (which drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, default=_uuid)
    # Nulled when the user deletes their data.
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    locale: Mapped[str] = mapped_column(String(8), default="ru")
    referrer: Mapped[str | None] = mapped_column(String(64), nullable=True)  # /start <payload>
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (UniqueConstraint("user_id", "purpose"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(40))
    granted: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version: Mapped[str] = mapped_column(String(16), default="1")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Title(Base):
    """Shared catalog entry (movie or TV show). No personal data."""

    __tablename__ = "titles"
    __table_args__ = (UniqueConstraint("media_type", "tmdb_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(300))
    original_title: Mapped[str] = mapped_column(String(300), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    countries: Mapped[list] = mapped_column(JSON, default=list)
    genre_ids: Mapped[list] = mapped_column(JSON, default=list)
    overview: Mapped[str] = mapped_column(Text, default="")
    runtime: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vote_average: Mapped[float | None] = mapped_column(Float, nullable=True)
    vote_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    directors: Mapped[list] = mapped_column(JSON, default=list)
    cast: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    poster_path: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UserTitle(Base):
    """A user's relation to a title: rating, wishlist entry, dismissal, ..."""

    __tablename__ = "user_titles"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_key"),
        Index("ix_user_titles_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title_id: Mapped[int | None] = mapped_column(ForeignKey("titles.id"), nullable=True)
    # 'kinopoisk' (imported) | 'bot' (feedback given inside the bot)
    source: Mapped[str] = mapped_column(String(16))
    external_key: Mapped[str] = mapped_column(String(400))
    raw_title: Mapped[str] = mapped_column(String(300), default="")
    raw_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 'rated' | 'wishlist' | 'watched' | 'dismissed'
    status: Mapped[str] = mapped_column(String(12))
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..10
    rated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class FavoriteDirector(Base):
    __tablename__ = "favorite_directors"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    tmdb_person_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TasteProfileRow(Base):
    __tablename__ = "taste_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    n_ratings: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id"))
    mode: Mapped[str] = mapped_column(String(12))
    score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    feedback: Mapped[str | None] = mapped_column(String(12), nullable=True)  # liked|disliked|watched|saved
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---- Monetization ---------------------------------------------------------


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_code: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|canceled|expired
    provider: Mapped[str] = mapped_column(String(24), default="manual")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Payment(Base):
    """Kept for accounting even after the user deletes their data (user_id -> NULL)."""

    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)  # webhook idempotency

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(24))
    external_id: Mapped[str] = mapped_column(String(100))
    plan_code: Mapped[str] = mapped_column(String(24))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(12), default="paid")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UsageEvent(Base):
    """Recorded even while monetization is off, so pricing can be set from real data."""

    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_user_feature_time", "user_id", "feature", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    feature: Mapped[str] = mapped_column(String(32))
    units: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---- Data export audit ------------------------------------------------------


class ExportLog(Base):
    __tablename__ = "export_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requested_by: Mapped[str] = mapped_column(String(100))
    dataset: Mapped[str] = mapped_column(String(32))
    row_count: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
