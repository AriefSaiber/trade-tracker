"""BTC Trend Momentum: Donchian breakout entries (long AND short), trend
filter, channel/time exits. Pure StrategyContext tests — no runtime needed."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.core.events import Bar, Regime
from backend.strategies.base import StrategyContext
from backend.strategies.btc_trend_momentum.strategy import BtcTrendMomentumStrategy

SYMBOL = "BTC/USD"

CONFIG = {
    "strategy_id": "btc_trend_momentum",
    "interval": "1h",
    "risk_per_trade_pct": 0.5,
    "allow_short": True,
    "parameters": {
        "donchian_period": 55,
        "exit_channel_period": 20,
        "daily_ema_fast": 50,
        "daily_ema_slow": 200,
        "atr_period": 14,
        "breakout_buffer_atr": 0.0,
        "min_history_bars": 80,
        "take_profit_r_multiple": 3.0,
        "max_holding_bars": 240,
    },
}


def hourly_frame(closes: list[float], end: str = "2026-07-10 15:00") -> pd.DataFrame:
    """OHLCV frame whose last row is the bar under decision."""
    idx = pd.date_range(end=end, periods=len(closes), freq="h", tz="UTC")
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.full(len(closes), 12.5),   # coins, not shares
    })


def daily_frame(days: int = 300, trend: float = 0.002,
                start_price: float = 50_000.0) -> pd.DataFrame:
    idx = pd.date_range(end="2026-07-10", periods=days, freq="D", tz="UTC")
    close = start_price * np.cumprod(np.full(days, 1 + trend))
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(days, 1000.0),
    }, index=idx)


def chop_closes(n: int = 119, level: float = 100.0) -> list[float]:
    """Alternating +/-1 chop around `level` — no breakout, nonzero ATR."""
    return [level + (1.0 if i % 2 == 0 else -1.0) for i in range(n)]


def make_strategy(hourly: pd.DataFrame, daily: pd.DataFrame,
                  qty: float = 0.0, held: int = 0,
                  config: dict | None = None) -> BtcTrendMomentumStrategy:
    ctx = StrategyContext(
        now=hourly.index[-1].to_pydatetime(),
        regime=Regime.TREND_UP,
        history={(SYMBOL, "1h"): hourly, (SYMBOL, "1d"): daily},
        position_qty={SYMBOL: qty} if qty else {},
        bars_held={SYMBOL: held} if held else {},
    )
    strategy = BtcTrendMomentumStrategy()
    strategy.initialize(config or CONFIG, ctx)
    last = hourly.iloc[-1]
    strategy.on_bar(Bar(
        symbol=SYMBOL, interval="1h",
        timestamp=hourly.index[-1].to_pydatetime(),
        open=float(last["open"]), high=float(last["high"]),
        low=float(last["low"]), close=float(last["close"]),
        volume=float(last["volume"]),
    ))
    return strategy


def test_long_breakout_in_daily_uptrend():
    hourly = hourly_frame(chop_closes() + [106.0])   # prior 55-bar high ~101.5
    strategy = make_strategy(hourly, daily_frame(trend=0.002))
    signal = strategy.generate_signal()
    assert signal is not None and signal.direction == "LONG"
    assert signal.symbol == SYMBOL
    assert 0.5 <= signal.confidence <= 1.0
    # sizing/target hints the Risk Engine honors
    assert signal.metadata["risk_per_trade_pct"] == 0.5
    assert signal.metadata["take_profit_r_multiple"] == 3.0
    assert signal.metadata["breakout_atr"] > 0


def test_short_breakdown_in_daily_downtrend():
    hourly = hourly_frame(chop_closes() + [94.0])    # prior 55-bar low ~98.5
    strategy = make_strategy(hourly, daily_frame(trend=-0.002))
    signal = strategy.generate_signal()
    assert signal is not None and signal.direction == "SHORT"


def test_no_signal_inside_channel():
    hourly = hourly_frame(chop_closes() + [100.0])
    strategy = make_strategy(hourly, daily_frame(trend=0.002))
    assert strategy.generate_signal() is None


def test_daily_trend_filter_blocks_counter_trend_breakout():
    # upside breakout while the daily EMA structure is bearish => no long
    hourly = hourly_frame(chop_closes() + [106.0])
    strategy = make_strategy(hourly, daily_frame(trend=-0.002))
    assert strategy.generate_signal() is None


def test_allow_short_false_blocks_breakdown():
    config = {**CONFIG, "allow_short": False}
    hourly = hourly_frame(chop_closes() + [94.0])
    strategy = make_strategy(hourly, daily_frame(trend=-0.002), config=config)
    assert strategy.generate_signal() is None


def test_channel_exit_closes_long():
    # long position, close breaks the prior 20-bar low => FLAT
    hourly = hourly_frame(chop_closes() + [97.0])    # prior 20-bar low ~98.5
    strategy = make_strategy(hourly, daily_frame(trend=0.002), qty=0.1, held=5)
    signal = strategy.generate_signal()
    assert signal is not None and signal.direction == "FLAT"
    assert signal.metadata["reason"] == "channel_exit_long"


def test_channel_exit_closes_short():
    hourly = hourly_frame(chop_closes() + [103.0])   # prior 20-bar high ~101.5
    strategy = make_strategy(hourly, daily_frame(trend=-0.002), qty=-0.1, held=5)
    signal = strategy.generate_signal()
    assert signal is not None and signal.direction == "FLAT"
    assert signal.metadata["reason"] == "channel_exit_short"


def test_time_stop_flattens_stale_position():
    # inside the channel (no channel exit) but held past max_holding_bars
    hourly = hourly_frame(chop_closes() + [100.0])
    strategy = make_strategy(hourly, daily_frame(trend=0.002), qty=0.1, held=240)
    signal = strategy.generate_signal()
    assert signal is not None and signal.direction == "FLAT"
    assert signal.metadata["reason"] == "time_stop"


def test_positioned_no_exit_conditions_returns_none():
    hourly = hourly_frame(chop_closes() + [100.0])
    strategy = make_strategy(hourly, daily_frame(trend=0.002), qty=0.1, held=5)
    assert strategy.generate_signal() is None


def test_insufficient_history_returns_none():
    hourly = hourly_frame(chop_closes(40) + [106.0])   # < min_history_bars
    strategy = make_strategy(hourly, daily_frame(trend=0.002))
    assert strategy.generate_signal() is None
