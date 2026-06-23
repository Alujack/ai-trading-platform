"use client";

import { ChartPanel } from "../components/dash/ChartPanel";
import { useDash } from "../components/dash/DashContext";
import { RecentSignalsPanel } from "../components/dash/RecentSignalsPanel";

export default function SignalsPage() {
  const { symbol, timeframe, setTimeframe } = useDash();

  return (
    <>
      <ChartPanel symbol={symbol} timeframe={timeframe} onTimeframeChange={setTimeframe} />
      <RecentSignalsPanel limit={50} title="All Signals" />
    </>
  );
}
