"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { pickActiveSignal, pipsBetween, riskReward, usdAt001Lot } from "@/lib/signals";
import { C, mono, tint } from "@/lib/theme";
import type { Signal, SignalsResponse, Symbol } from "@/lib/types";

function fmt(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 5 });
}

export function ActiveSetupPanel({ symbol }: { symbol: Symbol }) {
  const { data } = useSWR<SignalsResponse>(`/api/signals?symbol=${symbol}&limit=8`, fetcher, {
    refreshInterval: 30_000,
  });
  const signal = pickActiveSignal(data?.data ?? []);

  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line2}`, borderRadius: 14, display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: `1px solid ${C.line}` }}>
        <h2 style={{ margin: 0, fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".08em", color: C.text3 }}>
          Active Setup
        </h2>
        <span style={{ fontFamily: mono, fontSize: 11, color: C.muted2 }}>{symbol}</span>
      </div>

      {!signal ? (
        <div style={{ padding: 16, fontSize: 12.5, color: C.muted, lineHeight: 1.6 }}>
          No active setup for {symbol}. The engine posts one here as soon as a strategy fires and clears the AI + risk gate.
        </div>
      ) : (
        <Setup signal={signal} symbol={symbol} />
      )}
    </div>
  );
}

function Setup({ signal, symbol }: { signal: Signal; symbol: Symbol }) {
  const [executed, setExecuted] = useState(false);
  const [showWhy, setShowWhy] = useState(false);

  const entry = Number(signal.entryPrice);
  const stop = Number(signal.stopLoss);
  const target = Number(signal.takeProfit);
  const isLong = signal.direction === "LONG";
  const stopPips = Math.round(pipsBetween(symbol, entry, stop));
  const tpPips = Math.round(pipsBetween(symbol, entry, target));
  const rr = riskReward(entry, stop, target);
  const conf = signal.confidenceScore;

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
      {/* direction + confidence */}
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              padding: "5px 11px",
              borderRadius: 8,
              fontSize: 13,
              fontWeight: 700,
              background: isLong ? tint(C.up, 0.14) : tint(C.down, 0.14),
              color: isLong ? C.up : C.down,
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" style={{ transform: isLong ? "none" : "rotate(180deg)" }}>
              <path d="M12 19V5M12 5l-6 6M12 5l6 6" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {isLong ? "BUY" : "SELL"}
          </span>
          {signal.strategyName && <span style={{ fontSize: 12, color: C.text3 }}>{signal.strategyName}</span>}
          <span style={{ marginLeft: "auto", fontFamily: mono, fontSize: 11, color: C.muted2 }}>{signal.timeframe}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 5 }}>
          <span style={{ fontSize: 11, color: C.muted }}>AI confidence</span>
          <span style={{ fontFamily: mono, fontSize: 13, fontWeight: 600, color: conf >= 65 ? C.up : C.warn }}>
            {conf}
            <span style={{ color: C.muted2 }}>/100</span>
          </span>
        </div>
        <div style={{ height: 6, borderRadius: 4, background: "rgba(255,255,255,0.06)", overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${conf}%`, borderRadius: 4, background: "var(--accent, #f0b429)" }} />
        </div>
      </div>

      {/* levels */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "11px 13px", borderRadius: 10, background: tint(C.gold, 0.07), border: `1px solid ${tint(C.gold, 0.2)}` }}>
          <div>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted }}>Entry</div>
            <div style={{ fontFamily: mono, fontSize: 17, fontWeight: 600, color: C.gold, marginTop: 2 }}>{fmt(entry)}</div>
          </div>
          <span style={{ fontSize: 11, color: C.muted }}>{isLong ? "buy" : "sell"} zone</span>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <LevelBox label="Stop" value={fmt(stop)} sub={`${stopPips} pips · −$${usdAt001Lot(symbol, stopPips).toFixed(2)}`} color={C.down} />
          <LevelBox label="Target" value={fmt(target)} sub={`${tpPips} pips · +$${usdAt001Lot(symbol, tpPips).toFixed(2)}`} color={C.up} />
        </div>
      </div>

      {/* risk gate */}
      <div style={{ padding: "12px 13px", borderRadius: 11, background: C.fill, border: `1px solid ${C.line}`, display: "flex", flexDirection: "column", gap: 9 }}>
        <Row label="Risk / Reward" value={`1 : ${rr.toFixed(1)}`} valueColor={rr >= 2 ? C.up : C.warn} />
        <Row label="At 0.01 lot" value={`+$${usdAt001Lot(symbol, tpPips).toFixed(2)} / −$${usdAt001Lot(symbol, stopPips).toFixed(2)}`} valueColor={C.text} />
        <div style={{ display: "flex", alignItems: "center", gap: 7, paddingTop: 7, borderTop: `1px solid ${C.line}` }}>
          <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 16, height: 16, borderRadius: "50%", background: tint(C.up, 0.18) }}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
              <path d="M5 13l4 4L19 7" stroke={C.up} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <span style={{ fontSize: 11.5, color: C.text3 }}>Passed AI + risk gate · within daily limit</span>
        </div>
      </div>

      {/* action */}
      {executed ? (
        <div style={{ display: "flex", gap: 9, alignItems: "center" }}>
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 8, padding: 12, borderRadius: 11, background: tint(C.up, 0.14), border: `1px solid ${tint(C.up, 0.35)}`, color: C.up, fontSize: 13, fontWeight: 600 }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Marked on chart
          </div>
          <button onClick={() => setExecuted(false)} style={btnGhost}>Undo</button>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 9 }}>
          <button
            onClick={() => setExecuted(true)}
            style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 8, padding: 13, borderRadius: 11, background: "var(--accent, #f0b429)", border: "none", color: "#1a1306", fontSize: 13.5, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path d="M5 3l14 9-14 9V3z" fill="currentColor" />
            </svg>
            Track this setup
          </button>
          <button style={btnGhost}>Dismiss</button>
        </div>
      )}

      {/* why */}
      {signal.aiReasoning && (
        <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12 }}>
          <button
            onClick={() => setShowWhy((v) => !v)}
            style={{ width: "100%", background: "transparent", border: "none", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 11.5, fontWeight: 600, color: C.text3, fontFamily: "inherit", padding: 0 }}
          >
            Why this trade — AI reasoning
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" style={{ color: C.muted2, transform: showWhy ? "rotate(180deg)" : "none" }}>
              <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          {showWhy && <p style={{ margin: "9px 0 0", fontSize: 12, lineHeight: 1.6, color: C.text3 }}>{signal.aiReasoning}</p>}
        </div>
      )}
    </div>
  );
}

const btnGhost = {
  padding: "13px 16px",
  borderRadius: 11,
  background: "transparent",
  border: `1px solid ${tint("#ffffff", 0.1)}`,
  color: C.text3,
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
} as const;

function LevelBox({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return (
    <div style={{ flex: 1, padding: "11px 13px", borderRadius: 10, background: tint(color, 0.06), border: `1px solid ${tint(color, 0.18)}` }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted }}>{label}</div>
      <div style={{ fontFamily: mono, fontSize: 15, fontWeight: 600, color, marginTop: 2 }}>{value}</div>
      <div style={{ fontSize: 10, color: C.muted2, marginTop: 2 }}>{sub}</div>
    </div>
  );
}

function Row({ label, value, valueColor }: { label: string; value: string; valueColor: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      <span style={{ fontSize: 11, color: C.muted }}>{label}</span>
      <span style={{ fontFamily: mono, fontSize: 12.5, fontWeight: 600, color: valueColor }}>{value}</span>
    </div>
  );
}
