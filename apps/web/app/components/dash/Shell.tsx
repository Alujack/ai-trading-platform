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
        style={
          {
            "--accent": accent,
            display: "flex",
            minHeight: "100vh",
            background: C.bg,
            color: C.text,
          } as React.CSSProperties
        }
      >
        <LeftNavRail accent={accent} onAccent={setAccent} />

        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <TopBar
            symbol={symbol}
            timeframe={timeframe}
            onSymbolChange={setSymbol}
            onAiClick={openSettings}
            rtStatus={rtStatus}
          />
          <main
            style={{
              padding: "18px 24px 40px",
              display: "flex",
              flexDirection: "column",
              gap: 16,
              width: "100%",
              maxWidth: 1720,
              margin: "0 auto",
            }}
          >
            {children}
          </main>
        </div>
      </div>
    </DashContext.Provider>
  );
}
