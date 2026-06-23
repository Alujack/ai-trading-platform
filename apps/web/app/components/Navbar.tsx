"use client";

import { SYMBOLS, TIMEFRAMES, type Symbol, type Timeframe } from "@/lib/types";
import { AiProviderToggle } from "./AiProviderToggle";

interface NavbarProps {
  symbol: Symbol;
  timeframe: Timeframe;
  onSymbolChange: (symbol: Symbol) => void;
  onTimeframeChange: (timeframe: Timeframe) => void;
}

export function Navbar({ symbol, timeframe, onSymbolChange, onTimeframeChange }: NavbarProps) {
  return (
    <header className="sticky top-0 z-20 border-b border-neutral-800 bg-neutral-950/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-4">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded bg-emerald-500/15 text-emerald-400">
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4" aria-hidden>
              <path
                d="M3 17l5-5 4 4 8-8"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <span className="text-base font-semibold tracking-tight">TradingAI</span>
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs">
            <span className="text-neutral-500">Symbol</span>
            <select
              value={symbol}
              onChange={(e) => onSymbolChange(e.target.value as Symbol)}
              className="rounded border border-neutral-800 bg-neutral-900 px-2 py-1 text-sm font-medium text-neutral-100 focus:border-neutral-600 focus:outline-none"
            >
              {SYMBOLS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-2 text-xs">
            <span className="text-neutral-500">Timeframe</span>
            <select
              value={timeframe}
              onChange={(e) => onTimeframeChange(e.target.value as Timeframe)}
              className="rounded border border-neutral-800 bg-neutral-900 px-2 py-1 text-sm font-medium text-neutral-100 focus:border-neutral-600 focus:outline-none"
            >
              {TIMEFRAMES.map((tf) => (
                <option key={tf} value={tf}>
                  {tf}
                </option>
              ))}
            </select>
          </label>

          <span className="hidden h-5 w-px bg-neutral-800 sm:block" aria-hidden />
          <AiProviderToggle />
        </div>
      </div>
    </header>
  );
}
