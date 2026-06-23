"use client";

import { KpiStrip } from "../components/dash/KpiStrip";
import { PositionsPanel } from "../components/dash/PositionsPanel";
import { RecentSignalsPanel } from "../components/dash/RecentSignalsPanel";

export default function TradesPage() {
  return (
    <>
      <KpiStrip />
      <section style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "start" }}>
        <PositionsPanel />
        <RecentSignalsPanel limit={20} title="Signal History" />
      </section>
    </>
  );
}
