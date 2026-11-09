"""The OpenFOAM 14 `fluid` case writer (ADR-0027 step 3).

**These goldens prove determinism and review, not solver acceptance.** They were read by eye
against the OpenFOAM 14 sources and tutorials named in `stokes.compile.of14.fluid`; running the case
with `foamRun -solver fluid` is ADR-0027 step 5. A golden changes only with a reviewed diff.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from typing import Any

import pytest

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.of14 import fluid, turbulence
from stokes.compile.of14 import write as incompressible_write
from stokes.compile.snapshot import PatchCondition, SolveSnapshot
from stokes.run.executors import case_for

GOLDEN = Path(__file__).parent / "golden" / "compile" / "of14" / "elbow_energy"
FILES = (
    "0/T",
    "0/U",
    "0/p",
    "0/p_rgh",
    "0/ph_rgh",
    "constant/g",
    "constant/momentumTransport",
    "constant/pRef",
    "constant/physicalProperties",
    "system/controlDict",
    "system/fvConstraints",
    "system/fvSchemes",
    "system/fvSolution",
)


def elbow() -> SolveSnapshot:
    """A synthetic mixing elbow: cold and hot water in, one outlet, adiabatic walls."""
    return SolveSnapshot(
        settings_id="n_elbow",
        engine="of14.fluid",
        turbulence="laminar",
        iterations=2000,
        write_interval=200,
        residual_target=1e-5,
        nu=1.002e-3 / 998.2,
        rho=998.2,
        patches=(
            PatchCondition(
                "cold_inlet",
                "patch",
                "velocity_inlet",
                velocity=(0.1, 0.0, 0.0),
                thermal="fixedValue",
                temperature=293.0,
            ),
            PatchCondition(
                "hot_inlet",
                "patch",
                "velocity_inlet",
                velocity=(0.0, 0.3, 0.0),
                thermal="fixedValue",
                temperature=353.0,
            ),
            PatchCondition(
                "outlet",
                "patch",
                "pressure_outlet",
                pressure=0.0,
                thermal="inletOutlet",
                temperature=293.0,
                centroid=(0.15, 0.2, 0.025),
            ),
            PatchCondition("walls", "wall", "wall", thermal="zeroGradient"),
        ),
        monitor_patches=("cold_inlet", "hot_inlet", "outlet"),
        energy=True,
        mu=1.002e-3,
        cp=4182.0,
        kappa=0.598,
        T_ref=293.0,
        gravity=(0.0, -9.81, 0.0),
        p_ref=101325.0,
    )


@pytest.mark.parametrize("name", FILES)
def test_the_elbow_energy_golden(name: str) -> None:
    """**Byte for byte.** Review the diff against the OpenFOAM 14 references before updating."""
    tree = fluid.write(elbow())
    assert tree[name] == (GOLDEN / name).read_text(), (
        f"{name} changed; review the golden diff against the OpenFOAM 14 references "
        "(and, after ADR-0027 step 5, re-run the case) before updating it"
    )


def test_the_writer_returns_exactly_these_files() -> None:
    assert sorted(fluid.write(elbow())) == sorted(FILES)


def test_write_is_deterministic_and_keeps_the_patch_order() -> None:
    first, second = fluid.write(elbow()), fluid.write(elbow())
    assert first == second
    t = first["0/T"]
    assert t.index("cold_inlet") < t.index("hot_inlet") < t.index("outlet") < t.index("walls")


def test_the_energy_keywords() -> None:
    tree = fluid.write(elbow())
    assert "energy          sensibleInternalEnergy;" in tree["constant/physicalProperties"]
    assert "equationOfState rhoConst;" in tree["constant/physicalProperties"]
    assert "solver          fluid;" in tree["system/controlDict"]
    solution = tree["system/fvSolution"]
    assert "PIMPLE" in solution and "SIMPLE\n" not in solution.replace("PIMPLE", "")
    for field in ("p_rgh", "U", "e"):
        assert f"        {field}".ljust(24) + "1e-05;" in solution
    assert "div(phi,(p|rho))" in tree["system/fvSchemes"]


def test_a_non_orthogonal_mesh_gets_limited_schemes_and_a_corrector() -> None:
    """The elbow's tet mesh (max non-orthogonality 79.8 degrees) diverged at iteration 67."""
    plain = fluid.write(elbow())
    assert "Gauss linear corrected;" in plain["system/fvSchemes"]
    assert "nNonOrthogonalCorrectors 1;" in plain["system/fvSolution"]
    limited = fluid.write(dataclasses.replace(elbow(), non_orthogonal=True))
    assert "Gauss linear limited corrected 0.33;" in limited["system/fvSchemes"]
    assert "limited corrected 0.33;" in limited["system/fvSchemes"].split("snGradSchemes")[1]
    assert "nNonOrthogonalCorrectors 2;" in limited["system/fvSolution"]


def test_the_prandtl_number_carries_the_conductivity() -> None:
    snap = elbow()
    text = fluid.write(snap)["constant/physicalProperties"]
    assert f"Pr              {snap.mu * snap.cp / snap.kappa!r};" in text


def test_a_heat_flux_wall_is_an_external_temperature_with_q() -> None:
    snap = dataclasses.replace(
        elbow(),
        patches=(
            *elbow().patches[:3],
            PatchCondition("walls", "wall", "wall", thermal="heatFlux", heat_flux=1500.0),
        ),
    )
    t = fluid.write(snap)["0/T"]
    assert "type            externalTemperature;" in t
    assert "q               constant 1500.0;" in t


def test_a_fixed_temperature_wall_and_a_symmetry_plane() -> None:
    snap = dataclasses.replace(
        elbow(),
        patches=(
            *elbow().patches[:3],
            PatchCondition("walls", "wall", "wall", thermal="fixedValue", temperature=330.0),
            PatchCondition("mid", "symmetryPlane", "symmetry"),
        ),
    )
    tree = fluid.write(snap)
    assert "uniform 330.0" in tree["0/T"]
    for name in ("0/T", "0/p", "0/p_rgh", "0/U"):
        assert "type            symmetryPlane;" in tree[name]


def _patches_with_walls(**wall: object) -> tuple[PatchCondition, ...]:
    return (*elbow().patches[:3], PatchCondition("walls", "wall", "wall", **wall))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"turbulence": "notAModel"},  # the models the writer knows are the engine catalogue's
        {"engine": "of14.incompressibleFluid"},
        {"energy": False},
        {"patches": _patches_with_walls(thermal="convection")},
    ],
    ids=["turbulence", "incompressible", "energy-off", "convection"],
)
def test_what_the_writer_cannot_express_is_refused(overrides: dict[str, object]) -> None:
    snap = dataclasses.replace(elbow(), **overrides)  # type: ignore[arg-type]
    with pytest.raises(CompileUnsupported) as caught:
        fluid.write(snap)
    assert caught.value.code == "compile.unsupported"


@pytest.mark.parametrize(
    "overrides",
    [
        {"kappa": 0.0},
        {"cp": -1.0},
        {"mu": 0.0},
        {"p_ref": 0.0},
        {"patches": _patches_with_walls(thermal=None)},
        {"patches": _patches_with_walls(thermal="heatFlux")},
        {"patches": _patches_with_walls(thermal="fixedValue")},
    ],
    ids=["kappa", "cp", "mu", "p_ref", "no-thermal", "no-heat-flux", "no-temperature"],
)
def test_a_value_that_cannot_be_a_case_is_invalid(overrides: dict[str, object]) -> None:
    snap = dataclasses.replace(elbow(), **overrides)  # type: ignore[arg-type]
    with pytest.raises(CompileInvalid):
        fluid.write(snap)


def test_steps_run_the_fluid_solver_and_decompose_in_parallel() -> None:
    serial = fluid.steps(elbow())
    assert [step["argv"] for step in serial] == [["foamRun", "-solver", "fluid"]]
    parallel = fluid.write(dataclasses.replace(elbow(), cores=4))
    assert "system/decomposeParDict" in parallel


def test_the_executor_dispatches_by_snapshot() -> None:
    files, steps = case_for(elbow())
    assert files == fluid.write(elbow())
    assert steps[-1]["argv"] == ["foamRun", "-solver", "fluid"]
    from tests.test_compile_of14 import duct

    files, steps = case_for(duct())
    assert files == incompressible_write(duct())
    assert steps[-1]["argv"] == ["foamRun", "-solver", "incompressibleFluid"]


def test_the_version_and_basis() -> None:
    assert fluid.WRITER_VERSION == "of14-fluid/6"
    assert fluid.basis(elbow()) == "of14-fluid/6:of14.fluid:laminar"


# ---------------------------------------------------------------- kOmegaSST (ADR-0028)

KOMEGA_GOLDEN = Path(__file__).parent / "golden" / "compile" / "of14" / "elbow_energy_kOmegaSST"
KOMEGA_FILES = (*FILES, "0/k", "0/omega", "0/nut", "0/alphat")


def _with_inlet_group(condition: PatchCondition) -> PatchCondition:
    """An inlet with the elbow project's group: 5 % intensity, viscosity ratio 10."""
    if condition.kind != "velocity_inlet":
        return condition
    return dataclasses.replace(
        condition, turbulence_mode="intensity_ratio", intensity=0.05, viscosity_ratio=10.0
    )


def elbow_komega() -> SolveSnapshot:
    base = elbow()
    return dataclasses.replace(
        base,
        turbulence="kOmegaSST",
        patches=tuple(_with_inlet_group(item) for item in base.patches),
    )


def _cold_inlet(snap: SolveSnapshot, **change: Any) -> SolveSnapshot:
    patches = tuple(
        dataclasses.replace(item, **change) if item.name == "cold_inlet" else item
        for item in snap.patches
    )
    return dataclasses.replace(snap, patches=patches)


@pytest.mark.parametrize("name", KOMEGA_FILES)
def test_the_komega_golden(name: str) -> None:
    """**Byte for byte.** Review against `roomHeating` and ADR-0028 before updating."""
    tree = fluid.write(elbow_komega())
    assert tree[name] == (KOMEGA_GOLDEN / name).read_text(), (
        f"{name} changed; review the golden diff against ADR-0028 before updating it"
    )


def test_komega_writes_exactly_the_turbulence_files() -> None:
    assert sorted(fluid.write(elbow_komega())) == sorted(KOMEGA_FILES)


def test_komega_declares_the_ras_model_and_the_wall_functions() -> None:
    tree = fluid.write(elbow_komega())
    assert "RAS;" in tree["constant/momentumTransport"]
    assert "kOmegaSST;" in tree["constant/momentumTransport"]
    assert "kqRWallFunction" in tree["0/k"]
    assert "omegaWallFunction" in tree["0/omega"]
    assert "nutkWallFunction" in tree["0/nut"]
    assert "compressible::alphatJayatillekeWallFunction" in tree["0/alphat"]
    assert "Prt" in tree["0/alphat"]
    assert "wallDist" in tree["system/fvSchemes"]
    assert "meshWave" in tree["system/fvSchemes"]
    assert '"(k|omega)"' in tree["system/fvSolution"]


def test_the_laminar_case_keeps_no_turbulence_keywords() -> None:
    tree = fluid.write(elbow())
    assert "simulationType  laminar;" in tree["constant/momentumTransport"]
    assert "wallDist" not in tree["system/fvSchemes"]
    assert '"(U|e)"' in tree["system/fvSolution"]


def test_the_inflow_k_and_omega_come_from_the_intensity_and_the_ratio() -> None:
    nu = 1.002e-3 / 998.2
    snap = elbow_komega()
    inlets = [item for item in snap.patches if item.kind == "velocity_inlet"]
    assert len(inlets) == 2
    for patch in inlets:
        values = turbulence._values(snap, patch)
        k = 1.5 * (0.05 * math.hypot(*(patch.velocity or (0.0, 0.0, 0.0)))) ** 2
        assert (values["k"], values["omega"]) == pytest.approx((k, k / (10.0 * nu)))


def test_the_length_group_sets_omega_from_the_length_scale() -> None:
    snap = dataclasses.replace(
        elbow_komega(),
        patches=tuple(
            dataclasses.replace(item, turbulence_mode="intensity_length", length_scale=0.01)
            if item.kind == "velocity_inlet"
            else item
            for item in elbow_komega().patches
        ),
    )
    k = 1.5 * (0.05 * 0.1) ** 2
    cold = next(item for item in snap.patches if item.name == "cold_inlet")
    values = turbulence._values(snap, cold)
    assert values["omega"] == pytest.approx(math.sqrt(k) / (0.09**0.25 * 0.01))


@pytest.mark.parametrize(
    "change",
    [
        {"turbulence_mode": None},
        {"intensity": 0.0},
        {"viscosity_ratio": 0.0},
    ],
    ids=["no-group", "no-intensity", "no-ratio"],
)
def test_an_inlet_without_a_usable_group_is_invalid(change: dict[str, Any]) -> None:
    with pytest.raises(CompileInvalid) as caught:
        fluid.write(_cold_inlet(elbow_komega(), **change))
    assert "cold_inlet" in str(caught.value)


def test_komega_needs_a_velocity_inlet() -> None:
    snap = dataclasses.replace(
        elbow_komega(), patches=elbow_komega().patches[2:], monitor_patches=("outlet",)
    )
    with pytest.raises(CompileInvalid):
        fluid.write(snap)


# ---------------------------------------------------------------- the start state (step 5)


def _outlet_block(text: str) -> str:
    start = text.index("    outlet\n")
    return text[start : text.index("    }", start)]


def test_the_outlet_p_rgh_value_is_the_real_p_rgh_at_its_height() -> None:
    """**`value` is `p - rho*g.c`, not the gauge `p`**: the solver's first buoyancy force reads it
    before any update, and the gauge value crashed the elbow (ADR-0027 step 5)."""
    snap = elbow()
    expected = round(0.0 - snap.rho * (-9.81 * 0.2), 6)
    for name in ("0/p_rgh", "0/ph_rgh"):
        block = _outlet_block(fluid.write(snap)[name])
        assert "type            fixedValue;" in block
        assert f"value           uniform {expected};" in block


def test_an_outlet_along_gravity_is_one_hydrostatic_p_rgh_not_a_uniform_p() -> None:
    """**Gravity along the outlet plane** (ADR-0027 step 6): a `prghPressure` with a uniform `p`
    is not hydrostatic across the faces and blew the elbow up; the outlet holds one `p_rgh`, so
    the user's pressure is at the centroid and hydrostatic across the patch."""
    snap = dataclasses.replace(elbow(), gravity=(-9.81, 0.0, 0.0))
    expected = round(0.0 - snap.rho * (-9.81 * 0.15), 6)
    for name in ("0/p_rgh", "0/ph_rgh"):
        block = _outlet_block(fluid.write(snap)[name])
        assert "prghPressure" not in block
        assert "type            fixedValue;" in block
        assert f"value           uniform {expected};" in block


def test_the_pressure_equation_has_one_non_orthogonal_corrector() -> None:
    """Measured on the elbow's snappy mesh (ADR-0027 step 6): continuity error 8.3 -> 1.3."""
    assert "nNonOrthogonalCorrectors 1;" in fluid.write(elbow())["system/fvSolution"]


def test_gravity_and_an_outlet_start_the_case_hydrostatic() -> None:
    tree = fluid.write(elbow())
    solution = tree["system/fvSolution"]
    assert "hydrostaticInitialisation yes;" in solution
    assert "nHydrostaticCorrectors 5;" in solution
    assert "    ph_rgh\n" in solution
    assert "object          ph_rgh;" in tree["0/ph_rgh"]


def test_without_gravity_the_start_is_the_plain_one() -> None:
    snap = dataclasses.replace(elbow(), gravity=(0.0, 0.0, 0.0))
    tree = fluid.write(snap)
    assert "0/ph_rgh" not in tree
    assert "hydrostatic" not in tree["system/fvSolution"]
    assert "value           uniform 0.0;" in _outlet_block(tree["0/p_rgh"])


def test_a_closed_volume_with_gravity_does_not_start_hydrostatic() -> None:
    """With every pressure patch `fixedFluxPressure` the `ph_rgh` equation has no level."""
    snap = dataclasses.replace(
        elbow(), patches=(*elbow().patches[:2], elbow().patches[3]), monitor_patches=()
    )
    tree = fluid.write(snap)
    assert "0/ph_rgh" not in tree
    assert "hydrostatic" not in tree["system/fvSolution"]


def test_an_outlet_without_a_position_is_refused_with_gravity_on() -> None:
    outlet = dataclasses.replace(elbow().patches[2], centroid=None)
    patches = (*elbow().patches[:2], outlet, elbow().patches[3])
    with pytest.raises(CompileInvalid) as caught:
        fluid.write(dataclasses.replace(elbow(), patches=patches))
    assert caught.value.details["patch"] == "outlet"
    # Without gravity the height does not enter, so the same outlet is written.
    fluid.write(dataclasses.replace(elbow(), patches=patches, gravity=(0.0, 0.0, 0.0)))


class _Runner:
    """`job.read` over a dictionary of files; anything else is the agent refusing the path."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.asked: list[str] = []

    def call(self, method: str, **params: object) -> dict[str, object]:
        assert method == "job.read"
        path = str(params["path"])
        self.asked.append(path)
        if path not in self.files:
            raise RuntimeError("agent.not_found")
        return {"text": self.files[path]}


def test_the_energy_monitors_read_mean_p_and_t_from_the_pt_directory() -> None:
    """**OpenFOAM 14 writes `fields=(p T)` as `fields=(pT)`**, with `time p T` rows; asking for
    `fields=(p)` missed the file on the elbow run (ADR-0027 step 5)."""
    from stokes.run.executors import _read_monitors

    header = "# Time          \tareaAverage(p)\tareaAverage(T)\n"
    runner = _Runner(
        {
            "postProcessing/patchFlowRate(patch=outlet)/0/surfaceFieldValue.dat": (
                "# Time\tsum(phi)\n0\t0\n60\t4.08273182e+00\n"
            ),
            "postProcessing/patchAverage(patch=outlet,fields=(pT))/0/surfaceFieldValue.dat": (
                header + "0\t1.01325e+05\t3.0e+02\n60\t1.01325000e+05\t3.00098560e+02\n"
            ),
        }
    )
    snap = dataclasses.replace(elbow(), monitor_patches=("outlet",))
    found = _read_monitors(runner, "j_1", snap)
    assert found == {"outlet": {"flow_rate": 4.08273182, "p_mean": 101325.0, "T_mean": 300.09856}}


def test_the_flow_only_monitors_keep_their_paths() -> None:
    from stokes.run.executors import _read_monitors
    from tests.test_compile_of14 import duct

    runner = _Runner(
        {
            "postProcessing/patchAverage(patch=inlet,fields=(p))/0/surfaceFieldValue.dat": (
                "# Time\tareaAverage(p)\n10\t0.0851\n"
            ),
        }
    )
    snap = dataclasses.replace(duct(), monitor_patches=("inlet",))
    assert _read_monitors(runner, "j_1", snap) == {"inlet": {"p_mean": 0.0851}}


# ---------------------------------------------------------------- the reconstruct check (step 4)


def test_the_reconstruct_check_lists_temperature_when_energy_is_on() -> None:
    """**An energy run's reconstruct must bring back `T`**, in `0/` and in each time directory."""
    from stokes.compile.parallel import missing_reconstructed, reconstruct_fields_check

    snap = dataclasses.replace(elbow(), cores=4)
    check = reconstruct_fields_check(energy=snap.energy)
    assert check is not None
    assert "0/T" in check["initial"] and "0/p_rgh" in check["initial"]
    assert set(check["time_fields"]) == {"T", "U", "p", "p_rgh"}
    # The writer stages every initial file the check names.
    assert set(check["initial"]) <= set(fluid.write(snap))
    # The parallel run ends in a plain reconstructPar, which reconstructs every field.
    assert fluid.steps(snap)[-1]["argv"] == ["reconstructPar"]
    assert missing_reconstructed(check, list(check["initial"]), ["U", "p", "p_rgh"]) == ["T"]
    assert missing_reconstructed(check, list(check["initial"]), list(check["time_fields"])) == []


def test_the_reconstruct_check_is_absent_when_energy_is_off() -> None:
    from stokes.compile.parallel import missing_reconstructed, reconstruct_fields_check

    assert reconstruct_fields_check(energy=False) is None
    assert missing_reconstructed(None, [], []) == []


# ---------------------------------------------------------------- every registered model


def _fluid_models() -> list[str]:
    from stokes.engines.turbulence import names_for

    return [name for name in names_for("of14.fluid") if name != "laminar"]


@pytest.mark.parametrize("name", _fluid_models())
def test_every_model_registered_for_the_fluid_engine_writes_its_fields(name: str) -> None:
    """The catalogue says which fields a model transports; the writer must write a file for each,
    name the model in `momentumTransport`, and carry a convection scheme for every transported
    field. This pins the catalogue to the writer, not to OpenFOAM: running each model against its
    tutorial is still to do."""
    from stokes.engines.turbulence import model

    spec = model(name)
    snap = dataclasses.replace(
        elbow_komega(),
        turbulence=name,
        transient=spec.transient_required,
        delta_t=0.001,
        end_time=0.01,
    )
    tree = fluid.write(snap)
    assert f"{name};" in tree["constant/momentumTransport"]
    for field in spec.fields:
        assert f"0/{field}" in tree, f"{name} has no 0/{field}"
    for field in spec.transported_fields:
        assert f"div(phi,{field})" in tree["system/fvSchemes"], f"{name}: no scheme for {field}"
