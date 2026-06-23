"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { C, mono } from "@/lib/theme";
import type { Performance, PositionsResponse } from "@/lib/types";
import { Panel, ProgressBar, StatusDot } from "./ui";

const RISK_PER_TRADE_PCT = Number(process.env.NEXT_PUBLIC_RISK_PER_TRADE_PCT ?? "1");

function money(n: number): string {
  return `$${Math.round(n).toLocaleString()}`;
}

export function RiskEnginePanel() {
  const { data: pos } = useSWR<PositionsResponse>("/api/positions", fetcher, { refreshInterval: 30_000 });
  const { data: perf } = useSWR<Performance>("/api/performance", fetcher, { refreshInterval: 60_000 });
  const a = pos?.account;

  const equity = a?.equity ?? 0;
  const dailyLossLimit = equity * 0.03;
  const dailyLoss = a && a.dayPnL < 0 ? Math.abs(a.dayPnL) : 0;
  const dailyPct = dailyLossLimit > 0 ? (dailyLoss / dailyLossLimit) * 100 : 0;

  const drawdown = perf?.maxDrawdown ?? 0;
  const ddPct = (drawdown / 10) * 100;

  const exposure = a?.openRisk ?? 0;
  const exposurePct = a?.openRiskPct ?? 0;

  const paused = dailyLoss >= dailyLossLimit && dailyLossLimit > 0;

  return (
    <Panel
      title="Risk Engine"
      right={
        paused ? (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: C.warn }}>
            <StatusDot color={C.warn} /> Paused
          </span>
        ) : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: C.up }}>
            <StatusDot color={C.up} /> Trading enabled
          </span>
        )
      }
      bodyStyle={{ display: "flex", flexDirection: "column", gap: 16 }}
    >
      <Meter label="Daily loss limit" right={`${money(dailyLoss)} / ${money(dailyLossLimit)} · 3%`} rightColor={C.muted} pct={dailyPct} color={C.up} />
      <Meter label="Drawdown" right={`${drawdown.toFixed(1)}% / 10% max`} rightColor={ddPct > 60 ? C.warn : C.muted} pct={ddPct} color={ddPct > 60 ? C.warn : C.up} />
      <Meter label="Open exposure" right={`${money(exposure)} · ${exposurePct.toFixed(1)}%`} rightColor={C.muted} pct={Math.min(exposurePct / 5 * 100, 100)} color={C.blue} />

      <div style={{ paddingTop: 14, borderTop: `1px solid ${C.line}`, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <Stat label="Risk / trade" value={`${RISK_PER_TRADE_PCT.toFixed(1)}%`} />
        <Stat label="Open trades" value={`${a?.openCount ?? 0} / ${a?.maxOpen ?? 5}`} />
      </div>
    </Panel>
  );
}

function Meter({ label, right, rightColor, pct, color }: { label: string; right: string; rightColor: string; pct: number; color: string }) {
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontSize: 11.5, color: C.text3 }}>{label}</span>
        <span style={{ fontFamily: mono, fontSize: 11.5, color: rightColor }}>{right}</span>
      </div>
      <ProgressBar pct={pct} color={color} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted }}>{label}</div>
      <div style={{ fontFamily: mono, fontSize: 15, fontWeight: 600, marginTop: 2 }}>{value}</div>
    </div>
  );
}
