"""Structured output test for the Gemini CLI adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from headless_coder_sdk.core import create_coder
from headless_coder_sdk.gemini_cli import CODER_NAME as GEMINI_NAME

from .env import gemini_binary

GEMINI_BINARY = gemini_binary()
skip_reason = "Gemini CLI binary not found. Set GEMINI_BINARY_PATH or place 'gemini' on PATH."
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not GEMINI_BINARY, reason=skip_reason),
]

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "components": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
        },
    },
    "required": ["summary", "components"],
}


async def test_gemini_structured_output(tmp_path: Path) -> None:
    """Ensures Gemini can return structured JSON output for a schema."""

    workspace = tmp_path / "gemini_structured"
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
        result = await thread.run(
            'Summarise this repository and output two major components.',
            {"outputSchema": SCHEMA},
        )
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    assert isinstance(result.json, dict)
    summary = result.json.get("summary")
    components = result.json.get("components")
    assert isinstance(summary, str) and summary.strip(), "Summary should be populated."
    assert isinstance(components, list) and len(components) >= 2, "Components list should contain values."
