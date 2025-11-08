"""Resume integration test for the Codex adapter."""

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


async def test_codex_resume(tmp_path: Path) -> None:
    """Ensures Codex sessions can be resumed via thread identifiers."""

    workspace = tmp_path / "codex_resume"
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
    resumed = None
    try:
        first = await thread.run("Describe the purpose of README.md in one sentence.")
        assert isinstance(first.text, str) and first.text.strip(), "First run should return text."

        thread_id = thread.id
        assert thread_id, "Codex thread should expose an id after the first run."

        resumed = await coder.resume_thread(thread_id)
        second = await resumed.run("Provide two bullet points summarising the repository goals.")
        assert isinstance(second.text, str) and second.text.strip(), "Resumed run should return text."
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)
            if resumed is not None:
                await close(resumed)
