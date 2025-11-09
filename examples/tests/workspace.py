"""Workspace utilities shared by the example integration tests."""

from __future__ import annotations

import json
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
CLAUDE_CONFIG_FILES = ('settings.json', 'settings.local.json')


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
    """Copies Claude configuration into the workspace and loads env vars."""

    target = workspace / '.claude'
    shutil.rmtree(target, ignore_errors=True)
    if CLAUDE_TEMPLATE.exists():
        shutil.copytree(CLAUDE_TEMPLATE, target)
    else:
        target.mkdir(parents=True, exist_ok=True)
    _load_claude_environment(target)


def _load_claude_environment(config_dir: Path) -> None:
    """Reads Claude config files and exports their env entries."""

    os.environ['CLAUDE_CONFIG_DIR'] = str(config_dir)

    for name in CLAUDE_CONFIG_FILES:
        file_path = config_dir / name
        if not file_path.exists():
            continue
        try:
            data = json.loads(file_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            continue
        env_section = data.get('env')
        if not isinstance(env_section, dict):
            continue
        for key, value in env_section.items():
            if isinstance(value, str):
                os.environ[key] = value
