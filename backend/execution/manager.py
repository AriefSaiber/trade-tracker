"""Execution Manager: the ONLY component that talks to brokers, and it only
accepts orders that came from RiskEngine.evaluate() (RiskDecision.approved)."""
from __future__ import annotations

import structlog

from backend.core.event_bus import TOPIC_ALERT, TOPIC_ORDER_EVENT, EventBus
from backend.core.events import OrderAck, Position
from backend.execution.broker_adapter import BrokerAdapter
from backend.execution.reconciler import Reconciler
from backend.risk.engine import RiskDecision

log = structlog.get_logger(__name__)


class ReconciliationError(Exception):
    pass


class ExecutionManager:
    def __init__(self, broker: BrokerAdapter, bus: EventBus,
                 reconciler: Reconciler | None = None) -> None:
        self._broker = broker
        self._bus = bus
        self._submitted: dict[str, OrderAck] = {}   # local idempotency cache
        self._reconciler = reconciler or Reconciler(broker, bus)

    @property
    def blocked(self) -> bool:
        """True while a reconciliation mismatch is unresolved — new entries
        are dropped (fail flat)."""
        return self._reconciler.blocked

    async def execute(self, decision: RiskDecision) -> OrderAck | None:
        """Accepts a RiskDecision — not a raw Order — so there is no public
        path around the Risk Engine."""
        if not decision.approved or decision.order is None:
            log.warning("execute_called_without_approval", reason=decision.reason)
            return None
        if self.blocked:
            log.error("execution_blocked_reconciliation")
            await self._bus.publish(TOPIC_ALERT, {
                "level": "error",
                "message": "order dropped: execution blocked pending reconciliation",
            })
            return None

        order = decision.order
        if order.client_order_id in self._submitted:
            log.info("idempotent_resubmit_skipped", client_order_id=order.client_order_id)
            return self._submitted[order.client_order_id]

        ack = await self._broker.submit_order(order)
        self._submitted[order.client_order_id] = ack
        await self._bus.publish(TOPIC_ORDER_EVENT, {
            "client_order_id": ack.client_order_id,
            "broker_order_id": ack.broker_order_id,
            "status": ack.status.value,
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": order.qty,
            "at": ack.at.isoformat(),
        })
        return ack

    async def reconcile(self, local_positions: list[Position]) -> bool:
        """Broker truth vs local portfolio via the shared Reconciler. Mismatch
        => alert + block new entries until resolved (fail flat)."""
        result = await self._reconciler.reconcile(local_positions)
        return result.ok

    async def cancel_all(self) -> None:
        for order in await self._broker.get_orders(status="open"):
            await self._broker.cancel_order(order.client_order_id)
