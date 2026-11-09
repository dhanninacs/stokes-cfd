"""tag.* commands end to end through Session.execute (ADR-0020 slice 1): create and the
group, add, remove, set, invert, grow, region, rename, delete with undo, redo and events;
journal replay; the demo's plain groups; stale members and tag.rematch; the fingerprint
archive; by colour and by name; anchor and related; errors. Synthetic cubes, no OCP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.api import Journal, Session, default_registry, replay
from stokes.api.commands.selection import _point_on_faces
from stokes.errors import ModelError, ReferencedNode, StokesError, UnknownNode, ValidationError
from stokes.geometry.checkstore import CheckStore
from stokes.geometry.errors import MeshUnavailable, SelectionError, SelectionStale
from stokes.geometry.files import SourceStamp
from stokes.geometry.model import TAGS_PATH
from stokes.geometry.selection_cache import (
    SelectionCache,
    archive_file,
    read_archive,
    referenced,
)
from stokes.geometry.store import GeometryStore
from stokes.model.ids import stable_id
from tests.selection_support import (
    STAMP,
    add_part,
    cube,
    events_of,
    new_session,
    part,
    permuted,
    put_mesh,
)

SEL = f"{TAGS_PATH}/inlet"


@pytest.fixture
def two_cubes(tmp_path: Path) -> tuple[Session, str, str]:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    return session, a, b


def faces(session: Session, ref: str = SEL) -> dict[str, list[int]]:
    found = session.execute("tag.members", tag=ref)["members"]
    return {m["name"]: m["faces"] for m in found}


def box_mesh(dx: float, dy: float, dz: float, at: tuple[float, float, float] = (0.0, 0.0, 0.0)):
    """An axis-aligned box, six faces, two triangles each, wound outwards.

    Face ids as selection_support.cube: 0 x-min, 1 x-max, 2 y-min, 3 y-max, 4 z-min, 5 z-max."""
    from tests.selection_support import mesh_from_corners

    corners = []
    for axis in range(3):
        for side in (0.0, 1.0):
            u, v = (axis + 1) % 3, (axis + 2) % 3
            quad = []
            for a, b in ((0, 0), (1, 0), (1, 1), (0, 1)):
                point = np.zeros(3)
                point[axis], point[u], point[v] = side, a, b
                quad.append(point)
            normal = np.zeros(3)
            normal[axis] = 1.0 if side else -1.0
            for tri in ((0, 1, 2), (0, 2, 3)):
                p = [quad[i] for i in tri]
                if np.dot(np.cross(p[1] - p[0], p[2] - p[0]), normal) < 0.0:
                    p[1], p[2] = p[2], p[1]
                corners.append(np.array(p))
    scale = np.array([dx, dy, dz])
    return mesh_from_corners(
        np.array(corners) * scale + np.array(at), np.repeat(np.arange(6), 2), name="box"
    )


def test_create_makes_the_group_in_the_same_undo_step(two_cubes: tuple[Session, str, str]) -> None:
    session, a, _ = two_cubes
    seen = events_of(session)
    created = session.execute(
        "tag.create", name="inlet", members=[{"part": "/geometry/parts/a", "faces": [0]}]
    )
    assert created["path"] == SEL and created["kind"] == "boundary"
    assert created["color"] == "#3b82c4" and created["faces"] == 1 and created["stale"] == []
    assert created["members"] == [
        {"part": a, "faces": [[0, 0]], "basis": created["members"][0]["basis"]}
    ]
    group = session.execute("node.get", node=TAGS_PATH)
    assert group["id"] == stable_id("g", TAGS_PATH) and group["type"] == "group"
    names = [(e, d["path"]) for e, d in seen if e in ("node.added", "tag.changed")]
    assert names == [
        ("node.added", TAGS_PATH),
        ("node.added", SEL),
        ("tag.changed", SEL),
    ]
    session.execute("history.undo")
    assert session.require_project().find(TAGS_PATH) is None
    session.execute("history.redo")
    assert faces(session) == {"a": [0]}
    second = session.execute("tag.create", name="outlet", kind="plain", color="#AABBCC")
    assert second["color"] == "#aabbcc" and second["kind"] == "plain" and second["members"] == []


def test_add_remove_set_and_undo(two_cubes: tuple[Session, str, str]) -> None:
    session, a, b = two_cubes
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [0]}])
    seen = events_of(session)
    added = session.execute("tag.add", tag="inlet", members=[{"part": b, "faces": "all"}])
    assert added["changed"] and added["faces"] == 7 and added["parts"] == 2
    assert [(e, d.get("param")) for e, d in seen] == [
        ("node.changed", "members"),
        ("tag.changed", None),
    ]
    removed = session.execute("tag.remove", tag=SEL, members=[{"part": b, "faces": [5, [0, 1]]}])
    assert removed["members"][1]["faces"] == [[2, 4]]
    assert faces(session) == {"a": [0], "b": [2, 3, 4]}
    same = session.execute("tag.add", tag="inlet", members=[{"part": a, "faces": [0]}])
    assert same["changed"] is False  # nothing new: no undo step
    replaced = session.execute("tag.set", tag="inlet", members=[{"part": a, "faces": [[1, 2]]}])
    assert faces(session) == {"a": [1, 2]} and replaced["faces"] == 2
    assert session.execute("history.state")["undo"] == "set inlet"
    for _ in range(3):
        session.execute("history.undo")
    assert faces(session) == {"a": [0]}
    session.execute("history.redo")
    assert faces(session) == {"a": [0], "b": [0, 1, 2, 3, 4, 5]}


def test_invert_grow_and_expansion(two_cubes: tuple[Session, str, str]) -> None:
    session, a, b = two_cubes
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [0, 2]}])
    inverted = session.execute("tag.invert", tag="inlet", parts=["/geometry/parts"])
    assert faces(session) == {"a": [1, 3, 4, 5], "b": [0, 1, 2, 3, 4, 5]}
    assert inverted["members"][1] == {"part": b, "faces": "all"}
    grown = session.execute("tag.grow", tag="inlet")
    assert grown["changed"] is False and grown["added"] == 0  # every cube edge is 90 degrees
    session.execute("tag.set", tag="inlet", members=[{"part": a, "faces": [3]}])
    wide = session.execute("tag.grow", tag="inlet", angle_deg=95.0)
    assert wide["added"] == 5 and faces(session) == {"a": [0, 1, 2, 3, 4, 5]}
    seeded = session.execute(
        "tag.grow", tag="inlet", seeds=[{"part": b, "faces": [4]}], angle_deg=10.0
    )
    assert seeded["added"] == 1
    expanded = session.execute("tag.members", tag="inlet")
    assert expanded["faces"] == 7
    entry = expanded["members"][1]
    assert (
        entry["path"] == "/geometry/parts/b" and entry["faces"] == [4] and entry["part_faces"] == 6
    )
    assert entry["stale"] is False and entry["missing"] == [] and entry["exists"] is True


def test_region_grows_the_given_members_and_writes_nothing(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    single = session.execute("tag.region", members=[{"part": a, "faces": [0]}])
    assert single == {"members": [{"part": a, "faces": [[0, 0]]}]}  # a box edge is 90 degrees
    wide = session.execute(
        "tag.region",
        members=[{"part": a, "faces": [3]}, {"part": b, "faces": "all"}],
        angle_deg=95.0,
    )
    assert wide["members"] == [{"part": b, "faces": "all"}, {"part": a, "faces": [[0, 5]]}]
    assert session.require_project().find(TAGS_PATH) is None  # read-only: no tag group
    journaled = [r["command"] for r in Journal.read(tmp_path / "demo.jsonl")]
    assert journaled and "tag.region" not in journaled  # the journal records, but not this


def test_region_grows_the_split_cylinder_side_to_all_its_halves(tmp_path: Path) -> None:
    pytest.importorskip("OCP")
    import numpy as np

    from stokes.geometry.fingerprints import fingerprints_of
    from stokes.geometry.selection import ranges_from_mask
    from stokes.geometry.topology import facets_of, surface_of
    from tests.selection_support import split_cylinder, tessellated

    session = new_session(tmp_path)
    mesh = part(tessellated(split_cylinder(5.0, 40.0, 1), name="split"))
    p = add_part(session, "split", mesh)
    surface = surface_of([mesh])
    (printed,) = fingerprints_of(surface, facets_of(surface))
    side = [f for f in range(printed.faces) if abs(printed.normal[f][2]) < 0.5]
    assert len(side) == 2  # the split side, as many CAD exporters write it
    out = session.execute("tag.region", members=[{"part": p, "faces": [side[0]]}])
    expected = [list(r) for r in ranges_from_mask(np.isin(np.arange(printed.faces), side))]
    assert out == {"members": [{"part": p, "faces": expected}]}


def test_rename_and_delete(two_cubes: tuple[Session, str, str]) -> None:
    session, a, _ = two_cubes
    created = session.execute("tag.create", name="inlet", members=[{"part": a, "faces": "all"}])
    seen = events_of(session)
    renamed = session.execute("tag.rename", tag="inlet", name="intake")
    assert renamed["path"] == f"{TAGS_PATH}/intake"
    deleted = session.execute("tag.delete", tag=created["id"])
    assert deleted == {
        "id": created["id"],
        "path": f"{TAGS_PATH}/intake",
        "name": "intake",
        "deleted": True,
        "removed": [],  # no boundary or region used it
    }
    assert [e for e, _ in seen] == [
        "node.changed",
        "tag.changed",
        "node.removed",
        "tag.changed",
    ]
    session.execute("history.undo")
    assert session.execute("tag.list")[0]["name"] == "intake"


def test_journal_replays_the_same_selections(tmp_path: Path) -> None:
    session = new_session(tmp_path, "first")
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    session.execute(
        "tag.create", name="inlet", members=[{"part": "/geometry/parts/a", "faces": [3]}]
    )
    session.execute("tag.grow", tag="inlet", angle_deg=95.0)
    session.execute("tag.add", tag="inlet", members=[{"part": "/geometry/parts/b", "faces": "all"}])
    session.execute("tag.remove", tag="inlet", members=[{"part": b, "faces": [0]}])
    session.execute("tag.invert", tag="inlet", parts=[a])
    session.execute("tag.create", name="walls", members=[{"part": a, "faces": [1]}])
    session.execute("tag.rename", tag="walls", name="wall")
    records = Journal.read(tmp_path / "first.jsonl")
    assert [r["command"] for r in records[-7:]] == [
        "tag.create",
        "tag.grow",
        "tag.add",
        "tag.remove",
        "tag.invert",
        "tag.create",
        "tag.rename",
    ]
    assert records[-6]["params"]["result"]  # grow journals what it produced
    expected = {
        n: session.execute("node.get", node=f"{TAGS_PATH}/{n}")["params"] for n in ("inlet", "wall")
    }
    # Replayed with the meshes in place: the same text, versions included.
    second = new_session(tmp_path, "second")
    parts_end = max(i for i, r in enumerate(records) if r["command"] == "node.add") + 1
    replay(second, records[1:parts_end])
    for part_id, offset in ((a, 0.0), (b, 0.02)):
        put_mesh(second, part_id, part(cube(0.01, (offset, 0.0, 0.0))))
    replay(second, records[parts_end:])
    for name, params in expected.items():
        assert second.execute("node.get", node=f"{TAGS_PATH}/{name}")["params"] == params
    # Replayed with no mesh at all (imports still running): the same faces.
    third = new_session(tmp_path, "third")
    replay(third, records[1:])
    assert faces(third) == faces(session)
    assert faces(third, f"{TAGS_PATH}/wall") == {"a": [1]}


def test_the_demo_has_real_tags_not_plain_groups(tmp_path: Path) -> None:
    """The demo's tags hold real faces, so tagging a plain group is not what it demonstrates.

    **This test used to assert the opposite** — that `inlet`, `outlet` and `walls` were *plain
    groups*, `legacy: True`, and that `tag.add` refused them. That was true of the old demo, which
    created three empty tag groups and no geometry, so every tag-driven feature (glyphs, the
    Conditions panel, `boundary.assign`, `setup.check`) had nothing to work with and the default
    project made M1 look broken. The demo now imports real geometry and lets `boundary.assign`
    create
    each tag from real faces, so the assertion is inverted deliberately.

    The legacy plain-group path still exists and is still tested — see
    `test_boundary_commands.test_a_plain_group_boundary_becomes_typed_in_place`, which builds that
    shape explicitly rather than borrowing it from the demo.
    """
    from stokes.app.demo import build_demo

    session = Session()
    build_demo(session)
    listed = session.execute("tag.list")
    names = [entry["name"] for entry in listed]
    assert "inlet" in names and "outlet" in names and "walls" in names
    # Real tags, not legacy plain groups: each holds faces and carries a boundary kind.
    for entry in listed:
        if entry["name"] in ("inlet", "outlet", "walls"):
            assert not entry.get("legacy"), f"{entry['name']} is still a plain group"
    by_name = {entry["name"]: entry for entry in listed}
    assert by_name["inlet"]["kind"] == "boundary"
    # The names are taken, and a new one still works.
    with pytest.raises(ModelError):
        session.execute("tag.create", name="inlet")
    created = session.execute("tag.create", name="gap")
    assert created["path"] == f"{TAGS_PATH}/gap"


def test_a_changed_part_makes_members_stale_until_rematched(
    two_cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = two_cubes
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [0, 3]}])
    order = [3, 0, 5, 1, 2, 4]
    newer = SourceStamp(STAMP.name, STAMP.size + 1, STAMP.mtime_ns + 1)
    put_mesh(session, a, part(permuted(cube(0.01, name="a"), order), source=newer))
    assert session.execute("tag.list")[0]["stale"] == [a]
    with pytest.raises(SelectionStale) as caught:
        session.execute("tag.add", tag="inlet", members=[{"part": a, "faces": [1]}])
    assert caught.value.code == "tag.stale"
    with pytest.raises(SelectionStale):
        session.execute("tag.grow", tag="inlet")
    result = session.execute("tag.rematch", tag="inlet")
    assert result["changed"] and result["unmatched"] == 0 and result["stale"] == []
    (entry,) = result["report"]
    assert entry["matched"] == 2 and entry["reason"] == "re-matched"
    assert [(f["from"], f["to"]) for f in entry["faces"]] == [(0, 3), (3, 1)]
    assert all(f["confidence"] > 0.95 for f in entry["faces"])
    assert faces(session) == {"a": [1, 3]}
    session.execute("tag.add", tag="inlet", members=[{"part": a, "faces": [0]}])
    session.execute("history.undo")
    session.execute("history.undo")
    assert session.execute("tag.list")[0]["stale"] == [a]
    again = session.execute("tag.rematch", tag="inlet", min_confidence=0.99)
    assert again["unmatched"] == 0


def test_rematch_without_the_earlier_fingerprints_lists_every_face(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [2, 4]}])
    SelectionCache.of(session).project_closed()  # never saved, and memory forgets as well
    newer = SourceStamp(STAMP.name, STAMP.size, STAMP.mtime_ns + 5)
    put_mesh(session, a, part(cube(0.01, name="a"), source=newer))
    result = session.execute("tag.rematch", tag="inlet")
    (entry,) = result["report"]
    assert entry["unmatched"] == [2, 4] and entry["reason"].startswith("no fingerprints")
    assert result["members"] == [] and result["unmatched"] == 2


def test_the_archive_holds_the_versions_saved_selections_use(
    two_cubes: tuple[Session, str, str], tmp_path: Path
) -> None:
    session, a, b = two_cubes
    created = session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [1]}])
    session.execute(
        "tag.create", name="whole", members=[{"part": b, "faces": "all"}]
    )  # needs no version
    basis = created["members"][0]["basis"]
    folder = tmp_path / "demo.stokes"
    assert not archive_file(folder).exists()  # written when the project is saved
    session.execute("project.save")
    versions = read_archive(folder)
    assert set(versions) == {(a, basis)}
    kept = versions[(a, basis)]
    assert kept.faces == 6 and kept.triangles.tolist() == [2] * 6 and kept.quality == "fine"
    assert kept.area == pytest.approx([1e-4] * 6)
    session.execute("project.save", path=str(tmp_path / "copy"))  # Save as takes them along
    copy = tmp_path / "copy.stokes"
    assert set(read_archive(copy)) == {(a, basis)}
    archive_file(folder).write_bytes(archive_file(folder).read_bytes()[:-8])
    assert read_archive(folder) == {}  # a damaged archive is refused, not trusted
    session.execute("tag.delete", tag="inlet")
    session.execute("project.save")
    assert read_archive(copy) == {}  # only versions a saved tag uses stay


def test_rematch_after_reopening_reads_the_archive(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [0, 3]}])
    session.execute("project.save")
    reopened = Session()
    reopened.execute("project.open", path=str(tmp_path / "demo.stokes"))
    newer = SourceStamp(STAMP.name, STAMP.size, STAMP.mtime_ns + 7)
    put_mesh(reopened, a, part(permuted(cube(0.01, name="a"), [3, 0, 5, 1, 2, 4]), source=newer))
    result = reopened.execute("tag.rematch", tag="inlet")
    assert result["unmatched"] == 0 and faces(reopened) == {"a": [1, 3]}


def test_by_color_and_by_name(two_cubes: tuple[Session, str, str]) -> None:
    session, a, b = two_cubes
    session.execute("parameter.set", node=b, name="color", value="#cc331a")
    red = session.execute("tag.by_color", color="#CC331A")
    assert red["members"] == [{"part": b, "faces": "all"}] and red["level"] == "part"
    assert session.execute("tag.by_color", color="#cc331a", part=a)["count"] == 0
    named = session.execute("tag.by_name", pattern="A")
    assert named["members"] == [{"part": a, "faces": "all"}]
    by_path = session.execute("tag.by_name", pattern="/geometry/parts/*")
    assert by_path["count"] == 2
    created = session.execute("tag.create", name="inlet", members=named["members"])
    assert created["members"] == [{"part": a, "faces": "all"}]


def test_errors_are_typed(two_cubes: tuple[Session, str, str]) -> None:
    session, a, _ = two_cubes
    bare = add_part(session, "bare", None)
    u_in = session.execute("parameter.define", name="U", dimension="velocity", value="1 [m/s]")[
        "id"
    ]
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [0]}])
    cases: list[tuple[type[StokesError], str, dict[str, Any]]] = [
        (UnknownNode, "tag.add", {"tag": "nope", "members": []}),
        (ValidationError, "tag.create", {"name": "x", "kind": "volume"}),
        (ValidationError, "tag.create", {"name": "x", "color": "red"}),
        (ValidationError, "tag.create", {"name": "1x"}),
        (
            SelectionError,
            "tag.add",
            {"tag": "inlet", "members": [{"part": a, "faces": [6]}]},
        ),
        (
            SelectionError,
            "tag.add",
            {"tag": "inlet", "members": [{"part": "/geometry/parts", "faces": [0]}]},
        ),
        (
            UnknownNode,
            "tag.add",
            {"tag": "inlet", "members": [{"part": "/geometry/parts/zz"}]},
        ),
        (
            SelectionError,
            "tag.add",
            {"tag": "inlet", "members": [{"part": "/physics"}]},
        ),
        (ValidationError, "tag.add", {"tag": "inlet", "members": [{"part": u_in, "faces": "all"}]}),
        (SelectionError, "tag.grow", {"tag": "inlet", "angle_deg": 0.0}),
        (
            MeshUnavailable,
            "tag.grow",
            {"tag": "inlet", "seeds": [{"part": bare, "faces": [0]}]},
        ),
        (ValidationError, "tag.rematch", {"tag": "inlet", "min_confidence": 2.0}),
        (ValidationError, "tag.by_color", {"color": "#12345"}),
        (ValidationError, "tag.invert", {"tag": "inlet", "parts": [3]}),
        (ValidationError, "tag.add", {"tag": "/geometry/parts/a", "members": []}),
        (SelectionError, "tag.region", {"members": [{"part": a, "faces": [0]}], "angle_deg": 0.0}),
        (
            MeshUnavailable,
            "tag.region",
            {"members": [{"part": bare, "faces": [0]}]},
        ),
        (SelectionError, "tag.region", {"members": []}),
    ]
    for error, name, params in cases:
        with pytest.raises(error):
            session.execute(name, **params)
    assert session.execute("tag.grow", tag="inlet")["changed"] is False
    # members are checked when written (T-013): a value that does not parse to the members the
    # tag commands produce is refused with a validation error that names the problem, and the
    # tag keeps the members it had. The read path still refuses such values, as a backstop for
    # values written before the write check began.
    for value in ('[{"part":"n_00000000"}]', "DROP TABLE", {"set": {1, 2}}):
        with pytest.raises(ValidationError):
            session.execute("parameter.set", node=SEL, name="members", value=value)
    assert faces(session) == {"a": [0]}


def test_members_set_as_json_data_read_like_the_stored_text(
    two_cubes: tuple[Session, str, str],
) -> None:
    """`members` is a json parameter, so parameter.set can store a list: the commands and the
    fingerprint archive a save keeps read it like the text the commands write."""
    session, a, _ = two_cubes
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [0]}])
    node = session.require_project().get(SEL)
    stored = json.loads(node.params["members"])
    session.execute("parameter.set", node=SEL, name="members", value=stored)
    assert isinstance(node.params["members"], list)
    assert faces(session) == {"a": [0]}
    assert referenced(session.require_project()) == {(a, stored[0]["basis"])}


def test_the_commands_and_the_node_type_have_schemas() -> None:
    commands = {c["name"]: c for c in default_registry().json_schema()}
    for name in (
        "tag.create", "tag.add", "tag.remove", "tag.set",
        "tag.invert", "tag.grow", "tag.region", "tag.anchor", "tag.related",
        "tag.by_color", "tag.by_name", "tag.rename", "tag.delete",
        "tag.list", "tag.members", "tag.rematch",
        "geometry.check", "geometry.export_stl", "geometry.export_step",
    ):  # fmt: skip
        assert name in commands, name
    assert commands["tag.list"]["mutates"] is False
    assert commands["tag.region"]["mutates"] is False
    assert commands["tag.anchor"]["mutates"] is False
    assert commands["tag.related"]["mutates"] is False
    assert commands["tag.grow"]["input_schema"]["properties"]["angle_deg"]["default"] == 30.0
    session = Session()
    spec = session.execute("node.types")["tag"]
    assert spec["properties"]["kind"]["enum"] == ["boundary", "region", "plain"]
    assert spec["properties"]["members"]["default"] == "[]"
    assert spec["properties"]["members"]["x-kind"] == "json"
    assert spec["properties"]["color"]["pattern"] == "^#[0-9a-f]{6}$"


# ---------------------------------------------------------------- tags in use (ADR-0022 2.8)


def test_delete_refuses_a_tag_in_use_unless_forced(two_cubes: tuple[Session, str, str]) -> None:
    """With force, one step deletes the region that uses the tag, with its boundaries, and
    clears the partner that named one of them; undo brings everything back."""
    session, a, b = two_cubes
    domain = session.execute("region.create", name="domain", bodies=[a])
    inner = session.execute(
        "boundary.assign", target=[{"part": a, "faces": [0]}], kind="cyclic", region="domain"
    )["boundary"]
    outer = session.execute(
        "boundary.assign", target=[{"part": b, "faces": [0]}], kind="cyclic", region="other"
    )["boundary"]
    session.execute("boundary.set", boundary=outer["path"], values={"partner": inner["id"]})
    project = session.require_project()
    region_tag = project.get(domain["tag"]["id"])
    with pytest.raises(ReferencedNode, match="pass force") as caught:
        session.execute("tag.delete", tag=region_tag.path)
    assert caught.value.details["referrers"] == [
        {"id": domain["id"], "path": "/regions/domain", "param": "tag"}
    ]
    with pytest.raises(ReferencedNode):
        session.execute("node.remove", node=region_tag.id)
    with pytest.raises(ReferencedNode):
        session.execute("node.remove", node=TAGS_PATH)
    before = project.to_json()["root"]
    seen = events_of(session)
    deleted = session.execute("tag.delete", tag="domain", force=True)
    assert deleted == {
        "id": region_tag.id,
        "path": f"{TAGS_PATH}/domain",
        "name": "domain",
        "deleted": True,
        "removed": [domain["id"]],
    }
    assert project.find("/regions/domain") is None and project.find(inner["id"]) is None
    assert project.get(outer["id"]).params["partner"] is None
    assert [(e, d["path"]) for e, d in seen if e != "node.changed"] == [
        ("node.removed", "/regions/domain"),
        ("node.removed", f"{TAGS_PATH}/domain"),
        ("tag.changed", f"{TAGS_PATH}/domain"),
        ("boundary.changed", inner["path"]),
        ("boundary.changed", outer["path"]),
        ("region.changed", "/regions/domain"),
    ]
    assert session.execute("history.state")["undo"] == "delete tag domain"
    session.execute("history.undo")
    assert project.to_json()["root"] == before
    forced = session.execute("tag.delete", tag=inner["tag"]["id"], force=True)
    assert forced["removed"] == [inner["id"]]
    assert project.get(outer["id"]).params["partner"] is None
    plain = session.execute("tag.create", name="plain", kind="plain")
    assert session.execute("tag.delete", tag="plain")["removed"] == []
    assert session.execute("history.state")["undo_kind"] == "remove_node"  # unchanged when unused
    assert project.find(plain["id"]) is None


def test_list_names_the_users_of_each_tag_and_warns_of_shared_faces(
    two_cubes: tuple[Session, str, str],
) -> None:
    session, a, b = two_cubes
    walls = session.execute("tag.create", name="walls", members=[{"part": a, "faces": [0, 1, 2]}])
    inlet = session.execute(
        "boundary.assign", target=[{"part": a, "faces": [2, 3]}], kind="velocity_inlet"
    )
    session.execute("tag.create", name="body", members=[{"part": b, "faces": "all"}])
    session.execute("tag.create", name="lid", members=[{"part": b, "faces": [5]}])
    session.execute(
        "tag.create", name="scratch", kind="plain", members=[{"part": a, "faces": "all"}]
    )
    listed = {t["name"]: t for t in session.execute("tag.list")}
    assert listed["inlet"]["boundaries"] == [inlet["boundary"]["id"]]
    assert listed["inlet"]["regions"] == [] and listed["walls"]["boundaries"] == []
    assert listed["fluid"]["regions"] == [inlet["region"]["id"]]
    shared = {
        "code": "tag.overlap",
        "tags": [walls["id"], inlet["tag"]["id"]],
        "part": a,
        "faces": 1,
        "message": "walls and inlet share 1 face of /geometry/parts/a",
    }
    assert listed["walls"]["warnings"] == [shared] and listed["inlet"]["warnings"] == [shared]
    whole = listed["body"]["warnings"]  # a whole part and one of its faces share that face
    assert [(w["tags"], w["faces"]) for w in whole] == [
        ([listed["body"]["id"], listed["lid"]["id"]], 1)
    ]
    assert listed["scratch"]["warnings"] == [] and listed["fluid"]["warnings"] == []
    session.execute("tag.create", name="twin", members=[{"part": b, "faces": "all"}])
    twin = {t["name"]: t for t in session.execute("tag.list")}["twin"]["warnings"]
    assert sorted(w["faces"] for w in twin) == [1, 6]  # all of body's faces, and lid's one


# ---------------------------------------------------------------- tag.anchor (ADR-0022 8.1)


def test_anchor_of_one_face_gives_its_centre_and_normal(
    two_cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = two_cubes
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [3]}])
    out = session.execute("tag.anchor", tags=["/geometry/tags/inlet"])
    (anchor,) = out["anchors"]
    assert anchor["tag"] == SEL
    assert anchor["centroid"] == pytest.approx([0.005, 0.01, 0.005])
    assert anchor["normal"] == pytest.approx([0.0, 1.0, 0.0])
    assert anchor["area"] == pytest.approx(1e-4)
    assert anchor["extent"] == 0.0 and anchor["faces"] == 1 and anchor["parts"] == 1
    assert anchor["partial"] is False
    # A square face holds its own centre, so the glyph is drawn there.
    assert anchor["position"] == pytest.approx(anchor["centroid"])


def l_shape() -> np.ndarray:
    """An L of three unit squares in z = 0, the square (1..2, 1..2) missing: six triangles."""
    squares = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    triangles = []
    for x, y in squares:
        a, b, c, d = (x, y, 0), (x + 1, y, 0), (x + 1, y + 1, 0), (x, y + 1, 0)
        triangles += [(a, b, c), (a, c, d)]
    return np.array(triangles, dtype=np.float64)


def test_a_glyph_on_an_l_shaped_face_is_drawn_on_the_face() -> None:
    """The elbow's symmetry plane is an L: its centroid lies in the missing corner, off the face."""
    triangles = l_shape()
    centroid = triangles.reshape(-1, 3).mean(axis=0)  # (0.8333, 0.8333, 0): on the face here
    assert _point_on_faces(centroid, triangles) == pytest.approx(centroid)
    off = np.array([1.4, 1.4, 0.0])  # in the missing square
    moved = _point_on_faces(off, triangles)
    assert not (1.0 < moved[0] < 2.0 and 1.0 < moved[1] < 2.0), f"{moved} is still off the face"
    assert moved[2] == pytest.approx(0.0)
    # Above the face, not on it: moved onto it.
    assert _point_on_faces(np.array([0.5, 0.5, 0.3]), triangles)[2] == pytest.approx(0.0)
    # Nothing to place on: the point is kept.
    assert _point_on_faces(off, np.zeros((0, 3, 3))) == pytest.approx(off)


def test_anchor_of_two_opposite_faces_lies_on_a_face(two_cubes: tuple[Session, str, str]) -> None:
    """T-046-fix-3: a tag of two OPPOSITE faces must anchor ON a face, not inside the solid.

    This test used to assert `centroid == [0.005, 0.005, 0.005]` — the centre of the box, which is
    *inside* it — and a normal of `[0, 0, 1]` taken from the first face while the point sat in the
    middle. A glyph drawn there is buried in the part and never seen, which is how the defect was
    found (Claude's capture showed the outlet arrow on the inlet face).

    §8.1 places the glyph "at the centroid, along the normal"; an area-weighted centre satisfies
    that only when the member faces are coplanar. Here the two members disagree, so the anchor
    moves to the member face nearest that centre, carrying that face's own centroid and normal — a
    point genuinely on the surface. `area` and `extent` stay the sums over both faces.
    """
    session, a, _ = two_cubes
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [3]}])
    session.execute("tag.create", name="pair", members=[{"part": a, "faces": [4, 5]}])
    by_tag = {e["tag"]: e for e in session.execute("tag.anchor")["anchors"]}
    assert len(by_tag) == 2  # default: every tag
    pair = by_tag[f"{TAGS_PATH}/pair"]
    # The anchor is on a face: the box is 10 mm, so a face centre has one coordinate at 0 or 0.01
    # rather than all three at the middle.
    centre = pair["centroid"]
    at_surface = any(abs(c) < 1e-9 or abs(c - 0.01) < 1e-9 for c in centre)
    assert at_surface, f"the anchor {centre} is not on a face of the box"
    # Its normal is the normal of THAT face — a unit axis, not a partial sum.
    assert pair["normal"] in (
        [0.0, 0.0, 1.0],
        [0.0, 0.0, -1.0],
        [0.0, 1.0, 0.0],
        [0.0, -1.0, 0.0],
        [1.0, 0.0, 0.0],
        [-1.0, 0.0, 0.0],
    )
    # The figures that describe the whole member set are unchanged: both faces count.
    assert pair["area"] == pytest.approx(2e-4) and pair["extent"] == pytest.approx(0.01)
    assert pair["faces"] == 2 and pair["parts"] == 1 and pair["partial"] is False
    with pytest.raises(UnknownNode):
        session.execute("tag.anchor", tags=["nope"])


def test_anchor_flags_the_member_parts_without_a_mesh(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    bare = add_part(session, "bare", None)
    session.execute("tag.create", name="missing", members=[{"part": bare, "faces": "all"}])
    session.execute(
        "tag.create",
        name="mixed",
        members=[{"part": a, "faces": [0]}, {"part": bare, "faces": "all"}],
    )
    by_tag = {e["tag"]: e for e in session.execute("tag.anchor")["anchors"]}
    missing = by_tag[f"{TAGS_PATH}/missing"]
    assert missing["partial"] is True
    assert missing["faces"] == 0 and missing["parts"] == 0 and missing["area"] == 0.0
    assert missing["extent"] == 0.0 and missing["centroid"] == [0.0, 0.0, 0.0]
    assert missing["normal"] == [0.0, 0.0, 1.0]
    mixed = by_tag[f"{TAGS_PATH}/mixed"]
    assert mixed["partial"] is True and mixed["faces"] == 1 and mixed["parts"] == 1


def test_anchor_is_read_only_and_not_journaled(
    two_cubes: tuple[Session, str, str], tmp_path: Path
) -> None:
    session, a, _ = two_cubes
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [0]}])
    session.execute("tag.anchor")
    journaled = [r["command"] for r in Journal.read(tmp_path / "demo.jsonl")]
    assert "tag.anchor" not in journaled


# ---------------------------------------------------------------- tag.related (ADR-0022 6.4)


def test_related_same_area_over_the_two_equal_cubes(two_cubes: tuple[Session, str, str]) -> None:
    session, a, b = two_cubes
    out = session.execute("tag.related", members=[{"part": a, "faces": [5]}], relation="same_area")
    assert out["relation"] == "same_area" and out["tolerance"] == 0.02
    assert out["count"] == 11  # the other five faces of a, and all six of b
    assert {m["part"]: m["faces"] for m in out["members"]} == {a: [[0, 4]], b: "all"}
    scoped = session.execute(
        "tag.related", members=[{"part": a, "faces": [5]}], relation="same_area", parts=[a]
    )
    assert scoped["count"] == 5


def test_related_same_area_only_the_bottom_when_not_a_cube(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    box = add_part(session, "box", part(box_mesh(0.02, 0.01, 0.005)))
    out = session.execute(
        "tag.related", members=[{"part": box, "faces": [5]}], relation="same_area"
    )
    assert out["count"] == 1
    assert out["members"] == [{"part": box, "faces": [[4, 4]]}]


def test_related_parallel_and_coplanar_across_the_two_cubes(
    two_cubes: tuple[Session, str, str],
) -> None:
    session, a, b = two_cubes
    out = session.execute("tag.related", members=[{"part": a, "faces": [3]}], relation="parallel")
    assert out["tolerance"] == 0.035
    assert out["members"] == [{"part": b, "faces": [[3, 3]]}] and out["count"] == 1
    coplanar = session.execute(
        "tag.related", members=[{"part": a, "faces": [3]}], relation="coplanar"
    )
    # b's face 3 lies in the same plane y = 0.01
    assert coplanar["members"] == [{"part": b, "faces": [[3, 3]]}] and coplanar["count"] == 1
    assert coplanar["tolerance"] == 0.001


def test_related_tangent_grows_like_region_minus_the_pick(
    two_cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = two_cubes
    out = session.execute("tag.related", members=[{"part": a, "faces": [3]}], relation="tangent")
    assert out["count"] == 0 and out["members"] == []  # a box edge is 90 degrees
    wide = session.execute(
        "tag.related", members=[{"part": a, "faces": [3]}], relation="tangent", tolerance=95.0
    )
    assert wide["tolerance"] == 95.0
    assert wide["members"] == [{"part": a, "faces": [[0, 2], [4, 5]]}] and wide["count"] == 5


def test_related_same_color_keeps_the_coloured_part_minus_the_pick(
    two_cubes: tuple[Session, str, str],
) -> None:
    session, _, b = two_cubes
    session.execute("parameter.set", node=b, name="color", value="#cc331a")
    out = session.execute("tag.related", members=[{"part": b, "faces": [0]}], relation="same_color")
    assert out["count"] == 5 and out["tolerance"] is None
    assert out["members"] == [{"part": b, "faces": [[1, 5]]}]


def test_related_errors_are_typed(two_cubes: tuple[Session, str, str], tmp_path: Path) -> None:
    session, a, b = two_cubes
    bare = add_part(session, "bare", None)
    session.execute("parameter.set", node=b, name="color", value="#cc331a")
    cases: list[tuple[type[StokesError], dict[str, Any]]] = [
        (SelectionError, {"members": [{"part": a, "faces": [0]}], "relation": "round"}),
        (SelectionError, {"members": [], "relation": "same_area"}),
        (
            ValidationError,
            {"members": [{"part": b, "faces": [0]}], "relation": "same_color", "tolerance": 0.02},
        ),
        (
            ValidationError,
            {"members": [{"part": a, "faces": [0]}], "relation": "same_area", "tolerance": 0.0},
        ),
        (
            SelectionError,
            {"members": [{"part": a, "faces": [0]}], "relation": "tangent", "tolerance": 0.0},
        ),
        # three mutually perpendicular faces: not cylindrical
        (SelectionError, {"members": [{"part": a, "faces": [0, 2, 4]}], "relation": "coaxial"}),
        (MeshUnavailable, {"members": [{"part": bare, "faces": [0]}], "relation": "same_area"}),
    ]
    for error, params in cases:
        with pytest.raises(error) as raised:
            session.execute("tag.related", **params)
        if error is SelectionError:
            assert raised.value.code == "tag.invalid"
    # a single planar face passes the eigenvalue rule, and simply finds nothing
    out = session.execute("tag.related", members=[{"part": a, "faces": [5]}], relation="coaxial")
    assert out["count"] == 0
    session.execute("tag.related", members=[{"part": a, "faces": [3]}], relation="parallel")
    journaled = [r["command"] for r in Journal.read(tmp_path / "demo.jsonl")]
    assert "tag.related" not in journaled


def test_related_coaxial_finds_the_other_hole_of_the_same_radius(tmp_path: Path) -> None:
    pytest.importorskip("OCP")
    from stokes.geometry.fingerprints import fingerprints_of
    from stokes.geometry.selection import ranges_from_mask
    from stokes.geometry.store import GeometryStore
    from stokes.geometry.topology import facets_of, surface_of
    from tests.selection_support import split_cylinder, tessellated

    session = new_session(tmp_path)
    hole_a = add_part(session, "a", part(tessellated(split_cylinder(5.0, 40.0, 1), name="a")))
    hole_b = add_part(
        session,
        "b",
        part(tessellated(split_cylinder(5.0, 40.0, 1, (0.0, 0.0, 60.0)), name="b")),
    )
    hole_c = add_part(
        session,
        "c",
        part(tessellated(split_cylinder(6.0, 40.0, 1, (0.0, 0.0, 120.0)), name="c")),
    )

    def side_faces(part_id: str) -> list[int]:
        """The side of the hole, as many CAD exporters write it: two faces."""
        mesh = GeometryStore.of(session).mesh(part_id)
        assert mesh is not None
        surface = surface_of([mesh])
        (printed,) = fingerprints_of(surface, facets_of(surface))
        return [f for f in range(printed.faces) if abs(printed.normal[f][2]) < 0.5]

    def ranges_of(part_id: str, ids: list[int]) -> list[list[int]]:
        mesh = GeometryStore.of(session).mesh(part_id)
        assert mesh is not None
        surface = surface_of([mesh])
        (printed,) = fingerprints_of(surface, facets_of(surface))
        return [list(r) for r in ranges_from_mask(np.isin(np.arange(printed.faces), ids))]

    side_a, side_b, side_c = (side_faces(p) for p in (hole_a, hole_b, hole_c))
    assert len(side_a) == 2 and len(side_b) == 2 and len(side_c) == 2
    out = session.execute(
        "tag.related", members=[{"part": hole_a, "faces": side_a}], relation="coaxial"
    )
    assert out["tolerance"] == 0.02 and out["count"] == 2
    found = {m["part"]: m["faces"] for m in out["members"]}
    # the picked hole's own faces are out, and the other-radius hole stays out
    assert (
        found == {hole_b: ranges_of(hole_b, side_b)} and hole_a not in found and hole_c not in found
    )
    # one side face alone locates the axis: the rest of hole a and both of hole b match
    solo = session.execute(
        "tag.related", members=[{"part": hole_a, "faces": side_a[:1]}], relation="coaxial"
    )
    solo_found = {m["part"]: m["faces"] for m in solo["members"]}
    assert solo["count"] == 3 and hole_c not in solo_found
    assert solo_found[hole_a] == ranges_of(hole_a, side_a[1:])
    assert solo_found[hole_b] == ranges_of(hole_b, side_b)


# ---------------------------------------------------------------- geometry.check (T-031)
# The B-rep pass is a background job over the shape provider; these cases pin what the command
# decides before any worker starts, so they need no OCP. The job itself is in
# tests/test_geometry_brep.py.


def _jobs_started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Record every check job the command starts, without running one."""
    seen: list[dict[str, str]] = []

    def start(self: Any, specs: Any, bases: dict[str, str], *, project: Any) -> None:
        seen.append(dict(bases))
        return

    monkeypatch.setattr(CheckStore, "start", start)
    return seen


def _with_stored_brep(session: Session, part_id: str, *, basis: str) -> None:
    """Give the part the findings a finished job would have left in ``meta.check.brep``."""
    node = session.require_project().get(part_id)
    node.meta = {
        **node.meta,
        "check": {
            "brep": {
                "checked": True,
                "basis": basis,
                "ok": True,
                "free_edges": 0,
                "free_edge_faces": [],
                "at": "then",
            }
        },
    }


def test_geometry_check_has_wait_force_and_a_cancel_command(tmp_path: Path) -> None:
    commands = {c["name"]: c for c in default_registry().json_schema()}
    properties = commands["geometry.check"]["input_schema"]["properties"]
    assert properties["wait"]["default"] is False and properties["force"]["default"] is False
    assert commands["geometry.cancel_check"]["mutates"] is False
    # nothing runs: cancelling reports an empty list rather than refusing
    session = new_session(tmp_path)
    assert session.execute("geometry.cancel_check") == {"cancelled": []}


def test_a_checked_part_is_not_checked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second call whose findings are already for the part's version starts no job."""
    session = new_session(tmp_path)
    part_id = add_part(session, "out", part(cube(0.01)))
    session.execute("parameter.set", node=part_id, name="derived", value="n_00000000")
    basis = GeometryStore.of(session).basis(session.require_project().get(part_id))
    _with_stored_brep(session, part_id, basis=basis)
    started = _jobs_started(monkeypatch)
    again = session.execute("geometry.check", part=part_id, wait=True)
    assert started == []
    assert again["brep"]["checked"] is True and again["brep"]["at"] == "then"
    # and the findings survive the command's own write of the tessellation summary
    meta = session.execute("node.get", node=part_id)["meta"]["check"]
    assert meta["brep"]["at"] == "then" and meta["worst"] == "ok"


def test_force_checks_the_brep_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = new_session(tmp_path)
    part_id = add_part(session, "out", part(cube(0.01)))
    session.execute("parameter.set", node=part_id, name="derived", value="n_00000000")
    basis = GeometryStore.of(session).basis(session.require_project().get(part_id))
    _with_stored_brep(session, part_id, basis=basis)
    started = _jobs_started(monkeypatch)
    session.execute("geometry.check", part=part_id, force=True, wait=True)
    assert started == [{part_id: basis}]


def test_a_mesh_only_part_is_not_checked_and_starts_no_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = new_session(tmp_path)
    part_id = add_part(session, "shell", part(cube(0.01)))
    session.execute("parameter.set", node=part_id, name="family", value="mesh")
    started = _jobs_started(monkeypatch)
    result = session.execute("geometry.check", part=part_id)
    assert result["brep"] == {"checked": False, "reason": "mesh-only part"}
    assert started == [] and result["stats"]["faces"] == 6


def test_a_part_with_no_brep_to_load_starts_no_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No source file, no cache and no operation behind it: there is nothing a worker could load."""
    session = new_session(tmp_path)
    part_id = add_part(session, "cube", part(cube(0.01)))
    started = _jobs_started(monkeypatch)
    result = session.execute("geometry.check", part=part_id, force=True, wait=True)
    assert started == [] and result["brep"]["checked"] is False
    assert "no source file" in result["brep"]["reason"]


@pytest.fixture(autouse=True)
def _demo_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """**Keep the demo out of the real user folder** (Claude's defect 9).

    `build_demo` writes its STEP through `demo_folder()`, which is under `STOKES_HOME` (or the
    user's
    home). These tests called it with no redirect, so they read and wrote `~/.stokes/demo` — which
    is
    why a demo built by an EARLIER RUN, with different tags, was served to them: the content-hashed
    name matched a file left behind, so the current generator's tags never appeared and the tests
    failed on the previous run's data. Two failures in the full suite were that, not the code.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path_factory.mktemp("stokes-home")))
