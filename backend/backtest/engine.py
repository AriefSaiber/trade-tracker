"""Event-driven backtest engine — the canonical engine (MVP §11).

Same pipeline objects as paper/live: SignalValidationPipeline + RiskEngine +
order state machine. No `if backtesting:` branches anywhere — the only
difference is the injected data source and fill simulator.

Point-in-time discipline: strategies and validators only ever see history
sliced to <= current bar time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import structlog

from backend.backtest.cost_model import CostModel
from backend.backtest.metrics import BacktestMetrics, compute_metrics
from backend.core.events import (
    Bar, Fill, OrderSide, Regime, RegimeState, Signal,
)
from backend.portfolio.portfolio import Portfolio
from backend.regime.detector import RegimeDetector
from backend.risk.engine import AccountState, RiskEngine
from backend.strategies.base import StrategyBase, StrategyContext
from backend.validation.context import ValidationContext
from backend.validation.pipeline import SignalValidationPipeline
from backend.data import indicators as ind

log = structlog.get_logger(__name__)


@dataclass
class BacktestResult:
    metrics: BacktestMetrics
    portfolio: Portfolio
    funnel_records: list[dict] = field(default_factory=list)


def _bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        index=pd.DatetimeIndex([b.timestamp for b in bars], name="timestamp"),
    )
    return df.sort_index()


def _point_in_time_history(
    history: dict[tuple[str, str], pd.DataFrame],
    now: datetime,
    signal_interval: str,
) -> dict[tuple[str, str], pd.DataFrame]:
    """Return data available when an interval bar has closed.

    Providers stamp bars at their open. An intraday decision must therefore
    not see the fully formed daily OHLCV row for the same calendar day. Daily
    strategies are evaluated at their own bar close and retain that row.
    """
    ts = pd.Timestamp(now)
    session_start = ts.normalize()
    point_in_time: dict[tuple[str, str], pd.DataFrame] = {}
    for key, df in history.items():
        frame = df.iloc[:df.index.searchsorted(ts, side="right")]
        if signal_interval != "1d" and key[1] == "1d":
            frame = frame.iloc[:frame.index.searchsorted(session_start, side="left")]
        point_in_time[key] = frame
    return point_in_time


class EventDrivenEngine:
    def __init__(
        self,
        strategy: StrategyBase,
        strategy_config: dict,
        pipeline: SignalValidationPipeline,
        risk: RiskEngine,
        cost_model: CostModel,
        regime_detector: RegimeDetector,
        starting_cash: float,
        benchmark_symbol: str = "SPY",
    ) -> None:
        self._strategy = strategy
        self._strategy_cfg = strategy_config
        self._pipeline = pipeline
        self._risk = risk
        self._costs = cost_model
        self._regime = regime_detector
        self._benchmark = benchmark_symbol
        self.portfolio = Portfolio(starting_cash=starting_cash)

    def run(
        self,
        signal_bars: list[Bar],
        history: dict[tuple[str, str], pd.DataFrame],
    ) -> BacktestResult:
        """`signal_bars`: chronological bars of the strategy's trading interval.
        `history`: full frames per (symbol, interval) — the engine slices them
        point-in-time before every decision."""
        interval = str(self._strategy_cfg["interval"])
        ctx = StrategyContext(now=signal_bars[0].timestamp,
                              regime=Regime.TRANSITION, history={})
        self._strategy.initialize(self._strategy_cfg, ctx)
        bars_held: dict[str, int] = {}
        regime_cache: tuple[pd.Timestamp, RegimeState] | None = None

        for bar in signal_bars:
            now = bar.timestamp
            # point-in-time slice: data with timestamp <= t only. Frames are
            # sorted ascending, so a positional slice via searchsorted is
            # identical to the boolean mask `df[df.index <= t]` but O(log n)
            # instead of O(n) per bar — the difference between a crypto
            # backtest finishing in seconds vs. rebuilding a 26k-row mask on
            # every one of 26k bars.
            pit = _point_in_time_history(history, now, interval)
            marks = {bar.symbol: bar.close}

            daily_bench = pit.get((self._benchmark, "1d"), pd.DataFrame())
            daily_sym = pit.get((bar.symbol, "1d"), pd.DataFrame())
            if len(daily_bench) < 60:
                regime_state = RegimeState(self._benchmark, Regime.TRANSITION, now)
            else:
                marker = pd.Timestamp(daily_bench.index[-1])
                if regime_cache is not None and regime_cache[0] == marker:
                    cached = regime_cache[1]
                    regime_state = RegimeState(
                        self._benchmark, cached.regime, now, dict(cached.metrics))
                else:
                    regime_state = self._regime.classify(
                        daily_bench, self._benchmark, as_of=now)
                    regime_cache = (marker, regime_state)

            # stops/targets resolve intrabar, before the close-of-bar decision
            self._check_stops(bar)

            # position awareness for strategies (same fields the live runtime
            # populates): bars held ticks up for every symbol still open
            for sym in list(bars_held):
                if sym not in self.portfolio.positions:
                    del bars_held[sym]
            for sym in self.portfolio.positions:
                bars_held[sym] = bars_held.get(sym, 0) + 1

            ctx.now = now
            ctx.regime = regime_state.regime
            ctx.history = pit
            ctx.position_qty = {s: p.qty for s, p in self.portfolio.positions.items()}
            ctx.bars_held = dict(bars_held)
            self._strategy.on_bar(bar)

            signal = self._strategy.generate_signal()
            if signal is None:
                self.portfolio.snapshot_equity(now, marks)
                continue

            if signal.direction == "FLAT":
                # Exits are survival actions, not quality decisions: they skip
                # the validation gauntlet but still pass RiskEngine.evaluate()
                # (MVP §10 — "exits are always allowed through").
                self._execute_exit(signal, bar, regime_state, marks, now)
                self.portfolio.snapshot_equity(now, marks)
                continue

            vctx = ValidationContext(
                now=now,
                regime=regime_state,
                benchmark_symbol=self._benchmark,
                history=pit,
                strategy_config=self._strategy_cfg,
                open_positions=list(self.portfolio.positions.values()),
                equity=self.portfolio.equity(marks),
            )
            validated = self._pipeline.validate(signal, vctx, collect_diagnostics=True)
            if validated is None:
                self.portfolio.snapshot_equity(now, marks)
                continue

            atr_value = (
                float(ind.atr(daily_sym, 14).iloc[-1]) if len(daily_sym) >= 20 else 0.0
            )
            account = AccountState(
                equity=self.portfolio.equity(marks),
                equity_peak=self.portfolio.equity_peak,
                daily_pnl=self.portfolio.daily_pnl,
                open_positions=list(self.portfolio.positions.values()),
                open_positions_by_strategy=self._positions_by_strategy(),
                consecutive_losses_by_strategy=self._consecutive_losses(),
                cooldown_until_by_strategy={},
                now=now,
            )
            decision = self._risk.evaluate(validated, account, bar.close, atr_value)
            if decision.approved and decision.order is not None:
                self._simulate_fill(decision.order, bar, atr_value)

            self.portfolio.snapshot_equity(now, marks)

        self._strategy.teardown()
        metrics = compute_metrics(
            self.portfolio.closed_trades,
            self.portfolio.equity_curve,
            self.portfolio.starting_cash,
        )
        return BacktestResult(metrics=metrics, portfolio=self.portfolio,
                              funnel_records=list(self._pipeline.funnel.records))

    # ── helpers ───────────────────────────────────────────────────────────

    def _execute_exit(self, signal: Signal, bar: Bar, regime_state: RegimeState,
                      marks: dict[str, float], now: datetime) -> None:
        from backend.core.events import ValidatedSignal

        wrapper = ValidatedSignal(signal=signal, score=100.0, stage_results=[],
                                  regime=regime_state.regime.value, validated_at=now)
        account = AccountState(
            equity=self.portfolio.equity(marks),
            equity_peak=self.portfolio.equity_peak,
            daily_pnl=self.portfolio.daily_pnl,
            open_positions=list(self.portfolio.positions.values()),
            open_positions_by_strategy=self._positions_by_strategy(),
            consecutive_losses_by_strategy=self._consecutive_losses(),
            cooldown_until_by_strategy={},
            now=now,
        )
        decision = self._risk.evaluate(wrapper, account, bar.close, atr_value=1.0)
        if decision.approved and decision.order is not None:
            self._simulate_fill(decision.order, bar, atr_value=0.0)

    def _simulate_fill(self, order, bar: Bar, atr_value: float) -> None:
        is_buy = order.side == OrderSide.BUY
        # conservative: fill at next-tick proxy = bar close + adverse slippage,
        # clamped inside the bar's traded range
        price = self._costs.fill_price(bar.close, atr_value, is_buy)
        price = min(max(price, bar.low), bar.high)
        fill = Fill(order.client_order_id, order.symbol, order.side, order.qty,
                    price, bar.timestamp, self._costs.commission(order.qty, price))
        self.portfolio.apply_fill(fill, order.strategy_id,
                                  order.stop_loss, order.take_profit)

    def _check_stops(self, bar: Bar) -> None:
        """Conservative same-bar resolution: if both stop and target are inside
        the bar, assume the STOP was hit first."""
        pos = self.portfolio.positions.get(bar.symbol)
        if pos is None or pos.qty == 0:
            return
        is_long = pos.qty > 0
        exit_price: float | None = None
        if pos.stop_loss is not None:
            if (is_long and bar.low <= pos.stop_loss) or \
               (not is_long and bar.high >= pos.stop_loss):
                exit_price = pos.stop_loss
        if exit_price is None and pos.take_profit is not None:
            if (is_long and bar.high >= pos.take_profit) or \
               (not is_long and bar.low <= pos.take_profit):
                exit_price = pos.take_profit
        if exit_price is not None:
            side = OrderSide.SELL if is_long else OrderSide.BUY
            fill = Fill(f"stop-{bar.symbol}-{bar.timestamp.isoformat()}", bar.symbol,
                        side, abs(pos.qty), exit_price, bar.timestamp,
                        self._costs.commission(pos.qty, exit_price))
            self.portfolio.apply_fill(fill)

    def _positions_by_strategy(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.portfolio.positions.values():
            if p.strategy_id:
                out[p.strategy_id] = out.get(p.strategy_id, 0) + 1
        return out

    def _consecutive_losses(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for trade in self.portfolio.closed_trades:
            if trade.pnl <= 0:
                out[trade.strategy_id] = out.get(trade.strategy_id, 0) + 1
            else:
                out[trade.strategy_id] = 0
        return out
