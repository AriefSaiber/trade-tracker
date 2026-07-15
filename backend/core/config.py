"""Typed configuration: env via Pydantic Settings, thresholds via configs/*.yaml.

No module reads os.environ or opens YAML directly — everything goes through here.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"


class Settings(BaseSettings):
    """Secrets and mode flags from environment / .env only."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Paper mode is the default state of the system, always.
    live_trading: bool = Field(default=False, alias="LIVE_TRADING")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # Run the trading worker inside the API process (single-process local dev;
    # in Docker the worker is its own container and this stays false).
    worker_embedded: bool = Field(default=False, alias="WORKER_EMBEDDED")

    # Full SQLAlchemy URL override. Empty => derived: Postgres when a password
    # is configured, otherwise a local SQLite file (zero-setup dev fallback).
    database_url_override: str = Field(default="", alias="DATABASE_URL")

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="algotrader", alias="POSTGRES_DB")
    postgres_user: str = Field(default="algotrader", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")

    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")

    alpaca_paper_key_id: str = Field(default="", alias="ALPACA_PAPER_KEY_ID")
    alpaca_paper_secret: str = Field(default="", alias="ALPACA_PAPER_SECRET")
    alpaca_live_key_id: str = Field(default="", alias="ALPACA_LIVE_KEY_ID")
    alpaca_live_secret: str = Field(default="", alias="ALPACA_LIVE_SECRET")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    ai_analyst_enabled: bool = Field(default=False, alias="AI_ANALYST_ENABLED")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    jwt_secret: str = Field(default="dev-only-secret", alias="JWT_SECRET")

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        if self.postgres_password:
            return (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        # Zero-setup local dev: file-backed SQLite. Postgres/Timescale remains
        # the deployment target (docker compose sets POSTGRES_PASSWORD).
        return "sqlite+aiosqlite:///data/algotrader.db"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def alpaca_key_id(self) -> str:
        return self.alpaca_live_key_id if self.live_trading else self.alpaca_paper_key_id

    @property
    def alpaca_secret(self) -> str:
        return self.alpaca_live_secret if self.live_trading else self.alpaca_paper_secret


class YamlConfig(BaseModel):
    """Loaded, validated view of one configs/*.yaml file."""

    name: str
    data: dict[str, Any]

    def get(self, dotted_key: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


@lru_cache(maxsize=None)
def load_yaml_config(name: str, config_dir: str | None = None) -> YamlConfig:
    path = (Path(config_dir) if config_dir else CONFIG_DIR) / f"{name}.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return YamlConfig(name=name, data=data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
