"""Adapter registry utilities shared across every Python adapter."""

from __future__ import annotations

import copy
import logging
import threading
from typing import Optional

from .types import AdapterFactory, AdapterName, HeadlessCoder, StartOpts

LOGGER = logging.getLogger(__name__)
_REGISTRY: dict[AdapterName, AdapterFactory] = {}
_LOCK = threading.RLock()


def register_adapter(factory: AdapterFactory) -> None:
    """Registers an adapter factory using the factory's ``coder_name`` attribute."""

    name = getattr(factory, "coder_name", None)
    if not name:
        raise ValueError("Adapter factory must expose a coder_name attribute before registration.")
    with _LOCK:
        _REGISTRY[name] = factory
    LOGGER.debug("Registered adapter factory", extra={"adapter": name})


def unregister_adapter(name: AdapterName) -> None:
    """Removes a previously registered adapter factory when present."""

    with _LOCK:
        _REGISTRY.pop(name, None)
    LOGGER.debug("Unregistered adapter factory", extra={"adapter": name})


def clear_registered_adapters() -> None:
    """Clears every registered adapter, primarily used in tests."""

    with _LOCK:
        _REGISTRY.clear()
    LOGGER.debug("Cleared adapter registry")


def get_adapter_factory(name: AdapterName) -> Optional[AdapterFactory]:
    """Returns the adapter factory for ``name`` when registered."""

    with _LOCK:
        return _REGISTRY.get(name)


def create_coder(name: AdapterName, defaults: Optional[StartOpts] = None) -> HeadlessCoder:
    """Creates a headless coder instance from a registered adapter factory."""

    factory = get_adapter_factory(name)
    if not factory:
        raise KeyError(f'Adapter "{name}" is not registered. Did you forget to call register_adapter()?')
    cloned_defaults = copy.deepcopy(defaults) if defaults else None
    return factory(cloned_defaults)
