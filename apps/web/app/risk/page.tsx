"use client";

import { AiReadPanel } from "../components/dash/AiReadPanel";
import { useDash } from "../components/dash/DashContext";
import { KpiStrip } from "../components/dash/KpiStrip";
import { NewsPanel } from "../components/dash/NewsPanel";
import { RiskEnginePanel } from "../components/dash/RiskEnginePanel";

export default function RiskPage() {
  const { symbol, timeframe } = useDash();

  return (
    <>
      <KpiStrip />
      <section style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16, alignItems: "start" }}>
        <RiskEnginePanel />
        <AiReadPanel symbol={symbol} timeframe={timeframe} />
        <NewsPanel />
      </section>
    </>
  );
}
