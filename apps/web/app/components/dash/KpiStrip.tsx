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
  index: string;
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
      index: "01",
    },
    {
      label: "Day P&L",
      value: a ? usd(a.dayPnL, true) : "—",
      sub: a ? `${a.dayPnL >= 0 ? "+" : ""}${a.dayPnLPct.toFixed(2)}% today` : "—",
      tone: a && a.dayPnL < 0 ? C.down : C.up,
      index: "02",
    },
    {
      label: "Open risk",
      value: a ? usd(a.openRisk) : "—",
      sub: a ? `${a.openRiskPct.toFixed(1)}% of equity` : "—",
      tone: C.gold,
      index: "03",
    },
    {
      label: "Win rate",
      value: perf ? `${perf.winRate.toFixed(1)}%` : "—",
      sub: perf ? `${perf.totalTrades} closed trades` : "—",
      tone: C.text,
      index: "04",
    },
    {
      label: "Max drawdown",
      value: perf ? `−${perf.maxDrawdown.toFixed(1)}%` : "—",
      sub: "limit −10%",
      tone: C.down,
      index: "05",
    },
  ];

  return (
    <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      {kpis.map((k) => (
        <div
          key={k.label}
          style={{
            position: "relative",
            overflow: "hidden",
            background: "linear-gradient(145deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012))",
            border: `1px solid ${C.line2}`,
            borderRadius: 14,
            padding: "15px 16px",
            boxShadow: "0 14px 30px rgba(0,0,0,.12)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <div style={{ fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".1em", color: C.muted, marginBottom: 9 }}>
              {k.label}
            </div>
            <span style={{ fontFamily: mono, color: C.muted2, fontSize: 9 }}>{k.index}</span>
          </div>
          <div style={{ fontFamily: mono, fontSize: 22, fontWeight: 600, letterSpacing: "-0.035em", color: k.tone }}>
            {k.value}
          </div>
          <div style={{ fontSize: 11, color: C.muted2, marginTop: 3 }}>{k.sub}</div>
        </div>
      ))}
    </section>
  );
}
