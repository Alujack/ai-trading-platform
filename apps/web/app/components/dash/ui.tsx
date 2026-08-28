"use client";

import type { CSSProperties, ReactNode } from "react";
import { C } from "@/lib/theme";

export const panelStyle: CSSProperties = {
  background: "linear-gradient(160deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012))",
  border: `1px solid ${C.line2}`,
  borderRadius: 15,
  boxShadow: "0 18px 44px rgba(0,0,0,.15)",
  overflow: "hidden",
};

export function Panel({
  title,
  right,
  children,
  bodyStyle,
  noBody,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  bodyStyle?: CSSProperties;
  noBody?: boolean;
}) {
  return (
    <div style={{ ...panelStyle, display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          minHeight: 44,
          padding: "12px 16px",
          borderBottom: `1px solid ${C.line}`,
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 10.5,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: ".08em",
            color: C.text3,
          }}
        >
          {title}
        </h2>
        {right}
      </div>
      {noBody ? children : <div style={{ padding: 16, ...bodyStyle }}>{children}</div>}
    </div>
  );
}

export function ProgressBar({ pct, color }: { pct: number; color: string }) {
  return (
    <div style={{ height: 7, borderRadius: 4, background: "rgba(255,255,255,0.06)", overflow: "hidden" }}>
      <div style={{ height: "100%", width: `${Math.max(0, Math.min(100, pct))}%`, background: color, borderRadius: 4 }} />
    </div>
  );
}

export function Pill({
  children,
  color,
  bg,
  border,
}: {
  children: ReactNode;
  color: string;
  bg: string;
  border?: string;
}) {
  return (
    <span
      style={{
        padding: "3px 9px",
        borderRadius: 20,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: ".04em",
        background: bg,
        color,
        border: border ? `1px solid ${border}` : undefined,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

export function StatusDot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span
      style={{
        width: 7,
        height: 7,
        borderRadius: "50%",
        background: color,
        display: "inline-block",
        animation: pulse ? "livedot 2s ease-in-out infinite" : undefined,
      }}
    />
  );
}
