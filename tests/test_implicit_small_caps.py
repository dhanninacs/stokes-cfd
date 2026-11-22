"""Small flat caps stay faces of their own (ADR-0040, the face grouping of implicit parts).

The geometry worker names the faces of an implicit display mesh by the regions between feature
edges, with the narrow triangles dual contouring leaves along a sharp edge merged into a face
(:func:`stokes.geometry.worker_main.implicit_faces`). Counting triangles alone once merged a 10 mm
port cap that the simplifier had left as four triangles into its neighbour, so no tag could name
the outlet. The synthetic tests run everywhere; the meshing tests need ``stokes-implicit-mesh``.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

import stokes.model  # noqa: F401 - the registry, before the geometry model
from stokes.geometry.meshfile import shading_groups
from stokes.geometry.operations.implicit import body_of
from stokes.geometry.worker_main import implicit_faces
from tests.test_implicit_recipes import recipe
from tests.test_implicit_sketch_faces import FLAT_DEG, faces_of, mesh, needs_mesher

PRIMITIVE = "implicit_primitive"


def bar(
    profile: list[tuple[float, float]], length: float, stations: int
) -> tuple[np.ndarray, np.ndarray]:
    """A closed prism along z: ``profile`` (counter-clockwise in x, y) swept over ``length`` in
    ``stations`` segments, each side a strip of quads, each end a fan of the profile."""
    count = len(profile)
    z = np.linspace(0.0, length, stations + 1)
    positions = np.asarray([(x, y, h) for h in z for x, y in profile], dtype=np.float64)
    triangles: list[tuple[int, int, int]] = []
    for i in range(stations):
        for j in range(count):
            a, b = i * count + j, i * count + (j + 1) % count
            c, d = b + count, a + count
            triangles += [(a, b, c), (a, c, d)]
    top = stations * count
    for j in range(1, count - 1):
        triangles.append((0, j + 1, j))  # the end at z = 0 faces -z
        triangles.append((top, top + j, top + j + 1))  # the end at z = length faces +z
    return positions, np.asarray(triangles, dtype=np.int64)


def outward(positions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    t = triangles
    n = np.cross(positions[t[:, 1]] - positions[t[:, 0]], positions[t[:, 2]] - positions[t[:, 0]])
    return np.asarray(n / np.linalg.norm(n, axis=1, keepdims=True))


SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_the_bar_is_closed_and_faces_out() -> None:
    positions, triangles = bar(SQUARE, 200.0, 20)
    corners = positions[triangles]
    volume = np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum()
    assert volume / 6.0 == pytest.approx(10.0 * 10.0 * 200.0)


def test_a_two_triangle_end_cap_is_a_face_of_its_own() -> None:
    """A 10 mm square tube end at a 3 mm cell: each end is two triangles, the walls 40 each."""
    positions, triangles = bar(SQUARE, 200.0, 20)
    faces = implicit_faces(positions, triangles, cell=3.0)
    assert int(faces.max()) + 1 == 6
    normal = outward(positions, triangles)
    for sign in (1.0, -1.0):
        cap = np.flatnonzero(normal[:, 2] * sign > 0.99)
        assert cap.size == 2
        assert len(set(faces[cap].tolist())) == 1
        assert np.count_nonzero(faces == faces[cap[0]]) == 2
    # Counting triangles alone merges both ends into a wall.
    assert int(shading_groups(positions, triangles).max()) + 1 == 4


def test_a_narrow_strip_along_an_edge_joins_a_face() -> None:
    """A chamfer a hundredth of a cell wide (two triangles) is a sliver, not a face."""
    chamfered = [(0.0, 0.0), (10.0, 0.0), (10.0, 9.99), (9.99, 10.0), (0.0, 10.0)]
    positions, triangles = bar(chamfered, 100.0, 1)
    faces = implicit_faces(positions, triangles, cell=1.0)
    assert int(faces.max()) + 1 == 6
    normal = outward(positions, triangles)
    strip = np.flatnonzero(np.abs(normal[:, 0] - normal[:, 1]) < 1e-6)
    strip = strip[np.abs(normal[strip, 2]) < 0.5]
    assert strip.size == 2
    upright = np.abs(normal[:, 2]) < 0.5
    for one in strip:  # each triangle goes to the wall it matches best
        mates = np.flatnonzero((faces == faces[one]) & upright)
        assert any(abs(normal[m, 0] - normal[m, 1]) > 0.5 for m in mates)


def test_a_wide_chamfer_is_a_face_of_its_own() -> None:
    """Two cells wide, a chamfer is a face however few triangles it has."""
    chamfered = [(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (8.0, 10.0), (0.0, 10.0)]
    positions, triangles = bar(chamfered, 100.0, 1)
    assert int(implicit_faces(positions, triangles, cell=1.0).max()) + 1 == 7


# ------------------------------------------------------------------------------- the mesher
@needs_mesher
@pytest.mark.parametrize("which", ["coarse", "fine"])
def test_a_long_square_tube_keeps_its_small_end_caps(tmp_path: Path, which: str) -> None:
    """A 10 x 10 x 200 mm bar: at the coarse cell (3.1 mm) each end is four triangles."""
    body = body_of(recipe(PRIMITIVE, kind="box", size=[0.01, 0.01, 0.2]))
    made, _, normal = mesh(body, tmp_path, which)
    faces = faces_of(made)
    assert int(faces.max()) + 1 == 6
    flat = math.cos(math.radians(FLAT_DEG))
    for sign in (1.0, -1.0):
        cap = (normal[:, 2] * sign) >= flat
        assert len(set(faces[cap].tolist())) == 1, "an end is split"
        assert np.array_equal(faces == faces[cap][0], cap), "an end shares its face"


@needs_mesher
@pytest.mark.parametrize("which", ["coarse", "fine"])
def test_the_stub_ends_of_a_manifold_are_faces(tmp_path: Path, which: str) -> None:
    """A header with two 10 mm square stubs through it: 26 faces, each of the four stub ends one
    face of its own (four triangles at the coarse cell)."""
    stub = [0.01, 0.01, 0.06]
    body = body_of(
        recipe(
            "implicit_boolean",
            [recipe(PRIMITIVE, kind="box", size=[0.1, 0.03, 0.03])],
            [
                recipe(PRIMITIVE, kind="box", size=stub, position=[0.03, 0.0, 0.0]),
                recipe(PRIMITIVE, kind="box", size=stub, position=[-0.03, 0.0, 0.0]),
            ],
            op="union",
        )
    )
    made, positions, normal = mesh(body, tmp_path, which)
    faces = faces_of(made)
    assert int(faces.max()) + 1 == 26
    t = made.triangles.astype(np.int64)
    centre = positions[t].mean(axis=1)
    flat = math.cos(math.radians(FLAT_DEG))
    for x in (30.0, -30.0):
        for z, sign in ((30.0, 1.0), (-30.0, -1.0)):
            end = (normal[:, 2] * sign >= flat) & (np.abs(centre[:, 2] - z) < 1e-3)
            end &= np.abs(centre[:, 0] - x) < 5.0
            assert end.any()
            assert len(set(faces[end].tolist())) == 1
            assert np.array_equal(faces == faces[end][0], end)
