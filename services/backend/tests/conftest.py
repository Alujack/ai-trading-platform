"""Test fixtures.

The backend reads its configuration through a cached `get_settings()`, so any
test that varies the environment has to clear that cache. `clean_settings` does
it around every test so cases can't leak configuration into each other.
"""
from __future__ import annotations

import os

import pytest

from app.core.settings import get_settings
from app.domain.execution.broker import reset_broker

# The AI settings module reads provider keys at import time via
# pydantic-settings. No test makes a real API call, but importing the module
# chain touches them. (Carried over from services/ai/tests/conftest.py.)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch):
    """Isolate settings + the memoized broker/symbol map between tests."""
    get_settings.cache_clear()
    reset_broker()
    # A deterministic paper account, independent of the developer's .env.
    monkeypatch.setenv("PAPER_ACCOUNT_BALANCE", "10000")
    monkeypatch.setenv("PAPER_PEAK_BALANCE", "10000")
    monkeypatch.setenv("PAPER_RISK_PERCENT", "1")
    monkeypatch.delenv("BROKER_SYMBOL_MAP", raising=False)
    monkeypatch.delenv("API_SHADOW_MODE", raising=False)
    yield
    get_settings.cache_clear()
    reset_broker()


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """A fixed 32-byte key so ciphertext comparisons are reproducible."""
    key = "0" * 62 + "ff"
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    return key

@pytest.fixture
def mock_ai_provider(monkeypatch: pytest.MonkeyPatch):
    """Pin the AI layer to the built-in `mock` provider.

    Any test that drives the gate end to end must not depend on a real LLM: with
    a live provider configured the gate correctly fails CLOSED on a bad key, and
    the test would be asserting the developer's API credentials rather than the
    gate's logic. `mock` is deterministic and makes no network call.
    """
    from app.integrations.ai import providers
    from app.integrations.ai.settings import get_settings as get_ai_settings

    for var in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEFAULT_PROVIDER", "mock")

    get_ai_settings.cache_clear()
    monkeypatch.setattr(providers, "_active", "mock")
    monkeypatch.setattr(providers, "_cache", {})
    monkeypatch.setattr(providers, "_overrides", {})
    yield
    get_ai_settings.cache_clear()
