"""Budgets of the materials subsystem (contract "Tests"): evaluating a material on 101 points,
the import time of the package's own modules, and adding water from CoolProp with a warm worker
(the fit inline, CoolProp in the worker process; skipped without CoolProp)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from stokes.api import Session
from tests.perf.harness import HEADROOM, RESULTS, budget, machine_factor
from tests.test_materials_support import (  # noqa: F401 - fixtures
    needs_coolprop,
    private_home,
    warm_worker,
)

PACKAGE = "stokes.physics.materials"


def own_import_ms(module: str) -> float:
    """Self time of the modules of this package when ``module`` is imported (-X importtime)."""
    completed = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", f"import {module}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    own_us = 0
    for line in completed.stderr.splitlines():
        parts = line.split("|")
        if not line.startswith("import time:") or len(parts) != 3:
            continue
        try:
            self_us = int(parts[0].split(":")[1].strip())
        except ValueError:
            continue
        if parts[2].strip().startswith(PACKAGE):
            own_us += self_us
    return own_us / 1000


@pytest.mark.perf
def test_evaluate_latency(session: Session) -> None:
    session.execute("material.add_from_library", fluid="water_liquid")
    session.execute("material.evaluate", material="water_liquid")  # warm numpy and the catalogue
    with budget("materials.evaluate_101", target_ms=50.0):
        session.execute("material.evaluate", material="water_liquid", count=101)


@pytest.mark.perf
def test_import_budget() -> None:
    module = f"{PACKAGE}.checks"  # the deepest import chain: evaluate, methods, catalogue, model
    own_ms = min(own_import_ms(module) for _ in range(3))
    limit = 50.0 * machine_factor() * HEADROOM
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"name": f"import.{module}", "own_ms": own_ms}) + "\n")
    assert own_ms <= limit, f"import {module}: the package takes {own_ms:.0f} ms ({limit:.0f})"


@pytest.mark.perf
@needs_coolprop
@pytest.mark.usefixtures("warm_worker", "private_home")
def test_add_from_coolprop_latency(session: Session) -> None:
    # Warm what the first call pays once per process: the provider report, numpy's polynomial
    # module, the built-in templates the stand-ins come from.
    session.execute("material.add_from_library", provider="coolprop", fluid="Water", name="w0")
    with budget("materials.add_from_library_water", target_ms=200.0):
        session.execute("material.add_from_library", provider="coolprop", fluid="Water")
