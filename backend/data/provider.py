"""DataProvider interface (CLAUDE.md §5). Switching providers must never
require touching strategy code."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Awaitable, Callable

from backend.core.events import Bar


class DataProvider(ABC):
    @abstractmethod
    async def get_bars(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[Bar]: ...

    @abstractmethod
    async def subscribe_live(
        self, symbols: list[str], callback: Callable[[Bar], Awaitable[None]]
    ) -> None: ...
