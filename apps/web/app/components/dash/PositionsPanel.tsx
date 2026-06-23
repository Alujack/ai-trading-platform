"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { C, mono, tint } from "@/lib/theme";
import type { Position, PositionsResponse } from "@/lib/types";
import { Panel } from "./ui";

function fmtPrice(symbol: string, n: number): string {
  const dp = symbol === "EURUSD" ? 4 : symbol === "BTCUSD" ? 0 : 2;
  return n.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function pnlStr(n: number): string {
  return `${n < 0 ? "−" : "+"}$${Math.abs(Math.round(n)).toLocaleString()}`;
}

const COLS = "1.4fr 1fr 1fr 1fr";

export function PositionsPanel() {
  const { data, isLoading } = useSWR<PositionsResponse>("/api/positions", fetcher, { refreshInterval: 30_000 });
  const positions = data?.positions ?? [];
  const unrealized = data?.account.unrealized ?? 0;

  return (
    <Panel
      title="Open Positions"
      right={
        <span style={{ fontFamily: mono, fontSize: 11, color: unrealized < 0 ? C.down : C.up }}>
          {pnlStr(unrealized)} unrealized
        </span>
      }
      noBody
    >
      <div style={{ display: "grid", gridTemplateColumns: COLS, padding: "8px 16px", fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted2, borderBottom: `1px solid ${C.hair}` }}>
        <span>Symbol</span>
        <span style={{ textAlign: "right" }}>Entry</span>
        <span style={{ textAlign: "right" }}>Mark</span>
        <span style={{ textAlign: "right" }}>P&L</span>
      </div>

      {isLoading && <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>Loading…</p>}
      {!isLoading && positions.length === 0 && (
        <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>No open positions.</p>
      )}
      {positions.map((p) => (
        <Row key={p.id} p={p} />
      ))}
    </Panel>
  );
}

function Row({ p }: { p: Position }) {
  const long = p.direction === "LONG";
  const dirColor = long ? C.up : C.down;
  return (
    <div style={{ display: "grid", gridTemplateColumns: COLS, alignItems: "center", padding: "11px 16px", borderBottom: `1px solid ${tint("#ffffff", 0.04)}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ padding: "2px 6px", borderRadius: 5, fontSize: 9, fontWeight: 700, background: tint(dirColor, 0.14), color: dirColor }}>
          {p.direction}
        </span>
        <div>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>{p.symbol}</div>
          <div style={{ fontSize: 9.5, color: C.muted2 }}>{p.size.toFixed(2)} lot</div>
        </div>
      </div>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, color: C.text3 }}>{fmtPrice(p.symbol, p.entry)}</span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, color: "#d4d8de" }}>{fmtPrice(p.symbol, p.mark)}</span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12.5, fontWeight: 600, color: p.pnl < 0 ? C.down : C.up }}>{pnlStr(p.pnl)}</span>
    </div>
  );
}
