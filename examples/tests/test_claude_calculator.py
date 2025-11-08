"""Claude Agent SDK calculator integration test."""

from __future__ import annotations

from pathlib import Path

import asyncio
import pytest

from headless_coder_sdk.core import create_coder

from .calculator_validators import ensure_basic_calculator_behaviour
from .env import claude_credentials_available, claude_sdk_available, node_available, python_supports_claude
from .jsdom_bridge import JsdomUnavailableError

py_version_reason = 'Claude Agent SDK requires Python 3.10+.'
missing_sdk_reason = 'claude-agent-sdk is not installed; install it to run Claude tests.'
missing_creds_reason = 'Claude credentials are unavailable in the environment.'
missing_node_reason = 'Node.js + jsdom required for DOM validation.'

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not python_supports_claude(), reason=py_version_reason),
    pytest.mark.skipif(not claude_sdk_available(), reason=missing_sdk_reason),
    pytest.mark.skipif(not claude_credentials_available(), reason=missing_creds_reason),
    pytest.mark.skipif(not node_available(), reason=missing_node_reason),
]

if claude_sdk_available():
    from headless_coder_sdk.claude_agent_sdk import CODER_NAME as CLAUDE_NAME
else:  # pragma: no cover - guarded by skip
    CLAUDE_NAME = 'claude'


def build_prompt(workspace: Path) -> list[dict[str, str]]:
    instructions = [
        f"You are operating inside {workspace}.",
        'Tasks:',
        '- Overwrite index.html with a complete calculator page.',
        '- Requirements:',
        '  * Provide two numeric inputs with ids numberA and numberB.',
        '  * Use a select (id operator) supporting +, -, *, / and a compute button to trigger calculations.',
        '  * Include a span with id="result" showing the outcome.',
        '  * Define window.calculate(a, b, operator) to update the span and return the value.',
        '  * Attach listeners so the compute button prevents default submission.',
        '  * Invoke window.calculate from the compute button handler.',
    ]
    return [
        {
            "role": "system",
            "content": "You are a meticulous engineer generating deterministic project files.",
        },
        {"role": "user", "content": "\n".join(instructions)},
    ]


async def test_claude_generates_calculator(tmp_path: Path) -> None:
    """Ensures Claude generates a functional calculator web page."""

    workspace = tmp_path / "claude_calculator"
    workspace.mkdir(parents=True, exist_ok=True)

    coder = create_coder(
        CLAUDE_NAME,
        {
            "workingDirectory": str(workspace),
            "permissionMode": 'bypassPermissions',
            "allowedTools": ['Write', 'Edit', 'Read', 'NotebookEdit'],
        },
    )

    thread = await coder.start_thread()
    try:
        await asyncio.wait_for(thread.run(build_prompt(workspace)), timeout=180)
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    html = (workspace / 'index.html').read_text(encoding='utf-8')
    assert 'numberA' in html
    try:
        ensure_basic_calculator_behaviour(html)
    except JsdomUnavailableError as exc:  # pragma: no cover - guarded by skip
        pytest.skip(str(exc))
