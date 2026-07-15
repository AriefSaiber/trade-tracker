"""Abstract interfaces (CLAUDE.md §5): StrategyBase, DataProvider,
BrokerAdapter. Verifies they are proper ABCs, that concrete implementations
satisfy the contract, and that the foundational modules carry no
NotImplementedError placeholders."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from backend.core.events import (
    Bar,
    Order,
    OrderAck,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Regime,
    Signal,
)
from backend.data.provider import DataProvider
from backend.execution.broker_adapter import BrokerAdapter
from backend.strategies.base import StrategyBase, StrategyContext

REPO_ROOT = Path(__file__).resolve().parents[2]
TS = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


# --- StrategyBase ----------------------------------------------------------

def test_strategy_base_is_abstract():
    with pytest.raises(TypeError):
        StrategyBase()  # type: ignore[abstract]


def test_concrete_strategy_can_be_instantiated_and_emits_signal():
    class Dummy(StrategyBase):
        strategy_id = "dummy"

        def initialize(self, config, context):
            self._ctx = context

        def on_bar(self, bar):
            self._last = bar

        def generate_signal(self):
            return Signal(self.strategy_id, "AAPL", "FLAT", 0.0, TS, {})

    strat = Dummy()
    ctx = StrategyContext(now=TS, regime=Regime.RANGE)
    strat.initialize({}, ctx)
    strat.on_bar(Bar("AAPL", "1h", TS, 1, 1, 1, 1, 1))
    sig = strat.generate_signal()
    assert isinstance(sig, Signal) and sig.strategy_id == "dummy"
    # optional hooks have safe no-op defaults
    strat.teardown()


def test_strategy_context_bars_slicing():
    frame = pd.DataFrame({"close": [1.0, 2.0]})
    ctx = StrategyContext(now=TS, regime=Regime.TREND_UP,
                          history={("AAPL", "1h"): frame})
    assert ctx.bars("AAPL", "1h").equals(frame)
    # missing key -> empty frame, never KeyError
    assert ctx.bars("MSFT", "1d").empty


# --- DataProvider ----------------------------------------------------------

def test_data_provider_is_abstract():
    with pytest.raises(TypeError):
        DataProvider()  # type: ignore[abstract]


def test_concrete_data_provider_satisfies_contract():
    class FakeProvider(DataProvider):
        async def get_bars(self, symbol, interval, start, end):
            return [Bar(symbol, interval, TS, 1, 1, 1, 1, 1)]

        async def subscribe_live(self, symbols, callback):
            await callback(Bar(symbols[0], "1m", TS, 1, 1, 1, 1, 1))

    provider = FakeProvider()
    bars = asyncio.run(provider.get_bars("AAPL", "1h", TS, TS))
    assert len(bars) == 1 and bars[0].symbol == "AAPL"

    got: list[Bar] = []

    async def cb(bar):
        got.append(bar)

    asyncio.run(provider.subscribe_live(["MSFT"], cb))
    assert got and got[0].symbol == "MSFT"


# --- BrokerAdapter ---------------------------------------------------------

def test_broker_adapter_is_abstract():
    with pytest.raises(TypeError):
        BrokerAdapter()  # type: ignore[abstract]


def test_concrete_broker_adapter_satisfies_contract():
    class FakeBroker(BrokerAdapter):
        async def submit_order(self, order):
            return OrderAck(order.client_order_id, "brk-1", OrderStatus.SUBMITTED, TS)

        async def cancel_order(self, client_order_id):
            return None

        async def get_positions(self):
            return []

        async def get_orders(self, status=None):
            return []

    broker = FakeBroker()
    order = Order(
        client_order_id="cid-1", strategy_id="s", symbol="AAPL",
        side=OrderSide.BUY, qty=1, order_type=OrderType.MARKET,
        limit_price=None, stop_loss=1.0, take_profit=2.0, time_in_force="day",
    )
    ack = asyncio.run(broker.submit_order(order))
    assert isinstance(ack, OrderAck) and ack.client_order_id == "cid-1"
    assert asyncio.run(broker.get_positions()) == []


# --- Placeholder guard (directly asserts the goal condition) ---------------

FOUNDATION_FILES = [
    "backend/core/events.py",
    "backend/core/config.py",
    "backend/core/event_bus.py",
    "backend/strategies/base.py",
    "backend/data/provider.py",
    "backend/execution/broker_adapter.py",
]


@pytest.mark.parametrize("rel", FOUNDATION_FILES)
def test_foundation_files_exist(rel):
    assert (REPO_ROOT / rel).is_file(), f"missing foundation file: {rel}"


@pytest.mark.parametrize("rel", FOUNDATION_FILES)
def test_no_notimplemented_placeholders(rel):
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "NotImplementedError" not in text, f"placeholder found in {rel}"
