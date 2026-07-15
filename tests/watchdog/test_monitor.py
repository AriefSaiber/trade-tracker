"""Heartbeat monitor: missed heartbeat halts trading and blocks new entries."""
import asyncio
from datetime import datetime, timedelta, timezone

from backend.core.config import YamlConfig
from backend.core.event_bus import TOPIC_ALERT, EventBus
from backend.core.events import Signal, StageResult, ValidatedSignal
from backend.risk.engine import AccountState, RiskEngine
from backend.watchdog.monitor import (
    HeartbeatMonitor,
    InMemoryHeartbeatStore,
    RedisHeartbeatStore,
)

T0 = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


def cfg(components=None, timeout=30.0) -> YamlConfig:
    return YamlConfig(name="watchdog", data={"heartbeat": {
        "timeout_seconds": timeout,
        "check_interval_seconds": 0.0,   # no real sleep in run()
        "components": components or [],
    }})


def make_monitor(bus=None, components=None, timeout=30.0):
    return HeartbeatMonitor(InMemoryHeartbeatStore(), bus,
                            config=cfg(components, timeout))


def alert_recorder(bus: EventBus) -> list:
    alerts: list = []

    async def on_alert(payload):
        alerts.append(payload)

    bus.subscribe(TOPIC_ALERT, on_alert)
    return alerts


def test_fresh_heartbeat_is_not_overdue():
    mon = make_monitor()

    async def run():
        await mon.beat("worker")
        return await mon.check(now=T0 + timedelta(seconds=10))

    overdue = asyncio.run(run())
    assert overdue == []
    assert mon.trading_halted is False


def test_missed_heartbeat_halts_and_alerts():
    bus = EventBus()
    alerts = alert_recorder(bus)
    mon = make_monitor(bus)

    # Pin the beat time, then move the clock past the 30s timeout.
    mon._store._beats["worker"] = T0
    overdue = asyncio.run(mon.check(now=T0 + timedelta(seconds=31)))

    assert overdue == ["worker"]
    assert mon.trading_halted is True
    assert len(alerts) == 1
    assert alerts[0]["level"] == "critical"
    assert alerts[0]["source"] == "watchdog.heartbeat"


def test_expected_component_never_beating_is_overdue():
    mon = make_monitor(components=["data_feed"])
    overdue = asyncio.run(mon.check(now=T0))
    assert overdue == ["data_feed"]
    assert mon.trading_halted is True


def test_rearm_clears_halt():
    mon = make_monitor()
    mon._store._beats["worker"] = T0
    asyncio.run(mon.check(now=T0 + timedelta(seconds=31)))
    assert mon.trading_halted is True
    mon.rearm()
    assert mon.trading_halted is False
    assert mon.overdue == []


def test_run_bounded_iterations():
    mon = make_monitor(components=["worker"])
    asyncio.run(mon.run(iterations=3))
    # worker never beat => halted after the first check
    assert mon.trading_halted is True


def test_redis_store_roundtrip_via_fake_client():
    class FakeRedis:
        def __init__(self):
            self.h: dict[str, str] = {}

        async def hset(self, key, field, value):
            self.h[field] = value

        async def hgetall(self, key):
            # redis-py returns bytes by default; mimic that
            return {k.encode(): v.encode() for k, v in self.h.items()}

    store = RedisHeartbeatStore(FakeRedis())

    async def run():
        await store.record("worker", T0)
        return await store.read_all()

    beats = asyncio.run(run())
    assert beats == {"worker": T0}


# ── the §13 acceptance test: missed heartbeat => new entries blocked ──────────
def _validated() -> ValidatedSignal:
    signal = Signal("trend_pullback", "NVDA", "LONG", 0.8, T0, {})
    return ValidatedSignal(signal, 80.0,
                           [StageResult("confluence_score", True, {}, "ok")],
                           "TREND_UP", T0)


def _account(**overrides) -> AccountState:
    base = dict(
        equity=100_000.0, equity_peak=100_000.0, daily_pnl=0.0,
        open_positions=[], open_positions_by_strategy={},
        consecutive_losses_by_strategy={}, cooldown_until_by_strategy={},
        now=T0,
    )
    base.update(overrides)
    return AccountState(**base)


def test_missed_heartbeat_blocks_new_entries_end_to_end():
    mon = make_monitor()
    mon._store._beats["worker"] = T0
    asyncio.run(mon.check(now=T0 + timedelta(seconds=31)))
    assert mon.trading_halted is True

    engine = RiskEngine()
    # a healthy account would approve this entry ...
    ok = engine.evaluate(_validated(), _account(),
                         entry_price=100.0, atr_value=2.0)
    assert ok.approved is True
    # ... but once the watchdog has halted, the same entry is blocked.
    blocked = engine.evaluate(
        _validated(), _account(watchdog_halted=mon.trading_halted),
        entry_price=100.0, atr_value=2.0,
    )
    assert blocked.approved is False
    assert "watchdog" in blocked.reason
