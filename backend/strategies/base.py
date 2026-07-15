"""StrategyBase (CLAUDE.md §5).

HARD RULE: strategy code must never import from backend.execution,
backend.risk, or backend.portfolio. Strategies emit Signal objects only.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from backend.core.events import Bar, Regime, Signal, Tick


@dataclass
class StrategyContext:
    """Read-only market context injected into strategies. Identical object in
    backtest, paper, and live — this is what keeps one code path."""

    now: datetime
    regime: Regime
    # history frames keyed by (symbol, interval); rows are bars with
    # timestamp <= now ONLY (the runtime enforces point-in-time slicing).
    history: dict[tuple[str, str], pd.DataFrame] = field(default_factory=dict)
    # Read-only position awareness, populated by the runtime/backtester.
    # Strategies may use it for exit timing and to avoid pyramiding; the
    # Portfolio remains the source of truth and stays un-importable from here.
    position_qty: dict[str, float] = field(default_factory=dict)   # signed qty
    bars_held: dict[str, int] = field(default_factory=dict)        # bars since entry

    def bars(self, symbol: str, interval: str) -> pd.DataFrame:
        return self.history.get((symbol, interval), pd.DataFrame())

    def qty(self, symbol: str) -> float:
        return self.position_qty.get(symbol, 0.0)

    def held_bars(self, symbol: str) -> int:
        return self.bars_held.get(symbol, 0)


class StrategyBase(ABC):
    strategy_id: str = "unnamed"

    @abstractmethod
    def initialize(self, config: dict, context: StrategyContext) -> None: ...

    @abstractmethod
    def on_bar(self, bar: Bar) -> None: ...

    def on_tick(self, tick: Tick) -> None:  # optional
        pass

    @abstractmethod
    def generate_signal(self) -> Signal | None: ...

    def teardown(self) -> None:
        pass
