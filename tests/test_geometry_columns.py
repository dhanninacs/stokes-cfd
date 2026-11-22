"""`Columns`: which of many points are inside a closed surface (ADR-0042)."""

from __future__ import annotations

from typing import Any

import numpy as np

from stokes.geometry.inside import Columns, is_inside

Mesh = tuple[Any, Any]  # Any: numpy arrays of vertices and of triangles


def _box(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> Mesh:
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = np.array(
        [
            [x0, y0, z0],
            [x1, y0, z0],
            [x1, y1, z0],
            [x0, y1, z0],
            [x0, y0, z1],
            [x1, y0, z1],
            [x1, y1, z1],
            [x0, y1, z1],
        ],
        dtype=float,
    )
    f = np.array(
        [
            [0, 2, 1],
            [0, 3, 2],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [2, 3, 7],
            [2, 7, 6],
            [1, 2, 6],
            [1, 6, 5],
            [0, 4, 7],
            [0, 7, 3],
        ]
    )
    return v, f


def _sphere(n: int = 24) -> Mesh:
    theta = np.linspace(0.0, np.pi, n + 1)
    phi = np.linspace(0.0, 2 * np.pi, 2 * n, endpoint=False)
    v = [(np.sin(t) * np.cos(p), np.sin(t) * np.sin(p), np.cos(t)) for t in theta for p in phi]
    f = []
    for i in range(n):
        for j in range(2 * n):
            a, b = i * 2 * n + j, i * 2 * n + (j + 1) % (2 * n)
            c, d = a + 2 * n, b + 2 * n
            f += [(a, c, b), (b, c, d)]
    return np.array(v), np.array(f)


def test_points_in_and_out_of_a_box() -> None:
    v, f = _box((0.0, 0.0, 0.0), (2.0, 1.0, 0.5))
    points = np.array(
        [[1.0, 0.5, 0.25], [1.9, 0.9, 0.45], [2.1, 0.5, 0.25], [1.0, 0.5, 0.6], [-1, -1, -1]]
    )
    assert Columns(v, f, 0.01).inside(points).tolist() == [True, True, False, False, False]


def test_it_agrees_with_the_one_point_test_on_a_sphere() -> None:
    v, f = _sphere()
    rng = np.random.default_rng(7)
    points = rng.uniform(-1.2, 1.2, size=(400, 3))
    radius = np.linalg.norm(points, axis=1)
    points = points[np.abs(radius - 1.0) > 0.05]  # away from the faceted surface itself
    tris = [tuple(tuple(float(x) for x in v[i]) for i in row) for row in f]
    expected = [is_inside(tris, tuple(float(x) for x in p)) for p in points]  # type: ignore[arg-type]
    found = Columns(v, f, 0.01).inside(points).tolist()
    assert found == expected
    assert found == (np.linalg.norm(points, axis=1) < 1.0).tolist()


def test_wide_triangles_are_tested_against_every_point() -> None:
    # A long thin box: every triangle of its long sides reaches across the whole grid.
    v, f = _box((0.0, 0.0, 0.0), (10.0, 0.01, 0.01))
    points = np.array([[5.0, 0.005, 0.004], [5.0, 0.02, 0.005], [9.99, 0.008, 0.002]])
    assert Columns(v, f, 0.0005).inside(points).tolist() == [True, False, True]


def test_no_points_and_no_triangles() -> None:
    v, f = _box((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert Columns(v, f, 0.1).inside(np.empty((0, 3))).tolist() == []
    none = np.empty((0, 3), dtype=int)
    assert Columns(v, none, 0.1).inside(np.array([[0.5, 0.5, 0.5]])).tolist() == [False]
