"use client";

import {
  ColorType,
  createChart,
  LineStyle,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { findLargestCandleGap, formatDuration, marketStatus } from "@/lib/market";
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

function toEmaData(rows: Candle[]): LineData[] {
  return rows
    .filter((c) => c.indicators?.ema20 != null)
    .map((c) => ({
      time: Math.floor(Date.parse(c.timestamp) / 1000) as UTCTimestamp,
      value: Number(c.indicators?.ema20),
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
  const emaSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const fittedRef = useRef(false);
  const lastCandleTimeRef = useRef(0);
  const [followingLive, setFollowingLive] = useState(true);

  const { data: candles } = useSWR<Candle[]>(
    `/api/candles?symbol=${symbol}&timeframe=${timeframe}&limit=300`,
    fetcher,
    { refreshInterval: 30_000 },
  );
  const { data: signals } = useSWR<SignalsResponse>(`/api/signals?symbol=${symbol}&limit=8`, fetcher, {
    refreshInterval: 30_000,
  });
  const signal = pickActiveSignal(signals?.data ?? []);

  const ordered = (candles ?? [])
    .slice()
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp));
  const latest = ordered[0];
  const ind = latest?.indicators;
  const status = marketStatus(symbol, timeframe, latest?.timestamp);
  const gap = findLargestCandleGap(candles ?? [], timeframe, symbol);

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
      // Don't hijack the page: plain mouse-wheel scrolls the page as normal.
      // Chart zoom is opt-in — hold Ctrl (see the key listeners below) or drag.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
    });
    // Ctrl toggles wheel-zoom on the chart (and suppresses the browser's page
    // zoom while over it); releasing Ctrl gives the wheel back to page scroll.
    const setWheelZoom = (on: boolean) =>
      chart.applyOptions({ handleScale: { mouseWheel: on } });
    const onKey = (e: KeyboardEvent) => setWheelZoom(e.ctrlKey);
    const onBlur = () => setWheelZoom(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    window.addEventListener("blur", onBlur);
    const onVisibleRangeChange = () => {
      setFollowingLive(Math.abs(chart.timeScale().scrollPosition()) < 1);
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);
    const series = chart.addCandlestickSeries({
      upColor: C.up,
      downColor: C.down,
      borderVisible: false,
      wickUpColor: C.up,
      wickDownColor: C.down,
    });
    const emaSeries = chart.addLineSeries({
      color: C.blue,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: true,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    emaSeriesRef.current = emaSeries;
    linesRef.current = [];
    fittedRef.current = false;
    lastCandleTimeRef.current = 0;
    setFollowingLive(true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
      window.removeEventListener("blur", onBlur);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      emaSeriesRef.current = null;
      linesRef.current = [];
      fittedRef.current = false;
      lastCandleTimeRef.current = 0;
    };
  }, [symbol, timeframe]);

  useEffect(() => {
    if (!seriesRef.current || !candles || candles.length === 0) return;
    const candleData = toCandleData(candles);
    const emaData = toEmaData(candles);

    // TradingView-style lifecycle: load history once, then update/append only
    // the newest bar. This preserves the viewport and avoids redrawing 300 bars
    // every time a live OHLC payload arrives.
    if (!fittedRef.current) {
      seriesRef.current.setData(candleData);
      emaSeriesRef.current?.setData(emaData);
      const latestCandle = candleData.at(-1);
      lastCandleTimeRef.current = latestCandle
        ? Number(latestCandle.time)
        : 0;
      chartRef.current?.timeScale().fitContent();
      fittedRef.current = true;
      return;
    }

    const latestCandle = candleData.at(-1);
    const latestTime = latestCandle ? Number(latestCandle.time) : 0;
    if (latestCandle && latestTime >= lastCandleTimeRef.current) {
      seriesRef.current.update(latestCandle);
      lastCandleTimeRef.current = latestTime;
    }
    const latestEma = emaData.at(-1);
    if (latestEma) emaSeriesRef.current?.update(latestEma);
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
  const livePrice = latest ? Number(latest.close) : null;
  const candleOpen = latest ? Number(latest.open) : null;
  const priceDirection =
    livePrice == null || candleOpen == null || livePrice === candleOpen
      ? "flat"
      : livePrice > candleOpen
        ? "up"
        : "down";
  const pricePulseKey = latest
    ? `${latest.timestamp}:${latest.close}:${latest.volume}`
    : "empty";

  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line2}`, borderRadius: 14, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
      <div className="chart-toolbar">
        <div className="chart-identity">
          <h2>{symbol}</h2>
          <span className="chart-timeframe-label">{TF_LABEL[timeframe]}</span>
          {livePrice != null && (
            <span
              key={pricePulseKey}
              className="chart-stream-price"
              data-direction={priceDirection}
            >
              {livePrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </span>
          )}
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: C.text3 }}>
            <span style={{ width: 14, height: 2, borderRadius: 2, background: C.blue }} />
            EMA 20
          </span>
          <span className="chart-market-state" data-state={status.state} title={status.detail}>
            <span aria-hidden="true" />
            {status.label}
          </span>
          {gap && (
            <span className="chart-gap-warning" title={`Missing history between ${gap.start.toISOString()} and ${gap.end.toISOString()}`}>
              Data gap {formatDuration(gap.durationMs)}
            </span>
          )}
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

      <div className="chart-stage">
        <div ref={wrapRef} style={{ height: "100%", width: "100%" }} />
        {!followingLive && (
          <button
            type="button"
            className="chart-go-live"
            onClick={() => {
              chartRef.current?.timeScale().scrollToRealTime();
              setFollowingLive(true);
            }}
          >
            <span aria-hidden="true" />
            Go to live
          </button>
        )}
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
        <div
          style={{
            position: "absolute",
            left: 12,
            bottom: 10,
            pointerEvents: "none",
            fontSize: 10,
            fontFamily: mono,
            color: C.muted2,
            background: "rgba(10,11,14,0.6)",
            borderRadius: 5,
            padding: "2px 8px",
          }}
        >
          drag to pan · ctrl+scroll to zoom · double-click price axis to reset
        </div>
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
