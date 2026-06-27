"use client";

import { useState } from "react";
import Link from "next/link";
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
    <>
      <div className="flex items-center gap-1.5" title="Active AI provider — switch anytime">
        <span className="flex items-center gap-1.5 text-xs text-neutral-500">
          <span className={`h-1.5 w-1.5 rounded-full ${dotTone(data?.active)}`} aria-hidden />
          AI
        </span>
        <select
          value={data?.active ?? "mock"}
          disabled={!data || busy}
          onChange={(e) => change(e.target.value as AiProvider)}
          className="rounded-md border border-neutral-800 bg-neutral-900 px-2 py-1 text-sm font-medium text-neutral-100 focus:border-neutral-600 focus:outline-none disabled:opacity-60"
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
        <Link
          href="/settings"
          className="rounded-md border border-neutral-800 p-1.5 text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200"
          title="Settings"
          aria-label="Open settings"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none">
            <path
              d="M12 15a3 3 0 100-6 3 3 0 000 6z"
              stroke="currentColor"
              strokeWidth="1.6"
            />
            <path
              d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 008.6 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H2a2 2 0 110-4h.09A1.65 1.65 0 003.6 8.6a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H8a1.65 1.65 0 001-1.51V2a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V8a1.65 1.65 0 001.51 1H22a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"
              stroke="currentColor"
              strokeWidth="1.2"
            />
          </svg>
        </Link>
      </div>
    </>
  );
}
