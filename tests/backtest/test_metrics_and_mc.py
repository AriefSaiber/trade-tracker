from datetime import datetime, timezone

import pytest

from backend.backtest.metrics import compute_metrics
from backend.backtest.monte_carlo import monte_carlo_drawdown
from backend.portfolio.portfolio import ClosedTrade


def make_trade(pnl: float) -> ClosedTrade:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return ClosedTrade("SPY", "s1", 10, 100.0, 100.0 + pnl / 10, at, at, pnl, 0.0)


def test_expectancy_formula():
    # 40% win rate, avg win 300, avg loss 100 => E = 0.4*300 - 0.6*100 = 60
    trades = [make_trade(300)] * 4 + [make_trade(-100)] * 6
    m = compute_metrics(trades, [], 100_000)
    assert m.win_rate == pytest.approx(0.4)
    assert m.expectancy == pytest.approx(60.0)
    assert m.profit_factor == pytest.approx(1200 / 600)


def test_losing_streak():
    trades = [make_trade(100), make_trade(-50), make_trade(-50),
              make_trade(-50), make_trade(100)]
    m = compute_metrics(trades, [], 100_000)
    assert m.longest_losing_streak == 3


def test_monte_carlo_deterministic_with_seed():
    pnls = [200.0, -100.0, 150.0, -80.0, 300.0, -120.0] * 20
    a = monte_carlo_drawdown(pnls, 100_000, max_dd_halt_pct=15, n_resamples=500, seed=42)
    b = monte_carlo_drawdown(pnls, 100_000, max_dd_halt_pct=15, n_resamples=500, seed=42)
    assert a.dd_p95 == b.dd_p95
    assert a.terminal_p50 == b.terminal_p50


def test_monte_carlo_p95_geq_p50():
    pnls = [200.0, -100.0, 150.0, -80.0, 300.0, -120.0] * 20
    r = monte_carlo_drawdown(pnls, 100_000, max_dd_halt_pct=15, n_resamples=500)
    assert r.dd_p95 >= r.dd_p50
    assert 0.0 <= r.prob_hit_max_dd <= 1.0


def test_monte_carlo_empty_raises():
    with pytest.raises(ValueError):
        monte_carlo_drawdown([], 100_000, 15)
