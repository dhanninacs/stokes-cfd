"""Boundary values that vary in time, written as OpenFOAM 14 functions of time (ADR-0022 addendum
2026-11-24): each form for each writer (incompressibleFluid, fluid with a constant density, fluid
with an ideal gas, and the `fluid` writer with a coupled wall, as a fluid region of a conjugate
case would be written once that writer takes time), byte for byte against goldens.

The keywords were checked on a run of the install (each form on a moving-lid cavity, the written
boundary value at each time against the profile) and the walk `web/walk_time_bc.mjs` runs a case
made from these writers; a golden changes only with a reviewed diff. Set
``STOKES_UPDATE_GOLDEN=1`` to write the goldens after such a review.
"""

from __future__ import annotations

import math
import os
from dataclasses import replace
from pathlib import Path

import pytest

from stokes.compile.foam import Entry
from stokes.compile.of14 import fluid, multi_region
from stokes.compile.of14 import write as incompressible_write
from stokes.compile.of14.time_functions import function1
from stokes.compile.snapshot import (
    MULTI_REGION_ENGINE,
    PatchCondition,
    RegionCase,
    SolveSnapshot,
)
from stokes.physics.conditions.profiles import TimeProfile

GOLDEN = Path(__file__).parent / "golden" / "compile" / "of14" / "time_profiles"
UPDATE = os.environ.get("STOKES_UPDATE_GOLDEN") == "1"

SINE = TimeProfile("sine", mean=2.0, amplitude=0.5, frequency=4.0, phase=math.pi / 2)
SQUARE = TimeProfile("square", low=0.0, high=500.0, period=0.02, duty=0.25)
TABLE = TimeProfile("table", rows=((0.0, 0.001), (0.05, 0.004), (0.1, 0.002)))
WARMING = TimeProfile("table", rows=((0.0, 300.0), (1.0, 350.0)))


def _transient(snap: SolveSnapshot) -> SolveSnapshot:
    return replace(snap, transient=True, delta_t=0.001, end_time=0.2, write_interval=20)


def incompressible() -> SolveSnapshot:
    """A duct: a sine inlet speed along +x and a square (pulse) outlet pressure."""
    return _transient(
        SolveSnapshot(
            settings_id="n_duct",
            engine="of14.incompressibleFluid",
            turbulence="laminar",
            iterations=1000,
            write_interval=100,
            residual_target=1e-5,
            nu=1.5e-5,
            rho=1.2,
            patches=(
                PatchCondition(
                    "inlet",
                    "patch",
                    "velocity_inlet",
                    velocity=(2.0, 0.0, 0.0),
                    velocity_profile=SINE,
                ),
                PatchCondition(
                    "outlet",
                    "patch",
                    "pressure_outlet",
                    pressure=SQUARE.time_mean(),
                    pressure_profile=SQUARE,
                ),
                PatchCondition("walls", "wall", "wall"),
            ),
            monitor_patches=("inlet", "outlet"),
        )
    )


def mass_flow() -> SolveSnapshot:
    """A duct fed by a mass flow from a table."""
    snap = incompressible()
    return replace(
        snap,
        patches=(
            PatchCondition(
                "inlet",
                "patch",
                "mass_flow_inlet",
                mass_flow=TABLE.time_mean(),
                mass_flow_profile=TABLE,
            ),
            PatchCondition("outlet", "patch", "pressure_outlet", pressure=0.0),
            PatchCondition("walls", "wall", "wall"),
        ),
    )


def energy() -> SolveSnapshot:
    """The `fluid` writer with a constant density and gravity: a warming inlet temperature, a
    sine inlet speed and a pulsing outlet pressure (p_rgh, shifted hydrostatically)."""
    return _transient(
        SolveSnapshot(
            settings_id="n_energy",
            engine="of14.fluid",
            turbulence="laminar",
            iterations=100,
            write_interval=100,
            residual_target=1e-5,
            nu=1e-6,
            rho=998.0,
            energy=True,
            mu=1e-3,
            cp=4182.0,
            kappa=0.6,
            T_ref=300.0,
            gravity=(0.0, -9.81, 0.0),
            patches=(
                PatchCondition(
                    "inlet",
                    "patch",
                    "velocity_inlet",
                    velocity=(0.0, 0.0, 2.0),
                    velocity_profile=SINE,
                    thermal="fixedValue",
                    temperature=WARMING.time_mean(),
                    temperature_profile=WARMING,
                ),
                PatchCondition(
                    "outlet",
                    "patch",
                    "pressure_outlet",
                    pressure=SQUARE.time_mean(),
                    pressure_profile=SQUARE,
                    thermal="inletOutlet",
                    temperature=300.0,
                    centroid=(0.0, 0.1, 0.0),
                ),
                PatchCondition("walls", "wall", "wall", thermal="zeroGradient"),
            ),
            monitor_patches=("inlet", "outlet"),
        )
    )


def gas() -> SolveSnapshot:
    """The `fluid` writer with an ideal gas: the outlet's absolute pressure pulses (intake runner
    end), the inlet's mass flow is a sine."""
    flow = TimeProfile("sine", mean=0.01, amplitude=0.004, frequency=50.0)
    base = energy()
    return replace(
        base,
        density_model="ideal_gas",
        gravity=(0.0, 0.0, 0.0),
        rho=1.2,
        mu=1.8e-5,
        cp=1005.0,
        kappa=0.026,
        patches=(
            PatchCondition(
                "inlet",
                "patch",
                "mass_flow_inlet",
                mass_flow=flow.time_mean(),
                mass_flow_profile=flow,
                thermal="fixedValue",
                temperature=300.0,
            ),
            PatchCondition(
                "outlet",
                "patch",
                "pressure_outlet",
                pressure=SQUARE.time_mean(),
                pressure_profile=SQUARE,
                thermal="inletOutlet",
                temperature=300.0,
                centroid=(0.0, 0.0, 0.0),
            ),
            PatchCondition("walls", "wall", "wall", thermal="zeroGradient"),
        ),
    )


def coupled_wall() -> SolveSnapshot:
    """The `fluid` writer on its own with a coupled wall, as it would write one fluid region of a
    conjugate case: a sine inlet and a pulsing outlet beside the wall it shares with a solid.

    **The multi-region case itself is written steady for now** (`multi_region` refuses a transient
    one), so a conjugate run today gets the time-mean and `setup.profile_steady` says so; this
    golden is the region's dictionaries for when that writer takes time.
    """
    return replace(
        energy(),
        gravity=(0.0, 0.0, 0.0),
        patches=(
            *energy().patches[:2],
            PatchCondition("fluid_to_wall", "wall", "wall", thermal="coupled"),
        ),
    )


def test_a_conjugate_case_refuses_time_and_so_runs_on_the_mean() -> None:
    region = coupled_wall()
    steady = replace(
        region,
        transient=False,
        patches=tuple(
            replace(p, velocity_profile=None, pressure_profile=None, temperature_profile=None)
            for p in region.patches
        ),
    )
    regions = (
        RegionCase("fluid", "fluid", fluid=steady, neighbours=("wall",)),
        RegionCase("wall", "solid", rho=2700.0, cp=900.0, kappa=200.0, neighbours=("fluid",)),
    )
    case = replace(
        steady, engine=MULTI_REGION_ENGINE, regions=regions, region_order=("fluid", "wall")
    )
    written = multi_region.write(case)["0/fluid/U"]
    assert "uniformFixedValue" not in written and "uniform (0.0 0.0 2.0)" in written


def test_a_transient_conjugate_case_is_refused_at_compile() -> None:
    """What the set-up check steers away from (`setup.profile_steady` on a conjugate case): the
    multi-region writer refuses a transient case, profile or not."""
    from stokes.compile.errors import CompileUnsupported

    region = coupled_wall()
    regions = (
        RegionCase("fluid", "fluid", fluid=region, neighbours=("wall",)),
        RegionCase("wall", "solid", rho=2700.0, cp=900.0, kappa=200.0, neighbours=("fluid",)),
    )
    case = replace(
        region, engine=MULTI_REGION_ENGINE, regions=regions, region_order=("fluid", "wall")
    )
    with pytest.raises(CompileUnsupported) as caught:
        multi_region.write(case)
    assert caught.value.details.get("transient") is True


def test_the_hydrostatic_start_follows_the_outlet_profile() -> None:
    """`0/ph_rgh` is `0/p_rgh`'s dictionary: with a profile on the outlet, both follow it."""
    written = fluid.write(energy())
    assert "uniformFixedValue" in written["0/ph_rgh"]
    body = written["0/p_rgh"].split("boundaryField", 1)[1]
    assert written["0/ph_rgh"].split("boundaryField", 1)[1] == body


CASES = {
    "incompressible": (incompressible, incompressible_write, ("0/U", "0/p")),
    "mass_flow": (mass_flow, incompressible_write, ("0/U",)),
    "energy": (energy, fluid.write, ("0/U", "0/p_rgh", "0/T", "0/ph_rgh")),
    "gas": (gas, fluid.write, ("0/U", "0/p", "0/T")),
    "coupled_wall": (coupled_wall, fluid.write, ("0/U", "0/p_rgh", "0/T")),
}
FILES = [(case, name) for case, (_, _, names) in CASES.items() for name in names]


@pytest.mark.parametrize(("case", "name"), FILES)
def test_the_time_profile_goldens(case: str, name: str) -> None:
    """**Byte for byte.** Review the diff (and run the walk) before updating."""
    build, write, _ = CASES[case]
    text = write(build())[name]
    path = GOLDEN / case / name
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, newline="\n")
    assert text == path.read_text(), f"{case}/{name} changed; review the golden diff"


def test_each_form_is_the_function_of_the_same_name() -> None:
    sine = function1(SINE)
    keys = {entry.key: entry.value for entry in sine.items if isinstance(entry, Entry)}
    assert keys["level"] == 2.0 and keys["amplitude"] == 0.5 and keys["frequency"] == 4.0
    # A phase of +90 degrees starts the wave a quarter period early.
    assert keys["start"] == pytest.approx(-0.0625)
    square = {e.key: e.value for e in function1(SQUARE).items if isinstance(e, Entry)}
    assert square["level"] == 250.0 and square["amplitude"] == 250.0
    assert square["frequency"] == 50.0 and square["markSpace"] == pytest.approx(1 / 3)


def test_the_profile_moves_the_basis_and_a_constant_case_keeps_it() -> None:
    from stokes.run.snapshot import solve_basis

    plain = replace(
        incompressible(),
        patches=tuple(
            replace(p, velocity_profile=None, pressure_profile=None)
            for p in incompressible().patches
        ),
    )
    varied = incompressible()
    assert solve_basis(plain, "m", "j", "e") != solve_basis(varied, "m", "j", "e")
    other = replace(
        varied,
        patches=(
            replace(varied.patches[0], velocity_profile=replace(SINE, frequency=5.0)),
            *varied.patches[1:],
        ),
    )
    assert solve_basis(other, "m", "j", "e") != solve_basis(varied, "m", "j", "e")


def test_a_constant_case_writes_what_it_always_wrote() -> None:
    from tests.test_compile_of14 import GOLDEN as DUCT
    from tests.test_compile_of14 import duct

    assert incompressible_write(duct())["0/U"] == (DUCT / "0/U").read_text()
