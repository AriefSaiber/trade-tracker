"""BTC Trend Momentum — Donchian channel breakout with daily-trend filter.

Crypto-native algorithm: classic channel/trend following, the strategy family
with the strongest historical record on Bitcoin specifically. BTC spends most
of its time chopping and then trends violently in both directions; this
strategy stays flat in the chop and enters only when price breaks the prior
N-bar extreme WITH the daily EMA structure agreeing:

- LONG:  close breaks above the prior `donchian_period`-bar high while the
         daily EMA(fast) is above the daily EMA(slow) — macro uptrend.
- SHORT: mirror image (disable with `allow_short: false`); crypto's downtrends
         are long and deep enough that short capability matters.

Exits: the Risk Engine attaches the ATR stop / R-multiple target at entry
(mandatory). The strategy adds two FLAT exits of its own: a close through the
opposite `exit_channel_period`-bar extreme (trend over) and a time stop.

All parameters from config.yaml — no magic numbers here.
"""
from __future__ import annotations

from backend.core.events import Bar, Signal
from backend.data import indicators as ind
from backend.strategies.base import StrategyBase, StrategyContext


class BtcTrendMomentumStrategy(StrategyBase):
    strategy_id = "btc_trend_momentum"

    def initialize(self, config: dict, context: StrategyContext) -> None:
        self._cfg = config
        self._ctx = context
        self._last_bar: Bar | None = None
        p = config["parameters"]
        self._interval = str(config["interval"])
        self._donchian = int(p["donchian_period"])
        self._exit_channel = int(p["exit_channel_period"])
        self._ema_fast = int(p["daily_ema_fast"])
        self._ema_slow = int(p["daily_ema_slow"])
        self._atr_period = int(p["atr_period"])
        self._buffer_atr = float(p["breakout_buffer_atr"])
        self._min_bars = int(p["min_history_bars"])
        self._max_holding_bars = int(p["max_holding_bars"])
        # Indicators only look back donchian/atr bars, but the point-in-time
        # frame grows to the full history each bar; computing over the whole
        # thing is O(n) per bar (O(n²) over a multi-year backtest). Cap the
        # window at a trailing slice large enough that Donchian(N) and the
        # ATR EWM are numerically identical to the full-frame result.
        self._compute_window = int(p.get("compute_window", 400))
        self._allow_short = bool(config.get("allow_short", True))
        self._risk_pct = float(config["risk_per_trade_pct"])
        self._target_r = float(p["take_profit_r_multiple"])

    def on_bar(self, bar: Bar) -> None:
        self._last_bar = bar
        # context history frames are updated by the runtime before on_bar

    def generate_signal(self) -> Signal | None:
        if self._last_bar is None:
            return None
        bar = self._last_bar
        frame = self._ctx.bars(bar.symbol, self._interval)
        if len(frame) < max(self._min_bars, self._donchian + 1):
            return None

        # bounded trailing window — see _compute_window rationale
        work = frame.tail(self._compute_window)
        qty = self._ctx.qty(bar.symbol)
        if qty != 0:
            return self._exit_signal(bar, work, qty)
        return self._entry_signal(bar, work)

    # ── entries ────────────────────────────────────────────────────────────
    def _entry_signal(self, bar: Bar, frame) -> Signal | None:
        daily = self._ctx.bars(bar.symbol, "1d")
        if len(daily) < self._ema_slow:
            return None

        channel = ind.donchian(frame, self._donchian)
        upper = float(channel["upper"].iloc[-1])
        lower = float(channel["lower"].iloc[-1])
        atr_value = float(ind.atr(frame, self._atr_period).iloc[-1])
        if atr_value <= 0:
            return None

        ema_fast = float(ind.ema(daily["close"], self._ema_fast).iloc[-1])
        ema_slow = float(ind.ema(daily["close"], self._ema_slow).iloc[-1])
        buffer = self._buffer_atr * atr_value

        direction: str | None = None
        if bar.close > upper + buffer and ema_fast > ema_slow:
            direction, level = "LONG", upper
        elif self._allow_short and bar.close < lower - buffer and ema_fast < ema_slow:
            direction, level = "SHORT", lower
        if direction is None:
            return None

        # conviction scales with how decisively price cleared the channel
        breakout_atr = abs(bar.close - level) / atr_value
        confidence = max(0.5, min(1.0, 0.5 + breakout_atr * 0.25))
        return Signal(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            direction=direction,
            confidence=round(confidence, 3),
            bar_time=bar.timestamp,
            metadata={
                "channel_upper": round(upper, 2),
                "channel_lower": round(lower, 2),
                "breakout_atr": round(breakout_atr, 3),
                "atr": round(atr_value, 2),
                "daily_ema_fast": round(ema_fast, 2),
                "daily_ema_slow": round(ema_slow, 2),
                # honored by the Risk Engine (fractional crypto sizing)
                "risk_per_trade_pct": self._risk_pct,
                "take_profit_r_multiple": self._target_r,
            },
        )

    # ── exits (stop/target live in the Risk Engine; these are extra) ──────
    def _exit_signal(self, bar: Bar, frame, qty: float) -> Signal | None:
        held = self._ctx.held_bars(bar.symbol)
        if held >= self._max_holding_bars:
            return self._flat(bar, "time_stop", held=held)

        exit_channel = ind.donchian(frame, self._exit_channel)
        if qty > 0 and bar.close < float(exit_channel["lower"].iloc[-1]):
            return self._flat(bar, "channel_exit_long")
        if qty < 0 and bar.close > float(exit_channel["upper"].iloc[-1]):
            return self._flat(bar, "channel_exit_short")
        return None

    def _flat(self, bar: Bar, reason: str, **extra) -> Signal:
        return Signal(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            direction="FLAT",
            confidence=1.0,
            bar_time=bar.timestamp,
            metadata={"reason": reason, **extra},
        )
