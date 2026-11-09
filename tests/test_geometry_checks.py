"""Tessellation checks (ADR-0020 decision 6): a closed cube is clean; holes, non-manifold edges,
flipped and inverted faces, degenerate and sliver triangles, untriangulated faces, empty and
implausibly sized parts are found; ``geometry.check`` reports them per part and per group and
keeps the summary in the part's meta. Synthetic cubes; the OCP fixtures where OCP is present."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.geometry.checks import PartCheck, check_meshes
from stokes.geometry.mesh import PartMesh, TriMesh
from tests.selection_support import (
    add_part,
    corners_of,
    cube,
    events_of,
    mesh_from_corners,
    new_session,
    part,
)


def check(mesh: TriMesh) -> PartCheck:
    (found,) = check_meshes([PartMesh((mesh,))])
    return found


def codes(found: PartCheck) -> dict[str, int]:
    return {f.code: f.count for f in found.findings}


def edited(
    mesh: TriMesh, corners: np.ndarray[Any, Any], ids: list[int] | np.ndarray[Any, Any]
) -> TriMesh:
    return mesh_from_corners(corners, ids, name=mesh.name)


def test_a_closed_cube_is_clean() -> None:
    found = check(cube(0.01))
    assert found.findings == () and found.worst == "ok"
    stats = found.stats
    assert stats["closed"] and stats["free_edges"] == 0 and stats["edges"] == 18
    assert stats["volume"] == pytest.approx(1e-6, rel=1e-5)
    assert stats["area"] == pytest.approx(6e-4, rel=1e-5)
    assert stats["bbox"] == pytest.approx([0, 0, 0, 0.01, 0.01, 0.01], abs=1e-9)
    assert found.to_json()["ok"] is True


def test_a_hole_shows_as_free_edges() -> None:
    whole = cube(0.01)
    corners, ids = corners_of(whole), np.repeat(np.arange(6), 2)
    holed = edited(whole, corners[1:], ids[1:])  # one triangle of face 0 gone
    found = check(holed)
    assert codes(found) == {"geometry.free_edges": 3}
    assert found.worst == "warning" and not found.stats["closed"] and found.stats["volume"] is None
    assert found.findings[0].suggestion


def test_non_manifold_and_flipped_faces() -> None:
    whole = cube(1.0)
    corners, ids = corners_of(whole), list(np.repeat(np.arange(6), 2))
    fin = np.array([[[0.0, 0.0, 0.0], [0.0, 0.0, 1.0], [-1.0, 0.0, 0.5]]])  # on a cube edge
    found = check(edited(whole, np.concatenate([corners, fin]), [*ids, 6]))
    assert codes(found)["geometry.non_manifold_edges"] == 1
    assert codes(found)["geometry.free_edges"] == 2
    flipped = corners.copy()
    flipped[0:2] = flipped[0:2, ::-1]  # face 0 wound the wrong way round
    assert codes(check(edited(whole, flipped, ids))) == {"geometry.inconsistent_orientation": 4}


def test_an_inside_out_cube_is_inverted() -> None:
    found = check(cube(1.0, inward=True))
    assert codes(found) == {"geometry.inverted": 1}
    assert found.stats["closed"] and found.stats["volume"] == pytest.approx(-1.0)


def test_degenerate_slivers_and_untriangulated_faces() -> None:
    whole = cube(1.0)
    corners, ids = corners_of(whole), list(np.repeat(np.arange(6), 2))
    point = np.array([[[3.0, 3.0, 3.0], [3.0, 3.0, 3.0], [4.0, 3.0, 3.0]]])  # two corners in one
    found = check(edited(whole, np.concatenate([corners, point]), [*ids, 6]))
    assert codes(found) == {"geometry.degenerate_triangles": 1}
    needle = np.array([[[5.0, 0.0, 0.0], [6.0, 0.0, 0.0], [5.5, 1e-5, 0.0]]])
    found = check(edited(whole, np.concatenate([corners, needle]), [*ids, 6]))
    assert codes(found)["geometry.sliver_triangles"] == 1
    assert (
        next(f for f in found.findings if f.code == "geometry.sliver_triangles").severity == "info"
    )
    declared = mesh_from_corners(corners, ids, faces=8)  # faces 6 and 7 have no triangles
    assert codes(check(declared)) == {"geometry.untriangulated_faces": 2}


def test_empty_non_finite_and_implausible_parts() -> None:
    empty = mesh_from_corners(np.zeros((0, 3, 3)), [], faces=1)
    assert codes(check(empty)) == {"geometry.empty": 1} and check(empty).worst == "error"
    broken = corners_of(cube(1.0))
    broken[0, 0, 0] = np.nan
    assert "geometry.non_finite" in codes(
        check(edited(cube(1.0), broken, np.repeat(np.arange(6), 2)))
    )
    assert codes(check(cube(1e-7))) == {"geometry.size": 1}
    assert codes(check(cube(5e4))) == {"geometry.size": 1}


def test_parts_are_checked_apart_in_one_batch() -> None:
    whole = cube(1.0)
    holed = edited(whole, corners_of(whole)[1:], np.repeat(np.arange(6), 2)[1:])
    first, second, third = check_meshes(
        [PartMesh((whole,)), PartMesh((holed,)), PartMesh((cube(1.0, (1.0, 0.0, 0.0)),))]
    )
    assert first.findings == () and third.findings == ()  # the touching cube does not weld to it
    assert codes(second) == {"geometry.free_edges": 3}


def test_the_command_reports_and_keeps_a_summary(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    clean = add_part(session, "clean", part(cube(0.01, name="clean")))
    whole = cube(0.01, (0.02, 0.0, 0.0), name="holed")
    holed = add_part(
        session,
        "holed",
        part(edited(whole, corners_of(whole)[1:], np.repeat(np.arange(6), 2)[1:])),
    )
    waiting = add_part(session, "waiting", None)
    seen = events_of(session)
    single = session.execute("geometry.check", part="/geometry/parts/clean")
    assert single["checked"] and single["worst"] == "ok" and single["findings"] == []
    assert single["brep"]["checked"] is False and single["stats"]["closed"]
    summary = session.execute("geometry.check", part="/geometry/parts")
    assert summary["checked"] == 2 and summary["unchecked"] == 1 and summary["worst"] == "warning"
    assert summary["with_findings"] == 1
    by_id = {item["part"]: item for item in summary["parts"]}
    assert by_id[waiting] == {
        "part": waiting,
        "path": "/geometry/parts/waiting",
        "name": "waiting",
        "checked": False,
        "reason": "no mesh yet",
    }
    assert by_id[holed]["findings"][0]["code"] == "geometry.free_edges"
    meta = session.execute("node.get", node=holed)["meta"]["check"]
    assert meta["worst"] == "warning" and meta["counts"] == {"geometry.free_edges": 3}
    assert session.execute("node.get", node=clean)["meta"]["check"]["worst"] == "ok"
    assert [d["node_id"] for e, d in seen if e == "node.changed"] == [clean, clean, holed]
    # geometry.check now reflects its result on the status dot: the holed part warns, the clean one
    # goes back to ok (ADR-0020 decision 6).
    assert session.execute("node.get", node=holed)["status"] == "warning"
    assert session.execute("node.get", node=clean)["status"] == "ok"


def test_the_check_leaves_a_stale_part_stale(tmp_path: Path) -> None:
    """A check reports on a stale part but does not hide that it needs a re-import."""
    session = new_session(tmp_path)
    clean = add_part(session, "clean", part(cube(0.01, name="clean")))
    session.project.set_status(clean, "stale")
    assert session.execute("geometry.check", part=clean)["worst"] == "ok"
    assert session.execute("node.get", node=clean)["status"] == "stale"


def test_the_ocp_fixtures_are_closed() -> None:
    pytest.importorskip("OCP")
    from tests import geometry_fixtures as fx
    from tests.selection_support import split_cylinder, tessellated

    meshes = [
        PartMesh((tessellated(shape, quality),))
        for shape in (
            fx.box(10.0, 20.0, 30.0),
            fx.cylinder(5.0, 40.0),
            split_cylinder(5.0, 40.0, 3),
        )
        for quality in ("coarse", "fine")
    ]
    for found in check_meshes(meshes):
        assert found.worst == "ok", found.findings
        assert found.stats["closed"] and found.stats["volume"] > 0.0
