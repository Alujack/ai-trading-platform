"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { pipsBetween, riskReward } from "@/lib/signals";
import { C, mono, tint } from "@/lib/theme";
import type { Signal, SignalsResponse, SignalStatus } from "@/lib/types";
import { Panel } from "./ui";

const COLS = "1.3fr 1fr 0.8fr 1fr 0.7fr 24px";

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

function fmtPrice(symbol: string, v: string): string {
  const dp = symbol === "EURUSD" ? 4 : symbol === "BTCUSD" ? 0 : 2;
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp }) : v;
}

export function RecentSignalsPanel({ limit = 8, title = "Recent Signals" }: { limit?: number; title?: string }) {
  const { data, isLoading } = useSWR<SignalsResponse>(`/api/signals?limit=${limit}`, fetcher, { refreshInterval: 30_000 });
  const rows = data?.data ?? [];
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <Panel title={title} right={<span style={{ fontSize: 11, color: C.muted2 }}>click a row for details</span>} noBody>
      <div style={{ display: "grid", gridTemplateColumns: COLS, padding: "8px 16px", fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted2, borderBottom: `1px solid ${C.hair}` }}>
        <span>Symbol</span>
        <span>Strategy</span>
        <span style={{ textAlign: "right" }}>Conf</span>
        <span style={{ textAlign: "center" }}>Status</span>
        <span style={{ textAlign: "right" }}>Time</span>
        <span />
      </div>

      {isLoading && <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>Loading…</p>}
      {!isLoading && rows.length === 0 && (
        <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>No signals yet.</p>
      )}
      {rows.map((s) => (
        <Row key={s.id} s={s} open={openId === s.id} onToggle={() => setOpenId(openId === s.id ? null : s.id)} />
      ))}
    </Panel>
  );
}

function Row({ s, open, onToggle }: { s: Signal; open: boolean; onToggle: () => void }) {
  const long = s.direction === "LONG";
  const dirColor = long ? C.up : C.down;
  const st = STATUS[s.status];
  const confTone = s.confidenceScore >= 65 ? C.up : s.confidenceScore >= 45 ? C.warn : C.down;

  return (
    <div style={{ borderBottom: `1px solid ${tint("#ffffff", 0.04)}` }}>
      <button
        onClick={onToggle}
        style={{
          width: "100%",
          display: "grid",
          gridTemplateColumns: COLS,
          alignItems: "center",
          padding: "11px 16px",
          background: open ? "rgba(255,255,255,0.03)" : "transparent",
          border: "none",
          cursor: "pointer",
          fontFamily: "inherit",
          textAlign: "left",
          color: C.text,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ padding: "2px 6px", borderRadius: 5, fontSize: 9, fontWeight: 700, background: tint(dirColor, 0.14), color: dirColor }}>
            {s.direction}
          </span>
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>{s.symbol}</span>
        </div>
        <span style={{ fontSize: 11, color: C.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.strategyName ?? "—"}</span>
        <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, fontWeight: 600, color: confTone }}>{s.confidenceScore}</span>
        <span style={{ textAlign: "center" }}>
          <span style={{ padding: "2px 8px", borderRadius: 20, fontSize: 9, fontWeight: 600, letterSpacing: ".04em", background: st.bg, color: st.color }}>{s.status}</span>
        </span>
        <span style={{ textAlign: "right", fontFamily: mono, fontSize: 10.5, color: C.muted2 }}>{ago(s.createdAt)}</span>
        <span style={{ textAlign: "center", color: C.muted2, fontSize: 13, transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }}>›</span>
      </button>

      {open && (
        <div style={{ padding: "0 16px 14px 16px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
            <Detail label="Entry" value={fmtPrice(s.symbol, s.entryPrice)} tone={C.gold} />
            <Detail label="Stop" value={fmtPrice(s.symbol, s.stopLoss)} tone={C.down} sub={`${Math.round(pipsBetween(s.symbol, Number(s.entryPrice), Number(s.stopLoss)))} pips`} />
            <Detail label="Target" value={fmtPrice(s.symbol, s.takeProfit)} tone={C.up} sub={`${Math.round(pipsBetween(s.symbol, Number(s.entryPrice), Number(s.takeProfit)))} pips`} />
            <Detail label="Risk : Reward" value={`1 : ${riskReward(Number(s.entryPrice), Number(s.stopLoss), Number(s.takeProfit)).toFixed(1)}`} tone={C.text} />
          </div>
          <div style={{ marginTop: 10, background: C.fill, border: `1px solid ${C.line}`, borderRadius: 9, padding: "10px 12px" }}>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted, marginBottom: 5 }}>AI reasoning</div>
            <p style={{ margin: 0, fontFamily: mono, fontSize: 11.5, lineHeight: 1.6, color: C.text3, whiteSpace: "pre-wrap" }}>{s.aiReasoning || "—"}</p>
          </div>
        </div>
      )}
    </div>
  );
}

function Detail({ label, value, tone, sub }: { label: string; value: string; tone: string; sub?: string }) {
  return (
    <div style={{ background: C.fill, border: `1px solid ${C.line}`, borderRadius: 9, padding: "8px 11px" }}>
      <div style={{ fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted }}>{label}</div>
      <div style={{ fontFamily: mono, fontSize: 13.5, fontWeight: 600, color: tone, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted2, marginTop: 1 }}>{sub}</div>}
    </div>
  );
}
