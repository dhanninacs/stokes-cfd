"""The adjoint case writer (ADR-0038 decisions 0 to 2): what it adds to the incompressible case, and
what it refuses. The written case was run on FoamWin's adjointShapeOptimisationFoam (laminar
pitzDaily mesh, exit 0, |Ua| bounded); this test pins the files."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from stokes.compile.errors import CompileUnsupported
from stokes.compile.of14.adjoint import (
    APPLICATION,
    ENGINE,
    adjoint_steps,
    application,
    split_outlets,
    write_adjoint,
)
from stokes.compile.snapshot import PatchCondition, SolveSnapshot

SNAP = SolveSnapshot(
    settings_id="s",
    engine=ENGINE,
    turbulence="laminar",
    iterations=500,
    write_interval=500,
    residual_target=1e-5,
    nu=1e-3,
    rho=1.0,
    patches=(
        PatchCondition("inlet", "patch", "velocity_inlet", velocity=(0.0, 2.0, 0.0)),
        PatchCondition("outlet", "patch", "pressure_outlet", pressure=0.0),
        PatchCondition("walls", "wall", "wall"),
        PatchCondition("sides", "empty", "empty"),
    ),
)


def test_the_adjoint_case_adds_the_adjoint_and_turns_the_porosity_off() -> None:
    tree = write_adjoint(SNAP)
    assert {"0/Ua", "0/pa", "0/U", "0/p"} <= set(tree)
    control = tree["system/controlDict"]
    assert "application     adjointShapeOptimisationFoam;" in control
    assert "solver " not in control.split("functions")[0]
    properties = tree["constant/physicalProperties"]
    assert "lambda          0;" in properties and "alphaMax        0;" in properties
    assert "div(-phi,Ua)" in tree["system/fvSchemes"]
    assert "pRefCell" in tree["system/fvSolution"] and "pa" in tree["system/fvSolution"]
    ua = tree["0/Ua"]
    assert "uniform (0 -2 0)" in ua  # the inlet velocity reversed, at its full speed
    assert "adjointOutletVelocity" in ua and "noSlip" in ua and "type            empty" in ua
    assert "adjointOutletPressure" in tree["0/pa"]
    assert adjoint_steps(SNAP)[0]["argv"] == ["adjointShapeOptimisationFoam"]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"turbulence": "kEpsilon"}, "laminar flow only"),
        ({"transient": True}, "steady"),
        ({"cores": 2}, "one core"),
    ],
)
def test_what_the_foundation_adjoint_cannot_do_is_refused(change: dict, match: str) -> None:
    with pytest.raises(CompileUnsupported, match=match):
        write_adjoint(dataclasses.replace(SNAP, **change))


def test_an_unsupported_boundary_is_refused() -> None:
    mass = PatchCondition("inlet", "patch", "mass_flow_inlet", mass_flow=0.1)
    with pytest.raises(CompileUnsupported, match="mass_flow_inlet"):
        write_adjoint(dataclasses.replace(SNAP, patches=(mass, *SNAP.patches[1:])))


# ------------------------------------------------------------------ ADR-0041: the flow split
GOLDEN = Path(__file__).parent / "golden" / "compile" / "of14"

TEE = SolveSnapshot(
    settings_id="n_tee",
    engine=ENGINE,
    turbulence="laminar",
    iterations=400,
    write_interval=400,
    residual_target=1e-5,
    nu=1.5e-5,
    rho=1.2,
    patches=(
        PatchCondition("inlet", "patch", "velocity_inlet", velocity=(0.2, 0.0, 0.0)),
        PatchCondition("out1", "patch", "pressure_outlet", pressure=0.0),
        PatchCondition("out2", "patch", "pressure_outlet", pressure=0.0),
        PatchCondition("walls", "wall", "wall"),
    ),
    monitor_patches=("inlet", "out1", "out2"),
)
SPLIT = dataclasses.replace(TEE, adjoint_objective="flow_split")


@pytest.mark.parametrize(
    ("folder", "snap"), [("adjoint_pressure_loss", TEE), ("adjoint_flow_split", SPLIT)]
)
def test_the_adjoint_goldens(folder: str, snap: SolveSnapshot) -> None:
    """**Byte for byte.** The pressure-loss golden was written by the writer as it was before the
    flow split existed, so it pins that the pressure-loss case did not move."""
    tree = write_adjoint(snap)
    expected = sorted(
        p.relative_to(GOLDEN / folder).as_posix()
        for p in (GOLDEN / folder).rglob("*")
        if p.is_file()
    )
    assert sorted(tree) == expected
    for name in expected:
        assert tree[name] == (GOLDEN / folder / name).read_text(encoding="utf-8"), name


def test_the_flow_split_case_runs_adjoint_foam() -> None:
    tree = write_adjoint(SPLIT)
    assert "application     adjointFoam;" in tree["system/controlDict"]
    assert adjoint_steps(SPLIT)[0]["argv"] == ["adjointFoam"]
    assert application(SPLIT) == "adjointFoam" and application(TEE) == APPLICATION
    ua, pa = tree["0/Ua"], tree["0/pa"]
    # The inlet carries no adjoint velocity: J does not depend on the pressure.
    assert "uniform (0 -0.2 0)" not in ua and "uniform (-0.2 0 0)" not in ua
    assert ua.count("objectiveOutletVelocity") == 2 and pa.count("objectiveOutletPressure") == 2
    assert "adjointOutlet" not in ua + pa
    adjoint = tree["system/adjointDict"]
    for line in (
        "porosity        false;",
        "sensitivity     true;",
        "type            flowRatePartition;",
        "outletPatches   (out1 out2);",
        "targetFractions (0.5 0.5);",
        "viscousTerm     true;",
        "tangential      robin;",
        "type            UaGradU;",
    ):
        assert line in adjoint, line
    # Schemes, solvers, relaxation and residual control are the pressure-loss case's.
    pressure = write_adjoint(TEE)
    for name in ("system/fvSchemes", "system/fvSolution", "constant/physicalProperties"):
        assert tree[name] == pressure[name]
    assert "system/adjointDict" not in pressure


def test_three_outlets_take_a_third_each() -> None:
    third = PatchCondition("out3", "patch", "pressure_outlet", pressure=0.0)
    snap = dataclasses.replace(SPLIT, patches=(*SPLIT.patches, third))
    adjoint = write_adjoint(snap)["system/adjointDict"]
    assert "outletPatches   (out1 out2 out3);" in adjoint
    fractions = adjoint.split("targetFractions")[1].split(";")[0].strip(" ()").split()
    assert len(fractions) == 3 and abs(sum(float(f) for f in fractions) - 1.0) < 1e-12


def test_the_chosen_outlets_are_written_in_patch_order() -> None:
    snap = dataclasses.replace(SPLIT, adjoint_outlets=("out2", "out1"))
    assert split_outlets(snap) == ("out1", "out2")
    assert "outletPatches   (out1 out2);" in write_adjoint(snap)["system/adjointDict"]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"adjoint_outlets": ("out1",)}, "at least two"),
        ({"adjoint_outlets": ("out1", "walls")}, "not a pressure outlet"),
        ({"adjoint_outlets": ("out1", "nope")}, "not a pressure outlet"),
        ({"adjoint_objective": "drag"}, "adjoint objective"),
    ],
)
def test_what_the_flow_split_adjoint_cannot_do_is_refused(
    change: dict[str, Any], match: str
) -> None:
    with pytest.raises(CompileUnsupported, match=match):
        write_adjoint(dataclasses.replace(SPLIT, **change))


def test_a_flow_split_with_an_outlet_left_out_is_refused() -> None:
    third = PatchCondition("out3", "patch", "pressure_outlet", pressure=0.0)
    snap = dataclasses.replace(
        SPLIT, patches=(*SPLIT.patches, third), adjoint_outlets=("out1", "out2")
    )
    with pytest.raises(CompileUnsupported, match="out3 is left out"):
        adjoint_steps(snap)


def test_a_single_outlet_flow_split_is_refused() -> None:
    one = dataclasses.replace(SPLIT, patches=tuple(p for p in SPLIT.patches if p.name != "out2"))
    with pytest.raises(CompileUnsupported, match="at least two"):
        write_adjoint(one)


def test_the_adjoint_fields_keep_the_basis_of_a_pressure_loss_run() -> None:
    """The objective and outlets are in the basis only away from their defaults (ADR-0041)."""
    from stokes.run.snapshot import solve_basis

    before = solve_basis(TEE, "m", "j", ENGINE)
    assert (
        solve_basis(dataclasses.replace(TEE, adjoint_objective="pressure_loss"), "m", "j", ENGINE)
        == before
    )
    split = solve_basis(SPLIT, "m", "j", ENGINE)
    assert split != before
    chosen = dataclasses.replace(SPLIT, adjoint_outlets=("out1", "out2"))
    assert solve_basis(chosen, "m", "j", ENGINE) != split
