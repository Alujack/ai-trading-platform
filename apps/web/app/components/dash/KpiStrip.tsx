"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { C, mono } from "@/lib/theme";
import type { Performance, PositionsResponse } from "@/lib/types";

interface Kpi {
  label: string;
  value: string;
  sub: string;
  tone: string;
}

function usd(n: number, signed = false): string {
  const s = `$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  if (!signed) return s;
  return `${n < 0 ? "−" : "+"}${s}`;
}

export function KpiStrip() {
  const { data: perf } = useSWR<Performance>("/api/performance", fetcher, { refreshInterval: 60_000 });
  const { data: pos } = useSWR<PositionsResponse>("/api/positions", fetcher, { refreshInterval: 30_000 });
  const a = pos?.account;

  const kpis: Kpi[] = [
    {
      label: "Account equity",
      value: a ? usd(a.equity) : "—",
      sub: "paper account",
      tone: C.text,
    },
    {
      label: "Day P&L",
      value: a ? usd(a.dayPnL, true) : "—",
      sub: a ? `${a.dayPnL >= 0 ? "+" : ""}${a.dayPnLPct.toFixed(2)}% today` : "—",
      tone: a && a.dayPnL < 0 ? C.down : C.up,
    },
    {
      label: "Open risk",
      value: a ? usd(a.openRisk) : "—",
      sub: a ? `${a.openRiskPct.toFixed(1)}% of equity` : "—",
      tone: C.gold,
    },
    {
      label: "Win rate",
      value: perf ? `${perf.winRate.toFixed(1)}%` : "—",
      sub: perf ? `${perf.totalTrades} closed trades` : "—",
      tone: C.text,
    },
    {
      label: "Max drawdown",
      value: perf ? `−${perf.maxDrawdown.toFixed(1)}%` : "—",
      sub: "limit −10%",
      tone: C.down,
    },
  ];

  return (
    <section style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 12 }}>
      {kpis.map((k) => (
        <div
          key={k.label}
          style={{ background: C.panel, border: `1px solid ${C.line2}`, borderRadius: 13, padding: "14px 16px" }}
        >
          <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".09em", color: C.muted, marginBottom: 8 }}>
            {k.label}
          </div>
          <div style={{ fontFamily: mono, fontSize: 23, fontWeight: 600, letterSpacing: "-0.02em", color: k.tone }}>
            {k.value}
          </div>
          <div style={{ fontSize: 11, color: C.muted2, marginTop: 3 }}>{k.sub}</div>
        </div>
      ))}
    </section>
  );
}
