"use client";

import { useEffect, useState } from "react";
import { api, demo, type StrategyRow } from "@/lib/api";

const stateStyles: Record<string, string> = {
  active: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  paused: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  cooldown: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  disabled: "border-zinc-500/30 bg-zinc-500/10 text-zinc-400",
};

export default function StrategiesPage() {
  const [rows, setRows] = useState<StrategyRow[]>(demo.strategies);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.strategies().then(setRows);
  }, []);

  async function toggle(s: StrategyRow) {
    const enabled = !(s.enabled ?? true);
    setBusy(s.strategy_id);
    const res = await api.toggleStrategy(s.strategy_id, enabled);
    if (res.ok) {
      // the worker picks the file up next cycle; reflect the intent now
      setRows((prev) =>
        prev.map((r) =>
          r.strategy_id === s.strategy_id
            ? { ...r, enabled, state: enabled ? "active" : "disabled" }
            : r,
        ),
      );
    }
    setBusy(null);
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight text-white">Strategies</h1>
        <p className="text-sm text-zinc-500">
          Isolated plugins · regime-gated · quarterly revalidation · drift monitor
        </p>
      </header>
      <div className="grid gap-4 md:grid-cols-2">
        {rows.map((s) => {
          const enabled = s.enabled ?? true;
          return (
            <div key={s.strategy_id} className={`card card-hover p-5 ${enabled ? "" : "opacity-60"}`}>
              <div className="flex items-center justify-between">
                <div className="font-mono text-sm font-bold text-white">{s.strategy_id}</div>
                <div className="flex items-center gap-2">
                  <span className={`chip border ${stateStyles[s.state] ?? stateStyles.disabled}`}>
                    {s.state.toUpperCase()}
                  </span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={enabled}
                    aria-label={`${enabled ? "Disable" : "Enable"} ${s.strategy_id}`}
                    disabled={busy === s.strategy_id}
                    onClick={() => toggle(s)}
                    className={`relative h-5 w-9 rounded-full transition-colors disabled:opacity-50 ${
                      enabled ? "bg-emerald-500/80" : "bg-zinc-700"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                        enabled ? "left-[18px]" : "left-0.5"
                      }`}
                    />
                  </button>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {s.regimes.map((r) => (
                  <span key={r} className="chip border border-violet-500/25 bg-violet-500/10 text-violet-300">
                    {r}
                  </span>
                ))}
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2 text-center">
                <div className="rounded-lg bg-white/[0.03] px-2 py-2">
                  <div className="font-mono text-lg text-white">{s.trades}</div>
                  <div className="text-[10px] uppercase tracking-wide text-zinc-500">Trades</div>
                </div>
                <div className="rounded-lg bg-white/[0.03] px-2 py-2">
                  <div className="font-mono text-lg text-violet-300">{(s.win_rate * 100).toFixed(0)}%</div>
                  <div className="text-[10px] uppercase tracking-wide text-zinc-500">Win rate</div>
                </div>
                <div className="rounded-lg bg-white/[0.03] px-2 py-2">
                  <div className={`font-mono text-lg ${s.expectancy >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    ${s.expectancy.toFixed(1)}
                  </div>
                  <div className="text-[10px] uppercase tracking-wide text-zinc-500">Expectancy</div>
                </div>
              </div>
              {!enabled && (
                <p className="mt-3 text-xs text-zinc-500">
                  Disabled: no new signals. Stops/targets on open positions remain active.
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
