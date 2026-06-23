"use client";

import { createContext, useContext } from "react";
import type { RtStatus } from "@/lib/useRealtime";
import type { Symbol, Timeframe } from "@/lib/types";

export interface DashState {
  symbol: Symbol;
  timeframe: Timeframe;
  setSymbol: (s: Symbol) => void;
  setTimeframe: (t: Timeframe) => void;
  openSettings: () => void;
  rtStatus: RtStatus;
}

export const DashContext = createContext<DashState | null>(null);

export function useDash(): DashState {
  const ctx = useContext(DashContext);
  if (!ctx) throw new Error("useDash must be used inside <Shell>");
  return ctx;
}
