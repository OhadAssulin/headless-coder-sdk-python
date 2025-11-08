"""Codex CLI adapter providing the headless coder SDK surface."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
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
CODER_NAME = "codex"
SOFT_KILL_DELAY = 0.25
HARD_KILL_DELAY = 1.5
STDERR_BUFFER_LIMIT = 64 * 1024

ProcessRunner = Callable[
    [str, Sequence[str], dict[str, str], Optional[str]],
    Awaitable[asyncio.subprocess.Process],
]


@dataclass
class CodexThreadState:
    """Mutable state tracked for every Codex handle."""

    options: dict[str, Any]
    codex_executable_path: Optional[str] = None
    id: Optional[str] = None
    current_run: Optional["ActiveRun"] = None


@dataclass
class ActiveRun:
    """Tracks in-flight process metadata for cooperative cancellation."""

    process: asyncio.subprocess.Process
    unsubscribe: Callable[[], None]
    stderr: "StderrCollector"
    aborted: bool = False
    abort_reason: Optional[str] = None
    soft_kill_handle: Optional[asyncio.TimerHandle] = None
    hard_kill_handle: Optional[asyncio.TimerHandle] = None


class CodexThreadHandle(ThreadHandle):
    """Thread handle returned when starting or resuming Codex sessions."""

    def __init__(self, adapter: "CodexAdapter", state: CodexThreadState) -> None:
        """Initialises the thread handle wrapper."""
        self._adapter = adapter
        self.internal = state
        self.provider = CODER_NAME
        self.id = state.id

    async def run(self, input: PromptInput, opts: Optional[RunOpts] = None) -> RunResult:
        """Executes a blocking Codex run."""

        return await self._adapter._run_internal(self, input, opts)

    def run_streamed(self, input: PromptInput, opts: Optional[RunOpts] = None) -> EventIterator:
        """Streams Codex events as they are emitted by the CLI."""

        return self._adapter._run_streamed_internal(self, input, opts)

    async def interrupt(self, reason: Optional[str] = None) -> None:
        """Attempts to abort the current process when supported."""

        await self._adapter._abort_active_run(self.internal, reason or "Interrupted")

    async def close(self) -> None:
        """Codex threads have no additional cleanup semantics."""

        return None


class CodexAdapter(HeadlessCoder):
    """Adapter that shells out to the Codex CLI binary."""

    def __init__(
        self,
        default_opts: Optional[StartOpts] = None,
        process_runner: Optional[ProcessRunner] = None,
    ) -> None:
        """Creates a Codex adapter with optional defaults and runner injection."""
        self._default_opts = default_opts or {}
        self._process_runner = process_runner or _spawn_process

    async def start_thread(self, opts: Optional[StartOpts] = None) -> ThreadHandle:
        """Creates a new logical Codex thread."""

        merged = self._merge_start_opts(opts)
        state = CodexThreadState(
            options=self._extract_thread_options(merged),
            codex_executable_path=merged.get("codexExecutablePath"),
        )
        return CodexThreadHandle(self, state)

    async def resume_thread(self, thread_id: str, opts: Optional[StartOpts] = None) -> ThreadHandle:
        """Rehydrates an existing Codex thread by identifier."""

        merged = self._merge_start_opts(opts)
        state = CodexThreadState(
            options=self._extract_thread_options(merged),
            codex_executable_path=merged.get("codexExecutablePath"),
            id=thread_id,
        )
        return CodexThreadHandle(self, state)

    def get_thread_id(self, thread: ThreadHandle) -> Optional[str]:
        """Returns the thread identifier if known."""

        return getattr(thread, "id", None)

    async def close(self, thread: ThreadHandle) -> None:
        """Threads do not need extra cleanup besides interruption."""

        await thread.close()

    async def _run_internal(
        self,
        thread: CodexThreadHandle,
        input: PromptInput,
        run_opts: Optional[RunOpts],
    ) -> RunResult:
        """Executes Codex to completion and returns the final assistant response."""

        state = thread.internal
        self._assert_idle(state)
        prompt = _normalize_prompt(input)
        async with _schema_file(run_opts) as schema_path:
            process, active = await self._spawn_process(state, prompt, schema_path, run_opts)
            stderr_closed = False
            try:
                summary = await _consume_codex_events(
                    _iterate_process_lines(process),
                    run_opts,
                )
                exit_code = await process.wait()
                if summary.thread_id:
                    state.id = summary.thread_id
                    thread.id = summary.thread_id
                if not active.aborted and exit_code not in (0, None):
                    await active.stderr.close()
                    stderr_closed = True
                    raise RuntimeError(_format_process_error(exit_code, active.stderr.read()))
                return RunResult(
                    thread_id=state.id,
                    text=summary.final_response or None,
                    json=summary.structured_output,
                    usage=summary.usage,
                    raw=summary.raw,
                )
            finally:
                if not stderr_closed:
                    await active.stderr.close()
                await self._cleanup_run(state, active)

    def _run_streamed_internal(
        self,
        thread: CodexThreadHandle,
        input: PromptInput,
        run_opts: Optional[RunOpts],
    ) -> EventIterator:
        """Streams Codex CLI events mapped into the shared schema."""

        state = thread.internal
        self._assert_idle(state)
        prompt = _normalize_prompt(input)

        async def _iterator() -> AsyncIterator[CoderStreamEvent]:
            async with _schema_file(run_opts) as schema_path:
                process, active = await self._spawn_process(state, prompt, schema_path, run_opts)
                saw_done = False
                stderr_closed = False
                try:
                    async for raw_event in _iterate_process_lines(process):
                        for event in _normalize_codex_event(raw_event):
                            if event["type"] == "init" and event.get("threadId"):
                                state.id = event["threadId"]
                                thread.id = state.id
                            if event["type"] == "done":
                                saw_done = True
                            yield event
                    exit_code = await process.wait()
                    if active.aborted:
                        await active.stderr.close()
                        stderr_closed = True
                        reason = active.abort_reason or "Interrupted"
                        yield _create_cancelled_event(reason)
                        yield _create_interrupted_error_event(reason)
                        return
                    if exit_code not in (0, None):
                        await active.stderr.close()
                        stderr_closed = True
                        message = _format_process_error(exit_code, active.stderr.read())
                        yield _create_worker_exit_error_event(message)
                        return
                    if not saw_done:
                        yield {
                            "type": "done",
                            "provider": CODER_NAME,
                            "ts": now(),
                            "originalItem": {"reason": "completed"},
                        }
                finally:
                    if not stderr_closed:
                        await active.stderr.close()
                    await self._cleanup_run(state, active)

        return _iterator()

    async def _spawn_process(
        self,
        state: CodexThreadState,
        prompt: str,
        schema_path: Optional[str],
        run_opts: Optional[RunOpts],
    ) -> tuple[asyncio.subprocess.Process, ActiveRun]:
        """Spawns the Codex CLI process and wires cancellation handlers."""

        binary = state.codex_executable_path or "codex"
        args = _build_codex_args(state, schema_path)
        env = os.environ.copy()
        if run_opts and run_opts.get("extraEnv"):
            env.update(run_opts["extraEnv"])
        process = await self._process_runner(binary, args, env, None)
        if not process.stdin:
            raise RuntimeError("Codex process lacks stdin support")
        process.stdin.write(prompt.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        signal = run_opts.get("signal") if run_opts else None
        stderr = StderrCollector(process.stderr)
        unsubscribe = link_signal(signal, lambda reason: self._schedule_abort(state, reason))
        active = ActiveRun(process=process, unsubscribe=unsubscribe, stderr=stderr)
        state.current_run = active
        return process, active

    async def _cleanup_run(self, state: CodexThreadState, active: ActiveRun) -> None:
        """Cleans up resources once the CLI process finishes."""

        active.unsubscribe()
        self._cancel_kill_timers(active)
        if state.current_run is active:
            state.current_run = None

    def _schedule_abort(self, state: CodexThreadState, reason: Optional[str]) -> None:
        """Schedules an asynchronous abort when a cancellation signal fires."""

        loop = _try_get_running_loop()
        if loop:
            loop.create_task(self._abort_active_run(state, reason or "Interrupted"))

    async def _abort_active_run(self, state: CodexThreadState, reason: Optional[str]) -> None:
        """Terminates the running CLI process."""

        active = state.current_run
        if not active or active.aborted:
            return
        active.aborted = True
        active.abort_reason = reason or "Interrupted"
        active.unsubscribe()
        process = active.process
        try:
            process.terminate()
        except ProcessLookupError:
            return
        loop = _try_get_running_loop()
        if loop:
            active.soft_kill_handle = loop.call_later(SOFT_KILL_DELAY, _safe_terminate, process)
            active.hard_kill_handle = loop.call_later(HARD_KILL_DELAY, _safe_kill, process)
        with contextlib.suppress(Exception):
            await process.wait()

    def _cancel_kill_timers(self, active: ActiveRun) -> None:
        """Cancels scheduled termination timers when the process exits."""

        for handle in (active.soft_kill_handle, active.hard_kill_handle):
            if handle:
                handle.cancel()
        active.soft_kill_handle = None
        active.hard_kill_handle = None

    def _assert_idle(self, state: CodexThreadState) -> None:
        """Ensures only one Codex run executes per thread handle."""

        if state.current_run is not None:
            raise RuntimeError("Codex adapter only supports one in-flight run per thread.")

    def _merge_start_opts(self, overrides: Optional[StartOpts]) -> StartOpts:
        """Merges adapter defaults with per-call start options."""

        merged: StartOpts = {**self._default_opts}
        if overrides:
            merged.update(overrides)
        return merged

    def _extract_thread_options(self, merged: StartOpts) -> dict[str, Any]:
        """Extracts the subset of start options required for CLI flags."""

        return {
            "model": merged.get("model"),
            "sandboxMode": merged.get("sandboxMode"),
            "workingDirectory": merged.get("workingDirectory"),
            "skipGitRepoCheck": merged.get("skipGitRepoCheck"),
            "resume": merged.get("resume"),
        }


def create_adapter(defaults: Optional[StartOpts] = None) -> HeadlessCoder:
    """Factory entry point used by the registry."""

    return CodexAdapter(defaults)


create_adapter.coder_name = CODER_NAME  # type: ignore[attr-defined]


async def _spawn_process(
    binary: str,
    args: Sequence[str],
    env: dict[str, str],
    cwd: Optional[str],
) -> asyncio.subprocess.Process:
    """Spawns the Codex CLI executable."""

    return await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
    )


class StderrCollector:
    """Collects limited stderr output in the background."""

    def __init__(self, stream: Optional[Any], limit: int = STDERR_BUFFER_LIMIT) -> None:
        """Initialises the collector with the supplied stream and byte budget."""
        self._stream = stream
        self._limit = limit
        self._buffer = bytearray()
        self._task: Optional[asyncio.Task[None]] = None
        if stream is not None:
            self._task = asyncio.create_task(self._drain(stream))

    async def _drain(self, stream: Any) -> None:
        while True:
            chunk = await stream.read(1024)
            if not chunk:
                break
            if len(self._buffer) >= self._limit:
                continue
            slice_ = chunk[: self._limit - len(self._buffer)]
            self._buffer.extend(slice_)

    async def close(self) -> None:
        """Stops the collector and waits for pending reads."""

        if self._task:
            with contextlib.suppress(Exception):
                await self._task
            self._task = None

    def read(self) -> str:
        """Returns the buffered stderr as a UTF-8 string."""

        return self._buffer.decode("utf-8", errors="ignore").strip()


@contextlib.asynccontextmanager
async def _schema_file(run_opts: Optional[RunOpts]) -> AsyncIterator[Optional[str]]:
    """Materialises the JSON schema into a temporary file when requested."""

    if not run_opts or not run_opts.get("outputSchema"):
        yield None
        return
    payload = json.dumps(run_opts["outputSchema"], indent=2)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "schema.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(payload)
        yield path


def _build_codex_args(state: CodexThreadState, schema_path: Optional[str]) -> list[str]:
    """Constructs CLI arguments using the stored thread options."""

    options = state.options
    args = ["exec", "--experimental-json"]
    if options.get("model"):
        args.extend(["--model", str(options["model"])])
    if options.get("sandboxMode"):
        args.extend(["--sandbox", options["sandboxMode"]])
    if options.get("workingDirectory"):
        args.extend(["--cd", options["workingDirectory"]])
    if options.get("skipGitRepoCheck"):
        args.append("--skip-git-repo-check")
    if schema_path:
        args.extend(["--output-schema", schema_path])
    if state.id:
        args.extend(["resume", state.id])
    return args


async def _iterate_process_lines(process: asyncio.subprocess.Process) -> AsyncIterator[dict[str, Any]]:
    """Yields parsed JSON lines from the Codex CLI."""

    if not process.stdout:
        raise RuntimeError("Codex process lacks stdout")

    reader = process.stdout
    while True:
        line = await reader.readline()
        if not line:
            break
        decoded = line.decode("utf-8", errors="ignore").strip()
        if not decoded:
            continue
        try:
            yield json.loads(decoded)
        except json.JSONDecodeError:
            LOGGER.debug("Skipping malformed Codex line", extra={"line": decoded})
            continue


@dataclass
class CodexRunSummary:
    """Aggregated turn information returned after consuming CLI events."""

    thread_id: Optional[str] = None
    final_response: str = ""
    structured_output: Any = None
    usage: Any = None
    raw: Any = None


async def _consume_codex_events(
    events: AsyncIterator[dict[str, Any]],
    run_opts: Optional[RunOpts],
) -> CodexRunSummary:
    """Consumes Codex events and derives the final run summary."""

    summary = CodexRunSummary()
    structured = None
    async for event in events:
        event_type = event.get("type")
        if event_type == "thread.started":
            summary.thread_id = event.get("thread_id")
            continue
        if event_type == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                summary.final_response = item["text"]
            if structured is None:
                structured = _extract_structured_from_item(item)
            continue
        if event_type == "turn.completed":
            summary.usage = event.get("usage")
            if structured is None:
                structured = _extract_structured_from_turn(event)
            continue
        if event_type == "turn.failed":
            message = (event.get("error") or {}).get("message") or "Codex turn failed"
            raise RuntimeError(message)
        if event_type == "result":
            summary.raw = event
            continue
    if run_opts and run_opts.get("outputSchema") and structured is None:
        structured = _extract_json_payload(summary.final_response)
    summary.structured_output = structured
    return summary


def _extract_structured_from_item(item: dict[str, Any]) -> Any:
    """Extracts structured payloads from item-level responses."""

    return _first_structured(
        [
            item.get("output_json"),
            item.get("json"),
            item.get("output"),
            item.get("response_json"),
            item.get("structured"),
        ]
    )


def _extract_structured_from_turn(event: dict[str, Any]) -> Any:
    """Extracts structured payloads from turn-level responses."""

    return _first_structured(
        [
            event.get("output_json"),
            event.get("json"),
            event.get("result"),
            event.get("output"),
            event.get("response_json"),
        ]
    )


def _first_structured(candidates: list[Any]) -> Any:
    """Returns the first candidate that looks like structured data."""

    for candidate in candidates:
        if candidate and isinstance(candidate, (dict, list)):
            return candidate
    return None


def _normalize_prompt(input: PromptInput) -> str:
    """Normalises prompt inputs into the CLI-friendly format."""

    if isinstance(input, str):
        return input
    parts = [f"{msg['role'].upper()}: {msg['content']}" for msg in input]
    return "\n".join(parts)


def _normalize_codex_event(event: dict[str, Any]) -> list[CoderStreamEvent]:
    """Maps raw Codex events into the shared stream schema."""

    ev_type = str(event.get("type") or "codex.event")
    provider_event = ev_type.lower()
    ts = now()

    if ev_type == "thread.started":
        return [
            {
                "type": "init",
                "provider": CODER_NAME,
                "threadId": event.get("thread_id"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "turn.completed":
        return [
            {
                "type": "usage",
                "provider": CODER_NAME,
                "stats": event.get("usage"),
                "ts": ts,
                "originalItem": event,
            },
            {"type": "done", "provider": CODER_NAME, "ts": ts, "originalItem": event},
        ]
    if ev_type == "turn.failed":
        return [
            {
                "type": "error",
                "provider": CODER_NAME,
                "code": "turn.failed",
                "message": (event.get("error") or {}).get("message") or "Codex turn failed",
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "item.delta":
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            return [
                {
                    "type": "message",
                    "provider": CODER_NAME,
                    "role": "assistant",
                    "text": event.get("delta") or item.get("text"),
                    "delta": True,
                    "ts": ts,
                    "originalItem": event,
                }
            ]
        return [
            {
                "type": "progress",
                "provider": CODER_NAME,
                "label": f"item.delta:{item.get('type', 'event')}",
                "detail": event.get("delta"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "item.completed":
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            return [
                {
                    "type": "message",
                    "provider": CODER_NAME,
                    "role": "assistant",
                    "text": item.get("text"),
                    "ts": ts,
                    "originalItem": event,
                }
            ]
        return [
            {
                "type": "progress",
                "provider": CODER_NAME,
                "label": f"item.completed:{item.get('type', 'event')}",
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "tool_use":
        item = event.get("item") or {}
        return [
            {
                "type": "tool_use",
                "provider": CODER_NAME,
                "name": item.get("name", "tool"),
                "callId": item.get("id"),
                "args": item.get("input"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if ev_type == "tool_result":
        item = event.get("item") or {}
        return [
            {
                "type": "tool_result",
                "provider": CODER_NAME,
                "name": item.get("name", "tool"),
                "callId": item.get("id"),
                "result": item.get("output"),
                "exitCode": item.get("exit_code"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    if provider_event.startswith("permission"):
        decision = "granted" if provider_event.endswith("granted") else "denied"
        return [
            {
                "type": "permission",
                "provider": CODER_NAME,
                "decision": decision,
                "request": event.get("request") or event.get("permission"),
                "ts": ts,
                "originalItem": event,
            }
        ]
    return [
        {
            "type": "progress",
            "provider": CODER_NAME,
            "label": ev_type,
            "ts": ts,
            "originalItem": event,
        }
    ]


def _extract_json_payload(text: Optional[str]) -> Any:
    """Attempts to parse JSON embedded inside assistant responses."""

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


def _create_cancelled_event(reason: str) -> CoderStreamEvent:
    """Builds a cancelled event emitted when the user aborts."""

    return {
        "type": "cancelled",
        "provider": CODER_NAME,
        "ts": now(),
        "originalItem": {"reason": reason},
    }


def _create_interrupted_error_event(reason: str) -> CoderStreamEvent:
    """Builds the companion error event for cancellations."""

    return {
        "type": "error",
        "provider": CODER_NAME,
        "code": "interrupted",
        "message": reason,
        "ts": now(),
        "originalItem": {"reason": reason},
    }


def _safe_terminate(process: asyncio.subprocess.Process) -> None:
    """Attempts a graceful termination."""

    with contextlib.suppress(ProcessLookupError):
        process.terminate()


def _safe_kill(process: asyncio.subprocess.Process) -> None:
    """Escalates to SIGKILL if needed."""

    with contextlib.suppress(ProcessLookupError):
        process.kill()


def _try_get_running_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Returns the running loop when available."""

    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _format_process_error(exit_code: Optional[int], stderr: str) -> str:
    """Formats process exit errors."""

    base = f"Codex process exited with code {exit_code}"
    if stderr:
        return f"{base}: {stderr}"
    return base


def _create_worker_exit_error_event(message: str) -> CoderStreamEvent:
    """Creates an error event describing unexpected worker exits."""

    return {
        "type": "error",
        "provider": CODER_NAME,
        "code": "codex.worker_exit",
        "message": message,
        "ts": now(),
        "originalItem": {"reason": message},
    }
