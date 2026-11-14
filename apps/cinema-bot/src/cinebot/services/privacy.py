"""Consent, personal-data export ("download my data") and deletion.

Consent is opt-in and per purpose. Nothing beyond serving the user is done with
their data unless the matching consent is granted, and the bot's /privacy
command lets them revoke it at any time.
"""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import (
    Consent,
    FavoriteDirector,
    Payment,
    Recommendation,
    Subscription,
    TasteProfileRow,
    Title,
    UsageEvent,
    User,
    UserTitle,
    utcnow,
)
from ..domain.models import ConsentPurpose

POLICY_VERSION = "1"  # bump when the wording users agreed to changes


class PrivacyService:
    def __init__(self, sm: async_sessionmaker) -> None:
        self._sm = sm

    async def set_consent(self, user_id: int, purpose: ConsentPurpose, granted: bool) -> None:
        async with self._sm() as s, s.begin():
            row = (
                await s.execute(
                    select(Consent).where(Consent.user_id == user_id, Consent.purpose == purpose.value)
                )
            ).scalar_one_or_none()
            if row is None:
                row = Consent(user_id=user_id, purpose=purpose.value)
                s.add(row)
            row.granted = granted
            row.policy_version = POLICY_VERSION
            row.updated_at = utcnow()

    async def consents(self, user_id: int) -> dict[ConsentPurpose, bool]:
        async with self._sm() as s:
            rows = (await s.execute(select(Consent).where(Consent.user_id == user_id))).scalars().all()
        granted = {r.purpose: r.granted for r in rows}
        return {p: granted.get(p.value, False) for p in ConsentPurpose}

    async def export_user_data(self, user_id: int) -> dict:
        """Everything we hold about the user, in a portable form."""
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(UserTitle, Title.title, Title.year)
                    .outerjoin(Title, UserTitle.title_id == Title.id)
                    .where(UserTitle.user_id == user_id)
                )
            ).all()
            directors = (
                await s.execute(select(FavoriteDirector.name).where(FavoriteDirector.user_id == user_id))
            ).scalars().all()
            profile = await s.get(TasteProfileRow, user_id)
        return {
            "titles": [
                {
                    "title": t or ut.raw_title,
                    "year": y or ut.raw_year,
                    "status": ut.status,
                    "rating": ut.rating,
                    "source": ut.source,
                }
                for ut, t, y in rows
            ],
            "favorite_directors": list(directors),
            "taste_profile": (profile.weights if profile else {}),
            "consents": {p.value: v for p, v in (await self.consents(user_id)).items()},
        }

    async def purge_user(self, user_id: int) -> None:
        """Delete personal data. Payments are kept for accounting with the user link cut."""
        async with self._sm() as s, s.begin():
            await s.execute(update(Payment).where(Payment.user_id == user_id).values(user_id=None))
            for model in (
                UserTitle,
                FavoriteDirector,
                TasteProfileRow,
                Recommendation,
                Consent,
                UsageEvent,
                Subscription,
            ):
                await s.execute(delete(model).where(model.user_id == user_id))
            await s.execute(
                update(User)
                .where(User.id == user_id)
                .values(telegram_id=None, referrer=None, deleted_at=utcnow())
            )
