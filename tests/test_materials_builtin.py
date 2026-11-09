"""The built-in catalogue (ADR-0018 decision 4; decisions log F5 (c)): every record referenced,
fluid records reproduce the CoolProp values they were generated from and the NIST WebBook
reference states within their tolerance, solid records match their cited source exactly and stay
within 3 % of the second cited source; search, info and evaluation through the provider."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from stokes.errors import ValidationError
from stokes.physics.materials import catalogue
from stokes.physics.materials.errors import FluidUnknown, ProviderMissing
from stokes.physics.materials.evaluate import evaluate_all
from stokes.physics.materials.model import KIND_PROPERTIES
from stokes.physics.materials.providers import available_providers, builtin, get_provider
from stokes.units.registry import ureg

# NIST Chemistry WebBook (IAPWS-95 and the IAPWS transport formulations; nitrogen), 101325 Pa,
# and dry air from CoolProp's Lemmon formulation (no WebBook page for air): rho, mu, kappa, c_p,
# as the contract lists them.
REFERENCE_STATES = [
    ("water_liquid", 300.0, (996.557, 853.742e-6, 0.609500, 4180.64)),
    ("water_liquid", 350.0, (973.728, 368.470e-6, 0.664874, 4194.47)),
    ("steam", 500.0, (0.440930, 17.2987e-6, 0.0360352, 1981.61)),
    ("nitrogen", 300.0, (1.13816, 17.8901e-6, 0.0259687, 1041.36)),
    ("air", 300.0, (1.17700, 18.5373e-6, 0.0263845, 1006.37)),
]
FITTED = ("density", "viscosity", "specific_heat", "thermal_conductivity")
CITED = ("density", "specific_heat", "thermal_conductivity")


def records(kind: str) -> list[dict[str, Any]]:
    return [r for r in builtin.load() if r["kind"] == kind]


def at(record: dict[str, Any], prop: str, t: float, p: float | None = None) -> float:
    state = builtin.state_of(record)
    grid = np.array([t])
    pressure = np.full_like(grid, state.p_ref if p is None else p)
    values = evaluate_all(builtin.definitions_of(record), state, [prop], grid, pressure)
    return float(values[prop][0])


def si(cited: dict[str, Any], prop: str) -> float:
    """A cited value in SI; per-mole values through the molar mass the citation states."""
    unit = cited["unit"]
    q = ureg().Quantity(cited["value"], unit)
    if prop != "molar_mass" and "substance" in str(q.dimensionality):
        q = q / ureg().Quantity(cited["molar_mass"], "kg/mol")
    target = {
        "density": "kg/m**3",
        "specific_heat": "J/(kg*K)",
        "thermal_conductivity": "W/(m*K)",
        "molar_mass": "kg/mol",
    }[prop]
    return float(q.to(target).magnitude)


def test_the_catalogue_loads_twenty_unique_records() -> None:
    loaded = builtin.load()
    assert len(loaded) == 20
    assert len({r["id"] for r in loaded}) == 20
    assert len(records("fluid")) == 11 and len(records("solid")) == 9
    for record in loaded:
        assert set(record["properties"]) == set(KIND_PROPERTIES[record["kind"]])
        for prop, entry in record["properties"].items():
            catalogue.check_choice(catalogue.method(prop, entry["method"]), record["kind"])


def test_every_record_names_a_public_source() -> None:
    for record in records("fluid"):
        generated = record["generated"]
        assert generated["library"] == "CoolProp" and generated["version"] == "8.0.0"
        assert generated["backend"] in ("HEOS", "INCOMP") and generated["p"] == 101325.0
        assert generated["references"], record["id"]
        assert generated["points"] == 101 and generated["verification_points"] == 201
    for record in records("solid"):
        refs = record["references"]
        for prop in CITED:
            for which in ("primary", "secondary"):
                cited = refs[prop][which]
                assert cited["source"] and cited["url"].startswith("https://"), (record["id"], prop)
                assert cited["unit"] and cited["T"] > 0.0


def test_fluid_records_reproduce_the_coolprop_values_they_were_fitted_to() -> None:
    for record in records("fluid"):
        tolerance = record["state"]["tolerance"]
        check = record["check"]
        for prop in FITTED:
            fit = record["generated"]["fits"][prop]
            assert fit["method"] == record["properties"][prop]["method"]
            assert fit["max_rel_err"] <= tolerance
            for t, expected in zip(check["T"], check[prop], strict=True):
                got = at(record, prop, t)
                assert got == pytest.approx(expected, rel=tolerance), (record["id"], prop, t)


@pytest.mark.parametrize(("rid", "t", "expected"), REFERENCE_STATES)
def test_fluid_records_agree_with_the_reference_states(
    rid: str, t: float, expected: tuple[float, float, float, float]
) -> None:
    record = builtin.record(rid)
    tolerance = record["state"]["tolerance"]
    order = ("density", "viscosity", "thermal_conductivity", "specific_heat")
    for prop, value in zip(order, expected, strict=True):
        assert at(record, prop, t) == pytest.approx(value, rel=tolerance), (rid, prop)


def test_gases_are_ideal_where_the_fit_chose_it_and_carry_their_molar_mass() -> None:
    for rid in ("air", "nitrogen", "oxygen", "argon", "helium", "hydrogen", "methane"):
        record = builtin.record(rid)
        assert record["properties"]["density"]["method"] == "ideal_gas"
        assert record["properties"]["molar_mass"]["value"] > 0.0
    assert builtin.record("water_liquid")["properties"]["viscosity"]["method"] == "log_polynomial"


def test_solid_records_match_their_primary_source_exactly() -> None:
    for record in records("solid"):
        for prop in CITED:
            cited = record["references"][prop]["primary"]
            assert at(record, prop, cited["T"]) == pytest.approx(si(cited, prop), rel=1e-9), (
                record["id"],
                prop,
            )
        if "molar_mass" in record["references"]:
            cited = record["references"]["molar_mass"]["primary"]
            assert record["properties"]["molar_mass"]["value"] == pytest.approx(
                si(cited, "molar_mass"), rel=1e-12
            )


def test_solid_records_stay_within_three_percent_of_the_second_source() -> None:
    for record in records("solid"):
        for prop in CITED:
            cited = record["references"][prop]["secondary"]
            assert at(record, prop, cited["T"]) == pytest.approx(si(cited, prop), rel=0.03), (
                record["id"],
                prop,
            )


def test_temperature_dependent_solids_state_where_they_are_valid() -> None:
    steel = builtin.record("carbon_steel")
    assert steel["properties"]["specific_heat"]["valid_max"] == 873.15
    assert at(steel, "thermal_conductivity", 293.15) == pytest.approx(53.334)
    assert at(steel, "thermal_conductivity", 1073.15) == pytest.approx(54.0 - 3.33e-2 * 800.0)
    eps = builtin.record("eps_insulation")
    assert eps["state"]["T_min"] == 281.0 and eps["state"]["T_max"] == 313.0


def test_search_ranks_exact_names_then_cas_and_formula_then_substrings() -> None:
    provider = builtin.BuiltinProvider()
    assert provider.available().available
    assert provider.search("water")[0].fluid == "water_liquid"
    assert provider.search("H2O")[0].fluid in ("water_liquid", "steam")
    assert [r.fluid for r in provider.search("7440-50-8")] == ["copper"]
    assert provider.search("CO2")[0].fluid == "carbon_dioxide"
    steels = [r.fluid for r in provider.search("steel")]
    assert steels[0] == "carbon_steel" and {"stainless_304", "stainless_316"} <= set(steels)
    assert all(r.kind == "solid" for r in provider.search("", "solid"))
    assert len(provider.search("")) == 20
    assert provider.search("no such material") == []
    with pytest.raises(Exception, match="kind"):
        provider.search("", "plasma")


def test_info_and_evaluate_through_the_provider_protocol() -> None:
    provider = get_provider("builtin")
    info = provider.info("air").to_json()
    assert info["fluid"] == "air" and info["kind"] == "fluid"
    assert info["limits"]["T_min"] == 200.0 and info["generated"]["fluid"] == "Air"
    assert info["properties"] == {
        "density": True,
        "viscosity": True,
        "specific_heat": True,
        "thermal_conductivity": True,
    }
    grid = np.linspace(250.0, 350.0, 5)
    found = provider.evaluate("aluminium", "", "", "auto", grid, 101325.0)
    assert found.missing == [] and found.values["density"] == pytest.approx([2699.0] * 5)
    assert "viscosity" not in found.values
    assert available_providers()[0] == "builtin"


def test_unknown_materials_and_absent_providers_are_typed_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stokes.physics.materials.providers import remote

    with pytest.raises(FluidUnknown) as unknown:
        builtin.record("unobtainium")
    assert unknown.value.code == "materials.fluid_unknown"
    monkeypatch.setattr(remote, "installed", lambda name: False)  # no 'materials' extra
    with pytest.raises(ProviderMissing) as missing:
        get_provider("coolprop")
    assert missing.value.code == "materials.provider_missing"
    assert available_providers() == ["builtin"]
    with pytest.raises(ValidationError):
        get_provider("nonsense")
