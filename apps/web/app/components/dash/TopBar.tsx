"use client";

import { ChevronDown, TrendingDown, TrendingUp } from "lucide-react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { C, mono, tint } from "@/lib/theme";
import {
  SYMBOLS,
  type AiProviderState,
  type Candle,
  type PositionsResponse,
  type Symbol,
} from "@/lib/types";

const META: Record<Symbol, { badge: string; name: string; grad: string; fg: string }> = {
  XAUUSD: { badge: "Au", name: "Gold Spot", grad: "linear-gradient(135deg,#f5cd5c,#d99815)", fg: "#2a1f06" },
  EURUSD: { badge: "€", name: "Euro / Dollar", grad: "linear-gradient(135deg,#6f9bf0,#3a63c4)", fg: "#0a1430" },
  BTCUSD: { badge: "₿", name: "Bitcoin", grad: "linear-gradient(135deg,#f7a14a,#d97515)", fg: "#2a1606" },
};

const AI_LABEL: Record<string, string> = { mock: "Mock", anthropic: "Claude", gemini: "Gemini" };

function fmtPrice(n: number, symbol: Symbol): string {
  const dp = symbol === "EURUSD" ? 4 : symbol === "BTCUSD" ? 0 : 2;
  return n.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function session(): string {
  const h = new Date().getUTCHours();
  if (h >= 7 && h < 12) return "London · open";
  if (h >= 12 && h < 16) return "London–NY · overlap";
  if (h >= 16 && h < 21) return "New York · open";
  if (h >= 21 || h < 0) return "Sydney · open";
  return "Tokyo · open";
}

export function TopBar({
  symbol,
  timeframe,
  onSymbolChange,
  onAiClick,
  rtStatus = "connecting",
}: {
  symbol: Symbol;
  timeframe: string;
  onSymbolChange: (s: Symbol) => void;
  onAiClick: () => void;
  rtStatus?: "connecting" | "live" | "offline";
}) {
  const { data: candles } = useSWR<Candle[]>(
    `/api/candles?symbol=${symbol}&timeframe=${timeframe}&limit=2`,
    fetcher,
    { refreshInterval: 15_000 },
  );
  const { data: ai } = useSWR<AiProviderState>("/api/ai-provider", fetcher, { revalidateOnFocus: false });
  const { data: pos } = useSWR<PositionsResponse>("/api/positions", fetcher, { refreshInterval: 30_000 });

  const sorted = (candles ?? []).slice().sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const last = sorted[sorted.length - 1];
  const prev = sorted[sorted.length - 2];
  const price = last ? Number(last.close) : null;
  const change = last && prev ? Number(last.close) - Number(prev.close) : 0;
  const changePct = last && prev && Number(prev.close) ? (change / Number(prev.close)) * 100 : 0;
  const up = change >= 0;

  const m = META[symbol];
  const aiActive = ai?.active ?? "mock";
  const aiLive = aiActive !== "mock";
  const equity = pos?.account.equity;

  return (
    <header
      style={{
        height: 60,
        flex: "none",
        borderBottom: `1px solid ${C.line}`,
        background: "rgba(10,11,14,0.85)",
        backdropFilter: "blur(10px)",
        display: "flex",
        alignItems: "center",
        gap: 18,
        padding: "0 24px",
        position: "sticky",
        top: 0,
        zIndex: 20,
      }}
    >
      {/* symbol switcher */}
      <div
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "6px 12px 6px 10px",
          border: `1px solid ${tint("#ffffff", 0.09)}`,
          borderRadius: 10,
          background: C.fill,
        }}
      >
        <div
          style={{
            width: 26,
            height: 26,
            borderRadius: 7,
            background: m.grad,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 12,
            fontWeight: 800,
            color: m.fg,
          }}
        >
          {m.badge}
        </div>
        <div style={{ lineHeight: 1.1 }}>
          <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: "-0.01em" }}>{symbol}</div>
          <div style={{ fontSize: 10, color: C.muted }}>{m.name}</div>
        </div>
        <ChevronDown size={14} style={{ color: C.muted, marginLeft: 2 }} />
        <select
          value={symbol}
          onChange={(e) => onSymbolChange(e.target.value as Symbol)}
          aria-label="Symbol"
          style={{ position: "absolute", inset: 0, opacity: 0, cursor: "pointer", width: "100%" }}
        >
          {SYMBOLS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {/* live price */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <span style={{ fontFamily: mono, fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em" }}>
          {price != null ? fmtPrice(price, symbol) : "—"}
        </span>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            fontFamily: mono,
            fontSize: 13,
            fontWeight: 600,
            color: up ? C.up : C.down,
          }}
        >
          {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
          {up ? "+" : ""}
          {changePct.toFixed(2)}%
        </span>
        <span style={{ fontFamily: mono, fontSize: 12, color: C.muted }}>
          {up ? "+" : ""}
          {change.toFixed(symbol === "EURUSD" ? 4 : 2)}
        </span>
      </div>

      <div style={{ flex: 1 }} />

      {/* realtime status + market session */}
      <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: C.text3 }} title={`Realtime stream: ${rtStatus}`}>
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: rtStatus === "live" ? C.up : rtStatus === "connecting" ? C.warn : C.down,
            animation: rtStatus === "live" ? "livedot 2s ease-in-out infinite" : undefined,
          }}
        />
        {rtStatus === "live" ? "Live" : rtStatus === "connecting" ? "Connecting" : "Offline"}
        <span style={{ color: C.muted2 }}>·</span>
        {session()}
      </div>
      <span style={{ width: 1, height: 22, background: tint("#ffffff", 0.08) }} />

      {/* AI status → opens settings */}
      <button
        onClick={onAiClick}
        title="Manage AI providers"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 7,
          padding: "6px 11px",
          border: `1px solid ${tint("#ffffff", 0.09)}`,
          borderRadius: 9,
          background: C.fill,
          fontSize: 12,
          color: C.text2,
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: aiLive ? C.up : C.warn }} />
        {AI_LABEL[aiActive] ?? aiActive} <span style={{ color: C.muted2 }}>·</span> {aiLive ? "live" : "mock"}
        <ChevronDown size={13} style={{ color: C.muted, marginLeft: 2 }} />
      </button>

      {/* account */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 9,
          padding: "5px 7px 5px 12px",
          border: `1px solid ${tint("#ffffff", 0.09)}`,
          borderRadius: 10,
          background: C.fill,
        }}
      >
        <div style={{ textAlign: "right", lineHeight: 1.15 }}>
          <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: ".08em", color: C.muted }}>Paper</div>
          <div style={{ fontFamily: mono, fontSize: 13, fontWeight: 600 }}>
            {equity != null ? `$${equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
          </div>
        </div>
        <div
          style={{
            width: 30,
            height: 30,
            borderRadius: 8,
            background: "linear-gradient(135deg,#3a3f4a,#23262d)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 12,
            fontWeight: 700,
            color: C.text2,
          }}
        >
          JD
        </div>
      </div>
    </header>
  );
}
