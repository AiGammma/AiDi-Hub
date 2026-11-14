"""Ports: what the services need from the outside world.

Swap any adapter (TMDB -> another catalog, Claude -> another LLM, no payments ->
Telegram Stars / YooKassa) without touching service code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .models import MediaType, Mode, Person, TasteProfile, TitleMeta, TitleRef, VocabItem


class MetadataProvider(Protocol):
    async def search_title(
        self,
        title: str,
        year: int | None,
        media_hint: MediaType | None = None,
        original_title: str | None = None,
    ) -> TitleRef | None: ...

    async def get_title(self, tmdb_id: int, media_type: MediaType) -> TitleMeta: ...

    async def search_person(self, name: str) -> Person | None: ...

    async def director_filmography(self, person_id: int) -> list[TitleRef]: ...

    async def related_titles(self, tmdb_id: int, media_type: MediaType) -> list[TitleRef]: ...


@dataclass
class RerankItem:
    index: int
    reason: str
    vocab: list[VocabItem]


class Explainer(Protocol):
    """LLM-backed reranker. Returns None when it cannot help (caller falls back)."""

    async def rerank(
        self,
        profile: TasteProfile,
        profile_summary: str,
        candidates: list[TitleMeta],
        notes: list[str],
        mode: Mode,
        n: int,
    ) -> list[RerankItem] | None: ...


@dataclass
class CheckoutLink:
    url: str
    provider: str
    external_id: str


@dataclass
class PaymentEvent:
    provider: str
    external_id: str  # idempotency key from the provider
    user_id: int
    plan_code: str
    amount_minor: int
    currency: str
    days: int


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(self, user_id: int, plan_code: str) -> CheckoutLink: ...

    async def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent | None: ...
