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
import { C, mono } from "@/lib/theme";
import {
  TIMEFRAMES,
  type Candle,
  type SignalsResponse,
  type Symbol,
  type Timeframe,
} from "@/lib/types";

const TF_LABEL: Record<Timeframe, string> = {
  "1min": "1m",
  "5min": "5m",
  "15min": "15m",
  "60min": "1H",
  daily: "1D",
};

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

function chip(label: string, value: string, sub: string, tone: string) {
  return (
    <div
      key={label}
      style={{
        flex: "none",
        minWidth: 96,
        padding: "8px 12px",
        background: C.fill,
        border: `1px solid ${C.hair}`,
        borderRadius: 9,
      }}
    >
      <div style={{ fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".07em", color: C.muted }}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 3 }}>
        <span style={{ fontFamily: mono, fontSize: 14, fontWeight: 600, color: tone }}>{value}</span>
        {sub && <span style={{ fontSize: 10, color: C.muted2 }}>{sub}</span>}
      </div>
    </div>
  );
}

function fmt(n: number | null | undefined, dp = 1): string {
  return n == null ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: dp });
}

export function ChartPanel({
  symbol,
  timeframe,
  onTimeframeChange,
}: {
  symbol: Symbol;
  timeframe: Timeframe;
  onTimeframeChange: (tf: Timeframe) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);

  const { data: candles } = useSWR<Candle[]>(
    `/api/candles?symbol=${symbol}&timeframe=${timeframe}&limit=300`,
    fetcher,
    { refreshInterval: 30_000 },
  );
  const { data: signals } = useSWR<SignalsResponse>(`/api/signals?symbol=${symbol}&limit=8`, fetcher, {
    refreshInterval: 30_000,
  });
  const signal = pickActiveSignal(signals?.data ?? []);

  const latest = (candles ?? [])
    .slice()
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))[0];
  const ind = latest?.indicators;

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: C.panelDeep },
        textColor: "#8b929b",
        fontFamily: mono,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.035)" },
        horzLines: { color: "rgba(255,255,255,0.035)" },
      },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
      timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const series = chart.addCandlestickSeries({
      upColor: C.up,
      downColor: C.down,
      borderVisible: false,
      wickUpColor: C.up,
      wickDownColor: C.down,
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

  useEffect(() => {
    if (!seriesRef.current || !candles || candles.length === 0) return;
    seriesRef.current.setData(toCandleData(candles));
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

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
    const mk = (price: number, color: string, title: string) =>
      series.createPriceLine({ price, color, lineWidth: 2, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title });
    linesRef.current = [
      mk(entry, C.gold, `${dir} @ ${entry}`),
      mk(sl, C.down, `STOP ${sl}`),
      mk(tp, C.up, `TARGET ${tp}`),
    ];
  }, [signal]);

  const rsi = ind?.rsi != null ? Number(ind.rsi) : null;
  const rsiSub = rsi == null ? "" : rsi >= 70 ? "overbought" : rsi <= 30 ? "oversold" : "neutral";

  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line2}`, borderRadius: 14, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: `1px solid ${C.line}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 style={{ margin: 0, fontSize: 13, fontWeight: 700 }}>{symbol}</h2>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: C.text3 }}>
            <span style={{ width: 14, height: 2, borderRadius: 2, background: C.blue }} />
            EMA 20
          </span>
        </div>
        <div style={{ display: "flex", gap: 4, padding: 3, background: "rgba(255,255,255,0.03)", borderRadius: 9 }}>
          {TIMEFRAMES.map((tf) => {
            const active = tf === timeframe;
            return (
              <button
                key={tf}
                onClick={() => onTimeframeChange(tf)}
                style={{
                  fontFamily: mono,
                  fontSize: 11,
                  padding: "4px 9px",
                  borderRadius: 6,
                  cursor: "pointer",
                  border: active ? "1px solid var(--accent, #f0b429)" : "1px solid transparent",
                  background: active ? "rgba(255,255,255,0.07)" : "transparent",
                  color: active ? "var(--accent, #f0b429)" : C.muted,
                  fontWeight: active ? 600 : 400,
                }}
              >
                {TF_LABEL[tf]}
              </button>
            );
          })}
        </div>
      </div>

      <div style={{ position: "relative", height: 392, background: C.panelDeep }}>
        <div ref={wrapRef} style={{ height: "100%", width: "100%" }} />
        {!signal && candles && (
          <div
            style={{
              position: "absolute",
              right: 12,
              top: 12,
              pointerEvents: "none",
              borderRadius: 6,
              border: `1px solid ${C.line}`,
              background: "rgba(10,11,14,0.8)",
              padding: "4px 10px",
              fontSize: 11,
              color: C.muted,
            }}
          >
            No active signal — waiting for a setup
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 10, padding: "12px 16px", borderTop: `1px solid ${C.line}`, overflowX: "auto" }}>
        {chip("RSI 14", fmt(rsi, 1), rsiSub, C.text)}
        {chip("EMA 20", fmt(ind?.ema20 != null ? Number(ind.ema20) : null, 2), "", C.text3)}
        {chip("EMA 50", fmt(ind?.ema50 != null ? Number(ind.ema50) : null, 2), "", C.text3)}
        {chip("EMA 200", fmt(ind?.ema200 != null ? Number(ind.ema200) : null, 2), "", C.text3)}
        {chip("ATR 14", fmt(ind?.atr != null ? Number(ind.atr) : null, 1), "", C.text3)}
      </div>
    </div>
  );
}
