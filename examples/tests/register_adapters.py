"""Helpers for registering adapters before running integration tests."""

from __future__ import annotations

from headless_coder_sdk.core import get_adapter_factory, register_adapter
from headless_coder_sdk.codex_sdk import CODER_NAME as CODEX_NAME, create_adapter as create_codex
from headless_coder_sdk.gemini_cli import CODER_NAME as GEMINI_NAME, create_adapter as create_gemini

try:  # pragma: no cover - depends on Python version and dependency availability
    from headless_coder_sdk.claude_agent_sdk import (
        CODER_NAME as CLAUDE_NAME,
        create_adapter as create_claude,
    )

    CLAUDE_AVAILABLE = True
except ModuleNotFoundError:
    CLAUDE_AVAILABLE = False

_REGISTERED = False


def ensure_adapters_registered() -> None:
    """Registers every available adapter exactly once for the test process."""

    global _REGISTERED
    if _REGISTERED:
        return

    adapters = [
        (CODEX_NAME, create_codex),
        (GEMINI_NAME, create_gemini),
    ]
    if CLAUDE_AVAILABLE:
        adapters.append((CLAUDE_NAME, create_claude))

    for name, factory in adapters:
        if get_adapter_factory(name) is None:
            register_adapter(factory)
    _REGISTERED = True
