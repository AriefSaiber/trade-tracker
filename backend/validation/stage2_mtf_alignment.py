"""Stage 2 — Multi-timeframe alignment: trade with the higher-timeframe tide."""
from __future__ import annotations

from backend.core.events import Signal, StageResult
from backend.data import indicators as ind
from backend.validation.base import ValidationStage
from backend.validation.context import ValidationContext


class MtfAlignmentStage(ValidationStage):
    name = "mtf_alignment"

    def validate(self, signal: Signal, context: ValidationContext) -> StageResult:
        if signal.direction == "FLAT":
            return self._skipped("exit signal")

        daily_ema_p = int(self.config["long_requires_above_daily_ema"])
        hourly_ema_p = int(self.config["long_requires_rising_hourly_ema"])
        slope_lb = int(self.config["ema_slope_lookback"])

        daily = context.bars(signal.symbol, "1d")
        hourly = context.bars(signal.symbol, "1h")
        if len(daily) < daily_ema_p or len(hourly) < hourly_ema_p + slope_lb:
            return StageResult(self.name, False,
                               {"daily_bars": len(daily), "hourly_bars": len(hourly)},
                               "insufficient history for MTF check")

        price = float(daily["close"].iloc[-1])
        daily_ema = float(ind.ema(daily["close"], daily_ema_p).iloc[-1])
        hourly_slope = float(ind.ema_slope(hourly["close"], hourly_ema_p, slope_lb).iloc[-1])

        measured = {
            "price": round(price, 4),
            "daily_ema": round(daily_ema, 4),
            "hourly_ema_slope": round(hourly_slope, 6),
        }
        if signal.direction == "LONG":
            if price > daily_ema and hourly_slope > 0:
                return StageResult(self.name, True, measured, "ok")
            return StageResult(self.name, False, measured,
                               "long against higher-timeframe trend")
        # SHORT mirrored
        if price < daily_ema and hourly_slope < 0:
            return StageResult(self.name, True, measured, "ok")
        return StageResult(self.name, False, measured,
                           "short against higher-timeframe trend")
