"""The `coupled.simple` engine (ADR-0035): catalogue, writer, steps, agent boundary, refusals.

The real solve is `tests/engine/test_coupled_heated_duct_engine.py` (heatedDuct, Nu = 7.54).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from stokes.compile.coupled import simple as coupled
from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.snapshot import PatchCondition, SolveSnapshot
from stokes.engines.catalogue import COUPLED_ENGINE, ENGINES, solves_energy


def duct(**over: Any) -> SolveSnapshot:
    """The heated duct of coupledSimpleFoam's tutorial: Re 20, Pr 7, 300 K in, 350 K walls."""
    values: dict[str, Any] = {
        "settings_id": "n_duct",
        "engine": COUPLED_ENGINE,
        "turbulence": "laminar",
        "iterations": 100,
        "write_interval": 100,
        "residual_target": 1e-6,
        "nu": 0.1,
        "rho": 1.0,
        "patches": (
            PatchCondition(
                "inlet",
                "patch",
                "velocity_inlet",
                velocity=(1.0, 0.0, 0.0),
                thermal="fixedValue",
                temperature=300.0,
            ),
            PatchCondition(
                "outlet",
                "patch",
                "pressure_outlet",
                pressure=0.0,
                thermal="inletOutlet",
                temperature=300.0,
            ),
            PatchCondition("walls", "wall", "wall", thermal="fixedValue", temperature=350.0),
            PatchCondition("frontAndBack", "empty", "empty"),
        ),
        "monitor_patches": ("inlet", "outlet"),
        "energy": True,
        "mu": 0.1,
        "cp": 1000.0,
        "kappa": 0.1 * 1000.0 / 7.0,
        "T_ref": 300.0,
    }
    values.update(over)
    return SolveSnapshot(**values)


# ------------------------------------------------------------------ the catalogue


def test_the_engine_is_listed_with_its_models_and_its_limits() -> None:
    spec = next(e for e in ENGINES if e.id == COUPLED_ENGINE)
    assert spec.built and spec.turbulence == ("laminar", "kEpsilon", "kOmegaSST")
    assert "Linux or WSL host" in spec.why and "steady only" in spec.why
    assert solves_energy(COUPLED_ENGINE) and solves_energy("of14.fluid")
    assert not solves_energy("of14.incompressibleFluid")


# ------------------------------------------------------------------ the writer


def test_the_case_is_the_incompressible_case_plus_coupled() -> None:
    files = coupled.write(duct())
    assert {"system/fvSolution", "system/fvSchemes", "0/U", "0/p", "0/T"} <= set(files)
    solution = files["system/fvSolution"]
    assert "SIMPLE" in solution  # one directory still drives foamRun for a comparison
    coupled_block = solution[solution.index("COUPLED") :]
    for line in (
        "nOuterIterations 100;",
        "linearSolver    SparseLU;",
        "solveEnergy     yes;",
        "Pr              7",
    ):
        assert " ".join(line.split()) in " ".join(coupled_block.split()), line
    assert "T               1e-06;" in coupled_block or "T 1e-06" in " ".join(coupled_block.split())
    schemes = files["system/fvSchemes"]
    assert "div(devTauCorrFlux)" in schemes and "div((nu*dev2(T(grad(U)))))" in schemes
    assert "div(phi,T)" in schemes and "steadyState" in schemes


def test_the_temperature_boundaries() -> None:
    text = " ".join(coupled.write(duct())["0/T"].split())
    assert "walls { type fixedValue; value uniform 350.0; }" in text
    assert "inlet { type fixedValue; value uniform 300.0; }" in text
    assert "frontAndBack { type empty; }" in text
    heated = duct(
        patches=(
            *duct().patches[:2],
            PatchCondition("walls", "wall", "wall", thermal="heatFlux", heat_flux=1000.0),
        )
    )
    text = " ".join(coupled.write(heated)["0/T"].split())
    gradient = 1000.0 / heated.kappa  # positive: heat into the fluid
    assert f"type fixedGradient; gradient uniform {gradient!r};" in text


def test_without_energy_there_is_no_temperature() -> None:
    files = coupled.write(duct(energy=False))
    assert "0/T" not in files
    assert "solveEnergy" not in files["system/fvSolution"]
    assert "div(phi,T)" not in files["system/fvSchemes"]


def test_the_steps_solve_then_evaluate_the_monitors() -> None:
    steps = coupled.steps(duct())
    assert [s["argv"] for s in steps] == [["coupledSimpleFoam"], ["foamPostProcess", "-latestTime"]]
    assert steps[0]["parse"] == "foamRun" and steps[0]["parse_params"] == {"end_time": 100}
    assert coupled.basis(duct()).startswith(f"{coupled.WRITER_VERSION}:coupled.simple:laminar")


@pytest.mark.parametrize(
    ("change", "error", "words"),
    [
        ({"transient": True, "delta_t": 0.01, "end_time": 1.0}, CompileUnsupported, "steady"),
        ({"cores": 4}, CompileUnsupported, "one core"),
        (
            {
                "cells": coupled.DIRECT_MAX_CELLS_3D + 1,
                "patches": tuple(p for p in duct().patches if p.patch_type != "empty"),
            },
            CompileUnsupported,
            "offered up to",
        ),
        ({"turbulence": "realizableKE"}, CompileUnsupported, "exercised with"),
        ({"gravity": (0.0, -9.81, 0.0)}, CompileUnsupported, "no buoyancy"),
        ({"kappa": 0.0}, CompileInvalid, "conductivity"),
    ],
)
def test_what_the_solver_cannot_do_is_refused(
    change: dict[str, Any], error: type[Exception], words: str
) -> None:
    with pytest.raises(error, match=words):
        coupled.write(dataclasses.replace(duct(), **change))


# ------------------------------------------------------------------ the agent boundary


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from stokes_agent import jobs
    from stokes_agent.engine_env import EngineEnv

    engine = EngineEnv(
        bashrc=str(tmp_path / "bashrc"),
        env={"PATH": str(tmp_path)},
        project="OpenFOAM",
        version="14",
        flavour="org",
        seconds=0.0,
    )
    monkeypatch.setattr(jobs, "resolve_utility", lambda env, name: f"/bin/{name}")
    return jobs.JobStore(tmp_path / "jobs"), engine


def test_the_steps_pass_the_agent_boundary_and_nothing_wider_does(store: Any) -> None:
    from stokes_agent.engine_env import UTILITIES
    from stokes_agent.jobs import JobBadParams, Step

    jobstore, engine = store
    for step in coupled.steps(duct()):
        jobstore._check_argv(Step(step["name"], tuple(step["argv"])), engine)
        assert step["argv"][0] in UTILITIES
    for argv in (
        ("coupledSimpleFoam", "-parallel"),
        ("coupledSimpleFoam", "-case", "/tmp"),
        ("foamPostProcess", "-func", "anything"),
        ("foamPostProcess", "-latestTime", "-libs", "x"),
    ):
        with pytest.raises(JobBadParams):
            jobstore._check_argv(Step("x", argv), engine)


def test_a_host_without_the_solver_says_where_it_runs() -> None:
    from stokes_agent.engine_env import EngineEnv, missing_utility_reason

    linux = EngineEnv("b", {}, "OpenFOAM", "14", "org", 0.0)
    windows = dataclasses.replace(linux, native_tools={"foamRun": "C:/foamRun.exe"})
    assert "build-coupled.sh" in missing_utility_reason(windows, "coupledSimpleFoam")
    assert "WSL or Linux host" in missing_utility_reason(windows, "coupledSimpleFoam")
    assert "Allwmake" in missing_utility_reason(linux, "coupledSimpleFoam")


def test_an_agent_without_msgpack_stops_before_its_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import builtins

    from stokes_agent import __main__ as agent_main

    real_import = builtins.__import__

    def no_msgpack(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "msgpack":
            raise ImportError("No module named 'msgpack'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_msgpack)
    with pytest.raises(SystemExit) as stopped:
        agent_main.main(["--serve"])
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "", "no address may be printed"
    assert "python3-msgpack" in captured.err


# ------------------------------------------------------------------ the API


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    return tmp_path


def test_energy_is_allowed_on_the_coupled_engine_only_among_incompressible_ones(home: Path) -> None:
    from stokes.api import Session
    from stokes.errors import StokesError

    session = Session()
    session.execute("project.new", name="demo")
    session.execute("solve.configure", engine=COUPLED_ENGINE, values={"energy": True})
    node = session.require_project().find("/solver/settings")
    assert node is not None and node.params["energy"] is True
    with pytest.raises(StokesError, match=r"needs the of14\.fluid engine"):
        session.execute(
            "solve.configure", engine="of14.incompressibleFluid", values={"energy": True}
        )


def test_the_cell_limit_is_3d_only_and_unknown_size_passes() -> None:
    big = coupled.DIRECT_MAX_CELLS_3D * 10
    # The duct is 2D (an `empty` patch): the direct solver's fill-in is small, so no limit.
    assert "COUPLED" in coupled.write(dataclasses.replace(duct(), cells=big))["system/fvSolution"]
    # 3D at the limit, and 3D of unknown size (0), are written.
    three_d = tuple(p for p in duct().patches if p.patch_type != "empty")
    for cells in (coupled.DIRECT_MAX_CELLS_3D, 0):
        coupled.write(dataclasses.replace(duct(), cells=cells, patches=three_d))


def test_the_cell_count_does_not_move_the_run_basis() -> None:
    from stokes.run.snapshot import solve_basis

    a = solve_basis(duct(), "mesh", "j_1", "e")
    assert solve_basis(dataclasses.replace(duct(), cells=12345), "mesh", "j_1", "e") == a
