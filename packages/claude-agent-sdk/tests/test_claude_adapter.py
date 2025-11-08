"""Tests for the Claude Agent SDK adapter."""

from __future__ import annotations

import asyncio
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
CORE_SRC = ROOT / "packages" / "core" / "src"
ADAPTER_SRC = ROOT / "packages" / "claude-agent-sdk" / "src"
for path in (CORE_SRC, ADAPTER_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from headless_coder_sdk.core import AbortController, RunResult  # noqa: E402
from headless_coder_sdk.claude_agent_sdk.adapter import (  # noqa: E402
    ClaudeAdapter,
    _ClaudeSdkBindings,
)


@dataclass
class _StubTextBlock:
    """Stub text block replicating Claude content."""

    text: str


@dataclass
class _StubToolUseBlock:
    """Stub tool use block."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class _StubToolResultBlock:
    """Stub tool result block."""

    tool_use_id: str
    content: Any
    is_error: bool | None = None


@dataclass
class _StubThinkingBlock:
    """Stub thinking block."""

    thinking: str
    signature: str


@dataclass
class _StubAssistantMessage:
    """Stub assistant message."""

    content: list[Any]
    model: str = "claude"
    parent_tool_use_id: str | None = None


@dataclass
class _StubResultMessage:
    """Stub result message."""

    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = field(default_factory=dict)
    result: str | None = None


@dataclass
class _StubSystemMessage:
    """Stub system message."""

    subtype: str
    data: dict[str, Any]


@dataclass
class _StubStreamEvent:
    """Stub stream event wrapper."""

    uuid: str
    session_id: str
    event: dict[str, Any]
    parent_tool_use_id: str | None = None


@dataclass
class _StubClaudeAgentOptions:
    """Stub ClaudeAgentOptions capturing the adapter's mapped fields."""

    cwd: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    mcp_servers: dict[str, Any] | None = None
    continue_conversation: bool = False
    resume: str | None = None
    fork_session: bool = False
    include_partial_messages: bool = False
    model: str | None = None
    permission_mode: str | None = None
    permission_prompt_tool_name: str | None = None
    add_dirs: list[str] = field(default_factory=list)


class _StubSdk:
    """Provides deterministic responses for adapter tests."""

    def __init__(self) -> None:
        self._queues: list[list[Any]] = []

    def queue(self, messages: list[Any]) -> None:
        """Enqueues messages that will be returned on the next query call."""

        self._queues.append(messages)

    def query(self, *, prompt: str, options: _StubClaudeAgentOptions) -> AsyncIterator[Any]:
        """Returns an async iterator for the next queued response."""

        assert self._queues, "No stub responses enqueued"
        messages = self._queues.pop(0)

        async def _generator() -> AsyncIterator[Any]:
            for message in messages:
                await asyncio.sleep(0)
                yield message

        _ = prompt, options
        return _generator()

    def bindings(self) -> _ClaudeSdkBindings:
        """Builds bindings compatible with the adapter."""

        return _ClaudeSdkBindings(
            query=self.query,
            ClaudeAgentOptions=_StubClaudeAgentOptions,
            AssistantMessage=_StubAssistantMessage,
            SystemMessage=_StubSystemMessage,
            ResultMessage=_StubResultMessage,
            StreamEvent=_StubStreamEvent,
            TextBlock=_StubTextBlock,
            ThinkingBlock=_StubThinkingBlock,
            ToolUseBlock=_StubToolUseBlock,
            ToolResultBlock=_StubToolResultBlock,
        )


@pytest.mark.asyncio
async def test_run_returns_final_result() -> None:
    """Ensures blocking runs return the final assistant response."""

    sdk = _StubSdk()
    assistant = _StubAssistantMessage(content=[_StubTextBlock("Solution" )])
    result = _StubResultMessage(
        subtype="result",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="session-1",
        usage={"tokens": 10},
        result="Done",
    )
    sdk.queue([assistant, result])
    adapter = ClaudeAdapter(sdk=sdk.bindings())
    thread = await adapter.start_thread()
    schema = {"type": "object"}
    run_result = await thread.run("Summarise", {"outputSchema": schema})

    assert isinstance(run_result, RunResult)
    assert run_result.thread_id == "session-1"
    assert run_result.text == "Solution"
    assert run_result.json == "Done"
    assert run_result.usage == {"tokens": 10}


@pytest.mark.asyncio
async def test_run_raises_on_error_result() -> None:
    """Verifies Claude errors propagate with descriptive details."""

    sdk = _StubSdk()
    result = _StubResultMessage(
        subtype="error",
        duration_ms=1,
        duration_api_ms=1,
        is_error=True,
        num_turns=1,
        session_id="session-2",
        result="boom",
    )
    sdk.queue([result])
    adapter = ClaudeAdapter(sdk=sdk.bindings())
    thread = await adapter.start_thread()

    with pytest.raises(RuntimeError):
        await thread.run("fail")


@pytest.mark.asyncio
async def test_stream_emits_events_and_done() -> None:
    """Validates streaming runs emit message and done events."""

    sdk = _StubSdk()
    assistant = _StubAssistantMessage(content=[_StubTextBlock("Hello")])
    result = _StubResultMessage(
        subtype="result",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
    )
    sdk.queue([assistant, result])
    adapter = ClaudeAdapter(sdk=sdk.bindings())
    thread = await adapter.start_thread()

    events = [event async for event in thread.run_streamed("hello")]
    types = [event["type"] for event in events]
    assert "message" in types
    assert types[-1] == "done"


@pytest.mark.asyncio
async def test_stream_handles_cancellation() -> None:
    """Ensures streamed runs emit cancelled + error when aborted via signal."""

    sdk = _StubSdk()
    assistant = _StubAssistantMessage(content=[_StubTextBlock("Hi")])
    result = _StubResultMessage(
        subtype="result",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
    )
    sdk.queue([assistant, result])
    adapter = ClaudeAdapter(sdk=sdk.bindings())
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
