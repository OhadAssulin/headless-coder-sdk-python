"""Claude interrupt test."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from headless_coder_sdk.core import AbortController, create_coder

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

PROMPT = 'Create an HTML/CSS/JS Connect Four game that highlights the winning line when a player wins.'


async def test_claude_interrupt(tmp_path: Path) -> None:
    """Ensures Claude surfaces cancellation metadata when aborted mid-stream."""

    workspace = tmp_path / "claude_interrupt"
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
    controller = AbortController()

    async def cancel_tasks() -> None:
        await asyncio.sleep(5)
        controller.abort('user cancel')
        interrupt = getattr(thread, 'interrupt', None)
        if callable(interrupt):
            await interrupt('user cancel')

    cancel_task = asyncio.create_task(cancel_tasks())
    events: list[dict[str, object]] = []

    try:
        async for event in thread.run_streamed(PROMPT, {"signal": controller.signal}):
            events.append(event)
            if event["type"] in {"cancelled", "error"}:
                break
    except Exception as exc:  # pragma: no cover - depends on provider
        if getattr(exc, 'code', None) != 'interrupted':
            raise
    finally:
        cancel_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_task
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    types = [event["type"] for event in events]
    saw_error = any(event.get("code") == 'interrupted' for event in events if event["type"] == "error")
    assert "cancelled" in types or saw_error, 'Expected Claude to emit cancellation metadata.'
