"""Stage 3 — Volume confirmation: RVOL and OBV agreement. Skip-able per strategy."""
from __future__ import annotations

from backend.core.events import Signal, StageResult
from backend.data import indicators as ind
from backend.validation.base import ValidationStage
from backend.validation.context import ValidationContext


class VolumeConfirmationStage(ValidationStage):
    name = "volume_confirmation"

    def validate(self, signal: Signal, context: ValidationContext) -> StageResult:
        overrides = context.strategy_config.get("validation_overrides", {}) or {}
        if (overrides.get(self.name) or {}).get("skip", False):
            return self._skipped("skipped by strategy config")
        if signal.direction == "FLAT":
            return self._skipped("exit signal")

        interval = str(context.strategy_config["interval"])
        df = context.bars(signal.symbol, interval)
        lookback = int(self.config["rvol_lookback_bars"])
        if len(df) < lookback + 2:
            return StageResult(self.name, False, {"bars": len(df)},
                               "insufficient history for RVOL")

        rvol = float(ind.relative_volume(df["volume"], lookback).iloc[-1])
        rvol_min = float(self.config["relative_volume_min"])

        obv_series = ind.obv(df)
        obv_slope = float(obv_series.iloc[-1] - obv_series.iloc[-min(lookback, len(df))])
        obv_agrees = obv_slope > 0 if signal.direction == "LONG" else obv_slope < 0

        measured = {"rvol": round(rvol, 2), "rvol_min": rvol_min,
                    "obv_slope": round(obv_slope, 1), "obv_agrees": obv_agrees}
        if rvol < rvol_min:
            return StageResult(self.name, False, measured, "relative volume below minimum")
        if bool(self.config.get("require_obv_agreement", True)) and not obv_agrees:
            return StageResult(self.name, False, measured, "OBV disagrees with direction")
        return StageResult(self.name, True, measured, "ok")
