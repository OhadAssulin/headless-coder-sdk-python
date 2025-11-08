"""Tests covering the Gemini CLI adapter."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
CORE_SRC = ROOT / "packages" / "core" / "src"
ADAPTER_SRC = ROOT / "packages" / "gemini-cli" / "src"
for path in (CORE_SRC, ADAPTER_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from headless_coder_sdk.core import AbortController, RunResult  # noqa: E402
from headless_coder_sdk.gemini_cli import GeminiAdapter  # noqa: E402


class _FakeProcess:
    """Minimal asyncio.subprocess.Process double for unit tests."""

    def __init__(self, stdout_data: bytes = b"", stderr_data: bytes = b"", returncode: int = 0) -> None:
        """Initialises the fake process with canned stdout/stderr payloads."""
        self._stdout_data = stdout_data
        self._stderr_data = stderr_data
        self.returncode = returncode
        self.stdout = None
        self.stderr = None
        self._terminated = False

    async def communicate(self) -> tuple[bytes, bytes]:
        """Mimics asyncio.subprocess.Process.communicate()."""
        return self._stdout_data, self._stderr_data

    async def wait(self) -> int:
        """Returns the exit code when awaited."""
        return self.returncode

    def terminate(self) -> None:
        """Marks the process as terminated."""
        self._terminated = True

    def kill(self) -> None:
        """Marks the process as forcibly killed."""
        self._terminated = True


class _FakeStdout:
    """Async iterator that mimics Process.stdout for streaming tests."""

    def __init__(self, lines: list[str]) -> None:
        """Stores encoded lines that will be replayed sequentially."""
        self._lines = [line.encode("utf-8") for line in lines]

    async def readline(self) -> bytes:
        """Returns the next encoded line until exhaustion."""
        await asyncio.sleep(0)
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _StreamingProcess(_FakeProcess):
    """Fake process that exposes stdout for stream iteration."""

    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        """Initialises the streaming fake process."""
        super().__init__(stdout_data=b"", stderr_data=b"", returncode=returncode)
        self.stdout = _FakeStdout(lines)

    async def communicate(self) -> tuple[bytes, bytes]:  # pragma: no cover - not used in stream mode
        """Streaming mode should never rely on communicate()."""
        raise AssertionError("communicate() should not be invoked for streaming")


@pytest.mark.asyncio
async def test_run_returns_structured_result() -> None:
    """Ensures non-streaming runs parse Gemini output correctly."""
    payload = {
        "session_id": "abc",
        "response": "Hello",
        "stats": {"tokens": 10},
        "json": {"status": "ok"},
    }
    process = _FakeProcess(stdout_data=json.dumps(payload).encode("utf-8"))

    async def _runner(*_: Any, **__: Any):
        """Returns the prepared fake process."""
        return process

    adapter = GeminiAdapter(process_runner=_runner)
    thread = await adapter.start_thread()
    schema = {"type": "object"}
    result = await thread.run("Summarise", {"outputSchema": schema})

    assert isinstance(result, RunResult)
    assert result.thread_id == "abc"
    assert result.text == "Hello"
    assert result.json == {"status": "ok"}
    assert result.usage == {"tokens": 10}
    assert result.raw == payload


@pytest.mark.asyncio
async def test_run_raises_on_process_failure() -> None:
    """Verifies process failures bubble up as runtime errors."""
    process = _FakeProcess(stdout_data=b"", stderr_data=b"boom", returncode=1)

    async def _runner(*_: Any, **__: Any):
        """Returns the prepared failing process."""
        return process

    adapter = GeminiAdapter(process_runner=_runner)
    thread = await adapter.start_thread()

    with pytest.raises(RuntimeError):
        await thread.run("fail")


@pytest.mark.asyncio
async def test_stream_emits_normalised_events() -> None:
    """Validates event translation for streaming results."""
    lines = [
        json.dumps({"type": "init", "session_id": "abc", "model": "g"}),
        json.dumps({"type": "message", "role": "assistant", "content": "hi"}),
        json.dumps({"type": "result", "stats": {"tokens": 5}}),
    ]
    process = _StreamingProcess(lines)

    async def _runner(*_: Any, **__: Any):
        """Returns the streaming fake process."""
        return process

    adapter = GeminiAdapter(process_runner=_runner)
    thread = await adapter.start_thread()

    events = [event async for event in thread.run_streamed("ping")]
    assert events[0]["type"] == "init"
    assert events[1]["type"] == "message"
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_stream_handles_cancellation() -> None:
    """Ensures cancellation produces cancelled and error events."""
    lines = [json.dumps({"type": "message", "content": "hello"})]
    process = _StreamingProcess(lines)

    async def _runner(*_: Any, **__: Any):
        """Returns the streaming fake process that will be aborted."""
        return process

    adapter = GeminiAdapter(process_runner=_runner)
    controller = AbortController()
    thread = await adapter.start_thread()

    async def _consume() -> list[str]:
        """Collects stream event types while aborting midstream."""
        seen = []
        async for event in thread.run_streamed("ping", {"signal": controller.signal}):
            seen.append(event["type"])
            if event["type"] == "message":
                controller.abort("stop")
        return seen

    events = await _consume()
    assert "cancelled" in events
    assert "error" in events
