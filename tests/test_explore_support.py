"""Shared helpers for the ``stokes.explore`` tests (no tests of its own).

``block_packages`` makes optional packages look uninstalled to ``stokes.explore`` without touching
the interpreter's import system, which is how the missing-extra paths are tested.
"""

from __future__ import annotations

from importlib import import_module as real_import_module
from types import ModuleType

import pytest

from stokes.explore import _imports


def block_packages(monkeypatch: pytest.MonkeyPatch, *packages: str) -> None:
    """Make ``stokes.explore`` believe the given top-level packages are not installed."""

    def fake(name: str, package: str | None = None) -> ModuleType:
        if name.split(".", maxsplit=1)[0] in packages:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import_module(name, package)

    monkeypatch.setattr(_imports, "import_module", fake)
