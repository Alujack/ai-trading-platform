"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { C, mono, tint } from "@/lib/theme";
import type { MarketBias, MarketContext, Symbol, Timeframe } from "@/lib/types";
import { Panel, Pill } from "./ui";

function biasColors(bias: MarketBias): { color: string; bg: string; border: string } {
  if (bias === "Bullish") return { color: C.up, bg: tint(C.up, 0.12), border: tint(C.up, 0.3) };
  if (bias === "Bearish") return { color: C.down, bg: tint(C.down, 0.12), border: tint(C.down, 0.3) };
  return { color: C.text3, bg: tint("#ffffff", 0.06), border: tint("#ffffff", 0.12) };
}

// Color the dot by support (green) / resistance (red) when the level string
// hints at it; otherwise neutral.
function levelDot(text: string): string {
  if (/\b(S\d|support|demand|low)\b/i.test(text)) return C.up;
  if (/\b(R\d|resistance|supply|high)\b/i.test(text)) return C.down;
  return C.muted;
}

export function AiReadPanel({ symbol, timeframe }: { symbol: Symbol; timeframe: Timeframe }) {
  const { data, error, isLoading } = useSWR<MarketContext>(
    `/api/market-context?symbol=${symbol}&timeframe=${timeframe}`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000, keepPreviousData: true },
  );

  const bc = data ? biasColors(data.bias) : null;

  return (
    <Panel
      title="AI Market Read"
      right={
        data && bc ? (
          <Pill color={bc.color} bg={bc.bg} border={bc.border}>
            {data.bias}
          </Pill>
        ) : undefined
      }
      bodyStyle={{ display: "flex", flexDirection: "column", gap: 14 }}
    >
      {isLoading && !data && <p style={{ margin: 0, fontSize: 12.5, color: C.muted }}>Generating AI briefing…</p>}
      {error && <p style={{ margin: 0, fontSize: 12.5, color: C.down }}>AI briefing unavailable.</p>}

      {data && (
        <>
          <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: C.text2 }}>{data.summary}</p>

          {data.keyLevels.length > 0 && (
            <Section title="Key levels">
              {data.keyLevels.map((lv, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontFamily: mono, fontSize: 11.5, color: C.text3 }}>
                  <span style={{ width: 5, height: 5, borderRadius: "50%", background: levelDot(lv), flex: "none" }} />
                  {lv}
                </div>
              ))}
            </Section>
          )}

          {data.risks.length > 0 && (
            <Section title="Risks">
              {data.risks.map((rk, i) => (
                <div key={i} style={{ display: "flex", gap: 7, fontSize: 11.5, lineHeight: 1.45, color: "#c9a55a" }}>
                  <span>•</span>
                  {rk}
                </div>
              ))}
            </Section>
          )}
        </>
      )}
    </Panel>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".07em", color: C.muted, marginBottom: 7 }}>
        {title}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>{children}</div>
    </div>
  );
}
