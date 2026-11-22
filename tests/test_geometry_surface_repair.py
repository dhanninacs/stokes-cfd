"""The staged-surface repair the Gmsh driver runs (ADR-0043 addendum): crossings, folds, pockets.

Synthetic surfaces only: a closed cube, a cube with a triangle pushed through its neighbour, a
detached closed piece beside the body (a pocket) and one inside it facing inwards (an island).
"""

from __future__ import annotations

import itertools

import numpy as np

from stokes.geometry.surface_repair import crossing_triangles, knife_folds, repair

Tri = tuple[tuple[float, float, float], ...]


def cube(
    origin: tuple[float, float, float] = (0, 0, 0), size: float = 1.0, n: int = 4
) -> list[Tri]:
    """A closed cube of side ``size``, each face an n x n grid, normals facing out."""
    out: list[Tri] = []
    ox, oy, oz = origin
    step = size / n
    for axis, side in itertools.product(range(3), (0, 1)):
        u, v = [a for a in range(3) if a != axis]
        for i, j in itertools.product(range(n), range(n)):
            quad = []
            for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1)):
                p = [0.0, 0.0, 0.0]
                p[axis] = side * size
                p[u] = (i + di) * step
                p[v] = (j + dj) * step
                quad.append((p[0] + ox, p[1] + oy, p[2] + oz))
            a, b, c, d = quad
            first, second = (a, b, c), (a, c, d)
            normal = np.cross(np.subtract(b, a), np.subtract(c, a))
            outward = normal[axis] * (1 if side else -1) > 0
            if not outward:
                first, second = (a, c, b), (a, d, c)
            out += [first, second]
    return out


def stl(name: str, triangles: list[Tri]) -> str:
    lines = [f"solid {name}"]
    for triangle in triangles:
        lines += ["  facet normal 0 0 0", "    outer loop"]
        lines += [f"      vertex {x:.12g} {y:.12g} {z:.12g}" for x, y, z in triangle]
        lines += ["    endloop", "  endfacet"]
    lines.append(f"endsolid {name}")
    return "\n".join(lines) + "\n"


def arrays(triangles: list[Tri]) -> tuple[np.ndarray, np.ndarray]:
    corners = np.asarray(triangles, dtype=float).reshape(-1, 3)
    points, inverse = np.unique(corners.round(12), axis=0, return_inverse=True)
    return points, inverse.reshape(-1, 3)


def test_a_clean_closed_surface_is_left_as_it_is() -> None:
    texts = {"walls": stl("walls", cube())}
    out, report = repair(texts)
    assert out == texts
    assert (report.before, report.after, report.moved, report.pockets) == (0, 0, 0, 0)
    assert knife_folds(texts) == 0


def test_a_vertex_pushed_through_the_surface_is_found_and_smoothed_back() -> None:
    triangles = cube(n=4)
    points, faces = arrays(triangles)
    # Push the vertex in the middle of the z=1 face down through the cube and out of its bottom,
    # so its triangles cross the bottom face.
    middle = int(np.argmin(np.linalg.norm(points - (0.5, 0.5, 1.0), axis=1)))
    pushed = points.copy()
    pushed[middle] = (0.5, 0.5, -0.3)
    assert len(crossing_triangles(pushed, faces)) > 0
    text = stl("walls", [tuple(map(tuple, pushed[f])) for f in faces])
    out, report = repair({"walls": text})
    assert report.before > 0 and report.after == 0 and report.moved > 0
    fixed_points, fixed_faces = arrays(
        [
            tuple(map(tuple, t))
            for t in np.asarray(
                [
                    list(map(float, line.split()[1:]))
                    for line in out["walls"].splitlines()
                    if line.strip().startswith("vertex")
                ]
            ).reshape(-1, 3, 3)
        ]
    )
    assert len(crossing_triangles(fixed_points, fixed_faces)) == 0


def test_a_sealed_pocket_beside_the_body_is_dropped_and_an_island_is_kept() -> None:
    body = cube(size=1.0)
    pocket = cube(origin=(3, 0, 0), size=0.2, n=1)
    island = [(a, c, b) for a, b, c in cube(origin=(0.4, 0.4, 0.4), size=0.2, n=1)]  # faces in
    out, report = repair({"walls": stl("walls", body + pocket + island)})
    assert report.pockets == 1 and report.pocket_triangles == len(pocket)
    kept = out["walls"].count("outer loop")
    assert kept == len(body) + len(island)


def test_a_knife_fold_is_counted() -> None:
    # Two triangles back to back across their shared edge, 10 degrees apart.
    tip = (np.cos(np.radians(10)), np.sin(np.radians(10)), 0.0)
    folded = [((0, 0, 0), (1, 0, 0), (0, 0, 1)), ((0, 0, 0), (0, 0, 1), tip)]
    assert knife_folds({"a": stl("a", folded)}) == 1
    flat = [((0, 0, 0), (1, 0, 0), (0, 0, 1)), ((0, 0, 0), (0, 0, 1), (-1, 0, 0))]
    assert knife_folds({"a": stl("a", flat)}) == 0
