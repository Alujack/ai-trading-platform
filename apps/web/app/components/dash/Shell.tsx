"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { C, type Accent } from "@/lib/theme";
import { useRealtime } from "@/lib/useRealtime";
import type { Symbol, Timeframe } from "@/lib/types";
import { DashContext } from "./DashContext";
import { LeftNavRail } from "./LeftNavRail";
import { TopBar } from "./TopBar";

export function Shell({ children }: { children: React.ReactNode }) {
  const [symbol, setSymbol] = useState<Symbol>("XAUUSD");
  const [timeframe, setTimeframe] = useState<Timeframe>("60min");
  const [accent, setAccent] = useState<Accent>("#f0b429");
  const router = useRouter();
  const openSettings = () => router.push("/settings");
  const rtStatus = useRealtime();

  return (
    <DashContext.Provider
      value={{ symbol, timeframe, setSymbol, setTimeframe, openSettings, rtStatus }}
    >
      <div
        className="app-shell"
        style={
          {
            "--accent": accent,
            background: C.bg,
          } as React.CSSProperties
        }
      >
        <LeftNavRail accent={accent} onAccent={setAccent} />

        <div className="app-column">
          <TopBar
            symbol={symbol}
            timeframe={timeframe}
            onSymbolChange={setSymbol}
            onAiClick={openSettings}
            rtStatus={rtStatus}
          />
          <main className="dashboard-main">
            {children}
          </main>
        </div>
      </div>
    </DashContext.Provider>
  );
}
