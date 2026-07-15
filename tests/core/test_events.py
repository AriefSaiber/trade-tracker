"""Core event/message dataclasses (CLAUDE.md §5). These are canonical
interfaces consumed across the whole platform, so their shape is pinned here."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from backend.core.events import (
    Bar,
    Fill,
    Order,
    OrderAck,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Regime,
    RegimeState,
    Signal,
    StageResult,
    Tick,
    ValidatedSignal,
)

TS = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


def test_bar_is_frozen_ohlcv():
    bar = Bar("AAPL", "1h", TS, 100.0, 101.0, 99.5, 100.5, 1_000_000)
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        100.0, 101.0, 99.5, 100.5, 1_000_000,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        bar.close = 200.0  # type: ignore[misc]


def test_tick_is_frozen():
    tick = Tick("AAPL", TS, 100.25, 300)
    assert tick.price == 100.25
    with pytest.raises(dataclasses.FrozenInstanceError):
        tick.price = 1.0  # type: ignore[misc]


def test_signal_has_canonical_fields():
    sig = Signal("trend_pullback", "NVDA", "LONG", 0.8, TS, {"note": "x"})
    fields = {f.name for f in dataclasses.fields(sig)}
    assert fields == {
        "strategy_id", "symbol", "direction", "confidence", "bar_time", "metadata",
    }
    assert sig.direction == "LONG"


def test_validated_signal_wraps_signal_and_stage_results():
    sig = Signal("s", "AAPL", "LONG", 0.5, TS, {})
    stage = StageResult("regime_gate", True, {"adx": 30.0}, "ok")
    vs = ValidatedSignal(sig, 82.5, [stage], Regime.TREND_UP.value, TS)
    assert vs.score == 82.5
    assert vs.stage_results[0].passed is True
    assert vs.regime == "TREND_UP"


def test_stage_result_shape():
    r = StageResult("volume_confirmation", False, {"rvol": 0.9}, "rvol<1.2")
    assert (r.stage, r.passed, r.measured, r.reason) == (
        "volume_confirmation", False, {"rvol": 0.9}, "rvol<1.2",
    )


def test_regime_enum_labels():
    assert {r.value for r in Regime} == {
        "TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "TRANSITION",
    }
    # str-enum: value comparison works for config-driven code
    assert Regime.RANGE == "RANGE"


def test_regime_state_defaults_metrics():
    rs = RegimeState("SPY", Regime.HIGH_VOL, TS)
    assert rs.metrics == {}
    rs2 = RegimeState("SPY", Regime.TREND_UP, TS, {"adx": 28.0})
    assert rs2.metrics["adx"] == 28.0


def test_order_defaults_and_idempotency_key():
    order = Order(
        client_order_id="abc-123",
        strategy_id="trend_pullback",
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=10,
        order_type=OrderType.MARKET,
        limit_price=None,
        stop_loss=95.0,
        take_profit=110.0,
        time_in_force="day",
    )
    # stable client_order_id is REQUIRED on every order (idempotency)
    assert order.client_order_id == "abc-123"
    # sensible defaults for the fields the broker fills in later
    assert order.status is OrderStatus.PENDING
    assert order.filled_qty == 0.0
    assert order.avg_fill_price is None
    assert order.broker_order_id is None
    assert order.metadata == {}


def test_order_status_side_type_enums():
    assert {s.value for s in OrderStatus} >= {
        "PENDING", "SUBMITTED", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "EXPIRED",
    }
    assert {s.value for s in OrderSide} == {"BUY", "SELL"}
    assert {t.value for t in OrderType} == {"MARKET", "LIMIT", "STOP"}


def test_order_ack_shape():
    ack = OrderAck("abc-123", "brk-9", OrderStatus.SUBMITTED, TS)
    assert ack.client_order_id == "abc-123"
    assert ack.broker_order_id == "brk-9"
    assert ack.status is OrderStatus.SUBMITTED


def test_fill_commission_default_zero():
    fill = Fill("abc-123", "AAPL", OrderSide.BUY, 10, 100.5, TS)
    assert fill.commission == 0.0


def test_position_signed_qty_and_defaults():
    short = Position("AAPL", -5, 100.0)
    assert short.qty == -5  # negative == short
    assert short.stop_loss is None
    assert short.unrealized_pnl == 0.0
    assert short.sector is None
