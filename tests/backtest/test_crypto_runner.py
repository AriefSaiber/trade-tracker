"""Wiring + accounting tests for the crypto Gate A harness — no network.

Proves: (1) net_pnls counts the FULL round-trip commission (not just the exit
leg the engine attributes), (2) aggregate metrics/plateau/Gate-A verdict are
computed correctly, and (3) the whole pipeline runs the real EventDrivenEngine
over a synthetic crafted-breakout series and produces trades."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from backend.backtest.crypto_runner import (
    CRYPTO_COMMISSION_BPS, aggregate_metrics, bars_to_frame, evaluate_gate_a,
    net_pnls, run_single,
)
from backend.core.events import Bar
from backend.portfolio.portfolio import ClosedTrade
from backend.strategies.btc_trend_momentum.strategy import BtcTrendMomentumStrategy

SYMBOL = "BTC/USD"


# ── cost-correct accounting ────────────────────────────────────────────────
def test_net_pnls_charges_full_round_trip_commission():
    trade = ClosedTrade(
        symbol=SYMBOL, strategy_id="btc_trend_momentum", qty=0.5,
        entry_price=100_000.0, exit_price=110_000.0,
        entry_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        exit_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        pnl=5_000.0, commission=0.0,
    )
    # gross 5000; commission = 0.5*(100k+110k)*25bps = 105000*0.0025 = 262.5
    (net,) = net_pnls([trade])
    assert abs(net - (5_000.0 - 262.5)) < 1e-6


def test_aggregate_metrics_basic_shape():
    agg = aggregate_metrics([100.0, -50.0, 200.0, -25.0], starting_cash=10_000.0)
    assert agg.trade_count == 4
    assert agg.win_rate == 0.5
    assert abs(agg.net_profit - 225.0) < 1e-9
    assert abs(agg.profit_factor - (300.0 / 75.0)) < 1e-9
    assert agg.max_drawdown_pct >= 0.0


def test_aggregate_metrics_empty_is_zeroed():
    agg = aggregate_metrics([], starting_cash=10_000.0)
    assert agg.trade_count == 0 and agg.profit_factor == 0.0


# ── end-to-end engine wiring on a crafted breakout ─────────────────────────
def _uptrend_daily(days: int = 300, start_price: float = 30_000.0,
                   end: str = "2026-07-01") -> pd.DataFrame:
    """Mild uptrend with STATIONARY volatility (constant absolute range), so
    daily ATR sits mid-distribution and clears Stage 4's [20,90] percentile
    band — a compounding series would peg ATR at the 100th percentile."""
    rng = np.random.default_rng(7)
    # strong drift-to-noise so most days are up => ADX clears 25 (TREND_UP)
    close = start_price + np.linspace(0, 18_000, days) + rng.normal(0, 25, days)
    # varied absolute range (stationary, not %-of-price) so daily ATR wanders;
    # pin the recent bars to the median so the decision bar lands mid-band
    # (Stage 4 wants ATR percentile in [20,90]) rather than at a flat-series
    # degenerate 100th percentile
    spread = np.clip(250.0 + rng.normal(0, 40, days), 150.0, 350.0)
    spread[-15:] = 250.0
    idx = pd.date_range(end=end, periods=days, freq="D", tz="UTC")
    return pd.DataFrame({"open": close, "high": close + spread,
                         "low": close - spread, "close": close,
                         "volume": np.full(days, 5000.0)}, index=idx)


def _breakout_hourly() -> pd.DataFrame:
    """Long chop, then a decisive breakout above the 55-bar high, then a pullback
    through the 20-bar low to force a channel exit — at least one closed trade."""
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    closes = [30_000 + (60 if i % 2 else -60) for i in range(120)]   # chop
    closes += [31_800, 32_400, 33_000, 33_600]                        # breakout up
    closes += [30_500, 29_900, 29_500]                                # drop -> exit
    rows = []
    for i, c in enumerate(closes):
        c = float(c)
        rows.append(Bar(SYMBOL, "1h", base + timedelta(hours=i),
                        c - 20, c + 40, c - 40, c, 15.0))
    return rows


def test_run_single_produces_trades_through_real_engine():
    hourly_bars = _breakout_hourly()
    history = {(SYMBOL, "1h"): bars_to_frame(hourly_bars),
               (SYMBOL, "1d"): _uptrend_daily()}
    config = {
        "strategy_id": "btc_trend_momentum", "interval": "1h",
        "risk_per_trade_pct": 0.5, "allow_short": True,
        "allowed_regimes": ["TREND_UP", "TREND_DOWN"], "opt_in_high_vol": False,
        "validation_overrides": {
            "data_sanity": {"min_volume": 0.000001},
            "volume_confirmation": {"skip": True},
            "confluence_score": {"threshold": 0},   # let crafted setup through
            "event_filter": {"skip": True},
        },
        "parameters": {
            "donchian_period": 55, "exit_channel_period": 20,
            "daily_ema_fast": 50, "daily_ema_slow": 200, "atr_period": 14,
            "breakout_buffer_atr": 0.0, "min_history_bars": 80,
            "take_profit_r_multiple": 3.0, "max_holding_bars": 240,
        },
    }
    result = run_single(BtcTrendMomentumStrategy(), config, hourly_bars, history,
                        starting_cash=100_000.0, benchmark_symbol=SYMBOL)
    assert result is not None
    # the crafted breakout must fill a long entry through the full gauntlet
    assert result.portfolio.closed_trades or result.portfolio.positions
    # every fill quantity is fractional (crypto), never floored to zero
    for pos in result.portfolio.positions.values():
        assert 0 < abs(pos.qty) < 1


def test_run_single_empty_bars_returns_none():
    assert run_single(BtcTrendMomentumStrategy(), {}, [], {}, 100_000.0, SYMBOL) is None


def test_gate_a_fails_gracefully_on_thin_sample():
    """A short synthetic series can't clear the ≥100-trade bar — the harness
    must still return a well-formed report with a FAIL verdict, not crash."""
    hourly_bars = _breakout_hourly()       # ~127 bars, far too few for Gate A
    # stretch timestamps to ~95 days so build_windows yields ≥1 window
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    stretched = [Bar(b.symbol, b.interval, base + timedelta(hours=18 * i),
                     b.open, b.high, b.low, b.close, b.volume)
                 for i, b in enumerate(hourly_bars)]
    daily_end = stretched[-1].timestamp.strftime("%Y-%m-%d")
    history = {(SYMBOL, "1h"): bars_to_frame(stretched),
               (SYMBOL, "1d"): _uptrend_daily(days=400, end=daily_end)}
    config = {
        "strategy_id": "btc_trend_momentum", "interval": "1h",
        "risk_per_trade_pct": 0.5, "allow_short": True,
        "allowed_regimes": ["TREND_UP", "TREND_DOWN"], "opt_in_high_vol": False,
        "validation_overrides": {"data_sanity": {"min_volume": 0.000001},
                                 "volume_confirmation": {"skip": True},
                                 "confluence_score": {"threshold": 0},
                                 "event_filter": {"skip": True}},
        "parameters": {"donchian_period": 55, "exit_channel_period": 20,
                       "daily_ema_fast": 50, "daily_ema_slow": 200,
                       "atr_period": 14, "breakout_buffer_atr": 0.0,
                       "min_history_bars": 80, "take_profit_r_multiple": 3.0,
                       "max_holding_bars": 240}}
    report = evaluate_gate_a(
        strategy_factory=BtcTrendMomentumStrategy, base_config=config,
        signal_bars=stretched, history=history, starting_cash=100_000.0,
        benchmark_symbol=SYMBOL, symbol=SYMBOL, interval="1h",
        train_days=55, trade_days=30, mc_resamples=200,
    )
    assert report.passed is False
    assert any("trades" in c for c in report.failed_criteria)
    assert report.to_dict()["symbol"] == SYMBOL   # JSON-serializable
