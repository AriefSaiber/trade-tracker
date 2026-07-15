"""End-to-end paper-trading loop (MVP §5 architecture, §8 validation, §10 risk/
execution, §11 one-code-path).

This is the integration test the unit suites can't be: it wires the *real*
deterministic decision path end to end and replays market data through it —

    DataProvider -> RegimeDetector -> trend_pullback strategy
        -> SignalValidationPipeline (all 8 stages, in configs/validation.yaml order)
        -> RiskEngine -> PaperBroker -> Portfolio + TradeJournal

exactly as the worker/backtester would drive it. Every object below the strategy
is the production object on its real config; the only test doubles are the data
*source* (a MockDataProvider serving deterministic synthetic bars) and the wall
clock (bars carry fixed timestamps). No component has a `if testing:` branch.

The replay is 30 calendar days of 1h AAPL bars during regular session hours,
riding a long-term uptrend with periodic shallow pullbacks so the trend-pullback
strategy fires, and volume spikes on the resume bars so the RVOL gate clears.
The daily AAPL/SPY history is a strong, low-volatility uptrend so the benchmark
regime classifies TREND_UP (the only regime this strategy is allowed to trade).

Assertions (the contract this test pins down):
  1. at least one ValidatedSignal is produced with confluence score >= 70
  2. at least one order is filled in the paper broker
  3. every paper-broker fill appears in the trade journal
  4. every rejected signal has a logged stage and reason in the journal
  5. no file under backend/strategies/ imports backend.execution or backend.risk
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.core.config import YamlConfig
from backend.core.events import Bar, Regime, RegimeState
from backend.data import indicators as ind
from backend.data.provider import DataProvider
from backend.execution.paper_broker import PaperBroker
from backend.portfolio.journal import TradeJournal
from backend.portfolio.portfolio import Portfolio
from backend.regime.detector import RegimeDetector
from backend.risk.engine import AccountState, RiskEngine
from backend.strategies.base import StrategyContext
from backend.strategies.trend_pullback.strategy import TrendPullbackStrategy
from backend.validation.context import ValidationContext
from backend.validation.funnel_logger import FunnelLogger
from backend.validation.pipeline import SignalValidationPipeline

REPO_ROOT = Path(__file__).resolve().parents[2]

SYMBOL = "AAPL"
BENCHMARK = "SPY"
STARTING_CASH = 100_000.0

# 30-day replay window (June 2026 → EDT / UTC-4, no DST transition inside it).
REPLAY_START = datetime(2026, 6, 1, tzinfo=timezone.utc)
REPLAY_END = datetime(2026, 6, 30, tzinfo=timezone.utc)
BACKFILL_START = datetime(2026, 4, 1, tzinfo=timezone.utc)  # 1h history before the replay

# UTC hours that map to 10:00–15:00 ET in EDT — safely inside the session and
# clear of the open (15 min) and close (10 min) blackout windows.
SESSION_UTC_HOURS = (14, 15, 16, 17, 18, 19)

# Real trend_pullback plugin config (mirrors backend/strategies/trend_pullback/
# config.yaml so no separate YAML load is needed inside the test).
STRATEGY_CONFIG = {
    "strategy_id": "trend_pullback",
    "interval": "1h",
    "allowed_regimes": ["TREND_UP"],
    "opt_in_high_vol": False,
    "risk_per_trade_pct": 0.75,
    "validation_overrides": {"volume_confirmation": {"skip": False}},
    "parameters": {
        "daily_ema_period": 200,
        "pullback_ema_period": 20,
        "rsi_period": 14,
        "rsi_resume_min": 50,
        "min_history_bars": 60,
    },
}


# --------------------------------------------------------------------------- #
# Deterministic synthetic market data
# --------------------------------------------------------------------------- #
def _make_daily_frame(days: int, trend: float, seed: int,
                      start_price: float = 100.0) -> pd.DataFrame:
    """Deterministic daily OHLCV ending on the replay's last day. A positive
    ``trend`` with modest noise gives a clean uptrend: ADX > 25, EMA50 > EMA200,
    price > EMA200 and a mid-band ATR percentile."""
    rng = np.random.default_rng(seed)
    rets = trend + rng.normal(0, 0.01, days)
    close = start_price * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.005, days)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, days)))
    open_ = np.clip(close * (1 + rng.normal(0, 0.003, days)), low, high)
    volume = rng.integers(1_000_000, 5_000_000, days).astype(float)
    idx = pd.date_range(end=REPLAY_END, periods=days, freq="B", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _session_hours(start: datetime, end: datetime) -> list[datetime]:
    """Session-hour UTC timestamps on weekdays between ``start`` and ``end``."""
    out: list[datetime] = []
    day = start
    while day <= end:
        if day.weekday() < 5:  # Mon–Fri
            for hour in SESSION_UTC_HOURS:
                out.append(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc))
        day += timedelta(days=1)
    return out


def _make_hourly_frame(timestamps: list[datetime], base_price: float,
                       drift: float, amp: float, period: int) -> pd.DataFrame:
    """1h OHLCV: an uptrend (``drift`` per bar) with a sinusoidal pullback of
    amplitude ``amp`` and wavelength ``period``. The trend dominates the slow
    EMA(50) so the higher-timeframe MTF gate stays satisfied, while the faster
    EMA(20) still gets pulled below price at each trough — the exact
    pullback→resume the strategy looks for. Every bar where price crosses back
    above its EMA(20) gets a volume spike so the RVOL gate (≥ 1.2×) clears."""
    n = len(timestamps)
    i = np.arange(n)
    trend = base_price * (1 + drift) ** i
    close = trend * (1 + amp * np.sin(2 * np.pi * i / period))
    high = close * 1.0015
    low = close * 0.9985
    open_ = close * 0.9995
    volume = np.full(n, 1_000_000.0)
    ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().to_numpy()
    for k in range(1, n):
        if close[k] > ema20[k] and close[k - 1] <= ema20[k - 1]:
            volume[k] = 1_000_000.0 * 2.8  # resume bar → RVOL ≈ 2.8×
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.DatetimeIndex(timestamps),
    )


def _frame_to_bars(symbol: str, interval: str, df: pd.DataFrame) -> list[Bar]:
    return [
        Bar(symbol, interval, ts.to_pydatetime(),
            float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume))
        for ts, r in df.iterrows()
    ]


def _bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        index=pd.DatetimeIndex([b.timestamp for b in bars]),
    ).sort_index()


class MockDataProvider(DataProvider):
    """DataProvider serving pre-generated deterministic bars. Switching the data
    source must never require touching strategy/pipeline/risk code — so the loop
    below pulls everything through this interface, nothing else."""

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self._frames = frames

    async def get_bars(self, symbol: str, interval: str,
                       start: datetime, end: datetime) -> list[Bar]:
        df = self._frames.get((symbol, interval))
        if df is None:
            return []
        window = df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
        return _frame_to_bars(symbol, interval, window)

    async def subscribe_live(self, symbols, callback):  # pragma: no cover - unused
        raise NotImplementedError("historical replay only")


def _build_provider() -> MockDataProvider:
    aapl_daily = _make_daily_frame(days=400, trend=0.0025, seed=3)
    spy_daily = _make_daily_frame(days=400, trend=0.004, seed=3)
    base = float(aapl_daily["close"].iloc[-1])
    hours = _session_hours(BACKFILL_START, REPLAY_END)
    aapl_hourly = _make_hourly_frame(hours, base_price=base * 0.85,
                                     drift=0.0012, amp=0.015, period=10)
    return MockDataProvider({
        (SYMBOL, "1h"): aapl_hourly,
        (SYMBOL, "1d"): aapl_daily,
        (BENCHMARK, "1d"): spy_daily,
    })


# --------------------------------------------------------------------------- #
# The paper-trading loop under test — the real §5 decision path, no shortcuts.
# --------------------------------------------------------------------------- #
class LoopResult:
    def __init__(self) -> None:
        self.validated_signals: list = []
        self.fills: list = []
        self.rejections: list = []          # (signal, stage, reason)
        self.regimes_seen: set[str] = set()
        self.filled_orders: list = []       # broker orders in FILLED state
        self.journal: TradeJournal | None = None
        self.pipeline: SignalValidationPipeline | None = None
        self.broker: PaperBroker | None = None


async def _run_paper_trading_loop() -> LoopResult:
    provider = _build_provider()

    # Full history frames (as the provider serves them) for point-in-time slicing.
    history: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol, interval in ((SYMBOL, "1h"), (SYMBOL, "1d"), (BENCHMARK, "1d")):
        bars = await provider.get_bars(symbol, interval, BACKFILL_START - timedelta(days=1200), REPLAY_END)
        history[(symbol, interval)] = _bars_to_frame(bars)

    # The 30-day replay: 1h AAPL bars inside the replay window, in order.
    signal_bars = await provider.get_bars(SYMBOL, "1h", REPLAY_START, REPLAY_END)

    journal = TradeJournal()
    pipeline = SignalValidationPipeline(funnel=FunnelLogger(journal))
    risk = RiskEngine()
    detector = RegimeDetector()
    # The broker's price source is the latest replayed bar close — the paper
    # equivalent of the live quote cache. A mutable holder keeps the closure
    # stable across the loop instead of reaching into broker internals.
    latest_price: dict[str, float] = {}
    broker = PaperBroker(
        config=YamlConfig(name="broker",
                          data={"paper_simulator": {"slippage_bps": 3, "latency_ms": 0}}),
        price_source=latest_price.get,
    )
    portfolio = Portfolio(starting_cash=STARTING_CASH)

    strategy = TrendPullbackStrategy()
    ctx = StrategyContext(now=signal_bars[0].timestamp, regime=Regime.TRANSITION, history={})
    strategy.initialize(STRATEGY_CONFIG, ctx)

    result = LoopResult()
    result.journal, result.pipeline, result.broker = journal, pipeline, broker

    for bar in signal_bars:
        now = bar.timestamp
        # Point-in-time discipline: never let any stage see a bar with ts > now.
        pit = {key: df[df.index <= pd.Timestamp(now)] for key, df in history.items()}
        marks = {SYMBOL: bar.close}

        daily_bench = pit[(BENCHMARK, "1d")]
        regime_state = (
            detector.classify(daily_bench, BENCHMARK, as_of=now)
            if len(daily_bench) >= 60
            else RegimeState(BENCHMARK, Regime.TRANSITION, now)
        )
        result.regimes_seen.add(regime_state.regime.value)

        ctx.now, ctx.regime, ctx.history = now, regime_state.regime, pit
        strategy.on_bar(bar)
        signal = strategy.generate_signal()
        if signal is None:
            portfolio.snapshot_equity(now, marks)
            continue

        vctx = ValidationContext(
            now=now, regime=regime_state, benchmark_symbol=BENCHMARK, history=pit,
            strategy_config=STRATEGY_CONFIG,
            open_positions=list(portfolio.positions.values()),
            equity=portfolio.equity(marks),
        )
        validated = pipeline.validate(signal, vctx)
        if validated is None:
            failing = next(
                (r for r in reversed(pipeline.funnel.records)
                 if r["bar_time"] == signal.bar_time.isoformat() and not r["passed"]),
                None,
            )
            journal.record("signal_rejected", {
                "bar_time": signal.bar_time.isoformat(), "phase": "validation",
                "stage": failing["stage"], "reason": failing["reason"],
            })
            result.rejections.append((signal, failing["stage"], failing["reason"]))
            portfolio.snapshot_equity(now, marks)
            continue
        result.validated_signals.append(validated)

        daily_sym = pit[(SYMBOL, "1d")]
        atr_value = float(ind.atr(daily_sym, 14).iloc[-1]) if len(daily_sym) >= 20 else 0.0
        account = AccountState(
            equity=portfolio.equity(marks), equity_peak=portfolio.equity_peak,
            daily_pnl=portfolio.daily_pnl,
            open_positions=list(portfolio.positions.values()),
            open_positions_by_strategy={}, consecutive_losses_by_strategy={},
            cooldown_until_by_strategy={}, now=now,
        )
        decision = risk.evaluate(validated, account, bar.close, atr_value)
        if not decision.approved or decision.order is None:
            journal.record("signal_rejected", {
                "bar_time": signal.bar_time.isoformat(), "phase": "risk",
                "stage": "risk_engine", "reason": decision.reason,
            })
            result.rejections.append((signal, "risk_engine", decision.reason))
            portfolio.snapshot_equity(now, marks)
            continue

        # PaperBroker fills against the current bar's price (same state machine as live).
        latest_price[SYMBOL] = bar.close
        await broker.submit_order(decision.order)
        fill = broker.fills[-1]
        result.fills.append(fill)
        portfolio.apply_fill(fill, decision.order.strategy_id,
                             decision.order.stop_loss, decision.order.take_profit)
        journal.record("fill", fill)
        portfolio.snapshot_equity(now, marks)

    strategy.teardown()
    result.filled_orders = await broker.get_orders(status="FILLED")
    return result


# --------------------------------------------------------------------------- #
# Fixtures & tests
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def loop_result() -> LoopResult:
    import asyncio
    return asyncio.run(_run_paper_trading_loop())


def test_replay_is_non_vacuous(loop_result: LoopResult):
    """Guard: the 30-day replay actually exercised the full funnel — signals were
    validated, some rejected, at least one filled. Without this a silent
    no-trade run would make every assertion below vacuously true."""
    assert loop_result.validated_signals, "no signal survived the 8-stage pipeline"
    assert loop_result.rejections, "no signal was ever rejected — funnel untested"
    assert loop_result.fills, "no order reached the paper broker"
    assert "TREND_UP" in loop_result.regimes_seen


def test_pipeline_runs_all_eight_stages(loop_result: LoopResult):
    """The validation pipeline is the full stage-0..7 gauntlet, in yaml order,
    and every validated signal carries all eight stage results."""
    stage_names = [s.name for s in loop_result.pipeline._stages]
    assert stage_names == [
        "data_sanity", "regime_gate", "mtf_alignment", "volume_confirmation",
        "volatility_band", "confluence_score", "event_filter", "portfolio_correlation",
    ]
    for validated in loop_result.validated_signals:
        assert [r.stage for r in validated.stage_results] == stage_names
        assert all(r.passed for r in validated.stage_results)
        assert validated.regime == "TREND_UP"


def test_at_least_one_validated_signal_scores_at_least_70(loop_result: LoopResult):
    """Assertion 1: a ValidatedSignal cleared the confluence gate (≥ 70)."""
    top = max(v.score for v in loop_result.validated_signals)
    assert top >= 70, f"best confluence score {top} < 70"
    assert all(v.score >= 70 for v in loop_result.validated_signals)


def test_at_least_one_order_filled_in_paper_broker(loop_result: LoopResult):
    """Assertion 2: the paper broker actually filled an order (state machine
    reached FILLED, reported through the public get_orders API)."""
    assert len(loop_result.broker.fills) >= 1
    from backend.core.events import OrderStatus
    assert loop_result.filled_orders, "broker reports no FILLED orders"
    assert all(o.status == OrderStatus.FILLED for o in loop_result.filled_orders)


def test_every_fill_appears_in_the_trade_journal(loop_result: LoopResult):
    """Assertion 3: no fill is invisible to the journal (the dataset that powers
    the funnel analytics, the meta-model, and the AI analyst)."""
    journal = loop_result.journal
    journaled_fill_ids = {
        e["payload"]["client_order_id"]
        for e in journal.entries if e["kind"] == "fill"
    }
    assert journaled_fill_ids, "no fill was journaled"
    for fill in loop_result.broker.fills:
        assert fill.client_order_id in journaled_fill_ids


def test_every_rejected_signal_has_a_logged_stage_and_reason(loop_result: LoopResult):
    """Assertion 4: every rejection is observable — a stage and a reason land in
    the journal for each one (MVP §2 'every rejected signal is logged with the
    reason')."""
    journal = loop_result.journal
    rejection_records = [e for e in journal.entries if e["kind"] == "signal_rejected"]
    assert rejection_records, "no rejection was journaled"
    for record in rejection_records:
        payload = record["payload"]
        assert payload.get("stage"), f"rejection missing stage: {payload}"
        assert payload.get("reason"), f"rejection missing reason: {payload}"

    # Every in-loop rejection is matched by a journal record with stage + reason.
    logged_bar_times = {r["payload"]["bar_time"] for r in rejection_records}
    for signal, stage, reason in loop_result.rejections:
        assert stage and reason
        assert signal.bar_time.isoformat() in logged_bar_times


def test_strategies_do_not_import_execution_or_risk():
    """Assertion 5: static isolation guard — nothing under backend/strategies/
    imports backend.execution or backend.risk (strategies emit signals only)."""
    strategies_dir = REPO_ROOT / "backend" / "strategies"
    forbidden = ("backend.execution", "backend.risk")
    violations: list[str] = []
    for py in strategies_dir.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        for module in modules:
            if any(module == f or module.startswith(f + ".") for f in forbidden):
                violations.append(f"{py.relative_to(REPO_ROOT)}: imports {module}")
    assert not violations, f"strategy isolation violated: {violations}"
