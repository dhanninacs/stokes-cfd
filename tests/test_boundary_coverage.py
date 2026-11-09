"""boundary.overlap and boundary.uncovered through Session.execute: the faces an assign would
share with an existing boundary (decisions log G10: warned, allowed, refused by the compiler), and
the faces no boundary holds, per part. Both are queries: nothing is journaled, and a replayed
journal answers the same. Synthetic cubes, no OCP."""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Journal, Session, default_registry, replay
from stokes.errors import UnknownNode, ValidationError
from stokes.geometry.errors import SelectionError
from tests.selection_support import add_part, cube, new_session, part


@pytest.fixture
def cubes(tmp_path: Path) -> tuple[Session, str, str]:
    """Two meshed 6-face cubes ``a`` and ``b`` and a fluid material."""
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    session.execute("material.create", name="water")
    return session, a, b


def test_overlap_names_the_boundary_that_already_holds_the_faces(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, b = cubes
    inlet = session.execute(
        "boundary.assign", target=[{"part": a, "faces": [0, 1]}], kind="velocity_inlet"
    )["boundary"]
    session.execute(
        "tag.create", name="scratch", kind="plain", members=[{"part": a, "faces": "all"}]
    )
    found = session.execute("boundary.overlap", members=[{"part": a, "faces": [1, 2]}])
    assert found["faces"] == 1
    [only] = found["overlaps"]
    assert only["part"] == a and only["faces"] == 1
    assert only["tag"]["name"] == "inlet"
    assert only["boundaries"] == [
        {"id": inlet["id"], "name": "inlet", "path": inlet["path"], "kind": "velocity_inlet"}
    ]  # a plain tag holding the same faces is not a boundary and is not named
    assert session.execute("boundary.overlap", members=[{"part": a, "faces": [3]}]) == {
        "overlaps": [],
        "faces": 0,
    }
    other = session.execute("boundary.overlap", members=[{"part": b, "faces": "all"}])
    assert other["overlaps"] == []
    whole = session.execute("boundary.overlap", members=[{"part": a, "faces": "all"}])
    assert whole["faces"] == 2


def test_overlap_after_an_assign_matches_the_tag_list_warning(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, _ = cubes
    session.execute("boundary.assign", target=[{"part": a, "faces": [0, 1]}], kind="wall")
    asked = session.execute("boundary.overlap", members=[{"part": a, "faces": [1, 2]}])
    session.execute(
        "boundary.assign", target=[{"part": a, "faces": [1, 2]}], kind="velocity_inlet"
    )  # assigned anyway: allowed while preparing (G10)
    warned = [w for t in session.execute("tag.list") for w in t["warnings"]]
    assert warned and warned[0]["faces"] == asked["faces"] == 1


def test_uncovered_lists_the_faces_no_boundary_holds_per_part(
    cubes: tuple[Session, str, str],
) -> None:
    session, a, b = cubes
    assert session.execute("boundary.uncovered") == {"parts": [], "faces": 0}  # no region yet
    fresh = session.execute("boundary.uncovered", part=a)
    assert fresh["faces"] == 6 and fresh["parts"][0]["ranges"] == [[0, 5]]
    session.execute("region.create", name="domain", bodies=[a, b], material="water")
    session.execute(
        "boundary.assign", target=[{"part": a, "faces": [0, 2, 3]}], kind="velocity_inlet"
    )
    session.execute("boundary.assign", target=[{"part": b, "faces": "all"}], kind="wall")
    found = session.execute("boundary.uncovered")
    by_part = {p["part"]: p for p in found["parts"]}
    assert by_part[a]["ranges"] == [[1, 1], [4, 5]] and by_part[a]["faces"] == 3
    assert by_part[a]["total"] == 6 and by_part[a]["name"] == "a"
    assert by_part[b]["faces"] == 0 and by_part[b]["ranges"] == []
    assert found["faces"] == 3
    region = session.execute("region.list")[0]
    assert region["uncovered_faces"] == found["faces"]  # the same count the region reports
    only = session.execute("boundary.uncovered", part="/geometry/parts/a")
    assert [p["part"] for p in only["parts"]] == [a]


def test_queries_are_not_journaled_and_replay_answers_the_same(
    cubes: tuple[Session, str, str], tmp_path: Path
) -> None:
    session, a, _ = cubes
    session.execute("region.create", name="domain", bodies=[a], material="water")
    session.execute("boundary.assign", target=[{"part": a, "faces": [0]}], kind="wall")
    before = len(Journal.read(tmp_path / "demo.jsonl"))
    overlap = session.execute("boundary.overlap", members=[{"part": a, "faces": [0, 1]}])
    uncovered = session.execute("boundary.uncovered")
    records = Journal.read(tmp_path / "demo.jsonl")
    assert len(records) == before
    assert all(r["command"] not in ("boundary.overlap", "boundary.uncovered") for r in records)
    schema = {c["name"]: c for c in default_registry().json_schema()}
    assert schema["boundary.overlap"]["input_schema"]["required"] == ["members"]
    assert "part" in schema["boundary.uncovered"]["input_schema"]["properties"]

    fresh = Session()
    first, *rest = records
    assert first["command"] == "project.new"
    replay(fresh, [{**first, "params": {**first["params"], "path": str(tmp_path / "again")}}])
    replay(fresh, rest)  # the parts carry their face counts, so no mesh is needed
    assert fresh.execute("boundary.overlap", members=[{"part": a, "faces": [0, 1]}]) == overlap
    assert fresh.execute("boundary.uncovered") == uncovered


def test_bad_references_are_refused(cubes: tuple[Session, str, str]) -> None:
    session, a, _ = cubes
    with pytest.raises(UnknownNode):
        session.execute("boundary.uncovered", part="/geometry/parts/nope")
    with pytest.raises(ValidationError):
        session.execute("boundary.overlap", members="not a list")
    with pytest.raises(SelectionError):
        session.execute("boundary.overlap", members=[{"part": a}])  # names no faces


def test_uncovered_describes_the_faces_when_asked(cubes: tuple[Session, str, str]) -> None:
    """``describe`` says what each open face looks like, so a guided list can tell the ends of
    a pipe apart; without it the answer is the plain ranges (and replays without a mesh)."""
    session, a, _ = cubes
    session.execute("region.create", name="domain", bodies=[a], material="water")
    session.execute("boundary.assign", target=[{"part": a, "faces": [0]}], kind="wall")
    plain = session.execute("boundary.uncovered", part=a)
    assert "described" not in plain["parts"][0]
    [entry] = session.execute("boundary.uncovered", part=a, describe=True)["parts"]
    described = entry["described"]
    assert [d["face"] for d in described] == [1, 2, 3, 4, 5]
    for d in described:
        assert d["flat"] is True  # every face of a cube is a plane
        assert d["area"] == pytest.approx(1e-4, rel=1e-6)  # a 10 mm cube
        assert sum(v * v for v in d["normal"]) == pytest.approx(1.0)
        assert len(d["centroid"]) == 3


def test_in_2d_the_faces_across_the_plane_are_the_meshers_not_unassigned(
    cubes: tuple[Session, str, str],
) -> None:
    """ADR-0031: the domain part's faces on its bounding planes across the 2D plane become the
    `empty` front and back, so `boundary.uncovered` does not list them."""
    session, a, _ = cubes
    session.execute("region.assign", target=a, material="water", name="fluid")
    session.execute("boundary.assign", target=[{"part": a, "faces": [0]}], kind="velocity_inlet")
    before = session.execute("boundary.uncovered", part=a)["parts"][0]
    assert before["ranges"] == [[1, 5]]
    session.execute("mesh.configure", values={"domain_part": a, "two_d": "planar", "plane": "xy"})
    after = session.execute("boundary.uncovered", part=a)["parts"][0]
    assert after["ranges"] == [[1, 3]]  # z-min (4) and z-max (5) are the front and back
    session.execute("mesh.configure", values={"plane": "yz"})
    across_x = session.execute("boundary.uncovered", part=a)["parts"][0]
    assert across_x["ranges"] == [[2, 5]]  # x-max (1) is a back face now; the inlet holds x-min
