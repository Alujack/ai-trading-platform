"use client";

import { Activity, CircleAlert, RadioTower, ShieldCheck } from "lucide-react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { findLargestCandleGap, formatDuration, marketStatus } from "@/lib/market";
import type { Candle, Symbol, Timeframe } from "@/lib/types";
import type { RtStatus } from "@/lib/useRealtime";

export function MarketCommandHeader({
  symbol,
  timeframe,
  rtStatus,
}: {
  symbol: Symbol;
  timeframe: Timeframe;
  rtStatus: RtStatus;
}) {
  const { data: candles } = useSWR<Candle[]>(
    `/api/candles?symbol=${symbol}&timeframe=${timeframe}&limit=300`,
    fetcher,
    { refreshInterval: 30_000 },
  );
  const ordered = (candles ?? []).slice().sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const latest = ordered[ordered.length - 1];
  const status = marketStatus(symbol, timeframe, latest?.timestamp);
  const gap = findLargestCandleGap(ordered, timeframe, symbol);

  return (
    <section className="command-header">
      <div className="command-heading">
        <div className="command-kicker">Vigil command / {symbol}</div>
        <div className="command-title-row">
          <h1>Market decision desk</h1>
          <span>{timeframe}</span>
        </div>
        <p>Chart, setup quality, and execution risk—kept in one operational view.</p>
      </div>

      <div className="command-diagnostics" aria-label="Market diagnostics">
        <Diagnostic
          icon={<Activity size={15} />}
          label="Market"
          value={status.label}
          detail={status.session}
          tone={status.state}
        />
        <Diagnostic
          icon={<RadioTower size={15} />}
          label="Event channel"
          value={rtStatus === "live" ? "Connected" : rtStatus === "connecting" ? "Connecting" : "Offline"}
          detail={status.detail}
          tone={rtStatus === "live" ? "neutral" : rtStatus === "connecting" ? "closed" : "delayed"}
        />
        <Diagnostic
          icon={gap ? <CircleAlert size={15} /> : <ShieldCheck size={15} />}
          label="History integrity"
          value={gap ? `${formatDuration(gap.durationMs)} gap detected` : "Continuous"}
          detail={gap ? "Missing bars are not treated as live data" : "No abnormal gaps in this window"}
          tone={gap ? "delayed" : "live"}
        />
      </div>
    </section>
  );
}

function Diagnostic({
  icon,
  label,
  value,
  detail,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: "live" | "delayed" | "closed" | "missing" | "neutral";
}) {
  return (
    <div className="diagnostic" data-tone={tone}>
      <span className="diagnostic-icon" aria-hidden="true">{icon}</span>
      <span className="diagnostic-copy">
        <span className="diagnostic-label">{label}</span>
        <strong>{value}</strong>
        <span className="diagnostic-detail">{detail}</span>
      </span>
    </div>
  );
}
