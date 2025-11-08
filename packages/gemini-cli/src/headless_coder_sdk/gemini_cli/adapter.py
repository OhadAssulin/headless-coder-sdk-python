"""Gemini CLI adapter implementing the headless coder interface."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Sequence

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
CODER_NAME = "gemini"
SOFT_KILL_DELAY = 0.25
HARD_KILL_DELAY = 1.5
STRUCTURED_OUTPUT_SUFFIX = (
    "Respond with JSON that matches the provided schema. Do not include explanatory text outside the JSON."
)

ProcessRunner = Callable[
    [str, Sequence[str], dict[str, str], Optional[str]],
    Awaitable[asyncio.subprocess.Process],
]


@dataclass
class ActiveRun:
    """Tracks an in-flight Gemini CLI invocation to coordinate cancellation."""

    process: asyncio.subprocess.Process
    unsubscribe: Callable[[], None]
    aborted: bool = False
    abort_reason: Optional[str] = None
    soft_kill_handle: Optional[asyncio.TimerHandle] = None
    hard_kill_handle: Optional[asyncio.TimerHandle] = None


@dataclass
class GeminiThreadState:
    """Mutable state captured inside every thread handle."""

    opts: StartOpts
    thread_id: Optional[str] = None
    current_run: Optional[ActiveRun] = None


class GeminiThreadHandle(ThreadHandle):
    """Thread handle returned by the Gemini adapter."""

    def __init__(self, adapter: "GeminiAdapter", state: GeminiThreadState) -> None:
        """Initialises the handle wrapper.

        Args:
            adapter: Owning adapter instance.
            state: Mutable state captured for the thread lifecycle.
        """
        self._adapter = adapter
        self.internal = state
        self.provider = CODER_NAME
        self.id = state.thread_id

    async def run(self, input: PromptInput, opts: Optional[RunOpts] = None) -> RunResult:
        """Executes a blocking Gemini turn."""
        return await self._adapter._run_internal(self, input, opts)

    def run_streamed(self, input: PromptInput, opts: Optional[RunOpts] = None) -> EventIterator:
        """Streams Gemini events as they are produced."""
        return self._adapter._run_streamed_internal(self, input, opts)

    async def interrupt(self, reason: Optional[str] = None) -> None:
        """Propagates an interrupt request to the adapter."""
        self._adapter._abort_child(self.internal, reason or "Interrupted")

    async def close(self) -> None:
        """Closes the underlying Gemini process if one is active."""
        self._adapter._abort_child(self.internal, "Thread closed")


class GeminiAdapter(HeadlessCoder):
    """Adapter that shells out to the Gemini CLI headless runner."""

    def __init__(
        self,
        default_opts: Optional[StartOpts] = None,
        process_runner: Optional[ProcessRunner] = None,
    ) -> None:
        """Creates a Gemini adapter.

        Args:
            default_opts: Defaults applied to every start/resume call unless overridden.
            process_runner: Optional injection point for spawning the CLI (used in tests).
        """
        self._default_opts = default_opts or {}
        self._process_runner = process_runner or _spawn_process

    async def start_thread(self, opts: Optional[StartOpts] = None) -> ThreadHandle:
        """Starts a new stateless Gemini thread handle."""
        merged = self._merge_start_opts(opts)
        state = GeminiThreadState(opts=merged)
        return GeminiThreadHandle(self, state)

    async def resume_thread(self, thread_id: str, opts: Optional[StartOpts] = None) -> ThreadHandle:
        """Creates a handle that conceptually resumes a logical Gemini session."""
        merged = self._merge_start_opts(opts)
        state = GeminiThreadState(opts=merged, thread_id=thread_id)
        return GeminiThreadHandle(self, state)

    def get_thread_id(self, thread: ThreadHandle) -> Optional[str]:
        """Returns the most recent Gemini session identifier."""
        return getattr(thread, "id", None)

    async def close(self, thread: ThreadHandle) -> None:
        """Invokes the handle level cleanup hook."""
        await thread.close()

    async def _run_internal(
        self,
        thread: GeminiThreadHandle,
        input: PromptInput,
        run_opts: Optional[RunOpts],
    ) -> RunResult:
        """Executes the Gemini CLI to completion for non-streaming runs."""
        state = thread.internal
        self._assert_idle(state)
        prompt = self._apply_output_schema_prompt(input, run_opts)
        process, active = await self._spawn_process(state, prompt, "json", run_opts)
        stdout, stderr = await process.communicate()
        try:
            if active.aborted:
                raise _create_abort_error(active.abort_reason)
            if process.returncode not in (0, None):
                raise RuntimeError(_format_process_error("gemini", process.returncode, stderr))
            payload = _parse_gemini_json(stdout.decode("utf-8"))
            structured = _maybe_extract_structured(payload, run_opts)
            return RunResult(
                thread_id=payload.get("session_id") or state.thread_id,
                text=_extract_response_text(payload),
                json=structured,
                usage=payload.get("stats"),
                raw=payload,
            )
        finally:
            self._cleanup_run(state, active)

    def _run_streamed_internal(
        self,
        thread: GeminiThreadHandle,
        input: PromptInput,
        run_opts: Optional[RunOpts],
    ) -> EventIterator:
        """Runs Gemini in streaming mode, yielding normalised events."""
        state = thread.internal
        self._assert_idle(state)
        prompt = self._apply_output_schema_prompt(input, run_opts)

        async def _iterator() -> AsyncIterator[CoderStreamEvent]:
            process, active = await self._spawn_process(state, prompt, "stream-json", run_opts)
            try:
                assert process.stdout is not None
                async for line in _read_lines(process):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        LOGGER.debug("Skipping malformed Gemini line", extra={"line": line})
                        continue
                    for mapped in _normalize_gemini_event(event):
                        yield mapped
                await process.wait()
                if active.aborted:
                    reason = active.abort_reason or "Interrupted"
                    yield _create_cancelled_event(reason)
                    yield _create_interrupted_error_event(reason)
                    return
                if process.returncode not in (0, None):
                    raise RuntimeError(_format_process_error("gemini", process.returncode, None))
            finally:
                self._cleanup_run(state, active)

        return _iterator()

    def _merge_start_opts(self, overrides: Optional[StartOpts]) -> StartOpts:
        """Merges adapter defaults with per-call overrides."""
        merged: StartOpts = {**self._default_opts}
        if overrides:
            merged.update(overrides)
        return merged

    async def _spawn_process(
        self,
        state: GeminiThreadState,
        prompt: str,
        mode: str,
        run_opts: Optional[RunOpts],
    ) -> tuple[asyncio.subprocess.Process, ActiveRun]:
        """Spawns the Gemini CLI process, wiring cancellation before returning."""
        binary = _gemini_path(state.opts.get("geminiBinaryPath"))
        args = _build_gemini_args(state.opts, prompt, mode)
        env = os.environ.copy()
        if run_opts and run_opts.get("extraEnv"):
            env.update(run_opts["extraEnv"])
        process = await self._process_runner(binary, args, env, state.opts.get("workingDirectory"))
        signal = run_opts.get("signal") if run_opts else None
        active = self._register_run(state, process, signal)
        return process, active

    def _register_run(
        self,
        state: GeminiThreadState,
        process: asyncio.subprocess.Process,
        signal: Optional[Any],
    ) -> ActiveRun:
        """Registers bookkeeping for the supplied process and links cancellation."""
        unsubscribe = link_signal(signal, lambda reason: self._abort_child(state, reason))
        active = ActiveRun(process=process, unsubscribe=unsubscribe)
        state.current_run = active
        return active

    def _cleanup_run(self, state: GeminiThreadState, active: ActiveRun) -> None:
        """Cleans up references, timers, and signal subscriptions."""
        active.unsubscribe()
        self._cancel_kill_timers(active)
        if state.current_run is active:
            state.current_run = None

    def _abort_child(self, state: GeminiThreadState, reason: Optional[str]) -> None:
        """Attempts to cooperatively stop the currently running CLI process."""
        active = state.current_run
        if not active or active.aborted:
            return
        active.aborted = True
        active.abort_reason = reason or "Interrupted"
        process = active.process
        try:
            process.terminate()
        except ProcessLookupError:
            return
        loop = _try_get_running_loop()
        if loop:
            active.soft_kill_handle = loop.call_later(SOFT_KILL_DELAY, _safe_terminate, process)
            active.hard_kill_handle = loop.call_later(HARD_KILL_DELAY, _safe_kill, process)

    def _cancel_kill_timers(self, active: ActiveRun) -> None:
        """Cancels any pending termination timers spawned during abort handling."""
        for handle_attr in ("soft_kill_handle", "hard_kill_handle"):
            handle = getattr(active, handle_attr)
            if handle:
                handle.cancel()
                setattr(active, handle_attr, None)

    def _assert_idle(self, state: GeminiThreadState) -> None:
        """Ensures only one Gemini run happens at a time for the handle."""
        if state.current_run is not None:
            raise RuntimeError("Gemini adapter only supports one in-flight run per thread.")

    def _apply_output_schema_prompt(self, input: PromptInput, run_opts: Optional[RunOpts]) -> str:
        """Appends structured output instructions whenever the caller supplies a schema."""
        schema = run_opts.get("outputSchema") if run_opts else None
        if not schema:
            return _normalize_prompt(input)
        schema_snippet = json.dumps(schema, indent=2)
        instruction = f"{STRUCTURED_OUTPUT_SUFFIX}\nSchema:\n{schema_snippet}"
        if isinstance(input, str):
            return f"{input}\n\n{instruction}"
        return _normalize_prompt(([{"role": "system", "content": instruction}] + list(input)))


def create_adapter(defaults: Optional[StartOpts] = None) -> HeadlessCoder:
    """Factory entry point exposed to callers and the registry."""

    return GeminiAdapter(defaults)


create_adapter.coder_name = CODER_NAME  # type: ignore[attr-defined]


async def _spawn_process(
    binary: str,
    args: Sequence[str],
    env: dict[str, str],
    cwd: Optional[str],
) -> asyncio.subprocess.Process:
    """Spawns the Gemini CLI with piped stdout/stderr."""
    return await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )


def _normalize_prompt(input: PromptInput) -> str:
    """Serialises the prompt input into Gemini's textual format."""
    if isinstance(input, str):
        return input
    parts = [f"{msg['role']}: {msg['content']}" for msg in input]
    return "\n".join(parts)


def _gemini_path(override: Optional[str]) -> str:
    """Returns the binary path, defaulting to the `gemini` executable on PATH."""
    return override or "gemini"


def _build_gemini_args(opts: StartOpts, prompt: str, mode: str) -> list[str]:
    """Builds CLI arguments for the Gemini invocation."""
    args = ["--output-format", mode, "--prompt", prompt]
    if opts.get("model"):
        args.extend(["--model", str(opts["model"])])
    include_dirs = opts.get("includeDirectories")
    if include_dirs:
        args.extend(["--include-directories", ",".join(include_dirs)])
    if opts.get("yolo"):
        args.append("--yolo")
    return args


def _parse_gemini_json(output: str) -> dict[str, Any]:
    """Parses Gemini CLI JSON output with a permissive fallback to raw text."""
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"response": output.strip()}


def _maybe_extract_structured(payload: dict[str, Any], run_opts: Optional[RunOpts]) -> Any:
    """Extracts structured data from the payload when a schema was requested."""
    if not run_opts or not run_opts.get("outputSchema"):
        return payload.get("json")
    structured = payload.get("json")
    if structured is not None:
        return structured
    return _extract_json_payload(_extract_response_text(payload))


def _extract_response_text(payload: dict[str, Any]) -> str:
    """Returns the textual assistant response from the payload."""
    if "response" in payload:
        return str(payload["response"])
    if "text" in payload:
        return str(payload["text"])
    return ""


def _extract_json_payload(text: Optional[str]) -> Any:
    """Attempts to parse a JSON object embedded inside a text response."""
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


def _normalize_gemini_event(event: dict[str, Any]) -> list[CoderStreamEvent]:
    """Maps Gemini CLI streaming events into the shared wire format."""
    ev_type = event.get("type")
    ts = now()
    if ev_type == "init":
        return [
            {
                "type": "init",
                "provider": CODER_NAME,
                "threadId": event.get("session_id"),
                "model": event.get("model"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "message":
        return [
            {
                "type": "message",
                "provider": CODER_NAME,
                "role": event.get("role", "assistant"),
                "text": event.get("content"),
                "delta": bool(event.get("delta")),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "tool_use":
        return [
            {
                "type": "tool_use",
                "provider": CODER_NAME,
                "name": event.get("tool_name", "tool"),
                "callId": event.get("call_id"),
                "args": event.get("args"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "tool_result":
        return [
            {
                "type": "tool_result",
                "provider": CODER_NAME,
                "name": event.get("tool_name", "tool"),
                "callId": event.get("call_id"),
                "result": event.get("result"),
                "exitCode": event.get("exit_code"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "error":
        return [
            {
                "type": "error",
                "provider": CODER_NAME,
                "message": event.get("message", "gemini error"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "result":
        events: list[CoderStreamEvent] = []
        if event.get("stats"):
            events.append(
                {
                    "type": "usage",
                    "provider": CODER_NAME,
                    "stats": event.get("stats"),
                    "ts": ts,
                    "originalItem": event,
                }
            )
        events.append({"type": "done", "provider": CODER_NAME, "ts": ts, "originalItem": event})
        return events
    return [
        {
            "type": "progress",
            "provider": CODER_NAME,
            "label": str(ev_type or "gemini.event"),
            "ts": ts,
            "originalItem": event,
        }
    ]


def _create_cancelled_event(reason: str) -> CoderStreamEvent:
    """Builds the canonical cancelled stream event for Gemini."""
    return {
        "type": "cancelled",
        "provider": CODER_NAME,
        "ts": now(),
        "originalItem": {"reason": reason},
    }


def _create_interrupted_error_event(reason: str) -> CoderStreamEvent:
    """Builds the canonical interruption error event for Gemini."""
    return {
        "type": "error",
        "provider": CODER_NAME,
        "code": "interrupted",
        "message": reason,
        "ts": now(),
        "originalItem": {"reason": reason},
    }


def _create_abort_error(reason: Optional[str]) -> RuntimeError:
    """Creates a runtime error mirroring AbortError semantics."""
    error = RuntimeError(reason or "Operation was interrupted")
    setattr(error, "code", "interrupted")
    return error


def _format_process_error(name: str, code: Optional[int], stderr: Optional[bytes]) -> str:
    """Formats CLI failures with stderr snippets when available."""
    base = f"{name} exited with code {code}"
    if stderr:
        tail = stderr.decode("utf-8", errors="ignore").strip()
        if tail:
            return f"{base}: {tail}"
    return base


async def _read_lines(process: asyncio.subprocess.Process) -> AsyncIterator[str]:
    """Streams stdout lines from the spawned process."""
    reader = process.stdout
    assert reader is not None
    while True:
        line = await reader.readline()
        if not line:
            break
        yield line.decode("utf-8", errors="ignore")


def _safe_terminate(process: asyncio.subprocess.Process) -> None:
    """Attempts to terminate the process, ignoring races when it already exited."""
    try:
        process.terminate()
    except ProcessLookupError:
        return


def _safe_kill(process: asyncio.subprocess.Process) -> None:
    """Escalates to SIGKILL when cooperative termination fails."""
    try:
        process.kill()
    except ProcessLookupError:
        return


def _try_get_running_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Returns the current running loop when present."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
