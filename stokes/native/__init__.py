"""Loader for the C++ kernels with pure-Python fallbacks.

``stokes.native`` prefers the compiled ``stokes_native`` extension (built from
``native/`` with nanobind) and falls back to :mod:`stokes.native.fallback`,
which is the reference implementation every kernel is tested against.

Import rule: headless; numpy only.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from stokes.log import fields, get_logger

log = get_logger(__name__)

_impl: ModuleType | None = None
_using_fallback = False
_SUBMODULES = frozenset({"fallback"})


def _load() -> ModuleType:
    global _impl, _using_fallback  # noqa: PLW0603 - module-level cache
    if _impl is None:
        try:
            _impl = importlib.import_module("stokes_native")
            _using_fallback = False
        except ImportError:
            _impl = importlib.import_module("stokes.native.fallback")
            _using_fallback = True
            log.debug("native kernels unavailable, using Python fallback", extra=fields())
    return _impl


def using_fallback() -> bool:
    _load()
    return _using_fallback


def __getattr__(name: str) -> object:
    # Submodules and private names resolve normally; everything else comes from the kernels.
    if name.startswith("_") or name in _SUBMODULES:
        raise AttributeError(name)
    return getattr(_load(), name)
