"use client";

import { useState } from "react";
import { AlertOctagon, TrendingDown, TrendingUp } from "lucide-react";
import type { FunnelStage, PositionRow, RegimeInfo, SignalRow } from "@/lib/api";
import { api } from "@/lib/api";

const fmt = (n: number, d = 2) =>
  n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

export function StatCard({
  label,
  value,
  sub,
  positive,
}: {
  label: string;
  value: string;
  sub?: string;
  positive?: boolean | null;
}) {
  return (
    <div className="card card-hover p-5">
      <div className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
        {label}
      </div>
      <div className="mt-2 font-mono text-2xl font-semibold text-white">{value}</div>
      {sub && (
        <div
          className={`mt-1 flex items-center gap-1 text-xs font-medium ${
            positive == null ? "text-zinc-500" : positive ? "text-emerald-400" : "text-rose-400"
          }`}
        >
          {positive != null && (positive ? <TrendingUp size={13} /> : <TrendingDown size={13} />)}
          {sub}
        </div>
      )}
    </div>
  );
}

const regimeStyles: Record<string, string> = {
  TREND_UP: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  TREND_DOWN: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  RANGE: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  HIGH_VOL: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  TRANSITION: "bg-zinc-500/15 text-zinc-300 border-zinc-500/30",
};

export function RegimeBadge({ regime }: { regime: RegimeInfo }) {
  return (
    <div className="card card-hover flex flex-col gap-3 p-5">
      <div className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
        Market Regime
      </div>
      <span
        className={`chip w-fit border px-3 py-1 text-sm ${regimeStyles[regime.regime] ?? regimeStyles.TRANSITION}`}
      >
        {regime.regime.replace("_", " ")}
      </span>
      <div className="grid grid-cols-3 gap-2 text-center">
        {Object.entries(regime.metrics)
          .slice(0, 3)
          .map(([k, v]) => (
            <div key={k} className="rounded-lg bg-white/[0.03] px-2 py-1.5">
              <div className="font-mono text-sm text-violet-300">{fmt(Number(v), 1)}</div>
              <div className="text-[10px] uppercase tracking-wide text-zinc-500">
                {k.replace(/_/g, " ")}
              </div>
            </div>
          ))}
      </div>
    </div>
  );
}

export function EquityChart({ curve }: { curve: [string, number][] }) {
  if (curve.length < 2) return null;
  const values = curve.map(([, v]) => v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const W = 720;
  const H = 200;
  const pts = values.map((v, i) => [
    (i / (values.length - 1)) * W,
    H - ((v - min) / (max - min || 1)) * (H - 20) - 10,
  ]);
  const path = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${path} L${W},${H} L0,${H} Z`;
  const up = values[values.length - 1] >= values[0];

  return (
    <div className="card card-hover p-5">
      <div className="mb-1 flex items-baseline justify-between">
        <div className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
          Equity Curve · 90d
        </div>
        <div className={`font-mono text-sm ${up ? "text-emerald-400" : "text-rose-400"}`}>
          {up ? "+" : ""}
          {fmt(((values[values.length - 1] - values[0]) / values[0]) * 100)}%
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        <defs>
          <linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8b5cf6" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="eqline" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#a78bfa" />
            <stop offset="100%" stopColor="#7c3aed" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#eqfill)" />
        <path d={path} fill="none" stroke="url(#eqline)" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    </div>
  );
}

export function PositionsTable({ positions }: { positions: PositionRow[] }) {
  return (
    <div className="card card-hover p-5">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
        Open Positions
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.06] text-left text-xs text-zinc-500">
            <th className="pb-2 font-medium">Symbol</th>
            <th className="pb-2 font-medium">Qty</th>
            <th className="pb-2 font-medium">Entry</th>
            <th className="pb-2 text-right font-medium">Unrealized</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.symbol} className="border-b border-white/[0.03] last:border-0">
              <td className="py-2.5">
                <span className="font-mono font-semibold text-white">{p.symbol}</span>
                {p.strategy_id && (
                  <span className="ml-2 text-[10px] text-zinc-500">{p.strategy_id}</span>
                )}
              </td>
              <td className="py-2.5 font-mono text-zinc-300">{p.qty}</td>
              <td className="py-2.5 font-mono text-zinc-300">${fmt(p.avg_entry_price)}</td>
              <td
                className={`py-2.5 text-right font-mono ${
                  p.unrealized_pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                {p.unrealized_pnl >= 0 ? "+" : ""}
                {fmt(p.unrealized_pnl)}
              </td>
            </tr>
          ))}
          {positions.length === 0 && (
            <tr>
              <td colSpan={4} className="py-6 text-center text-zinc-600">
                No open positions
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function SignalsTable({ signals }: { signals: SignalRow[] }) {
  return (
    <div className="card card-hover p-5">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
        Latest Signals · validation scores & rejection reasons
      </div>
      <div className="space-y-2">
        {signals.map((s, i) => (
          <div
            key={i}
            className="flex items-center justify-between rounded-xl bg-white/[0.03] px-3.5 py-2.5"
          >
            <div className="flex items-center gap-3">
              <span
                className={`chip border ${
                  s.validated
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                    : "border-rose-500/30 bg-rose-500/10 text-rose-300"
                }`}
              >
                {s.validated ? "PASSED" : "REJECTED"}
              </span>
              <span className="font-mono text-sm font-semibold text-white">{s.symbol}</span>
              <span className="text-xs text-violet-300">{s.direction}</span>
              <span className="hidden text-xs text-zinc-500 sm:inline">{s.strategy_id}</span>
            </div>
            <div className="flex items-center gap-3">
              {!s.validated && s.reason && (
                <span className="hidden max-w-[260px] truncate text-xs text-zinc-500 md:inline">
                  {s.stage_failed}: {s.reason}
                </span>
              )}
              {s.score != null && (
                <span
                  className={`font-mono text-sm font-bold ${
                    s.score >= 70 ? "text-violet-300" : "text-zinc-500"
                  }`}
                >
                  {s.score}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function FunnelChart({ stages }: { stages: FunnelStage[] }) {
  const maxTotal = Math.max(...stages.map((s) => s.passed + s.failed), 1);
  return (
    <div className="card card-hover p-5">
      <div className="mb-4 text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
        Validation Funnel · last 30 days
      </div>
      <div className="space-y-2.5">
        {stages.map((s) => {
          const total = s.passed + s.failed;
          return (
            <div key={s.stage} className="flex items-center gap-3">
              <div className="w-44 truncate text-xs text-zinc-400">{s.stage}</div>
              <div className="flex h-5 flex-1 overflow-hidden rounded-md bg-white/[0.04]">
                <div
                  className="bg-gradient-to-r from-violet-600 to-violet-400"
                  style={{ width: `${(s.passed / maxTotal) * 100}%` }}
                />
                <div
                  className="bg-rose-500/40"
                  style={{ width: `${(s.failed / maxTotal) * 100}%` }}
                />
              </div>
              <div className="w-20 text-right font-mono text-xs text-zinc-400">
                {s.passed}<span className="text-zinc-600"> / {total}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function KillSwitch() {
  const [confirming, setConfirming] = useState(false);
  const [text, setText] = useState("");
  const [triggered, setTriggered] = useState(false);

  const fire = async () => {
    const res = await api.killSwitch(text);
    if (res.ok) setTriggered(true);
    setConfirming(false);
    setText("");
  };

  if (triggered)
    return (
      <div className="card border-rose-500/40 bg-rose-950/40 p-5 text-center">
        <div className="text-sm font-bold text-rose-300">KILL SWITCH ACTIVE</div>
        <div className="mt-1 text-xs text-rose-400/70">Live mode disarmed. Manual re-arm required.</div>
      </div>
    );

  return (
    <div className="card card-hover p-5">
      <div className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
        Emergency
      </div>
      {!confirming ? (
        <button
          onClick={() => setConfirming(true)}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-rose-500/40 bg-rose-500/10 py-3 text-sm font-bold text-rose-300 transition-all hover:bg-rose-500/20 hover:shadow-[0_0_20px_rgba(244,63,94,0.25)]"
        >
          <AlertOctagon size={16} /> KILL SWITCH
        </button>
      ) : (
        <div className="mt-3 space-y-2">
          <input
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder='Type "KILL" to confirm'
            className="w-full rounded-lg border border-rose-500/30 bg-ink-950 px-3 py-2 font-mono text-sm text-rose-200 outline-none placeholder:text-zinc-600 focus:border-rose-400"
          />
          <div className="flex gap-2">
            <button
              onClick={fire}
              disabled={text.trim().toUpperCase() !== "KILL"}
              className="flex-1 rounded-lg bg-rose-600 py-2 text-xs font-bold text-white disabled:opacity-30"
            >
              Confirm
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="flex-1 rounded-lg bg-white/[0.06] py-2 text-xs font-semibold text-zinc-300"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      <p className="mt-2 text-[10px] leading-snug text-zinc-600">
        Cancels open orders and disarms live mode. Also available: <code>touch data/KILL</code> or Telegram.
      </p>
    </div>
  );
}
