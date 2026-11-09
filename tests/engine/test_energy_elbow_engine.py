"""The energy equation on a real mixing elbow, run natively (ADR-0027 step 5).

    STOKES_ENGINE_TESTS=1 STOKES_ELBOW_ENERGY_PROJECT=<a meshed elbow .stokes> \\
        python -m pytest -q tests/engine/test_energy_elbow_engine.py -s

**The project is not in the repository.** The elbow geometry and its mesh live in a local,
git-ignored project, so the test takes its path from `STOKES_ELBOW_ENERGY_PROJECT` and skips with
the reason when it is unset or missing. The project must already be meshed and configured for
energy (`of14.fluid`, two velocity inlets at different temperatures, one pressure outlet); the
test copies it to a temporary directory, sets the iteration count and solves the copy, so the
original is never written.

**Why 300 iterations by default.** The outlet starts at its backflow temperature (300 K), and at
60 iterations its mean was still 300.1 K; at 300 it is 294.92 K against a 294.86 K balance. A
shorter run proves the case starts but not the mixing, so the count is overridable through
`STOKES_ELBOW_ENERGY_ITERATIONS` only for a smoke check of the start.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session

pytestmark = [pytest.mark.engine, pytest.mark.slow]

PROJECT_ENV = "STOKES_ELBOW_ENERGY_PROJECT"
ITERATIONS_ENV = "STOKES_ELBOW_ENERGY_ITERATIONS"
#: The inlet temperatures the elbow is set up with: every temperature must lie between them.
T_COLD, T_HOT = 293.0, 353.0
#: Rounding slack on the bounds; a real overshoot is far larger than this.
T_SLACK = 1e-3
#: How far the outlet mean may sit from the mass-weighted mixing balance of the inlets.
MIXING_TOLERANCE = 0.5


def project_source() -> Path:
    """The local elbow project, or a skip that says how to provide one."""
    named = os.environ.get(PROJECT_ENV, "").strip()
    if not named:
        pytest.skip(f"set {PROJECT_ENV} to a meshed elbow energy project to run this test")
    path = Path(named)
    if not path.is_dir():
        pytest.skip(f"{PROJECT_ENV} does not name a project directory on this machine")
    return path


def mixing_balance(monitors: dict[str, dict[str, Any]], inlets: list[str]) -> float:
    """The outlet temperature a perfect mix of the inlets would have: sum(|m| T) / sum(|m|)."""
    flows = [abs(float(monitors[name]["flow_rate"])) for name in inlets]
    temperatures = [float(monitors[name]["T_mean"]) for name in inlets]
    return sum(m * t for m, t in zip(flows, temperatures, strict=True)) / sum(flows)


def test_the_elbow_mixes_hot_and_cold_water(tmp_path: Path) -> None:
    source = project_source()
    project = tmp_path / source.name
    shutil.copytree(source, project)
    iterations = int(os.environ.get(ITERATIONS_ENV, "300"))

    session = Session()
    session.execute("project.open", path=str(project))
    session.execute(
        "solve.configure",
        engine="of14.fluid",
        values={"energy": True, "iterations": iterations, "write_interval": iterations},
    )
    check = session.execute("solve.check")
    assert check["state"] != "not_ready", check

    started = session.execute("run.start", wait=True, force=True)
    status = session.execute("run.status", run=started["run"])
    assert status["state"] in ("done", "done_unconverged"), status
    assert status["iterations"] == iterations, status

    monitors: dict[str, dict[str, Any]] = status["monitors"]
    inlets = sorted(name for name, data in monitors.items() if float(data["flow_rate"]) < 0.0)
    outlets = sorted(name for name, data in monitors.items() if float(data["flow_rate"]) > 0.0)
    assert len(inlets) >= 2 and len(outlets) == 1, monitors

    for name, data in monitors.items():
        assert T_COLD - T_SLACK <= float(data["T_mean"]) <= T_HOT + T_SLACK, (name, data)
    fields = session.execute("run.fields", run=started["run"])
    temperature = [item for item in fields["fields"] if item["name"] == "T"]
    assert temperature, fields
    assert float(temperature[0]["min"]) >= T_COLD - T_SLACK, temperature
    assert float(temperature[0]["max"]) <= T_HOT + T_SLACK, temperature

    expected = mixing_balance(monitors, inlets)
    outlet = float(monitors[outlets[0]]["T_mean"])
    assert abs(outlet - expected) <= MIXING_TOLERANCE, (outlet, expected, monitors)
