"""Environment detection helpers for provider-specific integration tests."""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

CLAUDE_KEYS = (
    'ANTHROPIC_API_KEY',
    'CLAUDE_API_KEY',
    'ANTHROPIC_API_TOKEN',
    'AWS_BEARER_TOKEN_BEDROCK',
)


def find_codex_binary() -> Optional[str]:
    """Returns the codex executable path when available."""

    override = os.environ.get('CODEX_EXECUTABLE_PATH')
    if override:
        path = Path(override)
        if path.exists():
            return str(path)
        return None
    return shutil.which('codex')


def gemini_binary() -> Optional[str]:
    """Returns the gemini CLI path when present on PATH or via env override."""

    override = os.environ.get('GEMINI_BINARY_PATH')
    if override:
        path = Path(override)
        if path.exists():
            return str(path)
        return None
    return shutil.which('gemini')


def claude_credentials_available() -> bool:
    """Checks whether any Claude credential environment variable is set."""

    return any(os.environ.get(key) for key in CLAUDE_KEYS)


def python_supports_claude() -> bool:
    """Claude Agent SDK requires Python >= 3.10."""

    return sys.version_info >= (3, 10)


def node_available() -> bool:
    """Determines whether Node.js is accessible for JSDOM helpers."""

    return shutil.which('node') is not None


def claude_sdk_available() -> bool:
    """Checks whether the claude-agent-sdk package can be imported."""

    try:  # pragma: no cover - depends on optional dependency
        importlib.import_module('claude_agent_sdk')
    except ModuleNotFoundError:
        return False
    return True
