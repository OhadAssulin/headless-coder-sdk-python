"""Interrupt handling test for Gemini CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from headless_coder_sdk.core import AbortController, create_coder
from headless_coder_sdk.gemini_cli import CODER_NAME as GEMINI_NAME

from .env import gemini_binary
import contextlib

GEMINI_BINARY = gemini_binary()
skip_reason = "Gemini CLI binary not found. Set GEMINI_BINARY_PATH or place 'gemini' on PATH."
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not GEMINI_BINARY, reason=skip_reason),
]

PROMPT = 'Create an HTML/CSS/JS Connect Four game that highlights the winning line when a player wins.'


async def test_gemini_interrupt(tmp_path: Path) -> None:
    """Ensures Gemini emits cancellation metadata when aborted."""

    workspace = tmp_path / "gemini_interrupt"
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
    controller = AbortController()

    async def cancel_soon() -> None:
        await asyncio.sleep(5)
        controller.abort('user cancel')
        interrupt = getattr(thread, 'interrupt', None)
        if callable(interrupt):
            await interrupt('user cancel')

    cancel_task = asyncio.create_task(cancel_soon())
    events: list[dict[str, object]] = []

    try:
        async for event in thread.run_streamed(PROMPT, {"signal": controller.signal}):
            events.append(event)
            if event["type"] in {"cancelled", "error"}:
                break
    except Exception as exc:  # pragma: no cover - behaviour depends on CLI
        if getattr(exc, "code", None) != "interrupted":
            raise
    finally:
        cancel_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_task
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    types = [event["type"] for event in events]
    assert "cancelled" in types or any(
        event.get("code") == "interrupted" for event in events if event["type"] == "error"
    ), "Expected Gemini to emit cancellation metadata."
