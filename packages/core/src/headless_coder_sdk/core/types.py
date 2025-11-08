"""Shared types and protocol definitions for the headless coder Python SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional, Protocol, Sequence, Union
from typing_extensions import Literal, TypedDict, runtime_checkable

Provider = Literal["codex", "gemini", "claude"]
"""Provider discriminant used throughout the SDK."""

AdapterName = str
"""Identifier attached to each adapter factory."""

CoderType = Provider
"""Alias that mirrors the TypeScript naming used across docs."""

SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
"""Sandbox enforcement modes propagated to providers."""


class PromptMessage(TypedDict):
    """Single chat message entry used when prompting coders."""

    role: Literal["user", "assistant", "system"]
    content: str


PromptInput = Union[str, Sequence[PromptMessage]]
"""Payload accepted by the adapters when running a turn."""


class CancellationSignalProtocol(Protocol):
    """Light-weight protocol describing the surface area of cancellation signals."""

    @property
    def aborted(self) -> bool:
        """Whether the signal has already been triggered."""

    @property
    def reason(self) -> Optional[str]:
        """Optional explanation describing why the signal fired."""

    def add_listener(self, callback: Callable[[Optional[str]], None]) -> Callable[[], None]:
        """Registers a callback that fires when the signal aborts and returns an unsubscribe callable."""


class StartOpts(TypedDict, total=False):
    """Options available when starting or resuming provider threads."""

    model: str
    workingDirectory: str
    sandboxMode: SandboxMode
    skipGitRepoCheck: bool
    codexExecutablePath: str
    allowedTools: Sequence[str]
    mcpServers: dict[str, Any]
    continue_: bool
    resume: str
    forkSession: bool
    geminiBinaryPath: str
    includeDirectories: Sequence[str]
    yolo: bool
    permissionMode: str
    permissionPromptToolName: str


class RunOpts(TypedDict, total=False):
    """Per-run execution modifiers shared across adapters."""

    outputSchema: dict[str, Any]
    streamPartialMessages: bool
    extraEnv: dict[str, str]
    signal: CancellationSignalProtocol


@dataclass
class RunResult:
    """Unified run result returned by every adapter."""

    thread_id: Optional[str] = None
    text: Optional[str] = None
    json: Any = None
    usage: Any = None
    raw: Any = None

    @property
    def threadId(self) -> Optional[str]:  # noqa: N802 (preserve TS casing for parity)
        """Alias that mirrors the camelCase identifier used by the TypeScript SDK."""

        return self.thread_id


class CoderStreamEvent(TypedDict, total=False):
    """Normalised streaming event emitted by adapters."""

    type: Literal[
        "init",
        "message",
        "tool_use",
        "tool_result",
        "progress",
        "permission",
        "file_change",
        "plan_update",
        "usage",
        "error",
        "cancelled",
        "done",
    ]
    provider: Provider
    role: Literal["assistant", "user", "system"]
    text: Optional[str]
    delta: bool
    model: Optional[str]
    label: Optional[str]
    detail: Optional[str]
    name: Optional[str]
    callId: Optional[str]
    args: Any
    result: Any
    exitCode: Optional[int]
    ts: int
    threadId: Optional[str]
    stats: Any
    code: Optional[str]
    message: Optional[str]
    originalItem: Any


EventIterator = AsyncIterator[CoderStreamEvent]
"""Async iterator yielded by streaming runs."""


@runtime_checkable
class ThreadHandle(Protocol):
    """Runtime contract implemented by each adapter-specific thread handle."""

    provider: Provider
    id: Optional[str]
    internal: Any

    async def run(self, input: PromptInput, opts: Optional[RunOpts] = None) -> RunResult:
        """Executes a full turn and returns once the provider finishes."""

    def run_streamed(self, input: PromptInput, opts: Optional[RunOpts] = None) -> EventIterator:
        """Streams provider events for the supplied prompt."""

    async def interrupt(self, reason: Optional[str] = None) -> None:
        """Cooperatively aborts the in-flight run if the provider supports it."""

    async def close(self) -> None:
        """Cleans up any provider-specific resources associated with the handle."""


@runtime_checkable
class HeadlessCoder(Protocol):
    """Protocol implemented by every adapter entry point."""

    async def start_thread(self, opts: Optional[StartOpts] = None) -> ThreadHandle:
        """Starts a new conversation with the provider."""

    async def resume_thread(self, thread_id: str, opts: Optional[StartOpts] = None) -> ThreadHandle:
        """Rehydrates a provider conversation using its identifier."""

    def get_thread_id(self, thread: ThreadHandle) -> Optional[str]:
        """Extracts the provider identifier from the supplied handle."""

    async def close(self, thread: ThreadHandle) -> None:
        """Closes the supplied handle when the provider exposes that concept."""


class AdapterFactory(Protocol):
    """Callable factory used to construct adapters for registration."""

    coder_name: AdapterName

    def __call__(self, defaults: Optional[StartOpts] = None) -> HeadlessCoder:
        """Builds an adapter while optionally applying default start options."""


def now() -> int:
    """Returns the current timestamp in milliseconds for parity with the TypeScript SDK."""

    import time

    return int(time.time() * 1000)
