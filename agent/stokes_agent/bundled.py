"""Separate programs Stokes ships beside itself (ADR-0041, decision G30).

Today one: ``adjointFoam``, the open-source adjoint (GPL-3.0, built from ``of14-adjoint``). It is an
aggregate, not part of Stokes: the agent runs it as its own process through argv and the case files,
exactly like the install's solvers, and nothing here links to or imports it.

**Looked for second.** A copy beside the configured OpenFOAM install's solvers wins
(`engine_env.utilities`, `engine_env.resolve_utility`); the bundled copy is the fallback, so a user
who builds a newer solver and copies it next to FoamWin's solvers gets that one.

**One place decides the folder**, from where the Stokes package itself lives, never from user text
or a machine path: ``<install>/bin/openfoam-adjoint/<program>``, where ``<install>`` is the folder
that holds the ``stokes`` package (the repository root in a development checkout) or the package
folder itself. A candidate counts only when it is a regular file whose resolved parent is that
folder, so a link cannot point the agent at another program.

Headless; standard library only.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The bundled programs, by the name a job's argv uses, and the folder under ``bin/`` each lives in.
BUNDLED: dict[str, str] = {"adjointFoam": "openfoam-adjoint"}


def program_file(name: str) -> str:
    """The file name of a program on this platform."""
    return name + (".exe" if os.name == "nt" else "")


def bundled_roots() -> list[Path]:
    """The folders a bundled program's ``bin/`` may be in, in the order they are tried: the
    install folder that holds the ``stokes`` package, then the package folder itself."""
    import stokes

    package = Path(stokes.__file__).resolve().parent
    return [package.parent, package]


def bundled_folder(name: str) -> list[Path]:
    """Where ``name`` would be bundled, for messages and tests (empty for an unknown name)."""
    folder = BUNDLED.get(name)
    if folder is None:
        return []
    return [root / "bin" / folder for root in bundled_roots()]


def bundled_tool(name: str) -> str | None:
    """The bundled copy of the allow-listed program ``name``, or None."""
    for folder in bundled_folder(name):
        candidate = folder / program_file(name)
        try:
            if candidate.is_file() and candidate.resolve().parent == folder.resolve():
                return str(candidate.resolve())
        except OSError:
            continue
    return None
