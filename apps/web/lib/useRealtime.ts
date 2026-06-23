"use client";

import { useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";
import { API_BASE } from "./api";

export type RtStatus = "connecting" | "live" | "offline";

/**
 * Opens an SSE stream and revalidates SWR data the instant the backend pushes a
 * candle/signal event — so the dashboard updates without polling lag.
 * Returns the connection status for a "live" indicator.
 */
export function useRealtime(): RtStatus {
  const { mutate } = useSWRConfig();
  const [status, setStatus] = useState<RtStatus>("connecting");
  const lastRef = useRef(0);

  useEffect(() => {
    const es = new EventSource(`${API_BASE}/api/stream`);

    es.onopen = () => setStatus("live");

    const revalidate = () => {
      // Coalesce bursts: refresh all keys at most ~once/second.
      const now = Date.now();
      if (now - lastRef.current < 800) return;
      lastRef.current = now;
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
