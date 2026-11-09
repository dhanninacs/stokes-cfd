"""region.* commands end to end through Session.execute (ADR-0022 decisions 2.9 and 4; contract:
docs/engineering/setup-schema.md): a region with its tag and boundaries group in one step, bodies
moved between region tags by the material they are given, the kind check, set, delete and list,
and replay with stable ids. Synthetic cubes, no OCP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Journal, Session, replay
from stokes.errors import ReferencedNode, UnknownNode, ValidationError
from stokes.geometry.errors import SelectionError
from stokes.geometry.model import TAGS_PATH
from stokes.physics.materials.errors import KindMismatch
from tests.selection_support import add_part, cube, events_of, new_session, part, put_mesh

PARTS = "/geometry/parts"


@pytest.fixture
def cubes(tmp_path: Path) -> tuple[Session, str, str]:
    """Two meshed 6-face cubes, ``a`` and ``b``, and the materials ``water`` (fluid) and ``alu``
    (solid)."""
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    session.execute("material.create", name="water")
    session.execute("material.create", name="alu", kind="solid")
    return session, a, b


def snapshot(session: Session) -> Any:  # Any: JSON
    return session.require_project().to_json()["root"]


def bodies(session: Session, region: str) -> list[str]:
    tag = session.require_project().get(session.require_project().get(region).params["tag"])
    return [m["part"] for m in json.loads(tag.params["members"])]


def regions(session: Session) -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in session.execute("region.list")}


def material_id(session: Session, name: str) -> str:
    return session.require_project().get(f"/physics/materials/{name}").id


# ---------------------------------------------------------------- create


def test_create_makes_the_region_its_tag_and_boundaries_group_in_one_step(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = cubes
    before = snapshot(session)
    seen = events_of(session)
    created = session.execute("region.create", name="pipe", bodies=[f"{PARTS}/a"], material="water")
    project = session.require_project()
    region = project.get("/regions/pipe")
    assert created["id"] == region.id and created["kind"] == "fluid"
    assert created["material"] == {
        "id": material_id(session, "water"),
        "name": "water",
        "kind": "fluid",
        "missing": False,
    }
    assert created["bodies"] == [a] and created["boundaries"] == []
    tag = project.get(region.params["tag"])
    assert tag.path == f"{TAGS_PATH}/pipe" and tag.params["kind"] == "region"
    assert json.loads(tag.params["members"]) == [{"part": a, "faces": "all"}]
    assert project.get("/regions/pipe/boundaries").type == "group"
    assert [e for e, _ in seen].count("region.changed") == 1
    assert session.execute("history.state")["undo"] == "create region pipe"
    session.execute("history.undo")
    assert snapshot(session) == before
    sink = session.execute("region.create", name="sink", material="alu")
    assert sink["kind"] == "solid"  # the material's kind
    with pytest.raises(KindMismatch) as caught:
        session.execute("region.create", name="x", kind="fluid", material="alu")
    assert caught.value.code == "materials.kind_mismatch"
    with pytest.raises(SelectionError, match="whole bodies"):
        session.execute("region.create", name="x", bodies=[{"part": a, "faces": [0]}])
    with pytest.raises(ValidationError):
        session.execute("region.create", name="sink")
    with pytest.raises(ValidationError):
        session.execute("region.create", name="x", kind="gas")
    with pytest.raises(UnknownNode):
        session.execute("region.create", name="x", material="unobtainium")


def test_create_takes_bodies_out_of_other_regions_and_types_a_plain_group(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, b = cubes
    session.execute("region.create", name="domain", bodies=[a, b])
    session.execute("node.add", parent="/regions", type="group", name="old")
    session.execute("node.add", parent="/regions/old", type="group", name="boundaries")
    old = session.require_project().get("/regions/old").id
    made = session.execute("region.create", name="old", bodies=[{"part": b, "faces": "all"}])
    assert made["id"] == old and made["bodies"] == [b]
    assert session.require_project().get("/regions/old").type == "region"
    assert bodies(session, "/regions/domain") == [a]


# ---------------------------------------------------------------- assign


def test_assign_moves_bodies_to_the_region_of_their_material(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, b = cubes
    session.execute("region.create", name="domain", bodies=[a, b])  # no material yet
    before = snapshot(session)
    seen = events_of(session)
    sink = session.execute("region.assign", material="alu", target=[b])
    assert sink["region"]["path"] == "/regions/alu" and sink["region"]["kind"] == "solid"
    assert sink["region"]["bodies"] == [b] and sink["moved"] == [b] and sink["bodies"] == [b]
    assert bodies(session, "/regions/domain") == [a]
    changed = [d["path"] for e, d in seen if e == "region.changed"]
    assert sorted(changed) == ["/regions/alu", "/regions/domain"]
    assert session.execute("history.state")["undo"] == "assign alu to 1 body"
    after = snapshot(session)
    session.execute("history.undo")
    assert snapshot(session) == before
    session.execute("history.redo")  # the same ids again: the region, its tag and its group
    assert snapshot(session) == after
    # The only fluid region without a material takes a fluid material; its bodies stay.
    filled = session.execute("region.assign", material="water", target=[f"{PARTS}/a"])
    assert filled["region"]["name"] == "domain" and filled["moved"] == []
    assert filled["region"]["material"]["name"] == "water"
    # A body moves from a region of another material; the region it leaves stays, empty.
    back = session.execute("region.assign", material="water", target=[{"part": b, "faces": "all"}])
    assert back["region"]["name"] == "domain" and back["moved"] == [b]
    assert bodies(session, "/regions/domain") == [a, b] and bodies(session, "/regions/alu") == []
    listed = regions(session)
    assert listed["alu"]["warnings"] == ["setup.empty_region"]
    # Bodies already in a region of the material stay where they are.
    session.execute("region.create", name="tank", material="water")
    again = session.execute("region.assign", material="water", target=[a])
    assert again["moved"] == [] and bodies(session, "/regions/tank") == []
    assert session.execute("region.assign", material="water", target="tank")["moved"] == []
    session.execute("tag.create", name="spare", kind="region")  # a region tag no region uses
    with pytest.raises(ValidationError, match="no bodies"):
        session.execute("region.assign", material="water", target="spare")
    with pytest.raises(SelectionError):
        session.execute("region.assign", material="water", target=[{"part": a, "faces": [2]}])


def test_assign_to_a_region_its_tag_or_a_group(cubes: tuple[Session, str, str]) -> None:
    session, a, b = cubes
    session.execute("region.create", name="domain")
    done = session.execute("region.assign", material="water", target="domain")
    assert done["region"]["material"]["name"] == "water" and done["moved"] == []
    project = session.require_project()
    assert project.get("/regions/domain").params["material"] == material_id(session, "water")
    state = session.execute("history.state")
    session.execute("region.assign", material="water", target=f"{TAGS_PATH}/domain")
    assert session.execute("history.state") == state  # nothing to change: no undo step
    before = snapshot(session)
    with pytest.raises(KindMismatch):
        session.execute("region.assign", material="alu", target="/regions/domain")
    assert snapshot(session) == before
    session.execute("tag.create", name="faces", members=[{"part": a, "faces": [0]}])
    with pytest.raises(SelectionError, match="boundary tag"):
        session.execute("region.assign", material="water", target="faces")
    grouped = session.execute("region.assign", material="water", target=PARTS)
    assert grouped["bodies"] == [a, b] and bodies(session, "/regions/domain") == [a, b]
    named = session.execute("region.assign", material="alu", target=[a], name="sink")
    assert named["region"]["path"] == "/regions/sink" and named["region"]["kind"] == "solid"
    session.execute("material.create", name="oil")
    with pytest.raises(ValidationError, match="holds water"):  # a named region keeps its material
        session.execute("region.assign", material="oil", target=[b], name="domain")
    with pytest.raises(KindMismatch):
        session.execute("region.assign", material="alu", target=[b], name="domain")
    with pytest.raises(UnknownNode):
        session.execute("region.assign", material="water", target="nowhere")


def test_conditions_first_then_a_material_fills_the_region_they_made(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, b = cubes
    session.execute("boundary.assign", target=[{"part": a, "faces": [0]}], kind="velocity_inlet")
    done = session.execute("region.assign", material="water", target=[a, b])
    assert done["region"]["path"] == "/regions/fluid" and done["moved"] == [a, b]
    assert list(regions(session)) == ["fluid"]
    entry = regions(session)["fluid"]
    assert entry["material"]["name"] == "water" and entry["uncovered_faces"] == 11


# ---------------------------------------------------------------- set, delete, list


def test_set_changes_material_kind_bodies_and_name(cubes: tuple[Session, str, str]) -> None:
    session, a, b = cubes
    session.execute("region.create", name="domain", bodies=[a], material="water")
    session.execute("region.create", name="other", bodies=[b])
    before = snapshot(session)
    done = session.execute("region.set", region="domain", members=[a, b], name="tank")
    assert done["changed"] is True and done["path"] == "/regions/tank"
    assert done["bodies"] == [a, b] and bodies(session, "/regions/other") == []
    session.execute("history.undo")
    assert snapshot(session) == before
    with pytest.raises(KindMismatch):  # water stays: a solid region cannot hold it
        session.execute("region.set", region="domain", kind="solid")
    solid = session.execute("region.set", region="domain", kind="solid", material="alu")
    assert solid["kind"] == "solid" and solid["material"]["name"] == "alu"
    same = session.execute("region.set", region="domain")
    assert same["changed"] is False
    cleared = session.execute("parameter.set", node="/regions/domain", name="material", value=None)
    assert cleared["params"]["material"] is None
    session.execute("node.add", parent="/regions", type="group", name="old")
    with pytest.raises(ValidationError, match=r"region\.create"):
        session.execute("region.set", region="old", kind="fluid")
    with pytest.raises(ValidationError):
        session.execute("region.set", region="domain", name="other")


def test_delete_removes_the_region_with_its_boundaries(cubes: tuple[Session, str, str]) -> None:
    session, a, b = cubes
    session.execute("region.create", name="left", bodies=[a])
    session.execute("region.create", name="right", bodies=[b])
    one = session.execute(
        "boundary.assign", target=[{"part": a, "faces": [0]}], kind="cyclic", region="left"
    )["boundary"]
    two = session.execute(
        "boundary.assign", target=[{"part": b, "faces": [0]}], kind="cyclic", region="right"
    )["boundary"]
    session.execute("boundary.set", boundary=two["path"], values={"partner": one["id"]})
    project = session.require_project()
    tag = project.get("/regions/left").params["tag"]
    before = snapshot(session)
    seen = events_of(session)
    deleted = session.execute("region.delete", region="left", delete_tag=True)
    assert deleted["boundaries"] == [one["id"]] and deleted["tag_deleted"] is True
    assert project.find("/regions/left") is None and project.find(tag) is None
    assert project.get(two["id"]).params["partner"] is None
    assert [d["path"] for e, d in seen if e == "boundary.changed"] == [one["path"], two["path"]]
    session.execute("history.undo")
    assert snapshot(session) == before
    with pytest.raises(ReferencedNode):  # the partner names a boundary inside the region
        session.execute("node.remove", node="/regions/left")
    session.execute("node.add", parent="/regions", type="region", name="twin", params={"tag": tag})
    with pytest.raises(ReferencedNode, match="twin"):
        session.execute("region.delete", region="left", delete_tag=True)
    with pytest.raises(UnknownNode):
        session.execute("region.delete", region="nowhere")


def test_list_reports_material_bodies_boundaries_and_uncovered_faces(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, b = cubes
    session.execute("region.create", name="domain", bodies=[a, b], material="water")
    inlet = session.execute(
        "boundary.assign", target=[{"part": a, "faces": [0]}], kind="velocity_inlet"
    )["boundary"]
    session.execute("boundary.assign", target=[{"part": a, "faces": [0, 1]}], kind="wall")
    session.execute(
        "boundary.assign", target=[{"part": b, "faces": "all"}], kind="wall"
    )  # a whole body
    session.execute("node.add", parent="/regions", type="group", name="old")
    listed = session.execute("region.list")
    entry = listed[0]
    project = session.require_project()
    assert entry["id"] == project.get("/regions/domain").id and entry["kind"] == "fluid"
    assert entry["bodies"] == [a, b] and inlet["id"] in entry["boundaries"]
    assert entry["uncovered_faces"] == 4  # a's faces 2 to 5
    assert entry["tag"]["name"] == "domain" and entry["stale"] is False
    assert entry["warnings"] == [] and entry["status"] == "ok"
    assert listed[1] == {
        "id": project.get("/regions/old").id,
        "path": "/regions/old",
        "name": "old",
        "legacy": True,
    }
    project.remove("/physics/materials/water")  # by hand: the material reference dangles
    entry = session.execute("region.list")[0]
    assert entry["material"]["missing"] is True and entry["warnings"] == ["setup.no_material"]
    project.get("/regions/domain").params["kind"] = "solid"
    session.execute("region.set", region="domain", material="alu")
    project.get("/regions/domain").params["kind"] = "fluid"  # a project edited by hand
    assert "setup.material_kind" in session.execute("region.list")[0]["warnings"]


def test_region_commands_replay_to_the_same_tree(tmp_path: Path) -> None:
    session = new_session(tmp_path, "first")
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    session.execute("material.create", name="water")
    session.execute("material.create", name="alu", kind="solid")
    session.execute("region.create", name="domain", bodies=[a, b])
    session.execute("region.assign", material="alu", target=[b])
    session.execute("region.assign", material="water", target=[a])
    session.execute("region.set", region="alu", name="sink")
    session.execute("boundary.assign", target=[{"part": a, "faces": [1]}], kind="wall")
    session.execute("region.delete", region="sink", delete_tag=True)
    session.execute("history.undo")
    records = Journal.read(tmp_path / "first.jsonl")
    for record in (r for r in records if r["command"] in ("region.create", "region.assign")):
        assert {"node_id", "tag_node_id"} <= set(record["params"])
    expected = snapshot(session)
    second = new_session(tmp_path, "second")
    parts_end = max(i for i, r in enumerate(records) if r["command"] == "node.add") + 1
    replay(second, records[1:parts_end])
    for part_id, offset in ((a, 0.0), (b, 0.02)):
        put_mesh(second, part_id, part(cube(0.01, (offset, 0.0, 0.0))))
    replay(second, records[parts_end:])
    assert {**snapshot(second), "name": "first"} == expected
