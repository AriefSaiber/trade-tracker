"""Market Regime Detector (MVP §7).

Deterministic classification from daily bars of a symbol/benchmark.
All thresholds come from configs/market.yaml (the ``regime:`` section).

The detector is pure and reproducible: :meth:`classify` computes ADX(14),
EMA(50), EMA(200) slope and a realized-volatility percentile from OHLCV bars
and labels the current regime. :meth:`update` layers stateful behaviour on top:
it publishes a ``regime`` event on the shared event bus only when the label
changes for a symbol, so downstream consumers see transitions, not noise.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import structlog

from backend.core.config import YamlConfig, load_yaml_config
from backend.core.event_bus import TOPIC_REGIME, EventBus
from backend.core.events import Bar, Regime, RegimeState
from backend.data import indicators as ind

log = structlog.get_logger(__name__)

_OHLCV = ("open", "high", "low", "close", "volume")


def bars_to_frame(bars: Iterable[Bar]) -> pd.DataFrame:
    """Convert an iterable of :class:`Bar` into an ascending-time OHLCV frame."""
    rows = sorted(bars, key=lambda b: b.timestamp)
    frame = pd.DataFrame(
        {
            "open": [b.open for b in rows],
            "high": [b.high for b in rows],
            "low": [b.low for b in rows],
            "close": [b.close for b in rows],
            "volume": [b.volume for b in rows],
        },
        index=pd.DatetimeIndex([b.timestamp for b in rows]),
    )
    return frame


class RegimeDetector:
    """Classify the prevailing market regime and announce changes.

    ``config`` defaults to ``configs/market.yaml``; ``bus`` is optional and only
    required when :meth:`update` is used to publish regime-change events.
    """

    def __init__(
        self,
        config: YamlConfig | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._cfg = config or load_yaml_config("market")
        self._bus = bus
        self._last: dict[str, RegimeState] = {}

    # ------------------------------------------------------------------ config
    def _threshold(self, key: str, default: float | int) -> float | int:
        """Read a threshold from the ``regime:`` section, falling back to a
        flat top-level key (keeps hand-built test configs working)."""
        val = self._cfg.get(f"regime.{key}")
        if val is None:
            val = self._cfg.get(key, default)
        return val

    # ------------------------------------------------------------- computation
    def compute_metrics(self, daily: pd.DataFrame) -> dict:
        """Point-in-time indicator snapshot for the last row of ``daily``.

        Returns raw floats plus a rounded ``metrics`` dict for reporting.
        """
        adx_period = int(self._threshold("adx_period", 14))
        ema_fast_p = int(self._threshold("ema_fast", 50))
        ema_slow_p = int(self._threshold("ema_slow", 200))
        slope_lb = int(self._threshold("ema_slope_lookback", 5))
        rv_period = int(self._threshold("realized_vol_period", 20))
        rv_lookback = int(self._threshold("realized_vol_lookback_days", 252))

        adx_val = float(ind.adx(daily, adx_period).iloc[-1])
        ema_fast = float(ind.ema(daily["close"], ema_fast_p).iloc[-1])
        ema_slow = float(ind.ema(daily["close"], ema_slow_p).iloc[-1])
        slope = float(ind.ema_slope(daily["close"], ema_fast_p, slope_lb).iloc[-1])
        rv = ind.realized_volatility(daily["close"], rv_period)
        vol_pct_series = ind.rolling_percentile_rank(rv, rv_lookback)
        last_pct = vol_pct_series.iloc[-1]
        vol_pct = float(last_pct) if not pd.isna(last_pct) else 50.0

        return {
            "adx": adx_val,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_slope": slope,
            "vol_percentile": vol_pct,
        }

    def _label(
        self,
        adx_val: float,
        ema_fast: float,
        ema_slow: float,
        slope: float,
        vol_pct: float,
    ) -> Regime:
        """Pure threshold classifier — deterministic given the metrics."""
        high_vol_pct = float(self._threshold("high_vol_percentile", 90))
        adx_trend_min = float(self._threshold("adx_trend_min", 25))
        adx_range_max = float(self._threshold("adx_range_max", 20))

        if vol_pct > high_vol_pct:
            return Regime.HIGH_VOL              # overrides everything else
        if adx_val > adx_trend_min and ema_fast > ema_slow and slope > 0:
            return Regime.TREND_UP
        if adx_val > adx_trend_min and ema_fast < ema_slow and slope < 0:
            return Regime.TREND_DOWN
        if adx_val < adx_range_max:
            return Regime.RANGE
        return Regime.TRANSITION

    # -------------------------------------------------------------- public API
    def classify(
        self,
        daily: pd.DataFrame | Iterable[Bar],
        symbol: str,
        as_of: datetime | None = None,
    ) -> RegimeState:
        """Classify the current regime for ``symbol``.

        ``daily`` is either an OHLCV DataFrame (ascending time index) or an
        iterable of :class:`Bar`. Only the trailing rows are used, so callers
        must pass point-in-time data (rows <= ``as_of``).
        """
        if not isinstance(daily, pd.DataFrame):
            daily = bars_to_frame(daily)

        m = self.compute_metrics(daily)
        regime = self._label(
            m["adx"], m["ema_fast"], m["ema_slow"], m["ema_slope"], m["vol_percentile"]
        )
        metrics = {
            "adx": round(m["adx"], 2),
            "ema_fast": round(m["ema_fast"], 4),
            "ema_slow": round(m["ema_slow"], 4),
            "ema_slope": round(m["ema_slope"], 6),
            "vol_percentile": round(m["vol_percentile"], 1),
        }
        state = RegimeState(
            symbol=symbol,
            regime=regime,
            as_of=as_of or datetime.now(timezone.utc),
            metrics=metrics,
        )
        log.info("regime_classified", symbol=symbol, regime=regime.value, **metrics)
        return state

    async def update(
        self,
        daily: pd.DataFrame | Iterable[Bar],
        symbol: str,
        as_of: datetime | None = None,
    ) -> RegimeState:
        """Classify and publish a ``regime`` event when the label changes.

        The first observation for a symbol always publishes; subsequent
        observations publish only on a regime transition. Requires a bus.
        """
        state = self.classify(daily, symbol, as_of)
        prev = self._last.get(symbol)
        self._last[symbol] = state
        if self._bus is not None and (prev is None or prev.regime != state.regime):
            log.info(
                "regime_changed",
                symbol=symbol,
                previous=prev.regime.value if prev else None,
                regime=state.regime.value,
            )
            await self._bus.publish(TOPIC_REGIME, state)
        return state

    def current(self, symbol: str) -> RegimeState | None:
        """Last classified state for ``symbol`` (via :meth:`update`), if any."""
        return self._last.get(symbol)
