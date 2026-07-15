"""Data staleness: quotes older than 2x interval block new entries."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.config import YamlConfig
from backend.core.event_bus import TOPIC_ALERT, EventBus
from backend.core.events import Signal, StageResult, ValidatedSignal
from backend.risk.engine import AccountState, RiskEngine
from backend.watchdog.staleness import (
    StalenessMonitor,
    interval_to_seconds,
)

T0 = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


def cfg(multiplier=2.0, hard=6.0, on_hard="hold_with_alert") -> YamlConfig:
    return YamlConfig(name="watchdog", data={"staleness": {
        "interval_multiplier": multiplier,
        "hard_limit_multiplier": hard,
        "on_hard_limit": on_hard,
    }})


def alert_recorder(bus: EventBus) -> list:
    alerts: list = []

    async def on_alert(payload):
        alerts.append(payload)

    bus.subscribe(TOPIC_ALERT, on_alert)
    return alerts


def test_interval_parsing():
    assert interval_to_seconds("1m") == 60
    assert interval_to_seconds("15m") == 900
    assert interval_to_seconds("1h") == 3600
    assert interval_to_seconds("1d") == 86400
    with pytest.raises(ValueError):
        interval_to_seconds("banana")


def test_fresh_quote_does_not_block():
    mon = StalenessMonitor(config=cfg())
    mon.record_quote("NVDA", T0, "1h")
    result = asyncio.run(mon.check(now=T0 + timedelta(hours=1)))
    assert result.block_entries is False
    assert mon.entries_blocked is False


def test_quote_older_than_2x_interval_blocks_entries():
    bus = EventBus()
    alerts = alert_recorder(bus)
    mon = StalenessMonitor(bus=bus, config=cfg())
    mon.record_quote("NVDA", T0, "1h")

    # 2h1m later => older than 2x the 1h interval
    result = asyncio.run(mon.check(now=T0 + timedelta(hours=2, minutes=1)))
    assert result.block_entries is True
    assert result.stale == ["NVDA"]
    assert mon.entries_blocked is True
    assert len(alerts) == 1
    assert alerts[0]["source"] == "watchdog.staleness"


def test_never_seen_symbol_is_stale():
    mon = StalenessMonitor(config=cfg())
    assert mon.is_stale("NVDA", now=T0) is True


def test_hard_limit_with_open_positions_auto_flatten():
    mon = StalenessMonitor(config=cfg(on_hard="auto_flatten"))
    mon.record_quote("NVDA", T0, "1h")
    flattened = {"n": 0}

    async def flatten():
        flattened["n"] += 1

    # 7h later => past the 6x hard limit
    result = asyncio.run(mon.check(
        now=T0 + timedelta(hours=7), has_open_positions=True, flatten=flatten,
    ))
    assert result.action == "auto_flatten"
    assert flattened["n"] == 1


def test_hard_limit_without_positions_only_blocks_entries():
    mon = StalenessMonitor(config=cfg(on_hard="auto_flatten"))
    mon.record_quote("NVDA", T0, "1h")
    flattened = {"n": 0}

    async def flatten():
        flattened["n"] += 1

    result = asyncio.run(mon.check(
        now=T0 + timedelta(hours=7), has_open_positions=False, flatten=flatten,
    ))
    assert result.block_entries is True
    assert result.action == "none"       # nothing to flatten
    assert flattened["n"] == 0


# ── staleness feeds the same entry gate as the watchdog halt ─────────────────
def test_stale_data_blocks_new_entries_end_to_end():
    mon = StalenessMonitor(config=cfg())
    mon.record_quote("NVDA", T0, "1h")
    asyncio.run(mon.check(now=T0 + timedelta(hours=3)))
    assert mon.entries_blocked is True

    signal = Signal("trend_pullback", "NVDA", "LONG", 0.8, T0, {})
    validated = ValidatedSignal(signal, 80.0,
                                [StageResult("confluence_score", True, {}, "ok")],
                                "TREND_UP", T0)
    account = AccountState(
        equity=100_000.0, equity_peak=100_000.0, daily_pnl=0.0,
        open_positions=[], open_positions_by_strategy={},
        consecutive_losses_by_strategy={}, cooldown_until_by_strategy={},
        data_stale=mon.entries_blocked, now=T0,
    )
    decision = RiskEngine().evaluate(validated, account,
                                     entry_price=100.0, atr_value=2.0)
    assert decision.approved is False
    assert "stale" in decision.reason
