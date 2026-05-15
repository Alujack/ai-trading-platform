"use client";

import { useEffect, useRef } from "react";
import { TV_INTERVAL, TV_SYMBOL } from "@/lib/tradingview";
import type { Symbol, Timeframe } from "@/lib/types";

const TV_SCRIPT_SRC = "https://s3.tradingview.com/tv.js";
const TV_SCRIPT_ID = "tradingview-widget-script";

declare global {
  interface Window {
    TradingView?: {
      widget: new (config: Record<string, unknown>) => unknown;
    };
  }
}

function loadTradingViewScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (typeof window === "undefined") return resolve();
    if (window.TradingView) return resolve();
    const existing = document.getElementById(TV_SCRIPT_ID) as HTMLScriptElement | null;
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error("tv.js failed")), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.id = TV_SCRIPT_ID;
    script.src = TV_SCRIPT_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("tv.js failed"));
    document.head.appendChild(script);
  });
}

interface TradingViewChartProps {
  symbol: Symbol;
  timeframe: Timeframe;
}

export function TradingViewChart({ symbol, timeframe }: TradingViewChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const containerId = `tv_chart_${symbol}_${timeframe}`;

  useEffect(() => {
    let cancelled = false;
    const el = containerRef.current;
    if (!el) return;
    el.innerHTML = "";

    loadTradingViewScript()
      .then(() => {
        if (cancelled || !window.TradingView || !containerRef.current) return;
        new window.TradingView.widget({
          container_id: containerId,
          symbol: TV_SYMBOL[symbol],
          interval: TV_INTERVAL[timeframe],
          theme: "dark",
          style: "1",
          locale: "en",
          timezone: "Etc/UTC",
          toolbar_bg: "#0a0a0a",
          enable_publishing: false,
          allow_symbol_change: false,
          hide_side_toolbar: false,
          withdateranges: true,
          autosize: true,
        });
      })
      .catch(() => {
        if (cancelled || !containerRef.current) return;
        containerRef.current.innerHTML =
          '<div class="flex h-full items-center justify-center text-sm text-neutral-500">Failed to load TradingView chart</div>';
      });

    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe, containerId]);

  return (
    <div className="h-[640px] w-full overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900/30">
      <div id={containerId} ref={containerRef} className="h-full w-full" />
    </div>
  );
}
