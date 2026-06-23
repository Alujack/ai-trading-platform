"use client";

import { ActiveSetupPanel } from "./components/dash/ActiveSetupPanel";
import { AiReadPanel } from "./components/dash/AiReadPanel";
import { ChartPanel } from "./components/dash/ChartPanel";
import { useDash } from "./components/dash/DashContext";
import { KpiStrip } from "./components/dash/KpiStrip";
import { NewsPanel } from "./components/dash/NewsPanel";
import { PositionsPanel } from "./components/dash/PositionsPanel";
import { RecentSignalsPanel } from "./components/dash/RecentSignalsPanel";
import { RiskEnginePanel } from "./components/dash/RiskEnginePanel";

export default function HomePage() {
  const { symbol, timeframe, setTimeframe } = useDash();

  return (
    <>
      <KpiStrip />

      <section style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 372px", gap: 16, alignItems: "start" }}>
        <ChartPanel symbol={symbol} timeframe={timeframe} onTimeframeChange={setTimeframe} />
        <ActiveSetupPanel symbol={symbol} />
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16, alignItems: "start" }}>
        <RiskEnginePanel />
        <AiReadPanel symbol={symbol} timeframe={timeframe} />
        <NewsPanel />
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "start" }}>
        <PositionsPanel />
        <RecentSignalsPanel />
      </section>
    </>
  );
}
