"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { C, mono } from "@/lib/theme";
import type { DriftResponse, StrategyDrift } from "@/lib/types";
import { Panel, Pill } from "./ui";

const COLS = "1.5fr 0.6fr 0.8fr 0.7fr 1fr 0.9fr";

/**
 * Is the confidence score informative? Positive spread between the top and
 * bottom populated decile means the model ranks its own trades correctly.
 * Anything inside +/-5 points is noise at realistic sample sizes.
 */
function discriminationTone(d: number | null): { color: string; label: string } {
  if (d === null) return { color: C.muted2, label: "—" };
  if (d > 5) return { color: C.up, label: `+${d.toFixed(1)}` };
  if (d < -5) return { color: C.down, label: d.toFixed(1) };
  return { color: C.muted, label: `${d > 0 ? "+" : ""}${d.toFixed(1)}` };
}

/** Recent minus lifetime win rate. Negative = decaying. */
function driftTone(d: number | null): { color: string; label: string } {
  if (d === null) return { color: C.muted2, label: "—" };
  if (d < -10) return { color: C.down, label: d.toFixed(1) };
  if (d > 10) return { color: C.up, label: `+${d.toFixed(1)}` };
  return { color: C.muted, label: `${d > 0 ? "+" : ""}${d.toFixed(1)}` };
}

function Row({ s }: { s: StrategyDrift }) {
  const disc = discriminationTone(s.discrimination);
  const dr = driftTone(s.drift);
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: COLS,
        gap: 8,
        padding: "9px 16px",
        borderTop: `1px solid ${C.hair}`,
        alignItems: "center",
        fontSize: 12,
      }}
    >
      <span style={{ color: C.text2, fontFamily: mono, overflow: "hidden", textOverflow: "ellipsis" }}>
        {s.strategyName}
      </span>
      <span style={{ color: C.muted, fontFamily: mono, textAlign: "right" }}>{s.trades}</span>
      <span style={{ color: C.text3, fontFamily: mono, textAlign: "right" }}>
        {s.meanConfidence.toFixed(0)}
      </span>
      <span style={{ color: C.text3, fontFamily: mono, textAlign: "right" }}>
        {s.winRate.toFixed(1)}%
      </span>
      <span style={{ color: disc.color, fontFamily: mono, textAlign: "right", fontWeight: 600 }}>
        {disc.label}
      </span>
      <span style={{ color: dr.color, fontFamily: mono, textAlign: "right", fontWeight: 600 }}>
        {dr.label}
      </span>
    </div>
  );
}

export function ModelDriftPanel() {
  const { data, error } = useSWR<DriftResponse>("/api/performance/drift", fetcher, {
    refreshInterval: 60_000,
  });

  const rows = data?.strategies ?? [];
  const scored = rows.filter((s) => s.discrimination !== null || s.drift !== null).length;

  return (
    <Panel
      title="Model Drift — claimed vs realized"
      right={
        <Pill
          color={scored > 0 ? C.blue : C.muted}
          bg="rgba(107,134,196,0.10)"
          border="rgba(107,134,196,0.25)"
        >
          {data ? `last ${data.window} trades` : "…"}
        </Pill>
      }
      noBody
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: COLS,
          gap: 8,
          padding: "8px 16px",
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: ".06em",
          color: C.muted2,
          borderBottom: `1px solid ${C.hair}`,
        }}
      >
        <span>Strategy</span>
        <span style={{ textAlign: "right" }}>Trades</span>
        <span style={{ textAlign: "right" }}>Conf</span>
        <span style={{ textAlign: "right" }}>Win%</span>
        <span style={{ textAlign: "right" }}>Discrim.</span>
        <span style={{ textAlign: "right" }}>Drift</span>
      </div>

      {error ? (
        <div style={{ padding: 16, fontSize: 12, color: C.down }}>Could not load drift data.</div>
      ) : rows.length === 0 ? (
        <div style={{ padding: 16, fontSize: 12, color: C.muted, lineHeight: 1.6 }}>
          No closed trades yet. This panel scores every signal&apos;s confidence against what the
          trade actually did, so it fills in once trades start resolving.
        </div>
      ) : (
        rows.map((s) => <Row key={s.strategyName} s={s} />)
      )}

      <div
        style={{
          padding: "10px 16px",
          borderTop: `1px solid ${C.hair}`,
          fontSize: 10.5,
          color: C.muted2,
          lineHeight: 1.65,
        }}
      >
        <strong style={{ color: C.muted }}>Discrim.</strong> = win rate of the highest-confidence
        decile minus the lowest. Positive means the score ranks trades correctly; ~0 means it is
        noise. <strong style={{ color: C.muted }}>Drift</strong> = recent win rate minus lifetime.
        Negative means the model is decaying. Raw <em>Conf</em> vs <em>Win%</em> is{" "}
        <em>not</em> a like-for-like comparison — confidence is a model score, not a calibrated
        probability.
      </div>
    </Panel>
  );
}
