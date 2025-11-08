"""Structured output integration test for the Codex adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from headless_coder_sdk.codex_sdk import CODER_NAME as CODEX_NAME
from headless_coder_sdk.core import create_coder

from .env import find_codex_binary

CODEX_BINARY = find_codex_binary()

skip_reason = "Codex CLI binary not found. Set CODEX_EXECUTABLE_PATH or place 'codex' on PATH."
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not CODEX_BINARY, reason=skip_reason),
]

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "keyPoints": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
        },
    },
    "required": ["summary", "keyPoints"],
    "additionalProperties": False,
}


async def test_codex_structured_output(tmp_path: Path) -> None:
    """Ensures Codex can return JSON that conforms to the provided schema."""

    workspace = tmp_path / "codex_structured"
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
        result = await thread.run(
            "Summarise the purpose of this repository and list two components.",
            {"outputSchema": SCHEMA},
        )
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    assert result.json, "Structured output should be parsed into result.json"
    structured = result.json
    assert isinstance(structured, dict)
    assert isinstance(structured.get("summary"), str)
    assert isinstance(structured.get("keyPoints"), list)
    assert len(structured["keyPoints"]) >= 2
