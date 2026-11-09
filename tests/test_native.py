from __future__ import annotations

import numpy as np

from stokes import native
from stokes.native import fallback

# Three triangles: A and B coplanar (normal +z), C folded 90 degrees along the edge (0,0,0)-(1,0,0).
POINTS = np.array(
    [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0, 0, 1]],
    dtype=np.float64,
)
TRIANGLES = np.array([[0, 1, 2], [1, 3, 2], [0, 4, 1]], dtype=np.int64)


def test_adjacency_and_normals() -> None:
    normals = fallback.triangle_normals(POINTS, TRIANGLES)
    assert np.allclose(normals[0], [0, 0, 1]) and np.allclose(normals[1], [0, 0, 1])
    assert np.allclose(np.abs(normals[2]), [0, 1, 0])
    offsets, neighbours = fallback.triangle_adjacency(TRIANGLES)
    assert offsets.tolist() == [0, 2, 3, 4]
    assert sorted(neighbours[:2].tolist()) == [1, 2]


def test_grow_by_angle_and_components() -> None:
    normals = fallback.triangle_normals(POINTS, TRIANGLES)
    offsets, neighbours = fallback.triangle_adjacency(TRIANGLES)
    seeds = np.array([0], dtype=np.int64)
    assert fallback.grow_by_angle(normals, offsets, neighbours, seeds, 30.0).tolist() == [0, 1]
    assert fallback.grow_by_angle(normals, offsets, neighbours, seeds, 95.0).tolist() == [0, 1, 2]
    assert fallback.connected_components(offsets, neighbours).tolist() == [0, 0, 0]
    via_loader = native.grow_by_angle(normals, offsets, neighbours, seeds, 30.0)
    assert via_loader.tolist() == [0, 1]
    assert isinstance(native.using_fallback(), bool)
