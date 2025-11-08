"""Claude Agent SDK streaming calculator test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from headless_coder_sdk.core import create_coder

from .calculator_validators import ensure_trig_calculator_behaviour
from .env import claude_credentials_available, claude_sdk_available, python_supports_claude
from .workspace import ensure_claude_config

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not python_supports_claude(), reason='Claude Agent SDK requires Python 3.10+.'),
    pytest.mark.skipif(not claude_sdk_available(), reason='claude-agent-sdk is not installed.'),
    pytest.mark.skipif(not claude_credentials_available(), reason='Claude credentials are unavailable.'),
]

if claude_sdk_available():
    from headless_coder_sdk.claude_agent_sdk import CODER_NAME as CLAUDE_NAME
else:  # pragma: no cover - guarded by skip
    CLAUDE_NAME = 'claude'


def build_prompt(workspace: Path) -> list[dict[str, str]]:
    instructions = [
        f"You are working inside {workspace}.",
        'Tasks:',
        '- Overwrite index.html with a sin/cos calculator.',
        '- Requirements:',
        '  * Provide inputs for degrees/radians plus spans with ids sinValue and cosValue.',
        '  * Define window.computeTrig() that updates the spans and returns the numeric results.',
        '  * Ensure clicking the compute button prevents default submission and triggers computeTrig.',
    ]
    return [
        {"role": "system", "content": "You generate deterministic project files."},
        {"role": "user", "content": "\n".join(instructions)},
    ]


async def test_claude_streams_calculator(workspace_factory) -> None:
    """Streams Claude output to build a trig calculator."""

    workspace = workspace_factory('claude_stream')
    ensure_claude_config(workspace)
    stream_path = workspace / 'stream.txt'

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
        with stream_path.open('w', encoding='utf-8') as handle:
            async for event in thread.run_streamed(build_prompt(workspace)):
                handle.write(json.dumps(event) + "\n")
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    html = (workspace / 'index.html').read_text(encoding='utf-8')
    ensure_trig_calculator_behaviour(html)

    assert stream_path.read_text(encoding='utf-8').strip(), 'Stream output should be recorded.'
