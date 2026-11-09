"""First-run launcher: install the libraries Stokes needs into its own venv, then start the app.

    python -m stokes.launcher [app options]

**What it does.** On the first launch (or after the required list changes) it asks the user, then
creates a virtual environment under ``%LOCALAPPDATA%\\Stokes\\venv`` (``~/.local/share/Stokes/venv``
elsewhere) and installs :data:`REQUIREMENTS` into it. It then runs ``stokes.app`` with that venv's
Python, with the folder that holds this ``stokes`` package on ``PYTHONPATH``. The Stokes code is not
copied into the venv: it runs from the folder the launcher sits in.

**Why stdlib only.** The launcher runs on the user's own Python before any of these libraries exist,
so it imports nothing outside the standard library.

**Consent.** Nothing is installed without a yes. A console asks on the terminal; a window without a
console asks with a dialog. A refusal exits with status 1 and changes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PYTHON_RANGE = ((3, 13), (3, 15))
"""The Python versions the requirements have wheels for: ``requires-python`` in pyproject.toml."""

REQUIREMENTS: tuple[str, ...] = (
    # core dependencies
    "numpy>=2.0",
    "scipy>=1.15",
    "pint>=0.24",
    "lark>=1.2",
    "cryptography>=43",
    # extras the desktop app needs: ui, server, agent, cad
    "PySide6>=6.11",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "websockets>=13",
    "msgpack>=1.1",
    "cadquery-ocp>=7.8",
)
"""Every library the desktop app imports at run time. ``tests/test_launcher.py`` keeps this list
in step with pyproject.toml, so a new dependency is added in both places or the test fails."""

MARKER = ".stokes-requirements"
"""A file in the venv holding the digest of the requirements it was installed with."""


def venv_dir() -> Path:
    """Where the Stokes venv lives on this user's account."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "Stokes" if base else Path.home() / ".local" / "share" / "Stokes"
    return root / "venv"


def venv_python(venv: Path) -> Path:
    """The venv's interpreter, at the path each platform uses."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def requirements_digest(requirements: Sequence[str] = REQUIREMENTS) -> str:
    """A digest of the requirement list; a changed list means the venv must be updated."""
    return hashlib.sha256("\n".join(requirements).encode("utf-8")).hexdigest()


def is_ready(venv: Path, requirements: Sequence[str] = REQUIREMENTS) -> bool:
    """True when the venv exists and was installed with exactly these requirements."""
    marker = venv / MARKER
    try:
        installed = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return venv_python(venv).is_file() and installed == requirements_digest(requirements)


def python_supported(version: tuple[int, int]) -> bool:
    low, high = PYTHON_RANGE
    return low <= version < high


def ask_consent(venv: Path) -> bool:
    """Ask before installing anything: on the terminal if there is one, else in a dialog."""
    question = (
        f"Stokes needs {len(REQUIREMENTS)} libraries installed into\n{venv}\n"
        "This downloads from the Python package index. Install them now?"
    )
    if sys.stdin is not None and sys.stdin.isatty():
        answer = input(f"{question} [y/N] ")
        return answer.strip().lower() in {"y", "yes"}
    try:
        import tkinter
        from tkinter import messagebox
    except ImportError:
        sys.stderr.write(question + "\n")
        return False
    root = tkinter.Tk()
    root.withdraw()
    try:
        return bool(messagebox.askyesno("Stokes first run", question))
    finally:
        root.destroy()


def install(venv: Path, requirements: Sequence[str] = REQUIREMENTS) -> None:
    """Create the venv and install the requirements. Raises ``RuntimeError`` on failure."""
    if not python_supported(sys.version_info[:2]):
        low, high = PYTHON_RANGE
        raise RuntimeError(
            f"Stokes needs Python {low[0]}.{low[1]} to {high[0]}.{high[1] - 1}; "
            f"this is {sys.version.split()[0]}"
        )
    venv.parent.mkdir(parents=True, exist_ok=True)
    # argv lists, no shell: the interpreter and the venv path are ours
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)  # noqa: S603
    pip = [str(venv_python(venv)), "-m", "pip", "install", *requirements]
    subprocess.run(pip, check=True)  # noqa: S603
    (venv / MARKER).write_text(requirements_digest(requirements) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Stokes's libraries, then start the app.")
    parser.add_argument("--reinstall", action="store_true", help="rebuild the venv even if ready")
    args, app_args = parser.parse_known_args(argv)
    venv = venv_dir()
    if args.reinstall or not is_ready(venv):
        if not ask_consent(venv):
            sys.stderr.write("Nothing was installed.\n")
            return 1
        try:
            install(venv)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            sys.stderr.write(f"The install failed: {exc}\n")
            return 1
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root))
    app = [str(venv_python(venv)), "-m", "stokes.app", *app_args]
    return subprocess.call(app, cwd=root, env=env)  # noqa: S603


if __name__ == "__main__":
    sys.exit(main())
