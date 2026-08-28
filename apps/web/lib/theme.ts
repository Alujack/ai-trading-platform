// Palette ported from the Trading Dashboard design. Single source of truth so
// every panel reads the same colors.
export const C = {
  bg: "#080a0d",
  rail: "#0a0d11",
  panel: "#11151a",
  panelDeep: "#090c10",
  line: "rgba(255,255,255,0.065)",
  line2: "rgba(255,255,255,0.09)",
  hair: "rgba(255,255,255,0.045)",
  fill: "rgba(255,255,255,0.035)",
  text: "#f1f3f5",
  text2: "#cbd0d6",
  text3: "#9ca4ae",
  muted: "#7e8792",
  muted2: "#59616c",
  up: "#56c596",
  down: "#f06b5d",
  warn: "#e7b952",
  blue: "#7197e8",
  gold: "#e9ad3d",
} as const;

export const ACCENTS = ["#f0b429", "#5b8cf0", "#4fb286"] as const;
export type Accent = (typeof ACCENTS)[number];

export const ACCENT_LABELS: Record<Accent, string> = {
  "#f0b429": "Gold",
  "#5b8cf0": "Blue",
  "#4fb286": "Green",
};

// rgba helper for tinted backgrounds/borders.
export function tint(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

export const mono = "var(--font-mono), 'JetBrains Mono', ui-monospace, monospace";
