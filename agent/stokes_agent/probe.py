"""Host probe: what engines and tools exist here (ADR-0011).

Fast and side-effect free: no environment scripts are sourced (that is the
engine attachment step in M2), only paths and executables are looked up.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

TOOLS: tuple[str, ...] = (
    "foamRun",
    "foamMultiRun",
    "blockMesh",
    "snappyHexMesh",
    "checkMesh",
    "cartesianMesh",
    "gmsh",
    "coupledSimpleFoam",
    "paraview",
    "mpirun",
    "nvidia-smi",
    "wsl.exe",
    "ssh",
)
OPENFOAM_GLOBS: tuple[str, ...] = (
    "/opt/openfoam*",
    "/usr/lib/openfoam/*",
    "~/OpenFOAM/OpenFOAM-*",
    "/opt/OpenFOAM/OpenFOAM-*",
)


def memory_bytes() -> int | None:
    try:
        if hasattr(os, "sysconf"):
            return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError):
        return None
    if platform.system() == "Windows":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    return None


def openfoam_candidates() -> list[str]:
    found: list[str] = []
    for key in ("WM_PROJECT_DIR", "FOAM_INST_DIR"):
        value = os.environ.get(key)
        if value and Path(value).is_dir():
            found.append(value)
    for pattern in OPENFOAM_GLOBS:
        for match in glob.glob(str(Path(pattern).expanduser())):  # noqa: PTH207 - glob of patterns
            if Path(match).is_dir() and match not in found:
                found.append(match)
    return found


def wsl_distros() -> list[str]:
    """Names of installed WSL distros (Windows only); empty elsewhere or when WSL is absent."""
    if platform.system() != "Windows" or shutil.which("wsl.exe") is None:
        return []
    argv = ["wsl.exe", "-l", "-q"]  # resolved on PATH by design; no shell involved
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=15, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return []
    text = completed.stdout.decode("utf-16-le", errors="ignore").replace("\x00", "")
    return [line.strip() for line in text.splitlines() if line.strip()]


def probe_host() -> dict[str, Any]:
    return {
        "hostname": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": memory_bytes(),
        "tools": {tool: shutil.which(tool) for tool in TOOLS},
        "openfoam_candidates": openfoam_candidates(),
        "wsl_distros": wsl_distros(),
        "home": str(Path.home()),
    }
