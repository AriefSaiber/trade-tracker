"""Stage 4 — Volatility band: ATR percentile must sit inside [min, max].
Too quiet = churn + costs; too wild = stops blown through."""
from __future__ import annotations

import pandas as pd

from backend.core.events import Signal, StageResult
from backend.data import indicators as ind
from backend.validation.base import ValidationStage
from backend.validation.context import ValidationContext


class VolatilityBandStage(ValidationStage):
    name = "volatility_band"

    def validate(self, signal: Signal, context: ValidationContext) -> StageResult:
        if signal.direction == "FLAT":
            return self._skipped("exit signal")

        daily = context.bars(signal.symbol, "1d")
        atr_period = int(self.config["atr_period"])
        lookback = int(self.config["percentile_lookback_days"])
        if len(daily) < atr_period + 10:
            return StageResult(self.name, False, {"bars": len(daily)},
                               "insufficient history for ATR percentile")

        atr_series = ind.atr(daily, atr_period)
        pct_series = ind.rolling_percentile_rank(atr_series, min(lookback, len(daily) - 1))
        pct = pct_series.iloc[-1]
        if pd.isna(pct):
            # not enough window for full percentile — use available history
            valid = atr_series.dropna()
            pct = float((valid.iloc[:-1] <= valid.iloc[-1]).mean() * 100)
        pct = float(pct)

        lo = float(self.config["percentile_min"])
        hi = float(self.config["percentile_max"])
        measured = {"atr_percentile": round(pct, 1), "band": [lo, hi]}
        if pct < lo:
            return StageResult(self.name, False, measured, "volatility too low")
        if pct > hi:
            return StageResult(self.name, False, measured, "volatility too high")
        return StageResult(self.name, True, measured, "ok")
