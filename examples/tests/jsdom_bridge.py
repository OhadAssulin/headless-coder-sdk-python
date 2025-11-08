"""Utilities for validating generated HTML using JSDOM via Node.js."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .env import node_available

REPO_ROOT = Path(__file__).resolve().parents[2]
TS_ROOT_DEFAULT = REPO_ROOT.parent / 'headless_coder' / 'main'
JS_RUNNER = REPO_ROOT / 'examples' / 'tests' / 'jsdom_runner.mjs'


class JsdomUnavailableError(RuntimeError):
    """Raised when Node.js or jsdom is not available for DOM validation."""


def _build_node_env() -> dict[str, str]:
    env = os.environ.copy()
    node_modules_candidates = []
    if 'NODE_PATH' in env and env['NODE_PATH']:
        node_modules_candidates.append(env['NODE_PATH'])
    ts_root = Path(os.environ.get('HEADLESS_CODER_TS_ROOT', TS_ROOT_DEFAULT))
    node_modules_candidates.append(str(ts_root / 'node_modules'))
    env['NODE_PATH'] = os.pathsep.join(filter(None, node_modules_candidates))
    return env


def run_jsdom(html: str, script: str, *, options: Mapping[str, Any] | None = None) -> Any:
    """Executes the provided script against the supplied HTML inside JSDOM."""

    if not node_available():
        raise JsdomUnavailableError('Node.js is required to run DOM validations.')
    payload = {
        'html': html,
        'script': script,
        'options': options or {},
    }
    process = subprocess.run(
        ['node', str(JS_RUNNER)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=_build_node_env(),
        check=False,
    )
    if not process.stdout:
        raise JsdomUnavailableError(process.stderr or 'Node.js returned no output.')
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise JsdomUnavailableError(f'Failed to parse JSDOM output: {process.stdout!r}') from exc
    if not result.get('ok', False):
        error = result.get('error') or {}
        raise JsdomUnavailableError(error.get('message', 'Unknown JSDOM error'))
    return result.get('result')
