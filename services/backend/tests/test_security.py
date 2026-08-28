"""Broker-secret encryption: round-trip, tamper detection, and Node compatibility.

The Phase 5 exit gate is that credentials encrypted by the Express API
(`lib/crypto.ts`, AES-256-GCM, `iv:authTag:ciphertext` hex) still decrypt here.
The fixture below is a blob produced by that Node implementation, so this test
fails if the format or the key handling ever drifts.
"""
from __future__ import annotations

import pytest

from app.core.security import (
    EncryptionNotConfigured,
    decrypt,
    encrypt,
    is_encryption_configured,
)


def test_round_trips_a_secret(encryption_key: str):
    blob = encrypt("hunter2-mt5-password")
    assert blob != "hunter2-mt5-password"
    assert decrypt(blob) == "hunter2-mt5-password"


def test_produces_the_node_blob_layout(encryption_key: str):
    iv, tag, ciphertext = encrypt("x").split(":")
    assert len(iv) == 24  # 12-byte nonce, hex
    assert len(tag) == 32  # 16-byte GCM tag, hex
    assert len(ciphertext) == 2  # 1 byte of plaintext


def test_uses_a_fresh_nonce_per_call(encryption_key: str):
    assert encrypt("same") != encrypt("same")


#: A real blob produced by `apps/api/src/lib/crypto.ts` (Node crypto,
#: aes-256-gcm) with ENCRYPTION_KEY = "0"*62 + "ff". This is the compatibility
#: fixture the Phase 5 exit gate calls for: if the Python implementation ever
#: stops matching Node's format or key handling, this test fails and existing
#: saved broker credentials would have become undecryptable.
NODE_BLOB = (
    "82068e7ed4fe9f3683028db8:"
    "80754956a52bca63240214569567e0b8:"
    "a69aa74952c2dde930b358a0c8d87794a5c21dbae06c"
)
NODE_PLAINTEXT = "mt5-secret-password-42"


def test_decrypts_ciphertext_written_by_the_node_implementation(encryption_key: str):
    assert decrypt(NODE_BLOB) == NODE_PLAINTEXT


def test_node_can_decrypt_what_python_encrypts(encryption_key: str):
    """The reverse direction, run through the real Node implementation.

    During the migration window both runtimes can write credentials, so
    compatibility has to hold in both directions. Skipped when node is absent.
    """
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    blob = encrypt(NODE_PLAINTEXT)
    script = """
const crypto = require("node:crypto");
const [keyHex, blob] = process.argv.slice(1);
const [ivHex, tagHex, ctHex] = blob.split(":");
const d = crypto.createDecipheriv("aes-256-gcm", Buffer.from(keyHex, "hex"), Buffer.from(ivHex, "hex"));
d.setAuthTag(Buffer.from(tagHex, "hex"));
process.stdout.write(d.update(ctHex, "hex", "utf8") + d.final("utf8"));
"""
    out = subprocess.run(
        [node, "-e", script, encryption_key, blob],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert out.stdout == NODE_PLAINTEXT


def test_rejects_a_tampered_blob(encryption_key: str):
    iv, tag, ciphertext = encrypt("secret").split(":")
    flipped = ciphertext[:-2] + ("00" if ciphertext[-2:] != "00" else "01")
    with pytest.raises(Exception):
        decrypt(f"{iv}:{tag}:{flipped}")


def test_rejects_a_malformed_blob(encryption_key: str):
    with pytest.raises(ValueError):
        decrypt("not-a-blob")
    with pytest.raises(ValueError):
        decrypt("aa:bb")


def test_refuses_to_run_without_a_key(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    assert is_encryption_configured() is False
    with pytest.raises(EncryptionNotConfigured):
        encrypt("x")


def test_rejects_a_wrong_length_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "abcd")
    assert is_encryption_configured() is False
    with pytest.raises(EncryptionNotConfigured):
        encrypt("x")


def test_rejects_a_non_hex_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "z" * 64)
    assert is_encryption_configured() is False


def test_a_different_key_cannot_decrypt(monkeypatch, encryption_key: str):
    blob = encrypt("secret")
    monkeypatch.setenv("ENCRYPTION_KEY", "1" * 64)
    with pytest.raises(Exception):
        decrypt(blob)
