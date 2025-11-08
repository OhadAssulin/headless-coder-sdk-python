"""Claude resume test."""

from __future__ import annotations

from pathlib import Path

import pytest

from headless_coder_sdk.core import create_coder

from .env import claude_credentials_available, claude_sdk_available, python_supports_claude

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


async def test_claude_resume(tmp_path: Path) -> None:
    """Ensures Claude sessions can be resumed using thread identifiers."""

    workspace = tmp_path / "claude_resume"
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
    resumed = None
    try:
        first = await thread.run('Describe the purpose of README.md in a short paragraph.')
        assert isinstance(first.text, str) and first.text.strip()
        thread_id = thread.id
        assert thread_id
        resumed = await coder.resume_thread(thread_id)
        second = await resumed.run('List two follow-up tasks related to the previous answer.')
        assert isinstance(second.text, str) and second.text.strip()
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)
            if resumed is not None:
                await close(resumed)
