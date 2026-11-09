"""Finding NIST REFPROP for CoolProp's REFPROP backend (ADR-0018 decision 6; contract
"Providers", REFPROP detection).

REFPROP is the user's own licensed program and is never shipped: Stokes only finds an
installation and hands its folder to CoolProp. The search order is the contract's: the
settings path (:func:`set_settings_path`, which ``material.providers(refprop_path=...)``
sets), the ``COOLPROP_REFPROP_ROOT`` variable, NIST's own ``RPPREFIX`` variable (which CoolProp
does not read, so Stokes passes it on), and the folders on ``PATH``. A candidate counts only
when the folder holds the shared library (``REFPRP64.DLL`` on Windows, ``librefprop.so`` on
Linux, ``librefprop.dylib`` on macOS); :func:`locate` does no more than look at files, so it
runs anywhere and its negative path is tested without REFPROP.

:func:`load` runs inside the materials worker process: it points CoolProp at the chosen folder
(both ``COOLPROP_REFPROP_ROOT`` in the worker's environment and the configuration key
``ALTERNATIVE_REFPROP_PATH``, so that the contract's order holds even though CoolProp itself
prefers the variable), then confirms the installation by building ``REFPROP::WATER``. CoolProp
loads REFPROP once per process and remembers a failure, and its loader prints to standard
output when the library cannot be opened; so nothing calls it before a library file has been
found, and a new settings path takes effect by restarting the worker (``worker.py`` does).
REFPROP is not re-entrant: :data:`LOCK` serialises every call into it.

Import rule: the standard library and this package's ``errors``; CoolProp only inside
:func:`load`, which only the worker process calls.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stokes.physics.materials.errors import RefpropMissing

LOCK = threading.Lock()
"""Held around every call into REFPROP: the library keeps global state and is not re-entrant."""
SOURCES: tuple[str, ...] = ("settings", "COOLPROP_REFPROP_ROOT", "RPPREFIX", "PATH")
PROBE_FLUID = "WATER"


class _Settings:
    """The REFPROP folder chosen in Stokes' settings, for the whole process."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.path: str | None = None


_settings = _Settings()


def library_names(platform: str = sys.platform) -> tuple[str, ...]:
    """The shared-library file names CoolProp loads on this platform."""
    if platform.startswith("win"):
        return ("REFPRP64.DLL",)
    if platform == "darwin":
        return ("librefprop.dylib",)
    return ("librefprop.so",)


def set_settings_path(path: str | None) -> None:
    """The REFPROP folder chosen in Stokes' settings (None or empty: none chosen)."""
    with _settings.lock:
        _settings.path = path.strip() if path and path.strip() else None


def settings_path() -> str | None:
    with _settings.lock:
        return _settings.path


@dataclass(frozen=True)
class Location:
    """Where REFPROP was found, or why it was not: ``folder`` is None when nothing was found;
    ``searched`` lists each candidate as ``source: folder (what was wrong)``."""

    folder: str | None
    source: str | None
    searched: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.folder is not None

    def reason(self) -> str:
        if self.found:
            return ""
        tried = "; ".join(self.searched) or "no candidate folder"
        return (
            "REFPROP was not found (searched the settings path, COOLPROP_REFPROP_ROOT, RPPREFIX "
            f"and PATH: {tried}); set its folder with material.providers(refprop_path=...)"
        )


def _rpprefix(environ: Mapping[str, str]) -> str | None:
    """NIST's variable; Windows treats names case-insensitively, a mapping may not."""
    for key in ("RPPREFIX", "RPprefix", "rpprefix"):
        value = environ.get(key)
        if value:
            return value
    return None


def locate(
    settings: str | None,
    environ: Mapping[str, str],
    *,
    is_file: Callable[[str], bool] = os.path.isfile,
    platform: str = sys.platform,
) -> Location:
    """The first candidate folder that holds the REFPROP library, in the contract's order."""
    names = library_names(platform)
    separator = ";" if platform.startswith("win") else ":"
    candidates: list[tuple[str, str]] = []
    if settings:
        candidates.append(("settings", settings))
    for source, value in (
        ("COOLPROP_REFPROP_ROOT", environ.get("COOLPROP_REFPROP_ROOT")),
        ("RPPREFIX", _rpprefix(environ)),
    ):
        if value:
            candidates.append((source, value))
    candidates.extend(
        ("PATH", part) for part in environ.get("PATH", "").split(separator) if part.strip()
    )
    searched: list[str] = []
    for source, raw in candidates:
        folder = raw.strip().strip('"')
        if any(is_file(str(Path(folder) / name)) for name in names):
            return Location(folder, source, tuple(searched))
        if source != "PATH":
            searched.append(f"{source}: {folder} (no {' or '.join(names)})")
    searched.append(f"PATH: no folder holds {' or '.join(names)}")
    return Location(None, None, tuple(searched))


@dataclass
class Detection:
    """What the worker found: the folder, how, REFPROP's version, or why it is unusable."""

    available: bool
    folder: str | None = None
    source: str | None = None
    version: str = ""
    reason: str = ""
    fluids: int = 0
    extra: dict[str, Any] = field(default_factory=dict)  # Any: JSON

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "available": self.available,
            "path": self.folder,
            "source": self.source,
            "version": self.version,
            "reason": self.reason,
            "fluids": self.fluids,
        }


def fluid_files(folder: str) -> list[str]:
    """REFPROP's pure fluids: the ``FLUIDS/*.FLD`` names of an installation."""
    root = Path(folder) / "FLUIDS"
    if not root.is_dir():
        return []
    return sorted(p.stem.upper() for p in root.iterdir() if p.suffix.upper() == ".FLD")


def load(
    settings: str | None,
    environ: MutableMapping[str, str],
    cp: Any,  # Any: CoolProp.CoolProp (no reliable stubs); a stand-in in tests
    *,
    is_file: Callable[[str], bool] = os.path.isfile,
    platform: str = sys.platform,
) -> Detection:
    """Point CoolProp at REFPROP and confirm it by building a REFPROP state (worker side).

    ``environ`` is changed (``COOLPROP_REFPROP_ROOT``) so that CoolProp loads the folder this
    order chose; in the worker that is the process environment.
    """
    where = locate(settings, environ, is_file=is_file, platform=platform)
    if where.folder is None:
        return Detection(False, reason=where.reason())
    environ["COOLPROP_REFPROP_ROOT"] = where.folder
    with LOCK:
        try:
            cp.set_config_string(cp.ALTERNATIVE_REFPROP_PATH, where.folder)
            cp.AbstractState("REFPROP", PROBE_FLUID)
            version = str(cp.get_global_param_string("REFPROP_version"))
        except ValueError as exc:
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else "no message"
            return Detection(
                False,
                folder=where.folder,
                source=where.source,
                reason=(
                    f"REFPROP in {where.folder} (from {where.source}) could not be loaded: "
                    f"{first}. Check that the folder holds a 64-bit library matching this "
                    "Python, then restart Stokes"
                ),
            )
    return Detection(
        True,
        folder=where.folder,
        source=where.source,
        version=version,
        fluids=len(fluid_files(where.folder)),
    )


def missing(detection: Detection) -> RefpropMissing:
    """The typed error for an unusable REFPROP."""
    return RefpropMissing(
        detection.reason or "REFPROP is not available",
        details={"path": detection.folder, "source": detection.source},
    )
