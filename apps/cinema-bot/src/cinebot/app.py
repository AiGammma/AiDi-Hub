"""Composition root: the only place that knows which adapters are in use."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from .adapters.claude import ClaudeExplainer
from .adapters.tmdb import TmdbClient
from .config import Settings
from .db.session import make_engine, make_sessionmaker
from .domain.ports import Explainer, MetadataProvider, PaymentProvider
from .services.catalog import CatalogService
from .services.directors import DirectorService
from .services.entitlements import EntitlementService
from .services.export import TasteExportService
from .services.importer import ImportService
from .services.payments import NullPaymentProvider, SubscriptionService
from .services.privacy import PrivacyService
from .services.profile import ProfileService
from .services.recommender import Recommender
from .services.users import UserService


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sm: async_sessionmaker
    provider: MetadataProvider
    catalog: CatalogService
    users: UserService
    profiles: ProfileService
    directors: DirectorService
    importer: ImportService
    recommender: Recommender
    entitlements: EntitlementService
    subscriptions: SubscriptionService
    payments: PaymentProvider
    privacy: PrivacyService
    exporter: TasteExportService
    has_llm: bool

    async def aclose(self) -> None:
        close = getattr(self.provider, "aclose", None)
        if close:
            await close()
        await self.engine.dispose()


def build_container(
    settings: Settings,
    provider: MetadataProvider | None = None,
    explainer: Explainer | None = None,
    engine: AsyncEngine | None = None,
) -> Container:
    engine = engine or make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    provider = provider or TmdbClient(
        settings.tmdb_api_key, settings.tmdb_base_url, settings.tmdb_language
    )
    if explainer is None and settings.llm_available:
        explainer = ClaudeExplainer(settings.anthropic_model, settings.llm_effort)

    catalog = CatalogService(sm, provider)
    profiles = ProfileService(sm, catalog)
    directors = DirectorService(sm, provider)
    return Container(
        settings=settings,
        engine=engine,
        sm=sm,
        provider=provider,
        catalog=catalog,
        users=UserService(sm),
        profiles=profiles,
        directors=directors,
        importer=ImportService(sm, catalog, profiles),
        recommender=Recommender(sm, catalog, provider, profiles, directors, explainer),
        entitlements=EntitlementService(sm, settings),
        subscriptions=SubscriptionService(sm),
        payments=NullPaymentProvider(),
        privacy=PrivacyService(sm),
        exporter=TasteExportService(sm, settings),
        has_llm=explainer is not None,
    )
