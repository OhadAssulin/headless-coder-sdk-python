"""Gemini CLI calculator integration test."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from headless_coder_sdk.core import create_coder
from headless_coder_sdk.gemini_cli import CODER_NAME as GEMINI_NAME

from .calculator_validators import ensure_basic_calculator_behaviour
from .env import gemini_binary, node_available
from .jsdom_bridge import JsdomUnavailableError

GEMINI_BINARY = gemini_binary()
skip_reason = "Gemini CLI binary not found. Set GEMINI_BINARY_PATH or place 'gemini' on PATH."
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not GEMINI_BINARY, reason=skip_reason),
    pytest.mark.skipif(not node_available(), reason="Node.js + jsdom required for DOM validation."),
]

def build_prompt(workspace: Path) -> list[dict[str, str]]:
    instructions = [
        f"You are operating inside {workspace}.",
        'Tasks:',
        '- Overwrite index.html with a complete calculator web page.',
        '- Requirements:',
        '  * Provide numeric inputs (ids numberA, numberB) and a select (id operator) for + - * /.',
        '  * Include a span with id="result" to display the outcome.',
        '  * Define window.calculate(a, b, operator) to update the result span and return the value.',
        '  * Ensure the compute button prevents default submission and invokes window.calculate.',
        '  * Use inline CSS only.',
    ]
    return [
        {"role": "system", "content": "You are a deterministic engineer generating project files."},
        {"role": "user", "content": "\n".join(instructions)},
    ]


async def test_gemini_generates_calculator(tmp_path: Path) -> None:
    """Ensures Gemini produces a calculator UI that behaves correctly."""

    workspace = tmp_path / "gemini_calculator"
    workspace.mkdir(parents=True, exist_ok=True)

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
        await asyncio.wait_for(thread.run(build_prompt(workspace)), timeout=180)
    except FileNotFoundError:  # pragma: no cover - indicates missing CLI despite detection
        pytest.skip('Skipping Gemini test because the gemini CLI is unavailable.')
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    html_path = workspace / 'index.html'
    html = html_path.read_text(encoding='utf-8')
    assert 'numberA' in html, 'Generated HTML should include the first input field.'

    try:
        ensure_basic_calculator_behaviour(html)
    except JsdomUnavailableError as exc:  # pragma: no cover - guarded by skip
        pytest.skip(str(exc))
