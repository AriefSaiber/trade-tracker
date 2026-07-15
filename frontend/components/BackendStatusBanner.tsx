"use client";

// Guards against the silent demo-data trap: every widget falls back to canned
// numbers when the API is unreachable (lib/api.ts), which must never be
// mistakable for live positions. Polls /api/health directly WITHOUT fallback.

import { useEffect, useState } from "react";

type Status = "checking" | "online" | "worker_down" | "offline";

export function BackendStatusBanner() {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    let cancelled = false;
    async function probe() {
      try {
        const res = await fetch("/api/health", { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const h = await res.json();
        if (!cancelled) setStatus(h.worker_alive ? "online" : "worker_down");
      } catch {
        if (!cancelled) setStatus("offline");
      }
    }
    probe();
    const id = setInterval(probe, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (status === "online" || status === "checking") return null;

  const offline = status === "offline";
  return (
    <div
      className={`sticky top-0 z-50 px-4 py-2 text-center text-sm font-semibold text-white ${
        offline ? "bg-red-600" : "bg-amber-600"
      }`}
    >
      {offline
        ? "BACKEND OFFLINE — everything below is hardcoded DEMO data, not your portfolio. Start the platform: python scripts/run_local.py"
        : "API is up but the trading worker is not running — data below may be empty or stale."}
    </div>
  );
}
