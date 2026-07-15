"""Algorithm GPT-Pro — robust momentum pullback (algorithm_model/STRATEGY_SPEC.md).

Long-only swing strategy: liquid stocks in the top momentum percentile of the
configured universe, in a long-term uptrend and a favorable market regime,
entered after a short-term pullback when intraday price breaks the prior
day's high (the spec's next-day buy-stop, triggered on the hourly bar that
crosses it). All signal-day conditions use COMPLETED daily bars only — the
current day's forming daily bar is never consulted.

Platform adaptations from the spec (deliberate, not silent):
- Stops/targets are the Risk Engine's job: the ATR stop uses
  configs/risk.yaml stops.atr_period/atr_multiplier; the spec's target_R
  rides along in signal metadata as ``take_profit_r_multiple``.
- Break-even stop moves are not supported by the runtime (a position's stop
  is fixed at fill). Time stop and the optional trend exit are emitted as
  FLAT signals; stop/target exits are enforced by the runtime.
- Portfolio caps (max positions, heat, exposure) are enforced by the Risk
  Engine and validation Stage 7, never here.
"""
from __future__ import annotations

import pandas as pd

from backend.core.events import Bar, Signal
from backend.data import indicators as ind
from backend.strategies.base import StrategyBase, StrategyContext


class GptProStrategy(StrategyBase):
    strategy_id = "gpt_pro"

    def initialize(self, config: dict, context: StrategyContext) -> None:
        self._cfg = config
        self._ctx = context
        self._last_bar: Bar | None = None
        # exits are only ours to manage for positions this instance signaled;
        # another strategy may hold the same symbol (reset when qty returns to 0)
        self._long_emitted = False
        p = config["parameters"]
        self._benchmark = str(p["benchmark_symbol"])
        self._universe = [str(s) for s in config.get("symbols", [])]
        self._min_price = float(p["min_price"])
        self._min_adv = float(p["min_average_dollar_volume"])
        self._max_atr_fraction = float(p["max_atr_fraction"])
        self._adv_days = int(p["adv_days"])
        self._atr_days = int(p["atr_days"])
        self._mom_lookback = int(p["momentum_lookback_days"])
        self._mom_skip = int(p["momentum_skip_days"])
        self._mom_pct_min = float(p["momentum_percentile_min"])
        self._market_sma = int(p["market_sma_days"])
        self._trend_fast = int(p["trend_sma_fast_days"])
        self._trend_mid = int(p["trend_sma_mid_days"])
        self._trend_slow = int(p["trend_sma_slow_days"])
        self._pullback_sma = int(p["pullback_sma_days"])
        self._tick = float(p["tick_size"])
        self._max_holding_days = int(p["max_holding_days"])
        self._bars_per_day = int(p["bars_per_day"])
        self._use_trend_exit = bool(p["use_trend_exit"])
        self._trend_exit_sma = int(p["trend_exit_sma_days"])
        self._risk_pct = float(config["risk_per_trade_pct"])
        self._target_r = float(p["target_r_multiple"])

    def on_bar(self, bar: Bar) -> None:
        self._last_bar = bar

    def generate_signal(self) -> Signal | None:
        if self._last_bar is None:
            return None
        bar = self._last_bar
        if self._ctx.qty(bar.symbol) == 0:
            self._long_emitted = False
            return self._evaluate_entry(bar)
        # positioned: manage time/trend exits only if this instance opened it
        return self._manage_exit(bar) if self._long_emitted else None

    # ── helpers ────────────────────────────────────────────────────────────
    def _completed_daily(self, symbol: str, today: pd.Timestamp) -> pd.DataFrame:
        """Daily bars strictly BEFORE the current session date — the last row
        is the spec's signal day t; today's forming bar is excluded."""
        df = self._ctx.bars(symbol, "1d")
        if df.empty:
            return df
        return df[df.index.normalize() < today]

    def _momentum(self, close: pd.Series) -> float | None:
        """momentum_12_1 = close[t - skip] / close[t - lookback] - 1."""
        if len(close) < self._mom_lookback + 1:
            return None
        return float(
            close.iloc[-self._mom_skip - 1] / close.iloc[-self._mom_lookback - 1] - 1.0
        )

    def _flat(self, bar: Bar, reason: str, held: int) -> Signal:
        return Signal(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            direction="FLAT",
            confidence=1.0,
            bar_time=bar.timestamp,
            metadata={"reason": reason, "bars_held": held},
        )

    # ── exit management ────────────────────────────────────────────────────
    def _manage_exit(self, bar: Bar) -> Signal | None:
        held = self._ctx.held_bars(bar.symbol)
        if self._max_holding_days > 0 and \
                held >= self._max_holding_days * self._bars_per_day:
            return self._flat(bar, "time_stop", held)
        if self._use_trend_exit:
            today = pd.Timestamp(bar.timestamp).normalize()
            daily = self._completed_daily(bar.symbol, today)
            if len(daily) >= self._trend_exit_sma:
                exit_sma = float(ind.sma(daily["close"], self._trend_exit_sma).iloc[-1])
                if float(daily["close"].iloc[-1]) < exit_sma:
                    return self._flat(bar, "trend_exit", held)
        return None

    # ── entry evaluation ───────────────────────────────────────────────────
    def _evaluate_entry(self, bar: Bar) -> Signal | None:
        today = pd.Timestamp(bar.timestamp).normalize()
        daily = self._completed_daily(bar.symbol, today)
        min_bars = max(self._mom_lookback + 1, self._trend_slow, self._trend_mid)
        if len(daily) < min_bars:
            return None
        close = daily["close"]
        last_close = float(close.iloc[-1])

        # universe filter (liquidity/volatility on signal day t)
        adv = float(ind.sma(close * daily["volume"], self._adv_days).iloc[-1])
        atr_value = float(ind.atr(daily, self._atr_days).iloc[-1])
        if last_close <= self._min_price or adv <= self._min_adv \
                or atr_value / last_close >= self._max_atr_fraction:
            return None

        # market regime filter: benchmark above its long SMA
        bench = self._completed_daily(self._benchmark, today)
        if len(bench) < self._market_sma:
            return None
        bench_close = float(bench["close"].iloc[-1])
        if bench_close <= float(ind.sma(bench["close"], self._market_sma).iloc[-1]):
            return None

        # trend filter: close > SMA(mid) and SMA(fast) > SMA(slow)
        if not (last_close > float(ind.sma(close, self._trend_mid).iloc[-1])
                and float(ind.sma(close, self._trend_fast).iloc[-1])
                > float(ind.sma(close, self._trend_slow).iloc[-1])):
            return None

        # pullback trigger: signal-day close under the short SMA
        if not last_close < float(ind.sma(close, self._pullback_sma).iloc[-1]):
            return None

        # cross-sectional momentum percentile within the configured universe
        mom = self._momentum(close)
        if mom is None:
            return None
        peers: list[float] = []
        for sym in self._universe:
            frame = self._completed_daily(sym, today)
            m = self._momentum(frame["close"]) if len(frame) else None
            if m is not None:
                peers.append(m)
        if not peers:
            return None
        percentile = sum(1 for m in peers if m <= mom) / len(peers)
        if percentile < self._mom_pct_min:
            return None

        # next-day buy-stop: prior day's high + tick, crossed by this bar
        trigger = float(daily["high"].iloc[-1]) + self._tick
        if bar.high < trigger:
            return None

        self._long_emitted = True
        confidence = min(1.0, 0.55 + 0.35 * percentile + 0.10 * min(max(mom, 0.0), 1.0))
        return Signal(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            direction="LONG",
            confidence=round(confidence, 3),
            bar_time=bar.timestamp,
            metadata={
                "momentum_12_1": round(mom, 4),
                "momentum_percentile": round(percentile, 3),
                "entry_trigger": round(trigger, 4),
                "adv": round(adv, 0),
                "atr_fraction": round(atr_value / last_close, 4),
                # honored by the Risk Engine (spec: 0.5% risk, 1.25R target)
                "risk_per_trade_pct": self._risk_pct,
                "take_profit_r_multiple": self._target_r,
            },
        )
