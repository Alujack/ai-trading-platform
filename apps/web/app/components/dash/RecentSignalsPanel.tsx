"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { C, mono, tint } from "@/lib/theme";
import type { Signal, SignalsResponse, SignalStatus } from "@/lib/types";
import { Panel } from "./ui";

const COLS = "1.3fr 1fr 0.8fr 1fr 0.7fr";

const STATUS: Record<SignalStatus, { color: string; bg: string }> = {
  ACTIVE: { color: C.up, bg: tint(C.up, 0.14) },
  PENDING: { color: C.warn, bg: tint(C.warn, 0.14) },
  CLOSED: { color: C.text3, bg: "rgba(255,255,255,0.06)" },
  CANCELLED: { color: "#8b929b", bg: "rgba(255,255,255,0.06)" },
};

function ago(iso: string): string {
  const diff = Date.now() - Date.parse(iso);
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "now";
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function RecentSignalsPanel({ limit = 8, title = "Recent Signals" }: { limit?: number; title?: string }) {
  const { data, isLoading } = useSWR<SignalsResponse>(`/api/signals?limit=${limit}`, fetcher, { refreshInterval: 30_000 });
  const rows = data?.data ?? [];

  return (
    <Panel title={title} right={<span style={{ fontSize: 11, color: C.muted2 }}>{rows.length} shown</span>} noBody>
      <div style={{ display: "grid", gridTemplateColumns: COLS, padding: "8px 16px", fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted2, borderBottom: `1px solid ${C.hair}` }}>
        <span>Symbol</span>
        <span>Strategy</span>
        <span style={{ textAlign: "right" }}>Conf</span>
        <span style={{ textAlign: "center" }}>Status</span>
        <span style={{ textAlign: "right" }}>Time</span>
      </div>

      {isLoading && <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>Loading…</p>}
      {!isLoading && rows.length === 0 && (
        <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>No signals yet.</p>
      )}
      {rows.map((s) => (
        <Row key={s.id} s={s} />
      ))}
    </Panel>
  );
}

function Row({ s }: { s: Signal }) {
  const long = s.direction === "LONG";
  const dirColor = long ? C.up : C.down;
  const st = STATUS[s.status];
  const confTone = s.confidenceScore >= 65 ? C.up : s.confidenceScore >= 45 ? C.warn : C.down;
  return (
    <div style={{ display: "grid", gridTemplateColumns: COLS, alignItems: "center", padding: "11px 16px", borderBottom: `1px solid ${tint("#ffffff", 0.04)}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ padding: "2px 6px", borderRadius: 5, fontSize: 9, fontWeight: 700, background: tint(dirColor, 0.14), color: dirColor }}>
          {s.direction}
        </span>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>{s.symbol}</span>
      </div>
      <span style={{ fontSize: 11, color: C.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {s.strategyName ?? "—"}
      </span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, fontWeight: 600, color: confTone }}>{s.confidenceScore}</span>
      <span style={{ textAlign: "center" }}>
        <span style={{ padding: "2px 8px", borderRadius: 20, fontSize: 9, fontWeight: 600, letterSpacing: ".04em", background: st.bg, color: st.color }}>
          {s.status}
        </span>
      </span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 10.5, color: C.muted2 }}>{ago(s.createdAt)}</span>
    </div>
  );
}
