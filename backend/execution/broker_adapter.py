"""BrokerAdapter interface (CLAUDE.md §5)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from backend.core.events import Order, OrderAck, Position


class BrokerAdapter(ABC):
    @abstractmethod
    async def submit_order(self, order: Order) -> OrderAck: ...

    @abstractmethod
    async def cancel_order(self, client_order_id: str) -> None: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_orders(self, status: str | None = None) -> list[Order]: ...
