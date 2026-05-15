"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { Candle, Symbol, Timeframe } from "@/lib/types";
import { Skeleton } from "./Skeleton";

interface IndicatorSidebarProps {
  symbol: Symbol;
  timeframe: Timeframe;
}

function priceDigits(symbol: Symbol): number {
  if (symbol === "BTCUSD") return 2;
  if (symbol === "EURUSD") return 4;
  return 2;
}

function fmt(value: string | null | undefined, digits: number): string {
  if (value == null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function rsiTone(rsi: number | null): string {
  if (rsi == null) return "text-neutral-500";
  if (rsi < 30) return "text-emerald-400";
  if (rsi > 70) return "text-rose-400";
  return "text-neutral-100";
}

export function IndicatorSidebar({ symbol, timeframe }: IndicatorSidebarProps) {
  const key = `/api/candles?symbol=${symbol}&timeframe=${timeframe}&limit=1`;
  const { data, error, isLoading } = useSWR<Candle[]>(key, fetcher, {
    refreshInterval: 30_000,
    revalidateOnFocus: false,
  });

  const latest = data?.[0];
  const ind = latest?.indicators ?? null;
  const digits = priceDigits(symbol);
  const rsiNum = ind?.rsi != null ? Number(ind.rsi) : null;

  return (
    <aside className="flex flex-col gap-4 rounded-lg border border-neutral-800 bg-neutral-900/30 p-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
          Indicators
        </h2>
        <span className="text-xs text-neutral-500">
          {symbol} · {timeframe}
        </span>
      </div>

      {isLoading && (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
        </div>
      )}

      {error && (
        <p className="text-sm text-rose-400">Failed to load indicators: {error.message}</p>
      )}

      {!isLoading && !error && !latest && (
        <p className="text-sm text-neutral-500">No candle data yet for this pair.</p>
      )}

      {!isLoading && !error && latest && (
        <>
          <div className="rounded-md border border-neutral-800 bg-neutral-950/60 p-3">
            <div className="text-[10px] uppercase tracking-wide text-neutral-500">Last close</div>
            <div className="mt-1 font-mono text-lg tabular-nums text-neutral-100">
              {fmt(latest.close, digits)}
            </div>
            <div className="mt-1 text-[10px] text-neutral-500">
              {new Date(latest.timestamp).toISOString().replace("T", " ").slice(0, 16)} UTC
            </div>
          </div>

          <dl className="grid grid-cols-1 gap-3">
            <IndicatorRow
              label="RSI (14)"
              value={fmt(ind?.rsi ?? null, 1)}
              tone={rsiTone(rsiNum)}
              hint={
                rsiNum == null
                  ? undefined
                  : rsiNum < 30
                    ? "oversold"
                    : rsiNum > 70
                      ? "overbought"
                      : "neutral"
              }
            />
            <IndicatorRow label="EMA 20" value={fmt(ind?.ema20 ?? null, digits)} />
            <IndicatorRow label="EMA 50" value={fmt(ind?.ema50 ?? null, digits)} />
            <IndicatorRow label="ATR" value={fmt(ind?.atr ?? null, digits)} />
          </dl>
        </>
      )}
    </aside>
  );
}

function IndicatorRow({
  label,
  value,
  tone = "text-neutral-100",
  hint,
}: {
  label: string;
  value: string;
  tone?: string;
  hint?: string;
}) {
  return (
    <div className="flex items-center justify-between rounded-md border border-neutral-800/60 bg-neutral-950/40 px-3 py-2.5">
      <div>
        <dt className="text-xs text-neutral-500">{label}</dt>
        {hint && <div className="text-[10px] uppercase tracking-wide text-neutral-600">{hint}</div>}
      </div>
      <dd className={`font-mono text-base tabular-nums ${tone}`}>{value}</dd>
    </div>
  );
}
