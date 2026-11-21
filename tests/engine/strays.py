"""Which OpenFOAM processes are still running, for the engine tests' "no strays" rule.

`pgrep` on POSIX, `Get-Process` on Windows (no `pgrep`, no `os.getuid`). Each line names the
process and its executable or command line, so a caller can keep only processes from the real
install: the unit tests create fake `blockMesh` scripts in temp folders.
"""

from __future__ import annotations

import os
import subprocess


def running(*names: str) -> list[str]:
    """One line per running process called any of ``names``: its id and its path or command."""
    if os.name == "nt":
        listed = ",".join(names)
        script = (
            f"Get-Process -Name {listed} -ErrorAction SilentlyContinue | "
            'ForEach-Object { "$($_.Id) $($_.Path)" }'
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=60,
            check=False,
        )
    else:
        completed = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-a", "|".join(names)],
            capture_output=True,
            timeout=20,
            check=False,
        )
    # `pgrep` exits 1 when nothing matches, and `Get-Process` prints nothing.
    return [line for line in completed.stdout.decode(errors="replace").splitlines() if line.strip()]


def from_install(lines: list[str], root: str) -> list[str]:
    """The lines whose path is under the install ``root``, whichever separator either uses."""
    wanted = root.replace("\\", "/").lower()
    return [line for line in lines if wanted in line.replace("\\", "/").lower()]
