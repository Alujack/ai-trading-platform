"use client";

import { useState } from "react";
import { C, type Accent } from "@/lib/theme";
import { useRealtime } from "@/lib/useRealtime";
import type { Symbol, Timeframe } from "@/lib/types";
import { AiSettingsModal } from "../AiSettingsModal";
import { DashContext } from "./DashContext";
import { LeftNavRail } from "./LeftNavRail";
import { TopBar } from "./TopBar";

export function Shell({ children }: { children: React.ReactNode }) {
  const [symbol, setSymbol] = useState<Symbol>("XAUUSD");
  const [timeframe, setTimeframe] = useState<Timeframe>("60min");
  const [accent, setAccent] = useState<Accent>("#f0b429");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const rtStatus = useRealtime();

  return (
    <DashContext.Provider
      value={{ symbol, timeframe, setSymbol, setTimeframe, openSettings: () => setSettingsOpen(true), rtStatus }}
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
        <LeftNavRail onSetup={() => setSettingsOpen(true)} accent={accent} onAccent={setAccent} />

        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <TopBar
            symbol={symbol}
            timeframe={timeframe}
            onSymbolChange={setSymbol}
            onAiClick={() => setSettingsOpen(true)}
            rtStatus={rtStatus}
          />
          <main style={{ padding: "18px 24px 32px", display: "flex", flexDirection: "column", gap: 16 }}>
            {children}
          </main>
        </div>

        <AiSettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      </div>
    </DashContext.Provider>
  );
}
