/**
 * Broker (MT5) credential management for the web UI. Lets the user set their MT5
 * account login/password/server from Settings → Broker instead of .env. The
 * password is encrypted at rest and never returned. A "test" pushes the creds to
 * the bridge (POST /session/login) and reports pass/fail.
 */
import { Router } from "express";
import { z } from "zod";
import {
  getActiveCredential,
  getCredentialStatus,
  recordTestResult,
  saveCredential,
} from "../execution/broker/credentials";
import { ensureBrokerSession } from "../execution/broker";
import { isEncryptionConfigured } from "../lib/crypto";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";

const router = Router();

const saveSchema = z.object({
  login: z.number().int().positive(),
  password: z.string().min(1).max(256),
  server: z.string().min(1).max(120),
  env: z.enum(["demo", "real"]).default("demo"),
});

// Current credential status — secret-free. Also reports whether ENCRYPTION_KEY
// is set, since saving is impossible without it.
router.get(
  "/brokers/credentials",
  asyncHandler(async (_req, res) => {
    const status = await getCredentialStatus();
    res.json({ ...status, encryptionReady: isEncryptionConfigured() });
  }),
);

// Save (replace) the active MT5 credential. Encrypts the password.
router.put(
  "/brokers/credentials",
  validate(saveSchema, "body"),
  asyncHandler(async (req, res) => {
    if (!isEncryptionConfigured()) {
      res.status(400).json({ error: "ENCRYPTION_KEY is not set on the server — cannot store secrets. Generate with `openssl rand -hex 32`." });
      return;
    }
    const body = req.body as z.infer<typeof saveSchema>;
    await saveCredential(body);
    const status = await getCredentialStatus();
    res.json({ ok: true, ...status });
  }),
);

// Test the active credential by logging the bridge terminal into the account.
// Records the verdict on the row so the UI can show "last test".
router.post(
  "/brokers/credentials/test",
  asyncHandler(async (_req, res) => {
    const cred = await getActiveCredential();
    if (!cred) {
      res.status(400).json({ error: "no broker credentials saved yet" });
      return;
    }
    const result = await ensureBrokerSession();
    await recordTestResult(cred.id, result);
    res.json(result);
  }),
);

export default router;
