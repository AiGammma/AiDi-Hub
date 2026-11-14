"""Plans, quotas and usage metering.

Every gated action goes through ``check`` before and ``record`` after. While
``CINEBOT_MONETIZATION_ENABLED=false`` everyone is treated as the unlimited
``owner`` plan, but usage is still recorded, so prices and limits can be chosen
from real behavior. Turning monetization on is a config flip plus provider
wiring (see PaymentProvider); no handler code changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import Settings
from ..db.models import Subscription, UsageEvent, User, utcnow

Period = Literal["day", "week", "month"]

# Features that can be gated. Add a name here, then call check/record in the handler.
PICK_TASTE = "pick_taste"
PICK_ENGLISH = "pick_english"
LLM_REASONS = "llm_reasons"  # personalised LLM explanations vs. template reasons


@dataclass(frozen=True)
class Limit:
    amount: int | None  # None = unlimited, 0 = not included
    period: Period = "week"


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    limits: dict[str, Limit]


UNLIMITED = Limit(None)

# Placeholder numbers: tune from usage_events once real people use the bot.
PLANS: dict[str, Plan] = {
    "free": Plan(
        "free",
        "Бесплатный",
        {
            PICK_TASTE: Limit(3, "week"),
            PICK_ENGLISH: Limit(2, "week"),
            LLM_REASONS: Limit(3, "week"),
        },
    ),
    "plus": Plan(
        "plus",
        "Plus",
        {PICK_TASTE: UNLIMITED, PICK_ENGLISH: UNLIMITED, LLM_REASONS: UNLIMITED},
    ),
    "owner": Plan(
        "owner",
        "Владелец",
        {PICK_TASTE: UNLIMITED, PICK_ENGLISH: UNLIMITED, LLM_REASONS: UNLIMITED},
    ),
}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    plan_code: str
    remaining: int | None  # None = unlimited
    period: Period | None = None


def window_start(period: Period, now: datetime) -> datetime:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return midnight
    if period == "week":
        return midnight - timedelta(days=midnight.weekday())
    return midnight.replace(day=1)


class EntitlementService:
    def __init__(self, sm: async_sessionmaker, settings: Settings) -> None:
        self._sm = sm
        self._settings = settings

    async def plan_for(self, user_id: int) -> Plan:
        if not self._settings.monetization_enabled:
            return PLANS["owner"]
        async with self._sm() as s:
            user = await s.get(User, user_id)
            if user and user.telegram_id in self._settings.owner_ids:
                return PLANS["owner"]
            sub = (
                await s.execute(
                    select(Subscription)
                    .where(Subscription.user_id == user_id, Subscription.status == "active")
                    .order_by(Subscription.started_at.desc())
                )
            ).scalars().first()
            if sub and (sub.expires_at is None or sub.expires_at > utcnow()):
                return PLANS.get(sub.plan_code, PLANS["free"])
        return PLANS["free"]

    async def check(self, user_id: int, feature: str) -> Decision:
        plan = await self.plan_for(user_id)
        limit = plan.limits.get(feature, Limit(0))
        if limit.amount is None:
            return Decision(True, plan.code, None)
        used = await self._used(user_id, feature, limit.period)
        remaining = max(0, limit.amount - used)
        return Decision(remaining > 0, plan.code, remaining, limit.period)

    async def record(self, user_id: int, feature: str, units: int = 1) -> None:
        async with self._sm() as s, s.begin():
            s.add(UsageEvent(user_id=user_id, feature=feature, units=units))

    async def usage_summary(self, user_id: int) -> dict[str, Decision]:
        return {f: await self.check(user_id, f) for f in (PICK_TASTE, PICK_ENGLISH, LLM_REASONS)}

    async def _used(self, user_id: int, feature: str, period: Period) -> int:
        since = window_start(period, utcnow())
        async with self._sm() as s:
            total = (
                await s.execute(
                    select(func.coalesce(func.sum(UsageEvent.units), 0)).where(
                        UsageEvent.user_id == user_id,
                        UsageEvent.feature == feature,
                        UsageEvent.created_at >= since,
                    )
                )
            ).scalar_one()
        return int(total)
