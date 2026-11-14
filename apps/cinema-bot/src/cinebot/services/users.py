from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import User


@dataclass(frozen=True)
class UserInfo:
    id: int
    public_id: str
    telegram_id: int | None
    is_new: bool = False


class UserService:
    def __init__(self, sm: async_sessionmaker) -> None:
        self._sm = sm

    async def get_or_create(self, telegram_id: int, referrer: str | None = None) -> UserInfo:
        async with self._sm() as s, s.begin():
            user = (await s.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
            if user:
                return UserInfo(user.id, user.public_id, user.telegram_id)
            user = User(telegram_id=telegram_id, referrer=(referrer or None))
            s.add(user)
            await s.flush()
            return UserInfo(user.id, user.public_id, user.telegram_id, is_new=True)
