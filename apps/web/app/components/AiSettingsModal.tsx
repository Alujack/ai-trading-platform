"use client";

import { type ReactNode, useEffect, useState } from "react";
import useSWR from "swr";
import { API_BASE, fetcher } from "@/lib/api";
import type { AiProvider, AiProviderDetail, AiProviderState } from "@/lib/types";

const DEFAULT_MODEL: Record<string, string> = {
  anthropic: "claude-sonnet-4-6",
  gemini: "gemini-2.5-flash",
};

const KEY_HELP: Record<string, string> = {
  anthropic: "Starts with sk-ant-… — from console.anthropic.com",
  gemini: "From aistudio.google.com/apikey",
};

async function call(path: string, method: string, payload?: unknown): Promise<AiProviderState> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(body?.error || `Request failed (${res.status})`);
  }
  return res.json() as Promise<AiProviderState>;
}

export function AiSettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, mutate } = useSWR<AiProviderState>("/api/ai-provider", fetcher, {
    revalidateOnFocus: false,
  });

  useEffect(() => {
    function onEsc(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    if (open) window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm sm:items-center"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-neutral-800 bg-neutral-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-neutral-100">AI Providers</h2>
            <p className="mt-0.5 text-xs text-neutral-500">
              Paste a key, test it, then switch. Keys are stored locally, never committed.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-neutral-500 hover:bg-neutral-900 hover:text-neutral-300"
            aria-label="Close"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="max-h-[70vh] space-y-3 overflow-y-auto p-5">
          {!data && <p className="text-sm text-neutral-500">Loading providers…</p>}
          {data?.providers.map((p) => (
            <ProviderRow
              key={p.name}
              detail={p}
              onState={(s) => mutate(s, false)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ProviderRow({
  detail,
  onState,
}: {
  detail: AiProviderDetail;
  onState: (s: AiProviderState) => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(detail.model ?? DEFAULT_MODEL[detail.name] ?? "");
  const [busy, setBusy] = useState<"" | "save" | "test" | "use" | "remove">("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function run(kind: typeof busy, fn: () => Promise<unknown>) {
    setBusy(kind);
    setMsg(null);
    try {
      await fn();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  }

  const save = () =>
    run("save", async () => {
      const s = await call("/api/ai-provider/key", "PUT", {
        provider: detail.name,
        apiKey: apiKey.trim(),
        model: model.trim() || undefined,
      });
      onState(s);
      setApiKey("");
      setMsg({ ok: true, text: "Key saved." });
    });

  const test = () =>
    run("test", async () => {
      const res = await fetch(`${API_BASE}/api/ai-provider/test`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider: detail.name }),
      });
      const body = (await res.json()) as { ok?: boolean; detail?: string; error?: string };
      setMsg({ ok: !!body.ok, text: body.detail || body.error || (body.ok ? "OK" : "Failed") });
    });

  const use = () =>
    run("use", async () => {
      onState(await call("/api/ai-provider", "PUT", { provider: detail.name }));
    });

  const remove = () =>
    run("remove", async () => {
      onState(await call("/api/ai-provider/key", "DELETE", { provider: detail.name }));
      setMsg({ ok: true, text: "Key removed." });
    });

  return (
    <div
      className={`rounded-lg border p-4 ${
        detail.active ? "border-emerald-600/50 bg-emerald-500/[0.04]" : "border-neutral-800 bg-neutral-900/40"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-neutral-100">{detail.label}</span>
        <StatusBadge detail={detail} />
        {detail.keyHint && (
          <span className="font-mono text-[11px] text-neutral-600">
            key {detail.keyHint} · {detail.keySource}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {detail.configured && !detail.active && (
            <button
              onClick={use}
              disabled={busy !== ""}
              className="rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {busy === "use" ? "…" : "Use"}
            </button>
          )}
          {detail.active && (
            <span className="rounded-md bg-emerald-500/15 px-2.5 py-1 text-xs font-medium text-emerald-300">
              Active
            </span>
          )}
        </div>
      </div>

      {detail.needsKey ? (
        <div className="mt-3 space-y-2">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={detail.hasKey ? "Replace key…" : "Paste API key"}
              className="flex-1 rounded-md border border-neutral-800 bg-neutral-950 px-3 py-1.5 font-mono text-xs text-neutral-100 placeholder:text-neutral-600 focus:border-neutral-600 focus:outline-none"
            />
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="model"
              className="w-full rounded-md border border-neutral-800 bg-neutral-950 px-3 py-1.5 font-mono text-xs text-neutral-300 placeholder:text-neutral-600 focus:border-neutral-600 focus:outline-none sm:w-44"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={save}
              disabled={busy !== "" || !apiKey.trim()}
              className="rounded-md border border-neutral-700 bg-neutral-800 px-3 py-1.5 text-xs font-medium text-neutral-100 hover:bg-neutral-700 disabled:opacity-40"
            >
              {busy === "save" ? "Saving…" : "Save key"}
            </button>
            <button
              onClick={test}
              disabled={busy !== "" || !detail.hasKey}
              className="rounded-md border border-neutral-800 px-3 py-1.5 text-xs font-medium text-neutral-300 hover:bg-neutral-900 disabled:opacity-40"
            >
              {busy === "test" ? "Testing…" : "Test"}
            </button>
            {detail.keySource === "ui" && (
              <button
                onClick={remove}
                disabled={busy !== ""}
                className="rounded-md px-2 py-1.5 text-xs text-rose-400/80 hover:text-rose-300 disabled:opacity-40"
              >
                Remove
              </button>
            )}
            <span className="font-mono text-[11px] text-neutral-600">{KEY_HELP[detail.name]}</span>
          </div>
        </div>
      ) : (
        <p className="mt-2 text-xs text-neutral-500">
          Built-in placeholder responses — no key needed. Useful when no model is configured.
        </p>
      )}

      {msg && (
        <p className={`mt-2 text-xs ${msg.ok ? "text-emerald-400" : "text-rose-400"}`}>{msg.text}</p>
      )}
    </div>
  );
}

function StatusBadge({ detail }: { detail: AiProviderDetail }) {
  if (!detail.needsKey) {
    return <Badge tone="amber">mock</Badge>;
  }
  if (detail.configured) {
    return <Badge tone="emerald">ready</Badge>;
  }
  return <Badge tone="neutral">no key</Badge>;
}

function Badge({ tone, children }: { tone: "emerald" | "amber" | "neutral"; children: ReactNode }) {
  const tones = {
    emerald: "bg-emerald-500/15 text-emerald-300",
    amber: "bg-amber-500/15 text-amber-300",
    neutral: "bg-neutral-800 text-neutral-400",
  } as const;
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${tones[tone]}`}>
      {children}
    </span>
  );
}
