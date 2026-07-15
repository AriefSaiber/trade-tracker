"""Deterministic market-data builders and context factory for validation tests."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from backend.core.events import Position, Regime, RegimeState
from backend.validation.context import ValidationContext

END_DAY = "2026-07-10"           # matches the global `now` fixture's date
END_HOUR = "2026-07-10 15:00"    # == the global `now` fixture

STRATEGY_CFG = {
    "strategy_id": "trend_pullback",
    "interval": "1h",
    "allowed_regimes": ["TREND_UP"],
    "opt_in_high_vol": False,
}


def make_hourly_frame(hours: int = 400, trend: float = 0.0004, seed: int = 11,
                      start_price: float = 100.0, end: str = END_HOUR,
                      last_volume_mult: float = 1.0) -> pd.DataFrame:
    """Synthetic hourly OHLCV ending exactly at `end` (UTC).

    `last_volume_mult` sets the final bar's volume as a multiple of the
    prior 20-bar average — the exact quantity RVOL measures.
    """
    rng = np.random.default_rng(seed)
    rets = trend + rng.normal(0, 0.002, hours)
    close = start_price * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.002, hours)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, hours)))
    open_ = np.clip(close * (1 + rng.normal(0, 0.001, hours)), low, high)
    volume = rng.integers(500_000, 1_500_000, hours).astype(float)
    if last_volume_mult != 1.0:
        volume[-1] = volume[-21:-1].mean() * last_volume_mult
    idx = pd.date_range(end=end, periods=hours, freq="h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def make_spread_frame(spreads, price: float = 100.0, end: str = END_DAY) -> pd.DataFrame:
    """Flat-close daily frame whose high-low spreads drive ATR exactly."""
    spreads = np.asarray(spreads, dtype=float)
    n = len(spreads)
    close = np.full(n, price)
    idx = pd.date_range(end=end, periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {"open": close, "high": close + spreads, "low": close - spreads,
         "close": close, "volume": np.full(n, 1_000_000.0)},
        index=idx,
    )


def make_ctx(now: datetime, *, regime: Regime = Regime.TREND_UP,
             history: dict | None = None, strategy_config: dict | None = None,
             open_positions: list[Position] | None = None, equity: float = 100_000.0,
             earnings: dict | None = None, sectors: dict | None = None) -> ValidationContext:
    return ValidationContext(
        now=now,
        regime=RegimeState("SPY", regime, now, {"adx": 30.0}),
        benchmark_symbol="SPY",
        history=history or {},
        strategy_config=strategy_config or dict(STRATEGY_CFG),
        open_positions=open_positions or [],
        equity=equity,
        earnings_calendar=earnings or {},
        sector_map=sectors or {},
    )
