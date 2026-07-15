"""Notification dispatcher interface + Telegram implementation (MVP §14)."""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
import structlog

from backend.core.config import Settings

log = structlog.get_logger(__name__)


class Notifier(ABC):
    @abstractmethod
    async def send(self, message: str, level: str = "info") -> None: ...


class TelegramNotifier(Notifier):
    def __init__(self, settings: Settings) -> None:
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id

    async def send(self, message: str, level: str = "info") -> None:
        if not self._token or not self._chat_id:
            log.warning("telegram_not_configured", level=level)
            return
        prefix = {"info": "i", "warning": "!", "error": "!!", "critical": "!!!"}.get(level, "i")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{self._token}/sendMessage",
                    json={"chat_id": self._chat_id,
                          "text": f"[{prefix}] AlgoTrader: {message}"},
                )
        except Exception as exc:
            # Notification failure must never break the trading path
            log.error("telegram_send_failed", error=str(exc))


class NullNotifier(Notifier):
    async def send(self, message: str, level: str = "info") -> None:
        log.info("notification", message=message, level=level)
