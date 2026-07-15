"""RegimeDetector tests (MVP §7).

Covers each regime label two ways: through the pure threshold classifier
(``_label``) and end-to-end from deterministic synthetic OHLCV bars. Also
covers config loading from ``configs/market.yaml`` and event-bus publishing.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.core.config import YamlConfig, load_yaml_config
from backend.core.event_bus import TOPIC_REGIME, EventBus
from backend.core.events import Bar, Regime
from backend.regime.detector import RegimeDetector, bars_to_frame

from tests.conftest import make_daily_frame

# Thresholds nested under `regime:` — mirrors the shape of configs/market.yaml
# and exercises RegimeDetector._threshold's regime-prefix lookup.
CFG = YamlConfig(name="market", data={"regime": {
    "adx_period": 14, "adx_trend_min": 25, "adx_range_max": 20,
    "ema_fast": 50, "ema_slow": 200, "ema_slope_lookback": 5,
    "realized_vol_period": 20, "realized_vol_lookback_days": 252,
    "high_vol_percentile": 90,
}})

N = 400


def _frame_from_close(close, seed: int = 1) -> pd.DataFrame:
    """Wrap a deterministic close path in a plausible OHLCV frame."""
    rng = np.random.default_rng(seed)
    close = np.asarray(close, float)
    n = len(close)
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.clip(close * (1 + rng.normal(0, 0.002, n)), low, high)
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.date_range(end="2026-07-10", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _range_frame() -> pd.DataFrame:
    # Directionless white noise around a constant => low ADX.
    rng = np.random.default_rng(7)
    return _frame_from_close(100 + rng.normal(0, 1.0, N))


def _high_vol_frame() -> pd.DataFrame:
    # Calm drift for most of the year, then a volatility explosion in the last
    # 25 bars => trailing-window realized-vol percentile pins to 100.
    rng = np.random.default_rng(9)
    calm = 100 * np.cumprod(1 + rng.normal(0.0005, 0.004, N - 25))
    spike = calm[-1] * np.cumprod(1 + rng.normal(0, 0.05, 25))
    return _frame_from_close(np.concatenate([calm, spike]), seed=2)


def _transition_frame() -> pd.DataFrame:
    # Mild uptrend that lands ADX between the range and trend thresholds
    # (20 < ADX < 25) with quiet volatility => the TRANSITION fallback.
    rng = np.random.default_rng(2)
    close = 100 * np.cumprod(1 + (0.0008 + rng.normal(0, 0.008, N)))
    return _frame_from_close(close, seed=2)


# --------------------------------------------------------------- data-driven

def test_strong_uptrend_classified_trend_up():
    daily = make_daily_frame(days=N, trend=0.004, seed=3)
    state = RegimeDetector(CFG).classify(daily, "SPY")
    assert state.regime == Regime.TREND_UP


def test_strong_downtrend_classified_trend_down():
    daily = make_daily_frame(days=N, trend=-0.004, seed=3)
    state = RegimeDetector(CFG).classify(daily, "SPY")
    assert state.regime == Regime.TREND_DOWN


def test_directionless_market_classified_range():
    state = RegimeDetector(CFG).classify(_range_frame(), "SPY")
    assert state.regime == Regime.RANGE
    assert state.metrics["adx"] < 20


def test_volatility_spike_classified_high_vol():
    state = RegimeDetector(CFG).classify(_high_vol_frame(), "SPY")
    assert state.regime == Regime.HIGH_VOL
    assert state.metrics["vol_percentile"] > 90


def test_ambiguous_market_classified_transition():
    state = RegimeDetector(CFG).classify(_transition_frame(), "SPY")
    assert state.regime == Regime.TRANSITION
    assert 20 <= state.metrics["adx"] <= 25


# ---------------------------------------------------------- pure classifier

@pytest.mark.parametrize(
    "adx, ema_fast, ema_slow, slope, vol_pct, expected",
    [
        (30.0, 105.0, 100.0, 0.5, 50.0, Regime.TREND_UP),
        (30.0, 95.0, 100.0, -0.5, 50.0, Regime.TREND_DOWN),
        (10.0, 100.0, 100.0, 0.0, 50.0, Regime.RANGE),
        (30.0, 105.0, 100.0, 0.5, 95.0, Regime.HIGH_VOL),   # vol overrides trend
        (22.0, 105.0, 100.0, 0.1, 50.0, Regime.TRANSITION),  # 20<adx<25, no clean trend
    ],
)
def test_label_thresholds(adx, ema_fast, ema_slow, slope, vol_pct, expected):
    det = RegimeDetector(CFG)
    assert det._label(adx, ema_fast, ema_slow, slope, vol_pct) is expected


# --------------------------------------------------------------- properties

def test_deterministic_same_input_same_output():
    daily = make_daily_frame(days=N, trend=0.002, seed=11)
    a = RegimeDetector(CFG).classify(daily, "SPY")
    b = RegimeDetector(CFG).classify(daily, "SPY")
    assert a.regime == b.regime
    assert a.metrics == b.metrics


def test_metrics_reported():
    daily = make_daily_frame(days=N, trend=0.002, seed=11)
    state = RegimeDetector(CFG).classify(daily, "SPY")
    for key in ("adx", "ema_fast", "ema_slow", "ema_slope", "vol_percentile"):
        assert key in state.metrics


def test_thresholds_load_from_market_yaml():
    # Default construction must read configs/market.yaml's regime: section.
    det = RegimeDetector()
    assert int(det._threshold("adx_period", -1)) == 14
    assert float(det._threshold("high_vol_percentile", -1)) == 90
    state = det.classify(make_daily_frame(days=N, trend=0.004, seed=3), "SPY")
    assert state.regime == Regime.TREND_UP


def test_accepts_bar_objects():
    frame = make_daily_frame(days=N, trend=0.004, seed=3)
    bars = [
        Bar("SPY", "1d", ts.to_pydatetime(), row.open, row.high, row.low,
            row.close, row.volume)
        for ts, row in frame.iterrows()
    ]
    from_bars = RegimeDetector(CFG).classify(bars, "SPY")
    from_frame = RegimeDetector(CFG).classify(frame, "SPY")
    assert from_bars.regime == from_frame.regime == Regime.TREND_UP
    assert bars_to_frame(bars).shape == frame.shape


# --------------------------------------------------------------- event bus

def test_update_publishes_regime_change_to_bus():
    bus = EventBus()
    received: list = []

    async def handler(state):
        received.append(state)

    bus.subscribe(TOPIC_REGIME, handler)
    det = RegimeDetector(CFG, bus=bus)

    up = make_daily_frame(days=N, trend=0.004, seed=3)
    down = make_daily_frame(days=N, trend=-0.004, seed=3)

    async def scenario():
        await det.update(up, "SPY")      # first observation -> publish
        await det.update(up, "SPY")      # same regime -> no publish
        await det.update(down, "SPY")    # regime changed -> publish

    asyncio.run(scenario())

    assert [s.regime for s in received] == [Regime.TREND_UP, Regime.TREND_DOWN]
    assert det.current("SPY").regime == Regime.TREND_DOWN


def test_update_without_bus_is_noop_but_tracks_state():
    det = RegimeDetector(CFG)  # no bus
    state = asyncio.run(det.update(make_daily_frame(days=N, trend=0.004, seed=3), "SPY"))
    assert state.regime == Regime.TREND_UP
    assert det.current("SPY").regime == Regime.TREND_UP
