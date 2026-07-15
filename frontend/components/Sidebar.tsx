"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  CandlestickChart,
  Filter,
  LayoutDashboard,
  Radar,
  Settings,
  Zap,
} from "lucide-react";

const nav = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/funnel", label: "Validation Funnel", icon: Filter },
  { href: "/strategies", label: "Strategies", icon: Zap },
  { href: "/regime", label: "Regime Monitor", icon: Radar },
  { href: "/backtests", label: "Backtests", icon: CandlestickChart },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-white/[0.06] bg-ink-900/60 backdrop-blur lg:flex">
      <div className="flex items-center gap-2.5 px-5 py-6">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-purple-800 shadow-glow">
          <Activity size={18} className="text-white" />
        </div>
        <div>
          <div className="text-sm font-bold tracking-wide text-white">AlgoTrader</div>
          <div className="text-[10px] font-semibold uppercase tracking-widest text-violet-400">
            AI · Local-first
          </div>
        </div>
      </div>

      <nav className="mt-2 flex-1 space-y-1 px-3">
        {nav.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors ${
                active
                  ? "bg-violet-600/15 text-violet-300 shadow-[inset_0_0_0_1px_rgba(139,92,246,0.25)]"
                  : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-200"
              }`}
            >
              <Icon size={16} className={active ? "text-violet-400" : "text-zinc-500 group-hover:text-zinc-300"} />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="px-5 py-5">
        <div className="rounded-xl border border-violet-500/20 bg-violet-950/30 p-3">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
            </span>
            <span className="text-xs font-semibold text-emerald-300">PAPER MODE</span>
          </div>
          <p className="mt-1 text-[11px] leading-snug text-zinc-500">
            Live trading disarmed. Promotion gates A–C required.
          </p>
        </div>
      </div>
    </aside>
  );
}
