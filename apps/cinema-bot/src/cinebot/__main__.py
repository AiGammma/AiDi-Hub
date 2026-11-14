"""Run the bot: ``python -m cinebot``."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .app import build_container
from .bot.handlers import router
from .config import Settings
from .db.session import init_db


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    if not settings.telegram_token:
        raise SystemExit("Set CINEBOT_TELEGRAM_TOKEN (create a bot with @BotFather).")
    if not settings.tmdb_api_key:
        raise SystemExit("Set CINEBOT_TMDB_API_KEY (free key at themoviedb.org -> Settings -> API).")

    container = build_container(settings)
    await init_db(container.engine)
    logging.info("LLM reranking: %s", "on" if container.has_llm else "off (no ANTHROPIC_API_KEY)")

    bot = Bot(settings.telegram_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.workflow_data.update(c=container)
    dp.include_router(router)
    try:
        await dp.start_polling(bot)
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
