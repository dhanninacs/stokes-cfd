"""boundary.* commands end to end through Session.execute (ADR-0022 decisions 2.8, 2.9 and 3;
contract: docs/engineering/setup-schema.md): the atomic assign onto picked faces, a tag, a
boundary and a plain-group boundary; one undo step that restores the tree exactly; one journal
record that replays to the same ids; re-kinding instead of duplicating; kinds and values checked
against the catalogue; the default region; set, delete and list; events. Synthetic cubes, no OCP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Journal, Session, replay
from stokes.api.commands.setup_plan import boundaries_id, region_tag_id
from stokes.errors import (
    ExpressionError,
    ModelError,
    ReferencedNode,
    StokesError,
    UnitMismatch,
    UnknownNode,
    ValidationError,
)
from stokes.geometry.errors import SelectionError
from stokes.geometry.model import SELECTION_PALETTE, TAGS_PATH
from stokes.model.ids import stable_id
from stokes.physics.conditions import InvalidKind, InvalidValue
from stokes.physics.conditions.catalogue import INLET_COLOR, OUTLET_COLOR
from tests.selection_support import add_part, cube, events_of, new_session, part, put_mesh

FLUID = "/regions/fluid"
BOUNDARIES = f"{FLUID}/boundaries"
WALL_COLOR = "#7f8c99"


@pytest.fixture
def cubes(tmp_path: Path) -> tuple[Session, str, str]:
    """A project with two 6-face cubes, ``a`` and ``b``, meshed."""
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    return session, a, b


def pick(part_ref: str, *faces: int) -> list[dict[str, Any]]:
    """The live pick of faces of one part, in the tag commands' input form."""
    return [{"part": part_ref, "faces": list(faces)}]


def assign(session: Session, target: Any, kind: str, **params: Any) -> dict[str, Any]:  # Any
    done: dict[str, Any] = session.execute("boundary.assign", target=target, kind=kind, **params)
    return done


def snapshot(session: Session) -> Any:  # Any: JSON
    """The whole tree as saved: ids, types, names, parameters, statuses and meta."""
    return session.require_project().to_json()["root"]


def faces(session: Session, tag: str) -> dict[str, list[int]]:
    found = session.execute("tag.members", tag=tag)["members"]
    return {m["name"]: m["faces"] for m in found}


def listed(session: Session, **params: Any) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in session.execute("boundary.list", **params)}


# ---------------------------------------------------------------- assign: one step


def test_assign_to_picked_faces_makes_tag_region_and_boundary_in_one_step(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = cubes
    before = snapshot(session)
    done = assign(session, pick(a, 0, 2), "velocity_inlet", values={"velocity": "2 [m/s]"})
    project = session.require_project()
    boundary = done["boundary"]
    assert boundary["path"] == f"{BOUNDARIES}/inlet" and boundary["kind"] == "velocity_inlet"
    assert boundary["label"] == "Velocity inlet" and boundary["family"] == "inlet"
    assert boundary["primary"] == {"name": "velocity", "value": 2.0, "display": "2 m/s"}
    assert boundary["status"] == "ok" and boundary["warnings"] == []
    assert done["tag"] == {
        "id": done["tag"]["id"],
        "path": f"{TAGS_PATH}/inlet",
        "name": "inlet",
        "color": INLET_COLOR,
        "kind": "boundary",
    }
    assert done["recoloured"] is False  # a new tag starts in the kind's colour
    assert faces(session, "inlet") == {"a": [0, 2]}
    region = project.get(FLUID)
    assert region.type == "region" and done["region"]["id"] == region.id
    assert region.params["kind"] == "fluid" and region.params["material"] is None
    region_tag = project.get(region.params["tag"])
    assert region_tag.path == f"{TAGS_PATH}/fluid" and region_tag.params["kind"] == "region"
    assert region_tag.params["members"] == "[]"
    assert region_tag.params["color"] not in (INLET_COLOR, OUTLET_COLOR)
    assert region_tag.id == region_tag_id(region.id)
    group = project.get(BOUNDARIES)
    assert group.type == "group" and group.id == boundaries_id(region.id)
    assert done["created"] == [
        stable_id("g", TAGS_PATH),
        region_tag.id,
        region.id,
        group.id,
        done["tag"]["id"],
        boundary["id"],
    ]
    state = session.execute("history.state")
    assert state["undo"] == "assign velocity inlet to inlet" and state["undo_kind"] == "composite"
    session.execute("history.undo")
    assert snapshot(session) == before  # the whole step: tags group, tags, region, boundary
    session.execute("history.redo")
    assert project.get(f"{BOUNDARIES}/inlet").id == boundary["id"]
    assert project.get(f"{TAGS_PATH}/inlet").params["color"] == INLET_COLOR


def test_each_command_is_one_journal_record_that_replays_to_the_same_tree(tmp_path: Path) -> None:
    session = new_session(tmp_path, "first")
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    session.execute("tag.create", name="inlet", members=pick("/geometry/parts/a", 0))
    assign(session, f"{TAGS_PATH}/inlet", "velocity_inlet", values={"velocity": "2 [m/s]"})
    assign(session, pick(a, 1), "pressure_outlet", name="exit")
    assign(session, pick(b, 3), "wall", region="sink", values={"thermal": "heat_flux"})
    session.execute(
        "boundary.set",
        boundary=f"{BOUNDARIES}/inlet",
        values={"velocity": "3 [m/s]"},
        members=pick(a, 0, 5),
    )
    session.execute("material.create", name="water")
    session.execute("region.assign", material="water", target=[a])
    session.execute("boundary.delete", boundary=f"{BOUNDARIES}/exit", delete_tag=True)
    session.execute("history.undo")
    records = Journal.read(tmp_path / "first.jsonl")
    commands = [r["command"] for r in records]
    assert commands[-9:] == [
        "tag.create",
        "boundary.assign",
        "boundary.assign",
        "boundary.assign",
        "boundary.set",
        "material.create",
        "region.assign",
        "boundary.delete",
        "history.undo",
    ]
    for record in (r for r in records if r["command"] == "boundary.assign"):
        assert {"node_id", "tag_node_id", "region_node_id"} <= set(record["params"])
    expected = snapshot(session)
    # Replayed with the meshes in place: the very same tree, ids, colours and versions included.
    second = new_session(tmp_path, "second")
    parts_end = max(i for i, r in enumerate(records) if r["command"] == "node.add") + 1
    replay(second, records[1:parts_end])
    for part_id, offset in ((a, 0.0), (b, 0.02)):
        put_mesh(second, part_id, part(cube(0.01, (offset, 0.0, 0.0))))
    replay(second, records[parts_end:])
    assert {**snapshot(second), "name": "first"} == expected
    # Replayed with no mesh at all (imports still running): the same ids and faces.
    third = new_session(tmp_path, "third")
    replay(third, records[1:])
    project, other = session.require_project(), third.require_project()
    assert [(n.id, n.path, n.type) for n in project.nodes()] == [
        (n.id, n.path, n.type) for n in other.nodes()
    ]
    for tag in ("inlet", "sink", "wall"):
        assert faces(third, tag) == faces(session, tag)


def test_a_second_assign_on_a_tag_changes_its_boundary_instead_of_adding_one(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = cubes
    session.execute("tag.create", name="inlet", members=pick(a, 0))
    project = session.require_project()
    tag = project.get(f"{TAGS_PATH}/inlet")
    assert tag.params["color"] == SELECTION_PALETTE[0]
    first = assign(session, "inlet", "wall")  # by the tag's name
    assert first["recoloured"] is True and tag.params["color"] == WALL_COLOR
    node = project.get(first["boundary"]["id"])
    assert node.path == f"{BOUNDARIES}/inlet"  # named after its tag
    second = assign(session, f"{TAGS_PATH}/inlet", "velocity_inlet", values={"velocity": "1 [m/s]"})
    assert second["boundary"]["id"] == node.id and second["created"] == []
    assert [e["id"] for e in session.execute("boundary.list")] == [node.id]
    assert node.params["kind"] == "velocity_inlet" and node.params["velocity"].value == 1.0
    assert tag.params["color"] == INLET_COLOR and second["recoloured"] is True
    session.execute("history.undo")  # the kind and the tag's colour come back together
    assert node.params["kind"] == "wall" and tag.params["color"] == WALL_COLOR
    third = assign(
        session, node.path, "velocity_inlet", values={"velocity": "4 [m/s]"}, color="#AABBCC"
    )
    assert third["boundary"]["primary"]["display"] == "4 m/s" and tag.params["color"] == "#aabbcc"
    renamed = assign(session, node.path, "velocity_inlet", name="intake")
    assert renamed["boundary"]["path"] == f"{BOUNDARIES}/intake" and node.name == "intake"
    with pytest.raises(ValidationError) as caught:  # a boundary does not move between regions
        assign(session, "inlet", "wall", region="elsewhere")
    assert caught.value.details["parameter"] == "region"
    session.execute(
        "node.add", parent=BOUNDARIES, type="boundary", name="twin", params={"tag": tag.id}
    )
    with pytest.raises(ValidationError, match="2 boundaries"):  # which one is meant?
        assign(session, "inlet", "wall")


def test_kinds_and_values_are_checked_and_a_refusal_changes_nothing(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = cubes
    session.execute("parameter.define", name="U_zero", dimension="velocity", value="0 [m/s]")
    session.execute("tag.create", name="inlet", members=pick(a, 0))
    before = snapshot(session)
    journaled = len(session.journal.records)
    seen = events_of(session)
    cases: list[tuple[type[StokesError], str, dict[str, Any], str]] = [
        (InvalidKind, "fan", {}, "kind"),
        (InvalidKind, "interface", {}, "kind"),  # arrives in M5
        (InvalidValue, "wall", {"velocity": "1 [m/s]"}, "velocity"),
        (InvalidValue, "wall", {"temperature": "300 [K]"}, "temperature"),  # adiabatic: unused
        (InvalidValue, "velocity_inlet", {"velocity": "0 [m/s]"}, "velocity"),
        (InvalidValue, "velocity_inlet", {"velocity": "U_zero"}, "velocity"),  # once evaluated
        (InvalidValue, "velocity_inlet", {"direction": "components", "velocity": "1"}, "velocity"),
        (InvalidValue, "velocity_inlet", {"intensity": 1.5}, "intensity"),
        (
            InvalidValue,
            "velocity_inlet",
            {"direction": "vector", "direction_vector": [0, 0, 0]},
            "direction_vector",
        ),
        (InvalidValue, "velocity_inlet", {"kind": "wall"}, "kind"),
        (InvalidValue, "velocity_inlet", {"tag": "inlet"}, "tag"),
        (InvalidValue, "mass_flow_inlet", {"mass_flow": "-1 [kg/s]"}, "mass_flow"),
        (InvalidValue, "velocity_inlet", {"porosity": 0.5}, "porosity"),
        (ExpressionError, "velocity_inlet", {"velocity": "fast"}, "velocity"),
        (UnitMismatch, "velocity_inlet", {"velocity": "2 [K]"}, "velocity"),
        (ValidationError, "velocity_inlet", {"direction": "sideways"}, "direction"),
    ]
    for error, kind, values, parameter in cases:
        with pytest.raises(error) as caught:
            assign(session, "inlet", kind, values=values)
        assert caught.value.details.get("parameter") == parameter, (kind, values)
    message = "velocity is used only with direction normal or vector"
    with pytest.raises(InvalidValue, match=message):
        assign(
            session, "inlet", "velocity_inlet", values={"direction": "components", "velocity": 1}
        )
    targets: list[tuple[type[StokesError], Any]] = [
        (ValidationError, []),
        (ValidationError, 42),
        (ValidationError, "/physics"),
        (ValidationError, "/geometry/parts/a"),
        (UnknownNode, "nope"),
        (UnknownNode, "/regions/fluid/boundaries/nope"),
        (SelectionError, [{"part": a, "faces": [6]}]),
        (SelectionError, [{"part": a, "faces": []}]),
    ]
    for error, target in targets:
        with pytest.raises(error):
            assign(session, target, "wall")
    with pytest.raises(ValidationError) as caught:
        assign(session, "inlet", "wall", color="red")
    assert caught.value.details["parameter"] == "color"
    with pytest.raises(ValidationError) as caught:
        assign(session, pick(a, 1), "wall", name="1st")
    assert caught.value.details["parameter"] == "name"
    session.execute("tag.create", name="body", kind="region", members=[{"part": a, "faces": "all"}])
    with pytest.raises(SelectionError, match="region tag"):  # a boundary takes a boundary tag
        assign(session, "body", "wall")
    session.execute("history.undo")  # the region tag again
    assert snapshot(session) == before
    assert len(session.journal.records) == journaled + 2  # the region tag's create and undo
    assert [e for e, _ in seen if e != "history.changed"] == [
        "node.added",
        "tag.changed",
    ]


def test_a_step_that_fails_while_applying_reverts_what_it_applied(
    cubes: tuple[Session, str, str],
) -> None:
    """The boundary's id is taken: its AddNode fails after the tags group, the region, its tag
    and boundaries group and the new tag have applied; the step takes them all back, and nothing
    is announced, journaled or left on the undo stack."""
    session, a, _ = cubes
    before = snapshot(session)
    journaled = len(session.journal.records)
    state = session.execute("history.state")
    seen = events_of(session)
    with pytest.raises(ModelError, match="already exists"):
        assign(session, pick(a, 0), "wall", node_id=a)
    assert snapshot(session) == before
    assert len(session.journal.records) == journaled and seen == []
    assert session.execute("history.state") == state
    done = assign(session, pick(a, 0), "wall")  # and the same command then goes through
    assert done["boundary"]["path"] == f"{BOUNDARIES}/wall"


def test_the_values_a_kind_uses_are_stored_in_si(cubes: tuple[Session, str, str]) -> None:
    session, a, _ = cubes
    done = assign(
        session,
        pick(a, 0),
        "velocity_inlet",
        values={
            "direction": "components",
            "velocity_x": "0 [m/s]",
            "velocity_y": 0,
            "velocity_z": "3 [m/s]",
            "temperature": "25 [degC]",
            "turbulence": "intensity_length",
            "length_scale": "5 [mm]",
        },
    )
    node = session.require_project().get(done["boundary"]["id"])
    assert node.params["velocity_z"].value == 3.0
    assert node.params["temperature"].value == pytest.approx(298.15)
    assert node.params["length_scale"].value == pytest.approx(0.005)
    assert done["boundary"]["warnings"] == []  # one component is not zero
    zero = assign(
        session, pick(a, 1), "velocity_inlet", values={"direction": "components"}, name="still"
    )
    assert zero["boundary"]["warnings"] == ["setup.value_invalid"]  # a finding, not a refusal
    walls = assign(
        session,
        pick(a, 2),
        "wall",
        values={"thermal": "convection", "heat_transfer_coefficient": "25 [W/(m**2*K)]"},
    )
    assert walls["boundary"]["primary"]["name"] == "heat_transfer_coefficient"


# ---------------------------------------------------------------- legacy boundaries


def test_a_plain_group_boundary_becomes_typed_in_place() -> None:
    """A plain-group boundary from before ADR-0022 becomes typed in place.

    **This used to run on `build_demo`,** which happened to provide exactly these legacy shapes (a
    plain `/regions/fluid` group, plain boundary groups, plain tag groups). The demo no longer has
    them: it now builds real typed boundaries, because a demo whose conditions are empty groups made
    every M1 feature look broken. So the legacy shape is built here explicitly, which is what this
    test
    is actually about — the upgrade path, not the demo.
    """
    session = Session()
    session.execute("project.new", name="legacy", path=None)
    # The parameter the assertion below evaluates ('2 m/s * 1.5' = '3 m/s').
    session.execute("parameter.define", name="U_in", dimension="velocity", value="2 [m/s]")
    session.execute("node.add", parent="/regions", type="group", name="fluid")
    session.execute("node.add", parent=FLUID, type="group", name="boundaries")
    for name in ("inlet", "outlet", "walls"):
        session.execute("node.add", parent=BOUNDARIES, type="group", name=name)
    session.execute("node.add", parent="/geometry", type="group", name="tags")
    for name in ("inlet", "outlet", "walls"):
        session.execute("node.add", parent=TAGS_PATH, type="group", name=name)
    project = session.require_project()
    ids = {n.path: n.id for n in project.nodes()}
    before = snapshot(session)
    done = assign(
        session, f"{BOUNDARIES}/inlet", "velocity_inlet", values={"velocity": "U_in * 1.5"}
    )
    node = project.get(f"{BOUNDARIES}/inlet")
    assert node.type == "boundary" and node.id == ids[f"{BOUNDARIES}/inlet"]
    assert done["boundary"]["id"] == node.id and done["boundary"]["primary"]["display"] == "3 m/s"
    region = project.get(FLUID)
    assert region.type == "region" and region.id == ids[FLUID]
    assert region.params["kind"] == "fluid"
    assert project.get(BOUNDARIES).id == ids[BOUNDARIES]  # kept, with the other boundaries
    assert [project.get(f"{BOUNDARIES}/{n}").type for n in ("outlet", "walls")] == ["group"] * 2
    # The demo's /geometry/tags/inlet is a plain group too: the boundary takes a new, empty tag.
    tag = project.get(node.params["tag"])
    assert tag.path == f"{TAGS_PATH}/inlet_2" and tag.params["members"] == "[]"
    assert tag.params["color"] == INLET_COLOR
    entries = listed(session)
    assert entries["inlet"]["warnings"] == ["setup.empty_tag"]
    assert entries["outlet"] == {
        "id": ids[f"{BOUNDARIES}/outlet"],
        "path": f"{BOUNDARIES}/outlet",
        "name": "outlet",
        "region": {"id": region.id, "path": FLUID, "name": "fluid"},
        "legacy": True,
    }
    session.execute("history.undo")
    assert snapshot(session) == before
    # A new boundary goes to the default region: the plain group /regions/fluid, made typed.
    session.execute("tag.create", name="gap")
    made = assign(session, "gap", "wall")
    assert made["region"]["id"] == ids[FLUID] and project.get(FLUID).type == "region"
    assert made["boundary"]["path"] == f"{BOUNDARIES}/gap" and made["recoloured"] is True
    assert project.get(f"{BOUNDARIES}/inlet").type == "group"  # the others stay as they were


def test_a_plain_group_boundary_takes_the_boundary_tag_of_its_name(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = cubes
    for parent, name in (("/regions", "fluid"), (FLUID, "boundaries"), (BOUNDARIES, "outlet")):
        session.execute("node.add", parent=parent, type="group", name=name)
    session.execute("node.add", parent=BOUNDARIES, type="group", name="walls")
    session.execute("node.add", parent=f"{BOUNDARIES}/walls", type="group", name="extra")
    session.execute("tag.create", name="outlet", members=pick(a, 1), color="#d9b43c")
    done = assign(session, f"{BOUNDARIES}/outlet", "pressure_outlet")
    assert done["tag"]["path"] == f"{TAGS_PATH}/outlet" and done["tag"]["color"] == OUTLET_COLOR
    assert done["recoloured"] is True
    assert done["created"] == [region_tag_id(done["region"]["id"])]  # only the region's tag
    project = session.require_project()
    assert project.get(f"{BOUNDARIES}/outlet").params["tag"] == done["tag"]["id"]
    assert faces(session, "outlet") == {"a": [1]}
    before = snapshot(session)
    with pytest.raises(ModelError, match="extra"):  # a boundary holds no children
        assign(session, f"{BOUNDARIES}/walls", "wall")
    assert snapshot(session) == before


# ---------------------------------------------------------------- events and regions


def test_events_name_each_node_and_each_boundary_once(cubes: tuple[Session, str, str]) -> None:
    session, a, _ = cubes
    seen = events_of(session)
    assign(session, pick(a, 0), "velocity_inlet", values={"velocity": "2 [m/s]"})
    assert [(e, d["path"], d.get("param")) for e, d in seen] == [
        ("node.added", TAGS_PATH, None),
        ("node.added", f"{TAGS_PATH}/fluid", None),
        ("node.added", FLUID, None),
        ("node.added", BOUNDARIES, None),
        ("node.added", f"{TAGS_PATH}/inlet", None),
        ("node.added", f"{BOUNDARIES}/inlet", None),
        ("node.changed", f"{BOUNDARIES}/inlet", "velocity"),
        ("tag.changed", f"{TAGS_PATH}/fluid", None),
        ("tag.changed", f"{TAGS_PATH}/inlet", None),
        ("boundary.changed", f"{BOUNDARIES}/inlet", None),
        ("region.changed", FLUID, None),
    ]
    assert all("node" in d for e, d in seen if e == "node.added")  # summaries for the tree
    seen.clear()
    assign(session, "inlet", "wall")
    assert [(e, d.get("param")) for e, d in seen] == [
        ("node.changed", "kind"),
        ("node.changed", "color"),
        ("boundary.changed", None),
    ]


def test_the_region_a_new_boundary_goes_to(cubes: tuple[Session, str, str]) -> None:
    session, a, _ = cubes
    first = assign(session, pick(a, 0), "wall", region="water_zone")
    assert first["region"]["path"] == "/regions/water_zone" and first["region"]["kind"] == "fluid"
    second = assign(session, pick(a, 1), "wall")  # the project's only fluid region
    assert second["region"]["id"] == first["region"]["id"]
    assert second["boundary"]["name"] == "wall_2" and second["tag"]["name"] == "wall_2"
    session.execute("region.create", name="air_zone")
    with pytest.raises(ValidationError, match="2 fluid regions") as caught:
        assign(session, pick(a, 2), "wall")
    assert caught.value.details["parameter"] == "region"
    third = assign(session, pick(a, 2), "wall", region="/regions/air_zone")
    assert third["region"]["name"] == "air_zone"
    with pytest.raises(UnknownNode):
        assign(session, pick(a, 3), "wall", region="/regions/nope")
    with pytest.raises(ValidationError):
        assign(session, pick(a, 3), "wall", region="/geometry")
    # A name given for picked faces: the boundary takes it; the new tag too, made unique.
    session.execute("tag.create", name="inlet")
    named = assign(session, pick(a, 3), "velocity_inlet", name="inlet", region="air_zone")
    assert named["boundary"]["path"] == "/regions/air_zone/boundaries/inlet"
    assert named["tag"]["name"] == "inlet_2"
    with pytest.raises(ValidationError) as caught:  # taken among the region's boundaries
        assign(session, pick(a, 4), "wall", name="inlet", region="air_zone")
    assert caught.value.details["parameter"] == "name"


def test_a_project_with_only_a_solid_region_gets_a_fluid_one(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    session.execute("region.create", name="fluid", kind="solid")
    done = assign(session, pick(a, 0), "symmetry")
    assert done["region"]["path"] == "/regions/fluid_2" and done["region"]["kind"] == "fluid"
    assert done["boundary"]["name"] == "symmetry" and done["boundary"]["primary"] is None


# ---------------------------------------------------------------- set, delete, list


def test_set_changes_kind_values_tag_faces_and_name_in_one_step(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, b = cubes
    done = assign(session, pick(a, 0), "wall")
    path = done["boundary"]["path"]
    project = session.require_project()
    node, tag = project.get(path), project.get(done["tag"]["id"])
    before = snapshot(session)
    changed = session.execute(
        "boundary.set",
        boundary=path,
        kind="velocity_inlet",
        values={"velocity": "2 [m/s]"},
        members=pick(a, 0, 1),
        name="intake",
    )
    assert changed["changed"] is True and changed["recoloured"] is True
    assert changed["kind"] == "velocity_inlet" and changed["path"] == f"{BOUNDARIES}/intake"
    assert faces(session, tag.id) == {"a": [0, 1]} and tag.params["color"] == INLET_COLOR
    assert session.execute("history.state")["undo"] == "edit boundary intake"
    session.execute("history.undo")
    assert snapshot(session) == before
    kept = session.execute("boundary.set", boundary=path, kind="pressure_outlet", keep_color=True)
    assert kept["recoloured"] is False and tag.params["color"] == WALL_COLOR
    other = session.execute("tag.create", name="other", members=pick(b, 0))["id"]
    moved = session.execute("boundary.set", boundary=node.id, tag="other")
    assert moved["tag"]["id"] == other and moved["recoloured"] is True
    assert project.get(other).params["color"] == OUTLET_COLOR
    with pytest.raises(InvalidValue):  # a pressure outlet uses no velocity
        session.execute("boundary.set", boundary=path, values={"velocity": "1 [m/s]"})
    session.execute("parameter.define", name="U_zero", dimension="velocity", value="0 [m/s]")
    unchanged = snapshot(session)
    with pytest.raises(InvalidValue, match="velocity must be positive") as caught:
        session.execute(  # checked once evaluated: the kind change goes back with it
            "boundary.set", boundary=path, kind="velocity_inlet", values={"velocity": "U_zero"}
        )
    assert caught.value.details == {"parameter": "velocity", "rule": "positive"}
    assert snapshot(session) == unchanged
    session.execute("region.create", name="solid_zone", kind="solid")
    with pytest.raises(SelectionError):
        session.execute("boundary.set", boundary=path, tag="solid_zone")
    with pytest.raises(UnknownNode):
        session.execute("boundary.set", boundary=path, tag="nowhere")
    state = session.execute("history.state")
    same = session.execute("boundary.set", boundary=path)
    assert same["changed"] is False and session.execute("history.state") == state
    session.execute("node.add", parent=BOUNDARIES, type="group", name="old")
    with pytest.raises(ValidationError, match=r"boundary\.assign"):
        session.execute("boundary.set", boundary=f"{BOUNDARIES}/old", kind="wall")
    with pytest.raises(ValidationError) as caught:
        session.execute("boundary.set", boundary=path, name="old")
    assert caught.value.details["parameter"] == "name"


def test_delete_clears_partners_and_deletes_the_tag_on_request(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = cubes
    left = assign(session, pick(a, 0), "cyclic", name="left")["boundary"]
    right = assign(session, pick(a, 1), "cyclic", name="right")["boundary"]
    session.execute("boundary.set", boundary=left["path"], values={"partner": right["path"]})
    session.execute("boundary.set", boundary=right["path"], values={"partner": left["id"]})
    entries = listed(session)
    assert entries["left"]["warnings"] == [] and entries["right"]["warnings"] == []
    assert entries["left"]["primary"] == {
        "name": "partner",
        "value": right["id"],
        "display": "right",
    }
    with pytest.raises(ReferencedNode, match="partner"):  # node.remove keeps references whole
        session.execute("node.remove", node=left["path"])
    project = session.require_project()
    before = snapshot(session)
    seen = events_of(session)
    deleted = session.execute("boundary.delete", boundary=left["path"], delete_tag=True)
    assert deleted == {
        "id": left["id"],
        "path": left["path"],
        "name": "left",
        "deleted": True,
        "tag_deleted": True,
    }
    assert project.get(right["id"]).params["partner"] is None
    assert project.find(f"{TAGS_PATH}/left") is None
    assert [(e, d["path"]) for e, d in seen] == [
        ("node.changed", right["path"]),
        ("node.removed", left["path"]),
        ("node.removed", f"{TAGS_PATH}/left"),
        ("tag.changed", f"{TAGS_PATH}/left"),
        ("boundary.changed", left["path"]),
        ("boundary.changed", right["path"]),
    ]
    assert listed(session)["right"]["warnings"] == ["setup.partner_missing"]
    session.execute("history.undo")  # the partner comes back with the boundary it names
    assert snapshot(session) == before
    tag = project.get(f"{BOUNDARIES}/right").params["tag"]
    session.execute(
        "node.add", parent=BOUNDARIES, type="boundary", name="twin", params={"tag": tag}
    )
    with pytest.raises(ReferencedNode, match="twin"):
        session.execute("boundary.delete", boundary=right["path"], delete_tag=True)
    kept = session.execute("boundary.delete", boundary=right["path"])
    assert kept["tag_deleted"] is False and project.get(tag).type == "tag"
    with pytest.raises(ValidationError):
        session.execute("boundary.delete", boundary=FLUID)


def test_list_reports_each_boundary_with_its_tag_value_findings_and_status(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, b = cubes
    assign(session, pick(a, 0), "velocity_inlet")  # no velocity yet
    assign(session, pick(a, 0, 1), "pressure_outlet")  # shares face 0 with the inlet
    loose = assign(session, pick(b, 2), "cyclic", region="other")  # no partner
    entries = listed(session)
    inlet = entries["inlet"]
    assert inlet["warnings"] == ["setup.overlap", "setup.value_invalid"]
    assert inlet["status"] == "warning"
    assert inlet["primary"] == {"name": "velocity", "value": 0.0, "display": "0 m/s"}
    assert inlet["tag"] == {
        "id": inlet["tag"]["id"],
        "name": "inlet",
        "path": f"{TAGS_PATH}/inlet",
        "color": INLET_COLOR,
        "visible": True,
        "parts": 1,
        "faces": 1,
        "stale": False,
        "missing": False,
    }
    assert entries["outlet"]["warnings"] == ["setup.overlap"]
    assert entries["outlet"]["primary"] == {"name": "pressure", "value": 0.0, "display": "0 Pa"}
    assert entries["cyclic"]["warnings"] == ["setup.partner_missing"]
    assert entries["cyclic"]["primary"] == {"name": "partner", "value": None, "display": "none"}
    assert [e["name"] for e in session.execute("boundary.list", region="other")] == ["cyclic"]
    with pytest.raises(UnknownNode):
        session.execute("boundary.list", region="nowhere")
    # A tag removed by hand, under the commands' guard: the boundary fails.
    session.require_project().remove(loose["tag"]["id"])
    entry = listed(session)["cyclic"]
    assert entry["status"] == "failed" and entry["tag"]["missing"] is True
    assert entry["warnings"][0] == "setup.tag_missing"
    session.require_project().get(loose["boundary"]["id"]).params["kind"] = "porous_jump"
    assert listed(session)["cyclic"]["label"] == "porous_jump"
    assert "boundary.invalid_kind" in listed(session)["cyclic"]["warnings"]
    json.dumps(session.execute("boundary.list"), allow_nan=False)


def test_node_remove_refuses_a_referenced_tag_and_its_group(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = cubes
    done = assign(session, pick(a, 0), "wall")
    for target, first in ((done["tag"]["path"], done["boundary"]), (TAGS_PATH, done["region"])):
        with pytest.raises(ReferencedNode) as caught:
            session.execute("node.remove", node=target)
        assert caught.value.code == "model.referenced"
        assert caught.value.details["referrers"][0]["path"] == first["path"]
    removed = session.execute("node.remove", node=FLUID)  # its references go with it
    assert removed["path"] == FLUID
    session.execute("node.remove", node=done["tag"]["path"])  # nothing refers to it now
