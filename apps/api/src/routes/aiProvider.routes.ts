import { Router } from "express";
import { z } from "zod";
import { HttpError } from "../errors/httpError";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";

const router = Router();

const AI_SERVICE_URL = process.env.AI_SERVICE_URL ?? "http://localhost:8000";

const setProviderSchema = z.object({
  provider: z.enum(["mock", "anthropic", "gemini"]),
});

interface ProviderState {
  active: string;
  available: string[];
}

async function aiFetch(path: string, init?: RequestInit): Promise<ProviderState> {
  let res: Response;
  try {
    res = await fetch(`${AI_SERVICE_URL}${path}`, init);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new HttpError(503, `AI service unreachable: ${msg}`);
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new HttpError(res.status === 400 ? 400 : 502, detail.slice(0, 200) || `AI service ${res.status}`);
  }
  return (await res.json()) as ProviderState;
}

// Current AI provider + which ones are selectable (depends on configured keys).
router.get(
  "/ai-provider",
  asyncHandler(async (_req, res) => {
    res.json(await aiFetch("/provider"));
  }),
);

// Switch the active AI provider at runtime.
router.put(
  "/ai-provider",
  validate(setProviderSchema, "body"),
  asyncHandler(async (req, res) => {
    res.json(
      await aiFetch("/provider", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(req.body),
      }),
    );
  }),
);

export default router;
