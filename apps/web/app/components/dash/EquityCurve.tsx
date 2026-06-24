"use client";

import { C, mono, tint } from "@/lib/theme";

/**
 * Self-contained SVG equity curve — one point per closed trade. No chart library
 * needed; the curve scales to its container via viewBox.
 */
export function EquityCurve({
  points,
  starting,
  height = 180,
}: {
  points: [string, number][];
  starting: number;
  height?: number;
}) {
  if (!points || points.length === 0) {
    return (
      <div style={{ padding: 24, textAlign: "center", color: C.muted, fontSize: 12.5 }}>
        No closed trades — nothing to plot.
      </div>
    );
  }

  const W = 600;
  const H = height;
  const padX = 8;
  const padY = 14;

  const equities = points.map((p) => p[1]);
  const end = equities[equities.length - 1];
  const up = end >= starting;
  const line = up ? C.up : C.down;

  // Include the starting balance and the baseline in the y-range.
  const allYs = [...equities, starting];
  const min = Math.min(...allYs);
  const max = Math.max(...allYs);
  const span = max - min || 1;

  // x: include an implicit point 0 at the starting balance for a clean origin.
  const n = points.length;
  const x = (i: number) => padX + (i / Math.max(1, n)) * (W - 2 * padX);
  const y = (v: number) => padY + (1 - (v - min) / span) * (H - 2 * padY);

  // Build the path starting from the opening balance.
  let d = `M ${x(0).toFixed(1)} ${y(starting).toFixed(1)}`;
  points.forEach((p, i) => {
    d += ` L ${x(i + 1).toFixed(1)} ${y(p[1]).toFixed(1)}`;
  });

  const baseY = y(starting);
  const areaD = `${d} L ${x(n).toFixed(1)} ${(H - padY).toFixed(1)} L ${x(0).toFixed(1)} ${(H - padY).toFixed(1)} Z`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ display: "block" }}>
      <defs>
        <linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={tint(line, 0.22)} />
          <stop offset="100%" stopColor={tint(line, 0)} />
        </linearGradient>
      </defs>
      {/* starting-balance baseline */}
      <line x1={padX} y1={baseY} x2={W - padX} y2={baseY} stroke={C.muted2} strokeWidth={1} strokeDasharray="4 4" opacity={0.6} />
      <path d={areaD} fill="url(#eqfill)" />
      <path d={d} fill="none" stroke={line} strokeWidth={1.8} strokeLinejoin="round" />
      <text x={padX + 2} y={baseY - 4} fontSize={9} fill={C.muted} fontFamily={mono}>
        {Math.round(starting).toLocaleString()}
      </text>
    </svg>
  );
}
