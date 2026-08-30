"use client";

import { ActiveSetupPanel } from "./components/dash/ActiveSetupPanel";
import { AiReadPanel } from "./components/dash/AiReadPanel";
import { ChartPanel } from "./components/dash/ChartPanel";
import { useDash } from "./components/dash/DashContext";
import { KpiStrip } from "./components/dash/KpiStrip";
import { MarketCommandHeader } from "./components/dash/MarketCommandHeader";
import { NewsPanel } from "./components/dash/NewsPanel";
import { PositionsPanel } from "./components/dash/PositionsPanel";
import { RecentSignalsPanel } from "./components/dash/RecentSignalsPanel";
import { RiskEnginePanel } from "./components/dash/RiskEnginePanel";

export default function HomePage() {
  const { symbol, timeframe, setTimeframe, rtStatus } = useDash();

  return (
    <>
      <MarketCommandHeader symbol={symbol} timeframe={timeframe} rtStatus={rtStatus} />

      <section className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_356px]">
        <ChartPanel symbol={symbol} timeframe={timeframe} onTimeframeChange={setTimeframe} />
        <ActiveSetupPanel symbol={symbol} />
      </section>

      <KpiStrip />

      <section className="grid grid-cols-1 items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
        <RiskEnginePanel />
        <AiReadPanel symbol={symbol} timeframe={timeframe} />
        <NewsPanel />
      </section>

      <section className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
        <PositionsPanel />
        <RecentSignalsPanel />
      </section>
    </>
  );
}
