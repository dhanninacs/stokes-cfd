"""Packaging of the materials providers (ADR-0018 decision 6; contract "Tests"): nothing of
REFPROP ships, the provider libraries never load with the API, and the API process keeps them
out even after it has used them (they live in the materials worker process)."""

from __future__ import annotations

import fnmatch
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.test_materials_support import HAS_COOLPROP, warm_worker  # noqa: F401 - a fixture

ROOT = Path(__file__).resolve().parent.parent
REFPROP_FILES = ("refprp*.dll", "refprop*.dll", "librefprop*", "*.fld", "*.mix", "*.bnc")


def shipped_roots() -> list[Path]:
    """What the wheel takes, from pyproject.toml: its packages and artifacts."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
    return [ROOT / package for package in wheel["packages"]]


def test_no_refprop_file_ships() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
    for pattern in wheel.get("artifacts", ()):
        assert not any(fnmatch.fnmatch(pattern.lower(), p) for p in REFPROP_FILES), pattern
    offenders = [
        str(path.relative_to(ROOT))
        for root in shipped_roots()
        for path in root.rglob("*")
        if path.is_file() and any(fnmatch.fnmatch(path.name.lower(), p) for p in REFPROP_FILES)
    ]
    assert offenders == []


def test_the_api_loads_no_provider_library() -> None:
    code = (
        "import sys, stokes.api; "
        "bad = sorted(m for m in ('CoolProp', 'cantera', 'scipy', 'numpy') if m in sys.modules); "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=180
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.skipif(not HAS_COOLPROP, reason="CoolProp is not installed")
@pytest.mark.usefixtures("warm_worker")
def test_using_the_providers_keeps_them_out_of_the_api_process() -> None:
    from stokes.physics.materials.providers import get_provider

    get_provider("coolprop").search("water")
    assert "CoolProp" not in sys.modules and "cantera" not in sys.modules
