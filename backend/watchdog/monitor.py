"""Heartbeat monitor (MVP §13).

Every running component emits a heartbeat to Redis; a missed heartbeat
(default 30s) halts trading and fires a critical alert. Once halted, new
entries are blocked until an operator re-arms — doing nothing is always safe.

Storage sits behind `HeartbeatStore` so the same monitor logic runs on both
paths: live/paper uses `RedisHeartbeatStore`, backtest/tests use the in-memory
store. All events are logged as structured JSON via structlog.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import structlog

from backend.core.config import YamlConfig, load_yaml_config
from backend.core.event_bus import TOPIC_ALERT, EventBus

log = structlog.get_logger(__name__)

# Single Redis hash: field = component name, value = ISO-8601 UTC timestamp.
HEARTBEAT_HASH_KEY = "watchdog:heartbeats"


class HeartbeatStore(ABC):
    """Where component heartbeats are recorded and read from."""

    @abstractmethod
    async def record(self, component: str, at: datetime) -> None: ...

    @abstractmethod
    async def read_all(self) -> dict[str, datetime]: ...


class InMemoryHeartbeatStore(HeartbeatStore):
    """Process-local store for backtest, paper, and tests."""

    def __init__(self) -> None:
        self._beats: dict[str, datetime] = {}

    async def record(self, component: str, at: datetime) -> None:
        self._beats[component] = at

    async def read_all(self) -> dict[str, datetime]:
        return dict(self._beats)


class RedisHeartbeatStore(HeartbeatStore):
    """Heartbeats in a single Redis hash. `redis` is a redis.asyncio client."""

    def __init__(self, redis, key: str = HEARTBEAT_HASH_KEY) -> None:
        self._redis = redis
        self._key = key

    async def record(self, component: str, at: datetime) -> None:
        await self._redis.hset(self._key, component, at.isoformat())

    async def read_all(self) -> dict[str, datetime]:
        raw = await self._redis.hgetall(self._key)
        out: dict[str, datetime] = {}
        for field, value in raw.items():
            comp = field.decode() if isinstance(field, (bytes, bytearray)) else field
            ts = value.decode() if isinstance(value, (bytes, bytearray)) else value
            out[comp] = datetime.fromisoformat(ts)
        return out


class HeartbeatMonitor:
    """Emits and checks heartbeats; halts trading on any missed heartbeat."""

    def __init__(
        self,
        store: HeartbeatStore,
        bus: EventBus | None = None,
        config: YamlConfig | None = None,
    ) -> None:
        cfg = config or load_yaml_config("watchdog")
        self._store = store
        self._bus = bus
        self._timeout = float(cfg.get("heartbeat.timeout_seconds", 30))
        self._check_interval = float(cfg.get("heartbeat.check_interval_seconds", 5))
        self._expected = list(cfg.get("heartbeat.components", []) or [])
        self.trading_halted = False
        self.overdue: list[str] = []

    async def beat(self, component: str) -> None:
        """Record a heartbeat for `component` at the current time."""
        await self._store.record(component, datetime.now(timezone.utc))

    async def check(self, now: datetime | None = None) -> list[str]:
        """Return components whose heartbeat is overdue; halt trading if any.

        A component that is expected (config) but has never beat counts as
        overdue — the system fails flat rather than assuming it is alive.
        """
        now = now or datetime.now(timezone.utc)
        beats = await self._store.read_all()

        overdue: set[str] = set()
        for comp in self._expected:
            if comp not in beats:
                overdue.add(comp)
        for comp, at in beats.items():
            if (now - at).total_seconds() > self._timeout:
                overdue.add(comp)

        self.overdue = sorted(overdue)
        if self.overdue and not self.trading_halted:
            self.trading_halted = True
            log.error(
                "watchdog_heartbeat_missed",
                overdue=self.overdue,
                timeout_s=self._timeout,
            )
            if self._bus is not None:
                await self._bus.publish(TOPIC_ALERT, {
                    "level": "critical",
                    "source": "watchdog.heartbeat",
                    "message": f"heartbeat missed: {self.overdue} — trading halted",
                    "overdue": self.overdue,
                    "at": now.isoformat(),
                })
        return self.overdue

    def rearm(self) -> None:
        """Manual re-arm: clears the halt. Does NOT re-enable live trading."""
        self.trading_halted = False
        self.overdue = []
        log.warning("watchdog_heartbeat_rearmed")

    async def run(self, *, iterations: int | None = None) -> None:
        """Periodic check loop. `iterations` bounds it for tests / shutdown;
        None runs forever."""
        count = 0
        while iterations is None or count < iterations:
            await self.check()
            count += 1
            if iterations is not None and count >= iterations:
                return
            await asyncio.sleep(self._check_interval)
