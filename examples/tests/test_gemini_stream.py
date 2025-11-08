"""Gemini CLI streaming calculator test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from headless_coder_sdk.core import create_coder
from headless_coder_sdk.gemini_cli import CODER_NAME as GEMINI_NAME

from .calculator_validators import ensure_trig_calculator_behaviour
from .env import gemini_binary

GEMINI_BINARY = gemini_binary()
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not GEMINI_BINARY, reason="Gemini CLI binary not found."),
]


def build_prompt(workspace: Path) -> list[dict[str, str]]:
    instructions = [
        f"You are working inside {workspace}.",
        'Tasks:',
        '- Overwrite index.html with a trigonometry assistant page that computes sin and cos.',
        '- Requirements:',
        '  * Provide inputs with ids trigAngleDegrees and trigAngleRadians.',
        '  * Add a compute button with id trigCompute.',
        '  * Include spans with ids trigSin and trigCos displaying the results.',
        (
            '  * Define window.handleTrig() to parse inputs, compute Math.sin/Math.cos, update the spans,'
            ' and return the results.'
        ),
        '  * Prevent default form submission and ensure the button triggers window.handleTrig().',
        '- Confirm completion in one sentence once the file is written.',
    ]
    return [
        {"role": "system", "content": "You create deterministic project files with no extraneous output."},
        {"role": "user", "content": "\n".join(instructions)},
    ]


async def test_gemini_streams_trig_calculator(workspace_factory) -> None:
    """Streams Gemini output and validates the generated trig calculator."""

    workspace = workspace_factory('gemini_stream')
    stream_path = workspace / 'stream.txt'

    coder = create_coder(
        GEMINI_NAME,
        {
            "workingDirectory": str(workspace),
            "includeDirectories": [str(workspace)],
            "yolo": True,
            "geminiBinaryPath": GEMINI_BINARY,
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
