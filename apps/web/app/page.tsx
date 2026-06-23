"use client";

import { useState } from "react";
import { IndicatorSidebar } from "./components/IndicatorSidebar";
import { MarketContextCard } from "./components/MarketContextCard";
import { Navbar } from "./components/Navbar";
import { PerformanceCard } from "./components/PerformanceCard";
import { SignalsTable } from "./components/SignalsTable";
import { TradingViewChart } from "./components/TradingViewChart";
import type { Symbol, Timeframe } from "@/lib/types";

export default function DashboardPage() {
  const [symbol, setSymbol] = useState<Symbol>("XAUUSD");
  const [timeframe, setTimeframe] = useState<Timeframe>("60min");

  return (
    <div className="min-h-screen">
      <Navbar
        symbol={symbol}
        timeframe={timeframe}
        onSymbolChange={setSymbol}
        onTimeframeChange={setTimeframe}
      />

      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6">
        <PerformanceCard />

        <MarketContextCard symbol={symbol} timeframe={timeframe} />

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
          <TradingViewChart symbol={symbol} timeframe={timeframe} />
          <IndicatorSidebar symbol={symbol} timeframe={timeframe} />
        </div>

        <SignalsTable />
      </main>
    </div>
  );
}
