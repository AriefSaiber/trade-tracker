export default function SettingsPage() {
  const rows = [
    { key: "LIVE_TRADING", value: "false", note: "Arming requires env flag + signed file + dashboard acknowledgment" },
    { key: "Broker", value: "Alpaca (paper)", note: "Separate paper/live credentials; no withdrawal scope" },
    { key: "Max daily loss", value: "3.0%", note: "Circuit breaker halts all strategies for the day" },
    { key: "Max drawdown", value: "15%", note: "Halt + manual re-arm" },
    { key: "Risk per trade", value: "0.75%", note: "Fixed fractional, 2×ATR(14) stops" },
    { key: "Portfolio heat cap", value: "5%", note: "Sum of open risk across positions" },
    { key: "Watchdog heartbeat", value: "30s", note: "Missed beat → trading halts + alert" },
  ];
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight text-white">Settings</h1>
        <p className="text-sm text-zinc-500">Read-only view — edit configs/*.yaml and .env, then restart.</p>
      </header>
      <div className="card divide-y divide-white/[0.05]">
        {rows.map((r) => (
          <div key={r.key} className="flex items-center justify-between gap-4 px-5 py-4">
            <div>
              <div className="text-sm font-semibold text-white">{r.key}</div>
              <div className="text-xs text-zinc-500">{r.note}</div>
            </div>
            <code className="shrink-0 rounded-lg bg-violet-500/10 px-3 py-1 font-mono text-xs text-violet-300">
              {r.value}
            </code>
          </div>
        ))}
      </div>
    </div>
  );
}
