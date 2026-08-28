/**
 * Server-only configuration for the BFF.
 *
 * `PYTHON_API_URL` is intentionally NOT prefixed `NEXT_PUBLIC_`: the Docker
 * service hostname must never reach the browser bundle, and neither must any
 * backend secret. The module-load guard below turns an accidental client import
 * into a loud failure instead of a silently leaked URL — that boundary is the
 * whole point of the BFF (plan §3: "Next.js has no database, Redis, broker,
 * Telegram or LLM secrets").
 */
if (typeof window !== "undefined") {
  throw new Error(
    "lib/server/backend.ts is server-only — importing it from a client component " +
      "would ship the backend URL to the browser. Call the same-origin /api/* route instead.",
  );
}

/** Requests that stream and must not be subject to a response timeout. */
export const STREAM_PATHS = ["/api/stream"] as const;

/** How long a non-streaming proxied request may take before we 504. */
export const PROXY_TIMEOUT_MS = Number(process.env.PYTHON_API_TIMEOUT_MS ?? "30000");

let warned = false;

/** The Python backend's base URL, resolved server-side only. */
export function backendUrl(): string {
  const url = process.env.PYTHON_API_URL;
  if (url) return url.replace(/\/+$/, "");
  if (!warned) {
    warned = true;
    console.warn(
      "[bff] PYTHON_API_URL is not set — falling back to http://localhost:8000. " +
        "Set it to the backend service URL (e.g. http://backend:8000 in Compose).",
    );
  }
  return "http://localhost:8000";
}
