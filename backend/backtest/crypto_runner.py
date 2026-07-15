"""Gate A backtest harness for crypto strategies (MVP §11–12).

Drives the CANONICAL EventDrivenEngine — same SignalValidationPipeline +
RiskEngine + fill/portfolio path as paper and live — over years of hourly
crypto bars, then applies the objective Gate A criteria from MVP §12:

    OOS profit factor >= 1.3, expectancy > 0 after costs, max DD <= 15%,
    >= 100 trades, walk-forward efficiency >= 0.5, parameter-plateau check
    passed, and a Monte Carlo drawdown distribution inside risk tolerance.

Nothing here is crypto-strategy-specific beyond defaults: it takes a strategy
config dict + a (symbol, interval)->frame history and reports a GateAReport.

Cost model (matches configs/broker.yaml paper_simulator crypto_* so that
backtest ≈ paper): ~10 bps half-spread slippage on entries plus a 25 bps
taker commission charged round-trip. Slippage is baked into fill prices by
the engine; the round-trip commission is applied here from the same bps so
BOTH legs are counted (the engine's ClosedTrade only carries the exit leg).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
import structlog

from backend.backtest.cost_model import CostModel
from backend.backtest.engine import BacktestResult, EventDrivenEngine
from backend.backtest.monte_carlo import MonteCarloResult, monte_carlo_drawdown
from backend.backtest.walk_forward import (
    WalkForwardResult, build_windows, run_walk_forward,
)
from backend.core.config import load_yaml_config
from backend.core.events import Bar
from backend.portfolio.portfolio import ClosedTrade
from backend.regime.detector import RegimeDetector
from backend.risk.engine import RiskEngine
from backend.strategies.base import StrategyBase
from backend.validation.pipeline import SignalValidationPipeline

log = structlog.get_logger(__name__)

# Crypto cost assumptions — kept in lockstep with configs/broker.yaml
# paper_simulator.crypto_slippage_bps / crypto_fee_bps so paper ≈ backtest.
CRYPTO_HALF_SPREAD_BPS = 10.0
CRYPTO_COMMISSION_BPS = 25.0

# Gate A thresholds (MVP §12). Pre-committed — do not tune to fit results.
GATE_A = {
    "min_profit_factor": 1.3,
    "min_expectancy": 0.0,          # strictly > 0 after costs
    "max_drawdown_pct": 15.0,
    "min_trades": 100,
    "min_wfe": 0.5,
    "max_prob_hit_halt": 0.05,      # "≈ 0" probability of hitting the DD halt
}


# --------------------------------------------------------------------------- #
# Data plumbing
# --------------------------------------------------------------------------- #
def bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        index=pd.DatetimeIndex([b.timestamp for b in bars], name="timestamp"),
    ).sort_index()


def _truncate(history: dict[tuple[str, str], pd.DataFrame],
              end: datetime) -> dict[tuple[str, str], pd.DataFrame]:
    """Frames sliced to <= end. Belt-and-braces against future leakage (the
    engine also slices point-in-time per bar) and it keeps per-run cost down."""
    ts = pd.Timestamp(end)
    return {k: df[df.index <= ts] for k, df in history.items()}


def _window_bars(signal_bars: list[Bar], start: datetime,
                 end: datetime) -> list[Bar]:
    return [b for b in signal_bars if start <= b.timestamp < end]


def _cost_model() -> CostModel:
    return CostModel(
        commission_per_share=0.0,
        commission_bps=CRYPTO_COMMISSION_BPS,
        min_commission=0.0,
        half_spread_bps=CRYPTO_HALF_SPREAD_BPS,
        impact_coefficient=0.0,     # predictable, spread-only slippage
        latency_ms=0.0,
    )


# --------------------------------------------------------------------------- #
# Single engine run
# --------------------------------------------------------------------------- #
def run_single(
    strategy: StrategyBase,
    strategy_config: dict,
    signal_bars: list[Bar],
    history: dict[tuple[str, str], pd.DataFrame],
    starting_cash: float,
    benchmark_symbol: str,
) -> BacktestResult | None:
    """One backtest over `signal_bars`. Fresh pipeline/risk/portfolio each call
    (no cross-run state). Returns None if the window has no bars to trade."""
    if not signal_bars:
        return None
    engine = EventDrivenEngine(
        strategy=strategy,
        strategy_config=strategy_config,
        pipeline=SignalValidationPipeline(),   # real 8-stage gauntlet
        risk=RiskEngine(),                      # real risk.yaml limits
        cost_model=_cost_model(),
        regime_detector=RegimeDetector(),
        starting_cash=starting_cash,
        benchmark_symbol=benchmark_symbol,      # crypto: BTC/USD is its own bench
    )
    return engine.run(signal_bars, history)


# --------------------------------------------------------------------------- #
# Cost-correct trade accounting
# --------------------------------------------------------------------------- #
def net_pnls(trades: list[ClosedTrade],
             commission_bps: float = CRYPTO_COMMISSION_BPS) -> list[float]:
    """Per-trade P&L net of the FULL round-trip taker commission.

    Slippage already lives inside `trade.pnl` (the engine fills entries at an
    adverse price). The engine attributes only the exit leg's commission to the
    trade, so we recompute the round trip from notional here — otherwise Gate A
    would count costs optimistically."""
    out: list[float] = []
    for t in trades:
        notional = abs(t.qty) * (t.entry_price + t.exit_price)
        out.append(t.pnl - notional * commission_bps / 10_000.0)
    return out


@dataclass
class AggregateMetrics:
    trade_count: int
    net_profit: float
    expectancy: float
    profit_factor: float
    win_rate: float
    avg_win: float
    avg_loss: float
    max_drawdown_pct: float
    longest_losing_streak: int


def aggregate_metrics(pnls: list[float], starting_cash: float) -> AggregateMetrics:
    """Trade-level metrics from a net-P&L sequence. Max drawdown is computed on
    the cumulative-equity path — the same construction the Monte Carlo uses, so
    the headline DD and the MC distribution are apples-to-apples."""
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return AggregateMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    win_rate = len(wins) / len(arr)
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(abs(losses.mean())) if losses.size else 0.0
    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(abs(losses.sum())) if losses.size else 0.0
    pf = (gross_win / gross_loss if gross_loss > 0
          else float("inf") if gross_win > 0 else 0.0)

    equity = starting_cash + np.cumsum(arr)
    equity = np.concatenate(([starting_cash], equity))
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / peak
    max_dd = float(dd.max() * 100)

    streak = longest = 0
    for p in arr:
        streak = streak + 1 if p <= 0 else 0
        longest = max(longest, streak)

    return AggregateMetrics(
        trade_count=int(arr.size),
        net_profit=float(arr.sum()),
        expectancy=float(arr.mean()),
        profit_factor=float(pf),
        win_rate=float(win_rate),
        avg_win=avg_win,
        avg_loss=avg_loss,
        max_drawdown_pct=max_dd,
        longest_losing_streak=longest,
    )


# --------------------------------------------------------------------------- #
# Walk-forward (rolling train -> trade, MVP §11.2)
# --------------------------------------------------------------------------- #
def _config_with(base: dict, donchian_period: int) -> dict:
    cfg = {**base, "parameters": {**base["parameters"],
                                  "donchian_period": int(donchian_period)}}
    return cfg


def run_walk_forward_crypto(
    strategy_factory,
    base_config: dict,
    signal_bars: list[Bar],
    history: dict[tuple[str, str], pd.DataFrame],
    starting_cash: float,
    benchmark_symbol: str,
    param_grid: list[int],
    train_days: int,
    trade_days: int,
) -> tuple[WalkForwardResult, list[ClosedTrade], dict]:
    """Rolling optimize-on-train / evaluate-on-trade. `optimize` picks the
    donchian_period with the best in-sample expectancy; `evaluate` reports
    out-of-sample expectancy with that choice and pools the OOS trades.

    Returns (walk_forward_result, pooled_oos_trades, diagnostics).
    """
    start = signal_bars[0].timestamp
    end = signal_bars[-1].timestamp
    windows = build_windows(start, end, train_days=train_days, trade_days=trade_days)
    if not windows:
        raise ValueError(
            f"data span {(end - start).days}d too short for a "
            f"{train_days}d train + {trade_days}d trade window")

    pooled: list[ClosedTrade] = []
    chosen_by_window: list[dict] = []
    state = {"param": param_grid[0]}

    def optimize(train_start: datetime, train_end: datetime) -> float:
        # print(), not log.info(): this must be visible on stdout regardless
        # of structlog's configured level (a WARNING-only harness run once
        # looked hung for 20+ minutes with zero output because every progress
        # marker was an INFO log that got filtered).
        print(f"  [walk-forward] optimizing train window "
              f"{train_start.date()} -> {train_end.date()} ...", flush=True)
        train_bars = _window_bars(signal_bars, train_start, train_end)
        hist = _truncate(history, train_end)
        best_exp, best_param = float("-inf"), param_grid[0]
        for param in param_grid:
            res = run_single(strategy_factory(), _config_with(base_config, param),
                             train_bars, hist, starting_cash, benchmark_symbol)
            exp = (aggregate_metrics(net_pnls(res.portfolio.closed_trades),
                                     starting_cash).expectancy
                   if res else 0.0)
            if exp > best_exp:
                best_exp, best_param = exp, param
        state["param"] = best_param
        print(f"  [walk-forward]   chosen donchian_period={best_param}  "
              f"IS expectancy={best_exp:.2f}", flush=True)
        log.info("wf_optimize", train_start=train_start.date().isoformat(),
                 train_end=train_end.date().isoformat(),
                 chosen_donchian=best_param, is_expectancy=round(best_exp, 2))
        return max(best_exp, 0.0)

    def evaluate(trade_start: datetime, trade_end: datetime) -> float:
        param = state["param"]
        trade_bars = _window_bars(signal_bars, trade_start, trade_end)
        hist = _truncate(history, trade_end)
        res = run_single(strategy_factory(), _config_with(base_config, param),
                         trade_bars, hist, starting_cash, benchmark_symbol)
        trades = res.portfolio.closed_trades if res else []
        pooled.extend(trades)
        agg = aggregate_metrics(net_pnls(trades), starting_cash)
        chosen_by_window.append({
            "trade_start": trade_start.date().isoformat(),
            "trade_end": trade_end.date().isoformat(),
            "donchian_period": param,
            "oos_trades": agg.trade_count,
            "oos_expectancy": round(agg.expectancy, 2),
        })
        print(f"  [walk-forward] OOS trade window "
              f"{trade_start.date()} -> {trade_end.date()}: "
              f"{agg.trade_count} trades, expectancy={agg.expectancy:.2f}",
              flush=True)
        log.info("wf_evaluate", trade_start=trade_start.date().isoformat(),
                 trade_end=trade_end.date().isoformat(), donchian=param,
                 oos_trades=agg.trade_count, oos_expectancy=round(agg.expectancy, 2))
        return agg.expectancy

    wf = run_walk_forward(windows, evaluate, optimize)
    return wf, pooled, {"windows": chosen_by_window, "param_grid": param_grid}


# --------------------------------------------------------------------------- #
# Parameter-plateau check (MVP §11.3)
# --------------------------------------------------------------------------- #
def plateau_check(
    strategy_factory,
    base_config: dict,
    signal_bars: list[Bar],
    history: dict[tuple[str, str], pd.DataFrame],
    starting_cash: float,
    benchmark_symbol: str,
    center: int,
    grid: list[int],
) -> dict:
    """Full-period expectancy across a donchian_period sweep. A robust edge
    sits on a plateau: perturbing the center param ±20% must not collapse
    profitability (and neighbours must stay positive)."""
    curve: dict[int, float] = {}
    for param in grid:
        print(f"  [plateau] full-period run at donchian_period={param} ...",
              flush=True)
        res = run_single(strategy_factory(), _config_with(base_config, param),
                         signal_bars, history, starting_cash, benchmark_symbol)
        agg = aggregate_metrics(net_pnls(res.portfolio.closed_trades) if res else [],
                                starting_cash)
        curve[param] = agg.expectancy
        print(f"  [plateau]   trades={agg.trade_count}  "
              f"expectancy={agg.expectancy:.2f}", flush=True)
        log.info("plateau_point", donchian=param, expectancy=round(agg.expectancy, 2),
                 trades=agg.trade_count)

    lo = min((p for p in grid if p >= center * 0.8 and p < center), default=center)
    hi = max((p for p in grid if p <= center * 1.2 and p > center), default=center)
    center_exp = curve.get(center, 0.0)
    neighbours = [curve.get(lo, 0.0), curve.get(hi, 0.0)]
    passed = (
        center_exp > 0
        and all(n > 0 for n in neighbours)
        and all(n >= 0.5 * center_exp for n in neighbours)
    )
    return {
        "passed": bool(passed),
        "center": center,
        "center_expectancy": round(center_exp, 2),
        "neighbours": {str(lo): round(curve.get(lo, 0.0), 2),
                       str(hi): round(curve.get(hi, 0.0), 2)},
        "curve": {str(k): round(v, 2) for k, v in curve.items()},
    }


# --------------------------------------------------------------------------- #
# Gate A verdict
# --------------------------------------------------------------------------- #
@dataclass
class GateAReport:
    symbol: str
    interval: str
    data_start: str
    data_end: str
    starting_cash: float
    oos: dict
    monte_carlo: dict
    walk_forward: dict
    plateau: dict
    criteria: dict
    passed: bool
    failed_criteria: list[str] = field(default_factory=list)
    variants_tried: list[int] = field(default_factory=list)
    runtime_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_gate_a(
    strategy_factory,
    base_config: dict,
    signal_bars: list[Bar],
    history: dict[tuple[str, str], pd.DataFrame],
    *,
    starting_cash: float,
    benchmark_symbol: str,
    symbol: str,
    interval: str,
    param_grid: list[int] | None = None,
    train_days: int,
    trade_days: int,
    mc_resamples: int = 10_000,
) -> GateAReport:
    """Full Gate A pipeline: walk-forward -> pooled OOS metrics -> Monte Carlo
    -> plateau -> objective pass/fail. Every varied parameter is logged for
    multiple-testing honesty (§11.6)."""
    t0 = time.perf_counter()
    center = int(base_config["parameters"]["donchian_period"])
    grid = param_grid or sorted({int(center * 0.6), int(center * 0.8), center,
                                 int(center * 1.2), int(center * 1.6)})
    max_dd_halt = float(load_yaml_config("risk").get("account.max_drawdown_pct", 15.0))

    span_days = (signal_bars[-1].timestamp - signal_bars[0].timestamp).days
    print(f"\n=== Gate A: {symbol} {interval}, {len(signal_bars)} bars, "
          f"{span_days}d span, param grid {grid} ===", flush=True)
    log.info("gate_a_start", symbol=symbol, interval=interval,
             bars=len(signal_bars), grid=grid, span_days=span_days)

    # 1) Walk-forward (rolling train->trade) over a small honest grid
    print("\n-- Phase 1/3: walk-forward optimization --", flush=True)
    wf, pooled, wf_diag = run_walk_forward_crypto(
        strategy_factory, base_config, signal_bars, history, starting_cash,
        benchmark_symbol, [p for p in grid if abs(p - center) <= center * 0.2] or [center],
        train_days, trade_days)

    # 2) Pooled OOS metrics (cost-correct) + Monte Carlo drawdown
    pooled_pnls = net_pnls(pooled)
    agg = aggregate_metrics(pooled_pnls, starting_cash)
    print(f"\n-- Phase 1 done: {agg.trade_count} pooled OOS trades, "
          f"expectancy={agg.expectancy:.2f} --", flush=True)
    mc: MonteCarloResult | None = None
    if pooled_pnls:
        print(f"\n-- Phase 2/3: Monte Carlo ({mc_resamples} resamples) --",
              flush=True)
        mc = monte_carlo_drawdown(pooled_pnls, starting_cash, max_dd_halt,
                                  n_resamples=mc_resamples)
        print("-- Phase 2 done --", flush=True)
    else:
        print("\n-- Phase 2/3: Monte Carlo skipped (no OOS trades) --", flush=True)

    # 3) Plateau robustness over the full period
    print("\n-- Phase 3/3: parameter plateau sweep (full period) --", flush=True)
    plateau = plateau_check(strategy_factory, base_config, signal_bars, history,
                            starting_cash, benchmark_symbol, center, grid)
    print("-- Phase 3 done --\n", flush=True)

    # 4) Objective pass/fail
    failed: list[str] = []
    if agg.profit_factor < GATE_A["min_profit_factor"]:
        failed.append(f"profit_factor {agg.profit_factor:.2f} < {GATE_A['min_profit_factor']}")
    if agg.expectancy <= GATE_A["min_expectancy"]:
        failed.append(f"expectancy {agg.expectancy:.2f} <= 0 after costs")
    if agg.max_drawdown_pct > GATE_A["max_drawdown_pct"]:
        failed.append(f"max_dd {agg.max_drawdown_pct:.1f}% > {GATE_A['max_drawdown_pct']}%")
    if agg.trade_count < GATE_A["min_trades"]:
        failed.append(f"trades {agg.trade_count} < {GATE_A['min_trades']}")
    if wf.efficiency < GATE_A["min_wfe"]:
        failed.append(f"WFE {wf.efficiency:.2f} < {GATE_A['min_wfe']}")
    if not plateau["passed"]:
        failed.append("parameter plateau check failed")
    if mc is None:
        failed.append("no OOS trades for Monte Carlo")
    elif mc.prob_hit_max_dd > GATE_A["max_prob_hit_halt"]:
        failed.append(f"P(hit {max_dd_halt:.0f}% halt) {mc.prob_hit_max_dd:.2%} "
                      f"> {GATE_A['max_prob_hit_halt']:.0%}")

    report = GateAReport(
        symbol=symbol, interval=interval,
        data_start=signal_bars[0].timestamp.isoformat(),
        data_end=signal_bars[-1].timestamp.isoformat(),
        starting_cash=starting_cash,
        oos=asdict(agg),
        monte_carlo=asdict(mc) if mc else {},
        walk_forward={"efficiency": round(wf.efficiency, 3),
                      "is_expectancies": [round(x, 2) for x in wf.is_expectancies],
                      "oos_expectancies": [round(x, 2) for x in wf.oos_expectancies],
                      **wf_diag},
        plateau=plateau,
        criteria=GATE_A,
        passed=not failed,
        failed_criteria=failed,
        variants_tried=grid,
        runtime_seconds=round(time.perf_counter() - t0, 1),
    )
    log.info("gate_a_done", passed=report.passed, failed=failed,
             runtime_s=report.runtime_seconds)
    return report


# --------------------------------------------------------------------------- #
# Pretty report
# --------------------------------------------------------------------------- #
def format_report(r: GateAReport) -> str:
    """ASCII-only (no unicode box-drawing/emoji): Windows console codepages
    (cp1252 etc.) reliably render this without an encoding crash, which once
    lost a completed multi-minute walk-forward run's results at print time."""
    o, mc, wf, pl = r.oos, r.monte_carlo, r.walk_forward, r.plateau
    verdict = "PASS -- promote to paper" if r.passed else "FAIL -- stay in exploration"
    lines = [
        "=" * 64,
        f" GATE A -- {r.symbol} {r.interval}   ({r.data_start[:10]} to {r.data_end[:10]})",
        "=" * 64,
        f" Verdict: {verdict}",
        "",
        " Out-of-sample (walk-forward pooled, net of costs)",
        f"   trades ............. {o['trade_count']:>8}   (need >= {GATE_A['min_trades']})",
        f"   profit factor ...... {o['profit_factor']:>8.2f}   (need >= {GATE_A['min_profit_factor']})",
        f"   expectancy/trade ... {o['expectancy']:>8.2f}   (need > 0)",
        f"   win rate .......... {o['win_rate']*100:>8.1f}%",
        f"   net profit ........ {o['net_profit']:>8.0f}",
        f"   max drawdown ...... {o['max_drawdown_pct']:>8.1f}%   (need <= {GATE_A['max_drawdown_pct']}%)",
        f"   longest losing .... {o['longest_losing_streak']:>8}",
        "",
        " Walk-forward efficiency",
        f"   WFE ............... {wf['efficiency']:>8}   (need >= {GATE_A['min_wfe']})",
        f"   windows ........... {len(wf.get('windows', []))}",
    ]
    if mc:
        lines += [
            "",
            " Monte Carlo (10k bootstrap of OOS trades)",
            f"   drawdown p50/p95 .. {mc['dd_p50']:>6.1f}% / {mc['dd_p95']:.1f}%",
            f"   terminal p5/p50/p95  {mc['terminal_p5']:.0f} / {mc['terminal_p50']:.0f} / {mc['terminal_p95']:.0f}",
            f"   P(hit DD halt) .... {mc['prob_hit_max_dd']*100:>7.2f}%   (need ~ 0)",
        ]
    lines += [
        "",
        " Parameter plateau (donchian_period +/-20%)",
        f"   center {pl['center']} -> {pl['center_expectancy']}   neighbours {pl['neighbours']}",
        f"   passed ............ {pl['passed']}",
        f"   full curve ........ {pl['curve']}",
        "",
        f" Variants tried (multiple-testing log): {r.variants_tried}",
        f" Runtime: {r.runtime_seconds}s",
        "=" * 64,
    ]
    if not r.passed:
        lines.append(" Failed criteria:")
        lines += [f"   - {c}" for c in r.failed_criteria]
        lines.append("=" * 64)
    return "\n".join(lines)
