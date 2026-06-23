"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { MarketBias, MarketContext, Symbol, Timeframe } from "@/lib/types";
import { Skeleton } from "./Skeleton";

function biasPill(bias: MarketBias): string {
  switch (bias) {
    case "Bullish":
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
    case "Bearish":
      return "border-rose-500/40 bg-rose-500/10 text-rose-300";
    default:
      return "border-neutral-600/50 bg-neutral-700/20 text-neutral-300";
  }
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export function MarketContextCard({
  symbol,
  timeframe,
}: {
  symbol: Symbol;
  timeframe: Timeframe;
}) {
  const { data, error, isLoading } = useSWR<MarketContext>(
    `/api/market-context?symbol=${symbol}&timeframe=${timeframe}`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000, keepPreviousData: true },
  );

  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-900/30">
      <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
          Market Context
        </h2>
        <div className="flex items-center gap-2">
          {data && (
            <span
              className={`rounded-full border px-2.5 py-0.5 text-xs font-medium uppercase tracking-wide ${biasPill(
                data.bias,
              )}`}
            >
              {data.bias}
            </span>
          )}
          <span className="text-xs text-neutral-500">
            {symbol} · {timeframe}
          </span>
        </div>
      </div>

      {isLoading && !data && (
        <div className="space-y-3 p-5">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <p className="pt-1 text-xs text-neutral-600">Generating AI market briefing…</p>
        </div>
      )}

      {error && (
        <p className="px-5 py-6 text-sm text-rose-400">
          AI briefing unavailable: {error.message}
        </p>
      )}

      {data && (
        <div className="space-y-4 p-5">
          <p className="text-sm leading-relaxed text-neutral-200">{data.summary}</p>

          {data.keyLevels.length > 0 && (
            <div>
              <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-neutral-500">
                Key levels
              </h3>
              <ul className="space-y-1">
                {data.keyLevels.map((level, i) => (
                  <li key={i} className="font-mono text-xs text-neutral-300">
                    {level}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.risks.length > 0 && (
            <div>
              <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-neutral-500">
                Risks
              </h3>
              <ul className="space-y-1">
                {data.risks.map((risk, i) => (
                  <li key={i} className="text-xs text-amber-300/90">
                    • {risk}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="text-[10px] text-neutral-600">
            Generated {fmtTime(data.generatedAt)}
            {data.cached ? " · cached" : ""}
          </p>
        </div>
      )}
    </section>
  );
}
