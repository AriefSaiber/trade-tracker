"""Shared dashboard state store — the worker → API bridge.

The worker publishes JSON-serializable snapshots (regime, portfolio, signals,
funnel, orders, strategies, health) under well-known keys; the FastAPI app
reads them on request. Two backends, one interface:

- ``InMemoryStateStore`` — worker embedded in the API process (local dev).
- ``RedisStateStore``    — worker and API in separate processes/containers
  (docker compose); state lives in Redis under ``algotrader:state:*``.

``connect_state_store`` picks Redis when it is reachable, otherwise falls
back to in-memory with a warning — the platform degrades, it never crashes.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from backend.core.config import Settings

log = structlog.get_logger(__name__)

KEY_PREFIX = "algotrader:state:"

# Well-known state keys
KEY_REGIME = "regime"
KEY_PORTFOLIO = "portfolio"
KEY_SIGNALS = "signals"
KEY_FUNNEL = "funnel"
KEY_FUNNEL_SUMMARY = "funnel_summary"
KEY_STRATEGIES = "strategies"
KEY_ORDERS = "orders"
KEY_WORKER = "worker"
KEY_ALERTS = "alerts"


class StateStore:
    """Async get/set of JSON-serializable state snapshots."""

    async def set(self, key: str, value: Any) -> None:
        raise NotImplementedError

    async def get(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class InMemoryStateStore(StateStore):
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def set(self, key: str, value: Any) -> None:
        # round-trip through JSON so both backends enforce the same contract
        self._data[key] = json.loads(json.dumps(value, default=str))

    async def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class RedisStateStore(StateStore):
    def __init__(self, redis) -> None:
        self._redis = redis

    async def set(self, key: str, value: Any) -> None:
        await self._redis.set(KEY_PREFIX + key, json.dumps(value, default=str))

    async def get(self, key: str, default: Any = None) -> Any:
        raw = await self._redis.get(KEY_PREFIX + key)
        if raw is None:
            return default
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        return json.loads(raw)

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:  # closing must never raise
            pass


async def connect_state_store(settings: Settings) -> StateStore:
    """Redis if reachable, otherwise in-memory (logged, never fatal)."""
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.redis_url, socket_connect_timeout=2.0, socket_timeout=2.0
        )
        await client.ping()
        log.info("state_store_connected", backend="redis", url=settings.redis_url)
        return RedisStateStore(client)
    except Exception as exc:
        log.warning("state_store_fallback_memory", error=str(exc))
        return InMemoryStateStore()
