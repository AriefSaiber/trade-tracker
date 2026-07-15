"""Simulated market data provider — deterministic synthetic OHLCV.

Lets the whole platform paper-trade end to end with ZERO external API keys:
``configs/market.yaml: provider: simulated`` (the out-of-the-box default).
Switching to Alpaca later touches configuration only — never strategy code
(DataProvider interface, CLAUDE.md §5).

Bars are generated deterministically per symbol (seeded by the symbol name):
a long-term uptrend with sinusoidal pullbacks on the intraday series and
volume spikes on pullback-resume bars — the regime classifies TREND_UP and
the baseline strategies genuinely fire, so the full funnel is exercised.

Simulated bars carry *simulated* session timestamps (weekday 14:00–19:00 UTC
== 10:00–15:00 ET). The worker derives its clock from the bar stream in this
mode — exactly the same point-in-time discipline the backtester uses.
"""
from __future__ import annotations

import zlib
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import numpy as np
import pandas as pd
import structlog

from backend.core.config import YamlConfig, load_yaml_config
from backend.core.events import Bar
from backend.data.provider import DataProvider

log = structlog.get_logger(__name__)

# UTC hours mapping to 10:00–15:00 ET during EDT — inside the session, clear of
# the open/close blackout windows enforced by validation stage 6.
SESSION_UTC_HOURS = (14, 15, 16, 17, 18, 19)


def _symbol_seed(symbol: str) -> int:
    """Stable per-symbol seed (hash() is salted per process; crc32 is not)."""
    return zlib.crc32(symbol.encode("utf-8"))


def session_hour_timestamps(start: datetime, end: datetime) -> list[datetime]:
    out: list[datetime] = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        if day.weekday() < 5:
            for hour in SESSION_UTC_HOURS:
                ts = day.replace(hour=hour)
                if start <= ts <= end:
                    out.append(ts)
        day += timedelta(days=1)
    return out


class SimulatedDataProvider(DataProvider):
    """Deterministic synthetic bars for any symbol, any window inside range.

    The full daily + hourly series for a symbol is generated once (anchored to
    ``anchor`` so runs are reproducible) and sliced per request.
    """

    def __init__(self, config: YamlConfig | None = None,
                 anchor: datetime | None = None) -> None:
        cfg = config or load_yaml_config("market")
        sim = cfg.get("simulator", {}) or {}
        self._daily_days = int(sim.get("history_days", 600))
        self._daily_trend = float(sim.get("daily_trend", 0.002))
        self._hourly_drift = float(sim.get("hourly_drift", 0.0012))
        self._pullback_amp = float(sim.get("pullback_amplitude", 0.015))
        self._pullback_period = int(sim.get("pullback_period_bars", 10))
        self._start_price = float(sim.get("start_price", 100.0))
        # Anchor = last generated timestamp. Fixed default keeps every run of
        # the simulator reproducible bar-for-bar.
        self._anchor = anchor or datetime(2026, 6, 30, tzinfo=timezone.utc)
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    # ── generation ─────────────────────────────────────────────────────────
    def _daily_frame(self, symbol: str) -> pd.DataFrame:
        key = (symbol, "1d")
        if key not in self._cache:
            rng = np.random.default_rng(_symbol_seed(symbol))
            n = self._daily_days
            rets = self._daily_trend + rng.normal(0, 0.01, n)
            close = self._start_price * np.cumprod(1 + rets)
            high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
            low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
            open_ = np.clip(close * (1 + rng.normal(0, 0.003, n)), low, high)
            volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
            idx = pd.date_range(end=self._anchor, periods=n, freq="B", tz="UTC")
            self._cache[key] = pd.DataFrame(
                {"open": open_, "high": high, "low": low,
                 "close": close, "volume": volume},
                index=idx,
            )
        return self._cache[key]

    def _hourly_frame(self, symbol: str) -> pd.DataFrame:
        key = (symbol, "1h")
        if key not in self._cache:
            daily = self._daily_frame(symbol)
            base = float(daily["close"].iloc[-1]) * 0.85
            start = self._anchor - timedelta(days=120)
            stamps = session_hour_timestamps(start, self._anchor + timedelta(hours=23))
            n = len(stamps)
            i = np.arange(n)
            trend = base * (1 + self._hourly_drift) ** i
            close = trend * (1 + self._pullback_amp
                             * np.sin(2 * np.pi * i / self._pullback_period))
            high = close * 1.0015
            low = close * 0.9985
            open_ = close * 0.9995
            volume = np.full(n, 1_000_000.0)
            # volume spike on each pullback-resume bar so RVOL (stage 3) clears
            ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().to_numpy()
            for k in range(1, n):
                if close[k] > ema20[k] and close[k - 1] <= ema20[k - 1]:
                    volume[k] = 1_000_000.0 * 2.8
            self._cache[key] = pd.DataFrame(
                {"open": open_, "high": high, "low": low,
                 "close": close, "volume": volume},
                index=pd.DatetimeIndex(stamps),
            )
        return self._cache[key]

    def _frame(self, symbol: str, interval: str) -> pd.DataFrame:
        if interval == "1d":
            return self._daily_frame(symbol)
        if interval == "1h":
            return self._hourly_frame(symbol)
        raise ValueError(f"simulated provider supports 1h/1d, got {interval!r}")

    # ── DataProvider interface ─────────────────────────────────────────────
    async def get_bars(self, symbol: str, interval: str,
                       start: datetime, end: datetime) -> list[Bar]:
        df = self._frame(symbol, interval)
        window = df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
        return [
            Bar(symbol, interval, ts.to_pydatetime(),
                float(r.open), float(r.high), float(r.low),
                float(r.close), float(r.volume))
            for ts, r in window.iterrows()
        ]

    async def subscribe_live(
        self, symbols: list[str], callback: Callable[[Bar], Awaitable[None]]
    ) -> None:
        raise NotImplementedError(
            "simulated mode is poll-driven: the worker advances the sim clock "
            "and calls get_bars — see backend/worker.py"
        )
