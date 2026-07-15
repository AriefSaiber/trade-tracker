"""Portfolio accounting: fills in/out, PnL, equity curve, daily reset."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.core.events import Fill, OrderSide
from backend.portfolio.portfolio import Portfolio

T0 = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)


def _fill(symbol: str, side: OrderSide, qty: float, price: float,
          at: datetime = T0, commission: float = 0.0) -> Fill:
    return Fill(f"co-{symbol}-{at.isoformat()}-{side.value}", symbol, side,
                qty, price, at, commission)


def test_open_position_reduces_cash_and_tracks_entry():
    p = Portfolio(starting_cash=10_000.0)
    p.apply_fill(_fill("AAPL", OrderSide.BUY, 10, 100.0), "s1", 95.0, 110.0)
    assert p.cash == 9_000.0
    pos = p.positions["AAPL"]
    assert pos.qty == 10 and pos.avg_entry_price == 100.0
    assert pos.stop_loss == 95.0 and pos.take_profit == 110.0
    assert pos.strategy_id == "s1"


def test_adding_averages_entry_price():
    p = Portfolio(starting_cash=10_000.0)
    p.apply_fill(_fill("AAPL", OrderSide.BUY, 10, 100.0))
    p.apply_fill(_fill("AAPL", OrderSide.BUY, 10, 110.0, T0 + timedelta(hours=1)))
    assert p.positions["AAPL"].qty == 20
    assert p.positions["AAPL"].avg_entry_price == 105.0


def test_close_realizes_pnl_and_records_trade():
    p = Portfolio(starting_cash=10_000.0)
    p.apply_fill(_fill("AAPL", OrderSide.BUY, 10, 100.0), "s1")
    p.apply_fill(_fill("AAPL", OrderSide.SELL, 10, 108.0, T0 + timedelta(days=1)))
    assert "AAPL" not in p.positions
    assert p.cash == 10_000.0 + 80.0
    assert p.daily_pnl == 80.0
    (trade,) = p.closed_trades
    assert trade.symbol == "AAPL" and trade.pnl == 80.0
    assert trade.entry_at == T0 and trade.exit_at == T0 + timedelta(days=1)
    assert trade.strategy_id == "s1"


def test_partial_close_keeps_remainder():
    p = Portfolio(starting_cash=10_000.0)
    p.apply_fill(_fill("AAPL", OrderSide.BUY, 10, 100.0))
    p.apply_fill(_fill("AAPL", OrderSide.SELL, 4, 110.0, T0 + timedelta(hours=2)))
    assert p.positions["AAPL"].qty == 6
    assert p.daily_pnl == 40.0
    # partial reductions realize PnL but only a full close records the trade
    assert p.closed_trades == []


def test_equity_marks_open_positions_and_snapshots_peak():
    p = Portfolio(starting_cash=10_000.0)
    p.apply_fill(_fill("AAPL", OrderSide.BUY, 10, 100.0))
    assert p.equity({"AAPL": 105.0}) == 9_000.0 + 1_050.0
    p.snapshot_equity(T0, {"AAPL": 105.0})
    p.snapshot_equity(T0 + timedelta(hours=1), {"AAPL": 90.0})
    assert p.equity_peak == 10_050.0
    assert len(p.equity_curve) == 2


def test_daily_reset_clears_daily_pnl_only():
    p = Portfolio(starting_cash=10_000.0)
    p.apply_fill(_fill("AAPL", OrderSide.BUY, 10, 100.0))
    p.apply_fill(_fill("AAPL", OrderSide.SELL, 10, 90.0, T0 + timedelta(hours=3)))
    assert p.daily_pnl == -100.0
    p.reset_daily()
    assert p.daily_pnl == 0.0
    assert len(p.closed_trades) == 1        # history survives the reset


def test_commission_reduces_cash():
    p = Portfolio(starting_cash=10_000.0)
    p.apply_fill(_fill("AAPL", OrderSide.BUY, 10, 100.0, commission=1.5))
    assert p.cash == 10_000.0 - 1_000.0 - 1.5
