"""Stage 5 — Confluence scoring (0–100). Weighted sum of independent
confirmations; weights and threshold from configs/validation.yaml.

A signal scoring below threshold dies here — marginal setups are where
the losses live.
"""
from __future__ import annotations

import pandas as pd

from backend.core.events import Signal, StageResult
from backend.data import indicators as ind
from backend.validation.base import ValidationStage
from backend.validation.context import ValidationContext
from backend.validation.scoring import weighted_confluence_score


class ConfluenceScoreStage(ValidationStage):
    name = "confluence_score"

    def validate(self, signal: Signal, context: ValidationContext) -> StageResult:
        if signal.direction == "FLAT":
            return self._skipped("exit signal")

        weights: dict[str, float] = self.config["weights"]
        components: dict[str, float] = {}   # each 0.0–1.0

        daily = context.bars(signal.symbol, "1d")
        hourly = context.bars(signal.symbol, "1h")
        interval_df = context.bars(signal.symbol, str(context.strategy_config["interval"]))
        is_long = signal.direction == "LONG"

        # 1. Higher-timeframe trend agreement
        components["htf_trend_agreement"] = self._htf_trend(daily, is_long)
        # 2. Momentum agreement
        components["momentum_agreement"] = self._momentum(interval_df, is_long)
        # 3. Volume confirmation strength
        components["volume_strength"] = self._volume(interval_df)
        # 4. Distance from nearest support/resistance
        components["sr_distance"] = self._sr_distance(daily, is_long)
        # 5. Market breadth proxy: benchmark vs its session VWAP
        components["market_breadth"] = self._breadth(context, is_long)
        # 6. Volatility band position (mid-band scores highest)
        components["volatility_position"] = self._vol_position(daily)

        score = weighted_confluence_score(
            components, {k: float(v) for k, v in weights.items()})
        # weights were framed for equity pullback entries; a strategy whose
        # entry style structurally zeroes a component (e.g. breakout =>
        # sr_distance 0) may carry its own threshold
        threshold = float(self._override(context, "threshold",
                                         self.config["threshold"]))
        measured = {
            "score": round(score, 1),
            "threshold": threshold,
            "components": {k: round(v, 3) for k, v in components.items()},
        }
        if score >= threshold:
            return StageResult(self.name, True, measured, "ok")
        return StageResult(self.name, False, measured,
                           f"confluence score {score:.1f} below {threshold}")

    # ── component scorers (each returns 0.0–1.0) ─────────────────────────

    def _htf_trend(self, daily: pd.DataFrame, is_long: bool) -> float:
        if len(daily) < 200:
            return 0.0
        price = float(daily["close"].iloc[-1])
        e50 = float(ind.ema(daily["close"], 50).iloc[-1])
        e200 = float(ind.ema(daily["close"], 200).iloc[-1])
        if is_long:
            return (0.5 if price > e200 else 0.0) + (0.5 if e50 > e200 else 0.0)
        return (0.5 if price < e200 else 0.0) + (0.5 if e50 < e200 else 0.0)

    def _momentum(self, df: pd.DataFrame, is_long: bool) -> float:
        mom_cfg = self.config["momentum"]
        period = int(mom_cfg["rsi_period"])
        if len(df) < period + 5:
            return 0.0
        rsi_val = float(ind.rsi(df["close"], period).iloc[-1])
        macd_hist = float(ind.macd(df["close"])["hist"].iloc[-1])
        score = 0.0
        if is_long:
            if macd_hist > 0:
                score += 0.5
            # rising but not overbought against entry
            if 40 <= rsi_val <= float(mom_cfg["rsi_overbought"]):
                score += 0.5
        else:
            if macd_hist < 0:
                score += 0.5
            if float(mom_cfg["rsi_oversold"]) <= rsi_val <= 60:
                score += 0.5
        return score

    def _volume(self, df: pd.DataFrame) -> float:
        if len(df) < 25:
            return 0.0
        rvol = float(ind.relative_volume(df["volume"], 20).iloc[-1])
        # 1.0x → 0.0; 2.0x+ → 1.0
        return max(0.0, min(1.0, rvol - 1.0))

    def _sr_distance(self, daily: pd.DataFrame, is_long: bool) -> float:
        if len(daily) < 30:
            return 0.0
        atr_val = float(ind.atr(daily, 14).iloc[-1])
        price = float(daily["close"].iloc[-1])
        window = daily.tail(20)
        # nearest resistance above (for longs) / support below (for shorts)
        level = float(window["high"].max()) if is_long else float(window["low"].min())
        dist = (level - price) if is_long else (price - level)
        min_atr = float(self.config["sr_distance_atr_min"])
        if atr_val <= 0:
            return 0.0
        return max(0.0, min(1.0, dist / (atr_val * min_atr)))

    def _breadth(self, context: ValidationContext, is_long: bool) -> float:
        bench = context.bars(context.benchmark_symbol, "1d")
        if len(bench) < 2:
            return 0.5   # neutral when benchmark unavailable
        v = ind.vwap(bench.tail(20))
        above = float(bench["close"].iloc[-1]) > float(v.iloc[-1])
        return 1.0 if above == is_long else 0.0

    def _vol_position(self, daily: pd.DataFrame) -> float:
        if len(daily) < 30:
            return 0.0
        atr_series = ind.atr(daily, 14).dropna()
        pct = float((atr_series.iloc[:-1] <= atr_series.iloc[-1]).mean() * 100)
        # mid-band (55) scores highest, tapering to 0 at the extremes
        return max(0.0, 1.0 - abs(pct - 55.0) / 55.0)
