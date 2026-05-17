"""Pytest fixtures and process-wide setup."""
import os

# Settings reads ANTHROPIC_API_KEY at import time via pydantic-settings.
# Tests don't make real API calls, but they import modules that touch settings.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
