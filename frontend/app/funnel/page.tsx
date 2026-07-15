"use client";

import { useEffect, useState } from "react";
import { api, demo, type FunnelStage, type SignalRow } from "@/lib/api";
import { FunnelChart, SignalsTable } from "@/components/widgets";

export default function FunnelPage() {
  const [funnel, setFunnel] = useState<FunnelStage[]>(demo.funnel);
  const [signals, setSignals] = useState<SignalRow[]>(demo.signals);

  useEffect(() => {
    Promise.all([api.funnel(), api.signals()]).then(([f, s]) => {
      setFunnel(f);
      setSignals(s);
    });
  }, []);

  const total = funnel[0] ? funnel[0].passed + funnel[0].failed : 0;
  const survived = funnel.at(-1)?.passed ?? 0;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight text-white">Validation Funnel</h1>
        <p className="text-sm text-zinc-500">
          {total} raw signals → {survived} validated ({total ? ((survived / total) * 100).toFixed(1) : 0}%
          survival). Fewer, better trades is the design intent.
        </p>
      </header>
      <FunnelChart stages={funnel} />
      <SignalsTable signals={signals} />
    </div>
  );
}
