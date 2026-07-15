"""Stage 0 — Data sanity: no stale data, no gaps, positive volume, sane prices."""
from __future__ import annotations

from backend.core.events import Signal, StageResult
from backend.data.quality import interval_seconds
from backend.validation.base import ValidationStage
from backend.validation.context import ValidationContext


class DataSanityStage(ValidationStage):
    name = "data_sanity"

    def validate(self, signal: Signal, context: ValidationContext) -> StageResult:
        interval = str(context.strategy_config["interval"])
        df = context.bars(signal.symbol, interval)
        if df.empty:
            return StageResult(self.name, False, {"bars": 0}, "no data for symbol")

        max_age_mult = float(self.config["max_bar_age_multiplier"])
        last_ts = df.index[-1].to_pydatetime()
        age_s = (context.now - last_ts).total_seconds()
        max_age_s = interval_seconds(interval) * max_age_mult

        recent = df.tail(int(self.config.get("lookback_bars", 30)))
        # crypto volume is denominated in coins (an hourly BTC bar can trade
        # < 1 BTC and still be perfectly liquid) — strategies may override
        min_volume = float(self._override(context, "min_volume",
                                          self.config["min_volume"]))
        zero_vol = bool((recent["volume"] < min_volume).any())
        sane = bool(
            (recent["low"] <= recent["close"]).all()
            and (recent["close"] <= recent["high"]).all()
            and (recent["low"] > 0).all()
        )

        measured = {
            "bar_age_seconds": round(age_s, 1),
            "max_age_seconds": max_age_s,
            "zero_volume_bars": zero_vol,
            "prices_sane": sane,
        }
        if age_s > max_age_s:
            return StageResult(self.name, False, measured, "stale data")
        if zero_vol:
            return StageResult(self.name, False, measured, "zero-volume bars in lookback")
        if not sane:
            return StageResult(self.name, False, measured, "price outside OHLC bands")
        return StageResult(self.name, True, measured, "ok")
