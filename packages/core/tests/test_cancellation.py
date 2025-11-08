"""Tests covering cancellation primitives."""

from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = PACKAGE_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from headless_coder_sdk.core import AbortController, CancellationError, link_signal  # noqa: E402


def test_abort_controller_notifies_listeners() -> None:
    controller = AbortController()
    signal = controller.signal
    seen: list[str | None] = []
    unsubscribe = signal.add_listener(lambda reason: seen.append(reason))

    controller.abort("boom")

    assert signal.aborted is True
    assert signal.reason == "boom"
    assert seen == ["boom"]

    unsubscribe()  # should no-op even after abort


@pytest.mark.asyncio
async def test_cancellation_signal_wait_and_throw() -> None:
    controller = AbortController()
    signal = controller.signal

    waiter = asyncio.create_task(signal.wait())
    await asyncio.sleep(0)
    controller.abort("stop")
    await waiter

    with pytest.raises(CancellationError):
        signal.throw_if_aborted()


def test_link_signal_returns_noop_when_missing() -> None:
    unsubscribe = link_signal(None, lambda _: None)
    unsubscribe()
