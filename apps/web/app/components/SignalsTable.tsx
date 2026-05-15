"use client";

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

export function SignalsTable() {
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
        <span className="text-xs text-neutral-500">last 5</span>
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
              {signals.map((s) => (
                <tr
                  key={s.id}
                  className="border-b border-neutral-800/60 last:border-b-0 hover:bg-neutral-800/30"
                >
                  <td className="px-5 py-3 font-medium text-neutral-100">{s.symbol}</td>
                  <td className="px-5 py-3">
                    <span
                      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${
                        s.direction === "LONG"
                          ? "bg-emerald-500/10 text-emerald-300"
                          : "bg-rose-500/10 text-rose-300"
                      }`}
                    >
                      {s.direction === "LONG" ? "▲" : "▼"} {s.direction}
                    </span>
                  </td>
                  <td
                    className={`px-5 py-3 text-right font-mono tabular-nums ${confidenceTone(s.confidenceScore)}`}
                  >
                    {s.confidenceScore}
                  </td>
                  <td className="px-5 py-3">
                    <span
                      className={`inline-block rounded border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${STATUS_TONE[s.status]}`}
                    >
                      {s.status}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-right font-mono text-xs text-neutral-400 tabular-nums">
                    {fmtTimestamp(s.createdAt)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
