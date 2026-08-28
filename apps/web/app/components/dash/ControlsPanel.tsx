"use client";

import { useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { API_BASE, fetcher } from "@/lib/api";
import { C, mono, tint } from "@/lib/theme";
import type {
  EffectiveRiskConfig,
  ExecutionMapResponse,
  ExecutionMode,
  LayerMode,
  LayersResponse,
  RiskConfigResponse,
} from "@/lib/types";
import { Panel, StatusDot, Toggle } from "./ui";

const LAYERS_KEY = "/api/config/layers";

const MODES: ExecutionMode[] = ["OFF", "AUTO", "CONFIRM"];
const LAYER_MODE_COLOR: Record<LayerMode, string> = {
  FULL: C.up,
  STRATEGY_ONLY: C.warn,
  MIXED: C.gold,
};
const LAYER_MODE_LABEL: Record<LayerMode, string> = {
  FULL: "FULL STACK",
  STRATEGY_ONLY: "STRATEGY ONLY",
  MIXED: "PARTIAL",
};
const MODE_COLOR: Record<ExecutionMode, string> = { OFF: C.down, AUTO: C.up, CONFIRM: C.warn };
const MODE_HELP: Record<ExecutionMode, string> = {
  OFF: "Signals still generate + log. Nothing opens. (Kill-switch)",
  AUTO: "Open immediately once the gate approves (AI if enabled; risk always).",
  CONFIRM: "Send a Telegram alert and wait for your Approve.",
};

async function api(path: string, method: string, body?: unknown): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = ((await res.json()) as { error?: string }).error ?? "";
    } catch {
      /* ignore */
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
}

// --------------------------------------------------------------------------
// Execution mode + kill switch
// --------------------------------------------------------------------------

export function ExecutionControlPanel() {
  const { data } = useSWR<ExecutionMapResponse>("/api/config/execution", fetcher, {
    refreshInterval: 15_000,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const global = data?.global ?? "AUTO";
  const overrides = (data?.rows ?? []).filter((r) => r.scope !== "GLOBAL");

  // The discretionary layers are switchable; the risk engine and breakers are
  // not, and are deliberately not offered here — they are listed as fixed.
  const { data: layers } = useSWR<LayersResponse>(LAYERS_KEY, fetcher, {
    refreshInterval: 30_000,
  });

  async function run(fn: () => Promise<void>, key = "/api/config/execution") {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await mutate(key);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  const setGlobal = (mode: ExecutionMode) =>
    run(() => api("/api/config/execution", "PUT", { scope: "GLOBAL", scopeKey: "", mode }));

  const setLayers = (body: unknown) => run(() => api(LAYERS_KEY, "PUT", body), LAYERS_KEY);

  return (
    <Panel
      title="Execution Control"
      right={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: MODE_COLOR[global] }}>
          <StatusDot color={MODE_COLOR[global]} pulse={global !== "OFF"} /> {global}
        </span>
      }
      bodyStyle={{ display: "flex", flexDirection: "column", gap: 14 }}
    >
      <div>
        <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted, marginBottom: 8 }}>
          Global mode
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
          {MODES.map((m) => {
            const active = global === m;
            return (
              <button
                key={m}
                disabled={busy}
                onClick={() => setGlobal(m)}
                style={{
                  padding: "10px 8px",
                  borderRadius: 10,
                  border: `1px solid ${active ? MODE_COLOR[m] : C.line2}`,
                  background: active ? tint(MODE_COLOR[m], 0.16) : "transparent",
                  color: active ? MODE_COLOR[m] : C.text3,
                  fontWeight: 700,
                  fontSize: 12.5,
                  cursor: busy ? "default" : "pointer",
                  fontFamily: "inherit",
                }}
              >
                {m}
              </button>
            );
          })}
        </div>
        <p style={{ margin: "8px 0 0", fontSize: 11, color: C.muted, lineHeight: 1.5 }}>{MODE_HELP[global]}</p>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button
          disabled={busy}
          onClick={() => run(() => api("/api/config/kill", "POST", { reason: "dashboard" }))}
          style={{
            flex: 1,
            padding: "11px 8px",
            borderRadius: 10,
            border: `1px solid ${tint(C.down, 0.5)}`,
            background: tint(C.down, 0.14),
            color: C.down,
            fontWeight: 800,
            letterSpacing: ".04em",
            fontSize: 12.5,
            cursor: busy ? "default" : "pointer",
            fontFamily: "inherit",
          }}
        >
          🛑 KILL
        </button>
        <button
          disabled={busy}
          onClick={() => run(() => api("/api/config/arm", "POST", { reason: "dashboard" }))}
          style={{
            flex: 1,
            padding: "11px 8px",
            borderRadius: 10,
            border: `1px solid ${C.line2}`,
            background: "transparent",
            color: C.text3,
            fontWeight: 700,
            fontSize: 12.5,
            cursor: busy ? "default" : "pointer",
            fontFamily: "inherit",
          }}
        >
          Arm (CONFIRM)
        </button>
      </div>

      <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted }}>
            Gate layers
          </span>
          {layers && (
            <span style={{ fontFamily: mono, fontSize: 9.5, fontWeight: 700, letterSpacing: ".05em", color: LAYER_MODE_COLOR[layers.mode] }}>
              {LAYER_MODE_LABEL[layers.mode]}
            </span>
          )}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          {(["FULL", "STRATEGY_ONLY"] as const).map((m) => {
            const active = layers?.mode === m;
            const tone = LAYER_MODE_COLOR[m];
            return (
              <button
                key={m}
                disabled={busy || !layers}
                onClick={() => setLayers({ mode: m })}
                style={{
                  padding: "9px 8px",
                  borderRadius: 10,
                  border: `1px solid ${active ? tone : C.line2}`,
                  background: active ? tint(tone, 0.16) : "transparent",
                  color: active ? tone : C.text3,
                  fontWeight: 700,
                  fontSize: 11.5,
                  cursor: busy || !layers ? "default" : "pointer",
                  fontFamily: "inherit",
                }}
              >
                {m === "FULL" ? "ALL LAYERS ON" : "STRATEGY ONLY"}
              </button>
            );
          })}
        </div>

        <p style={{ margin: "8px 0 0", fontSize: 11, color: layers?.mode === "FULL" ? C.muted : C.warn, lineHeight: 1.5 }}>
          {layers?.mode === "FULL"
            ? "Every filter applies: the model scores and can veto, and regime, hours, trend bias and range position all have to agree."
            : "Filters are off — what reaches risk is the strategy's raw opinion. Sizing, stops, RR, the news blackout, the breakers and the caps still apply to every signal."}
        </p>

        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 7 }}>
          {(layers?.layers ?? []).map((l) => (
            <div key={l.key} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
              <span style={{ fontSize: 11.5, color: l.enabled ? C.text3 : C.muted }} title={l.offMeans}>
                {l.label}
              </span>
              <Toggle
                on={l.enabled}
                busy={busy}
                tone={l.enabled ? C.up : C.warn}
                title={l.enabled ? `Turn off — ${l.offMeans}` : `Turn ${l.label} back on`}
                onClick={() => setLayers({ layer: l.key, enabled: !l.enabled })}
              />
            </div>
          ))}
        </div>
      </div>

      <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12 }}>
        <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted, marginBottom: 8 }}>
          Always on — not switchable
        </div>
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 5 }}>
          {(layers?.mandatory ?? []).map((m) => (
            <li key={m} style={{ fontSize: 11, color: C.muted2, display: "flex", gap: 7 }}>
              <span style={{ color: C.up }}>✓</span>
              <span>{m}</span>
            </li>
          ))}
        </ul>
      </div>

      {overrides.length > 0 && (
        <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12 }}>
          <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted, marginBottom: 8 }}>
            Overrides
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {overrides.map((r) => (
              <div key={`${r.scope}:${r.scopeKey}`} style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                <span style={{ color: C.text3 }}>
                  {r.scope === "SYMBOL" ? "" : "strategy "}
                  {r.scopeKey}
                </span>
                <span style={{ fontFamily: mono, fontWeight: 700, color: MODE_COLOR[r.mode] }}>{r.mode}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {err && <p style={{ margin: 0, fontSize: 11.5, color: C.down }}>{err}</p>}
    </Panel>
  );
}

// --------------------------------------------------------------------------
// Risk parameters (editable, per scope)
// --------------------------------------------------------------------------

const FIELDS: { key: keyof EffectiveRiskConfig; label: string; suffix?: string }[] = [
  { key: "riskPerTradePct", label: "Risk / trade", suffix: "%" },
  { key: "minRR", label: "Min R:R" },
  { key: "dailyLossLimitPct", label: "Daily loss limit", suffix: "%" },
  { key: "maxDrawdownPct", label: "Max drawdown", suffix: "%" },
  { key: "maxOpenTrades", label: "Max open trades" },
  { key: "maxOpenRiskPct", label: "Max open risk", suffix: "%" },
  { key: "maxRiskPerCurrencyPct", label: "Max risk / currency", suffix: "%" },
  { key: "aiMinScore", label: "AI score floor" },
  { key: "newsBeforeMin", label: "News blackout before", suffix: "m" },
  { key: "newsAfterMin", label: "News blackout after", suffix: "m" },
  { key: "approvalTtlMin", label: "Approval TTL", suffix: "m" },
];

const SCOPES = [
  { label: "Global", scope: "GLOBAL" as const, key: "", query: "" },
  { label: "XAUUSD", scope: "SYMBOL" as const, key: "XAUUSD", query: "symbol=XAUUSD" },
  { label: "EURUSD", scope: "SYMBOL" as const, key: "EURUSD", query: "symbol=EURUSD" },
  { label: "BTCUSD", scope: "SYMBOL" as const, key: "BTCUSD", query: "symbol=BTCUSD" },
];

export function RiskControlPanel() {
  const [scopeIdx, setScopeIdx] = useState(0);
  const scope = SCOPES[scopeIdx];
  const path = `/api/config/risk${scope.query ? `?${scope.query}` : ""}`;
  const { data } = useSWR<RiskConfigResponse>(path, fetcher, { refreshInterval: 30_000 });

  const [draft, setDraft] = useState<Partial<Record<keyof EffectiveRiskConfig, string>>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Reset the draft whenever the resolved config or scope changes.
  useEffect(() => {
    if (data?.effective) {
      const next: Partial<Record<keyof EffectiveRiskConfig, string>> = {};
      for (const f of FIELDS) next[f.key] = String(data.effective[f.key]);
      setDraft(next);
      setMsg(null);
    }
  }, [data?.effective, scopeIdx]);

  async function save() {
    if (!data) return;
    setBusy(true);
    setMsg(null);
    const body: Record<string, number | string> = { scope: scope.scope, scopeKey: scope.key };
    for (const f of FIELDS) {
      const raw = draft[f.key];
      if (raw == null || raw === "") continue;
      const n = Number(raw);
      if (Number.isFinite(n)) body[f.key] = n;
    }
    try {
      await api("/api/config/risk", "PUT", body);
      await mutate(path);
      setMsg("Saved — effective immediately, no redeploy.");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Risk Parameters"
      right={
        <div style={{ display: "flex", gap: 4 }}>
          {SCOPES.map((s, i) => (
            <button
              key={s.label}
              onClick={() => setScopeIdx(i)}
              style={{
                padding: "3px 8px",
                borderRadius: 7,
                border: `1px solid ${i === scopeIdx ? "var(--accent, #f0b429)" : C.line2}`,
                background: i === scopeIdx ? tint("#f0b429", 0.12) : "transparent",
                color: i === scopeIdx ? "var(--accent, #f0b429)" : C.muted,
                fontSize: 10,
                fontWeight: 700,
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              {s.label}
            </button>
          ))}
        </div>
      }
      bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }}
    >
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        {FIELDS.map((f) => {
          const b = data?.bounds?.[f.key];
          return (
            <label key={f.key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".05em", color: C.muted }}>
                {f.label}
                {f.suffix ? ` (${f.suffix})` : ""}
              </span>
              <input
                type="number"
                value={draft[f.key] ?? ""}
                min={b?.min}
                max={b?.max}
                step={b?.int ? 1 : "any"}
                onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                style={{
                  background: C.fill,
                  border: `1px solid ${C.line2}`,
                  borderRadius: 8,
                  padding: "7px 9px",
                  color: C.text,
                  fontFamily: mono,
                  fontSize: 13,
                  outline: "none",
                }}
              />
            </label>
          );
        })}
      </div>

      {scope.scope !== "GLOBAL" && (
        <p style={{ margin: 0, fontSize: 11, color: C.muted2, lineHeight: 1.5 }}>
          Editing <b style={{ color: C.text3 }}>{scope.label}</b>. Values shown are the resolved effective config
          (SYMBOL ► GLOBAL ► defaults); saving sets them at the {scope.label} level.
        </p>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button
          disabled={busy}
          onClick={save}
          style={{
            padding: "9px 18px",
            borderRadius: 9,
            border: "none",
            background: "var(--accent, #f0b429)",
            color: "#1a1306",
            fontWeight: 800,
            fontSize: 12.5,
            cursor: busy ? "default" : "pointer",
            fontFamily: "inherit",
          }}
        >
          {busy ? "Saving…" : "Save"}
        </button>
        {msg && (
          <span style={{ fontSize: 11.5, color: msg.startsWith("Saved") ? C.up : C.down }}>{msg}</span>
        )}
      </div>
    </Panel>
  );
}
