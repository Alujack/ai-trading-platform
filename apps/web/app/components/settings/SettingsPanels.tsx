"use client";

import { type ReactNode, useEffect, useState } from "react";
import useSWR from "swr";
import { API_BASE, fetcher } from "@/lib/api";
import type { AiProvider, AiProviderDetail, AiProviderState, TelegramStatus } from "@/lib/types";

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

/**
 * The full settings UI, rendered inline on the /settings page (it used to be a
 * cramped popup modal). One <SettingsGroup> per concern so the page can breathe.
 */
export function SettingsPanels() {
  const { data, mutate } = useSWR<AiProviderState>("/api/ai-provider", fetcher, {
    revalidateOnFocus: false,
  });

  return (
    <div className="flex w-full flex-col gap-5">
      <SettingsGroup
        title="AI providers"
        subtitle="Paste a key, test it, then switch. Keys are stored locally, never committed."
      >
        <div className="space-y-3">
          {!data && <p className="text-sm text-neutral-500">Loading providers…</p>}
          {data?.providers.map((p) => (
            <ProviderRow key={p.name} detail={p} onState={(s) => mutate(s, false)} />
          ))}
        </div>
      </SettingsGroup>

      <SettingsGroup title="Telegram alerts" subtitle="Confirm trades from your phone.">
        <TelegramSection />
      </SettingsGroup>

      <SettingsGroup title="Broker (MT5)" subtitle="Your trading account login — demo first.">
        <BrokerSection />
      </SettingsGroup>
    </div>
  );
}

function SettingsGroup({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-neutral-800 bg-neutral-950/60 p-5">
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-neutral-100">{title}</h2>
        <p className="mt-0.5 text-xs text-neutral-500">{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

function randomHex(bytes: number): string {
  const a = new Uint8Array(bytes);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

function TelegramSection() {
  const { data, mutate } = useSWR<TelegramStatus>("/api/telegram", fetcher, { revalidateOnFocus: false });
  const [token, setToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [userIds, setUserIds] = useState("");
  const [secret, setSecret] = useState("");
  const [publicUrl, setPublicUrl] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "test" | "remove" | "register">("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [seeded, setSeeded] = useState(false);

  // Seed visible (non-secret) fields once from the server status.
  useEffect(() => {
    if (data && !seeded) {
      setChatId(data.chatId ?? "");
      setUserIds(data.allowedUserIds.join(","));
      setSeeded(true);
    }
  }, [data, seeded]);

  async function run(kind: typeof busy, fn: () => Promise<void>) {
    setBusy(kind);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  }

  const save = () =>
    run("save", async () => {
      const body: Record<string, string> = { chatId, allowedUserIds: userIds };
      if (token.trim()) body.botToken = token.trim();
      if (secret.trim()) body.webhookSecret = secret.trim();
      const res = await fetch(`${API_BASE}/api/telegram`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(((await res.json().catch(() => ({}))) as { error?: string }).error || "Save failed");
      await mutate();
      setToken("");
      setSecret("");
      setMsg({ ok: true, text: "Saved." });
    });

  const test = () =>
    run("test", async () => {
      const res = await fetch(`${API_BASE}/api/telegram/test`, { method: "POST" });
      const b = (await res.json()) as { ok?: boolean; detail?: string; error?: string };
      setMsg({ ok: !!b.ok, text: b.detail || b.error || (b.ok ? "Sent." : "Failed") });
    });

  const register = () =>
    run("register", async () => {
      const res = await fetch(`${API_BASE}/api/telegram/webhook`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ publicUrl: publicUrl.trim() }),
      });
      const b = (await res.json()) as { ok?: boolean; url?: string; error?: string };
      if (!b.ok) throw new Error(b.error || "Register failed");
      await mutate();
      setMsg({ ok: true, text: `Webhook registered → ${b.url}` });
    });

  const remove = () =>
    run("remove", async () => {
      await fetch(`${API_BASE}/api/telegram`, { method: "DELETE" });
      await mutate();
      setToken("");
      setSecret("");
      setSeeded(false);
      setMsg({ ok: true, text: "Cleared UI-set credentials (env values, if any, still apply)." });
    });

  const inputCls =
    "w-full rounded-md border border-neutral-800 bg-neutral-950 px-3 py-1.5 font-mono text-xs text-neutral-100 placeholder:text-neutral-600 focus:border-neutral-600 focus:outline-none";
  const labelCls = "mb-1 block text-[11px] font-medium uppercase tracking-wide text-neutral-500";

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {data?.configured ? (
          <Badge tone="emerald">connected</Badge>
        ) : (
          <Badge tone="neutral">not set</Badge>
        )}
        {data?.tokenHint && (
          <span className="font-mono text-[11px] text-neutral-600">
            token {data.tokenHint} · {data.sources.botToken}
          </span>
        )}
        {data?.webhook?.url && (
          <span className="font-mono text-[11px] text-emerald-500/70">webhook live</span>
        )}
      </div>

      <div className="space-y-3">
        <div>
          <label className={labelCls}>Bot token</label>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={data?.hasToken ? "Replace token…" : "Paste token from @BotFather"}
            className={inputCls}
          />
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label className={labelCls}>Chat ID</label>
            <input value={chatId} onChange={(e) => setChatId(e.target.value)} placeholder="e.g. 123456789" className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>Allowed user IDs</label>
            <input value={userIds} onChange={(e) => setUserIds(e.target.value)} placeholder="comma-separated" className={inputCls} />
          </div>
        </div>
        <div>
          <label className={labelCls}>
            Webhook secret {data?.hasWebhookSecret && <span className="text-emerald-500/70">· set</span>}
          </label>
          <div className="flex gap-2">
            <input
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder={data?.hasWebhookSecret ? "Replace secret…" : "Auto-generate →"}
              className={inputCls}
            />
            <button
              type="button"
              onClick={() => setSecret(randomHex(24))}
              className="shrink-0 rounded-md border border-neutral-700 px-2.5 py-1.5 text-xs text-neutral-300 hover:bg-neutral-800"
            >
              Generate
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={save}
            disabled={busy !== ""}
            className="rounded-md border border-neutral-700 bg-neutral-800 px-3 py-1.5 text-xs font-medium text-neutral-100 hover:bg-neutral-700 disabled:opacity-40"
          >
            {busy === "save" ? "Saving…" : "Save"}
          </button>
          <button
            onClick={test}
            disabled={busy !== "" || !data?.configured}
            className="rounded-md border border-neutral-800 px-3 py-1.5 text-xs font-medium text-neutral-300 hover:bg-neutral-900 disabled:opacity-40"
          >
            {busy === "test" ? "Sending…" : "Send test"}
          </button>
          {data && (data.sources.botToken === "ui" || data.sources.chatId === "ui") && (
            <button
              onClick={remove}
              disabled={busy !== ""}
              className="rounded-md px-2 py-1.5 text-xs text-rose-400/80 hover:text-rose-300 disabled:opacity-40"
            >
              Clear
            </button>
          )}
        </div>

        <div className="border-t border-neutral-800 pt-3">
          <label className={labelCls}>Register webhook (public URL)</label>
          <div className="flex gap-2">
            <input
              value={publicUrl}
              onChange={(e) => setPublicUrl(e.target.value)}
              placeholder="https://abc.trycloudflare.com"
              className={inputCls}
            />
            <button
              onClick={register}
              disabled={busy !== "" || !publicUrl.trim() || !data?.hasToken}
              className="shrink-0 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
            >
              {busy === "register" ? "…" : "Register"}
            </button>
          </div>
          <p className="mt-1.5 text-[11px] text-neutral-600">
            Expose the API publicly, then register. Local tunnel:{" "}
            <span className="font-mono text-neutral-500">cloudflared tunnel --url http://localhost:4100</span>
          </p>
          {data?.webhook?.lastError && (
            <p className="mt-1 text-[11px] text-rose-400/80">last error: {data.webhook.lastError}</p>
          )}
        </div>

        {msg && <p className={`text-xs ${msg.ok ? "text-emerald-400" : "text-rose-400"}`}>{msg.text}</p>}
      </div>
    </div>
  );
}

interface BrokerStatus {
  configured: boolean;
  login: number | null;
  server: string | null;
  env: "demo" | "real" | null;
  hasPassword: boolean;
  lastTest: { ok: boolean; detail: string; testedAt: string } | null;
  updatedAt: string | null;
  encryptionReady: boolean;
}

function BrokerSection() {
  const { data, mutate } = useSWR<BrokerStatus>("/api/brokers/credentials", fetcher, {
    revalidateOnFocus: false,
  });
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [server, setServer] = useState("");
  const [env, setEnv] = useState<"demo" | "real">("demo");
  const [busy, setBusy] = useState<"" | "save" | "test">("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [seeded, setSeeded] = useState(false);

  // Seed visible (non-secret) fields once from server status.
  useEffect(() => {
    if (data && !seeded) {
      if (data.login != null) setLogin(String(data.login));
      if (data.server) setServer(data.server);
      if (data.env) setEnv(data.env);
      setSeeded(true);
    }
  }, [data, seeded]);

  async function run(kind: typeof busy, fn: () => Promise<void>) {
    setBusy(kind);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  }

  const save = () =>
    run("save", async () => {
      const res = await fetch(`${API_BASE}/api/brokers/credentials`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ login: Number(login), password, server: server.trim(), env }),
      });
      if (!res.ok)
        throw new Error(((await res.json().catch(() => ({}))) as { error?: string }).error || "Save failed");
      await mutate();
      setPassword("");
      setMsg({ ok: true, text: "Saved. Run a test to log the terminal in." });
    });

  const test = () =>
    run("test", async () => {
      const res = await fetch(`${API_BASE}/api/brokers/credentials/test`, { method: "POST" });
      const b = (await res.json()) as { ok?: boolean; detail?: string; error?: string };
      setMsg({ ok: !!b.ok, text: b.detail || b.error || (b.ok ? "Logged in." : "Failed") });
      await mutate();
    });

  const inputCls =
    "w-full rounded-md border border-neutral-800 bg-neutral-950 px-3 py-1.5 font-mono text-xs text-neutral-100 placeholder:text-neutral-600 focus:border-neutral-600 focus:outline-none";
  const labelCls = "mb-1 block text-[11px] font-medium uppercase tracking-wide text-neutral-500";
  const canSave = busy === "" && login.trim() !== "" && password !== "" && server.trim() !== "" && !!data?.encryptionReady;

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {data?.configured ? <Badge tone="emerald">saved</Badge> : <Badge tone="neutral">not set</Badge>}
        {data?.lastTest && (
          <Badge tone={data.lastTest.ok ? "emerald" : "amber"}>
            {data.lastTest.ok ? "login ok" : "login failed"}
          </Badge>
        )}
        <Badge tone={data?.env === "real" ? "amber" : "neutral"}>{data?.env ?? env}</Badge>
      </div>

      {data && !data.encryptionReady && (
        <p className="mb-3 text-[11px] text-amber-400/90">
          Set <span className="font-mono">ENCRYPTION_KEY</span> on the server before saving secrets
          (<span className="font-mono">openssl rand -hex 32</span>).
        </p>
      )}

      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label className={labelCls}>Account login</label>
            <input
              value={login}
              onChange={(e) => setLogin(e.target.value.replace(/[^0-9]/g, ""))}
              inputMode="numeric"
              placeholder="e.g. 12345678"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>Environment</label>
            <select value={env} onChange={(e) => setEnv(e.target.value as "demo" | "real")} className={inputCls}>
              <option value="demo">demo</option>
              <option value="real">real</option>
            </select>
          </div>
        </div>
        <div>
          <label className={labelCls}>Server</label>
          <input
            value={server}
            onChange={(e) => setServer(e.target.value)}
            placeholder="e.g. Exness-MT5Trial7"
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Password {data?.hasPassword && <span className="text-emerald-500/70">· set</span>}</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={data?.hasPassword ? "Replace password…" : "MT5 account password"}
            className={inputCls}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={save}
            disabled={!canSave}
            className="rounded-md border border-neutral-700 bg-neutral-800 px-3 py-1.5 text-xs font-medium text-neutral-100 hover:bg-neutral-700 disabled:opacity-40"
          >
            {busy === "save" ? "Saving…" : "Save"}
          </button>
          <button
            onClick={test}
            disabled={busy !== "" || !data?.configured}
            className="rounded-md border border-neutral-800 px-3 py-1.5 text-xs font-medium text-neutral-300 hover:bg-neutral-900 disabled:opacity-40"
          >
            {busy === "test" ? "Testing…" : "Test connection"}
          </button>
        </div>

        <p className="text-[11px] text-neutral-600">
          Pushes credentials to the MT5 bridge to log the terminal in. Demo first — real requires the
          promotion gate.
        </p>
        {msg && <p className={`text-xs ${msg.ok ? "text-emerald-400" : "text-rose-400"}`}>{msg.text}</p>}
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
