"""Symmetric encryption for secrets at rest — wire-compatible with `lib/crypto.ts`.

AES-256-GCM with a 96-bit random IV. The stored blob is the same
self-describing `"iv:authTag:ciphertext"` hex string the Express API wrote, so
credentials saved before the migration keep decrypting afterwards (Phase 5 exit
gate). The key comes from `ENCRYPTION_KEY` (32 bytes as 64 hex chars) and must
stay stable across restarts, so we refuse to run with an ephemeral one.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_IV_BYTES = 12
_TAG_BYTES = 16
KEY_HELP = "Generate one with `openssl rand -hex 32`."


class EncryptionNotConfigured(RuntimeError):
    """`ENCRYPTION_KEY` is absent or malformed."""


def _key() -> bytes:
    raw = os.environ.get("ENCRYPTION_KEY")
    if not raw:
        raise EncryptionNotConfigured(
            "ENCRYPTION_KEY is not set — required to encrypt/decrypt broker secrets. " + KEY_HELP
        )
    try:
        key = bytes.fromhex(raw.strip())
    except ValueError as exc:
        raise EncryptionNotConfigured(
            "ENCRYPTION_KEY must be hex-encoded (64 hex chars). " + KEY_HELP
        ) from exc
    if len(key) != 32:
        raise EncryptionNotConfigured(
            "ENCRYPTION_KEY must be 32 bytes (64 hex chars). " + KEY_HELP
        )
    return key


def encrypt(plaintext: str) -> str:
    """Encrypt into the portable `iv:authTag:ciphertext` hex string."""
    iv = os.urandom(_IV_BYTES)
    sealed = AESGCM(_key()).encrypt(iv, plaintext.encode("utf-8"), None)
    # cryptography appends the GCM tag; Node keeps it in a separate field.
    ciphertext, tag = sealed[:-_TAG_BYTES], sealed[-_TAG_BYTES:]
    return f"{iv.hex()}:{tag.hex()}:{ciphertext.hex()}"


def decrypt(blob: str) -> str:
    """Reverse of :func:`encrypt`. Raises if the key is wrong or the blob was altered."""
    parts = blob.split(":")
    if len(parts) != 3 or not all(parts):
        raise ValueError("malformed encrypted blob (expected iv:authTag:ciphertext)")
    iv_hex, tag_hex, ct_hex = parts
    try:
        iv = bytes.fromhex(iv_hex)
        tag = bytes.fromhex(tag_hex)
        ciphertext = bytes.fromhex(ct_hex)
    except ValueError as exc:
        raise ValueError("malformed encrypted blob (non-hex segment)") from exc
    return AESGCM(_key()).decrypt(iv, ciphertext + tag, None).decode("utf-8")


def is_encryption_configured() -> bool:
    """True when `ENCRYPTION_KEY` is present and well-formed (for status checks)."""
    try:
        _key()
        return True
    except EncryptionNotConfigured:
        return False
