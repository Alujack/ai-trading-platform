"use client";

import {
  ColorType,
  createChart,
  LineStyle,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { pickActiveSignal } from "@/lib/signals";
import type { Candle, SignalsResponse, Symbol, Timeframe } from "@/lib/types";

const UP = "#4fb286";
const DOWN = "#e5604d";

function toCandleData(rows: Candle[]): CandlestickData[] {
  return rows
    .map((c) => ({
      time: Math.floor(Date.parse(c.timestamp) / 1000) as UTCTimestamp,
      open: Number(c.open),
      high: Number(c.high),
      low: Number(c.low),
      close: Number(c.close),
    }))
    .sort((a, b) => (a.time as number) - (b.time as number));
}

export function SignalChart({ symbol, timeframe }: { symbol: Symbol; timeframe: Timeframe }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);

  const { data: candles } = useSWR<Candle[]>(
    `/api/candles?symbol=${symbol}&timeframe=${timeframe}&limit=300`,
    fetcher,
    { refreshInterval: 30_000 },
  );
  const { data: signals } = useSWR<SignalsResponse>(
    `/api/signals?symbol=${symbol}&limit=8`,
    fetcher,
    { refreshInterval: 30_000 },
  );
  const signal = pickActiveSignal(signals?.data ?? []);

  // Create the chart once per symbol/timeframe.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0a0a0a" },
        textColor: "#a3a3a3",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      rightPriceScale: { borderColor: "#262626" },
      timeScale: { borderColor: "#262626", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const series = chart.addCandlestickSeries({
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    linesRef.current = [];
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      linesRef.current = [];
    };
  }, [symbol, timeframe]);

  // Feed candle data.
  useEffect(() => {
    if (!seriesRef.current || !candles || candles.length === 0) return;
    seriesRef.current.setData(toCandleData(candles));
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  // Draw / redraw the signal's entry, stop, and target.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    for (const line of linesRef.current) series.removePriceLine(line);
    linesRef.current = [];
    if (!signal) return;

    const entry = Number(signal.entryPrice);
    const sl = Number(signal.stopLoss);
    const tp = Number(signal.takeProfit);
    const dir = signal.direction === "LONG" ? "BUY" : "SELL";
    const mk = (price: number, color: string, title: string): IPriceLine =>
      series.createPriceLine({
        price,
        color,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title,
      });
    linesRef.current = [
      mk(entry, "#e5b341", `${dir} @ ${entry}`),
      mk(sl, DOWN, `STOP ${sl}`),
      mk(tp, UP, `TARGET ${tp}`),
    ];
  }, [signal]);

  return (
    <div className="relative h-[640px] w-full overflow-hidden rounded-lg border border-neutral-800 bg-[#0a0a0a]">
      <div ref={wrapRef} className="h-full w-full" />
      {!signal && candles && (
        <div className="pointer-events-none absolute right-3 top-3 rounded border border-neutral-800 bg-neutral-950/80 px-2.5 py-1 text-[11px] text-neutral-500">
          No active signal for {symbol} — waiting for a setup
        </div>
      )}
    </div>
  );
}
