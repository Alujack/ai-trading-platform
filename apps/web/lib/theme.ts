// Palette ported from the Trading Dashboard design. Single source of truth so
// every panel reads the same colors.
export const C = {
  bg: "#0a0b0e",
  rail: "#0c0d11",
  panel: "#13151a",
  panelDeep: "#0b0c0f",
  line: "rgba(255,255,255,0.06)",
  line2: "rgba(255,255,255,0.07)",
  hair: "rgba(255,255,255,0.04)",
  fill: "rgba(255,255,255,0.025)",
  text: "#e8eaed",
  text2: "#c4c9d0",
  text3: "#9aa0a8",
  muted: "#7d848e",
  muted2: "#5f6670",
  up: "#4fb286",
  down: "#e5604d",
  warn: "#e5b341",
  blue: "#6b86c4",
  gold: "#f0b429",
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
