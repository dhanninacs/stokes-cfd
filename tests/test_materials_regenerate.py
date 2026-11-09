"""Regenerating the built-in fluid records from CoolProp (ADR-0018 slice 2): the eleven fluid
records of ``data/materials.json`` were fitted to CoolProp at stated states; running the same
fits now must choose the same method and degree, stay within the record's tolerance, and agree
with the stored record within its stored error. With the CoolProp version the records name, the
fits are the same to rounding. Skipped without CoolProp."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from stokes.physics.materials.evaluate import evaluate_all
from stokes.physics.materials.fit import (
    FITTED_PROPERTIES,
    FitInput,
    fit_property,
    verification_grid,
)
from stokes.physics.materials.providers import builtin, get_provider, remote
from tests.test_materials_support import needs_coolprop, warm_worker  # noqa: F401 - fixture

pytestmark = [needs_coolprop, pytest.mark.usefixtures("warm_worker")]
FLUIDS = [r["id"] for r in builtin.load() if r["kind"] == "fluid"]


def library_source(record: dict[str, Any]) -> tuple[str, str, str, str]:  # Any: a record
    """(provider, fluid, composition, basis) the record was generated from."""
    generated = record["generated"]
    fluid = str(generated["fluid"])
    if generated["provider"] == "coolprop_incomp" and "[" in fluid:
        name, fraction = fluid.rstrip("]").split("[")
        return "coolprop_incomp", name, fraction, "mass"
    return str(generated["provider"]), fluid, "", ""


def test_there_are_eleven_fluid_records() -> None:
    assert len(FLUIDS) == 11


@pytest.mark.parametrize("rid", FLUIDS)
def test_a_record_regenerates_within_its_stored_error(rid: str) -> None:
    record = builtin.record(rid)
    state = record["state"]
    provider_name, fluid, composition, basis = library_source(record)
    provider = get_provider(provider_name)
    phase = str(record["phase"])
    t = verification_grid(float(state["T_min"]), float(state["T_max"]))
    library = provider.evaluate(fluid, composition, basis, phase, t, float(state["p_ref"]))
    at_ref = provider.evaluate(
        fluid, composition, basis, phase, np.array([float(state["T_ref"])]), float(state["p_ref"])
    )
    stored_state = builtin.state_of(record)
    stored = evaluate_all(
        builtin.definitions_of(record),
        stored_state,
        list(FITTED_PROPERTIES),
        t,
        np.full_like(t, float(state["p_ref"])),
    )
    same_version = remote.package_version("coolprop") == record["generated"]["version"]
    for prop in FITTED_PROPERTIES:
        fit = record["generated"]["fits"][prop]
        values = library.values[prop]
        # The record still reproduces the library within the error it states.
        deviation = float(np.max(np.abs(stored[prop] - values) / values))
        assert deviation <= fit["max_rel_err"] * (1.0 + 1e-6) + 1e-9, (rid, prop)
        result = fit_property(
            FitInput(
                kind=prop,
                phase=phase,
                t=t,
                values=values,
                p_ref=float(state["p_ref"]),
                T_ref=float(state["T_ref"]),
                at_ref=float(at_ref.values[prop][0]),
                molar_mass=stored_state.molar_mass,
                tolerance=float(state["tolerance"]),
                max_degree=int(state["max_degree"]),
            )
        )
        assert (result.method, result.degree) == (fit["method"], fit["degree"]), (rid, prop)
        assert result.ok
        regenerated = evaluate_all(
            {prop: _definition(prop, result.entry())},
            stored_state,
            [prop],
            t,
            np.full_like(t, float(state["p_ref"])),
        )[prop]
        apart = float(np.max(np.abs(regenerated - stored[prop]) / stored[prop]))
        assert apart <= fit["max_rel_err"] + result.max_rel_err, (rid, prop)
        if same_version:
            # A constant is the library's value at T_ref; the records interpolated it from
            # their 101 nodes, which is about 1e-6 away. Everything else is the same fit.
            slack = 2e-6 if result.method == "constant" else 1e-6
            assert abs(result.max_rel_err - fit["max_rel_err"]) <= slack * max(
                1.0, fit["max_rel_err"]
            ), (rid, prop)
            for key, value in result.params.items():
                expected = record["properties"][prop][key]
                assert value == pytest.approx(expected, rel=slack, abs=1e-12), (rid, prop, key)


def _definition(prop: str, entry: dict[str, Any]) -> Any:  # Any: JSON entry in, Definition out
    from stokes.physics.materials.evaluate import definition_from_entry

    return definition_from_entry(prop, entry)
