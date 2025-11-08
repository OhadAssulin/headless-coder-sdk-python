"""Workspace utilities shared by the example integration tests."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get('HEADLESS_CODER_TEST_ROOT', '/tmp/headless-coder-sdk-python'))
CLAUDE_TEMPLATE = Path(
    os.environ.get(
        'CLAUDE_TEST_CONFIG_TEMPLATE',
        '/Users/ohadassulin/vs-code-projects/twitter_research/.claude',
    ),
)


def workspace_path(name: str, root: Path | None = None) -> Path:
    """Returns the absolute workspace path for the provided logical name."""

    base = root or DEFAULT_ROOT
    return base / name


def reset_workspace(name: str, root: Path | None = None) -> Path:
    """Removes the workspace directory if it exists and recreates it."""

    path = workspace_path(name, root)
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_claude_config(workspace: Path) -> None:
    """Copies the Claude configuration template into the workspace when available."""

    target = workspace / '.claude'
    shutil.rmtree(target, ignore_errors=True)
    if CLAUDE_TEMPLATE.exists():
        shutil.copytree(CLAUDE_TEMPLATE, target)
