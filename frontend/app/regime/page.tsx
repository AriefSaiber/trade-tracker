"use client";

import { useEffect, useState } from "react";
import { api, demo, type RegimeInfo } from "@/lib/api";
import { RegimeBadge } from "@/components/widgets";

const descriptions: Record<string, string> = {
  TREND_UP: "ADX > 25, EMA50 > EMA200, positive slope. Trend strategies active.",
  TREND_DOWN: "ADX > 25, EMA50 < EMA200, negative slope. Short-side trend only.",
  RANGE: "ADX < 20. Mean-reversion strategies active; trend systems gated off.",
  HIGH_VOL: "Realized vol > 90th percentile. Most strategies pause. Overrides all.",
  TRANSITION: "No clear regime. Reduced size or no new entries.",
};

export default function RegimePage() {
  const [regime, setRegime] = useState<RegimeInfo>(demo.regime);
  useEffect(() => {
    api.regime().then(setRegime);
    const id = setInterval(() => api.regime().then(setRegime), 30000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight text-white">Regime Monitor</h1>
        <p className="text-sm text-zinc-500">
          Strategies only run in regimes they are suited for — the single biggest win-rate lever.
        </p>
      </header>
      <div className="grid gap-4 md:grid-cols-2">
        <RegimeBadge regime={regime} />
        <div className="card p-5">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
            Classification rules
          </div>
          <div className="mt-3 space-y-2.5">
            {Object.entries(descriptions).map(([k, v]) => (
              <div
                key={k}
                className={`rounded-xl px-3.5 py-2.5 text-xs leading-relaxed ${
                  regime.regime === k
                    ? "border border-violet-500/40 bg-violet-500/10 text-zinc-200 shadow-glow"
                    : "bg-white/[0.03] text-zinc-500"
                }`}
              >
                <span className="font-mono font-bold text-violet-300">{k}</span> — {v}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
