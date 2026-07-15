"""Pure, deterministic indicator functions on pandas Series/DataFrames.

Point-in-time discipline: every function returns values aligned to bar t
using data <= t only. No .shift(-1), no forward-looking windows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100.0).where(avg_loss != 0, 100.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_smooth = true_range(df).ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / tr_smooth
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / tr_smooth
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0)


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0.0))
    return (direction * df["volume"]).cumsum()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP: caller must pass a single session's bars."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    return (typical * df["volume"]).cumsum() / cum_vol.replace(0.0, np.nan)


def relative_volume(volume: pd.Series, lookback: int = 20) -> pd.Series:
    # rolling mean EXCLUDES current bar (shift(1)) — no self-inclusion bias
    avg = volume.shift(1).rolling(lookback).mean()
    return volume / avg.replace(0.0, np.nan)


def realized_volatility(close: pd.Series, period: int = 20) -> pd.Series:
    returns = np.log(close / close.shift(1))
    return returns.rolling(period).std() * np.sqrt(252)


def rolling_percentile_rank(series: pd.Series, lookback: int) -> pd.Series:
    """Percentile (0..100) of the current value within its trailing window.

    Vectorized over a sliding window (identical output to the previous
    ``rolling(lookback+1).apply`` form, including the NaN/min-periods
    semantics) but ~100x faster — the Python-level apply was the dominant cost
    when the regime detector reclassifies on every intraday bar of a
    multi-year backtest."""
    arr = series.to_numpy(dtype=float)
    n = arr.size
    w = lookback + 1
    out = np.full(n, np.nan)
    if n >= w:
        windows = np.lib.stride_tricks.sliding_window_view(arr, w)  # (n-w+1, w)
        last = windows[:, -1][:, None]
        frac = np.mean(windows[:, :-1] <= last, axis=1) * 100.0
        # rolling(w) requires a full, NaN-free window (min_periods == w)
        frac[np.isnan(windows).any(axis=1)] = np.nan
        out[w - 1:] = frac
    return pd.Series(out, index=series.index)


def ema_slope(close: pd.Series, period: int, lookback: int) -> pd.Series:
    line = ema(close, period)
    return (line - line.shift(lookback)) / lookback


def donchian(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Donchian channel over the PRIOR `period` bars (shift(1) excludes the
    current bar, so a close above `upper` is a genuine breakout of the
    preceding range, never self-referential)."""
    upper = df["high"].shift(1).rolling(period).max()
    lower = df["low"].shift(1).rolling(period).min()
    return pd.DataFrame({"upper": upper, "lower": lower,
                         "mid": (upper + lower) / 2})
