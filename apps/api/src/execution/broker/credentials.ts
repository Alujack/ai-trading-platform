/**
 * Broker (MT5) credential store. Persists UI-entered MT5 account creds with the
 * password encrypted at rest, and hands the decrypted creds to the broker layer
 * only when pushing a session to the bridge. Single-account v1: exactly one row
 * is `isActive`.
 */
import { decrypt, encrypt } from "../../lib/crypto";
import { prisma } from "../../lib/prisma";

export type BrokerEnv = "demo" | "real";

export interface BrokerCredentialInput {
  login: number;
  password: string;
  server: string;
  env: BrokerEnv;
}

/** Decrypted credential — keep in memory only, never log or return to a client. */
export interface ActiveCredential {
  id: string;
  login: number;
  password: string;
  server: string;
  env: BrokerEnv;
}

/** Safe, secret-free view for the UI / status endpoints. */
export interface CredentialStatus {
  configured: boolean;
  login: number | null;
  server: string | null;
  env: BrokerEnv | null;
  hasPassword: boolean;
  lastTest: unknown | null;
  updatedAt: string | null;
}

/** Save (replace) the active credential. Encrypts the password; deactivates any prior row. */
export async function saveCredential(input: BrokerCredentialInput): Promise<void> {
  const passwordEnc = encrypt(input.password);
  await prisma.$transaction([
    prisma.brokerCredential.updateMany({ where: { isActive: true }, data: { isActive: false } }),
    prisma.brokerCredential.create({
      data: {
        broker: "exness",
        login: input.login,
        passwordEnc,
        server: input.server,
        env: input.env,
        isActive: true,
      },
    }),
  ]);
}

/** The active credential with its password decrypted, or null if none is configured. */
export async function getActiveCredential(): Promise<ActiveCredential | null> {
  const row = await prisma.brokerCredential.findFirst({
    where: { isActive: true },
    orderBy: { updatedAt: "desc" },
  });
  if (!row) return null;
  return {
    id: row.id,
    login: row.login,
    password: decrypt(row.passwordEnc),
    server: row.server,
    env: row.env === "real" ? "real" : "demo",
  };
}

/** Secret-free status for the settings UI. */
export async function getCredentialStatus(): Promise<CredentialStatus> {
  const row = await prisma.brokerCredential.findFirst({
    where: { isActive: true },
    orderBy: { updatedAt: "desc" },
  });
  if (!row) {
    return { configured: false, login: null, server: null, env: null, hasPassword: false, lastTest: null, updatedAt: null };
  }
  return {
    configured: true,
    login: row.login,
    server: row.server,
    env: row.env === "real" ? "real" : "demo",
    hasPassword: Boolean(row.passwordEnc),
    lastTest: row.lastTest ?? null,
    updatedAt: row.updatedAt.toISOString(),
  };
}

/** Record the outcome of a /session/login test against a credential row. */
export async function recordTestResult(id: string, result: { ok: boolean; detail: string }): Promise<void> {
  await prisma.brokerCredential.update({
    where: { id },
    data: { lastTest: { ...result, testedAt: new Date().toISOString() } },
  });
}
