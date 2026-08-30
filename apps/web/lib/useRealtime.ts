"use client";

import { useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";
import { API_BASE } from "./api";
import type { Candle, RealtimeCandle } from "./types";

export type RtStatus = "connecting" | "live" | "offline";

function isRealtimeCandle(value: unknown): value is RealtimeCandle {
  if (!value || typeof value !== "object") return false;
  const candle = value as Partial<RealtimeCandle>;
  return (
    typeof candle.symbol === "string" &&
    typeof candle.timeframe === "string" &&
    typeof candle.timestamp === "string" &&
    typeof candle.open === "string" &&
    typeof candle.high === "string" &&
    typeof candle.low === "string" &&
    typeof candle.close === "string" &&
    typeof candle.volume === "string"
  );
}

function mergeRealtimeCandle(
  current: Candle[] | undefined,
  live: RealtimeCandle,
): Candle[] | undefined {
  if (!current?.length) return current;
  const liveTime = Date.parse(live.timestamp);
  if (!Number.isFinite(liveTime)) return current;

  const existingIndex = current.findIndex(
    (row) => Date.parse(row.timestamp) === liveTime,
  );
  const existing = existingIndex >= 0 ? current[existingIndex] : undefined;
  const next: Candle = {
    id: existing?.id ?? `live:${live.symbol}:${live.timeframe}:${liveTime}`,
    ...live,
    createdAt: existing?.createdAt ?? new Date().toISOString(),
    indicators: existing?.indicators ?? null,
  };

  if (existingIndex >= 0) {
    const rows = current.slice();
    rows[existingIndex] = next;
    return rows;
  }

  // Candle endpoints are newest-first. Keep their loaded window size stable as
  // each newly opened bar arrives through the stream.
  return [next, ...current].slice(0, current.length);
}

/**
 * Opens an SSE stream and writes full candle events directly into SWR's local
 * cache. Components receive the latest OHLC without refetching their history.
 * Returns the connection status for a "live" indicator.
 */
export function useRealtime(): RtStatus {
  const { mutate } = useSWRConfig();
  const [status, setStatus] = useState<RtStatus>("connecting");
  const lastRef = useRef(new Map<string, number>());

  useEffect(() => {
    const es = new EventSource(`${API_BASE}/api/stream`);

    es.onopen = () => setStatus("live");

    const revalidate = (event: MessageEvent<string>) => {
      const shouldRefresh = (series: string) => {
        const now = Date.now();
        const last = lastRef.current.get(series) ?? 0;
        if (now - last < 800) return false;
        lastRef.current.set(series, now);
        return true;
      };

      try {
        const update = JSON.parse(event.data) as {
          type?: string;
          symbol?: string;
          timeframe?: string;
          candle?: unknown;
        };
        if (update.type === "candle" && update.symbol) {
          const series = `${update.symbol}:${update.timeframe ?? "all"}`;
          if (!shouldRefresh(series)) return;
          const prefix = update.timeframe
            ? `/api/candles?symbol=${update.symbol}&timeframe=${update.timeframe}&`
            : `/api/candles?symbol=${update.symbol}&`;
          if (isRealtimeCandle(update.candle)) {
            const candle = update.candle;
            void mutate<Candle[]>(
              (key) => typeof key === "string" && key.startsWith(prefix),
              (current) => mergeRealtimeCandle(current, candle),
              { revalidate: false },
            );
          } else {
            // Older workers send metadata-only events; keep compatibility
            // during rolling deploys by fetching just the affected series.
            void mutate(
              (key) => typeof key === "string" && key.startsWith(prefix),
              undefined,
              { revalidate: true },
            );
          }
          return;
        }
      } catch {
        // Unknown events retain the broad refresh behavior for compatibility.
      }
      if (!shouldRefresh("all")) return;
      void mutate(() => true, undefined, { revalidate: true });
    };

    es.onmessage = revalidate;
    es.onerror = () => {
      setStatus("offline");
      // EventSource auto-reconnects; reflect that we're trying again.
      setTimeout(() => setStatus((s) => (s === "offline" ? "connecting" : s)), 1500);
    };

    return () => es.close();
  }, [mutate]);

  return status;
}
