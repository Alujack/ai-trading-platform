/**
 * Browser API client.
 *
 * After the BFF cutover (plan 11, Phase 7) every call is SAME-ORIGIN: the
 * browser hits Next.js at `/api/*`, and the `app/api/[...path]` route forwards
 * to the Python backend over a server-only `PYTHON_API_URL`. There is no
 * backend hostname or secret in the client bundle.
 *
 * `NEXT_PUBLIC_API_URL` is still honoured as an escape hatch so a developer can
 * point the dashboard at a backend running outside Next (or roll back to the
 * legacy Express host) without a code change. Leave it unset in normal use.
 */
// `?? ""` is not enough: a blank-but-present value would still be a string, and
// Next inlines NEXT_PUBLIC_* at build time, so a leftover `.env` entry must be
// treated as unset rather than producing "  /api/candles".
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").trim();

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Pull the `{ error }` message every backend failure carries. */
async function errorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error?: string };
    return body?.error ?? "";
  } catch {
    return ""; // body wasn't JSON; fall back to the status code
  }
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await errorDetail(res);
    throw new ApiError(res.status, detail || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function fetcher<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const detail = await errorDetail(res);
    throw new ApiError(res.status, detail || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}
