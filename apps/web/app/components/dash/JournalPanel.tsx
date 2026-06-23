"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { C, mono, tint } from "@/lib/theme";
import type { JournalEntry, JournalResponse } from "@/lib/types";
import { Panel } from "./ui";

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}

function pnlStr(n: number | null): string {
  if (n == null) return "—";
  return `${n < 0 ? "−" : "+"}$${Math.abs(Math.round(n)).toLocaleString()}`;
}

export function JournalPanel({ limit = 30 }: { limit?: number }) {
  const { data, isLoading } = useSWR<JournalResponse>(`/api/journal?limit=${limit}`, fetcher, {
    refreshInterval: 60_000,
  });
  const rows = data?.data ?? [];

  return (
    <Panel title="Trade Journal" right={<span style={{ fontSize: 11, color: C.muted2 }}>{rows.length} entries</span>} noBody>
      {isLoading && <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted }}>Loading…</p>}
      {!isLoading && rows.length === 0 && (
        <p style={{ padding: 16, margin: 0, fontSize: 12.5, color: C.muted, lineHeight: 1.6 }}>
          No journal entries yet. Each closed trade is journaled with its reasoning and an AI review —
          they&apos;ll appear here once trades close.
        </p>
      )}
      {rows.map((e) => (
        <JournalRow key={e.id} entry={e} />
      ))}
    </Panel>
  );
}

function JournalRow({ entry }: { entry: JournalEntry }) {
  const [open, setOpen] = useState(false);
  const long = entry.direction === "LONG";
  const dirColor = long ? C.up : C.down;
  const pnlColor = entry.profitLoss == null ? C.muted2 : entry.profitLoss < 0 ? C.down : C.up;

  return (
    <div style={{ borderBottom: `1px solid ${tint("#ffffff", 0.04)}` }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 11,
          padding: "12px 16px",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          fontFamily: "inherit",
          textAlign: "left",
        }}
      >
        <span style={{ padding: "2px 6px", borderRadius: 5, fontSize: 9, fontWeight: 700, background: tint(dirColor, 0.14), color: dirColor }}>
          {entry.direction}
        </span>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: C.text }}>{entry.symbol}</span>
        {entry.strategyName && <span style={{ fontSize: 11, color: C.muted }}>{entry.strategyName}</span>}
        <span style={{ marginLeft: "auto", fontFamily: mono, fontSize: 12.5, fontWeight: 600, color: pnlColor }}>
          {pnlStr(entry.profitLoss)}
        </span>
        <span style={{ fontFamily: mono, fontSize: 10.5, color: C.muted2, minWidth: 78, textAlign: "right" }}>
          {fmtDate(entry.closedAt ?? entry.createdAt)}
        </span>
        <span style={{ color: C.muted2, fontSize: 12, transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }}>›</span>
      </button>

      {open && (
        <div style={{ padding: "0 16px 14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
          {entry.emotions && (
            <Field label="Emotions">
              <span style={{ color: C.text3 }}>{entry.emotions}</span>
            </Field>
          )}
          <Field label="Notes">
            <span style={{ color: C.text2, whiteSpace: "pre-wrap" }}>{entry.notes}</span>
          </Field>
          <Field label="AI review">
            <span style={{ color: C.text3, whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{entry.aiReview}</span>
          </Field>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ background: C.fill, border: `1px solid ${C.line}`, borderRadius: 9, padding: "10px 12px" }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", color: C.muted, marginBottom: 5 }}>{label}</div>
      <div style={{ fontSize: 12 }}>{children}</div>
    </div>
  );
}
