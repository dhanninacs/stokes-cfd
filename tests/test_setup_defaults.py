"""``setup.defaults`` end to end (ADR-0022 decision 10.1; contract:
docs/engineering/setup-schema.md): the tag names an enclosure or an extraction gives, turned into
a region with a material and typed boundaries in ONE undo step, by tag name and by picked caps;
the walls the faces no tag holds become; the second run that skips what is already there; one undo
that takes the whole set-up back; a journal that replays to the same ids. Synthetic cubes, no OCP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Journal, Session, replay
from stokes.errors import UnknownNode, ValidationError
from stokes.geometry.errors import SelectionError
from stokes.geometry.model import TAGS_PATH
from stokes.physics.conditions.templates import EXTERNAL, INTERNAL
from stokes.physics.materials.errors import KindMismatch
from tests.selection_support import add_part, cube, events_of, new_session, part, put_mesh

PARTS = "/geometry/parts"
FLUID = "/regions/fluid"
BOUNDARIES = f"{FLUID}/boundaries"
AIR = "/physics/materials/air"
DOC = Path(__file__).resolve().parents[1] / "docs" / "engineering" / "setup-schema.md"


# ---------------------------------------------------------------- helpers


def pick(part_ref: str, *faces: int) -> list[dict[str, Any]]:  # Any: JSON members
    """The live pick of faces of one part, in the tag commands' input form."""
    return [{"part": part_ref, "faces": list(faces)}]


def snapshot(session: Session) -> Any:  # Any: JSON
    """The whole tree as saved: ids, types, names, parameters, statuses and meta."""
    return session.require_project().to_json()["root"]


def tag(session: Session, name: str, *faces: int, part_ref: str = f"{PARTS}/a") -> str:
    """A boundary-kind tag holding ``faces`` of one part."""
    made = session.execute("tag.create", name=name, members=pick(part_ref, *faces))
    return str(made["id"])


def faces(session: Session, name: str) -> dict[str, list[int]]:
    found = session.execute("tag.members", tag=name)["members"]
    return {m["name"]: m["faces"] for m in found}


def boundaries(session: Session) -> dict[str, dict[str, Any]]:  # Any: JSON
    """``boundary.list`` by name."""
    return {e["name"]: e for e in session.execute("boundary.list")}


def names(done: dict[str, Any]) -> list[tuple[str, str]]:  # Any: the result's JSON
    """The planned boundaries as (name, kind), in the order the template lists them."""
    return [(row["name"], row["kind"]) for row in done["boundaries"]]


@pytest.fixture
def box(tmp_path: Path) -> tuple[Session, str]:
    """A project with one meshed six-face cube ``a`` (faces 0 x-min, 1 x-max, 2..5 the sides)."""
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    return session, a


def enclosure(session: Session, a: str) -> None:
    """The three tags an enclosure output carries for this box."""
    tag(session, "inlet", 0, part_ref=a)
    tag(session, "outlet", 1, part_ref=a)
    tag(session, "far_field", 2, 3, 4, 5, part_ref=a)


# ---------------------------------------------------------------- the tables


def test_the_two_tables_are_the_contract() -> None:
    """ADR-0022 decision 10.1, as ``docs/engineering/setup-schema.md`` repeats it."""
    assert EXTERNAL == {
        "inlet": ("velocity_inlet", {}),
        "outlet": ("pressure_outlet", {}),
        "symmetry": ("symmetry", {}),
        "far_field": ("wall", {"shear": "slip"}),
        "ground": ("wall", {}),
        "walls": ("wall", {}),
    }
    assert INTERNAL == {
        "inlet": ("velocity_inlet", {}),
        "outlet": ("pressure_outlet", {}),
        "walls": ("wall", {}),
    }


def test_the_contract_lists_the_command_as_built() -> None:
    text = DOC.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| `setup.defaults`")]
    assert len(rows) == 1 and "built" in rows[0]
    assert "### `setup.defaults` (T-039, built)" in text


# ---------------------------------------------------------------- external


def test_external_turns_the_enclosure_tags_into_conditions(box: tuple[Session, str]) -> None:
    session, a = box
    enclosure(session, a)
    project = session.require_project()
    done = session.execute("setup.defaults", target=a, template="external")
    region = project.get(FLUID)
    air = project.get(AIR)
    assert done["region"] == {
        "id": region.id,
        "path": FLUID,
        "name": "fluid",
        "kind": "fluid",
    }
    assert done["material"] == {"id": air.id, "path": AIR, "name": "air", "kind": "fluid"}
    assert done["material_created"] is True
    assert names(done) == [
        ("inlet", "velocity_inlet"),
        ("outlet", "pressure_outlet"),
        ("far_field", "wall"),
    ]
    assert done["walls_faces"] == 0 and done["skipped"] == [] and done["warnings"] == []
    assert project.find(f"{TAGS_PATH}/walls") is None  # nothing was uncovered
    region_tag = project.get(region.params["tag"])
    assert json.loads(region_tag.params["members"]) == [{"part": a, "faces": "all"}]
    assert region.params["material"] == air.id and air.children  # the properties came with it
    assert project.get(f"{BOUNDARIES}/far_field").params["shear"] == "slip"
    assert done["boundaries"][2]["tag"] == project.get(f"{TAGS_PATH}/far_field").id
    # The "done means" of the brief: ready apart from the inlet velocity.
    checked = session.execute("setup.check")
    assert checked["state"] == "incomplete"
    assert [f["code"] for f in checked["findings"] if f["severity"] == "error"] == [
        "setup.value_invalid"
    ]


def test_symmetry_and_ground_become_their_kinds(box: tuple[Session, str]) -> None:
    session, a = box
    tag(session, "inlet", 0, part_ref=a)
    tag(session, "symmetry", 1, part_ref=a)
    tag(session, "ground", 2, part_ref=a)
    done = session.execute("setup.defaults", target=a, template="external")
    assert names(done) == [
        ("inlet", "velocity_inlet"),
        ("symmetry", "symmetry"),
        ("ground", "wall"),
        ("walls", "wall"),  # the faces 3 to 5 no tag holds
    ]
    project = session.require_project()
    assert project.get(f"{BOUNDARIES}/symmetry").params["kind"] == "symmetry"
    assert project.get(f"{BOUNDARIES}/ground").params["shear"] == "no_slip"


def test_the_faces_no_tag_holds_become_walls(box: tuple[Session, str]) -> None:
    session, a = box
    tag(session, "inlet", 0, part_ref=a)
    done = session.execute("setup.defaults", target=a, template="external")
    assert names(done) == [("inlet", "velocity_inlet"), ("walls", "wall")]
    assert done["walls_faces"] == 5
    assert faces(session, "walls") == {"a": [1, 2, 3, 4, 5]}
    assert session.require_project().get(f"{BOUNDARIES}/walls").params["shear"] == "no_slip"


def test_a_given_inlet_sets_an_existing_tag_and_makes_a_missing_one(
    box: tuple[Session, str],
) -> None:
    session, a = box
    tag(session, "inlet", 1, 2, part_ref=a)  # the tag is there, with the wrong faces
    done = session.execute(
        "setup.defaults", target=a, template="external", inlet=pick(a, 0), outlet=pick(a, 1)
    )
    assert faces(session, "inlet") == {"a": [0]}  # set, not extended
    assert faces(session, "outlet") == {"a": [1]}  # made
    assert names(done) == [
        ("inlet", "velocity_inlet"),
        ("outlet", "pressure_outlet"),
        ("walls", "wall"),
    ]
    assert faces(session, "walls") == {"a": [2, 3, 4, 5]} and done["walls_faces"] == 4


# ---------------------------------------------------------------- internal


def test_internal_takes_the_picked_caps_and_walls_the_rest(box: tuple[Session, str]) -> None:
    """The brief's case: a caps tag over faces 0 to 2, the inlet face 0 and the outlet face 1, so
    face 2 becomes a wall and one warning says so."""
    session, a = box
    tag(session, "caps", 0, 1, 2, part_ref=a)
    done = session.execute(
        "setup.defaults",
        target=a,
        template="internal",
        inlet=pick(a, 0),
        outlet=pick(a, 1),
        velocity="2 [m/s]",
    )
    assert names(done) == [
        ("inlet", "velocity_inlet"),
        ("outlet", "pressure_outlet"),
        ("walls", "wall"),
    ]
    assert done["material"]["name"] == "water" and done["material_created"] is True
    assert faces(session, "inlet") == {"a": [0]}
    assert faces(session, "outlet") == {"a": [1]}
    assert faces(session, "walls") == {"a": [2, 3, 4, 5]} and done["walls_faces"] == 4
    assert done["warnings"] == [
        "1 cap face of the extraction is in neither the inlet nor the outlet: it became a wall"
    ]
    # The openings the extraction named leave its tag: a face in two boundary tags is refused
    # when the case is compiled, so the named caps and the leftover one alike leave it.
    assert faces(session, "caps") == {}
    checked = session.execute("setup.check")
    assert checked["state"] == "ready" and checked["next"] == "Set-up complete"
    assert "setup.overlap" not in [f["code"] for f in checked["findings"]]


def test_internal_leaves_a_caps_tag_a_boundary_already_uses(box: tuple[Session, str]) -> None:
    """A caps tag the user already gave a boundary is not an opening to name: the helper leaves
    it alone, and the faces it holds stay covered."""
    session, a = box
    caps = tag(session, "caps", 0, 1, 2, part_ref=a)
    session.execute("boundary.assign", target=caps, kind="wall", region="fluid")
    before = snapshot(session)
    done = session.execute("setup.defaults", target=a, template="internal", inlet=pick(a, 0))
    assert faces(session, "caps") == {"a": [0, 1, 2]}  # left alone
    assert names(done) == [("inlet", "velocity_inlet"), ("walls", "wall")]
    assert faces(session, "walls") == {"a": [3, 4, 5]}
    session.execute("history.undo")
    assert snapshot(session) == before


# ---------------------------------------------------------------- a second run


def test_a_second_run_skips_the_boundaries_it_finds(box: tuple[Session, str]) -> None:
    session, a = box
    enclosure(session, a)
    first = session.execute("setup.defaults", target=a, template="external", velocity="2 [m/s]")
    before = snapshot(session)
    state = session.execute("history.state")
    again = session.execute("setup.defaults", target=a, template="external", velocity="4 [m/s]")
    assert again["skipped"] == ["inlet", "outlet", "far_field"]
    assert again["boundaries"] == [] and again["walls_faces"] == 0
    assert again["region"]["id"] == first["region"]["id"] and again["material_created"] is False
    assert snapshot(session) == before and session.execute("history.state") == state  # no step
    assert "velocity" in again["warnings"][0]  # it reached no boundary, and says so
    assert session.require_project().get(f"{BOUNDARIES}/inlet").params["velocity"].value == 2.0


# ---------------------------------------------------------------- velocity


def test_velocity_lands_on_the_inlet_and_leaves_the_set_up_ready(box: tuple[Session, str]) -> None:
    session, a = box
    enclosure(session, a)
    done = session.execute("setup.defaults", target=a, template="external", velocity="2 [m/s]")
    assert done["warnings"] == []
    node = session.require_project().get(f"{BOUNDARIES}/inlet")
    assert node.params["velocity"].value == pytest.approx(2.0)
    assert boundaries(session)["inlet"]["primary"] == {
        "name": "velocity",
        "value": 2.0,
        "display": "2 m/s",
    }
    checked = session.execute("setup.check")
    assert checked["state"] == "ready" and checked["next"] == "Set-up complete"


def test_a_velocity_the_target_has_no_inlet_for_is_a_warning(box: tuple[Session, str]) -> None:
    session, a = box
    tag(session, "outlet", 1, part_ref=a)
    done = session.execute("setup.defaults", target=a, template="external", velocity="2 [m/s]")
    assert names(done) == [("outlet", "pressure_outlet"), ("walls", "wall")]
    assert len(done["warnings"]) == 1 and done["warnings"][0].startswith("velocity was given")


# ---------------------------------------------------------------- undo and replay


def test_one_undo_takes_the_whole_set_up_back(box: tuple[Session, str]) -> None:
    session, a = box
    enclosure(session, a)
    before = snapshot(session)
    session.execute("setup.defaults", target=a, template="external", velocity="2 [m/s]")
    assert session.execute("history.state")["undo"] == "add default conditions (external)"
    session.execute("history.undo")
    assert snapshot(session) == before  # the region, the tags, the boundaries and air, all of it
    session.execute("history.redo")
    project = session.require_project()
    assert project.get(FLUID).type == "region" and project.get(AIR).children
    assert session.execute("setup.check")["state"] == "ready"


def test_the_journal_replays_to_the_same_ids_in_a_fresh_session(tmp_path: Path) -> None:
    session = new_session(tmp_path, "first")
    a = add_part(session, "a", part(cube(0.01, name="a")))
    tag(session, "inlet", 0, part_ref=a)
    tag(session, "far_field", 2, 3, 4, 5, part_ref=a)
    session.execute("setup.defaults", target=a, template="external", velocity="2 [m/s]")
    records = Journal.read(tmp_path / "first.jsonl")
    record = records[-1]
    assert record["command"] == "setup.defaults" and record["params"]["target"] == a
    made = record["params"]["node_ids"]
    project = session.require_project()
    assert made == {
        "region": project.get(FLUID).id,
        "material": project.get(AIR).id,
        "tags": {"walls": project.get(f"{TAGS_PATH}/walls").id},
        "boundaries": {
            name: project.get(f"{BOUNDARIES}/{name}").id for name in ("inlet", "far_field", "walls")
        },
    }
    expected = snapshot(session)
    # Replayed with the meshes in place: the very same tree, ids and versions included.
    second = new_session(tmp_path, "second")
    parts_end = max(i for i, r in enumerate(records) if r["command"] == "node.add") + 1
    replay(second, records[1:parts_end])
    put_mesh(second, a, part(cube(0.01, name="a")))
    replay(second, records[parts_end:])
    assert {**snapshot(second), "name": "first"} == expected
    # Replayed with no mesh at all (imports still running): the same ids and faces.
    third = new_session(tmp_path, "third")
    replay(third, records[1:])
    assert [(n.id, n.path, n.type) for n in project.nodes()] == [
        (n.id, n.path, n.type) for n in third.require_project().nodes()
    ]
    for name in ("inlet", "far_field", "walls"):
        assert faces(third, name) == faces(session, name)


# ---------------------------------------------------------------- the material


def test_a_solid_material_is_refused(box: tuple[Session, str]) -> None:
    session, a = box
    enclosure(session, a)
    session.execute("material.create", name="alu", kind="solid")
    before = snapshot(session)
    state = session.execute("history.state")
    with pytest.raises(KindMismatch) as caught:
        session.execute("setup.defaults", target=a, template="external", material="alu")
    assert caught.value.code == "materials.kind_mismatch"
    assert caught.value.details["parameter"] == "material"
    assert snapshot(session) == before and session.execute("history.state") == state


def test_the_projects_only_fluid_material_is_used(box: tuple[Session, str]) -> None:
    session, a = box
    session.execute("material.create", name="water")
    tag(session, "inlet", 0, part_ref=a)
    done = session.execute("setup.defaults", target=a, template="external")
    assert done["material"]["name"] == "water" and done["material_created"] is False
    assert session.require_project().find(AIR) is None


# ---------------------------------------------------------------- the region


def test_a_part_already_in_a_region_keeps_that_region(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    session.execute("region.create", name="sink", bodies=[a])
    tag(session, "inlet", 0, part_ref=a)
    done = session.execute("setup.defaults", target=a, template="external")
    project = session.require_project()
    assert done["region"]["path"] == "/regions/sink" and project.find(FLUID) is None
    assert project.get("/regions/sink").params["material"] == project.get(AIR).id
    assert [
        m["part"]
        for m in json.loads(
            project.get(project.get("/regions/sink").params["tag"]).params["members"]
        )
    ] == [a]
    assert b not in {
        m["part"]
        for m in json.loads(
            project.get(project.get("/regions/sink").params["tag"]).params["members"]
        )
    }


def test_a_region_target_is_taken_as_it_is(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    session.execute("region.create", name="duct", bodies=[a])
    tag(session, "inlet", 0, part_ref=a)
    done = session.execute("setup.defaults", target="/regions/duct", template="internal")
    assert done["region"]["path"] == "/regions/duct" and names(done)[0] == (
        "inlet",
        "velocity_inlet",
    )
    assert done["walls_faces"] == 5


# ---------------------------------------------------------------- errors and shape


def test_the_errors_name_the_parameter(box: tuple[Session, str]) -> None:
    session, a = box
    tag(session, "inlet", 0, part_ref=a)
    with pytest.raises(ValidationError) as caught:
        session.execute("setup.defaults", target=a, template="sideways")
    assert caught.value.details["parameter"] == "template"
    with pytest.raises(UnknownNode) as caught:
        session.execute("setup.defaults", target="/geometry/parts/nope", template="external")
    assert caught.value.details["parameter"] == "target"
    with pytest.raises(ValidationError) as caught:  # a tag is neither a part nor a region
        session.execute("setup.defaults", target=f"{TAGS_PATH}/inlet", template="external")
    assert caught.value.details["parameter"] == "target"
    session.execute("region.create", name="empty")
    with pytest.raises(ValidationError) as caught:  # a region with no body
        session.execute("setup.defaults", target="/regions/empty", template="external")
    assert caught.value.details["parameter"] == "target"
    other = add_part(session, "b", part(cube(0.01, (0.05, 0.0, 0.0), name="b")))
    before = snapshot(session)
    with pytest.raises(ValidationError) as caught:  # a cap of another body is not this opening
        session.execute("setup.defaults", target=a, template="internal", inlet=pick(other, 0))
    assert caught.value.details["parameter"] == "inlet"
    with pytest.raises(SelectionError) as caught:  # a face the part does not have
        session.execute("setup.defaults", target=a, template="internal", inlet=pick(a, 9))
    assert caught.value.details["parameter"] == "inlet"
    assert snapshot(session) == before  # every refusal changed nothing


def test_the_result_is_json_with_the_keys_of_the_contract(box: tuple[Session, str]) -> None:
    session, a = box
    enclosure(session, a)
    done = session.execute("setup.defaults", target=a, template="external")
    assert list(done) == [
        "region",
        "material",
        "material_created",
        "boundaries",
        "walls_faces",
        "skipped",
        "warnings",
    ]
    assert all(list(row) == ["name", "kind", "tag"] for row in done["boundaries"])
    assert all(isinstance(warning, str) and warning for warning in done["warnings"])
    json.dumps(done, allow_nan=False)  # nothing in it needs a custom encoder


def test_the_step_announces_each_node_and_each_boundary(box: tuple[Session, str]) -> None:
    session, a = box
    tag(session, "inlet", 0, part_ref=a)
    seen = events_of(session)
    session.execute("setup.defaults", target=a, template="external")
    events = [(event, data.get("path")) for event, data in seen]
    assert [path for event, path in events if event == "boundary.changed"] == [
        f"{BOUNDARIES}/inlet",
        f"{BOUNDARIES}/walls",
    ]
    assert ("region.changed", FLUID) in events
    assert ("node.added", AIR) in events and ("tag.changed", f"{TAGS_PATH}/walls") in events
    assert all("node" in data for event, data in seen if event == "node.added")


# ---------------------------------------------------------------- the CLI


def project_with_an_enclosure(tmp_path: Path) -> str:
    """A saved project holding one meshed cube with the tags an enclosure output carries."""
    from stokes.geometry.cache import write_cache
    from stokes.geometry.transport import pack_part

    session = new_session(tmp_path, "demo")
    mesh = part(cube(0.01, name="a"))
    a = add_part(session, "a", mesh)
    enclosure(session, a)
    folder = session.project_path
    assert folder is not None
    write_cache(folder, a, pack_part(mesh))
    session.execute("project.save")
    return str(folder)


def test_the_cli_sets_up_a_prepared_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``stokes setup defaults <project> <target> <template> --velocity ...``, and the check it
    leaves behind; a bad template is the usual refusal, not a crash."""
    from stokes.cli.main import run

    project = project_with_an_enclosure(tmp_path)
    code = run(["setup", "defaults", project, f"{PARTS}/a", "external", "--velocity", "2 [m/s]"])
    done = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and names(done) == [
        ("inlet", "velocity_inlet"),
        ("outlet", "pressure_outlet"),
        ("far_field", "wall"),
    ]
    assert done["material"]["name"] == "air" and done["material_created"] is True
    code = run(["setup", "check", project])
    checked = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and checked["state"] == "ready"
    code = run(["setup", "defaults", project, f"{PARTS}/a", "sideways"])
    refused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1 and refused["error"]["code"] == "validation"
    assert refused["error"]["details"]["parameter"] == "template"
