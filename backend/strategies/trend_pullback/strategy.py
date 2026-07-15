"""Trend pullback (MVP §9 #1). TREND_UP only.

Price above daily EMA(200); buy pullbacks to the hourly EMA(20) when
momentum resumes (close back above EMA(20) with RSI recovering).
All parameters from config.yaml — no magic numbers here.
"""
from __future__ import annotations

from backend.core.events import Bar, Signal
from backend.data import indicators as ind
from backend.strategies.base import StrategyBase, StrategyContext


class TrendPullbackStrategy(StrategyBase):
    strategy_id = "trend_pullback"

    def initialize(self, config: dict, context: StrategyContext) -> None:
        self._cfg = config
        self._ctx = context
        self._last_bar: Bar | None = None
        p = config["parameters"]
        self._daily_ema = int(p["daily_ema_period"])
        self._pullback_ema = int(p["pullback_ema_period"])
        self._rsi_period = int(p["rsi_period"])
        self._rsi_resume_min = float(p["rsi_resume_min"])
        self._interval = str(config["interval"])
        self._min_bars = int(p["min_history_bars"])

    def on_bar(self, bar: Bar) -> None:
        self._last_bar = bar
        # context history frames are updated by the runtime before on_bar

    def generate_signal(self) -> Signal | None:
        if self._last_bar is None:
            return None
        bar = self._last_bar
        if self._ctx.qty(bar.symbol) != 0:
            # already positioned: exits are the stop/target's job, and adding
            # to a winner here would be pyramiding the same signal
            return None
        hourly = self._ctx.bars(bar.symbol, self._interval)
        daily = self._ctx.bars(bar.symbol, "1d")
        if len(hourly) < self._min_bars or len(daily) < self._daily_ema:
            return None

        daily_ema = float(ind.ema(daily["close"], self._daily_ema).iloc[-1])
        pull_ema = ind.ema(hourly["close"], self._pullback_ema)
        rsi = ind.rsi(hourly["close"], self._rsi_period)

        above_trend = bar.close > daily_ema
        # pullback: previous close at/below EMA(20); resume: current close above it
        pulled_back = float(hourly["close"].iloc[-2]) <= float(pull_ema.iloc[-2])
        resumed = bar.close > float(pull_ema.iloc[-1])
        momentum_ok = float(rsi.iloc[-1]) > self._rsi_resume_min

        if above_trend and pulled_back and resumed and momentum_ok:
            depth = abs(bar.close - float(pull_ema.iloc[-1])) / bar.close
            confidence = max(0.5, min(1.0, 0.6 + float(rsi.iloc[-1]) / 200 - depth))
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                direction="LONG",
                confidence=round(confidence, 3),
                bar_time=bar.timestamp,
                metadata={
                    "daily_ema": round(daily_ema, 4),
                    "pullback_ema": round(float(pull_ema.iloc[-1]), 4),
                    "rsi": round(float(rsi.iloc[-1]), 2),
                },
            )
        return None
