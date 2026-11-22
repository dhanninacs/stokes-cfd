"""The real ``stokes-implicit-mesh`` (ADR-0035 decisions 14, 18 and 19), run when it is built.

Skipped where the program is not built (it needs a Rust toolchain); the ``implicit mesher`` CI job
builds it and runs these with ``STOKES_IMPLICIT_MESHER`` pointing at it.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from stokes.implicit import features as F  # noqa: N812 - the builders read as F.box, F.sphere
from stokes.implicit import graph, runner
from stokes.implicit.errors import MesherMissing
from stokes.implicit.frame import translation

try:
    PROGRAM = runner.find_mesher()
except MesherMissing:
    PROGRAM = None
pytestmark = pytest.mark.skipif(
    PROGRAM is None or PROGRAM.suffix == ".py", reason="stokes-implicit-mesh is not built"
)


def _request(body: F.Body, cell: float, threads: int = 0) -> runner.MeshRequest:
    low, high = body.bounded()
    centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
    root = body.field(translation(tuple(-c for c in centre)))  # type: ignore[arg-type]
    local = (
        tuple(a - c for a, c in zip(low, centre, strict=True)),
        tuple(b - c for b, c in zip(high, centre, strict=True)),
    )
    cube = runner.cube_for(local, cell)  # type: ignore[arg-type]
    return runner.MeshRequest(graph.to_text(root), cube, threads=threads)


def _volume(positions: np.ndarray, triangles: np.ndarray) -> float:
    p = positions.astype(np.float64)
    t = triangles.astype(np.int64)
    return float(np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum() / 6)


def _folds(positions: np.ndarray, triangles: np.ndarray) -> int:
    """Edges whose two triangles' normals are more than 170 degrees apart (decision 18)."""
    p = positions.astype(np.float64)
    t = triangles.astype(np.int64)
    normal = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
    area = np.linalg.norm(normal, axis=1)
    edges = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    owner = np.tile(np.arange(len(t)), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    same = (edges[1:] == edges[:-1]).all(axis=1)
    a, b = owner[:-1][same], owner[1:][same]
    # As the mesher: a triangle far below the others in area (a needle between two vertices the
    # weld pulled apart) has no reliable normal and makes no fold.
    floor = 2e-6 * float(np.median(area))
    live = (area[a] > floor) & (area[b] > floor)
    cos = np.einsum("ij,ij->i", normal[a], normal[b]) / (area[a] * area[b] + 1e-300)
    return int((live & (cos < math.cos(math.radians(170.0)))).sum())


def test_a_sphere_is_closed_with_its_volume(tmp_path: Path) -> None:
    outcome = runner.mesh(_request(F.sphere(10.0, (0.0, 0.0, 0.0)), 0.1), tmp_path)
    stats = outcome.stats
    assert stats["open_edges"] == 0 and stats["nonmanifold_edges"] == 0
    # Fidget's dual contouring folds about a tenth of a sphere's triangles; the mesher's fold pass
    # removes every one (decision 18), and its count agrees with an independent one.
    assert stats["folds_raw"] > 1000
    assert stats["folds"] == 0 == _folds(outcome.mesh.positions, outcome.mesh.triangles)
    volume = _volume(outcome.mesh.positions, outcome.mesh.triangles)
    assert volume == pytest.approx(4 / 3 * math.pi * 1000.0, rel=0.005)


def test_the_same_input_gives_the_same_bytes_at_any_thread_count(tmp_path: Path) -> None:
    body = F.difference(
        F.box((20.0, 20.0, 20.0), (0.0, 0.0, 0.0)), [F.cylinder(4.0, 30.0, (3.0, 0.0, 0.0), "z")]
    )
    one = runner.mesh(_request(body, 0.25, threads=1), tmp_path / "one").mesh
    many = runner.mesh(_request(body, 0.25, threads=8), tmp_path / "many").mesh
    assert np.array_equal(one.positions, many.positions)
    assert np.array_equal(one.triangles, many.triangles)


def test_two_touching_boxes_are_one_closed_shell(tmp_path: Path) -> None:
    joined = F.union(
        [F.box((10.0, 10.0, 10.0), (0.0, 0.0, 0.0)), F.box((10.0, 10.0, 10.0), (10.0, 0.0, 0.0))]
    )
    for cell in (0.1, 0.125, 0.2, 0.25, 0.3125, 0.4, 0.5, 0.625, 0.8, 1.0):
        outcome = runner.mesh(_request(joined, cell), tmp_path)
        assert outcome.stats["open_edges"] == 0 and outcome.stats["nonmanifold_edges"] == 0, cell
        volume = _volume(outcome.mesh.positions, outcome.mesh.triangles)
        assert volume == pytest.approx(2000.0, rel=0.005), cell


@pytest.mark.parametrize("cells", [48, 96, 192, 384])
def test_curved_and_sharp_bodies_come_out_without_folds(tmp_path: Path, cells: int) -> None:
    """Decision 18 over bodies with curved faces, sharp edges and cells of different sizes."""
    from stokes.implicit import frame as fr

    bodies = [
        F.torus(8.0, 2.5, (0.0, 0.0, 0.0), "x"),
        F.cone(6.0, 12.0, 1.5, (0.0, 0.0, 0.0), "z"),
        F.transformed(F.box((12.0, 8.0, 5.0), (0.0, 0.0, 0.0)), fr.rotation((1.0, 1.0, 0.3), 0.7)),
        F.difference(
            F.rounded_box((16.0, 16.0, 16.0), (0.0, 0.0, 0.0), 2.0),
            [F.cylinder(3.0, 20.0, (0.0, 0.0, 0.0), a) for a in "xyz"],
        ),
    ]
    for body in bodies:
        low, high = body.bounded()
        extent = max(b - a for a, b in zip(low, high, strict=True))
        outcome = runner.mesh(_request(body, extent / cells), tmp_path)
        stats = outcome.stats
        assert (stats["open_edges"], stats["nonmanifold_edges"], stats["folds"]) == (0, 0, 0)
        assert _folds(outcome.mesh.positions, outcome.mesh.triangles) == 0


def test_sketched_bodies_have_their_volumes(tmp_path: Path) -> None:
    """A pulled circle is a cylinder; a revolved square is a ring of Pappus's volume."""
    from stokes.implicit import sketch as sk

    sizes = {"width": 2.0, "height": 2.0, "radius": 3.0, "corner_radius": 0.0, "slot_length": 0.0}
    pulled = sk.extrude(sk.named("circle", sides=3, **sizes), "xy", (0.0, 0.0, 0.0), 10.0, False)
    mesh = runner.mesh(_request(pulled, 0.1), tmp_path).mesh
    assert _volume(mesh.positions, mesh.triangles) == pytest.approx(math.pi * 9 * 10, rel=0.01)
    square = sk.shifted(sk.named("rectangle", sides=3, **sizes), 6.0)
    for angle, share in ((2 * math.pi, 1.0), (math.pi, 0.5)):
        ring = sk.revolve(square, "xy", (0.0, 0.0, 0.0), angle)
        mesh = runner.mesh(_request(ring, 0.1), tmp_path).mesh
        expected = 2 * math.pi * 6.0 * 4.0 * share
        assert _volume(mesh.positions, mesh.triangles) == pytest.approx(expected, rel=0.01)


def test_a_sketched_non_convex_profile_pulls_to_its_volume(tmp_path: Path) -> None:
    """ADR-0036: an L profile with a round hole, drawn as entities, pulled 20 mm."""
    from stokes.implicit import sketch as sk
    from stokes.implicit.profile2d import custom_profile

    def line(a: tuple[float, float], b: tuple[float, float]) -> dict[str, object]:
        return {"kind": "line", "a": list(a), "b": list(b)}

    corners = [(0.0, 0.0), (40.0, 0.0), (40.0, 10.0), (10.0, 10.0), (10.0, 30.0), (0.0, 30.0)]
    entities = [line(p, q) for p, q in zip(corners, corners[1:] + corners[:1], strict=True)]
    entities.append({"kind": "circle", "c": [5.0, 5.0], "r": 3.0})
    body = sk.extrude(custom_profile(entities, 1.0), "xy", (0.0, 0.0, 0.0), 20.0, False)
    outcome = runner.mesh(_request(body, 0.2), tmp_path)
    assert outcome.stats["open_edges"] == 0 and outcome.stats["folds"] == 0
    expected = (40 * 10 + 10 * 20 - math.pi * 9) * 20
    assert _volume(outcome.mesh.positions, outcome.mesh.triangles) == pytest.approx(
        expected, rel=0.01
    )
