"""Pytest fixtures shared across the example integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from .register_adapters import ensure_adapters_registered
from .workspace import DEFAULT_ROOT

ensure_adapters_registered()


@pytest.fixture(scope='session')
def examples_root() -> Path:
    """Root directory under which temporary workspaces will be created."""

    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    return DEFAULT_ROOT


@pytest.fixture
def fresh_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Provides an isolated workspace directory for stateful tests."""

    temp_dir = tmp_path_factory.mktemp('headless-coder')
    return Path(temp_dir)
