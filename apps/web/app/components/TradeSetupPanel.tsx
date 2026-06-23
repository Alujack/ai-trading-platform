"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { pickActiveSignal, pipsBetween, riskReward, usdAt001Lot } from "@/lib/signals";
import type { SignalsResponse, Symbol } from "@/lib/types";

function fmt(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 5 });
}

export function TradeSetupPanel({ symbol }: { symbol: Symbol }) {
  const { data, error, isLoading } = useSWR<SignalsResponse>(
    `/api/signals?symbol=${symbol}&limit=8`,
    fetcher,
    { refreshInterval: 30_000 },
  );
  const signal = pickActiveSignal(data?.data ?? []);

  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-900/30">
      <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
          Trade Setup
        </h2>
        <span className="text-xs text-neutral-500">{symbol}</span>
      </div>

      {isLoading && <p className="px-5 py-6 text-sm text-neutral-500">Loading…</p>}
      {error && <p className="px-5 py-6 text-sm text-rose-400">Failed to load: {error.message}</p>}

      {!isLoading && !error && !signal && (
        <p className="px-5 py-6 text-sm text-neutral-500">
          No active setup for {symbol}. The engine posts one here as soon as a strategy fires and
          clears the AI + risk gate.
        </p>
      )}

      {signal && <Setup signal={signal} symbol={symbol} />}
    </section>
  );
}

function Setup({
  signal,
  symbol,
}: {
  signal: SignalsResponse["data"][number];
  symbol: Symbol;
}) {
  const entry = Number(signal.entryPrice);
  const stop = Number(signal.stopLoss);
  const target = Number(signal.takeProfit);
  const isLong = signal.direction === "LONG";
  const stopPips = Math.round(pipsBetween(symbol, entry, stop));
  const tpPips = Math.round(pipsBetween(symbol, entry, target));
  const rr = riskReward(entry, stop, target);

  return (
    <div className="p-5">
      <div className="mb-4 flex items-center gap-3">
        <span
          className={`rounded px-2.5 py-1 text-sm font-bold ${
            isLong ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300"
          }`}
        >
          {isLong ? "BUY" : "SELL"}
        </span>
        <span className="text-sm text-neutral-300">{signal.symbol}</span>
        <span className="font-mono text-xs text-neutral-500">{signal.timeframe}</span>
        {signal.strategyName && (
          <span className="rounded border border-neutral-800 px-2 py-0.5 font-mono text-[11px] text-neutral-400">
            {signal.strategyName}
          </span>
        )}
        <span className="ml-auto font-mono text-xs text-neutral-500">
          conf {signal.confidenceScore} · {signal.status}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <Level
          label="Entry"
          value={fmt(entry)}
          sub={`${isLong ? "buy" : "sell"} here`}
          tone="text-amber-300"
        />
        <Level
          label="Stop"
          value={fmt(stop)}
          sub={`${stopPips} pips · −$${usdAt001Lot(symbol, stopPips).toFixed(2)}`}
          tone="text-rose-300"
        />
        <Level
          label="Target"
          value={fmt(target)}
          sub={`${tpPips} pips · +$${usdAt001Lot(symbol, tpPips).toFixed(2)}`}
          tone="text-emerald-300"
        />
      </div>

      <div className="mt-3 flex items-center gap-4 font-mono text-xs text-neutral-500">
        <span>
          R:R <span className="text-neutral-300">1:{rr.toFixed(1)}</span>
        </span>
        <span>
          at 0.01 lot:{" "}
          <span className="text-emerald-300">+${usdAt001Lot(symbol, tpPips).toFixed(2)}</span>
          {" / "}
          <span className="text-rose-300">−${usdAt001Lot(symbol, stopPips).toFixed(2)}</span>
        </span>
      </div>

      <details className="mt-4 rounded-md border border-neutral-800/70 bg-neutral-950/40">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-neutral-400">
          Why this trade — AI reasoning
        </summary>
        <pre className="whitespace-pre-wrap px-3 pb-3 text-xs leading-relaxed text-neutral-300">
          {signal.aiReasoning}
        </pre>
      </details>
    </div>
  );
}

function Level({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone: string;
}) {
  return (
    <div className="rounded-md border border-neutral-800/60 bg-neutral-950/40 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">{label}</div>
      <div className={`mt-1 font-mono text-base tabular-nums ${tone}`}>{value}</div>
      <div className="mt-0.5 text-[10px] text-neutral-600">{sub}</div>
    </div>
  );
}
