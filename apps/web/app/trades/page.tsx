"use client";

import { KpiStrip } from "../components/dash/KpiStrip";
import { PositionsPanel } from "../components/dash/PositionsPanel";
import { RecentSignalsPanel } from "../components/dash/RecentSignalsPanel";

export default function TradesPage() {
  return (
    <>
      <KpiStrip />
      <section className="page-grid-two">
        <PositionsPanel />
        <RecentSignalsPanel limit={20} title="Signal History" />
      </section>
    </>
  );
}
