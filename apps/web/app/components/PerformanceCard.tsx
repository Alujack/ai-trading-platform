"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { Performance } from "@/lib/types";
import { Skeleton } from "./Skeleton";

function fmtMoney(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function pnlTone(n: number): string {
  if (n > 0) return "text-emerald-300";
  if (n < 0) return "text-rose-300";
  return "text-neutral-100";
}

function winRateTone(pct: number): string {
  if (pct >= 60) return "text-emerald-300";
  if (pct >= 40) return "text-amber-300";
  return "text-rose-300";
}

function rrTone(rr: number): string {
  if (rr >= 2) return "text-emerald-300";
  if (rr >= 1) return "text-amber-300";
  return "text-rose-300";
}

export function PerformanceCard() {
  const { data, error, isLoading } = useSWR<Performance>("/api/performance", fetcher, {
    refreshInterval: 30_000,
    revalidateOnFocus: false,
  });

  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-900/30">
      <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
          Performance
        </h2>
        <span className="text-xs text-neutral-500">closed trades</span>
      </div>

      {isLoading && (
        <div className="grid grid-cols-2 gap-3 p-4 md:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      )}

      {error && (
        <p className="px-5 py-6 text-sm text-rose-400">
          Failed to load performance: {error.message}
        </p>
      )}

      {!isLoading && !error && data && data.totalTrades === 0 && (
        <p className="px-5 py-6 text-sm text-neutral-500">
          No closed trades yet. Stats will appear once paper trades have hit SL or TP.
        </p>
      )}

      {!isLoading && !error && data && data.totalTrades > 0 && (
        <dl className="grid grid-cols-2 gap-3 p-4 md:grid-cols-5">
          <Stat label="Total trades" value={data.totalTrades.toString()} />
          <Stat
            label="Win rate"
            value={`${data.winRate.toFixed(1)}%`}
            tone={winRateTone(data.winRate)}
          />
          <Stat
            label="Total P&L"
            value={fmtMoney(data.totalPnL)}
            tone={pnlTone(data.totalPnL)}
          />
          <Stat
            label="Max drawdown"
            value={fmtMoney(data.maxDrawdown)}
            tone="text-rose-300"
          />
          <Stat
            label="Avg R:R"
            value={data.averageRR.toFixed(2)}
            tone={rrTone(data.averageRR)}
          />
        </dl>
      )}
    </section>
  );
}

function Stat({
  label,
  value,
  tone = "text-neutral-100",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="rounded-md border border-neutral-800/60 bg-neutral-950/40 px-3 py-2.5">
      <dt className="text-[10px] uppercase tracking-wide text-neutral-500">{label}</dt>
      <dd className={`mt-1 font-mono text-lg tabular-nums ${tone}`}>{value}</dd>
    </div>
  );
}
