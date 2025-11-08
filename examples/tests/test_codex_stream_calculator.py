"""Streaming calculator integration test for the Codex adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from headless_coder_sdk.codex_sdk import CODER_NAME as CODEX_NAME
from headless_coder_sdk.core import create_coder

from .calculator_validators import ensure_trig_calculator_behaviour
from .env import find_codex_binary

CODEX_BINARY = find_codex_binary()
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not CODEX_BINARY, reason="Codex CLI binary not found."),
]


def build_prompt(workspace: Path) -> list[dict[str, str]]:
    instructions = [
        f"You are working inside {workspace}.",
        'Tasks:',
        '- Overwrite index.html with a scientific calculator focused on sine and cosine.',
        '- Requirements:',
        '  * Provide inputs with ids angleDegrees and angleRadians plus buttons to compute sine and cosine.',
        '  * Display results in spans with ids sinResult and cosResult.',
        '  * Implement window.toRadians(deg) and window.updateTrigValues() in inline JavaScript.',
        '  * Attach event handlers so clicking the compute button updates both sine and cosine.',
        '  * Include inline CSS for clarity without external assets.',
        '- Do not create additional files or run shell commands beyond writing index.html.',
        '- After writing the file, confirm completion succinctly.',
    ]
    return [
        {"role": "system", "content": "You produce deterministic project artifacts exactly as specified."},
        {"role": "user", "content": "\n".join(instructions)},
    ]


async def test_codex_streams_trig_calculator(workspace_factory) -> None:
    """Streams Codex output while verifying the generated trig calculator."""

    workspace = workspace_factory('codex_stream')
    stream_path = workspace / "stream.txt"

    coder = create_coder(
        CODEX_NAME,
        {
            "workingDirectory": str(workspace),
            "sandboxMode": "workspace-write",
            "skipGitRepoCheck": True,
            "codexExecutablePath": CODEX_BINARY,
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

    streamed = stream_path.read_text(encoding='utf-8').strip()
    assert streamed, 'Stream output should be recorded.'
