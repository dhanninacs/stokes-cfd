"""The ``region`` and ``boundary`` node types (ADR-0022 decisions 2.1, 2.4 and 2.9; contract
"Nodes" in docs/engineering/setup-schema.md): registration, the parameters and their defaults,
references, the JSON schema, expressions and persistence. Nodes are made with ``node.add`` here;
the ``boundary.*`` and ``region.*`` commands that own them come with T-036."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.errors import ValidationError
from stokes.geometry import model as geometry
from stokes.model import NodeTypeRegistry, QuantityValue, load_project, registry, save_project
from stokes.model.schema import register_builtin_types
from stokes.physics.conditions import catalogue

TAGS = "/geometry/tags"
FLUID = "/regions/fluid"
BOUNDARIES = f"{FLUID}/boundaries"
Row = tuple[str, str, str | None, Any]
"""A parameter of ``boundary``: name, kind, dimension, default."""
ADR_PARAMS: list[Row] = [  # ADR-0022 decision 2.4, in its order
    ("kind", "enum", None, "wall"),
    ("tag", "ref", None, None),
    ("direction", "enum", None, "normal"),
    ("velocity", "quantity", "velocity", 0.0),
    ("velocity_x", "quantity", "velocity", 0.0),
    ("velocity_y", "quantity", "velocity", 0.0),
    ("velocity_z", "quantity", "velocity", 0.0),
    ("direction_vector", "array", "dimensionless", (1.0, 0.0, 0.0)),
    ("mass_flow", "quantity", "mass_flow", 0.0),
    ("pressure", "quantity", "pressure", 0.0),
    ("total_pressure", "quantity", "pressure", 0.0),
    ("temperature", "quantity", "temperature", 300.0),
    ("backflow_temperature", "quantity", "temperature", 300.0),
    ("turbulence", "enum", None, "intensity_ratio"),
    ("intensity", "number", None, 0.05),
    ("viscosity_ratio", "number", None, 10.0),
    ("length_scale", "quantity", "length", 0.01),
    ("shear", "enum", None, "no_slip"),
    ("thermal", "enum", None, "adiabatic"),
    ("heat_flux", "quantity", "heat_flux", 0.0),
    ("heat_transfer_coefficient", "quantity", "heat_transfer_coefficient", 10.0),
    ("ambient_temperature", "quantity", "temperature", 300.0),
    ("partner", "ref", None, None),
    # The wall layer override (brief M-504): no default, so absence is the empty = inherit
    # state, and null clears it. ADR-0022's own list ends at `partner`.
    ("layers", "int", None, None),
    # The per-boundary surface size (brief M-503): the same empty = inherit shape, in SI metres.
    ("mesh_size", "quantity", "length", None),
]


@pytest.fixture
def prepared(session: Session) -> Session:
    """A project with a boundary tag ``inlet``, a region tag ``fluid``, the region ``fluid`` on it
    and the region's ``boundaries`` group, all made with ``node.add``."""
    session.execute("node.add", parent="/geometry", type="group", name="tags")
    session.execute("node.add", parent=TAGS, type="tag", name="inlet")
    session.execute("node.add", parent=TAGS, type="tag", name="fluid", params={"kind": "region"})
    session.execute(
        "node.add", parent="/regions", type="region", name="fluid", params={"tag": f"{TAGS}/fluid"}
    )
    session.execute("node.add", parent=FLUID, type="group", name="boundaries")
    return session


def add_boundary(session: Session, name: str, **params: Any) -> str:
    added = session.execute(
        "node.add",
        parent=BOUNDARIES,
        type="boundary",
        name=name,
        params={"tag": f"{TAGS}/inlet", **params},
    )
    node_id: str = added["id"]
    return node_id


# ---------------------------------------------------------------- the types


def test_the_geometry_types_are_tag_region_boundary_and_the_operations() -> None:
    # ADR-0021 decision 8.1 registers one node type per operation beside the tags, regions and
    # boundaries, so the registry is where the operation cards and their parameter forms come
    # from; the operation types are named in OPERATION_TYPES (T-024).
    assert [spec.type for spec in geometry.geometry_specs()] == [
        "tag",
        "region",
        "boundary",
        *geometry.OPERATION_TYPE_NAMES,
    ]
    fresh = NodeTypeRegistry()
    register_builtin_types(fresh)
    assert {"tag", "region", "boundary"} <= set(fresh.types())
    assert set(geometry.OPERATION_TYPE_NAMES) <= set(fresh.types())
    assert fresh.get("boundary") == registry.get("boundary")
    assert fresh.get("region") == registry.get("region")
    assert (geometry.REGIONS_PATH, geometry.BOUNDARIES_GROUP) == ("/regions", "boundaries")
    assert (geometry.REGION_TYPE, geometry.BOUNDARY_TYPE) == ("region", "boundary")
    assert geometry.REGION_KINDS == ("fluid", "solid")
    assert geometry.DEFAULT_REGION == "fluid"


def test_boundary_has_the_parameters_of_the_adr_in_its_order() -> None:
    spec = registry.get("boundary")
    assert [(p.name, p.kind, p.dimension, p.default) for p in spec.params] == ADR_PARAMS
    assert spec.param("kind").choices == catalogue.KIND_IDS
    assert spec.param("tag").ref_type == "tag"
    assert spec.param("partner").ref_type == "boundary"
    assert spec.children == ()
    for param in spec.params:
        assert param.label and param.doc, param.name


def test_region_has_a_kind_a_tag_and_a_material() -> None:
    spec = registry.get("region")
    assert [(p.name, p.kind, p.default) for p in spec.params] == [
        ("kind", "enum", "fluid"),
        ("tag", "ref", None),
        ("material", "ref", None),
    ]
    assert spec.param("kind").choices == geometry.REGION_KINDS
    assert (spec.param("tag").ref_type, spec.param("material").ref_type) == ("tag", "material")
    assert spec.children == ("group",)  # its boundaries group
    assert not spec.allows_child("boundary")
    # **The doc says what a region IS, not which document decided it** (claude-win2's review
    # item 9): it read "A mesh region (ADR-0022 decision 2.9): …", and the ADR half reaches a
    # user as noise. What matters is that the words still describe the node.
    assert "region" in spec.doc.lower()
    assert "boundaries" in spec.doc


def test_the_json_schema_lists_every_parameter_with_its_dimension() -> None:
    schema = registry.get("boundary").json_schema()
    props = schema["properties"]
    assert list(props) == [row[0] for row in ADR_PARAMS]
    for name, kind, dimension, _ in ADR_PARAMS:
        if kind == "quantity":
            assert props[name]["x-kind"] == "quantity", name
            assert props[name]["x-dimension"] == dimension, name
    assert props["direction_vector"]["x-dimension"] == "dimensionless"
    assert list(props["direction_vector"]["default"]) == [1.0, 0.0, 0.0]
    assert props["tag"]["x-kind"] == "ref" and props["tag"]["x-ref-type"] == "tag"
    assert props["partner"]["x-ref-type"] == "boundary" and "default" not in props["partner"]
    assert props["kind"]["enum"] == list(catalogue.KIND_IDS) and props["kind"]["default"] == "wall"
    assert schema["x-children"] == []
    region = registry.get("region").json_schema()
    assert region["properties"]["tag"]["x-ref-type"] == "tag"
    assert region["properties"]["material"]["x-ref-type"] == "material"
    assert region["x-children"] == ["group"]
    assert "boundary" in Session().execute("node.types")


# ---------------------------------------------------------------- nodes


def test_a_boundary_added_with_node_add_is_a_valid_wall(prepared: Session) -> None:
    node_id = add_boundary(prepared, "walls")
    project = prepared.require_project()
    node = project.get(node_id)
    assert node.path == f"{BOUNDARIES}/walls"
    assert node.params["kind"] == "wall" and node.status == "ok"
    assert node.params["tag"] == project.get(f"{TAGS}/inlet").id
    assert node.params["partner"] is None
    got = prepared.execute("node.get", node=node_id)
    assert got["params"]["temperature"] == {"value": 300.0, "unit": "K"}
    assert got["params"]["direction_vector"] == [1.0, 0.0, 0.0]
    assert got["displays"]["temperature"]
    # Every default is a valid value of its parameter: setting it again changes nothing.
    for param in registry.get("boundary").params:
        if param.kind == "ref":
            continue
        if param.default is None:
            # **A parameter with no default has no "default value" to write back** (the layer
            # override): its absence IS the empty state, and null clears it. The round trip
            # that matters is covered by the layers tests.
            continue
        before = node.params[param.name]
        value = list(param.default) if param.kind == "array" else param.default
        prepared.execute("parameter.set", node=node_id, name=param.name, value=value)
        assert node.params[param.name] == before, param.name
    assert isinstance(node.params["velocity"], QuantityValue)


def test_kind_takes_the_catalogue_ids_and_refuses_others(prepared: Session) -> None:
    node_id = add_boundary(prepared, "inlet")
    for kind_id in catalogue.KIND_IDS:
        prepared.execute("parameter.set", node=node_id, name="kind", value=kind_id)
    with pytest.raises(ValidationError):
        prepared.execute("parameter.set", node=node_id, name="kind", value="fan")
    assert prepared.require_project().get(node_id).params["kind"] == "interface"
    with pytest.raises(ValidationError):
        add_boundary(prepared, "other", kind="porous_jump")


def test_partner_is_null_until_set_and_refuses_a_tag(prepared: Session) -> None:
    left = add_boundary(prepared, "left", kind="cyclic")
    right = add_boundary(prepared, "right", kind="cyclic")
    project = prepared.require_project()
    assert project.get(left).params["partner"] is None
    with pytest.raises(ValidationError):
        prepared.execute("parameter.set", node=left, name="partner", value=f"{TAGS}/inlet")
    assert project.get(left).params["partner"] is None
    prepared.execute("parameter.set", node=left, name="partner", value=f"{BOUNDARIES}/right")
    assert project.get(left).params["partner"] == right
    with pytest.raises(ValidationError):  # and a boundary's tag refuses a boundary
        prepared.execute("parameter.set", node=left, name="tag", value=right)


def test_a_region_material_refuses_a_part(prepared: Session) -> None:
    project = prepared.require_project()
    region = project.get(FLUID)
    assert region.type == "region"
    assert region.params["kind"] == "fluid" and region.params["material"] is None
    assert region.params["tag"] == project.get(f"{TAGS}/fluid").id
    prepared.execute("node.add", parent="/geometry", type="part", name="body")
    with pytest.raises(ValidationError):
        prepared.execute("parameter.set", node=FLUID, name="material", value="/geometry/body")
    water = prepared.execute("node.add", parent="/physics/materials", type="material", name="water")
    prepared.execute("parameter.set", node=FLUID, name="material", value=water["id"])
    assert region.params["material"] == water["id"]
    with pytest.raises(ValidationError):
        prepared.execute("parameter.set", node=FLUID, name="tag", value=water["id"])
    with pytest.raises(ValidationError):
        prepared.execute("parameter.set", node=FLUID, name="kind", value="gas")
    prepared.execute("parameter.set", node=FLUID, name="kind", value="solid")


def test_a_boundary_value_is_an_expression_reference(prepared: Session) -> None:
    """decision 2.1: regions.fluid.boundaries.inlet.velocity stays addressable through a change
    of kind, so fields and study variables can read it."""
    prepared.execute("parameter.define", name="U_in", dimension="velocity", value="2 [m/s]")
    node_id = add_boundary(prepared, "inlet", kind="velocity_inlet", velocity="U_in * 1.5")
    project = prepared.require_project()
    assert project.get(node_id).params["velocity"].value == pytest.approx(3.0)
    prepared.execute(
        "parameter.define",
        name="q",
        dimension="velocity",
        value="regions.fluid.boundaries.inlet.velocity * 2",
    )
    prepared.execute("parameter.set", node=node_id, name="kind", value="mass_flow_inlet")
    prepared.execute(
        "parameter.set", node="/physics/parameters/U_in", name="value", value="4 [m/s]"
    )
    assert project.get("/physics/parameters/q").params["value"].value == pytest.approx(12.0)


def test_the_catalogue_reads_a_boundary_as_it_is_stored(prepared: Session) -> None:
    node_id = add_boundary(prepared, "inlet", kind="velocity_inlet", direction="components")
    node = prepared.require_project().get(node_id)
    spec = catalogue.kind(node.params["kind"])
    used = [use.name for use in spec.uses(node.params)]
    assert used[:4] == ["direction", "velocity_x", "velocity_y", "velocity_z"]
    assert "velocity" not in used and "mass_flow" not in used


def test_regions_and_boundaries_survive_a_save_and_load(prepared: Session, tmp_path: Path) -> None:
    left = add_boundary(
        prepared,
        "left",
        kind="velocity_inlet",
        direction="vector",
        direction_vector=[0.0, 2.0, 0.0],
        velocity="3 [m/s]",
    )
    right = add_boundary(prepared, "right", kind="cyclic", partner=f"{BOUNDARIES}/left")
    project = prepared.require_project()
    loaded = load_project(save_project(project, tmp_path / "setup"))
    stored = loaded.get(left)
    assert stored.type == "boundary" and stored.path == f"{BOUNDARIES}/left"
    assert stored.params["direction_vector"] == [0.0, 2.0, 0.0]
    assert stored.params["velocity"].value == pytest.approx(3.0)
    assert stored.params["tag"] == project.get(f"{TAGS}/inlet").id
    assert loaded.get(right).params["partner"] == left
    assert loaded.get(FLUID).type == "region"
    assert loaded.get(FLUID).params["tag"] == project.get(f"{TAGS}/fluid").id
