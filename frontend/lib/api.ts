// API client with graceful fallback to demo data so the dashboard renders
// before the backend/worker is populated.

export type RegimeInfo = {
  regime: string;
  metrics: Record<string, number>;
  as_of: string | null;
};

export type PositionRow = {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  unrealized_pnl: number;
  strategy_id?: string;
};

export type PortfolioInfo = {
  equity: number;
  cash: number;
  daily_pnl: number;
  positions: PositionRow[];
  equity_curve: [string, number][];
};

export type SignalRow = {
  strategy_id: string;
  symbol: string;
  direction: string;
  score: number | null;
  validated: boolean;
  stage_failed?: string;
  reason?: string;
  bar_time: string;
};

export type FunnelStage = { stage: string; passed: number; failed: number };

export type StrategyRow = {
  strategy_id: string;
  state: "active" | "paused" | "cooldown" | "disabled";
  enabled?: boolean;
  regimes: string[];
  trades: number;
  win_rate: number;
  expectancy: number;
};

export type HealthInfo = {
  status: string;
  live_trading: boolean;
  kill_switch_active: boolean;
  trading_halted: boolean;
};

async function get<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

// ── demo fallbacks ─────────────────────────────────────────────────────────

const demoEquityCurve: [string, number][] = Array.from({ length: 90 }, (_, i) => {
  const d = new Date(Date.now() - (89 - i) * 86400000);
  const drift = 100000 * (1 + i * 0.0011);
  const wobble = Math.sin(i / 6) * 900 + Math.sin(i / 2.3) * 400;
  return [d.toISOString().slice(0, 10), Math.round(drift + wobble)];
});

export const demo = {
  health: {
    status: "ok",
    live_trading: false,
    kill_switch_active: false,
    trading_halted: false,
  } as HealthInfo,
  regime: {
    regime: "TREND_UP",
    metrics: { adx: 28.4, vol_percentile: 42.0, ema_slope: 0.31 },
    as_of: new Date().toISOString(),
  } as RegimeInfo,
  portfolio: {
    equity: 108234,
    cash: 61240,
    daily_pnl: 412.55,
    positions: [
      { symbol: "NVDA", qty: 40, avg_entry_price: 512.3, unrealized_pnl: 634.2, strategy_id: "trend_pullback" },
      { symbol: "MSFT", qty: 55, avg_entry_price: 448.1, unrealized_pnl: -122.4, strategy_id: "trend_pullback" },
      { symbol: "SPY", qty: 30, avg_entry_price: 592.7, unrealized_pnl: 251.8, strategy_id: "rsi2_mean_reversion" },
    ],
    equity_curve: demoEquityCurve,
  } as PortfolioInfo,
  signals: [
    { strategy_id: "trend_pullback", symbol: "NVDA", direction: "LONG", score: 84, validated: true, bar_time: "2026-07-10T14:00:00Z" },
    { strategy_id: "trend_pullback", symbol: "AAPL", direction: "LONG", score: 66, validated: false, stage_failed: "confluence_score", reason: "score 66.0 below 70", bar_time: "2026-07-10T13:00:00Z" },
    { strategy_id: "rsi2_mean_reversion", symbol: "SPY", direction: "LONG", score: 78, validated: true, bar_time: "2026-07-09T20:00:00Z" },
    { strategy_id: "trend_pullback", symbol: "QQQ", direction: "LONG", score: null, validated: false, stage_failed: "regime_gate", reason: "regime RANGE not in allowed_regimes", bar_time: "2026-07-09T15:00:00Z" },
    { strategy_id: "trend_pullback", symbol: "MSFT", direction: "LONG", score: null, validated: false, stage_failed: "volume_confirmation", reason: "relative volume below minimum", bar_time: "2026-07-08T17:00:00Z" },
  ] as SignalRow[],
  funnel: [
    { stage: "data_sanity", passed: 128, failed: 2 },
    { stage: "regime_gate", passed: 96, failed: 32 },
    { stage: "mtf_alignment", passed: 71, failed: 25 },
    { stage: "volume_confirmation", passed: 52, failed: 19 },
    { stage: "volatility_band", passed: 47, failed: 5 },
    { stage: "confluence_score", passed: 21, failed: 26 },
    { stage: "event_filter", passed: 19, failed: 2 },
    { stage: "portfolio_correlation", passed: 17, failed: 2 },
  ] as FunnelStage[],
  strategies: [
    { strategy_id: "trend_pullback", state: "active", enabled: true, regimes: ["TREND_UP"], trades: 34, win_rate: 0.47, expectancy: 42.1 },
    { strategy_id: "rsi2_mean_reversion", state: "active", enabled: true, regimes: ["TREND_UP", "RANGE"], trades: 58, win_rate: 0.71, expectancy: 18.6 },
    { strategy_id: "btc_trend_momentum", state: "active", enabled: true, regimes: ["TREND_UP", "TREND_DOWN"], trades: 12, win_rate: 0.42, expectancy: 61.3 },
  ] as StrategyRow[],
};

export const api = {
  health: () => get<HealthInfo>("/api/health", demo.health),
  regime: () => get<RegimeInfo>("/api/regime", demo.regime),
  portfolio: () => get<PortfolioInfo>("/api/portfolio", demo.portfolio),
  signals: () => get<SignalRow[]>("/api/signals", demo.signals),
  funnel: () => get<FunnelStage[]>("/api/validation/funnel/summary", demo.funnel),
  strategies: () => get<StrategyRow[]>("/api/strategies", demo.strategies),
  killSwitch: (acknowledgment: string) =>
    fetch("/api/kill-switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flatten: false, acknowledgment }),
    }).then((r) => r.json()).catch(() => ({ ok: false })),
  toggleStrategy: (strategyId: string, enabled: boolean) =>
    fetch(`/api/strategies/${encodeURIComponent(strategyId)}/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }).then((r) => r.json()).catch(() => ({ ok: false })),
};
