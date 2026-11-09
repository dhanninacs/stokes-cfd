"""Discover FoamWin's local JSON handshake; never accept an executable path from RPC.

The administrator/user selects a trusted install through STOKES_FOAMWIN_ENV or a
standard installation directory. PowerShell runs that install's fixed handshake once;
subsequent utilities run directly with a filtered environment and absolute tool paths.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine_env import EngineEnv


def candidates() -> list[Path]:
    """Explicit configuration first, then standard FoamWin installation locations."""
    paths: list[Path] = []
    configured = os.environ.get("STOKES_FOAMWIN_ENV", "")
    if configured:
        paths.append(Path(configured))
    for variable in ("ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            paths.extend(
                (
                    Path(base) / "FoamWin" / "of14-env.ps1",
                    Path(base) / "Programs" / "FoamWin" / "of14-env.ps1",
                )
            )
    root = os.environ.get("WM_PROJECT_DIR")
    if root:
        paths.extend((Path(root) / "of14-env.ps1", Path(root).parent / "of14-env.ps1"))
    return list(
        dict.fromkeys(path.resolve() for path in paths if path.is_absolute() and path.is_file())
    )


def capture(path: Path, timeout: float) -> EngineEnv:
    """Return EngineEnv from a validated FoamWin handshake (lazy import avoids a cycle)."""
    from .engine_env import UTILITIES, EngineEnv, EngineEnvError

    if not path.is_absolute() or not path.is_file():
        raise EngineEnvError("FoamWin handshake must be an absolute regular file")
    shell = (
        Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    started = time.monotonic()
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable, host-configured script, no shell
            [
                str(shell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(path),
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineEnvError(f"cannot query FoamWin: {exc}") from exc
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise EngineEnvError(
            "FoamWin handshake failed: " + result.stderr.decode("utf-8", errors="replace")[:1000]
        )
    try:
        data = json.loads(result.stdout.decode("utf-8-sig"))
        if data.get("schemaVersion") != 1 or data.get("distribution") != "FoamWin":
            raise ValueError("unsupported handshake")
        if (
            data.get("openfoam", {}).get("flavour") != "org"
            or str(data.get("openfoam", {}).get("version")) != "14"
        ):
            raise ValueError("OpenFOAM Foundation 14 is required")
        raw = data["environment"]
        appbin = Path(raw["FOAM_APPBIN"]).resolve(strict=True)
        libbin = Path(raw["FOAM_LIBBIN"]).resolve(strict=True)
        # **The external profile's programs too** (ADR-0029): an imported tutorial runs `icoFoam`,
        # `topoSet`, `foamMultiRun` and others. Only a name on one of the two closed lists is ever
        # looked up, and only as an `.exe` directly in the install's own `FOAM_APPBIN`.
        from .flags_external import EXTERNAL_UTILITIES

        tools: dict[str, str] = {}
        for name in (*UTILITIES, "reconstructParMesh", *sorted(EXTERNAL_UTILITIES)):
            tool = appbin / f"{name}.exe"
            if tool.is_file() and tool.resolve().parent == appbin:
                tools[name] = str(tool)
        if not all(name in tools for name in ("blockMesh", "checkMesh", "foamRun")):
            raise ValueError("required native tools are absent")
        env = {
            key: value
            for key, value in raw.items()
            if isinstance(key, str)
            and isinstance(value, str)
            and (
                key.startswith(("WM_", "FOAM_", "MPI_"))
                or key
                in {
                    "SystemRoot",
                    "WINDIR",
                    "TEMP",
                    "TMP",
                    "USERPROFILE",
                    "APPDATA",
                    "LOCALAPPDATA",
                    "PATHEXT",
                }
            )
        }
        parallel = data.get("parallel") or {}
        launcher = None
        search = [str(appbin), str(libbin)]
        if parallel.get("available") is True:
            executable = Path(parallel.get("executable", ""))
            if (
                parallel.get("backend") != "msmpi"
                or parallel.get("countFlag") != "-n"
                or not executable.is_absolute()
                or not executable.is_file()
                or executable.name.lower() != "mpiexec.exe"
            ):
                raise ValueError("invalid Microsoft MPI launcher")
            if not (libbin / "msmpi/libPstream.dll").is_file():
                raise ValueError("MPI-enabled Pstream is absent")
            launcher = str(executable.resolve())
            search.extend((str(libbin / "msmpi"), str(executable.parent)))
        search.extend((str(libbin / "dummy"), str(shell.parents[2])))
        env["PATH"] = os.pathsep.join(search)
        return EngineEnv(
            str(path),
            env,
            "OpenFOAM",
            "14",
            "org",
            time.monotonic() - started,
            native_tools=tools,
            mpi_executable=launcher,
            mpi_count_flag="-n",
        )
    except (KeyError, TypeError, ValueError, OSError, AttributeError) as exc:
        raise EngineEnvError(f"invalid FoamWin handshake: {exc}") from exc
