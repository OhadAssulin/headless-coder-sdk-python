"""Web calculator generation test for the Codex adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from headless_coder_sdk.codex_sdk import CODER_NAME as CODEX_NAME
from headless_coder_sdk.core import create_coder

from .calculator_validators import ensure_basic_calculator_behaviour
from .env import find_codex_binary, node_available
from .jsdom_bridge import JsdomUnavailableError

CODEX_BINARY = find_codex_binary()
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not CODEX_BINARY, reason="Codex CLI binary not found."),
    pytest.mark.skipif(not node_available(), reason="Node.js + jsdom required for DOM validation."),
]


def build_prompt(target_dir: Path) -> list[dict[str, str]]:
    instructions = [
        f"Create a minimal web-based calculator and save it as index.html in {target_dir}.",
        'Requirements:',
        '- Provide inputs (ids numberA, numberB), a select (id operator) with + - * /, and a button compute.',
        '- Include a span with id="result" to display outcomes.',
        '- Define window.calculate(a, b, operator) that returns the numeric result and updates the span.',
        '- Attach a click listener to the compute button that reads the inputs and calls window.calculate.',
        '- Use only inline CSS/JS; overwrite existing index.html without creating extra files.',
    ]
    return [
        {
            "role": "system",
            "content": "You are an autonomous coding agent with access only to the working directory.",
        },
        {"role": "user", "content": "\n".join(instructions)},
    ]


async def test_codex_generates_calculator(tmp_path: Path) -> None:
    """Ensures Codex produces a functional calculator web page."""

    workspace = tmp_path / "codex_web"
    workspace.mkdir(parents=True, exist_ok=True)

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
        await thread.run(build_prompt(workspace))
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    html_path = workspace / 'index.html'
    html = html_path.read_text(encoding='utf-8')
    assert 'calculator' in html.lower(), 'Generated HTML should reference a calculator.'

    try:
        ensure_basic_calculator_behaviour(html)
    except JsdomUnavailableError as exc:  # pragma: no cover - guarded by skip
        pytest.skip(str(exc))
