"use client";

import { useEffect, useState } from "react";
import {
  demo,
  api,
  type FunnelStage,
  type HealthInfo,
  type PortfolioInfo,
  type RegimeInfo,
  type SignalRow,
} from "@/lib/api";
import {
  EquityChart,
  FunnelChart,
  KillSwitch,
  PositionsTable,
  RegimeBadge,
  SignalsTable,
  StatCard,
} from "@/components/widgets";

const fmtUsd = (n: number) =>
  "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function Dashboard() {
  const [health, setHealth] = useState<HealthInfo>(demo.health);
  const [regime, setRegime] = useState<RegimeInfo>(demo.regime);
  const [portfolio, setPortfolio] = useState<PortfolioInfo>(demo.portfolio);
  const [signals, setSignals] = useState<SignalRow[]>(demo.signals);
  const [funnel, setFunnel] = useState<FunnelStage[]>(demo.funnel);

  useEffect(() => {
    const load = async () => {
      const [h, r, p, s, f] = await Promise.all([
        api.health(),
        api.regime(),
        api.portfolio(),
        api.signals(),
        api.funnel(),
      ]);
      setHealth(h);
      setRegime(r);
      setPortfolio(p);
      setSignals(s);
      setFunnel(f);
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  const dailyPositive = portfolio.daily_pnl >= 0;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Dashboard</h1>
          <p className="text-sm text-zinc-500">
            Deterministic pipeline · every rejection logged · paper is the default
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`chip border ${
              health.trading_halted
                ? "border-rose-500/40 bg-rose-500/10 text-rose-300"
                : "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
            }`}
          >
            {health.trading_halted ? "TRADING HALTED" : "SYSTEMS NOMINAL"}
          </span>
          <span className="chip border border-violet-500/30 bg-violet-500/10 text-violet-300">
            {health.live_trading ? "LIVE" : "PAPER"}
          </span>
        </div>
      </header>

      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Equity" value={fmtUsd(portfolio.equity)} sub="account value" positive={null} />
        <StatCard
          label="Daily PnL"
          value={fmtUsd(portfolio.daily_pnl)}
          sub={dailyPositive ? "up today" : "down today"}
          positive={dailyPositive}
        />
        <StatCard label="Cash" value={fmtUsd(portfolio.cash)} sub="buying power" positive={null} />
        <StatCard
          label="Open Positions"
          value={String(portfolio.positions.length)}
          sub="across strategies"
          positive={null}
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <EquityChart curve={portfolio.equity_curve} />
        </div>
        <div className="space-y-4">
          <RegimeBadge regime={regime} />
          <KillSwitch />
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <SignalsTable signals={signals} />
        <FunnelChart stages={funnel} />
      </section>

      <section>
        <PositionsTable positions={portfolio.positions} />
      </section>
    </div>
  );
}
