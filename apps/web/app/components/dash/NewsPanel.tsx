"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { C, mono, tint } from "@/lib/theme";
import type { Impact, NewsItem, NewsResponse } from "@/lib/types";
import { Panel, Pill } from "./ui";

const IMPACT: Record<Impact, { color: string; bg: string; label: string }> = {
  HIGH: { color: C.down, bg: tint(C.down, 0.14), label: "HIGH" },
  MEDIUM: { color: C.warn, bg: tint(C.warn, 0.14), label: "MED" },
  LOW: { color: "#8b929b", bg: "rgba(255,255,255,0.06)", label: "LOW" },
};

function when(iso: string, upcoming: boolean): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${upcoming ? "in " : ""}${day} ${time}`;
}

function meta(n: NewsItem): string {
  const parts: string[] = [];
  if (n.actual) parts.push(`act ${n.actual}`);
  if (n.forecast) parts.push(`fc ${n.forecast}`);
  if (n.previous) parts.push(`prev ${n.previous}`);
  return parts.join("  ·  ");
}

export function NewsPanel() {
  const { data, isLoading } = useSWR<NewsResponse>("/api/news?limit=8", fetcher, { refreshInterval: 60_000 });
  const rows = data?.data ?? [];
  const highAhead = rows.filter((r) => r.impact === "HIGH" && r.upcoming).length;

  return (
    <Panel
      title="News & Calendar"
      right={highAhead > 0 ? <Pill color={C.down} bg={tint(C.down, 0.14)}>{highAhead} high-impact ahead</Pill> : undefined}
      noBody
    >
      {isLoading && <p style={{ padding: "16px", margin: 0, fontSize: 12.5, color: C.muted }}>Loading…</p>}
      {!isLoading && rows.length === 0 && (
        <p style={{ padding: "16px", margin: 0, fontSize: 12.5, color: C.muted, lineHeight: 1.6 }}>
          No news yet — the n8n calendar + AI-summary workflows populate this feed.
        </p>
      )}
      {rows.map((n) => {
        const im = IMPACT[n.impact];
        const m = meta(n) || (n.aiSummary ? n.aiSummary.slice(0, 60) : "");
        return (
          <div key={n.id} style={{ display: "flex", gap: 11, padding: "12px 16px", borderBottom: `1px solid ${C.hair}` }}>
            <span style={{ flex: "none", height: "fit-content", padding: "2px 7px", borderRadius: 5, fontSize: 9, fontWeight: 700, letterSpacing: ".04em", background: im.bg, color: im.color }}>
              {im.label}
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
                <span style={{ fontFamily: mono, fontSize: 10, color: C.muted2 }}>{n.currency}</span>
                <span style={{ fontSize: 12.5, color: "#d4d8de", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.title}</span>
              </div>
              {m && <div style={{ fontFamily: mono, fontSize: 10, color: C.muted2, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m}</div>}
            </div>
            <span style={{ flex: "none", fontFamily: mono, fontSize: 10.5, color: n.upcoming ? C.up : C.muted2 }}>{when(n.scheduledAt, n.upcoming)}</span>
          </div>
        );
      })}
    </Panel>
  );
}
