"""Portfolio accounting: cash, positions, PnL, equity curve."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import structlog

from backend.core.events import Fill, OrderSide, Position

log = structlog.get_logger(__name__)


@dataclass
class ClosedTrade:
    symbol: str
    strategy_id: str
    qty: float
    entry_price: float
    exit_price: float
    entry_at: datetime
    exit_at: datetime
    pnl: float
    commission: float


@dataclass
class Portfolio:
    starting_cash: float
    cash: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    _entry_times: dict[str, datetime] = field(default_factory=dict)
    equity_peak: float = 0.0
    daily_pnl: float = 0.0

    def __post_init__(self) -> None:
        if self.cash == 0.0:
            self.cash = self.starting_cash
        self.equity_peak = self.starting_cash

    def apply_fill(self, fill: Fill, strategy_id: str | None = None,
                   stop_loss: float | None = None,
                   take_profit: float | None = None) -> None:
        signed = fill.qty if fill.side == OrderSide.BUY else -fill.qty
        self.cash -= signed * fill.price + fill.commission
        pos = self.positions.get(fill.symbol)

        if pos is None:
            self.positions[fill.symbol] = Position(
                symbol=fill.symbol, qty=signed, avg_entry_price=fill.price,
                strategy_id=strategy_id, stop_loss=stop_loss, take_profit=take_profit,
            )
            self._entry_times[fill.symbol] = fill.at
            return

        if (pos.qty > 0) == (signed > 0):   # adding to position
            total = abs(pos.qty) + abs(signed)
            pos.avg_entry_price = (
                pos.avg_entry_price * abs(pos.qty) + fill.price * abs(signed)
            ) / total
            pos.qty += signed
            return

        # reducing / closing
        closing_qty = min(abs(signed), abs(pos.qty))
        direction = 1 if pos.qty > 0 else -1
        pnl = (fill.price - pos.avg_entry_price) * closing_qty * direction
        self.daily_pnl += pnl
        remaining = pos.qty + signed
        if remaining == 0 or (remaining > 0) != (pos.qty > 0):
            self.closed_trades.append(ClosedTrade(
                symbol=fill.symbol,
                strategy_id=pos.strategy_id or strategy_id or "unknown",
                qty=closing_qty * direction,
                entry_price=pos.avg_entry_price,
                exit_price=fill.price,
                entry_at=self._entry_times.get(fill.symbol, fill.at),
                exit_at=fill.at,
                pnl=pnl,
                commission=fill.commission,
            ))
            log.info("trade_closed", symbol=fill.symbol, pnl=round(pnl, 2))
            if remaining == 0:
                del self.positions[fill.symbol]
                self._entry_times.pop(fill.symbol, None)
                return
        pos.qty = remaining

    def equity(self, marks: dict[str, float]) -> float:
        value = self.cash
        for symbol, pos in self.positions.items():
            mark = marks.get(symbol, pos.avg_entry_price)
            value += pos.qty * mark
        return value

    def snapshot_equity(self, at: datetime, marks: dict[str, float]) -> float:
        eq = self.equity(marks)
        self.equity_curve.append((at, eq))
        self.equity_peak = max(self.equity_peak, eq)
        return eq

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
