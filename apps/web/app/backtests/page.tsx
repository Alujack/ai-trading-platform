"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { ApiError, fetcher, postJson } from "@/lib/api";
import { C, mono, tint } from "@/lib/theme";
import type { BacktestMetric, BacktestRunDetail, BacktestRunsResponse } from "@/lib/types";
import { EquityCurve } from "../components/dash/EquityCurve";
import { Panel, Pill, StatusDot } from "../components/dash/ui";

interface JobStatus {
  running: boolean;
  ok: boolean | null;
  runId: string | null;
  error: string | null;
  startedAt: string | null;
  finishedAt: string | null;
}

function verdictTone(verdict: string): { color: string; label: string } {
  const v = verdict.toUpperCase();
  if (v.startsWith("POSITIVE")) return { color: C.up, label: "POSITIVE" };
  if (v.startsWith("MARGINAL")) return { color: C.warn, label: "MARGINAL" };
  if (v.startsWith("NEGATIVE")) return { color: C.down, label: "NEGATIVE" };
  if (v.startsWith("NOT SIGNIFICANT")) return { color: C.blue, label: "LOW SAMPLE" };
  return { color: C.muted, label: "NO TRADES" };
}

function pf(x: number): string {
  return x === Infinity || x > 1e6 ? "∞" : x.toFixed(2);
}
function pnl(n: number): string {
  return `${n < 0 ? "−" : "+"}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
function rkey(m: { strategy: string; symbol: string; timeframe: string }): string {
  return `${m.strategy}|${m.symbol}|${m.timeframe}`;
}

const COLS = "1.3fr 0.9fr 0.7fr 0.6fr 0.6fr 0.7fr 0.6fr 0.8fr 0.9fr 1fr";

export default function BacktestsPage() {
  const { data, isLoading, mutate } = useSWR<BacktestRunsResponse>("/api/backtests", fetcher, {
    refreshInterval: 15_000,
  });
  const runs = data?.runs ?? [];
  const [runId, setRunId] = useState<string | null>(null);
  const activeRun = runs.find((r) => r.id === runId) ?? runs[0];

  const { data: detail } = useSWR<BacktestRunDetail>(
    activeRun ? `/api/backtests/${activeRun.id}` : null,
    fetcher,
  );

  // ── Run controls + live job polling ──────────────────────────────────
  const [label, setLabel] = useState("");
  const [costs, setCosts] = useState(true);
  const [runErr, setRunErr] = useState<string | null>(null);
  const { data: job, mutate: mutateJob } = useSWR<JobStatus>("/api/backtests/run/status", fetcher, {
    refreshInterval: 2000,
  });
  const busy = !!job?.running;
  const wasRunning = useRef(false);

  useEffect(() => {
    const now = !!job?.running;
    if (wasRunning.current && !now) {
      // A run just finished — refresh the list and jump to the new run.
      void mutate();
      if (job?.runId) setRunId(job.runId);
      if (job && job.ok === false) setRunErr(job.error || "Backtest failed");
    }
    wasRunning.current = now;
  }, [job, mutate]);

  async function runBacktest() {
    setRunErr(null);
    try {
      await postJson("/api/backtests/run", { label: label || undefined, noCosts: !costs });
      void mutateJob(); // flip to running immediately
    } catch (e) {
      setRunErr(e instanceof ApiError ? e.message : "Failed to start backtest");
    }
  }

  // ── Result sorting + selection for the equity curve ──────────────────
  const results = useMemo(() => {
    const rs = activeRun?.results ?? [];
    return [...rs].sort((a, b) => {
      const at = a.trades > 0 ? 1 : 0;
      const bt = b.trades > 0 ? 1 : 0;
      if (at !== bt) return bt - at;
      return b.expectancy_r - a.expectancy_r;
    });
  }, [activeRun]);

  const [selKey, setSelKey] = useState<string | null>(null);
  const selected = useMemo(() => {
    if (!results.length) return null;
    return results.find((r) => rkey(r) === selKey) ?? results.find((r) => r.trades > 0) ?? results[0];
  }, [results, selKey]);

  const curve = selected && detail?.equityCurves ? detail.equityCurves[rkey(selected)] ?? [] : [];

  return (
    <>
      <Panel
        title="Run a Backtest"
        right={
          busy ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 11.5, color: C.warn, fontFamily: mono }}>
              <StatusDot color={C.warn} pulse /> Running…
            </span>
          ) : job?.finishedAt ? (
            <span style={{ fontSize: 11, color: job.ok ? C.up : C.down, fontFamily: mono }}>
              last run {job.ok ? "ok" : "failed"}
            </span>
          ) : null
        }
      >
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 14 }}>
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Label (optional)"
            disabled={busy}
            style={{
              background: C.panelDeep, color: C.text2, border: `1px solid ${C.line2}`,
              borderRadius: 8, padding: "7px 10px", fontSize: 12.5, minWidth: 220, fontFamily: "inherit",
            }}
          />
          <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12.5, color: C.text3, cursor: "pointer" }}>
            <input type="checkbox" checked={costs} onChange={(e) => setCosts(e.target.checked)} disabled={busy} />
            Apply costs (realistic)
          </label>
          <button
            onClick={runBacktest}
            disabled={busy}
            style={{
              background: busy ? tint("#ffffff", 0.06) : "var(--accent, #f0b429)",
              color: busy ? C.muted : "#1a1306",
              border: "none", borderRadius: 8, padding: "8px 16px", fontSize: 12.5, fontWeight: 700,
              cursor: busy ? "default" : "pointer", fontFamily: "inherit",
            }}
          >
            {busy ? "Running…" : "Run backtest"}
          </button>
          <span style={{ fontSize: 11, color: C.muted2 }}>
            all strategies · 15min/60min/1min · all symbols
          </span>
        </div>
        {runErr && <p style={{ margin: "10px 0 0", fontSize: 12, color: C.down }}>{runErr}</p>}
        {busy && (
          <p style={{ margin: "10px 0 0", fontSize: 11.5, color: C.muted }}>
            Replaying stored candles through every strategy — this usually takes a few seconds.
          </p>
        )}
      </Panel>

      {isLoading && !activeRun ? (
        <Panel title="Backtest Runs"><p style={{ margin: 0, color: C.muted, fontSize: 12.5 }}>Loading…</p></Panel>
      ) : !activeRun ? (
        <Panel title="Backtest Runs">
          <p style={{ margin: 0, fontSize: 13, color: C.text3 }}>
            No runs yet — hit <strong style={{ color: C.text }}>Run backtest</strong> above to create your first one.
          </p>
        </Panel>
      ) : (
        <>
          <Panel
            title="Backtest Runs"
            right={
              runs.length > 0 && (
                <select
                  value={activeRun.id}
                  onChange={(e) => setRunId(e.target.value)}
                  style={{ background: C.panelDeep, color: C.text2, border: `1px solid ${C.line2}`, borderRadius: 8, padding: "5px 8px", fontSize: 11.5, fontFamily: mono }}
                >
                  {runs.map((r) => (
                    <option key={r.id} value={r.id}>
                      {(r.label ? `${r.label} · ` : "") + new Date(r.createdAt).toLocaleString()}
                    </option>
                  ))}
                </select>
              )
            }
          >
            <div style={{ display: "flex", flexWrap: "wrap", gap: 18, fontSize: 12 }}>
              <Meta label="Date" value={new Date(activeRun.createdAt).toLocaleString()} />
              <Meta label="Balance" value={`$${activeRun.startingBalance.toLocaleString()}`} />
              <Meta label="Risk/trade" value={`${activeRun.riskPct}%`} />
              <Meta label="Costs" value={activeRun.costsApplied ? "applied" : "DISABLED (optimistic)"} valueColor={activeRun.costsApplied ? C.text2 : C.warn} />
              <Meta label="Strategies" value={activeRun.config?.strategies?.join(", ") ?? "—"} />
              <Meta label="Timeframes" value={activeRun.config?.timeframes?.join(", ") ?? "—"} />
            </div>
          </Panel>

          <Panel title="Results — per strategy × symbol × timeframe" noBody>
            <div style={{ display: "grid", gridTemplateColumns: COLS, padding: "8px 16px", fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".05em", color: C.muted2, borderBottom: `1px solid ${C.hair}` }}>
              <span>Strategy</span><span>Symbol</span><span>TF</span>
              <span style={{ textAlign: "right" }}>Trades</span>
              <span style={{ textAlign: "right" }}>Win%</span>
              <span style={{ textAlign: "right" }}>Exp R</span>
              <span style={{ textAlign: "right" }}>PF</span>
              <span style={{ textAlign: "right" }}>Max DD</span>
              <span style={{ textAlign: "right" }}>Net P&L</span>
              <span style={{ textAlign: "right" }}>Verdict</span>
            </div>
            {results.map((m) => (
              <ResultRow key={rkey(m)} m={m} active={selected ? rkey(selected) === rkey(m) : false} onClick={() => setSelKey(rkey(m))} />
            ))}
          </Panel>

          {selected && (
            <Panel
              title={`Equity Curve — ${selected.strategy} · ${selected.symbol} · ${selected.timeframe}`}
              right={<span style={{ fontFamily: mono, fontSize: 11, color: selected.net_pnl < 0 ? C.down : C.up }}>{pnl(selected.net_pnl)} · {selected.trades} trades</span>}
            >
              <EquityCurve points={curve} starting={activeRun.startingBalance} />
            </Panel>
          )}
        </>
      )}
    </>
  );
}

function Meta({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div>
      <div style={{ fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted2, marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 12.5, color: valueColor ?? C.text2, fontFamily: mono }}>{value}</div>
    </div>
  );
}

function ResultRow({ m, active, onClick }: { m: BacktestMetric; active: boolean; onClick: () => void }) {
  const tone = verdictTone(m.verdict);
  const dead = m.trades === 0;
  return (
    <div
      onClick={onClick}
      title={m.verdict}
      style={{
        display: "grid", gridTemplateColumns: COLS, alignItems: "center", padding: "10px 16px",
        borderBottom: `1px solid ${tint("#ffffff", 0.04)}`, cursor: "pointer",
        background: active ? "rgba(255,255,255,0.04)" : "transparent",
        opacity: dead ? 0.5 : 1,
      }}
    >
      <span style={{ fontSize: 12, fontWeight: 600, color: C.text2 }}>{m.strategy}</span>
      <span style={{ fontSize: 12, color: C.text3 }}>{m.symbol}</span>
      <span style={{ fontSize: 11.5, color: C.muted, fontFamily: mono }}>{m.timeframe}</span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, color: C.text3 }}>{m.trades}</span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, color: C.text3 }}>{(m.win_rate * 100).toFixed(0)}%</span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, color: m.expectancy_r < 0 ? C.down : m.expectancy_r > 0 ? C.up : C.muted }}>{m.expectancy_r >= 0 ? "+" : ""}{m.expectancy_r.toFixed(2)}</span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, color: C.text3 }}>{pf(m.profit_factor)}</span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, color: C.text3 }}>{(m.max_drawdown_pct * 100).toFixed(1)}%</span>
      <span style={{ textAlign: "right", fontFamily: mono, fontSize: 12, fontWeight: 600, color: m.net_pnl < 0 ? C.down : m.net_pnl > 0 ? C.up : C.muted }}>{pnl(m.net_pnl)}</span>
      <span style={{ textAlign: "right" }}>
        <Pill color={tone.color} bg={tint(tone.color, 0.13)} border={tint(tone.color, 0.3)}>{tone.label}</Pill>
      </span>
    </div>
  );
}
