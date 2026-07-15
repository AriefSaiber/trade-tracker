"""End-to-end determinism of the EventDrivenEngine (MVP §11).

The backtester is only trustworthy if replaying the *same* fixed Bar sequence
through the *same* Strategy -> ValidationPipeline -> RiskEngine -> fill/portfolio
path yields byte-identical results every time. This test builds its inputs from
a fixed seed, runs the whole engine three times, and asserts the metrics, the
equity curve, and every closed trade are identical across all three runs.

The RiskEngine, CostModel, Portfolio and metrics are the real objects — the only
test doubles are a deterministic one-shot strategy and a pass-through validation
pipeline (the pipeline is pure and covered on its own in tests/validation). The
trading-hours clock gate is disabled so the run is calendar/timezone independent;
determinism, not session realism, is what this test pins down.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

from backend.backtest.cost_model import CostModel
from backend.backtest.engine import BacktestResult, EventDrivenEngine
from backend.core.config import YamlConfig, load_yaml_config
from backend.core.events import Bar, Signal, ValidatedSignal
from backend.regime.detector import RegimeDetector
from backend.risk.engine import RiskEngine
from backend.strategies.base import StrategyBase, StrategyContext
from backend.validation.context import ValidationContext

SYMBOL = "NVDA"
SEED = 20260712
STARTING_CASH = 100_000.0


class _OneShotLongStrategy(StrategyBase):
    """Emits a single LONG signal on the first bar it sees, then nothing.

    Deterministic by construction — it carries no random state, so the only
    thing that can vary run-to-run is the engine plumbing under test.
    """

    strategy_id = "det_stub"

    def __init__(self, symbol: str) -> None:
        self._symbol = symbol
        self._last: Bar | None = None
        self._fired = False

    def initialize(self, config: dict, context: StrategyContext) -> None:
        self._cfg = config

    def on_bar(self, bar: Bar) -> None:
        self._last = bar

    def generate_signal(self) -> Signal | None:
        if self._fired or self._last is None:
            return None
        self._fired = True
        return Signal(
            strategy_id=self.strategy_id,
            symbol=self._symbol,
            direction="LONG",
            confidence=0.9,
            bar_time=self._last.timestamp,
            metadata={},
        )


class _PassThroughPipeline:
    """Approves every signal with a fixed score. Mirrors the real pipeline's
    interface (``validate`` + ``funnel.records``) without its stage logic, so
    the test doesn't depend on 8 stages worth of synthetic-history tuning. The
    real pipeline is deterministic and covered in tests/validation/."""

    def __init__(self) -> None:
        self.funnel = SimpleNamespace(records=[])

    def validate(self, signal: Signal, ctx: ValidationContext) -> ValidatedSignal:
        return ValidatedSignal(
            signal=signal,
            score=100.0,
            stage_results=[],
            regime=ctx.regime.regime.value,
            validated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def _risk_engine_no_clock() -> RiskEngine:
    """Real RiskEngine on the real risk.yaml limits, with only the trading-hours
    clock gate switched off (keeps the run timezone-independent)."""
    data = copy.deepcopy(load_yaml_config("risk").data)
    data["trading_hours"]["enforce"] = False
    return RiskEngine(config=YamlConfig(name="risk", data=data))


def _build_daily_history(seed: int) -> dict[tuple[str, str], pd.DataFrame]:
    """Deterministic daily OHLCV strictly *before* the trading session, so every
    point-in-time slice sees the full frame and ATR(14) is a positive constant.
    ATR must be > 0 or the RiskEngine fails flat and never sizes an entry."""
    rng = np.random.default_rng(seed)
    n = 40
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, n))
    high = close + np.abs(rng.normal(0.0, 1.0, n)) + 1.0
    low = close - np.abs(rng.normal(0.0, 1.0, n)) - 1.0
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.date_range("2026-01-05", periods=n, freq="B", tz="UTC")
    frame = pd.DataFrame(
        {"open": close.copy(), "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    return {(SYMBOL, "1d"): frame}


def _signal_bars() -> list[Bar]:
    """Fixed trading-interval sequence: bar 0 opens a long; bar 1 is a wide
    green bar whose high clears any plausible take-profit while its low stays
    above any plausible stop, forcing a clean profitable exit; bar 2 is quiet."""
    base = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
    return [
        Bar(SYMBOL, "1h", base, 100.0, 101.0, 99.0, 100.0, 2_000_000.0),
        Bar(SYMBOL, "1h", base + timedelta(hours=1), 100.0, 500.0, 100.0, 110.0, 2_000_000.0),
        Bar(SYMBOL, "1h", base + timedelta(hours=2), 110.0, 111.0, 109.0, 110.0, 2_000_000.0),
    ]


def _run_once(seed: int) -> BacktestResult:
    engine = EventDrivenEngine(
        strategy=_OneShotLongStrategy(SYMBOL),
        strategy_config={"interval": "1h", "strategy_id": "det_stub"},
        pipeline=_PassThroughPipeline(),
        risk=_risk_engine_no_clock(),
        cost_model=CostModel(commission_per_share=0.005, min_commission=1.0),
        regime_detector=RegimeDetector(),
        starting_cash=STARTING_CASH,
    )
    return engine.run(_signal_bars(), _build_daily_history(seed))


def test_engine_run_is_non_vacuous():
    """Guard against the test passing only because nothing ever traded."""
    result = _run_once(SEED)
    assert result.metrics.trade_count == 1
    assert len(result.portfolio.closed_trades) == 1
    assert result.metrics.net_profit > 0
    assert result.metrics.win_rate == 1.0
    assert result.metrics.longest_losing_streak == 0
    assert len(result.portfolio.equity_curve) == len(_signal_bars())


def test_engine_deterministic_across_3_runs():
    """Same fixed Bar sequence + same seed => identical metrics, equity curve
    and closed trades on every one of three independent runs."""
    results = [_run_once(SEED) for _ in range(3)]

    first_metrics = asdict(results[0].metrics)
    first_curve = results[0].portfolio.equity_curve
    first_trades = results[0].portfolio.closed_trades

    for other in results[1:]:
        assert asdict(other.metrics) == first_metrics
        assert other.portfolio.equity_curve == first_curve
        assert other.portfolio.closed_trades == first_trades
