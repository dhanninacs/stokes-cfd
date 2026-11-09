"""material.* through Session.execute (ADR-0018 slice 1): schemas, creation from templates and
from the built-in library with provenance, set_method with its refusals and undo, evaluation,
the check codes, the library queries without a project, journal replay, and a study varying a
material property."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Journal, Session, default_registry, replay
from stokes.errors import ModelError, ReferencedNode, UnknownNode, ValidationError
from stokes.physics.materials.errors import (
    EvaluationFailed,
    FluidUnknown,
    InvalidMethod,
    NeedsMolarMass,
    ProviderMissing,
)
from stokes.physics.materials.providers import builtin
from tests.test_study_support import recorder

COMMANDS = {
    "material.methods": (False, False),
    "material.create": (True, True),
    "material.add_from_library": (True, True),
    "material.set_method": (True, True),
    "material.evaluate": (False, True),
    "material.check": (False, True),
    "material.list": (False, True),
    "material.library_search": (False, False),
    "material.library_info": (False, False),
    "material.providers": (False, False),
    "material.refit": (True, True),
    "material.set_state": (True, True),
    "material.cancel_fit": (False, True),
}
"""name: (mutates, requires_project)"""
WATER = "/physics/materials/water"


def params(session: Session, path: str) -> dict[str, Any]:
    found: dict[str, Any] = session.execute("node.get", node=path)["params"]
    return found


def codes(session: Session, material: str) -> list[str]:
    return [f["code"] for f in session.execute("material.check", material=material)["findings"]]


def test_commands_are_registered_with_their_schemas() -> None:
    schema = {c["name"]: c for c in default_registry().json_schema()}
    for name, (mutates, requires_project) in COMMANDS.items():
        assert schema[name]["mutates"] is mutates, name
        assert schema[name]["requires_project"] is requires_project, name
        assert schema[name]["description"], name
    assert schema["material.create"]["input_schema"]["required"] == ["name"]
    assert schema["material.set_method"]["input_schema"]["required"] == [
        "material",
        "property",
        "method",
    ]
    assert schema["material.evaluate"]["input_schema"]["properties"]["count"]["default"] == 101


def test_methods_lists_the_catalogue_without_a_project() -> None:
    session = Session()
    fluid = session.execute("material.methods")
    assert {m["property"] for m in fluid} == {
        "density",
        "viscosity",
        "specific_heat",
        "thermal_conductivity",
        "molar_mass",
        "formation_enthalpy",
    }
    sutherland = next(m for m in fluid if m["id"] == "sutherland")
    assert [(p["name"], p["dimension"]) for p in sutherland["params"]] == [
        ("sutherland_coefficient", "sutherland_coefficient"),
        ("sutherland_temperature", "temperature"),
    ]
    assert sutherland["of14"].startswith("sutherland") and sutherland["available"] is True
    density = session.execute("material.methods", kind="solid", property="density")
    assert [m["id"] for m in density] == ["constant"]
    assert density[0]["of14"].startswith("rhoConst")
    assert density[0]["params"][0]["dimension"] == "density"
    carreau = next(m for m in fluid if m["id"] == "carreau")
    assert carreau["available"] is False and carreau["independent"] == "strain_rate"
    with pytest.raises(InvalidMethod):
        session.execute("material.methods", kind="mixture")


# ---------------------------------------------------------------- create


def test_create_is_one_undo_step_with_events(session: Session) -> None:
    seen = recorder(session, "node.added")
    created = session.execute("material.create", name="water", description="tap")
    assert created["path"] == WATER and created["params"]["source"] == "user"
    assert created["params"]["description"] == "tap"
    assert [d["path"] for _, d in seen] == [WATER] + [f"{WATER}/{k}" for k in created["properties"]]
    assert session.execute("history.state")["undo"] == "create material water"
    session.execute("history.undo")
    with pytest.raises(UnknownNode):
        session.execute("node.get", node=WATER)
    session.execute("history.redo")
    tree = session.execute("node.tree", root=WATER)
    assert tree["id"] == created["id"]
    assert {c["name"]: c["id"] for c in tree["children"]} == created["properties"]


def test_create_validates_kind_template_and_name(session: Session) -> None:
    with pytest.raises(ValidationError):
        session.execute("material.create", name="m", kind="plasma")
    with pytest.raises(ValidationError):
        session.execute("material.create", name="m", kind="solid", template="gas")
    with pytest.raises(ValidationError):
        session.execute("material.create", name="bad name")
    session.execute("material.create", name="m")
    with pytest.raises(ModelError):
        session.execute("material.create", name="m")
    assert [m["name"] for m in session.execute("material.list")] == ["m"]


def test_list_names_the_regions_that_use_each_material(session: Session) -> None:
    """ADR-0022 decision 4.1: regions reference materials, and material.list says which."""
    session.execute("material.create", name="water")
    session.execute("material.create", name="alu", kind="solid")
    one = session.execute("region.create", name="one", material="water")["id"]
    two = session.execute("region.create", name="two", material="water")["id"]
    sink = session.execute("region.create", name="sink", material="alu")["id"]
    session.execute("region.create", name="empty")
    found = {m["name"]: m["regions"] for m in session.execute("material.list")}
    assert found == {"water": [one, two], "alu": [sink]}
    session.execute("region.set", region="two", material="alu", kind="solid")
    found = {m["name"]: m["regions"] for m in session.execute("material.list")}
    assert found == {"water": [one], "alu": [two, sink]}
    with pytest.raises(ReferencedNode):  # a material a region uses stays
        session.execute("node.remove", node=WATER)


@pytest.mark.parametrize(
    ("template", "record"), [("liquid", "water_liquid"), ("gas", "air"), ("solid", "aluminium")]
)
def test_templates_come_from_the_builtin_catalogue(
    session: Session, template: str, record: str
) -> None:
    # Decisions log F6 (a): a material built from scratch takes the app's own library, not
    # Fluent's database: each property is the record's value at its T_ref, held constant
    # (F6 (b)), except a gas's density, which keeps the record's ideal-gas law.
    kind = "solid" if template == "solid" else "fluid"
    made = session.execute("material.create", name="m", kind=kind, template=template)
    found = builtin.record(record)
    t_ref = found["state"]["T_ref"]
    expected = session.execute("material.add_from_library", fluid=record, name="r")
    for prop in made["properties"]:
        mine = params(session, f"/physics/materials/m/{prop}")
        if found["properties"][prop]["method"] == "ideal_gas":
            assert mine["method"] == "ideal_gas"
            continue
        assert mine["method"] == "constant", prop
        value = session.execute("material.evaluate", material="r", T=[t_ref], properties=[prop])[
            "properties"
        ][prop]["method"][0]
        assert mine["value"]["value"] == pytest.approx(value, rel=1e-12), prop
    assert made["params"]["T_min"]["value"] == found["state"]["T_min"]
    meta = session.require_project().get("/physics/materials/m").meta
    assert meta["template"] == {"name": template, "record": record}
    assert expected["params"]["source"] == "builtin" and made["params"]["source"] == "user"
    assert session.execute("material.check", material="m")["ok"] is True


def test_no_template_value_comes_from_fluents_database(session: Session) -> None:
    session.execute("material.create", name="water")
    session.execute("material.create", name="air", template="gas")
    session.execute("material.create", name="block", kind="solid")
    fluent = {  # Fluent's water-liquid, air and aluminum entries, which slice 1 used
        "water": {"density": 998.2, "viscosity": 1.003e-3, "specific_heat": 4182.0},
        "air": {"specific_heat": 1006.43, "thermal_conductivity": 0.0242},
        "block": {"density": 2719.0, "specific_heat": 871.0, "thermal_conductivity": 202.4},
    }
    for name, values in fluent.items():
        for prop, value in values.items():
            mine = params(session, f"/physics/materials/{name}/{prop}")["value"]["value"]
            assert mine != pytest.approx(value, rel=1e-4), (name, prop)
    mu = params(session, "/physics/materials/air/viscosity")
    assert mu["method"] == "constant"


# ---------------------------------------------------------------- the library


def test_add_from_library_keeps_the_records_methods_and_provenance(session: Session) -> None:
    seen = recorder(session, "node.added")
    added = session.execute("material.add_from_library", fluid="water_liquid")
    assert added["name"] == "water_liquid" and added["record"] == "water_liquid"
    assert added["params"]["source"] == "builtin" and added["params"]["fluid"] == "water_liquid"
    assert added["params"]["T_min"] == {"value": 275.0, "unit": "K"}
    assert len(seen) == 7
    project = session.require_project()
    viscosity = project.get("/physics/materials/water_liquid/viscosity")
    assert viscosity.params["method"] == "log_polynomial"
    assert viscosity.meta["fit"]["provider_version"] == "8.0.0"
    assert viscosity.meta["fit"]["max_rel_err"] <= viscosity.meta["fit"]["tolerance"]
    assert viscosity.meta["library"] == {
        "provider": "builtin",
        "record": "water_liquid",
        "method": "log_polynomial",
        "reference": builtin.record("water_liquid")["generated"]["references"],
    }
    again = session.execute("material.add_from_library", fluid="water_liquid")
    assert again["name"] == "water_liquid_2"
    copper = session.execute("material.add_from_library", fluid="copper", name="cu")
    cited = session.require_project().get(f"{copper['path']}/thermal_conductivity").meta["library"]
    assert cited["reference"]["primary"]["value"] == 4.01 and "fit" not in cited
    session.execute("history.undo")
    with pytest.raises(UnknownNode):
        session.execute("node.get", node="/physics/materials/cu")


def test_forced_forms_take_the_value_at_t_ref_or_the_grid(session: Session) -> None:
    added = session.execute(
        "material.add_from_library",
        fluid="water_liquid",
        name="water",
        T_ref="25 degC",
        methods={"density": "constant", "viscosity": "piecewise_linear"},
    )
    assert added["params"]["T_ref"]["value"] == pytest.approx(298.15)
    record = builtin.record("water_liquid")
    rho = params(session, f"{WATER}/density")
    expected = session.execute("material.evaluate", material=WATER, T=[298.15], source="library")
    assert rho["method"] == "constant"
    assert rho["value"]["value"] == pytest.approx(expected["properties"]["density"]["library"][0])
    mu = params(session, f"{WATER}/viscosity")
    assert mu["method"] == "piecewise_linear" and len(mu["table_temperatures"]) == 101
    assert mu["table_temperatures"][0] == record["state"]["T_min"]
    assert mu["valid_max"]["value"] == record["properties"]["viscosity"]["valid_max"]
    meta = session.require_project().get(f"{WATER}/density").meta
    assert meta["library"]["derived"] == {"method": "constant", "T": pytest.approx(298.15)}
    assert "fit" not in meta
    with pytest.raises(ValidationError):
        session.execute("material.add_from_library", fluid="air", methods={"density": "polynomial"})


def test_library_errors(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from stokes.physics.materials.providers import remote

    with pytest.raises(FluidUnknown):
        session.execute("material.add_from_library", fluid="unobtainium")
    monkeypatch.setattr(remote, "installed", lambda name: False)  # the extra is not installed
    with pytest.raises(ProviderMissing) as missing:
        session.execute("material.add_from_library", fluid="Water", provider="coolprop")
    assert "stokes-cfd[materials]" in missing.value.message
    with pytest.raises(ValidationError):
        session.execute("material.add_from_library", fluid="Water", provider="nist")
    assert session.execute("material.list") == []


def test_library_queries_need_no_project(monkeypatch: pytest.MonkeyPatch) -> None:
    from stokes.physics.materials.providers import remote

    session = Session()
    found = session.execute("material.library_search", query="nitrogen", providers=["builtin"])
    assert found["total"] == 1 and found["records"][0]["fluid"] == "nitrogen"
    assert found["records"][0]["molar_mass"] == pytest.approx(0.02801348)
    solids = session.execute("material.library_search", kind="solid", limit=3)
    assert solids["total"] == 9 and len(solids["records"]) == 3  # library providers: fluids
    info = session.execute("material.library_info", fluid="stainless_304")
    assert info["methods"]["density"] == {"method": "constant", "value": 7894.0}
    assert info["references"]["density"]["secondary"]["value"] == 7.9
    monkeypatch.setattr(remote, "installed", lambda name: False)
    with pytest.raises(ProviderMissing):
        session.execute("material.library_search", providers=["cantera"])
    assert session.execute("material.library_search", query="water")["records"][0]["provider"] == (
        "builtin"
    )
    listed = session.execute("material.providers")
    assert listed[0]["name"] == "builtin" and listed[0]["available"] is True
    assert all(p["available"] is False for p in listed[1:])


# ---------------------------------------------------------------- set_method


def test_set_method_is_one_undo_step_with_events(session: Session) -> None:
    session.execute("material.create", name="water")
    seen = recorder(session, "node.changed")
    result = session.execute(
        "material.set_method",
        material="water",
        property="density",
        method="polynomial",
        params={"coefficients": [1000.0, -0.05]},
        valid_min="0 degC",
        valid_max=373.15,
    )
    assert result["params"]["method"] == "polynomial"
    assert result["params"]["coefficients"] == [1000.0, -0.05]
    assert result["params"]["valid_min"]["value"] == pytest.approx(273.15)
    assert [d["param"] for _, d in seen] == ["method", "coefficients", "valid_min", "valid_max"]
    assert session.execute("history.state")["undo"] == "set density method of water"
    session.execute("history.undo")
    assert params(session, f"{WATER}/density")["method"] == "constant"
    session.execute("history.redo")
    assert params(session, f"{WATER}/density")["coefficients"] == [1000.0, -0.05]


def test_set_method_refuses_and_changes_nothing(session: Session) -> None:
    session.execute("material.create", name="water")
    before = session.execute("node.tree", root=WATER)
    cases: list[tuple[dict[str, Any], type[Exception]]] = [
        ({"property": "density", "method": "constant", "params": {"exponent": 2}}, ValidationError),
        ({"property": "density", "method": "sutherland"}, InvalidMethod),
        ({"property": "viscosity", "method": "carreau"}, InvalidMethod),
        (
            {"property": "density", "method": "polynomial", "params": {"coefficients": []}},
            ValidationError,
        ),
        (
            {"property": "density", "method": "constant", "params": {"value": "2 [m/s]"}},
            ValidationError,
        ),
        ({"property": "surface_tension", "method": "constant"}, ValidationError),
    ]
    for case, error in cases:
        with pytest.raises(error):
            session.execute("material.set_method", material="water", **case)
    assert session.execute("node.tree", root=WATER) == before
    session.execute("material.create", name="block", kind="solid")
    with pytest.raises(InvalidMethod):
        session.execute(
            "material.set_method", material="block", property="density", method="ideal_gas"
        )
    with pytest.raises(ValidationError):
        session.execute(
            "material.set_method", material="block", property="viscosity", method="constant"
        )
    with pytest.raises(UnknownNode):
        session.execute(
            "material.set_method", material="nowhere", property="density", method="constant"
        )


def test_methods_that_need_the_molar_mass_wait_for_it(session: Session) -> None:
    session.execute("material.create", name="gas", template="liquid")
    session.execute(
        "material.set_method",
        material="gas",
        property="molar_mass",
        method="constant",
        params={"value": 0},
    )
    with pytest.raises(NeedsMolarMass):
        session.execute(
            "material.set_method", material="gas", property="density", method="ideal_gas"
        )
    assert params(session, "/physics/materials/gas/density")["method"] == "constant"
    session.execute(
        "material.set_method",
        material="gas",
        property="molar_mass",
        method="constant",
        params={"value": "28.97 [g/mol]"},
    )
    session.execute("material.set_method", material="gas", property="density", method="ideal_gas")
    rho = session.execute("material.evaluate", material="gas", T=[300], properties=["density"])
    assert rho["properties"]["density"]["method"][0] == pytest.approx(1.1768, rel=1e-3)


def test_editing_a_library_property_detaches_it_undoably(session: Session) -> None:
    session.execute("material.add_from_library", fluid="air")
    path = "/physics/materials/air/viscosity"
    session.execute(
        "material.set_method",
        material="air",
        property="viscosity",
        method="sutherland",
        params={"sutherland_coefficient": 1.458e-6, "sutherland_temperature": 110.4},
    )
    project = session.require_project()
    assert project.get(path).meta["detached"] is True
    assert project.get(path).meta["fit"]["method"] == "log_polynomial"  # what the record held
    session.execute("history.undo")
    assert "detached" not in project.get(path).meta
    assert project.get(path).params["method"] == "log_polynomial"


# ---------------------------------------------------------------- evaluate


def test_evaluate_gives_si_values_with_units(session: Session) -> None:
    session.execute("material.add_from_library", fluid="air")
    result = session.execute("material.evaluate", material="air", count=5)
    assert result["T"] == pytest.approx([200.0, 400.0, 600.0, 800.0, 1000.0])
    assert result["p"] == 101325.0 and result["source"] == "method"
    rho = result["properties"]["density"]
    assert rho["dimension"] == "density" and rho["unit"] == "kg/m**3"
    assert rho["method_id"] == "ideal_gas" and rho["library"] is None
    assert rho["method"][0] == pytest.approx(101325.0 * 0.02896546 / (8.31446261815324 * 200.0))
    assert result["properties"]["molar_mass"]["display_unit"] == "g/mol"
    doubled = session.execute(
        "material.evaluate", material="air", T=["300 K", 600], p="2 [bar]", properties=["density"]
    )
    assert doubled["properties"]["density"]["method"][0] == pytest.approx(
        2.0e5 * 0.02896546 / (8.31446261815324 * 300.0)
    )
    both = session.execute("material.evaluate", material="air", count=3, source="both")
    assert both["properties"]["viscosity"]["max_rel_err"] == 0.0
    session.execute("material.create", name="user")
    mine = session.execute("material.evaluate", material="user", count=3, source="both")
    assert mine["properties"]["density"]["library"] is None


def test_evaluate_reports_gaps_and_refuses_bad_grids(session: Session) -> None:
    session.execute("material.create", name="water")
    session.execute(
        "material.set_method",
        material="water",
        property="density",
        method="rational_polynomial",
        params={"coefficients": [0.0, 0.0, 0.0, 0.0, 0.0]},
    )
    result = session.execute("material.evaluate", material="water", T=[300], properties=["density"])
    assert result["properties"]["density"]["method"] == [None]
    with pytest.raises(ValidationError):
        session.execute("material.evaluate", material="water", T_min=400, T_max=300)
    with pytest.raises(ValidationError):
        session.execute("material.evaluate", material="water", count=1)
    with pytest.raises(ValidationError):
        session.execute("material.evaluate", material="water", properties=["colour"])
    with pytest.raises(ValidationError):
        session.execute("material.evaluate", material="water", source="dream")
    with pytest.raises(ValidationError):
        session.execute("material.evaluate", material="water", T=[-5])


def test_an_anisotropic_conductivity_has_three_components(session: Session) -> None:
    session.execute("material.create", name="laminate", kind="solid")
    session.execute(
        "material.set_method",
        material="laminate",
        property="thermal_conductivity",
        method="anisotropic",
        params={"principal_values": [0.5, 0.5, 20.0]},
    )
    result = session.execute(
        "material.evaluate", material="laminate", count=2, properties=["thermal_conductivity"]
    )
    k = result["properties"]["thermal_conductivity"]
    assert k["components"] == ["k1", "k2", "k3"] and k["method"][2] == [20.0, 20.0]


# ---------------------------------------------------------------- check


def test_check_codes(session: Session) -> None:
    session.execute("material.create", name="water")
    assert session.execute("material.check", material="water") == {
        "material": session.execute("node.get", node=WATER)["id"],
        "path": WATER,
        "name": "water",
        "findings": [],
        "ok": True,
    }
    session.execute(
        "material.set_method",
        material="water",
        property="density",
        method="boussinesq",
        params={"value": 998.0, "expansion_coefficient": "2e-3 [1/K]"},
    )
    assert codes(session, "water") == ["materials.boussinesq_range"]
    assert session.execute("material.check", material="water")["ok"] is True
    session.execute(
        "material.set_method",
        material="water",
        property="viscosity",
        method="polynomial",
        params={"coefficients": [1e-3, -1e-5]},
        valid_max=330,
    )
    assert set(codes(session, "water")) == {
        "materials.evaluation",  # 1e-3 - 1e-5 T is negative over the whole range
        "materials.extrapolation",
        "materials.boussinesq_range",
    }
    check = session.execute("material.check", material="water")
    assert check["ok"] is False and check["findings"][0]["severity"] == "error"
    evaluation = next(f for f in check["findings"] if f["code"] == "materials.evaluation")
    assert evaluation["property"] == "viscosity" and "not positive" in evaluation["message"]


def test_check_codes_for_state_and_molar_mass(session: Session) -> None:
    session.execute("material.create", name="air", template="gas")
    session.execute(
        "parameter.set", node="/physics/materials/air/molar_mass", name="value", value=0
    )
    assert codes(session, "air") == ["materials.needs_molar_mass"]
    session.execute("history.undo")
    session.execute("parameter.set", node="/physics/materials/air", name="T_min", value=1200)
    assert codes(session, "air") == ["materials.range"]
    session.execute("history.undo")
    session.execute(
        "parameter.set", node="/physics/materials/air/density", name="method", value="carreau"
    )
    assert codes(session, "air") == ["materials.invalid_method"]
    session.execute("history.undo")
    project = session.require_project()
    project.set_status("/physics/materials/air/specific_heat", "stale")
    assert codes(session, "air") == ["materials.stale_fit"]
    statuses = {m["name"]: m["status"] for m in session.execute("material.list")}
    assert statuses == {"air": "stale"}


def test_a_failed_expression_is_an_evaluation_error(session: Session) -> None:
    session.execute("parameter.define", name="rho_w", dimension="density", value="1000 [kg/m**3]")
    session.execute("material.create", name="water")
    session.execute("parameter.set", node=f"{WATER}/density", name="value", value="rho_w * 1.0")
    session.execute("node.remove", node="/physics/parameters/rho_w")
    assert codes(session, "water") == ["materials.evaluation"]
    assert session.execute("material.list")[0]["status"] == "failed"
    with pytest.raises(EvaluationFailed):
        session.execute("material.evaluate", material="water", properties=["density"])
    viscosity = session.execute("material.evaluate", material="water", properties=["viscosity"])
    from stokes.physics.materials.authoring import template

    expected = template("liquid")["properties"]["viscosity"]["value"]
    assert viscosity["properties"]["viscosity"]["method"][0] == pytest.approx(expected)


def test_a_failed_material_state_cannot_be_evaluated(session: Session) -> None:
    session.execute("parameter.define", name="T_op", dimension="temperature", value="300 [K]")
    session.execute("material.create", name="water")
    session.execute("parameter.set", node=WATER, name="T_ref", value="T_op")
    session.execute("node.remove", node="/physics/parameters/T_op")
    with pytest.raises(EvaluationFailed):
        session.execute("material.evaluate", material="water")


# ---------------------------------------------------------------- journals and studies


def test_journal_replays_materials_exactly(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    first = Session(journal_path=journal)
    first.execute("project.new", name="demo")
    first.execute("material.create", name="water")
    first.execute("material.add_from_library", fluid="air")
    first.execute("material.add_from_library", fluid="air", methods={"density": "constant"})
    first.execute(
        "material.set_method",
        material="water",
        property="viscosity",
        method="power_law",
        params={"value": "1 [mPa*s]", "reference_temperature": 293.15, "exponent": -1.5},
    )
    first.execute("material.evaluate", material="water")  # queries are not journaled
    first.execute("material.check", material="air")
    records = Journal.read(journal)
    assert [r["command"] for r in records] == [
        "project.new",
        "material.create",
        "material.add_from_library",
        "material.add_from_library",
        "material.set_method",
    ]
    second = Session()
    second.execute("project.new", name="other")
    replay(second, records[1:])
    root = "/physics/materials"
    assert second.execute("node.tree", root=root) == first.execute("node.tree", root=root)
    left = first.require_project().get(f"{root}/air_2/viscosity").meta
    right = second.require_project().get(f"{root}/air_2/viscosity").meta
    assert left == right and "fit" in left


def test_a_study_varies_a_material_property(session: Session) -> None:
    session.execute("material.create", name="water")
    session.execute("study.create", name="sweep")
    session.execute(
        "study.add_variable",
        study="sweep",
        parameter="physics.materials.water.viscosity.value",
        values=["1 [mPa*s]", "2 [mPa*s]"],
        name="mu",
    )
    session.execute(
        "study.add_response",
        study="sweep",
        expression="physics.materials.water.viscosity.value * 1000 [m*s/kg]",
        name="mu_scaled",
    )
    session.execute("study.generate", study="sweep")
    result = session.execute("study.update", study="sweep")
    outputs = [p["outputs"]["mu_scaled"] for p in result["points"]]
    assert outputs == pytest.approx([1.0, 2.0])
    assert all(math.isfinite(v) for v in outputs)


# ---------------------------------------------------------------- F6 (c): hand edits detach


def test_parameter_set_on_a_library_property_detaches_it_in_one_undo_step(
    session: Session,
) -> None:
    session.execute("material.add_from_library", fluid="air")
    path = "/physics/materials/air/viscosity"
    prop = session.require_project().get(path)
    assert "detached" not in prop.meta and prop.meta["signature"]["params"]
    seen = recorder(session, "node.changed")
    session.execute("parameter.set", node=path, name="valid_max", value=900)
    assert prop.meta["detached"] is True and prop.params["valid_max"].value == 900.0
    assert session.execute("history.state")["undo"] == "set valid_max (detached from the library)"
    assert any(d.get("path") == path and "param" not in d for _, d in seen)  # meta announced
    assert session.execute("material.list")[0]["detached"] == ["viscosity"]
    session.execute("history.undo")  # one step takes back the edit and the mark
    assert "detached" not in prop.meta and prop.params["valid_max"].value == 1000.0
    assert session.execute("material.list")[0]["detached"] == []
    session.execute("history.redo")
    assert prop.meta["detached"] is True and prop.params["valid_max"].value == 900.0
    session.execute("parameter.set", node=path, name="kind", value="viscosity")  # structural
    assert session.execute("history.state")["undo"] == "set kind"


def test_parameter_set_leaves_user_materials_alone(session: Session) -> None:
    session.execute("material.create", name="water")
    path = "/physics/materials/water/viscosity"
    session.execute("parameter.set", node=path, name="value", value="1 [mPa*s]")
    assert "detached" not in session.require_project().get(path).meta
    assert session.execute("history.state")["undo"] == "set value"


def test_a_hand_edit_made_before_any_material_command_is_still_seen(tmp_path: Path) -> None:
    first = Session()
    first.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    first.execute("material.add_from_library", fluid="steam")
    first.execute("project.save")
    second = Session()  # no material.* command yet: no listener in this session
    second.execute("project.open", path=str(tmp_path / "demo.stokes"))
    path = "/physics/materials/steam/density"
    second.execute("parameter.set", node=path, name="coefficients", value=[1.0, 0.0])
    assert "detached" not in second.require_project().get(path).meta
    assert second.execute("material.list")[0]["detached"] == ["density"]  # by its signature
    second.execute("history.undo")
    assert second.execute("material.list")[0]["detached"] == []


def test_set_state_on_a_user_material_is_one_undo_step(session: Session) -> None:
    session.execute("material.create", name="water")
    changed = session.execute(
        "material.set_state", material="water", T_min="5 degC", T_max=350, phase="liquid"
    )
    assert changed["params"]["T_min"]["value"] == pytest.approx(278.15)
    assert changed["fit"] == {} and changed["background"] is False
    assert session.execute("history.state")["undo"] == "set state of water"
    session.execute("history.undo")
    assert params(session, WATER)["phase"] == "auto"
    with pytest.raises(ValidationError):
        session.execute("material.set_state", material="water")
    with pytest.raises(ValidationError):
        session.execute("material.set_state", material="water", phase="plasma")
    with pytest.raises(ValidationError):
        session.execute("material.set_state", material="water", tolerance=2.0)
    with pytest.raises(ValidationError):
        session.execute("material.refit", material="water")
