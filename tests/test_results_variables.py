"""The variables a results view offers (`stokes.post.variables`, `results.variables`) and the
built-in derived quantities (dynamic and total pressure, the pressure coefficient), evaluated
through the same commands as any field."""

from __future__ import annotations

from typing import Any

import pytest

from stokes.api import Session
from stokes.api.commands import results as results_commands
from stokes.api.commands.results_variables import stored_fields
from stokes.post.variables import BUILTINS, builtin_definitions, catalogue, describe
from tests.test_results_commands import fake  # noqa: F401 - the fixture


def test_a_vector_is_offered_as_its_magnitude_and_components() -> None:
    stored = {"U": ("m/s", 3), "U_mag": ("m/s", 1), "p": ("m2/s2", 1)}
    names = [v["name"] for v in catalogue(stored, {}, True, True, True)]
    assert names[:4] == ["U_mag", "U_x", "U_y", "U_z"]
    assert "U" not in names
    assert names.index("p") < names.index("p_dynamic") < names.index("Cp")


def test_every_variable_says_its_group_units_and_meaning() -> None:
    stored = {
        "U": ("m/s", 3),
        "U_mag": ("m/s", 1),
        "p": ("Pa", 1),
        "T": ("K", 1),
        "k": ("m2/s2", 1),
    }
    rows = {v["name"]: v for v in catalogue(stored, {"q": "0.5*U_mag^2"}, False, True, True)}
    assert rows["T"]["group"] == "Temperature" and rows["T"]["units"] == "K"
    assert rows["k"]["group"] == "Turbulence" and rows["k"]["label"] == "Turbulent kinetic energy"
    assert rows["p_total"]["units"] == "Pa" and rows["Cp"]["units"] == ""
    assert rows["q"]["source"] == "function" and rows["q"]["group"] == "Functions"
    assert all(str(r["description"]) for r in rows.values())


def test_a_builtin_the_run_cannot_compute_says_why() -> None:
    absolute = {"U": ("m/s", 3), "U_mag": ("m/s", 1), "p": ("Pa", 1)}
    rows = {v["name"]: v for v in catalogue(absolute, {}, False, False, True)}
    assert rows["p_total"]["available"] is False and "density" in str(rows["p_total"]["why"])
    no_ends = {v["name"]: v for v in catalogue(absolute, {}, False, True, False)}
    assert no_ends["Cp"]["available"] is False and "inlet" in str(no_ends["Cp"]["why"])
    no_pressure = {v["name"]: v for v in catalogue({"T": ("K", 1)}, {}, True, True, True)}
    assert no_pressure["p_total"]["available"] is False


def test_the_definitions_follow_the_kind_of_pressure() -> None:
    assert builtin_definitions(True, False)["p_total"] == BUILTINS["p_total"].kinematic
    assert "rho" in builtin_definitions(False, True)["p_dynamic"]
    assert builtin_definitions(False, False) == {}


def test_an_unknown_field_is_still_offered_under_its_own_name() -> None:
    assert describe("alpha.water").group == "Other"
    assert describe("Ux2_mag").label == "Ux2 magnitude"


def test_stored_fields_come_from_every_patch_and_the_slice() -> None:
    header = {
        "units": {"U": "m/s", "p": "m2/s2", "T": "K"},
        "patches": [{"fields": {"U": {"components": 3}, "p": {"components": 1}}}],
        "slice": {"fields": {"T": {"components": 1}}},
    }
    assert stored_fields(header) == {"U": ("m/s", 3), "p": ("m2/s2", 1), "T": ("K", 1)}


def test_builtins_evaluate_like_any_field(fake: Session, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """On the fake plate: p = 1 + x, |U| = 2, so p_dynamic = 2, p_total = 3 + x, and with
    U_ref = 2 and p_ref = 1, Cp = x / 2."""
    total = fake.execute("results.range", surfaces=["inlet"], field="p_total", share=0)
    assert (total["min"], total["max"], total["units"]) == (
        pytest.approx(3.0),
        pytest.approx(4.0),
        "m2/s2",
    )

    def refs(_ctx: Any, _run: str) -> dict[str, float]:
        return {"U_ref": 2.0, "p_ref": 1.0}

    monkeypatch.setattr(results_commands, "_references", refs)
    cp = fake.execute("results.range", surfaces=["inlet"], field="Cp", share=0)
    assert cp["units"] == "" and cp["max"] == pytest.approx(0.5)


def test_a_user_function_overrides_a_builtin(fake: Session) -> None:  # noqa: F811
    fake.execute("results.function_save", name="p_total", definition="2 * p")
    out = fake.execute("results.range", surfaces=["inlet"], field="p_total", share=0)
    assert out["max"] == pytest.approx(4.0)


def test_a_known_vector_s_components_join_its_group() -> None:
    assert describe("vorticity_x").group == "Flow features"
    assert describe("wallShearStress_z").label == "Wall shear stress Z"
    assert describe("foo_y").group == "Other"


def test_a_run_read_before_the_derived_quantities_says_how_to_get_them() -> None:
    from stokes.post.variables import READ_AGAIN

    old = {
        v["name"]: v
        for v in catalogue(
            {"U": ("m/s", 3), "U_mag": ("m/s", 1), "T": ("K", 1)}, {}, True, True, True
        )
    }
    assert old["vorticity_mag"]["available"] is False and old["vorticity_mag"]["why"] == READ_AGAIN
    assert "wallHeatFlux" in old
    new = {
        v["name"]: v
        for v in catalogue({"U": ("m/s", 3), "vorticity_mag": ("1/s", 1)}, {}, True, True, True)
    }
    assert new["vorticity_mag"]["available"] is True and "yPlus" not in new
