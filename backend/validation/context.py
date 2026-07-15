"""Market context handed to every validation stage.

Deterministic snapshot: same inputs => same stage outputs, in backtest and live.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from backend.core.events import Position, RegimeState


@dataclass
class ValidationContext:
    now: datetime
    regime: RegimeState
    benchmark_symbol: str
    # (symbol, interval) -> bars DataFrame with rows <= now only
    history: dict[tuple[str, str], pd.DataFrame]
    strategy_config: dict
    open_positions: list[Position] = field(default_factory=list)
    equity: float = 0.0
    earnings_calendar: dict[str, list[str]] = field(default_factory=dict)  # symbol -> ISO dates
    sector_map: dict[str, str] = field(default_factory=dict)

    def bars(self, symbol: str, interval: str) -> pd.DataFrame:
        return self.history.get((symbol, interval), pd.DataFrame())
