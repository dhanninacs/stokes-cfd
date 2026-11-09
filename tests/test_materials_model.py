"""Material node types (stokes.physics.materials.model, ADR-0018 decision 1): generated schemas,
the new dimensions, the children ``material.create`` builds per kind, persistence, what the model
does not yet stop (removing or renaming a property, reported by material.check), and the import
discipline that keeps numpy out of ``import stokes.api``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from stokes.api import Session
from stokes.model import Project, load_project, registry, save_project
from stokes.model.ids import stable_id
from stokes.physics.materials import model
from stokes.units import DIMENSIONS, display_unit, format_quantity, quantity

NEW_DIMENSIONS = {
    "molar_mass": "kg/mol",
    "thermal_expansion": "1/K",
    "specific_energy": "J/kg",
    "specific_entropy": "J/(kg*K)",
    "specific_volume": "m**3/kg",
    "sutherland_coefficient": "kg/(m*s*K**0.5)",
    "surface_tension": "N/m",
}


def test_the_material_types_are_registered_with_generated_schemas() -> None:
    assert {"material", "property"} <= set(registry.types())
    schema = registry.json_schema()
    material = schema["material"]
    assert material["x-children"] == ["property"] and material["description"]
    assert material["properties"]["kind"]["enum"] == list(model.MATERIAL_KINDS)
    assert material["properties"]["provider"]["enum"] == list(model.PROVIDERS)
    assert material["properties"]["T_ref"]["x-dimension"] == "temperature"
    assert material["properties"]["p_ref"]["default"] == 101325.0
    prop = schema["property"]["properties"]
    assert schema["property"]["x-children"] == []
    assert prop["kind"]["enum"] == list(model.PROPERTY_KINDS)
    assert prop["method"]["enum"] == list(model.METHOD_IDS)
    assert prop["value"]["x-dimension"] == "dynamic"
    assert prop["sutherland_coefficient"]["x-dimension"] == "sutherland_coefficient"
    assert prop["breakpoints"]["x-dimension"] == "temperature"
    assert set(prop["dimension"]["enum"]) == set(model.PROPERTY_DIMENSIONS.values())


def test_the_new_dimensions() -> None:
    for name, si_unit in NEW_DIMENSIONS.items():
        assert DIMENSIONS[name].si_unit == si_unit
    for system in ("si", "mm", "imperial"):
        assert display_unit("molar_mass", system) == "g/mol"
    assert display_unit("specific_volume", "imperial") == "ft**3/lb"
    q = quantity(28.97, "g/mol")
    assert float(q.to("kg/mol").magnitude) == pytest.approx(0.02897)
    text = format_quantity(quantity(1.458e-6, "kg/(m*s*K**0.5)"))
    assert text.startswith("1.458e-06")


def test_create_builds_the_children_of_each_kind(session: Session) -> None:
    fluid = session.execute("material.create", name="water")
    assert list(fluid["properties"]) == list(model.KIND_PROPERTIES["fluid"])
    for kind, pid in fluid["properties"].items():
        assert pid == stable_id("n", f"{fluid['id']}/{kind}")
        node = session.execute("node.get", node=pid)
        assert node["params"]["kind"] == kind
        assert node["params"]["dimension"] == model.PROPERTY_DIMENSIONS[kind]
        assert node["params"]["method"] == "constant"
    solid = session.execute("material.create", name="block", kind="solid")
    assert list(solid["properties"]) == list(model.KIND_PROPERTIES["solid"])
    density = session.execute("node.get", node="/physics/materials/block/density")
    assert density["params"]["value"] == {"value": 2699.0, "unit": "kg/m**3"}  # aluminium
    assert density["displays"]["value"].startswith("2699")


def test_a_property_added_by_hand_starts_valid(session: Session) -> None:
    session.execute("material.create", name="water")
    added = session.execute(
        "node.add", parent="/physics/materials/water", type="property", name="spare"
    )
    assert added["params"]["kind"] == "density" and added["params"]["method"] == "constant"
    assert added["params"]["valid_max"] == {"value": 0.0, "unit": "K"}


def test_a_material_survives_save_and_load(tmp_path: Path) -> None:
    session = Session()
    session.execute("project.new", name="demo")
    session.execute("material.add_from_library", fluid="air")
    session.execute("material.create", name="block", kind="solid")
    project = session.require_project()
    folder = save_project(project, tmp_path / "demo")
    loaded = load_project(folder)
    assert loaded.root.to_json() == project.root.to_json()
    viscosity = loaded.get("/physics/materials/air/viscosity")
    assert viscosity.meta["fit"]["provider"] == "coolprop"
    assert viscosity.meta["library"]["record"] == "air"


def test_removing_or_renaming_a_property_is_reported_not_prevented(session: Session) -> None:
    # The model cannot refuse it in slice 1 (no owned-children rule in stokes.model yet), so the
    # check says what is wrong and undo puts it back.
    session.execute("material.create", name="water")
    session.execute("node.remove", node="/physics/materials/water/viscosity")
    codes = [f["code"] for f in session.execute("material.check", material="water")["findings"]]
    assert codes == ["materials.structure"]
    session.execute("history.undo")
    assert session.execute("material.check", material="water")["ok"] is True
    session.execute("node.rename", node="/physics/materials/water/density", name="rho")
    findings = session.execute("material.check", material="water")["findings"]
    assert {f["code"] for f in findings} == {"materials.structure"} and len(findings) == 2


def test_property_values_are_expression_targets(session: Session) -> None:
    session.execute("parameter.define", name="rho_w", dimension="density", value="1000 [kg/m**3]")
    session.execute("material.create", name="water")
    session.execute(
        "parameter.set",
        node="/physics/materials/water/density",
        name="value",
        value="rho_w * 0.998",
    )
    project = session.require_project()
    assert project.evaluate("physics.materials.water.density.value").magnitude == pytest.approx(
        998.0
    )
    session.execute("parameter.set", node="/physics/parameters/rho_w", name="value", value=990.0)
    density = project.get("/physics/materials/water/density").params["value"]
    assert density.value == pytest.approx(990.0 * 0.998) and density.expr == "rho_w * 0.998"


def test_material_types_are_known_to_a_project_built_from_scratch() -> None:
    project = Project.new("bare")
    group = project.get(model.MATERIALS_PATH)
    material = project.add(group.id, "material", "m", {"kind": "solid"})
    assert material.params["source"] == "user" and material.params["T_ref"].value == 300.0


def run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=180
    )


def test_the_api_loads_only_the_numpy_free_node_types() -> None:
    allowed = ("stokes.physics", "stokes.physics.materials", "stokes.physics.materials.model")
    code = (
        "import sys, stokes.api; "
        "bad = sorted(m for m in sys.modules if (m.startswith('stokes.physics') and m not in "
        f"{allowed!r}) or m.split('.')[0] == 'numpy'); "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    completed = run(code)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_the_catalogue_and_the_command_module_load_without_numpy() -> None:
    code = (
        "import sys, stokes.physics.materials.catalogue, stokes.api.commands.material; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'numpy'); "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    completed = run(code)
    assert completed.returncode == 0, completed.stdout + completed.stderr
