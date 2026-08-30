"use client";

import {
  LayoutDashboard,
  Radio,
  CandlestickChart,
  BookOpen,
  ShieldCheck,
  FlaskConical,
  Settings,
  type LucideIcon,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ACCENTS, ACCENT_LABELS, C, type Accent } from "@/lib/theme";

const ITEMS: { href: string; label: string; Icon: LucideIcon }[] = [
  { href: "/", label: "Home", Icon: LayoutDashboard },
  { href: "/signals", label: "Signals", Icon: Radio },
  { href: "/trades", label: "Trades", Icon: CandlestickChart },
  { href: "/journal", label: "Journal", Icon: BookOpen },
  { href: "/risk", label: "Risk", Icon: ShieldCheck },
  { href: "/backtests", label: "Backtest", Icon: FlaskConical },
];

const MOBILE_ITEMS = [
  ITEMS[0],
  ITEMS[1],
  ITEMS[2],
  ITEMS[3],
  ITEMS[4],
  { href: "/settings", label: "Settings", Icon: Settings },
];

export function LeftNavRail({
  accent,
  onAccent,
}: {
  accent: Accent;
  onAccent: (a: Accent) => void;
}) {
  const pathname = usePathname();

  return (
    <>
      <nav className="nav-rail" aria-label="Primary navigation">
        <Link className="nav-brand" href="/" aria-label="Cambotix Vigil home">
          <Image
            className="brand-mark"
            src="/brand/vigil-mark.svg"
            alt=""
            width={34}
            height={34}
            priority
            unoptimized
            aria-hidden="true"
          />
          <span className="nav-brand-copy" style={{ lineHeight: 1.05 }}>
            <span style={{ display: "block", fontSize: 12, fontWeight: 800, letterSpacing: ".12em" }}>CAMBOTIX</span>
            <span style={{ display: "block", marginTop: 4, color: C.gold, fontSize: 8.5, fontWeight: 750, letterSpacing: ".17em" }}>VIGIL · AI TRADING</span>
          </span>
        </Link>

        <div className="nav-section-label">Workspace</div>
        <div className="nav-list">
          {ITEMS.map((it) => (
            <RailItem key={it.href} {...it} active={pathname === it.href} />
          ))}
        </div>

        <div className="nav-footer">
          <div className="accent-picker">
            <span className="accent-picker-label" style={{ color: C.muted, fontSize: 10 }}>Accent</span>
            <span className="accent-swatches">
              {ACCENTS.map((a) => (
                <button
                  className="accent-swatch"
                  key={a}
                  onClick={() => onAccent(a)}
                  title={`${ACCENT_LABELS[a]} accent`}
                  aria-label={`Use ${ACCENT_LABELS[a]} accent`}
                  aria-pressed={accent === a}
                  style={{ background: a, border: accent === a ? "2px solid #fff" : "2px solid transparent" }}
                />
              ))}
            </span>
          </div>
          <RailItem href="/settings" label="Settings" Icon={Settings} active={pathname === "/settings"} />
        </div>
      </nav>

      <nav className="mobile-dock" aria-label="Mobile navigation">
        {MOBILE_ITEMS.map((it) => {
          const active = pathname === it.href;
          return (
            <Link key={it.href} href={it.href} data-active={active} title={it.label} aria-label={it.label}>
              <it.Icon size={19} strokeWidth={active ? 2.2 : 1.8} />
            </Link>
          );
        })}
      </nav>
    </>
  );
}

function RailItem({ href, label, Icon, active }: { href: string; label: string; Icon: LucideIcon; active?: boolean }) {
  return (
    <Link className="nav-item" href={href} data-active={!!active} aria-current={active ? "page" : undefined} title={label}>
      <Icon size={17} strokeWidth={active ? 2.15 : 1.8} aria-hidden="true" />
      <span className="nav-item-label">{label}</span>
    </Link>
  );
}
