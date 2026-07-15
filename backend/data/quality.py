"""Data-quality checks used by the downloader and by validation Stage 0."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.core.events import Bar

_INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}


def interval_seconds(interval: str) -> int:
    try:
        return _INTERVAL_SECONDS[interval]
    except KeyError as exc:
        raise ValueError(f"Unknown interval: {interval}") from exc


def bar_age_seconds(last_bar: Bar, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return (now - last_bar.timestamp).total_seconds()


def is_stale(last_bar: Bar, max_age_multiplier: float, now: datetime | None = None) -> bool:
    max_age = interval_seconds(last_bar.interval) * max_age_multiplier
    return bar_age_seconds(last_bar, now) > max_age


def count_gaps(bars: list[Bar]) -> int:
    """Number of missing bars in a contiguous window (session gaps for 1d
    and intraday overnight gaps are the caller's responsibility to exclude)."""
    if len(bars) < 2:
        return 0
    step = timedelta(seconds=interval_seconds(bars[0].interval))
    gaps = 0
    for prev, cur in zip(bars, bars[1:]):
        delta = cur.timestamp - prev.timestamp
        if delta > step:
            gaps += int(delta / step) - 1
    return gaps


def has_zero_volume(bars: list[Bar], min_volume: float) -> bool:
    return any(b.volume < min_volume for b in bars)


def prices_sane(bars: list[Bar]) -> bool:
    for b in bars:
        if not (b.low <= b.open <= b.high and b.low <= b.close <= b.high and b.low > 0):
            return False
    return True
