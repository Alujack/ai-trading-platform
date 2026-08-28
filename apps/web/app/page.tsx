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
      <section className="dashboard-intro">
        <div className="dashboard-intro-copy">
          <div className="dashboard-eyebrow">AI-assisted decision workspace</div>
          <h1 className="dashboard-title">Trade the context, not the noise.</h1>
          <p className="dashboard-subtitle">
            Market structure, active setups, and risk controls in one focused view for {symbol} on the {timeframe} chart.
          </p>
        </div>
        <div className="intro-status">
          <span className="intro-status-dot" aria-hidden="true" />
          Risk guardrails active
        </div>
      </section>

      <KpiStrip />

      <section className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_372px]">
        <ChartPanel symbol={symbol} timeframe={timeframe} onTimeframeChange={setTimeframe} />
        <ActiveSetupPanel symbol={symbol} />
      </section>

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
