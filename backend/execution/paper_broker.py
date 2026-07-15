"""Paper broker: same order state machine as live, with configurable
slippage and latency so paper ≈ live (MVP §10)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog

from backend.core.assets import is_crypto
from backend.core.config import YamlConfig, load_yaml_config
from backend.core.events import (
    Fill, Order, OrderAck, OrderSide, OrderStatus, Position,
)
from backend.execution.broker_adapter import BrokerAdapter
from backend.execution.order_state_machine import transition

log = structlog.get_logger(__name__)


class PaperBroker(BrokerAdapter):
    def __init__(self, config: YamlConfig | None = None,
                 price_source=None) -> None:
        """price_source: callable(symbol) -> float | None (latest price).
        In backtest the engine injects bar prices; in paper the live quote cache."""
        cfg = config or load_yaml_config("broker")
        sim = cfg.get("paper_simulator", {})
        self._slippage_bps = float(sim.get("slippage_bps", 3))
        # crypto trades with wider spreads and a %-of-notional taker fee —
        # paper fills must model both or the equity curve lies optimistically
        self._crypto_slippage_bps = float(sim.get("crypto_slippage_bps", 10))
        self._crypto_fee_bps = float(sim.get("crypto_fee_bps", 25))
        self._latency_ms = float(sim.get("latency_ms", 300))
        self._price_source = price_source
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self.fills: list[Fill] = []

    async def submit_order(self, order: Order) -> OrderAck:
        # Idempotency: resubmitting the same client_order_id returns the
        # original ack — a retry can never create a duplicate position.
        if order.client_order_id in self._orders:
            existing = self._orders[order.client_order_id]
            log.info("duplicate_submit_ignored", client_order_id=order.client_order_id)
            return OrderAck(existing.client_order_id, f"paper-{existing.client_order_id}",
                            existing.status, datetime.now(timezone.utc))

        if order.stop_loss is None:
            transition(order, OrderStatus.REJECTED)
            self._orders[order.client_order_id] = order
            raise ValueError("entry order without stop_loss rejected")

        self._orders[order.client_order_id] = order
        transition(order, OrderStatus.SUBMITTED)
        await asyncio.sleep(self._latency_ms / 1000.0)

        price = self._price_source(order.symbol) if self._price_source else None
        if price is None:
            # Fail flat: no price, no fill, order rejected with alert-worthy log
            transition(order, OrderStatus.REJECTED)
            log.error("paper_fill_failed_no_price", symbol=order.symbol,
                      client_order_id=order.client_order_id)
            return OrderAck(order.client_order_id, f"paper-{order.client_order_id}",
                            order.status, datetime.now(timezone.utc))

        crypto = is_crypto(order.symbol)
        slippage_bps = self._crypto_slippage_bps if crypto else self._slippage_bps
        slip = price * slippage_bps / 10_000
        fill_price = price + slip if order.side == OrderSide.BUY else price - slip
        order.broker_order_id = f"paper-{order.client_order_id}"
        transition(order, OrderStatus.FILLED)
        order.filled_qty = order.qty
        order.avg_fill_price = fill_price

        commission = (abs(order.qty) * fill_price * self._crypto_fee_bps / 10_000
                      if crypto else 0.0)
        fill = Fill(order.client_order_id, order.symbol, order.side,
                    order.qty, fill_price, datetime.now(timezone.utc),
                    commission=commission)
        self.fills.append(fill)
        self._apply_fill(order, fill_price)
        log.info("paper_fill", symbol=order.symbol, side=order.side.value,
                 qty=order.qty, price=round(fill_price, 4))
        return OrderAck(order.client_order_id, order.broker_order_id,
                        order.status, datetime.now(timezone.utc))

    def _apply_fill(self, order: Order, price: float) -> None:
        signed = order.qty if order.side == OrderSide.BUY else -order.qty
        pos = self._positions.get(order.symbol)
        if pos is None:
            self._positions[order.symbol] = Position(
                symbol=order.symbol, qty=signed, avg_entry_price=price,
                strategy_id=order.strategy_id, stop_loss=order.stop_loss,
                take_profit=order.take_profit,
            )
            return
        new_qty = pos.qty + signed
        if new_qty == 0:
            del self._positions[order.symbol]
            return
        if (pos.qty > 0) == (signed > 0):   # adding
            pos.avg_entry_price = (
                pos.avg_entry_price * abs(pos.qty) + price * abs(signed)
            ) / abs(new_qty)
        pos.qty = new_qty

    async def cancel_order(self, client_order_id: str) -> None:
        order = self._orders.get(client_order_id)
        if order and order.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIAL,
                                      OrderStatus.PENDING):
            transition(order, OrderStatus.CANCELLED)

    async def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    async def get_orders(self, status: str | None = None) -> list[Order]:
        orders = list(self._orders.values())
        if status:
            orders = [o for o in orders if o.status.value == status]
        return orders
