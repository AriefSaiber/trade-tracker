"""Algorithm GPT-Pro unit tests (algorithm_model/STRATEGY_SPEC.md rules).

Deterministic synthetic daily frames exercise each spec filter in isolation:
market regime, trend, pullback, momentum percentile, liquidity, breakout
trigger, point-in-time discipline (today's forming daily bar excluded), and
the FLAT exits (time stop, optional trend exit, ownership guard).
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.core.events import Bar, Regime
from backend.strategies.base import StrategyContext
from backend.strategies.gpt_pro.strategy import GptProStrategy

SYM = "AAPL"
BENCH = "SPY"
PEERS = ["P1", "P2", "P3"]

# Friday 2026-06-26 15:00 UTC == 11:00 ET (inside the session);
# daily history ends Thursday 2026-06-25 — all rows are completed days.
BAR_TIME = datetime(2026, 6, 26, 15, 0, tzinfo=timezone.utc)
DAILY_END = "2026-06-25"

CONFIG = {
    "strategy_id": "gpt_pro",
    "class": "backend.strategies.gpt_pro.strategy.GptProStrategy",
    "enabled": True,
    "symbols": [SYM, *PEERS],
    "interval": "1h",
    "allowed_regimes": ["TREND_UP"],
    "opt_in_high_vol": False,
    "risk_per_trade_pct": 0.5,
    "parameters": {
        "benchmark_symbol": BENCH,
        "min_price": 5.0,
        "min_average_dollar_volume": 20_000_000,
        "max_atr_fraction": 0.08,
        "adv_days": 20,
        "atr_days": 20,
        "momentum_lookback_days": 252,
        "momentum_skip_days": 21,
        "momentum_percentile_min": 0.80,
        "market_sma_days": 200,
        "trend_sma_fast_days": 50,
        "trend_sma_mid_days": 100,
        "trend_sma_slow_days": 200,
        "pullback_sma_days": 10,
        "tick_size": 0.01,
        "target_r_multiple": 1.25,
        "max_holding_days": 60,
        "bars_per_day": 6,
        "use_trend_exit": False,
        "trend_exit_sma_days": 20,
    },
}


def make_frame(days: int = 300, ret: float = 0.003, dip_last: int = 0,
               dip: float = 0.97, volume: float = 5_000_000.0,
               start_price: float = 100.0) -> pd.DataFrame:
    """Deterministic daily OHLCV: steady compounding uptrend; optionally the
    last ``dip_last`` closes drop to ``dip`` x the preceding close (a
    short-term pullback below SMA10 that leaves the long trend intact)."""
    close = start_price * (1 + ret) ** np.arange(days, dtype=float)
    if dip_last:
        close[-dip_last:] = close[-dip_last - 1] * dip
    high = close * 1.005
    low = close * 0.995
    open_ = close * 0.999
    idx = pd.date_range(end=DAILY_END, periods=days, freq="B", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": np.full(days, volume)},
        index=idx,
    )


def make_ctx(frames: dict[str, pd.DataFrame]) -> StrategyContext:
    return StrategyContext(
        now=BAR_TIME,
        regime=Regime.TREND_UP,
        history={(sym, "1d"): df for sym, df in frames.items()},
    )


def make_bar(high: float, symbol: str = SYM) -> Bar:
    return Bar(symbol, "1h", BAR_TIME,
               open=high * 0.995, high=high, low=high * 0.99,
               close=high * 0.998, volume=1_000_000.0)


def passing_frames() -> dict[str, pd.DataFrame]:
    """SYM: uptrend with a fresh 3-day pullback; peers: flat (momentum
    laggards); benchmark: clean uptrend above its SMA200."""
    frames = {SYM: make_frame(dip_last=3), BENCH: make_frame()}
    for p in PEERS:
        frames[p] = make_frame(ret=0.0)
    return frames


def make_strategy(frames: dict[str, pd.DataFrame],
                  config: dict | None = None) -> GptProStrategy:
    strat = GptProStrategy()
    strat.initialize(config or copy.deepcopy(CONFIG), make_ctx(frames))
    return strat


def breakout_high(frames: dict[str, pd.DataFrame]) -> float:
    """A bar high safely above yesterday's high + tick."""
    return float(frames[SYM]["high"].iloc[-1]) * 1.03


# ── entry rules ────────────────────────────────────────────────────────────
def test_emits_long_when_all_filters_pass_and_breakout_triggers():
    frames = passing_frames()
    strat = make_strategy(frames)
    strat.on_bar(make_bar(breakout_high(frames)))
    signal = strat.generate_signal()

    assert signal is not None and signal.direction == "LONG"
    assert signal.strategy_id == "gpt_pro"
    assert 0.5 <= signal.confidence <= 1.0
    # risk-engine overrides ride in metadata (spec: 0.5% risk, 1.25R target)
    assert signal.metadata["risk_per_trade_pct"] == 0.5
    assert signal.metadata["take_profit_r_multiple"] == 1.25
    expected_trigger = float(frames[SYM]["high"].iloc[-1]) + 0.01
    assert signal.metadata["entry_trigger"] == round(expected_trigger, 4)
    assert signal.metadata["momentum_percentile"] == 1.0


def test_no_signal_without_breakout():
    frames = passing_frames()
    strat = make_strategy(frames)
    trigger = float(frames[SYM]["high"].iloc[-1]) + 0.01
    strat.on_bar(make_bar(trigger * 0.98))
    assert strat.generate_signal() is None


def test_no_signal_when_market_below_its_sma():
    frames = passing_frames()
    frames[BENCH] = make_frame(ret=-0.002)   # benchmark downtrend
    strat = make_strategy(frames)
    strat.on_bar(make_bar(breakout_high(frames)))
    assert strat.generate_signal() is None


def test_no_signal_without_pullback():
    frames = passing_frames()
    frames[SYM] = make_frame(dip_last=0)     # close above SMA10 — no pullback
    strat = make_strategy(frames)
    strat.on_bar(make_bar(breakout_high(frames)))
    assert strat.generate_signal() is None


def test_no_signal_when_momentum_percentile_below_minimum():
    frames = passing_frames()
    for p in PEERS:                           # peers now outrun SYM's momentum
        frames[p] = make_frame(ret=0.006)
    strat = make_strategy(frames)
    strat.on_bar(make_bar(breakout_high(frames)))
    assert strat.generate_signal() is None


def test_no_signal_when_illiquid():
    frames = passing_frames()
    frames[SYM] = make_frame(dip_last=3, volume=100.0)   # ADV << $20M
    strat = make_strategy(frames)
    strat.on_bar(make_bar(breakout_high(frames)))
    assert strat.generate_signal() is None


def test_todays_forming_daily_bar_is_excluded():
    """Point-in-time discipline: a pullback visible only in TODAY's daily bar
    must not create a signal — conditions come from completed days only."""
    frames = passing_frames()
    no_dip = make_frame(dip_last=0)
    today_row = pd.DataFrame(
        {"open": [200.0], "high": [200.0], "low": [150.0],
         "close": [150.0], "volume": [5_000_000.0]},   # deep dip, today only
        index=pd.DatetimeIndex([pd.Timestamp("2026-06-26", tz="UTC")]),
    )
    frames[SYM] = pd.concat([no_dip, today_row])
    strat = make_strategy(frames)
    strat.on_bar(make_bar(breakout_high(frames)))
    assert strat.generate_signal() is None


# ── exit rules ─────────────────────────────────────────────────────────────
def _enter_long(strat: GptProStrategy, frames: dict[str, pd.DataFrame]) -> None:
    strat.on_bar(make_bar(breakout_high(frames)))
    assert strat.generate_signal() is not None   # LONG emitted → exits are ours


def test_time_stop_emits_flat_after_max_holding():
    frames = passing_frames()
    strat = make_strategy(frames)
    _enter_long(strat, frames)
    strat._ctx.position_qty[SYM] = 100.0
    strat._ctx.bars_held[SYM] = 60 * 6           # max_holding_days * bars_per_day
    strat.on_bar(make_bar(breakout_high(frames)))
    signal = strat.generate_signal()
    assert signal is not None and signal.direction == "FLAT"
    assert signal.metadata["reason"] == "time_stop"


def test_no_exit_before_time_stop():
    frames = passing_frames()
    strat = make_strategy(frames)
    _enter_long(strat, frames)
    strat._ctx.position_qty[SYM] = 100.0
    strat._ctx.bars_held[SYM] = 10
    strat.on_bar(make_bar(breakout_high(frames)))
    assert strat.generate_signal() is None


def test_does_not_exit_positions_it_did_not_open():
    """Ownership guard: another strategy's position in the same symbol must
    never be time-stopped by this one."""
    frames = passing_frames()
    strat = make_strategy(frames)                # no LONG emitted here
    strat._ctx.position_qty[SYM] = 100.0
    strat._ctx.bars_held[SYM] = 1_000
    strat.on_bar(make_bar(breakout_high(frames)))
    assert strat.generate_signal() is None


def test_trend_exit_when_enabled():
    frames = passing_frames()                    # dip → close < SMA20 too
    config = copy.deepcopy(CONFIG)
    config["parameters"]["use_trend_exit"] = True
    strat = make_strategy(frames, config)
    _enter_long(strat, frames)
    strat._ctx.position_qty[SYM] = 100.0
    strat._ctx.bars_held[SYM] = 10
    strat.on_bar(make_bar(breakout_high(frames)))
    signal = strat.generate_signal()
    assert signal is not None and signal.direction == "FLAT"
    assert signal.metadata["reason"] == "trend_exit"


# ── plugin wiring ──────────────────────────────────────────────────────────
def test_worker_discovers_gpt_pro_plugin():
    from backend.worker import load_strategy_configs

    configs = {c["strategy_id"]: c for c in load_strategy_configs()}
    assert "gpt_pro" in configs
    cfg = configs["gpt_pro"]
    assert cfg["class"] == "backend.strategies.gpt_pro.strategy.GptProStrategy"
    assert cfg["allowed_regimes"] == ["TREND_UP"]
    assert cfg["risk_per_trade_pct"] == 0.5
