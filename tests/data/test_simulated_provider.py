"""Simulated provider: deterministic, OHLC-sane, correctly windowed."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from backend.data.simulated_provider import (
    SESSION_UTC_HOURS, SimulatedDataProvider, session_hour_timestamps,
)

START = datetime(2026, 3, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 30, 23, 0, tzinfo=timezone.utc)


def _bars(symbol="AAPL", interval="1h", start=START, end=END):
    provider = SimulatedDataProvider()
    return asyncio.run(provider.get_bars(symbol, interval, start, end))


def test_deterministic_across_instances():
    a = _bars()
    b = _bars()
    assert len(a) == len(b) > 0
    assert all(x.close == y.close and x.timestamp == y.timestamp
               for x, y in zip(a, b))


def test_different_symbols_differ():
    aapl = _bars("AAPL")
    msft = _bars("MSFT")
    assert aapl[-1].close != msft[-1].close


def test_ohlc_sane_and_monotonic():
    bars = _bars()
    for prev, cur in zip(bars, bars[1:]):
        assert cur.timestamp > prev.timestamp
    for bar in bars:
        assert bar.low <= bar.open <= bar.high
        assert bar.low <= bar.close <= bar.high
        assert bar.volume > 0


def test_window_is_respected():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 5, 23, 0, tzinfo=timezone.utc)
    bars = _bars(start=start, end=end)
    assert bars
    assert all(start <= b.timestamp <= end for b in bars)


def test_hourly_bars_are_session_hours_on_weekdays_only():
    for bar in _bars():
        assert bar.timestamp.weekday() < 5
        assert bar.timestamp.hour in SESSION_UTC_HOURS


def test_daily_bars_available_for_regime_lookback():
    bars = _bars(interval="1d", start=END - timedelta(days=900), end=END)
    assert len(bars) >= 400        # enough for EMA200 + vol percentile history


def test_session_hour_timestamps_helper():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)          # a Monday
    stamps = session_hour_timestamps(start, start + timedelta(days=1, hours=23))
    assert stamps[0].hour == SESSION_UTC_HOURS[0]
    assert len(stamps) == len(SESSION_UTC_HOURS) * 2           # Mon + Tue
