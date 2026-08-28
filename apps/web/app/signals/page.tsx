"use client";

import { ChartPanel } from "../components/dash/ChartPanel";
import { useDash } from "../components/dash/DashContext";
import { RawFeedPanel } from "../components/dash/RawFeedPanel";
import { RecentSignalsPanel } from "../components/dash/RecentSignalsPanel";

export default function SignalsPage() {
  const { symbol, timeframe, setTimeframe } = useDash();

  return (
    <>
      <ChartPanel symbol={symbol} timeframe={timeframe} onTimeframeChange={setTimeframe} />
      {/* Gated signals: what cleared every layer and automation can act on. */}
      <RecentSignalsPanel limit={50} title="All Signals" />
      {/* Pure strategy output, layers named not applied — observe-only, for manual trading. */}
      <RawFeedPanel limit={50} />
    </>
  );
}
