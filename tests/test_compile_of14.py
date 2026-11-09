"""The OpenFOAM 14 case writer (ADR-0008, T-212).

**The golden is byte-for-byte, and its output has been RUN.** The duct case built from the same
snapshot
meshes to 25,000 cells (`blockMesh` exit 0, `checkMesh` `Mesh OK.`) and solves to convergence
(`foamRun -solver incompressibleFluid`, exit 0, "SIMPLE solution converged in 64
iterations"), with the
inlet monitor reporting 3.0e-05 m3/s — which is 0.075 m/s x 0.02 x 0.02 m, the right number. So
the golden
is not merely self-consistent; it is a case OpenFOAM accepts and answers physics for.

The engine test that reproduces that run is `tests/engine/test_compile_of14_engine.py`.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.of14 import DIALECT, ENGINES, WRITER_VERSION, basis, steps, write
from stokes.compile.snapshot import PatchCondition, SolveSnapshot

GOLDEN = Path(__file__).parent / "golden" / "compile" / "of14" / "duct"
FILES = (
    "0/U",
    "0/p",
    "constant/momentumTransport",
    "constant/physicalProperties",
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
)


def duct() -> SolveSnapshot:
    """The straight duct: the brief's snapshot, with the three patches a block mesh makes."""
    return SolveSnapshot(
        settings_id="n_duct",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=1000,
        write_interval=100,
        residual_target=1e-5,
        nu=1.5e-5,
        rho=1.2,
        patches=(
            PatchCondition("inlet", "patch", "velocity_inlet", velocity=(0.075, 0.0, 0.0)),
            PatchCondition("outlet", "patch", "pressure_outlet", pressure=0.0),
            PatchCondition("walls", "wall", "wall"),
        ),
        monitor_patches=("inlet", "outlet"),
    )


@pytest.mark.parametrize("name", FILES)
def test_the_duct_golden(name: str) -> None:
    """**Byte for byte.** `tests/golden/` changes only with a reviewed diff.

    The goldens were written from this writer's output **after being read by eye against
    `pitzDailySteady`** — the steady tutorial, which is the reference for every key here — and
    then the
    case was run: see the module docstring.
    """
    tree = write(duct())
    assert tree[name] == (GOLDEN / name).read_text(), (
        f"{name} changed; if that is intended, review the golden diff and re-run the engine test "
        f"(blockMesh, checkMesh, foamRun) before updating it"
    )


def test_the_writer_returns_exactly_seven_files() -> None:
    """A case is these seven. A missing one is a solver error at start; an extra one is
    a file nobody
    asked for."""
    assert sorted(write(duct())) == sorted(FILES)


def test_write_is_deterministic_and_keeps_the_patch_order() -> None:
    """Two calls on equal snapshots return equal trees — which is what lets the basis hash the
    result."""
    assert write(duct()) == write(duct())
    other = SolveSnapshot(
        **{
            **{f: getattr(duct(), f) for f in duct().__dataclass_fields__ if f != "patches"},
            "patches": tuple(reversed(duct().patches)),
        }
    )
    assert write(other)["0/U"] != write(duct())["0/U"], "the patch order must reach the file"


def test_the_dimensions_are_bracketed_and_the_error_that_taught_us() -> None:
    """**Measured, not guessed.** The parenthesised seven-number form is a FOAM FATAL:

        expected a [ in dimensionSet

    so the brackets are part of the value. `U` is m/s and `p` is m2/s2, because an incompressible
    solver's `p` is `p/rho`.
    """
    tree = write(duct())
    assert "dimensions      [0 1 -1 0 0 0 0];" in tree["0/U"]
    assert "dimensions      [0 2 -2 0 0 0 0];" in tree["0/p"]
    assert "dimensions      (" not in tree["0/U"]


def test_the_laminar_stress_term_is_present() -> None:
    """**Correction 1, and it is load-bearing.** With `default none`, the laminar model needs
    `div((nuEff*dev2(T(grad(U))))) Gauss linear;`; without it `foamRun` stops with a FOAM FATAL
    that a keyword is undefined."""
    schemes = write(duct())["system/fvSchemes"]
    assert "div((nuEff*dev2(T(grad(U))))) Gauss linear;" in schemes
    assert "div(phi,U)      bounded Gauss linearUpwind grad(U);" in schemes
    # Steady, so the ddt scheme is not a time scheme.
    assert "steadyState" in schemes
    # And no turbulence schemes: this writer is laminar, and a scheme for a field that does not
    # exist is
    # noise a reader has to check.
    for absent in ("div(phi,k)", "div(phi,epsilon)", "div(phi,omega)"):
        assert absent not in schemes


def test_residual_control_uses_the_snapshots_target() -> None:
    """The target is the number a user set; the solver settings around it are the tutorial's."""
    solution = write(duct())["system/fvSolution"]
    assert "residualControl" in solution
    assert "consistent      yes;" in solution
    assert "relaxationFactors" in solution
    # The wildcard key is QUOTED, as the tutorial writes it — bare `.*` is not a key.
    assert '".*"' in solution


def test_the_control_dict_counts_iterations_not_seconds() -> None:
    """`deltaT 1` with `writeControl timeStep` is what makes `endTime` an iteration count."""
    control = write(duct())["system/controlDict"]
    assert "solver          incompressibleFluid;" in control
    assert "endTime         1000;" in control
    assert "deltaT          1;" in control
    assert "writeControl    timeStep;" in control
    assert "writeInterval   100;" in control
    assert "runTimeModifiable false;" in control


def test_the_monitors_use_the_installs_function_names() -> None:
    """**Measured.** The function is `patchFlowRate`, not `flowRatePatch`: OpenFOAM resolves
    `#includeFunc` against the FILE names under
    `$FOAM_ETC/caseDicts/functions/surfaceFieldValue/`, and
    the wrong one is `FOAM FATAL IO ERROR: Cannot find configuration file flowRatePatch`."""
    control = write(duct())["system/controlDict"]
    assert "#includeFunc patchFlowRate(patch=inlet)" in control
    assert "#includeFunc patchAverage(patch=inlet, fields=(p))" in control
    assert "flowRatePatch" not in control.replace("patchFlowRate", "")


def test_no_funcName_because_the_install_does_not_support_it() -> None:
    """Correction 3's fallback. `grep -rl funcName $FOAM_SRC/OpenFOAM/db/functionObjects/`
    finds nothing,
    so a monitor's `postProcessing/` folder IS its call text — which is why T-202b relaxes
    `job.read` for
    the segment after `postProcessing/`."""
    control = write(duct())["system/controlDict"]
    assert "funcName" not in control


@pytest.mark.parametrize(
    ("kind", "patch_type", "u_type", "p_type"),
    (
        ("wall", "wall", "noSlip", "zeroGradient"),
        ("unassigned", "patch", "noSlip", "zeroGradient"),
        ("slip_wall", "wall", "slip", "slip"),
        ("symmetry", "symmetryPlane", "symmetryPlane", "symmetryPlane"),
        ("symmetry", "symmetry", "symmetry", "symmetry"),
    ),
)
def test_the_kind_table(kind: str, patch_type: str, u_type: str, p_type: str) -> None:
    """Each kind's `type` word in both fields, so a wrong one is a test failure rather than a
    case that
    runs and means something else."""
    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.0,
        patches=(
            PatchCondition("inlet", "patch", "velocity_inlet", velocity=(1.0, 0.0, 0.0)),
            PatchCondition("outlet", "patch", "pressure_outlet", pressure=0.0),
            PatchCondition("sides", patch_type, kind),
        ),
    )
    tree = write(snap)
    for field, expected in (("0/U", u_type), ("0/p", p_type)):
        block = tree[field].split("sides", 1)[1]
        assert f"type            {expected};" in block, f"{field} for {kind}: {block[:120]}"


def test_an_extra_unassigned_patch_gets_a_condition() -> None:
    """**Correction 5.** A patch in `constant/polyMesh/boundary` with no `boundaryField` entry
    is a FOAM
    FATAL at start, so a default patch the mesher made still has to be written."""
    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.0,
        patches=(
            PatchCondition("inlet", "patch", "velocity_inlet", velocity=(1.0, 0.0, 0.0)),
            PatchCondition("outlet", "patch", "pressure_outlet", pressure=0.0),
            PatchCondition("walls", "wall", "wall"),
            PatchCondition("unassigned", "patch", "unassigned"),
        ),
    )
    tree = write(snap)
    for field in ("0/U", "0/p"):
        assert "unassigned" in tree[field], f"{field} has no entry for the default patch"


def test_a_pressure_outlet_is_written_as_p_over_rho() -> None:
    """**A factor-of-rho error is a wrong answer, not a crash**, which is why the division is in the
    writer. 120 Pa at rho 1.2 is 100."""
    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.2,
        patches=(
            PatchCondition("inlet", "patch", "velocity_inlet", velocity=(1.0, 0.0, 0.0)),
            PatchCondition("outlet", "patch", "pressure_outlet", pressure=120.0),
        ),
    )
    assert "uniform 100.0" in write(snap)["0/p"]


@pytest.mark.parametrize(
    ("kind", "patch_type"),
    (
        ("cyclic", "cyclic"),
        ("interface", "patch"),
        ("pressure_inlet", "patch"),
    ),
)
def test_an_unsupported_kind_is_refused(kind: str, patch_type: str) -> None:
    """A gap in the product, said plainly, rather than a condition written as something similar."""
    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.0,
        patches=(PatchCondition("p1", patch_type, kind),),
    )
    with pytest.raises(CompileUnsupported) as caught:
        write(snap)
    assert caught.value.code == "compile.unsupported"


def test_an_unsupported_engine_or_turbulence_is_refused() -> None:
    """One engine, and only the momentum models Foundation 14 registers for it: `buoyantKEpsilon`
    is the fluid solver's, and an unknown name is never read as laminar."""

    def make(**overrides: object) -> SolveSnapshot:
        base: dict[str, object] = {
            "settings_id": "n",
            "engine": "of14.incompressibleFluid",
            "turbulence": "laminar",
            "iterations": 10,
            "write_interval": 10,
            "residual_target": 1e-5,
            "nu": 1e-5,
            "rho": 1.0,
            "patches": (
                PatchCondition("inlet", "patch", "velocity_inlet", velocity=(1.0, 0.0, 0.0)),
            ),
        }
        return SolveSnapshot(**{**base, **overrides})  # type: ignore[arg-type]

    with pytest.raises(CompileUnsupported):
        write(make(engine="of14.simpleFoam"))
    with pytest.raises(CompileUnsupported):
        write(make(turbulence="buoyantKEpsilon"))
    with pytest.raises(CompileUnsupported):
        write(make(turbulence="nope"))


def _turbulent(**overrides: object) -> SolveSnapshot:
    """A duct-like snapshot whose inlet carries the turbulence inputs a model starts from."""
    base: dict[str, object] = {
        "settings_id": "n",
        "engine": "of14.incompressibleFluid",
        "turbulence": "kOmegaSST",
        "iterations": 10,
        "write_interval": 10,
        "residual_target": 1e-5,
        "nu": 1e-5,
        "rho": 1.0,
        "patches": (
            PatchCondition(
                "inlet",
                "patch",
                "velocity_inlet",
                velocity=(1.0, 0.0, 0.0),
                turbulence_mode="intensity_ratio",
                intensity=0.05,
                viscosity_ratio=10.0,
            ),
            PatchCondition("outlet", "patch", "pressure_outlet", pressure=0.0),
            PatchCondition("walls", "wall", "wall"),
        ),
    }
    return SolveSnapshot(**{**base, **overrides})  # type: ignore[arg-type]


def test_every_incompressible_model_writes_a_case() -> None:
    """The writer was laminar-only while the catalogue offered every registered model, so the
    GUI's Flow model choice was refused at run time for all of them (measured on FoamWin)."""
    from stokes.engines.turbulence import model, names_for

    for name in names_for("of14.incompressibleFluid"):
        spec = model(name)
        snap = _turbulent(
            turbulence=name, transient=spec.transient_required, delta_t=1e-3, end_time=1e-2
        )
        tree = write(snap)
        if spec.family == "laminar":
            continue
        assert f"model           {name};" in tree["constant/momentumTransport"], name
        for field in spec.fields:
            assert f"0/{field}" in tree, (name, field)


def test_a_turbulent_steady_case_and_a_transient_les_case() -> None:
    steady = write(_turbulent())
    assert "simulationType  RAS;" in steady["constant/momentumTransport"]
    assert {"0/k", "0/omega", "0/nut"} <= set(steady)
    assert "div(phi,k)" in steady["system/fvSchemes"] and "SIMPLE" in steady["system/fvSolution"]
    les = write(_turbulent(turbulence="WALE", transient=True, delta_t=1e-3, end_time=1e-2))
    assert "simulationType  LES;" in les["constant/momentumTransport"]
    assert "Euler" in les["system/fvSchemes"] and "PIMPLE" in les["system/fvSolution"]
    assert "pFinal" in les["system/fvSolution"]
    assert "deltaT          0.001;" in les["system/controlDict"]
    with pytest.raises(CompileInvalid):
        write(_turbulent(turbulence="WALE"))  # LES needs a transient run


def test_a_symmetry_condition_on_a_wall_patch_is_refused() -> None:
    """**The solver would accept it and get it wrong.** OpenFOAM's `symmetry` assumes the face is a
    mirror, so the condition and the patch type must agree."""
    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.0,
        patches=(PatchCondition("sides", "wall", "symmetry"),),
    )
    with pytest.raises(CompileInvalid) as caught:
        write(snap)
    assert caught.value.code == "compile.invalid"


@pytest.mark.parametrize(
    "kwargs",
    (
        {"iterations": 0},
        {"write_interval": 0},
        {"residual_target": 0.0},
        {"nu": 0.0},
        {"rho": 0.0},
    ),
)
def test_a_value_out_of_range_is_refused(kwargs: dict[str, object]) -> None:
    base = {
        "settings_id": "n",
        "engine": "of14.incompressibleFluid",
        "turbulence": "laminar",
        "iterations": 10,
        "write_interval": 10,
        "residual_target": 1e-5,
        "nu": 1e-5,
        "rho": 1.0,
        "patches": (PatchCondition("inlet", "patch", "velocity_inlet", velocity=(1.0, 0.0, 0.0)),),
    }
    with pytest.raises(CompileInvalid) as caught:
        write(SolveSnapshot(**{**base, **kwargs}))  # type: ignore[arg-type]
    assert caught.value.code == "compile.invalid"


def test_a_velocity_inlet_without_a_velocity_is_refused() -> None:
    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.0,
        patches=(PatchCondition("inlet", "patch", "velocity_inlet"),),
    )
    with pytest.raises(CompileInvalid):
        write(snap)


def test_a_patch_name_that_is_not_a_word_is_refused() -> None:
    """The name becomes a dictionary key, so it must be a word. Refusing here names the PATCH;
    letting
    the printer refuse would name a line in a file the user never wrote."""
    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.0,
        patches=(PatchCondition("in let", "patch", "wall"),),
    )
    with pytest.raises(CompileInvalid) as caught:
        write(snap)
    assert "in let" in str(caught.value)


def test_two_patches_with_one_name_are_refused() -> None:
    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.0,
        patches=(
            PatchCondition("walls", "wall", "wall"),
            PatchCondition("walls", "wall", "wall"),
        ),
    )
    with pytest.raises(CompileInvalid, match="two patches"):
        write(snap)


def test_a_monitor_patch_that_is_not_in_the_case_is_refused() -> None:
    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.0,
        patches=(PatchCondition("walls", "wall", "wall"),),
        monitor_patches=("nope",),
    )
    with pytest.raises(CompileInvalid, match="monitor patch"):
        write(snap)


def test_steps_is_the_foamrun_invocation() -> None:
    """The job step, with the solver named on the command line AND in `controlDict` — both are
    real, and
    the flag makes the log say what the job did."""
    assert steps(duct()) == [
        {
            "name": "solve",
            "argv": ["foamRun", "-solver", "incompressibleFluid"],
            "timeout_s": 7200,
            "parse": "foamRun",
            "parse_params": {"end_time": 1000},
        }
    ]


def test_mpi_solver_case_decomposes_runs_and_reconstructs() -> None:
    snap = dataclasses.replace(duct(), cores=4)
    files = write(snap)
    assert "numberOfSubdomains 4;" in files["system/decomposeParDict"]
    dictionary = " ".join(files["system/decomposeParDict"].split())
    assert "method simple;" in dictionary
    assert "n (4 1 1);" in dictionary
    assert steps(snap) == [
        {"name": "decomposePar", "argv": ["decomposePar"], "timeout_s": 600},
        {
            "name": "solve",
            "argv": ["foamRun", "-solver", "incompressibleFluid"],
            "ranks": 4,
            "timeout_s": 7200,
            "parse": "foamRun",
            "parse_params": {"end_time": 1000},
        },
        {"name": "reconstructPar", "argv": ["reconstructPar"], "timeout_s": 1800},
    ]


@pytest.mark.parametrize("cores", [0, -1, 257, True, 1.5])
def test_solver_refuses_invalid_core_counts(cores: object) -> None:
    with pytest.raises(CompileInvalid, match="cores"):
        write(dataclasses.replace(duct(), cores=cores))  # type: ignore[arg-type]


def test_the_dialect_and_basis() -> None:
    """`basis` names how the case was written, so a change to this module invalidates a cached
    run even
    when the snapshot is identical."""
    assert DIALECT == "of14"
    assert WRITER_VERSION == "of14/3"
    assert ENGINES == ("of14.incompressibleFluid",)
    assert basis(duct()) == "of14/3:of14.incompressibleFluid:laminar"


def test_importing_the_writer_pulls_in_nothing_heavy() -> None:
    """`stokes.compile` is headless and pure: a case writer that imported numpy or VTK would
    put them in
    the way of every session, and the layering gate forbids it anyway."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import stokes.compile.of14\n"
        "bad = [m for m in ('numpy', 'vtk', 'vtkmodules', 'OCP', 'PySide6', 'stokes.model')\n"
        "       if m in sys.modules]\n"
        "print('BAD:' + ','.join(bad))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin"},
        cwd=str(Path(__file__).parent.parent),
    )
    assert completed.returncode == 0, completed.stderr
    line = next(ln for ln in completed.stdout.splitlines() if ln.startswith("BAD:"))
    assert line == "BAD:", f"stokes.compile.of14 imported something heavy: {line}"


# ---------------------------------------------------------------- the mass-flow inlet


def _mass_flow(mass_flow: float | None = 0.5) -> SolveSnapshot:
    base = duct()
    patches = (
        PatchCondition("inlet", "patch", "mass_flow_inlet", mass_flow=mass_flow),
        *base.patches[1:],
    )
    return SolveSnapshot(**{**base.__dict__, "patches": patches})


def test_a_mass_flow_inlet_is_a_flow_rate_inlet_with_its_density() -> None:
    """Keywords as `fluid/squareBend` in the OpenFOAM 14 tutorials writes them."""
    files = write(_mass_flow(0.5))
    inlet = files["0/U"].split("inlet", 1)[1].split("}", 1)[0]
    assert "flowRateInletVelocity" in inlet
    assert "massFlowRate" in inlet and "constant 0.5" in inlet
    assert "rhoInlet" in inlet and "1.2" in inlet
    # the pressure there is the solver's to find, as at a velocity inlet
    assert "zeroGradient" in files["0/p"].split("inlet", 1)[1].split("}", 1)[0]


@pytest.mark.parametrize("bad", (None, 0.0, -1.0, float("inf")))
def test_a_mass_flow_inlet_needs_a_positive_mass_flow(bad: float | None) -> None:
    with pytest.raises(CompileInvalid):
        write(_mass_flow(bad))


def test_a_mass_flow_inlet_keeps_a_flow_only_basis_apart_from_its_own() -> None:
    """The new field is in the basis only where a mass-flow inlet gives it a value."""
    from stokes.run.snapshot import solve_basis

    assert solve_basis(duct(), "m", "j", "e") == solve_basis(duct(), "m", "j", "e")
    assert solve_basis(_mass_flow(0.5), "m", "j", "e") != solve_basis(
        _mass_flow(0.6), "m", "j", "e"
    )


def test_transient_controls_write_an_adaptive_step_physical_write_times_and_averages() -> None:
    les = {"turbulence": "WALE", "transient": True, "delta_t": 1e-4, "end_time": 0.5}
    plain = write(_turbulent(**les))["system/controlDict"]
    assert "writeControl    timeStep;" in plain and "adjustTimeStep" not in plain
    assert "fieldAverage" not in plain
    tuned = write(
        _turbulent(**les, max_courant=0.8, write_time=0.05, average=True, average_start=0.2)
    )["system/controlDict"]
    assert "adjustTimeStep  yes;" in tuned and "maxCo           0.8;" in tuned
    assert "writeControl    adjustableRunTime;" in tuned and "writeInterval   0.05;" in tuned
    assert "#includeFunc fieldAverage(U, p, nut, timeStart=0.2)" in tuned
    fixed = write(_turbulent(**les, write_time=0.05))["system/controlDict"]
    assert "writeControl    runTime;" in fixed, "a fixed step writes on runTime"


def test_a_steady_run_ignores_the_transient_controls() -> None:
    steady = write(_turbulent(max_courant=0.8, write_time=0.05, average=True))
    control = steady["system/controlDict"]
    assert "adjustTimeStep" not in control and "fieldAverage" not in control
    assert "writeControl    timeStep;" in control
