"""Back-compat shim.

The provider-dispatching `analyze()` now lives in `providers.py`, which supports
mock / Anthropic / Gemini with a runtime-switchable active provider. This module
re-exports it so existing imports keep working.
"""
from __future__ import annotations

from .providers import analyze

__all__ = ["analyze"]
