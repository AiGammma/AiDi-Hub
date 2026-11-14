"""Payment plumbing: provider port stub + idempotent subscription activation.

Nothing here is wired to the bot yet. To go live, implement a PaymentProvider
(Telegram Stars is the lowest-friction option for digital goods inside Telegram;
YooKassa / Stripe work through webhooks), call ``activate`` from its webhook or
``successful_payment`` handler, set CINEBOT_MONETIZATION_ENABLED=true.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import Payment, Subscription, utcnow
from ..domain.ports import CheckoutLink, PaymentEvent


class PaymentsDisabled(RuntimeError):
    pass


class NullPaymentProvider:
    name = "none"

    async def create_checkout(self, user_id: int, plan_code: str) -> CheckoutLink:
        raise PaymentsDisabled("Платежи пока не подключены")

    async def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent | None:
        return None


class SubscriptionService:
    def __init__(self, sm: async_sessionmaker) -> None:
        self._sm = sm

    async def activate(self, event: PaymentEvent) -> bool:
        """Record a payment and extend/start a subscription. Idempotent on
        (provider, external_id): returns False if this event was already applied."""
        try:
            async with self._sm() as s, s.begin():
                s.add(
                    Payment(
                        user_id=event.user_id,
                        provider=event.provider,
                        external_id=event.external_id,
                        plan_code=event.plan_code,
                        amount_minor=event.amount_minor,
                        currency=event.currency,
                    )
                )
                await s.flush()
                current = (
                    await s.execute(
                        select(Subscription)
                        .where(
                            Subscription.user_id == event.user_id,
                            Subscription.plan_code == event.plan_code,
                            Subscription.status == "active",
                        )
                        .order_by(Subscription.expires_at.desc())
                    )
                ).scalars().first()
                now = utcnow()
                if current and current.expires_at and current.expires_at > now:
                    current.expires_at = current.expires_at + timedelta(days=event.days)
                else:
                    s.add(
                        Subscription(
                            user_id=event.user_id,
                            plan_code=event.plan_code,
                            provider=event.provider,
                            started_at=now,
                            expires_at=now + timedelta(days=event.days),
                        )
                    )
            return True
        except IntegrityError:
            return False
