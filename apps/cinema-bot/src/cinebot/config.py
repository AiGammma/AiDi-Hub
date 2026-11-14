from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CINEBOT_", extra="ignore")

    telegram_token: str = ""

    tmdb_api_key: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_language: str = "ru-RU"

    # ANTHROPIC_API_KEY itself is read by the SDK from the environment.
    anthropic_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    llm_enabled: bool = True

    database_url: str = "sqlite+aiosqlite:///./cinebot.db"

    owner_telegram_ids: str = ""
    monetization_enabled: bool = False

    export_salt: str = ""
    export_min_group_size: int = Field(default=5, ge=2)
    export_min_ratings: int = Field(default=20, ge=1)

    @property
    def owner_ids(self) -> set[int]:
        return {int(x) for x in self.owner_telegram_ids.replace(";", ",").split(",") if x.strip()}

    @property
    def llm_available(self) -> bool:
        return self.llm_enabled and bool(os.environ.get("ANTHROPIC_API_KEY"))
