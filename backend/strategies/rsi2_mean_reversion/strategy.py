"""Short-term mean reversion in uptrends (MVP §9 #2). TREND_UP / RANGE.

RSI(2) < entry threshold with price above daily EMA(200). Exit handled by
risk-engine stops plus RSI(2) > exit threshold or time stop (runtime exit
signals emitted as FLAT).
"""
from __future__ import annotations

from backend.core.events import Bar, Signal
from backend.data import indicators as ind
from backend.strategies.base import StrategyBase, StrategyContext


class Rsi2MeanReversionStrategy(StrategyBase):
    strategy_id = "rsi2_mean_reversion"

    def initialize(self, config: dict, context: StrategyContext) -> None:
        self._cfg = config
        self._ctx = context
        self._last_bar: Bar | None = None
        p = config["parameters"]
        self._rsi_period = int(p["rsi_period"])
        self._entry_below = float(p["entry_rsi_below"])
        self._exit_above = float(p["exit_rsi_above"])
        self._trend_ema = int(p["trend_ema_period"])
        self._time_stop_bars = int(p.get("time_stop_bars", 0))  # 0 = disabled
        self._interval = str(config["interval"])

    def on_bar(self, bar: Bar) -> None:
        self._last_bar = bar

    def generate_signal(self) -> Signal | None:
        if self._last_bar is None:
            return None
        bar = self._last_bar
        bars = self._ctx.bars(bar.symbol, self._interval)
        daily = self._ctx.bars(bar.symbol, "1d")
        if len(bars) < self._rsi_period + 5 or len(daily) < self._trend_ema:
            return None

        rsi2 = float(ind.rsi(bars["close"], self._rsi_period).iloc[-1])
        trend_ema = float(ind.ema(daily["close"], self._trend_ema).iloc[-1])
        in_position = self._ctx.qty(bar.symbol) != 0

        if in_position:
            # Exit on RSI recovery OR the configured time stop — mean-reversion
            # trades that neither bounce nor stop out must not linger.
            held = self._ctx.held_bars(bar.symbol)
            time_stop = 0 < self._time_stop_bars <= held
            if rsi2 > self._exit_above or time_stop:
                return Signal(
                    strategy_id=self.strategy_id,
                    symbol=bar.symbol,
                    direction="FLAT",
                    confidence=1.0,
                    bar_time=bar.timestamp,
                    metadata={"rsi2": round(rsi2, 2),
                              "reason": "time_stop" if time_stop else "rsi_exit",
                              "bars_held": held},
                )
            return None

        if bar.close > trend_ema and rsi2 < self._entry_below:
            confidence = min(1.0, (self._entry_below - rsi2) / self._entry_below + 0.5)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                direction="LONG",
                confidence=round(confidence, 3),
                bar_time=bar.timestamp,
                metadata={"rsi2": round(rsi2, 2), "trend_ema": round(trend_ema, 4)},
            )
        return None
