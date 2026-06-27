/**
 * Symmetric encryption for secrets stored at rest (e.g. broker passwords).
 * AES-256-GCM with a per-value random IV; output is a single self-describing
 * string "iv:authTag:ciphertext" (all hex). GCM's auth tag detects tampering.
 *
 * The key comes from ENCRYPTION_KEY — 32 bytes as 64 hex chars
 * (generate: `openssl rand -hex 32`). It MUST be stable across restarts, or
 * previously-encrypted values become undecryptable. We refuse to run without it
 * rather than silently use an ephemeral key that would lose data on restart.
 */
import crypto from "node:crypto";

const ALGO = "aes-256-gcm";

function key(): Buffer {
  const hex = process.env.ENCRYPTION_KEY;
  if (!hex) {
    throw new Error(
      "ENCRYPTION_KEY is not set — required to encrypt/decrypt broker secrets. Generate one with `openssl rand -hex 32`.",
    );
  }
  const buf = Buffer.from(hex, "hex");
  if (buf.length !== 32) {
    throw new Error("ENCRYPTION_KEY must be 32 bytes (64 hex chars). Generate with `openssl rand -hex 32`.");
  }
  return buf;
}

/** Encrypt plaintext into a portable "iv:authTag:ciphertext" hex string. */
export function encrypt(plaintext: string): string {
  const iv = crypto.randomBytes(12); // 96-bit nonce, recommended for GCM
  const cipher = crypto.createCipheriv(ALGO, key(), iv);
  const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `${iv.toString("hex")}:${tag.toString("hex")}:${ct.toString("hex")}`;
}

/** Reverse of {@link encrypt}. Throws if the key is wrong or the blob was tampered with. */
export function decrypt(blob: string): string {
  const [ivHex, tagHex, ctHex] = blob.split(":");
  if (!ivHex || !tagHex || !ctHex) {
    throw new Error("malformed encrypted blob (expected iv:authTag:ciphertext)");
  }
  const decipher = crypto.createDecipheriv(ALGO, key(), Buffer.from(ivHex, "hex"));
  decipher.setAuthTag(Buffer.from(tagHex, "hex"));
  return decipher.update(ctHex, "hex", "utf8") + decipher.final("utf8");
}

/** True when ENCRYPTION_KEY is present and well-formed (for status/health checks). */
export function isEncryptionConfigured(): boolean {
  try {
    key();
    return true;
  } catch {
    return false;
  }
}
