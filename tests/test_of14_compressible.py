"""Explicit ideal-gas closure, pressure conventions and transient energy controls."""

from __future__ import annotations

from dataclasses import replace

import pytest

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.of14 import fluid
from stokes.compile.snapshot import PatchCondition, SolveSnapshot


def gas_duct() -> SolveSnapshot:
    """An original SI duct definition, independent of installed OpenFOAM tutorials."""
    return SolveSnapshot(
        settings_id="gas_duct",
        engine="of14.fluid",
        turbulence="laminar",
        iterations=12,
        write_interval=1,
        residual_target=1e-6,
        nu=1.8e-5 / 1.1766,
        rho=1.1766,
        patches=(
            PatchCondition(
                "inlet",
                "patch",
                "velocity_inlet",
                velocity=(1.0, 0.0, 0.0),
                thermal="fixedValue",
                temperature=300.0,
                turbulence_mode="intensity_ratio",
                intensity=0.05,
                viscosity_ratio=10.0,
            ),
            PatchCondition(
                "outlet",
                "patch",
                "pressure_outlet",
                pressure=0.0,
                thermal="inletOutlet",
                temperature=300.0,
            ),
            PatchCondition("walls", "wall", "wall", thermal="zeroGradient"),
        ),
        monitor_patches=("inlet", "outlet"),
        energy=True,
        mu=1.8e-5,
        cp=1005.0,
        kappa=0.0257,
        density_model="ideal_gas",
        molar_mass=0.02897,
        T_ref=300.0,
        p_ref=101325.0,
        delta_t=0.001,
        end_time=0.005,
    )


def test_gas_solves_enthalpy_and_absolute_pressure() -> None:
    files = fluid.write(gas_duct())
    props = files["constant/physicalProperties"]
    for word in ("hePsiThermo", "hConst", "perfectGas", "sensibleEnthalpy"):
        assert word in props
    assert "molWeight       28.97;" in props
    assert "Cp              1005.0;" in props
    assert "[1 -1 -2 0 0 0 0]" in files["0/p"]
    assert "uniform 101325.0" in files["0/p"]
    assert "div(phi,h)" in files["system/fvSchemes"]
    assert "div(phi,e)" not in files["system/fvSchemes"]
    assert '"(U|h)"' in files["system/fvSolution"]
    assert "p_rgh" not in files["system/fvSolution"]
    assert {"0/p_rgh", "0/ph_rgh", "constant/g", "constant/pRef"}.isdisjoint(files)


def test_gauge_pressure_is_converted_once_without_density_scaling() -> None:
    snap = gas_duct()
    outlet = replace(snap.patches[1], pressure=2500.0)
    files = fluid.write(replace(snap, patches=(snap.patches[0], outlet, snap.patches[2])))
    assert "uniform 103825.0" in files["0/p"]
    assert "mass flow rate through inlet" in files["system/controlDict"]


def test_explicit_constant_density_is_not_reclassified_by_value() -> None:
    files = fluid.write(replace(gas_duct(), density_model="constant", rho=1.2))
    assert "rhoConst" in files["constant/physicalProperties"]
    assert "sensibleInternalEnergy" in files["constant/physicalProperties"]
    assert "0/p_rgh" in files


@pytest.mark.parametrize("density_model", ["peng_robinson", "boussinesq", "unknown"])
def test_an_unimplemented_density_law_is_never_frozen(density_model: str) -> None:
    with pytest.raises(CompileUnsupported, match="density model"):
        fluid.write(replace(gas_duct(), density_model=density_model))


@pytest.mark.parametrize("molar_mass", [0.0, -0.01, float("inf"), float("nan")])
def test_gas_requires_a_physical_molar_mass(molar_mass: float) -> None:
    with pytest.raises(CompileInvalid, match="molar mass"):
        fluid.write(replace(gas_duct(), molar_mass=molar_mass))


def test_gas_requires_positive_cv() -> None:
    with pytest.raises(CompileInvalid, match="Cv"):
        fluid.write(replace(gas_duct(), cp=200.0))


def test_gas_requires_positive_absolute_outlet_pressure() -> None:
    snap = gas_duct()
    outlet = replace(snap.patches[1], pressure=-101325.0)
    with pytest.raises(CompileInvalid, match="absolute pressure"):
        fluid.write(replace(snap, patches=(snap.patches[0], outlet, snap.patches[2])))


def test_buoyant_gas_is_explicitly_refused() -> None:
    with pytest.raises(CompileUnsupported, match="gravity"):
        fluid.write(replace(gas_duct(), gravity=(0.0, -9.81, 0.0)))


def test_transient_controls_use_seconds_and_cannot_stop_at_steady_residuals() -> None:
    snap = replace(gas_duct(), transient=True)
    files = fluid.write(snap)
    assert "endTime         0.005;" in files["system/controlDict"]
    assert "deltaT          0.001;" in files["system/controlDict"]
    assert "Euler;" in files["system/fvSchemes"]
    assert "steadyState" not in files["system/fvSchemes"]
    assert "pFinal" in files["system/fvSolution"]
    assert '"(U|h)Final"' in files["system/fvSolution"]
    assert "residualControl" not in files["system/fvSolution"]
    assert "nOuterCorrectors 2;" in files["system/fvSolution"]
    assert fluid.steps(snap)[0]["parse_params"]["end_time"] == 0.005


@pytest.mark.parametrize("field", ["delta_t", "end_time"])
@pytest.mark.parametrize("value", [0.0, -0.1, float("inf"), float("nan")])
def test_transient_times_are_positive_and_finite(field: str, value: float) -> None:
    with pytest.raises(CompileInvalid, match=field):
        fluid.write(replace(gas_duct(), transient=True, **{field: value}))


@pytest.mark.parametrize("model", ["kEpsilon", "kOmegaSST", "WALE", "kEqn"])
def test_gas_turbulence_uses_shared_model_fields(model: str) -> None:
    files = fluid.write(replace(gas_duct(), turbulence=model, transient=True))
    assert model in files["constant/momentumTransport"]
    assert "0/nut" in files
    assert "0/alphat" in files
    assert "[1 -1 -1 0 0 0 0]" in files["0/alphat"]
    assert "h" in files["system/fvSolution"]
