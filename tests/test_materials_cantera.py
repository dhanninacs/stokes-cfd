"""Cantera through the materials worker (ADR-0018 slice 2; contract "Tests"): air from
``air.yaml`` against hand-computed ideal-gas values, a GRI-Mech 3.0 composition, the NASA-7
export as ``janaf`` against Cantera's own c_p, and the range and composition refusals. Skipped
without Cantera (the 'materials' extra)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stokes.physics.materials.errors import FluidUnknown, OutOfRange
from stokes.physics.materials.providers import get_provider
from tests.test_materials_support import (  # noqa: F401 - fixtures
    fit_methods,
    library_session,
    needs_cantera,
    private_home,
    warm_worker,
)

pytestmark = [needs_cantera, pytest.mark.usefixtures("warm_worker", "private_home")]
AIR = "O2:0.21,N2:0.78,AR:0.01"


def test_air_from_air_yaml_at_300_k() -> None:
    provider = get_provider("cantera")
    info = provider.info("air.yaml", AIR, "mole").to_json()
    assert info["molar_mass"] * 1000.0 == pytest.approx(28.970, abs=5e-4)  # kg/kmol
    at = info["values_at_ref"]
    assert at["density"] == pytest.approx(1.1768, rel=1e-4)
    assert at["specific_heat"] == pytest.approx(1003.05, rel=5e-3)
    assert at["viscosity"] > 0.0 and at["thermal_conductivity"] > 0.0
    assert info["limits"]["T_min"] == 300.0  # NASA-7 data of N2 and AR start at 300 K
    assert info["limits"]["species"] == ["O2", "N2", "AR"]


def test_a_gri_mech_composition_evaluates() -> None:
    grid = get_provider("cantera").evaluate(
        "gri30.yaml", "CH4:1,O2:2,N2:7.52", "mole", "auto", np.array([300.0, 600.0]), 101325.0
    )
    assert grid.missing == []
    for prop in ("density", "viscosity", "specific_heat", "thermal_conductivity"):
        assert np.all(np.isfinite(grid.values[prop])) and np.all(grid.values[prop] > 0.0)


def test_the_nasa7_export_reproduces_canteras_specific_heat(tmp_path: Path) -> None:
    session = library_session(tmp_path)
    added = session.execute(
        "material.add_from_library", provider="cantera", fluid="air.yaml", composition=AIR
    )
    assert added["params"]["T_min"]["value"] == 300.0  # clipped to the mechanism's range
    methods = fit_methods(added)
    assert methods["density"] == "ideal_gas" and methods["specific_heat"] == "janaf"
    assert added["fit"]["specific_heat"]["max_rel_err"] < 1e-6
    project = session.require_project()
    cp = project.get(f"{added['path']}/specific_heat")
    assert cp.params["breakpoints"][1] == 1000.0  # NASA-7 T_common
    both = session.execute(
        "material.evaluate", material=added["name"], T_min=300, T_max=2000, count=40, source="both"
    )
    assert both["properties"]["specific_heat"]["max_rel_err"] < 1e-6


def test_ranges_and_compositions_are_checked() -> None:
    provider = get_provider("cantera")
    with pytest.raises(OutOfRange):
        provider.evaluate("air.yaml", AIR, "mole", "auto", np.array([250.0, 300.0]), 101325.0)
    with pytest.raises(FluidUnknown):
        provider.info("air.yaml", "XX:1", "mole")
    with pytest.raises(FluidUnknown):
        provider.info("no_such_mechanism.yaml", AIR, "mole")
    found = provider.search("air")
    assert found[0].fluid == "air.yaml"
    assert found[0].to_json()["example_composition"] == AIR
