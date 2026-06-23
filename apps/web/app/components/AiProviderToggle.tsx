"use client";

import { useState } from "react";
import useSWR from "swr";
import { API_BASE, fetcher } from "@/lib/api";
import { AI_PROVIDERS, type AiProvider, type AiProviderState } from "@/lib/types";

const LABELS: Record<AiProvider, string> = {
  mock: "Mock",
  anthropic: "Claude",
  gemini: "Gemini",
};

// Mock is a placeholder; Claude/Gemini are live models.
function dotTone(active: AiProvider | undefined): string {
  if (active === "mock") return "bg-amber-400";
  if (active) return "bg-emerald-400";
  return "bg-neutral-600";
}

export function AiProviderToggle() {
  const { data, mutate, error } = useSWR<AiProviderState>("/api/ai-provider", fetcher, {
    revalidateOnFocus: false,
  });
  const [busy, setBusy] = useState(false);

  async function change(provider: AiProvider) {
    if (!data || provider === data.active) return;
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/ai-provider`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider }),
      });
      if (res.ok) {
        mutate((await res.json()) as AiProviderState, false);
      }
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return <span className="text-xs text-rose-400/80">AI service offline</span>;
  }

  return (
    <label className="flex items-center gap-2 text-xs" title="Active AI provider — switch anytime">
      <span className="flex items-center gap-1.5 text-neutral-500">
        <span className={`h-1.5 w-1.5 rounded-full ${dotTone(data?.active)}`} aria-hidden />
        AI
      </span>
      <select
        value={data?.active ?? "mock"}
        disabled={!data || busy}
        onChange={(e) => change(e.target.value as AiProvider)}
        className="rounded border border-neutral-800 bg-neutral-900 px-2 py-1 text-sm font-medium text-neutral-100 focus:border-neutral-600 focus:outline-none disabled:opacity-60"
      >
        {AI_PROVIDERS.map((p) => {
          const unavailable = data ? !data.available.includes(p) : p !== "mock";
          return (
            <option key={p} value={p} disabled={unavailable}>
              {LABELS[p]}
              {unavailable ? " (no key)" : ""}
            </option>
          );
        })}
      </select>
    </label>
  );
}
