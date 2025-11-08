"""Pytest fixtures shared across the example integration tests."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable
import sys

import pytest

from .env import claude_sdk_available

if not claude_sdk_available():  # pragma: no cover - executed when real SDK unavailable
    fakes_path = Path(__file__).resolve().parents[1] / 'fakes'
    sys.path.insert(0, str(fakes_path))

from .register_adapters import ensure_adapters_registered
from .workspace import DEFAULT_ROOT, reset_workspace

ensure_adapters_registered()


@pytest.fixture(scope='session')
def examples_root() -> Path:
    """Root directory under which temporary workspaces will be created."""

    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    return DEFAULT_ROOT


@pytest.fixture
def workspace_factory() -> Callable[[str], Path]:
    """Factory fixture that resets workspaces under the shared root."""

    def _factory(name: str) -> Path:
        unique = f"{name}-{uuid.uuid4().hex}"
        return reset_workspace(unique)

    return _factory
