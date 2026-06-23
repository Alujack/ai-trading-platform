import { Router } from "express";
import { z } from "zod";
import { HttpError } from "../errors/httpError";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";

const router = Router();

const AI_SERVICE_URL = process.env.AI_SERVICE_URL ?? "http://localhost:8000";

const providerName = z.string().min(1).max(40);

const setProviderSchema = z.object({ provider: providerName });
const setKeySchema = z.object({
  provider: providerName,
  apiKey: z.string().min(1).max(400),
  model: z.string().max(120).optional(),
});
const providerOnlySchema = z.object({ provider: providerName });

// Proxy any call to the AI service and pass its JSON (and status) through.
async function aiFetch<T>(path: string, init?: RequestInit): Promise<{ status: number; body: T }> {
  let res: Response;
  try {
    res = await fetch(`${AI_SERVICE_URL}${path}`, init);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new HttpError(503, `AI service unreachable: ${msg}`);
  }
  const text = await res.text();
  let body: unknown = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { detail: text.slice(0, 200) };
  }
  if (!res.ok) {
    const detail =
      (body as { detail?: string })?.detail ?? `AI service ${res.status}`;
    throw new HttpError(res.status === 400 ? 400 : 502, detail);
  }
  return { status: res.status, body: body as T };
}

const json = (init?: { method?: string; payload?: unknown }): RequestInit => ({
  method: init?.method ?? "GET",
  headers: { "content-type": "application/json" },
  body: init?.payload ? JSON.stringify(init.payload) : undefined,
});

// Current provider + per-provider config status (depends on configured keys).
router.get(
  "/ai-provider",
  asyncHandler(async (_req, res) => {
    const { body } = await aiFetch("/provider");
    res.json(body);
  }),
);

// Switch the active provider at runtime.
router.put(
  "/ai-provider",
  validate(setProviderSchema, "body"),
  asyncHandler(async (req, res) => {
    const { body } = await aiFetch("/provider", json({ method: "POST", payload: req.body }));
    res.json(body);
  }),
);

// Save an API key (and optional model) for a provider — pasted from the UI.
router.put(
  "/ai-provider/key",
  validate(setKeySchema, "body"),
  asyncHandler(async (req, res) => {
    const { body } = await aiFetch("/provider/key", json({ method: "PUT", payload: req.body }));
    res.json(body);
  }),
);

// Remove a UI-set key for a provider.
router.delete(
  "/ai-provider/key",
  validate(providerOnlySchema, "body"),
  asyncHandler(async (req, res) => {
    const { body } = await aiFetch("/provider/key", json({ method: "DELETE", payload: req.body }));
    res.json(body);
  }),
);

// Verify a provider's key with one tiny live call.
router.post(
  "/ai-provider/test",
  validate(providerOnlySchema, "body"),
  asyncHandler(async (req, res) => {
    const { body } = await aiFetch("/provider/test", json({ method: "POST", payload: req.body }));
    res.json(body);
  }),
);

export default router;
