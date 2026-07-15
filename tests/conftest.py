from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.events import Regime, RegimeState, Signal  # noqa: E402


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)


def make_daily_frame(days: int = 300, trend: float = 0.001,
                     seed: int = 7, start_price: float = 100.0) -> pd.DataFrame:
    """Deterministic synthetic daily OHLCV."""
    rng = np.random.default_rng(seed)
    rets = trend + rng.normal(0, 0.01, days)
    close = start_price * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.005, days)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, days)))
    open_ = np.clip(close * (1 + rng.normal(0, 0.003, days)), low, high)
    volume = rng.integers(1_000_000, 5_000_000, days).astype(float)
    idx = pd.date_range(end="2026-07-10", periods=days, freq="B", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


@pytest.fixture
def uptrend_daily() -> pd.DataFrame:
    return make_daily_frame(trend=0.0025)


@pytest.fixture
def signal(now: datetime) -> Signal:
    return Signal(
        strategy_id="trend_pullback",
        symbol="NVDA",
        direction="LONG",
        confidence=0.8,
        bar_time=now,
        metadata={},
    )


@pytest.fixture
def regime_trend_up(now: datetime) -> RegimeState:
    return RegimeState("SPY", Regime.TREND_UP, now, {"adx": 30.0})
