export default function BacktestsPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight text-white">Backtests</h1>
        <p className="text-sm text-zinc-500">
          Event-driven engine · realistic costs · walk-forward + Monte Carlo before promotion
        </p>
      </header>
      <div className="card p-8 text-center">
        <div className="text-sm text-zinc-400">
          No backtest results yet. Run one from the CLI:
        </div>
        <code className="mt-3 inline-block rounded-lg bg-ink-950 px-4 py-2 font-mono text-xs text-violet-300">
          python -m backend.backtest.engine --strategy trend_pullback
        </code>
        <p className="mx-auto mt-4 max-w-md text-xs leading-relaxed text-zinc-600">
          Promotion Gate A requires: profit factor ≥ 1.3, expectancy &gt; 0 after costs, max DD ≤ 15%,
          ≥ 100 OOS trades, walk-forward efficiency ≥ 0.5, plateau check, Monte Carlo 95th-pct DD in tolerance.
        </p>
      </div>
    </div>
  );
}
