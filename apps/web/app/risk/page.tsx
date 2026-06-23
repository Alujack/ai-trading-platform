"use client";

import { ExecutionControlPanel, RiskControlPanel } from "../components/dash/ControlsPanel";
import { KpiStrip } from "../components/dash/KpiStrip";
import { NewsPanel } from "../components/dash/NewsPanel";
import { RiskEnginePanel } from "../components/dash/RiskEnginePanel";

export default function RiskPage() {
  return (
    <>
      <KpiStrip />
      <section
        style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "start" }}
      >
        <ExecutionControlPanel />
        <RiskEnginePanel />
      </section>
      <section
        style={{
          display: "grid",
          gridTemplateColumns: "1.4fr 1fr",
          gap: 16,
          alignItems: "start",
          marginTop: 16,
        }}
      >
        <RiskControlPanel />
        <NewsPanel />
      </section>
    </>
  );
}
