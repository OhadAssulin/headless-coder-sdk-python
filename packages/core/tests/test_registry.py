"""Tests covering the adapter registry utilities."""

from __future__ import annotations

import pathlib
import sys
from typing import Any, Optional

import pytest

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = PACKAGE_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from headless_coder_sdk.core import (  # noqa: E402
    AdapterFactory,
    CoderType,
    PromptInput,
    RunOpts,
    RunResult,
    ThreadHandle,
    clear_registered_adapters,
    create_coder,
    register_adapter,
    unregister_adapter,
)


@pytest.fixture(autouse=True)
def _registry_isolation() -> None:
    """Ensures every test runs with a clean registry."""

    clear_registered_adapters()
    yield
    clear_registered_adapters()


class _DummyThread(ThreadHandle):
    """Minimal thread used to validate registry plumbing."""

    def __init__(self, provider: CoderType) -> None:
        self.provider = provider
        self.id = "dummy-thread"
        self.internal: dict[str, Any] = {}

    async def run(self, input: PromptInput, opts: Optional[RunOpts] = None) -> RunResult:
        return RunResult(thread_id=self.id, text=str(input))

    def run_streamed(self, input: PromptInput, opts: Optional[RunOpts] = None):  # pragma: no cover - unused
        async def _iterator():
            yield {
                "type": "message",
                "provider": self.provider,
                "text": str(input),
                "ts": 0,
            }

        return _iterator()

    async def interrupt(self, reason: Optional[str] = None) -> None:  # pragma: no cover
        return None

    async def close(self) -> None:  # pragma: no cover
        return None


class _DummyCoder:
    """Minimal adapter used for registry validation."""

    def __init__(self, defaults: Optional[dict[str, Any]] = None) -> None:
        self.defaults = defaults or {}

    async def start_thread(self, opts: Optional[dict[str, Any]] = None) -> _DummyThread:
        _ = opts or self.defaults
        return _DummyThread("codex")

    async def resume_thread(self, thread_id: str, opts: Optional[dict[str, Any]] = None) -> _DummyThread:
        _ = (thread_id, opts)
        return _DummyThread("codex")

    def get_thread_id(self, thread: ThreadHandle) -> Optional[str]:  # pragma: no cover - passthrough
        return thread.id

    async def close(self, thread: ThreadHandle) -> None:  # pragma: no cover - passthrough
        _ = thread
        return None


async def _start_dummy_thread(coder: _DummyCoder) -> RunResult:
    thread = await coder.start_thread()
    return await thread.run("ping")


def _build_factory() -> AdapterFactory:
    def factory(defaults=None):
        return _DummyCoder(defaults)

    factory.coder_name = "dummy"
    return factory


def test_register_and_create_coder() -> None:
    factory = _build_factory()
    register_adapter(factory)

    coder = create_coder("dummy")
    assert isinstance(coder, _DummyCoder)


def test_create_coder_raises_when_missing() -> None:
    with pytest.raises(KeyError):
        create_coder("missing")


def test_unregister_removes_factory() -> None:
    factory = _build_factory()
    register_adapter(factory)
    unregister_adapter("dummy")
    with pytest.raises(KeyError):
        create_coder("dummy")


def test_clear_registered_adapters_drops_everything() -> None:
    register_adapter(_build_factory())
    clear_registered_adapters()
    with pytest.raises(KeyError):
        create_coder("dummy")


@pytest.mark.asyncio
async def test_create_coder_applies_defaults() -> None:
    factory = _build_factory()
    register_adapter(factory)
    coder = create_coder("dummy", defaults={"model": "gpt"})
    result = await _start_dummy_thread(coder)
    assert result.text == "ping"
