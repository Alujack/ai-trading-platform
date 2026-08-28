/**
 * Same-origin BFF proxy (plan 11, Phase 7).
 *
 * The browser talks only to this Next.js origin; every `/api/*` request is
 * forwarded to the Python backend over a server-only URL. This is deliberately
 * THIN — it must never grow trading logic:
 *
 *   - No database, Redis, broker, Telegram or LLM access lives here.
 *   - No risk decisions. FastAPI owns the single authoritative risk engine.
 *   - It forwards method, path, query, body and an allowlisted set of headers,
 *     and passes the upstream status and body straight back.
 *
 * SSE (`/api/stream`) is streamed through unbuffered so the dashboard's
 * EventSource keeps working.
 */
import { NextRequest } from "next/server";
import { backendUrl, PROXY_TIMEOUT_MS, STREAM_PATHS } from "@/lib/server/backend";

// Node runtime (not edge): we need undici streaming and a server-only env var.
export const runtime = "nodejs";
// Never cache a proxied trading response — positions and signals are live data.
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

/** Request headers worth forwarding. Everything else (host, cookies for other
 *  origins, connection controls) is dropped so the upstream sees a clean call. */
const FORWARD_REQUEST_HEADERS = [
  "content-type",
  "accept",
  "accept-language",
  "x-telegram-bot-api-secret-token",
  "x-request-id",
];

/** Response headers worth returning. Hop-by-hop headers must not be copied. */
const FORWARD_RESPONSE_HEADERS = [
  "content-type",
  "cache-control",
  "x-accel-buffering",
  "x-request-id",
];

function pickRequestHeaders(req: NextRequest): Headers {
  const out = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (value) out.set(name, value);
  }
  return out;
}

function pickResponseHeaders(upstream: Response): Headers {
  const out = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return out;
}

function isStream(pathname: string): boolean {
  return STREAM_PATHS.some((p) => pathname === p);
}

async function proxy(req: NextRequest): Promise<Response> {
  const { pathname, search } = new URL(req.url);
  const target = `${backendUrl()}${pathname}${search}`;
  const streaming = isStream(pathname);

  // A streamed SSE response must never be aborted by a request timeout.
  const controller = new AbortController();
  const timer = streaming
    ? null
    : setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers: pickRequestHeaders(req),
      // GET/HEAD carry no body; everything else streams the original body through.
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.text(),
      signal: controller.signal,
      cache: "no-store",
      redirect: "manual",
    });
  } catch (err) {
    const aborted = err instanceof Error && err.name === "AbortError";
    const detail = err instanceof Error ? err.message : String(err);
    return Response.json(
      {
        error: aborted
          ? `Backend timed out after ${PROXY_TIMEOUT_MS}ms`
          : `Backend unreachable: ${detail}`,
      },
      { status: aborted ? 504 : 502 },
    );
  } finally {
    if (timer) clearTimeout(timer);
  }

  const headers = pickResponseHeaders(upstream);

  if (streaming) {
    // Pass the byte stream straight through, with the no-buffering hints intact
    // so neither Next nor a proxy in front of it batches the events.
    headers.set("content-type", upstream.headers.get("content-type") ?? "text/event-stream");
    headers.set("cache-control", "no-cache, no-transform");
    headers.set("x-accel-buffering", "no");
    headers.set("connection", "keep-alive");
    return new Response(upstream.body, { status: upstream.status, headers });
  }

  return new Response(upstream.body, { status: upstream.status, headers });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
