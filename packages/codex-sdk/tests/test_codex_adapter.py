"""Tests for the Codex CLI adapter."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
CORE_SRC = ROOT / "packages" / "core" / "src"
ADAPTER_SRC = ROOT / "packages" / "codex-sdk" / "src"
for path in (CORE_SRC, ADAPTER_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from headless_coder_sdk.core import AbortController, RunResult  # noqa: E402
from headless_coder_sdk.codex_sdk import CodexAdapter  # noqa: E402


class _StubStdin:
    """Captures data written to stdin by the adapter."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True


class _StubStdout:
    """Provides readline() over a fixed set of JSON lines."""

    def __init__(self, lines: list[dict[str, Any]]) -> None:
        self._lines = [json.dumps(line).encode("utf-8") + b"\n" for line in lines]

    async def readline(self) -> bytes:
        await asyncio.sleep(0)
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _StubStderr:
    """Feeds stderr data to the collector."""

    def __init__(self, payload: bytes = b"") -> None:
        self._payload = payload
        self._read = False

    async def read(self, _: int) -> bytes:
        await asyncio.sleep(0)
        if self._read:
            return b""
        self._read = True
        return self._payload


class _StubProcess:
    """Minimal subprocess replacement used by the tests."""

    def __init__(self, lines: list[dict[str, Any]], returncode: int = 0, stderr: bytes = b"") -> None:
        self.stdin = _StubStdin()
        self.stdout = _StubStdout(lines)
        self.stderr = _StubStderr(stderr)
        self.returncode = returncode
        self._terminated = False

    async def wait(self) -> int:
        await asyncio.sleep(0)
        return self.returncode

    def terminate(self) -> None:
        self._terminated = True

    def kill(self) -> None:
        self._terminated = True
        self.returncode = -9


class _ProcessRunner:
    """Queues stub processes returned to the adapter."""

    def __init__(self) -> None:
        self._queue: list[_StubProcess] = []

    def enqueue(self, process: _StubProcess) -> None:
        self._queue.append(process)

    async def __call__(self, *_: Any, **__: Any) -> _StubProcess:
        assert self._queue, "No stub processes queued"
        return self._queue.pop(0)


@pytest.mark.asyncio
async def test_run_returns_summary() -> None:
    """Ensures blocking runs return the final assistant response."""

    runner = _ProcessRunner()
    runner.enqueue(
        _StubProcess(
            lines=[
                {"type": "thread.started", "thread_id": "abc"},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}},
                {"type": "turn.completed", "usage": {"tokens": 10}},
            ]
        )
    )
    adapter = CodexAdapter(process_runner=runner)
    thread = await adapter.start_thread()
    schema = {"type": "object"}
    result = await thread.run("Summarise", {"outputSchema": schema})

    assert isinstance(result, RunResult)
    assert result.thread_id == "abc"
    assert result.text == "hello"
    assert result.usage == {"tokens": 10}


@pytest.mark.asyncio
async def test_run_raises_on_non_zero_exit() -> None:
    """Verifies process failures bubble up as runtime errors."""

    runner = _ProcessRunner()
    runner.enqueue(
        _StubProcess(
            lines=[{"type": "thread.started", "thread_id": "abc"}],
            returncode=1,
            stderr=b"boom",
        )
    )
    adapter = CodexAdapter(process_runner=runner)
    thread = await adapter.start_thread()

    with pytest.raises(RuntimeError):
        await thread.run("fail")


@pytest.mark.asyncio
async def test_stream_emits_events() -> None:
    """Validates streaming runs emit message and done events."""

    runner = _ProcessRunner()
    runner.enqueue(
        _StubProcess(
            lines=[
                {"type": "thread.started", "thread_id": "xyz"},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}},
                {"type": "turn.completed", "usage": {"tokens": 4}},
            ]
        )
    )
    adapter = CodexAdapter(process_runner=runner)
    thread = await adapter.start_thread()

    events = [event async for event in thread.run_streamed("hi")]
    types = [event["type"] for event in events]
    assert "message" in types
    assert types[-1] == "done"


@pytest.mark.asyncio
async def test_stream_handles_cancellation() -> None:
    """Ensures cancellation emits cancelled and error events."""

    runner = _ProcessRunner()
    runner.enqueue(
        _StubProcess(
            lines=[{"type": "item.completed", "item": {"type": "agent_message", "text": "pending"}}]
        )
    )
    adapter = CodexAdapter(process_runner=runner)
    controller = AbortController()
    thread = await adapter.start_thread()

    async def _consume() -> list[str]:
        seen: list[str] = []
        async for event in thread.run_streamed("hi", {"signal": controller.signal}):
            seen.append(event["type"])
            controller.abort("stop")
        return seen

    events = await _consume()
    assert "cancelled" in events
    assert "error" in events
