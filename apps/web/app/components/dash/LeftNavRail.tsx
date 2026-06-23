"use client";

import {
  LayoutDashboard,
  Radio,
  CandlestickChart,
  ShieldCheck,
  Settings,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ACCENTS, ACCENT_LABELS, C, type Accent } from "@/lib/theme";

const ITEMS: { href: string; label: string; Icon: LucideIcon }[] = [
  { href: "/", label: "Home", Icon: LayoutDashboard },
  { href: "/signals", label: "Signals", Icon: Radio },
  { href: "/trades", label: "Trades", Icon: CandlestickChart },
  { href: "/risk", label: "Risk", Icon: ShieldCheck },
];

export function LeftNavRail({
  onSetup,
  accent,
  onAccent,
}: {
  onSetup: () => void;
  accent: Accent;
  onAccent: (a: Accent) => void;
}) {
  const pathname = usePathname();

  return (
    <nav
      style={{
        width: 66,
        flex: "none",
        borderRight: `1px solid ${C.line}`,
        background: C.rail,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "14px 0",
        gap: 4,
        position: "sticky",
        top: 0,
        height: "100vh",
      }}
    >
      <div
        style={{
          width: 38,
          height: 38,
          borderRadius: 10,
          background: "var(--accent, #f0b429)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontWeight: 800,
          fontSize: 15,
          color: "#1a1306",
          marginBottom: 10,
          boxShadow: "0 4px 14px rgba(240,180,41,0.25)",
        }}
      >
        Au
      </div>

      {ITEMS.map((it) => {
        const active = pathname === it.href;
        return (
          <Link key={it.href} href={it.href} style={{ textDecoration: "none" }}>
            <RailItem label={it.label} Icon={it.Icon} active={active} />
          </Link>
        );
      })}

      <div style={{ flex: 1 }} />

      {/* accent picker */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, marginBottom: 8 }}>
        {ACCENTS.map((a) => (
          <button
            key={a}
            onClick={() => onAccent(a)}
            title={`${ACCENT_LABELS[a]} accent`}
            aria-label={`${ACCENT_LABELS[a]} accent`}
            style={{
              width: 14,
              height: 14,
              borderRadius: "50%",
              background: a,
              border: accent === a ? "2px solid #fff" : "2px solid transparent",
              cursor: "pointer",
              padding: 0,
            }}
          />
        ))}
      </div>

      <button
        onClick={onSetup}
        title="AI providers & settings"
        style={btnReset}
      >
        <RailItem label="Setup" Icon={Settings} />
      </button>
    </nav>
  );
}

const btnReset = { border: "none", background: "transparent", padding: 0, cursor: "pointer", fontFamily: "inherit" } as const;

function RailItem({ label, Icon, active }: { label: string; Icon: LucideIcon; active?: boolean }) {
  return (
    <div
      style={{
        position: "relative",
        width: 50,
        height: 48,
        borderRadius: 11,
        background: active ? "rgba(255,255,255,0.05)" : "transparent",
        color: active ? "var(--accent, #f0b429)" : C.muted,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 3,
      }}
    >
      {active && (
        <span
          style={{
            position: "absolute",
            left: -8,
            top: 11,
            width: 3,
            height: 26,
            borderRadius: 3,
            background: "var(--accent, #f0b429)",
          }}
        />
      )}
      <Icon size={19} strokeWidth={1.8} />
      <span style={{ fontSize: 8.5, letterSpacing: ".02em" }}>{label}</span>
    </div>
  );
}
