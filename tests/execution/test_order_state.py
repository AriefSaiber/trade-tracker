import asyncio
from datetime import datetime, timezone

import pytest

from backend.core.events import Order, OrderSide, OrderStatus, OrderType
from backend.execution.order_state_machine import IllegalTransition, transition
from backend.execution.paper_broker import PaperBroker


def make_order(coid: str = "test-1") -> Order:
    return Order(
        client_order_id=coid,
        strategy_id="trend_pullback",
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=10,
        order_type=OrderType.MARKET,
        limit_price=None,
        stop_loss=95.0,
        take_profit=110.0,
        time_in_force="day",
        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )


def test_legal_lifecycle():
    order = make_order()
    transition(order, OrderStatus.SUBMITTED)
    transition(order, OrderStatus.PARTIAL)
    transition(order, OrderStatus.FILLED)
    assert order.status == OrderStatus.FILLED


def test_illegal_transition_raises():
    order = make_order()
    transition(order, OrderStatus.SUBMITTED)
    transition(order, OrderStatus.FILLED)
    with pytest.raises(IllegalTransition):
        transition(order, OrderStatus.CANCELLED)   # FILLED is terminal


def test_cannot_fill_from_pending():
    order = make_order()
    with pytest.raises(IllegalTransition):
        transition(order, OrderStatus.FILLED)


def test_submitted_order_can_expire():
    order = make_order()
    transition(order, OrderStatus.SUBMITTED)
    transition(order, OrderStatus.EXPIRED)
    assert order.status == OrderStatus.EXPIRED


def test_partial_can_expire_but_not_go_back_to_submitted():
    order = make_order()
    transition(order, OrderStatus.SUBMITTED)
    transition(order, OrderStatus.PARTIAL)
    with pytest.raises(IllegalTransition):
        transition(order, OrderStatus.SUBMITTED)
    transition(order, OrderStatus.EXPIRED)
    assert order.status == OrderStatus.EXPIRED


def test_paper_broker_idempotency():
    """Resubmitting the same client_order_id must NOT create a second position."""
    broker = PaperBroker(price_source=lambda s: 100.0)

    async def run():
        ack1 = await broker.submit_order(make_order("dup-1"))
        ack2 = await broker.submit_order(make_order("dup-1"))   # retry
        positions = await broker.get_positions()
        return ack1, ack2, positions

    ack1, ack2, positions = asyncio.run(run())
    assert ack1.client_order_id == ack2.client_order_id
    assert len(positions) == 1
    assert positions[0].qty == 10          # not 20


def test_paper_broker_fails_flat_without_price():
    broker = PaperBroker(price_source=lambda s: None)

    async def run():
        return await broker.submit_order(make_order("noprice-1"))

    ack = asyncio.run(run())
    assert ack.status == OrderStatus.REJECTED


def test_paper_broker_applies_adverse_slippage():
    broker = PaperBroker(price_source=lambda s: 100.0)

    async def run():
        await broker.submit_order(make_order("slip-1"))
        return broker.fills[-1]

    fill = asyncio.run(run())
    assert fill.price > 100.0   # buys fill worse than quote
