"use client";

import { ExecutionControlPanel, RiskControlPanel } from "../components/dash/ControlsPanel";
import { KpiStrip } from "../components/dash/KpiStrip";
import { NewsPanel } from "../components/dash/NewsPanel";
import { RiskEnginePanel } from "../components/dash/RiskEnginePanel";

export default function RiskPage() {
  return (
    <>
      <KpiStrip />
      <section className="page-grid-two">
        <ExecutionControlPanel />
        <RiskEnginePanel />
      </section>
      <section className="page-grid-feature">
        <RiskControlPanel />
        <NewsPanel />
      </section>
    </>
  );
}
