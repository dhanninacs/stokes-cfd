"""Pulled sketches mesh to clean faces (ADR-0036 decision 3 and its addendum of 2026-11-14).

A rectangle drawn in the sketcher, pulled, once came out of the mesher with ridges, sawtooth edges
and 36 face patches, because its field took the ``sqrt`` of a squared distance that is zero on the
boundary: dual contouring samples the gradient there and got 0/0. The field is now planes joined
with ``max`` and ``min``. These tests run the drawn-sketch path (the operation's recipe, then the
geometry worker's display meshing at its coarse and fine cells) and check the faces the app shows:
a box is six flat faces with every vertex on them, an L eight, a pulled circle three.

The meshing tests need the real ``stokes-implicit-mesh`` (the reference mesher rounds every sharp
edge to a cell); the field tests run everywhere.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import stokes.model  # noqa: F401 - the registry, before the geometry model
from stokes.geometry.operations.implicit import body_of
from stokes.geometry.worker_main import (
    COARSE_DISPLAY_CELLS,
    DISPLAY_CELLS,
    FINEST_DISPLAY_CELLS,
    implicit_faces,
)
from stokes.implicit import graph, runner
from stokes.implicit.bodymesh import BodyMesh, mesh_body
from stokes.implicit.errors import MesherMissing
from stokes.implicit.evaluate import evaluate
from stokes.implicit.features import Body
from stokes.implicit.graph import X, Y
from stokes.implicit.profile2d import Point, curves_of, custom_profile, loops_of
from tests.test_implicit_recipes import recipe

try:
    PROGRAM: Path | None = runner.find_mesher()
except MesherMissing:
    PROGRAM = None
needs_mesher = pytest.mark.skipif(
    PROGRAM is None or PROGRAM.suffix == ".py", reason="stokes-implicit-mesh is not built"
)

MM = 1000.0
ON_FACE = 1e-4
"""Millimetres a vertex may lie off its face: float32 positions near 60 mm are 4e-6 mm apart."""
ROUND_FACE = 0.5
"""Share of a cell a vertex may lie off a round face: a vertex on a round sharp edge (a cap's rim)
sits where the tangent planes meet, outside the circle."""
FLAT_DEG = 0.5
"""The viewport's test for a planar face (``MEASURE_PLANAR_COS`` in web/src/viewport/scene.ts)."""
PASSES = {
    "coarse": (COARSE_DISPLAY_CELLS, COARSE_DISPLAY_CELLS),
    "fine": (DISPLAY_CELLS, FINEST_DISPLAY_CELLS),
}


def lines(*corners_mm: tuple[float, float]) -> list[dict[str, Any]]:  # Any: JSON
    """A closed polyline as the sketcher writes it: line entities, coordinates in metres."""
    points = [[u / MM, v / MM] for u, v in corners_mm]
    return [
        {"kind": "line", "a": a, "b": b}
        for a, b in zip(points, points[1:] + points[:1], strict=True)
    ]


def drawn(entities: list[dict[str, Any]], depth_mm: float) -> Body:
    return body_of(
        recipe("implicit_sketch", profile="custom", entities=entities, depth=depth_mm / MM)
    )


def mesh(body: Body, tmp_path: Path, which: str) -> tuple[BodyMesh, np.ndarray, np.ndarray]:
    """The display mesh of one pass, in millimetres, and its unit triangle normals."""
    cells, finest = PASSES[which]
    made = mesh_body(body, tmp_path / which, cells=cells, finest=finest)
    assert made.outcome.stats["open_edges"] == 0 and made.outcome.stats["nonmanifold_edges"] == 0
    positions = made.positions * MM
    t = made.triangles.astype(np.int64)
    normal = np.cross(
        positions[t[:, 1]] - positions[t[:, 0]], positions[t[:, 2]] - positions[t[:, 0]]
    )
    normal /= np.linalg.norm(normal, axis=1, keepdims=True)
    return made, positions, normal


def faces_of(made: BodyMesh) -> np.ndarray:
    """The face of every triangle as the geometry worker numbers them."""
    return implicit_faces(made.positions.astype(np.float32), made.triangles, made.cube.cell / MM)


def solid(made: BodyMesh, positions: np.ndarray) -> np.ndarray:
    """Triangles with an area: dual contouring leaves a few needles whose three vertices are all on
    the surface but on one line, and whose normal means nothing (the mesher's fold rule skips them
    alike)."""
    t = made.triangles.astype(np.int64)
    area = np.linalg.norm(
        np.cross(positions[t[:, 1]] - positions[t[:, 0]], positions[t[:, 2]] - positions[t[:, 0]]),
        axis=1,
    )
    return np.asarray(area > 1e-6 * np.median(area))


def assert_prism(
    made: BodyMesh, positions: np.ndarray, normal: np.ndarray, planes: list[tuple[Any, float]]
) -> None:
    """The worker's faces are exactly ``planes`` ((outward unit normal, offset): n . p = offset):
    every triangle lies on its face's plane and faces out of it within the viewport's flatness
    test, and every vertex is on a plane."""
    faces = faces_of(made)
    assert int(faces.max()) + 1 == len(planes)
    n = np.asarray([p[0] for p in planes], dtype=np.float64)
    offset = np.asarray([p[1] for p in planes])
    t = made.triangles.astype(np.int64)
    centroid = positions[t].mean(axis=1)
    plane_of = np.argmin(np.abs(centroid @ n.T - offset), axis=1)
    live = solid(made, positions)
    agree = np.einsum("ij,ij->i", normal, n[plane_of])[live]
    assert agree.min() >= math.cos(math.radians(FLAT_DEG)), "a triangle leans off its face"
    for face in range(len(planes)):
        assert len(set(plane_of[faces == face].tolist())) == 1, "a face spans two planes"
    assert len(set(plane_of.tolist())) == len(planes), "two faces on one plane"
    on_some = np.abs(positions @ n.T - offset).min(axis=1)
    assert on_some.max() <= ON_FACE, f"a vertex is {on_some.max():.3g} mm off every face"


def box_planes(low: tuple[float, ...], high: tuple[float, ...]) -> list[tuple[Any, float]]:
    planes: list[tuple[Any, float]] = []
    for axis in range(3):
        unit = np.eye(3)[axis]
        planes += [(-unit, -low[axis]), (unit, high[axis])]
    return planes


# ------------------------------------------------------------------------------- the field
def test_a_drawn_polygon_is_planes_with_no_square_root_or_angle() -> None:
    """ADR-0035 decision 5: no ``sqrt`` of a quantity that is zero on the surface."""
    l_shape = lines((0, 0), (40, 0), (40, 10), (10, 10), (10, 30), (0, 30))
    for entities in (lines((0, 0), (60, 0), (60, 20), (0, 20)), l_shape):
        root = custom_profile(entities, MM).field(X, Y)
        ops = {node.op for node in graph.walk(root)}
        assert not ops & {"sqrt", "atan2"}, ops


def test_a_drawn_circle_is_the_exact_circle() -> None:
    shape = custom_profile([{"kind": "circle", "c": [0.01, 0.0], "r": 0.005}], MM)
    assert shape.metric == "exact"
    points = [
        (10.0, 0.0, 0.0),
        (17.0, 0.0, 0.0),
        (10.0, 5.0, 0.0),
        (10.0 + 5 / math.sqrt(2), -5 / math.sqrt(2), 0.0),
    ]
    assert evaluate(shape.field(X, Y), points) == pytest.approx([-5.0, 2.0, 0.0, 0.0], abs=1e-12)


@pytest.mark.parametrize(
    "corners",
    [
        [(0, 0), (40, 0), (40, 10), (10, 10), (10, 30), (0, 30)],  # an L
        [(0, 0), (20, 0), (20, 20), (10, 10), (0, 20)],  # a notch on the diagonal
        [(0, 0), (30, 0), (30, 30), (20, 30), (20, 10), (10, 10), (10, 30), (0, 30)],  # a U
        [(0, 0), (10, 5), (20, 0), (15, 10), (20, 20), (10, 15), (0, 20), (5, 10)],  # a star
    ],
)
def test_convex_pieces_cover_the_polygon_and_overlap_across_their_seams(
    corners: list[tuple[float, float]],
) -> None:
    """The pieces are convex, their areas sum to the polygon's, and the field is negative on every
    diagonal between two pieces, so no point inside the material is a zero of the field."""
    from stokes.implicit.profile2d import _cleaned, _convex_pieces

    loop = _cleaned(loops_of(curves_of(lines(*corners), MM))[0])
    pieces = _convex_pieces(loop)

    def area(points: list[tuple[float, float]]) -> float:
        return 0.5 * sum(
            p[0] * q[1] - q[0] * p[1] for p, q in zip(points, points[1:] + points[:1], strict=True)
        )

    total = 0.0
    for piece in pieces:
        points = [loop[i] for i in piece]
        for i in range(len(points)):
            a, b, c = points[i - 1], points[i], points[(i + 1) % len(points)]
            assert (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) >= -1e-9
        total += area(points)
    assert total == pytest.approx(area(loop), rel=1e-12)
    root = custom_profile(lines(*corners), MM).field(X, Y)
    n = len(loop)
    seams = [
        (loop[a], loop[b])
        for piece in pieces
        for a, b in zip(piece, piece[1:] + piece[:1], strict=True)
        if (b - a) % n != 1
    ]
    assert len(seams) == 2 * (len(pieces) - 1)
    for (au, av), (bu, bv) in seams:
        s = np.linspace(0.02, 0.98, 49)
        along = np.column_stack([au + s * (bu - au), av + s * (bv - av), np.zeros_like(s)])
        assert evaluate(root, along).max() < 0.0


# ------------------------------------------------------------------------------- the mesher
@needs_mesher
@pytest.mark.parametrize("which", ["coarse", "fine"])
@pytest.mark.parametrize("size", [(60.0, 20.0, 25.0), (40.0, 20.0, 20.0)])
def test_a_drawn_rectangle_pulls_to_a_six_faced_box(
    tmp_path: Path, which: str, size: tuple[float, float, float]
) -> None:
    w, h, d = size
    body = drawn(lines((0, 0), (w, 0), (w, h), (0, h)), d)
    made, positions, normal = mesh(body, tmp_path, which)
    assert_prism(made, positions, normal, box_planes((0, 0, 0), (w, h, d)))


@needs_mesher
@pytest.mark.parametrize("which", ["coarse", "fine"])
def test_a_named_rectangle_still_pulls_to_a_six_faced_box(tmp_path: Path, which: str) -> None:
    body = body_of(
        recipe("implicit_sketch", profile="rectangle", width=0.06, height=0.02, depth=0.025)
    )
    made, positions, normal = mesh(body, tmp_path, which)
    assert_prism(made, positions, normal, box_planes((-30, -10, 0), (30, 10, 25)))


@needs_mesher
@pytest.mark.parametrize("which", ["coarse", "fine"])
def test_a_drawn_l_pulls_to_eight_flat_faces(tmp_path: Path, which: str) -> None:
    body = drawn(lines((0, 0), (40, 0), (40, 10), (10, 10), (10, 30), (0, 30)), 20.0)
    made, positions, normal = mesh(body, tmp_path, which)
    x, y, z = np.eye(3)
    planes = [
        (-y, 0.0),
        (x, 40.0),
        (y, 10.0),
        (x, 10.0),
        (y, 30.0),
        (-x, 0.0),
        (-z, 0.0),
        (z, 20.0),
    ]
    assert_prism(made, positions, normal, planes)
    # The reflex edge is sharp and straight: vertices sit on both of its planes at once (and,
    # by the check above, none rounds it off).
    on_edge = (np.abs(positions[:, 0] - 10.0) <= ON_FACE) & (
        np.abs(positions[:, 1] - 10.0) <= ON_FACE
    )
    assert on_edge.sum() >= 2


@needs_mesher
@pytest.mark.parametrize("which", ["coarse", "fine"])
def test_a_drawn_circle_pulls_to_a_three_faced_cylinder(tmp_path: Path, which: str) -> None:
    """Two flat caps and one round side, every vertex on them; the same mesh as the named circle."""
    r, d = 10.0, 25.0
    body = drawn([{"kind": "circle", "c": [0.0, 0.0], "r": r / MM}], d)
    made, positions, normal = mesh(body, tmp_path, which)
    faces = faces_of(made)
    assert int(faces.max()) + 1 == 3
    t = made.triangles.astype(np.int64)
    area = np.linalg.norm(
        np.cross(positions[t[:, 1]] - positions[t[:, 0]], positions[t[:, 2]] - positions[t[:, 0]]),
        axis=1,
    )
    flat = np.abs(normal[:, 2]) >= math.cos(math.radians(FLAT_DEG))
    caps = 0
    for face in range(3):
        mine = faces == face
        share = area[mine & flat].sum() / area[mine].sum()
        if share > 0.5:  # a cap: flat but for the odd sliver dual contouring leaves on its rim
            caps += 1
            assert share >= 0.999, share
        else:  # the side: vertical but for the same slivers
            upright = np.abs(normal[:, 2]) <= 0.05
            assert area[mine & upright].sum() / area[mine].sum() >= 0.99
    assert caps == 2
    radial = np.hypot(positions[:, 0], positions[:, 1])
    on_cap = np.minimum(np.abs(positions[:, 2]), np.abs(positions[:, 2] - d)) <= ON_FACE
    off_side = np.abs(radial - r)[~on_cap]
    assert off_side.max() <= ROUND_FACE * made.cube.cell
    assert float(np.median(off_side)) <= 0.01 * made.cube.cell
    named = body_of(recipe("implicit_sketch", profile="circle", radius=r / MM, depth=d / MM))
    same, _, _ = mesh(named, tmp_path / "named", which)
    assert np.array_equal(same.positions, made.positions)
    assert np.array_equal(same.triangles, made.triangles)


@needs_mesher
def test_a_ball_joined_to_a_block_leaves_the_blocks_faces_apart(tmp_path: Path) -> None:
    """A curved surface that crosses flat faces at a shallow angle must not join them into one
    face. A 20 x 10 x 10 mm block with a 6 mm ball on its end once came out with its +y, -y, +z
    and -z faces and the ball as a single face: the feature-angle split chains through the ball,
    which meets each of them at 34 degrees. The block's edges then shaded serrated, and no
    boundary could name one of those faces (walkthrough of 2026-11-17)."""
    block = recipe(
        "implicit_sketch",
        profile="custom",
        entities=lines((-10, -5), (10, -5), (10, 5), (-10, 5)),
        depth=0.010,
    )
    ball = recipe("implicit_primitive", kind="sphere", radius=0.006, position=[0.010, 0, 0.005])
    body = body_of(recipe("implicit_boolean", [block], [ball], op="union", blend_radius=0.0))
    for which in PASSES:
        made, positions, normal = mesh(body, tmp_path, which)
        faces = faces_of(made)
        live = solid(made, positions)
        seen: set[int] = set()
        for axis, sign in ((0, -1.0), (1, 1.0), (1, -1.0), (2, 1.0), (2, -1.0)):
            on = live & (sign * normal[:, axis] > 0.9999)
            ids, counts = np.unique(faces[on], return_counts=True)
            main = int(ids[np.argmax(counts)])
            assert counts.max() >= 0.9 * counts.sum(), f"{which}: a flat face is in pieces"
            assert main not in seen, f"{which}: two of the block's faces are one face"
            seen.add(main)
            mine = (faces == main) & live
            # The face is flat: no ball triangle shares it (a bevel at the junction may, a little).
            assert (sign * normal[mine, axis]).min() > math.cos(math.radians(30.0)), which


# ------------------------------------------------------------------------------- drawn arcs
def arc(a: Point, m: Point, b: Point) -> dict[str, Any]:  # Any: JSON
    """A three-point arc as the sketcher writes it: corners in millimetres, written in metres."""
    return {
        "kind": "arc",
        "a": [a[0] / MM, a[1] / MM],
        "m": [m[0] / MM, m[1] / MM],
        "b": [b[0] / MM, b[1] / MM],
    }


def edge(a: Point, b: Point) -> dict[str, Any]:  # Any: JSON
    """A line as the sketcher writes it: corners in millimetres, written in metres."""
    return {"kind": "line", "a": [a[0] / MM, a[1] / MM], "b": [b[0] / MM, b[1] / MM]}


def circle_points(radius: float, angles_deg: list[float]) -> np.ndarray:
    """Points (u, v, 0) of a circle of ``radius`` about the origin, at the given angles."""
    t = np.radians(np.asarray(angles_deg, dtype=np.float64))
    return np.column_stack([radius * np.cos(t), radius * np.sin(t), np.zeros_like(t)])


def test_a_drawn_arc_is_the_exact_circular_segment() -> None:
    """A half disc: an arc through (0, 10) closed by its chord. The field is zero on the circle
    (no chord error) and its graph has no angle test."""
    entities = [arc((-10, 0), (0, 10), (10, 0)), edge((10, 0), (-10, 0))]
    root = custom_profile(entities, MM).field(X, Y)
    assert not {node.op for node in graph.walk(root)} & {"atan2"}
    angles = [float(a) for a in np.linspace(0.0, 180.0, 361) if math.sin(math.radians(a)) > 0.05]
    assert np.abs(evaluate(root, circle_points(10.0, angles))).max() <= 1e-9
    values = evaluate(root, np.array([(0, 5, 0), (0, 11, 0), (0, -1, 0)], dtype=np.float64))
    assert values[0] < 0 < values[1]
    assert values[2] > 0


def test_a_drawn_concave_arc_removes_its_segment() -> None:
    """A 30 x 20 mm rectangle, counter-clockwise, whose top edge dips into it to 5 mm: the segment
    between the chord and the arc is taken away, so a point just under the chord is outside."""
    entities = [
        edge((-15, -10), (15, -10)),
        edge((15, -10), (15, 10)),
        arc((15, 10), (0, 5), (-15, 10)),
        edge((-15, 10), (-15, -10)),
    ]
    root = custom_profile(entities, MM).field(X, Y)
    above_chord, inside = evaluate(root, np.array([(0, 7, 0), (0, 3, 0)], dtype=np.float64))
    assert above_chord > 0
    assert inside < 0


def test_a_drawn_major_arc_keeps_the_disc_less_its_cap() -> None:
    """A 270 degree arc (from (10, 0) through 135 degrees to (0, -10)) closed by its chord: the
    circle is on the surface along the major arc, the centre is inside and the minor cap is not."""
    entities = [
        arc((10, 0), (-10 / math.sqrt(2), 10 / math.sqrt(2)), (0, -10)),
        edge((0, -10), (10, 0)),
    ]
    root = custom_profile(entities, MM).field(X, Y)
    assert np.abs(evaluate(root, circle_points(10.0, [45.0, 135.0, 225.0, 269.0]))).max() <= 1e-9
    values = evaluate(root, np.array([(0, 0, 0), (7.0710678, -7.0710678, 0)], dtype=np.float64))
    assert values[0] < 0 < values[1]


@needs_mesher
@pytest.mark.parametrize("which", ["coarse", "fine"])
def test_a_drawn_arc_pulls_to_a_four_faced_cylinder_half(tmp_path: Path, which: str) -> None:
    """The half disc of the field test, pulled 10 mm: four faces (the round side, the flat chord,
    two caps), and the round side's vertices on its circle."""
    entities = [arc((-10, 0), (0, 10), (10, 0)), edge((10, 0), (-10, 0))]
    made, positions, normal = mesh(drawn(entities, 10.0), tmp_path, which)
    faces = faces_of(made)
    live = solid(made, positions)
    triangles = np.array([int(((faces == f) & live).sum()) for f in np.unique(faces)])
    big = triangles > 0.01 * triangles.sum()
    assert int(big.sum()) == 4, f"{which}: face sizes {triangles.tolist()}"
    # The coarse pass leaves a two-triangle sliver where the arc meets its chord (a corner, not a
    # face); it is the only other face and is kept tiny.
    assert int(triangles[~big].sum()) <= 4, f"{which}: face sizes {triangles.tolist()}"
    t = made.triangles.astype(np.int64)
    round_side = (np.abs(normal[:, 2]) < 0.1) & (normal[:, 1] > 0.0) & live
    used = np.unique(t[round_side].ravel())
    radial = np.hypot(positions[used, 0], positions[used, 1])
    deviation = np.abs(radial - 10.0)
    # The circle's tolerances (a drawn circle pulls with the same field and the same mesher): a
    # vertex on a rim sits where the tangent planes meet, so the maximum is half a cell, and the
    # median is a hundredth. The absolute values are reported by the failure messages.
    cell = made.cube.cell
    assert deviation.max() <= ROUND_FACE * cell, f"{which}: {deviation.max():.3g} mm off the circle"
    assert float(np.median(deviation)) <= 0.01 * cell, f"{which}: median {np.median(deviation):.3g}"
