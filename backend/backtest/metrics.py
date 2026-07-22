"""Backtest performance metrics (MVP §11)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from backend.portfolio.portfolio import ClosedTrade

TRADING_DAYS = 252
SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60


@dataclass
class BacktestMetrics:
    net_profit: float
    expectancy: float
    profit_factor: float
    win_rate: float
    avg_win: float
    avg_loss: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    mar: float
    trade_count: int
    longest_losing_streak: int


def _curve_timing(equity_curve: list[tuple]) -> tuple[float, float]:
    """Return (observations/year, elapsed years) from actual timestamps."""
    if len(equity_curve) < 2:
        return float(TRADING_DAYS), 1 / TRADING_DAYS
    start = equity_curve[0][0]
    end = equity_curve[-1][0]
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return float(TRADING_DAYS), max(len(equity_curve) / TRADING_DAYS, 1 / TRADING_DAYS)
    elapsed_years = (end - start).total_seconds() / SECONDS_PER_YEAR
    if elapsed_years <= 0:
        return float(TRADING_DAYS), 1 / TRADING_DAYS
    return max((len(equity_curve) - 1) / elapsed_years, 1.0), elapsed_years


def compute_metrics(trades: list[ClosedTrade],
                    equity_curve: list[tuple], starting_cash: float) -> BacktestMetrics:
    pnls = np.array([t.pnl - t.commission for t in trades]) if trades else np.array([])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    win_rate = float(len(wins) / len(pnls)) if len(pnls) else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(abs(losses.mean())) if len(losses) else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) else 0.0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win else 0.0

    equity = np.array([e for _, e in equity_curve]) if equity_curve else np.array([starting_cash])
    returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else np.array([0.0])
    observations_per_year, years = _curve_timing(equity_curve)
    sharpe = float(returns.mean() / returns.std() * np.sqrt(observations_per_year)) \
        if returns.std() > 0 else 0.0
    downside = returns[returns < 0]
    sortino = float(returns.mean() / downside.std() * np.sqrt(observations_per_year)) \
        if len(downside) and downside.std() > 0 else 0.0

    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / peak
    max_dd = float(dd.max() * 100) if len(dd) else 0.0

    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if equity[0] > 0 else 0.0
    mar = float(cagr / (max_dd / 100)) if max_dd > 0 else 0.0

    streak = longest = 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        longest = max(longest, streak)

    return BacktestMetrics(
        net_profit=float(pnls.sum()) if len(pnls) else 0.0,
        expectancy=float(expectancy),
        profit_factor=float(profit_factor),
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd,
        mar=mar,
        trade_count=len(pnls),
        longest_losing_streak=longest,
    )
