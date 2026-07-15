"""Walk-forward optimization scaffolding (MVP §11.2).

Rolling train->trade windows; reports Walk-Forward Efficiency =
OOS performance / IS performance. Requires >= 0.5 for promotion (Gate A).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable


@dataclass
class WalkForwardWindow:
    train_start: datetime
    train_end: datetime
    trade_start: datetime
    trade_end: datetime


@dataclass
class WalkForwardResult:
    windows: list[WalkForwardWindow]
    is_expectancies: list[float]
    oos_expectancies: list[float]

    @property
    def efficiency(self) -> float:
        is_avg = sum(self.is_expectancies) / len(self.is_expectancies)
        oos_avg = sum(self.oos_expectancies) / len(self.oos_expectancies)
        if is_avg <= 0:
            return 0.0
        return max(0.0, oos_avg / is_avg)


def build_windows(
    start: datetime,
    end: datetime,
    train_days: int = 730,
    trade_days: int = 182,
) -> list[WalkForwardWindow]:
    windows: list[WalkForwardWindow] = []
    cursor = start
    while True:
        train_end = cursor + timedelta(days=train_days)
        trade_end = train_end + timedelta(days=trade_days)
        if trade_end > end:
            break
        windows.append(WalkForwardWindow(cursor, train_end, train_end, trade_end))
        cursor = cursor + timedelta(days=trade_days)
    return windows


def run_walk_forward(
    windows: list[WalkForwardWindow],
    evaluate: Callable[[datetime, datetime], float],
    optimize: Callable[[datetime, datetime], float],
) -> WalkForwardResult:
    """`optimize(train_start, train_end)` returns in-sample expectancy with
    best params (persisting them); `evaluate(trade_start, trade_end)` returns
    out-of-sample expectancy using those params."""
    is_exp: list[float] = []
    oos_exp: list[float] = []
    for w in windows:
        is_exp.append(optimize(w.train_start, w.train_end))
        oos_exp.append(evaluate(w.trade_start, w.trade_end))
    return WalkForwardResult(windows, is_exp, oos_exp)
