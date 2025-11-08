"""Claude Agent SDK adapter implementing the shared headless coder contract."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from headless_coder_sdk.core import (
    CoderStreamEvent,
    EventIterator,
    HeadlessCoder,
    PromptInput,
    RunOpts,
    RunResult,
    StartOpts,
    ThreadHandle,
    link_signal,
    now,
)

LOGGER = logging.getLogger(__name__)
CODER_NAME = "claude"


class ClaudeSdkNotAvailableError(RuntimeError):
    """Raised when the Claude SDK dependency cannot be imported."""


@dataclass
class _ClaudeSdkBindings:
    """Holds references to the Claude SDK symbols used by the adapter."""

    query: Callable[..., AsyncIterator[Any]]
    ClaudeAgentOptions: type
    AssistantMessage: type
    SystemMessage: type
    ResultMessage: type
    StreamEvent: type
    TextBlock: type
    ThinkingBlock: type
    ToolUseBlock: type
    ToolResultBlock: type


def _import_sdk() -> _ClaudeSdkBindings:
    """Imports Claude SDK symbols and wraps them inside a bindings container."""

    try:
        from claude_agent_sdk import query as sdk_query
        from claude_agent_sdk.types import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            StreamEvent,
            SystemMessage,
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )
    except Exception as exc:  # pragma: no cover - exercised only when dependency missing
        raise ClaudeSdkNotAvailableError(
            "claude-agent-sdk is not available. Install it with Python >=3.10 to use this adapter."
        ) from exc
    return _ClaudeSdkBindings(
        query=sdk_query,
        ClaudeAgentOptions=ClaudeAgentOptions,
        AssistantMessage=AssistantMessage,
        SystemMessage=SystemMessage,
        ResultMessage=ResultMessage,
        StreamEvent=StreamEvent,
        TextBlock=TextBlock,
        ThinkingBlock=ThinkingBlock,
        ToolUseBlock=ToolUseBlock,
        ToolResultBlock=ToolResultBlock,
    )


@dataclass
class ClaudeThreadState:
    """Tracks mutable information for each Claude thread."""

    opts: StartOpts
    session_id: str
    resume: bool
    current_run: Optional["ActiveClaudeRun"] = None


@dataclass
class ActiveClaudeRun:
    """Captures metadata about an in-flight Claude SDK run."""

    generator: AsyncIterator[Any]
    unsubscribe: Callable[[], None]
    aborted: bool = False
    abort_reason: Optional[str] = None


class ClaudeThreadHandle(ThreadHandle):
    """Thread handle bridging the Claude adapter into the shared interface."""

    def __init__(self, adapter: "ClaudeAdapter", state: ClaudeThreadState) -> None:
        """Initialises the handle with the owning adapter and state."""

        self._adapter = adapter
        self.internal = state
        self.provider = CODER_NAME
        self.id = state.session_id

    async def run(self, input: PromptInput, opts: Optional[RunOpts] = None) -> RunResult:
        """Delegates to the adapter's blocking run helper."""

        return await self._adapter._run_internal(self, input, opts)

    def run_streamed(self, input: PromptInput, opts: Optional[RunOpts] = None) -> EventIterator:
        """Delegates to the adapter's streaming helper."""

        return self._adapter._run_streamed_internal(self, input, opts)

    async def interrupt(self, reason: Optional[str] = None) -> None:
        """Cooperatively interrupts the active run if present."""

        await self._adapter._abort_active_run(self.internal, reason or "Interrupted")

    async def close(self) -> None:
        """No-op close hook for API parity."""

        return None


class ClaudeAdapter(HeadlessCoder):
    """Adapter that shells out to the Claude Agent SDK."""

    def __init__(
        self,
        default_opts: Optional[StartOpts] = None,
        sdk: Optional[_ClaudeSdkBindings] = None,
    ) -> None:
        """Creates a new adapter instance.

        Args:
            default_opts: Default start options injected when callers omit values.
            sdk: Optional SDK bindings used for testing or custom loading.
        """

        self._default_opts = default_opts or {}
        self._sdk: Optional[_ClaudeSdkBindings] = sdk
        self._sdk_error: Optional[Exception] = None
        if sdk is None:
            try:
                self._sdk = _import_sdk()
            except ClaudeSdkNotAvailableError as exc:
                self._sdk_error = exc

    async def start_thread(self, opts: Optional[StartOpts] = None) -> ThreadHandle:
        """Creates a new logical Claude session."""

        merged = self._merge_start_opts(opts)
        session_id = merged.get("resume") or str(uuid.uuid4())
        state = ClaudeThreadState(opts=merged, session_id=session_id, resume=False)
        return ClaudeThreadHandle(self, state)

    async def resume_thread(self, thread_id: str, opts: Optional[StartOpts] = None) -> ThreadHandle:
        """Resumes an existing Claude session via its identifier."""

        merged = self._merge_start_opts(opts)
        state = ClaudeThreadState(opts=merged, session_id=thread_id, resume=True)
        return ClaudeThreadHandle(self, state)

    def get_thread_id(self, thread: ThreadHandle) -> Optional[str]:
        """Returns the session identifier from the handle."""

        return getattr(thread, "id", None)

    async def close(self, thread: ThreadHandle) -> None:
        """No-op close hook for parity with the interface."""

        await thread.close()

    async def _run_internal(
        self,
        thread: ClaudeThreadHandle,
        input: PromptInput,
        run_opts: Optional[RunOpts],
    ) -> RunResult:
        """Executes Claude to completion and returns the final assistant response."""

        sdk = self._ensure_sdk()
        state = thread.internal
        self._assert_idle(state)
        prompt = self._apply_output_schema_prompt(input, run_opts)
        options = self._build_options(state, run_opts)
        generator = sdk.query(prompt=prompt, options=options)
        active = self._register_run(state, generator, run_opts)
        last_text = ""
        final_message: Any = None
        try:
            async for message in generator:
                self._capture_session_id(state, thread, message)
                if isinstance(message, sdk.AssistantMessage):
                    last_text = _render_assistant_text(message, sdk)
                elif isinstance(message, sdk.ResultMessage):
                    final_message = message
            if active.aborted:
                raise _create_abort_error(active.abort_reason)
            structured = self._extract_structured_output(last_text, final_message, run_opts)
            usage = getattr(final_message, "usage", None)
            if isinstance(final_message, sdk.ResultMessage) and final_message.is_error:
                raise RuntimeError(_build_result_error_message(final_message))
            return RunResult(
                thread_id=state.session_id,
                text=last_text or getattr(final_message, "result", None),
                json=structured,
                usage=usage,
                raw=final_message,
            )
        finally:
            await self._cleanup_run(state, active)

    def _run_streamed_internal(
        self,
        thread: ClaudeThreadHandle,
        input: PromptInput,
        run_opts: Optional[RunOpts],
    ) -> EventIterator:
        """Streams Claude events and normalises them into shared event objects."""

        sdk = self._ensure_sdk()
        state = thread.internal
        self._assert_idle(state)
        prompt = self._apply_output_schema_prompt(input, run_opts)
        options = self._build_options(state, run_opts)

        async def _iterator() -> AsyncIterator[CoderStreamEvent]:
            generator = sdk.query(prompt=prompt, options=options)
            active = self._register_run(state, generator, run_opts)
            saw_done = False
            try:
                async for message in generator:
                    self._capture_session_id(state, thread, message)
                    for event in _normalize_claude_message(message, sdk):
                        yield event
                        if event["type"] == "done":
                            saw_done = True
                if active.aborted:
                    reason = active.abort_reason or "Interrupted"
                    yield _create_cancelled_event(reason)
                    yield _create_interrupted_error_event(reason)
                    return
                    if not saw_done:
                        yield {
                            "type": "done",
                            "provider": CODER_NAME,
                            "ts": now(),
                            "originalItem": {"reason": "completed"},
                        }
            finally:
                await self._cleanup_run(state, active)

        return _iterator()

    def _merge_start_opts(self, overrides: Optional[StartOpts]) -> StartOpts:
        """Merges default and per-call start options."""

        merged: StartOpts = {**self._default_opts}
        if overrides:
            merged.update(overrides)
        return merged

    def _build_options(self, state: ClaudeThreadState, run_opts: Optional[RunOpts]):
        """Translates StartOpts and RunOpts into ClaudeAgentOptions."""

        sdk = self._ensure_sdk()
        opts = state.opts
        permission_mode = opts.get("permissionMode")
        if not permission_mode and opts.get("yolo"):
            permission_mode = "bypassPermissions"
        include_partials = bool(run_opts.get("streamPartialMessages")) if run_opts else False
        add_dirs = list(opts.get("includeDirectories", [])) if opts.get("includeDirectories") else []
        options = sdk.ClaudeAgentOptions(
            cwd=opts.get("workingDirectory"),
            allowed_tools=list(opts.get("allowedTools", [])) if opts.get("allowedTools") else [],
            mcp_servers=opts.get("mcpServers") or {},
            continue_conversation=bool(opts.get("continue_")),
            resume=state.session_id if state.resume else None,
            fork_session=bool(opts.get("forkSession")),
            include_partial_messages=include_partials,
            model=opts.get("model"),
            permission_mode=permission_mode,
            permission_prompt_tool_name=opts.get("permissionPromptToolName"),
            add_dirs=add_dirs,
        )
        return options

    def _register_run(
        self,
        state: ClaudeThreadState,
        generator: AsyncIterator[Any],
        run_opts: Optional[RunOpts],
    ) -> ActiveClaudeRun:
        """Registers an active run and wires cancellation handlers."""

        def _on_abort(reason: Optional[str]) -> None:
            loop = _try_get_running_loop()
            if loop:
                loop.create_task(self._abort_active_run(state, reason or "Interrupted"))

        signal = run_opts.get("signal") if run_opts else None
        unsubscribe = link_signal(signal, _on_abort)
        active = ActiveClaudeRun(generator=generator, unsubscribe=unsubscribe)
        state.current_run = active
        return active

    async def _cleanup_run(self, state: ClaudeThreadState, active: ActiveClaudeRun) -> None:
        """Cleans up run bookkeeping once execution finishes."""

        active.unsubscribe()
        if state.current_run is active:
            state.current_run = None
        with contextlib.suppress(Exception):
            await active.generator.aclose()

    async def _abort_active_run(self, state: ClaudeThreadState, reason: Optional[str]) -> None:
        """Signals the currently active generator to stop."""

        active = state.current_run
        if not active or active.aborted:
            return
        active.aborted = True
        active.abort_reason = reason or "Interrupted"
        active.unsubscribe()
        with contextlib.suppress(Exception):
            await active.generator.aclose()

    def _capture_session_id(self, state: ClaudeThreadState, handle: ClaudeThreadHandle, message: Any) -> None:
        """Updates the stored session identifier when Claude reports a new value."""

        session_id = getattr(message, "session_id", None)
        if session_id and session_id != state.session_id:
            state.session_id = session_id
            state.resume = True
            handle.id = session_id

    def _assert_idle(self, state: ClaudeThreadState) -> None:
        """Ensures only one run executes at a time per thread."""

        if state.current_run is not None:
            raise RuntimeError("Claude adapter only supports one in-flight run per thread.")

    def _apply_output_schema_prompt(self, input: PromptInput, run_opts: Optional[RunOpts]) -> str:
        """Appends structured output instructions to the prompt when needed."""

        schema = run_opts.get("outputSchema") if run_opts else None
        if not schema:
            return _normalize_prompt(input)
        schema_snippet = json.dumps(schema, indent=2)
        instruction = (
            "You must respond with valid JSON that satisfies the provided schema. "
            "Do not include prose before or after the JSON.\n"
            f"Schema:\n{schema_snippet}"
        )
        if isinstance(input, str):
            return f"{input}\n\n{instruction}"
        system_message = {"role": "system", "content": instruction}
        return _normalize_prompt([system_message, *list(input)])

    def _extract_structured_output(
        self,
        assistant_text: str,
        result_message: Any,
        run_opts: Optional[RunOpts],
    ) -> Any:
        """Derives structured output from the assistant text or result payload."""

        if not run_opts or not run_opts.get("outputSchema"):
            if result_message is not None:
                return getattr(result_message, "result", None)
            return None
        payload = getattr(result_message, "result", None)
        if payload:
            return payload
        return _extract_json_payload(assistant_text)

    def _ensure_sdk(self) -> _ClaudeSdkBindings:
        """Returns the SDK bindings or raises if unavailable."""

        if self._sdk:
            return self._sdk
        if self._sdk_error:
            raise ClaudeSdkNotAvailableError(str(self._sdk_error)) from self._sdk_error
        raise ClaudeSdkNotAvailableError("claude-agent-sdk bindings are missing")


def create_adapter(defaults: Optional[StartOpts] = None) -> HeadlessCoder:
    """Factory entry point consumed by the registry."""

    return ClaudeAdapter(defaults)


create_adapter.coder_name = CODER_NAME  # type: ignore[attr-defined]


def _normalize_prompt(input: PromptInput) -> str:
    """Normalises chat-style prompts into the Claude CLI string format."""

    if isinstance(input, str):
        return input
    parts = [f"{msg['role']}: {msg['content']}" for msg in input]
    return "\n".join(parts)


def _render_assistant_text(message: Any, sdk: _ClaudeSdkBindings) -> str:
    """Extracts plain text from assistant content blocks."""

    text_blocks = []
    for block in getattr(message, "content", []):
        if isinstance(block, sdk.TextBlock):
            text_blocks.append(block.text)
        elif isinstance(block, sdk.ThinkingBlock):
            # Thinking blocks are omitted from the final message body.
            continue
        elif isinstance(block, sdk.ToolResultBlock):
            if isinstance(block.content, str):
                text_blocks.append(block.content)
        elif isinstance(block, sdk.ToolUseBlock):
            continue
    return "\n".join(text_blocks).strip()


def _normalize_claude_message(message: Any, sdk: _ClaudeSdkBindings) -> list[CoderStreamEvent]:
    """Maps Claude message dataclasses into shared stream events."""

    ts = now()
    events: list[CoderStreamEvent] = []
    if isinstance(message, sdk.AssistantMessage):
        text = _render_assistant_text(message, sdk)
        if text:
            events.append(
                {
                    "type": "message",
                    "provider": CODER_NAME,
                    "role": "assistant",
                    "text": text,
                    "ts": ts,
                    "originalItem": message,
                }
            )
        for block in getattr(message, "content", []):
            if isinstance(block, sdk.ToolUseBlock):
                events.append(
                    {
                        "type": "tool_use",
                        "provider": CODER_NAME,
                        "name": block.name,
                        "callId": block.id,
                        "args": block.input,
                        "ts": ts,
                        "originalItem": block,
                    }
                )
            elif isinstance(block, sdk.ToolResultBlock):
                events.append(
                    {
                        "type": "tool_result",
                        "provider": CODER_NAME,
                        "name": block.tool_use_id,
                        "callId": block.tool_use_id,
                        "result": block.content,
                        "ts": ts,
                        "originalItem": block,
                    }
                )
        return events
    if isinstance(message, sdk.ResultMessage):
        if message.is_error:
            events.append(
                {
                    "type": "error",
                    "provider": CODER_NAME,
                    "message": _build_result_error_message(message),
                    "ts": ts,
                    "originalItem": message,
                }
            )
            return events
        if message.usage:
            events.append(
                {
                    "type": "usage",
                    "provider": CODER_NAME,
                    "stats": message.usage,
                    "ts": ts,
                    "originalItem": message,
                }
            )
        events.append({"type": "done", "provider": CODER_NAME, "ts": ts, "originalItem": message})
        return events
    if isinstance(message, sdk.SystemMessage):
        label = getattr(message, "subtype", "system")
        session_id = message.data.get("session_id") if isinstance(message.data, dict) else None
        events.append(
            {
                "type": "init" if "session" in label else "progress",
                "provider": CODER_NAME,
                "threadId": session_id,
                "label": label,
                "ts": ts,
                "originalItem": message,
            }
        )
        return events
    if isinstance(message, sdk.StreamEvent):
        return _normalize_stream_event_dict(message.event)
    return [
        {
            "type": "progress",
            "provider": CODER_NAME,
            "label": getattr(message, "__class__", type("", (), {})).__name__,
            "ts": ts,
            "originalItem": message,
        }
    ]


def _normalize_stream_event_dict(event: dict[str, Any]) -> list[CoderStreamEvent]:
    """Normalises raw stream events into the shared representation."""

    event = event or {}
    event_type = str(event.get("type") or event.get("label") or "claude.event").lower()
    ts = now()
    if "partial" in event_type:
        return [
            {
                "type": "message",
                "provider": CODER_NAME,
                "role": "assistant",
                "text": event.get("text") or event.get("content"),
                "delta": True,
                "ts": ts,
                "originalItem": event,
            }
        ]
    if "assistant" in event_type:
        return [
            {
                "type": "message",
                "provider": CODER_NAME,
                "role": "assistant",
                "text": event.get("text") or event.get("content"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if "tool_use" in event_type:
        return [
            {
                "type": "tool_use",
                "provider": CODER_NAME,
                "name": event.get("name", "tool"),
                "callId": event.get("id"),
                "args": event.get("input"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if "tool_result" in event_type:
        return [
            {
                "type": "tool_result",
                "provider": CODER_NAME,
                "name": event.get("name", "tool"),
                "callId": event.get("tool_use_id") or event.get("id"),
                "result": event.get("output"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if "error" in event_type:
        return [
            {
                "type": "error",
                "provider": CODER_NAME,
                "message": event.get("message", "Claude run failed"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if event_type in ("result", "completed", "final"):
        return [{"type": "done", "provider": CODER_NAME, "ts": ts, "originalItem": event}]
    return [
        {
            "type": "progress",
            "provider": CODER_NAME,
            "label": event.get("type") or event.get("label") or "claude.event",
            "ts": ts,
            "originalItem": event,
        }
    ]


def _extract_json_payload(text: Optional[str]) -> Any:
    """Parses JSON fenced code blocks from the assistant response when possible."""

    if not text:
        return None
    candidate = text.strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None


def _create_abort_error(reason: Optional[str]) -> RuntimeError:
    """Creates an abort-shaped runtime error."""

    error = RuntimeError(reason or "Operation was interrupted")
    error.code = "interrupted"  # type: ignore[attr-defined]
    return error


def _create_cancelled_event(reason: str) -> CoderStreamEvent:
    """Builds a cancelled stream event."""

    return {
        "type": "cancelled",
        "provider": CODER_NAME,
        "ts": now(),
        "originalItem": {"reason": reason},
    }


def _create_interrupted_error_event(reason: str) -> CoderStreamEvent:
    """Builds the error payload associated with cancellations."""

    return {
        "type": "error",
        "provider": CODER_NAME,
        "code": "interrupted",
        "message": reason,
        "ts": now(),
        "originalItem": {"reason": reason},
    }


def _build_result_error_message(result: Any) -> str:
    """Formats Claude result errors for diagnostics."""

    summary = getattr(result, "result", None) or getattr(result, "subtype", "Claude run failed")
    return f"Claude run failed: {summary}"


def _try_get_running_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Returns the running loop when available."""

    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
