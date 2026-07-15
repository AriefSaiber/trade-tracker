"""Monte Carlo drawdown analysis (MVP §11.5): bootstrap resamples of the
OOS trade sequence -> distributions of max drawdown and terminal equity."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MonteCarloResult:
    n_resamples: int
    dd_p50: float
    dd_p95: float
    terminal_p5: float
    terminal_p50: float
    terminal_p95: float
    prob_hit_max_dd: float


def monte_carlo_drawdown(
    trade_pnls: list[float],
    starting_equity: float,
    max_dd_halt_pct: float,
    n_resamples: int = 10_000,
    seed: int = 42,
) -> MonteCarloResult:
    """Deterministic (seeded) bootstrap. Returns drawdown percentiles and the
    probability of hitting the account-level max-drawdown halt."""
    rng = np.random.default_rng(seed)
    pnls = np.asarray(trade_pnls, dtype=float)
    if len(pnls) == 0:
        raise ValueError("no trades to resample")

    max_dds = np.empty(n_resamples)
    terminals = np.empty(n_resamples)
    n = len(pnls)
    for i in range(n_resamples):
        sample = rng.choice(pnls, size=n, replace=True)
        equity = starting_equity + np.cumsum(sample)
        equity = np.concatenate(([starting_equity], equity))
        peak = np.maximum.accumulate(equity)
        dd = (peak - equity) / peak
        max_dds[i] = dd.max() * 100
        terminals[i] = equity[-1]

    return MonteCarloResult(
        n_resamples=n_resamples,
        dd_p50=float(np.percentile(max_dds, 50)),
        dd_p95=float(np.percentile(max_dds, 95)),
        terminal_p5=float(np.percentile(terminals, 5)),
        terminal_p50=float(np.percentile(terminals, 50)),
        terminal_p95=float(np.percentile(terminals, 95)),
        prob_hit_max_dd=float((max_dds >= max_dd_halt_pct).mean()),
    )
