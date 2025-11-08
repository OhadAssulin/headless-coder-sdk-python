"""Interrupt handling test for the Codex adapter."""

from __future__ import annotations

import pytest

from headless_coder_sdk.codex_sdk import CODER_NAME as CODEX_NAME
from headless_coder_sdk.core import AbortController, create_coder

from .env import find_codex_binary

CODEX_BINARY = find_codex_binary()
skip_reason = "Codex CLI binary not found. Set CODEX_EXECUTABLE_PATH or place 'codex' on PATH."
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not CODEX_BINARY, reason=skip_reason),
]

PROMPT = (
    "List every file in the repository one by one, pausing to explain each file before turning to the next."
)


async def test_codex_stream_interrupt(workspace_factory) -> None:
    """Ensures aborting the signal yields cancelled + error events."""

    workspace = workspace_factory('codex_interrupt')

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
    controller = AbortController()
    events: list[dict[str, object]] = []

    try:
        async for event in thread.run_streamed(PROMPT, {"signal": controller.signal}):
            events.append(event)
            if event["type"] == "message" and not controller.signal.aborted:
                controller.abort("user-cancelled")
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    assert controller.signal.aborted, "AbortController should report aborted state."
    types = [event["type"] for event in events]
    assert "cancelled" in types, "Stream should emit a cancelled event after abort."
    error_events = [event for event in events if event["type"] == "error"]
    assert error_events, "Stream should emit an error event after abort."
    assert error_events[-1].get("code") == "interrupted"
