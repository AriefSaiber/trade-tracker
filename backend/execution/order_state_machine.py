"""Order state machine: PENDING -> SUBMITTED -> PARTIAL -> FILLED /
CANCELLED / REJECTED / EXPIRED. Illegal transitions raise."""
from __future__ import annotations

import structlog

from backend.core.events import Order, OrderStatus

log = structlog.get_logger(__name__)

_ALLOWED: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.CANCELLED},
    OrderStatus.SUBMITTED: {
        OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED,
        OrderStatus.REJECTED, OrderStatus.EXPIRED,
    },
    OrderStatus.PARTIAL: {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED},
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
}

TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELLED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED}


class IllegalTransition(Exception):
    pass


def transition(order: Order, new_status: OrderStatus) -> Order:
    if new_status not in _ALLOWED[order.status]:
        raise IllegalTransition(
            f"{order.client_order_id}: {order.status.value} -> {new_status.value}"
        )
    log.info("order_transition", client_order_id=order.client_order_id,
             symbol=order.symbol, from_status=order.status.value,
             to_status=new_status.value)
    order.status = new_status
    return order


def is_terminal(order: Order) -> bool:
    return order.status in TERMINAL
