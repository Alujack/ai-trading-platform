"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { API_BASE, fetcher } from "@/lib/api";
import { pipsBetween, riskReward } from "@/lib/signals";
import { C, mono, tint } from "@/lib/theme";
import type { FeatureFlagState, RawSignal, RawSignalsResponse } from "@/lib/types";
import { Panel, Toggle } from "./ui";

/**
 * The raw ("layers off") strategy feed.
 *
 * One toggle turns it on. While it is on, every candidate a strategy emits is
 * shown exactly as the strategy produced it, with a badge naming the layer that
 * stopped it rather than the layer silently swallowing it. That is the view for
 * trading by hand while automation keeps running the full protected stack.
 *
 * Nothing on this panel can place an order: the rows come from a table with no
 * path into execution, and the toggle only controls visibility.
 */

const COLS = "1.25fr 1fr 0.6fr 1.05fr 0.6fr 0.6fr 24px";

/** blockedBy tag → short label + tone. */
const LAYER: Record<string, { label: string; tone: string }> = {
  ai_score: { label: "AI SCORE", tone: C.blue },
  ai_judgment: { label: "AI VETO", tone: C.blue },
  ai_unreachable: { label: "AI DOWN", tone: C.muted },
  regime: { label: "REGIME", tone: "#a98bd8" },
  stale_data: { label: "STALE DATA", tone: C.down },
  risk_news: { label: "NEWS", tone: C.warn },
  risk_rr: { label: "RR", tone: C.warn },
  risk_daily_loss: { label: "DAILY LOSS", tone: C.down },
  risk_drawdown: { label: "DRAWDOWN", tone: C.down },
  risk_gold: { label: "GOLD CAP", tone: C.gold },
  risk_inputs: { label: "BAD LEVELS", tone: C.down },
  risk: { label: "RISK", tone: C.down },
  cooldown: { label: "COOLDOWN", tone: C.muted },
  duplicate: { label: "DUPLICATE", tone: C.muted2 },
  insufficient_candles: { label: "THIN DATA", tone: C.muted },
  unknown: { label: "OTHER", tone: C.muted },
};

function layerBadge(r: RawSignal): { label: string; tone: string } {
  if (r.verdict === "GENERATED") return { label: "PASSED ALL", tone: C.up };
  if (r.verdict === "PENDING") return { label: "GATING…", tone: C.muted2 };
  return LAYER[r.blockedBy ?? "unknown"] ?? LAYER.unknown;
}

function ago(iso: string): string {
  const min = Math.floor((Date.now() - Date.parse(iso)) / 60_000);
  if (min < 1) return "now";
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function fmtPrice(symbol: string, v: string): string {
  const dp = symbol === "EURUSD" ? 4 : symbol === "BTCUSD" ? 0 : 2;
  const n = Number(v);
  return Number.isFinite(n)
    ? n.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp })
    : v;
}

async function setFeed(enabled: boolean): Promise<void> {
  const res = await fetch(`${API_BASE}/api/config/raw-feed`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = ((await res.json()) as { error?: string }).error ?? "";
    } catch {
      /* ignore non-JSON body */
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
}

export function RawFeedPanel({ limit = 50 }: { limit?: number }) {
  const [blockedOnly, setBlockedOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const flagKey = "/api/config/raw-feed";
  const listKey = `/api/signals/raw?limit=${limit}${blockedOnly ? "&blockedOnly=1" : ""}`;
  const { data: flag } = useSWR<FeatureFlagState>(flagKey, fetcher, { refreshInterval: 30_000 });
  const on = flag?.enabled ?? false;
  const { data, isLoading } = useSWR<RawSignalsResponse>(listKey, fetcher, {
    refreshInterval: on ? 15_000 : 0,
  });

  const rows = data?.data ?? [];
  const passed = rows.filter((r) => r.verdict === "GENERATED").length;
  const blocked = rows.filter((r) => r.verdict === "REJECTED" || r.verdict === "SKIPPED").length;

  async function toggle() {
    setBusy(true);
    setErr(null);
    try {
      await setFeed(!on);
      await Promise.all([mutate(flagKey), mutate(listKey)]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Raw Strategy Feed — layers off"
      right={
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {on && (
            <button
              onClick={() => setBlockedOnly(!blockedOnly)}
              style={{
                padding: "3px 9px",
                borderRadius: 20,
                fontSize: 9.5,
                fontWeight: 700,
                letterSpacing: ".04em",
                cursor: "pointer",
                fontFamily: "inherit",
                background: blockedOnly ? tint(C.down, 0.16) : "transparent",
                color: blockedOnly ? C.down : C.muted,
                border: `1px solid ${blockedOnly ? tint(C.down, 0.4) : C.line2}`,
              }}
            >
              BLOCKED ONLY
            </button>
          )}
          <Toggle
            on={on}
            busy={busy}
            tone={C.warn}
            title={on ? "Hide the raw strategy feed" : "Show the pure strategy signals (observe only)"}
            onClick={toggle}
          />
        </div>
      }
      noBody
    >
      <div
        style={{
          padding: "10px 16px",
          borderBottom: `1px solid ${C.hair}`,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <p style={{ margin: 0, fontSize: 11.5, color: C.muted, lineHeight: 1.5 }}>
          {on ? (
            <>
              Every candidate the strategies emit, untouched — with the layer that stopped it named
              instead of applied. <strong style={{ color: C.text3 }}>Observe only:</strong> nothing here
              can open a trade, and automation still runs the full protected stack.
            </>
          ) : (
            <>
              Off — you only see signals that cleared AI, risk, news and the portfolio caps. Turn this on
              to also see the pure strategy output for manual trading.
            </>
          )}
        </p>
        {on && (
          <span style={{ fontFamily: mono, fontSize: 10.5, color: C.muted2, whiteSpace: "nowrap" }}>
            {data?.pagination.total ?? 0} raw · <span style={{ color: C.up }}>{passed} passed</span> ·{" "}
            <span style={{ color: C.down }}>{blocked} blocked</span>
          </span>
        )}
      </div>

      {err && (
        <p style={{ margin: 0, padding: "10px 16px", fontSize: 11.5, color: C.down }}>{err}</p>
      )}

      {!on && (
        <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>
          Turning this on drops every layer, data freshness included. Rows tagged{" "}
          <strong style={{ color: C.down }}>STALE DATA</strong> are priced off frozen candles — check
          the bar age on the row before you act on one.
        </p>
      )}

      {on && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: COLS,
              padding: "8px 16px",
              fontSize: 9.5,
              textTransform: "uppercase",
              letterSpacing: ".06em",
              color: C.muted2,
              borderBottom: `1px solid ${C.hair}`,
            }}
          >
            <span>Symbol</span>
            <span>Strategy</span>
            <span style={{ textAlign: "right" }}>Conf</span>
            <span style={{ textAlign: "center" }}>Stopped by</span>
            <span style={{ textAlign: "right" }}>RR</span>
            <span style={{ textAlign: "right" }}>Time</span>
            <span />
          </div>

          {isLoading && (
            <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>Loading…</p>
          )}
          {!isLoading && rows.length === 0 && (
            <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>
              No raw candidates recorded yet — the feed fills on the next strategy scan.
            </p>
          )}
          {rows.map((r) => (
            <Row
              key={r.id}
              r={r}
              open={openId === r.id}
              onToggle={() => setOpenId(openId === r.id ? null : r.id)}
            />
          ))}
        </>
      )}
    </Panel>
  );
}

function Row({ r, open, onToggle }: { r: RawSignal; open: boolean; onToggle: () => void }) {
  const long = r.direction === "LONG";
  const dirColor = long ? C.up : C.down;
  const badge = layerBadge(r);
  const entry = Number(r.entryPrice);
  const stop = Number(r.stopLoss);
  const target = Number(r.takeProfit);
  const rr = riskReward(entry, stop, target);

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
          <span
            style={{
              padding: "2px 6px",
              borderRadius: 5,
              fontSize: 9,
              fontWeight: 700,
              background: tint(dirColor, 0.14),
              color: dirColor,
            }}
          >
            {r.direction}
          </span>
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>{r.symbol}</span>
          <span style={{ fontFamily: mono, fontSize: 9.5, color: C.muted2 }}>{r.timeframe}</span>
        </div>
        <span
          style={{
            fontSize: 11,
            color: C.muted,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {r.strategyName}
        </span>
        <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, color: C.text3 }}>
          {r.confidence}
        </span>
        <span style={{ textAlign: "center" }}>
          <span
            style={{
              padding: "2px 8px",
              borderRadius: 20,
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: ".04em",
              background: tint(badge.tone, 0.14),
              color: badge.tone,
            }}
          >
            {badge.label}
          </span>
        </span>
        <span style={{ textAlign: "right", fontFamily: mono, fontSize: 11.5, color: rr >= 2 ? C.up : C.warn }}>
          {rr.toFixed(1)}
        </span>
        <span style={{ textAlign: "right", fontFamily: mono, fontSize: 10.5, color: C.muted2 }}>
          {ago(r.lastSeenAt)}
          {r.seenCount > 1 && <span style={{ color: C.muted2 }}> ×{r.seenCount}</span>}
        </span>
        <span
          style={{
            textAlign: "center",
            color: C.muted2,
            fontSize: 13,
            transform: open ? "rotate(90deg)" : "none",
            transition: "transform .15s",
          }}
        >
          ›
        </span>
      </button>

      {open && (
        <div style={{ padding: "0 16px 14px 16px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
            <Detail label="Entry" value={fmtPrice(r.symbol, r.entryPrice)} tone={C.gold} />
            <Detail
              label="Stop"
              value={fmtPrice(r.symbol, r.stopLoss)}
              tone={C.down}
              sub={`${Math.round(pipsBetween(r.symbol, entry, stop))} pips`}
            />
            <Detail
              label="Target"
              value={fmtPrice(r.symbol, r.takeProfit)}
              tone={C.up}
              sub={`${Math.round(pipsBetween(r.symbol, entry, target))} pips`}
            />
            <Detail label="Risk : Reward" value={`1 : ${rr.toFixed(1)}`} tone={C.text} />
          </div>

          <Block label="Strategy reasoning (raw, no AI)">{r.reasoning || "—"}</Block>

          {r.verdict === "GENERATED" ? (
            <Block label="Layers">
              Cleared every layer — this one became a real signal
              {r.signalId ? ` (${r.signalId})` : ""} and went to the execution decider.
            </Block>
          ) : r.verdict === "PENDING" ? (
            <Block label="Layers">Gate still running for this candidate.</Block>
          ) : (
            <Block label={`Stopped by ${badge.label.toLowerCase()}`}>
              {r.blockedReason || "—"}
              {r.blockedBy === "stale_data" &&
                "\n\nThese prices came off a frozen series — the levels above may be nowhere near the live market. Re-check the current price before acting."}
              {"\n\n"}Automation did NOT take this trade. Trading it by hand is your call — position
              size it yourself; the risk engine never sized this one.
            </Block>
          )}
        </div>
      )}
    </div>
  );
}

function Detail({
  label,
  value,
  tone,
  sub,
}: {
  label: string;
  value: string;
  tone: string;
  sub?: string;
}) {
  return (
    <div style={{ background: C.fill, border: `1px solid ${C.line}`, borderRadius: 9, padding: "8px 11px" }}>
      <div style={{ fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted }}>
        {label}
      </div>
      <div style={{ fontFamily: mono, fontSize: 13.5, fontWeight: 600, color: tone, marginTop: 2 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 10, color: C.muted2, marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        marginTop: 10,
        background: C.fill,
        border: `1px solid ${C.line}`,
        borderRadius: 9,
        padding: "10px 12px",
      }}
    >
      <div
        style={{
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: ".06em",
          color: C.muted,
          marginBottom: 5,
        }}
      >
        {label}
      </div>
      <p
        style={{
          margin: 0,
          fontFamily: mono,
          fontSize: 11.5,
          lineHeight: 1.6,
          color: C.text3,
          whiteSpace: "pre-wrap",
        }}
      >
        {children}
      </p>
    </div>
  );
}
