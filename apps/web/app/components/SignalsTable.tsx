"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { Signal, SignalStatus, SignalsResponse } from "@/lib/types";
import { Skeleton } from "./Skeleton";

const STATUS_TONE: Record<SignalStatus, string> = {
  PENDING: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  ACTIVE: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  CLOSED: "bg-neutral-500/10 text-neutral-300 border-neutral-500/30",
  CANCELLED: "bg-rose-500/10 text-rose-300 border-rose-500/30",
};

function fmtTimestamp(iso: string): string {
  const d = new Date(iso);
  return `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 16)}`;
}

function confidenceTone(score: number): string {
  if (score >= 80) return "text-emerald-300";
  if (score >= 60) return "text-amber-300";
  return "text-rose-300";
}

function priceDigits(symbol: string): number {
  if (symbol === "EURUSD") return 4;
  return 2;
}

function fmtPrice(value: string, digits: number): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function riskReward(signal: Signal): string {
  const entry = Number(signal.entryPrice);
  const sl = Number(signal.stopLoss);
  const tp = Number(signal.takeProfit);
  if (!Number.isFinite(entry) || !Number.isFinite(sl) || !Number.isFinite(tp)) return "—";
  const risk = Math.abs(entry - sl);
  const reward = Math.abs(tp - entry);
  if (risk === 0) return "—";
  return `1:${(reward / risk).toFixed(2)}`;
}

export function SignalsTable() {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const { data, error, isLoading } = useSWR<SignalsResponse>(
    "/api/signals?limit=5",
    fetcher,
    { refreshInterval: 30_000, revalidateOnFocus: false },
  );

  const signals: Signal[] = data?.data ?? [];

  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-900/30">
      <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
          Recent Signals
        </h2>
        <span className="text-xs text-neutral-500">last 5 · click a row for details</span>
      </div>

      {isLoading && (
        <div className="flex flex-col gap-2 p-4">
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
        </div>
      )}

      {error && (
        <p className="px-5 py-6 text-sm text-rose-400">
          Failed to load signals: {error.message}
        </p>
      )}

      {!isLoading && !error && signals.length === 0 && (
        <p className="px-5 py-6 text-sm text-neutral-500">No signals recorded yet.</p>
      )}

      {!isLoading && !error && signals.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-[10px] uppercase tracking-wide text-neutral-500">
              <tr className="border-b border-neutral-800">
                <th className="px-5 py-2.5 text-left font-medium">Symbol</th>
                <th className="px-5 py-2.5 text-left font-medium">Direction</th>
                <th className="px-5 py-2.5 text-right font-medium">Confidence</th>
                <th className="px-5 py-2.5 text-left font-medium">Status</th>
                <th className="px-5 py-2.5 text-right font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => {
                const isOpen = expandedId === s.id;
                const digits = priceDigits(s.symbol);
                return (
                  <FragmentRow
                    key={s.id}
                    signal={s}
                    isOpen={isOpen}
                    digits={digits}
                    onToggle={() => setExpandedId(isOpen ? null : s.id)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function FragmentRow({
  signal,
  isOpen,
  digits,
  onToggle,
}: {
  signal: Signal;
  isOpen: boolean;
  digits: number;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer border-b border-neutral-800/60 last:border-b-0 hover:bg-neutral-800/30 ${
          isOpen ? "bg-neutral-800/30" : ""
        }`}
      >
        <td className="px-5 py-3 font-medium text-neutral-100">
          <span className="mr-2 inline-block w-3 text-neutral-500">{isOpen ? "▾" : "▸"}</span>
          {signal.symbol}
        </td>
        <td className="px-5 py-3">
          <span
            className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${
              signal.direction === "LONG"
                ? "bg-emerald-500/10 text-emerald-300"
                : "bg-rose-500/10 text-rose-300"
            }`}
          >
            {signal.direction === "LONG" ? "▲" : "▼"} {signal.direction}
          </span>
        </td>
        <td
          className={`px-5 py-3 text-right font-mono tabular-nums ${confidenceTone(signal.confidenceScore)}`}
        >
          {signal.confidenceScore}
        </td>
        <td className="px-5 py-3">
          <span
            className={`inline-block rounded border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${STATUS_TONE[signal.status]}`}
          >
            {signal.status}
          </span>
        </td>
        <td className="px-5 py-3 text-right font-mono text-xs text-neutral-400 tabular-nums">
          {fmtTimestamp(signal.createdAt)}
        </td>
      </tr>
      {isOpen && (
        <tr className="border-b border-neutral-800/60 bg-neutral-950/40">
          <td colSpan={5} className="px-5 py-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Detail label="Entry" value={fmtPrice(signal.entryPrice, digits)} />
              <Detail
                label="Stop loss"
                value={fmtPrice(signal.stopLoss, digits)}
                tone="text-rose-300"
              />
              <Detail
                label="Take profit"
                value={fmtPrice(signal.takeProfit, digits)}
                tone="text-emerald-300"
              />
              <Detail label="Risk:Reward" value={riskReward(signal)} />
            </div>
            <div className="mt-4 rounded-md border border-neutral-800/60 bg-neutral-950/60 p-3">
              <div className="text-[10px] uppercase tracking-wide text-neutral-500">
                AI reasoning
              </div>
              <p className="mt-1 font-mono text-xs leading-relaxed text-neutral-300">
                {signal.aiReasoning || "—"}
              </p>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function Detail({
  label,
  value,
  tone = "text-neutral-100",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="rounded-md border border-neutral-800/60 bg-neutral-950/40 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">{label}</div>
      <div className={`mt-1 font-mono text-sm tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}
