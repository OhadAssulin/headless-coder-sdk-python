"""Cancellation utilities that mirror AbortController semantics in Python."""

from __future__ import annotations

import asyncio
import threading
from typing import Callable, Optional

from .types import CancellationSignalProtocol


class CancellationError(RuntimeError):
    """Exception raised when an operation is aborted cooperatively."""

    def __init__(self, reason: Optional[str] = None) -> None:
        message = reason or "Operation was interrupted"
        super().__init__(message)
        self.reason = reason or message


class CancellationSignal(CancellationSignalProtocol):
    """Concrete implementation of the cancellation protocol used by the adapters."""

    def __init__(self) -> None:
        self._aborted = False
        self._reason: Optional[str] = None
        self._callbacks: list[Callable[[Optional[str]], None]] = []
        self._lock = threading.RLock()

    @property
    def aborted(self) -> bool:
        """Returns whether the signal has already fired."""

        return self._aborted

    @property
    def reason(self) -> Optional[str]:
        """Returns the explanatory string supplied during abort."""

        return self._reason

    def add_listener(self, callback: Callable[[Optional[str]], None]) -> Callable[[], None]:
        """Registers a callback that fires exactly once when the signal aborts."""

        with self._lock:
            if self._aborted:
                callback(self._reason)
                return lambda: None
            self._callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._callbacks.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def _trigger(self, reason: Optional[str]) -> None:
        with self._lock:
            if self._aborted:
                return
            self._aborted = True
            self._reason = reason
            callbacks = list(self._callbacks)
            self._callbacks.clear()

        for callback in callbacks:
            try:
                callback(reason)
            except Exception:
                # Callbacks must never bubble back into the signal.
                pass

    async def wait(self) -> None:
        """Awaitable helper that resolves once the signal fires."""

        if self._aborted:
            return
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def _on_abort(_: Optional[str]) -> None:
            if not future.done():
                future.set_result(None)

        unsubscribe = self.add_listener(_on_abort)
        try:
            await future
        finally:
            unsubscribe()

    def throw_if_aborted(self) -> None:
        """Raises :class:`CancellationError` when the signal has already fired."""

        if self._aborted:
            raise CancellationError(self._reason)


class AbortController:
    """Small helper mirroring the web AbortController API."""

    def __init__(self) -> None:
        self._signal = CancellationSignal()

    @property
    def signal(self) -> CancellationSignal:
        """Returns the managed :class:`CancellationSignal` instance."""

        return self._signal

    def abort(self, reason: Optional[str] = None) -> None:
        """Triggers the signal and notifies every subscriber."""

        self._signal._trigger(reason)


def link_signal(
    signal: Optional[CancellationSignalProtocol], callback: Callable[[Optional[str]], None]
) -> Callable[[], None]:
    """Links a signal to a callback, returning an unsubscribe no-op when no signal is provided."""

    if signal is None:
        return lambda: None
    return signal.add_listener(callback)
