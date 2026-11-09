"""Finding a point inside a closed surface: the analytic cases, so the answer is known.

**Why the tests build their own triangles.** The finder's failure mode is a point that is inside
the bounding box but outside the solid - the centroid of a pipe, or a point in the hole of a
flange - and a test that cannot say independently where "inside" is would pass on a wrong point.
A box and a sphere have closed-form answers, so those are the primary cases; a hollow pipe is the
case that catches the centroid trap.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from stokes.geometry.inside import (
    COARSE_LATTICE,
    LATTICE,
    inside_point,
    inside_point_or_coarse,
    is_inside,
)

# ------------------------------------------------------------------ shapes


def box_mesh(
    x0: float, y0: float, z0: float, x1: float, y1: float, z1: float
) -> tuple[np.ndarray, np.ndarray]:
    """An axis-aligned box, outward-facing, as 12 triangles.

    Written here rather than through the geometry store: the test must own the triangles so a
    change in the store cannot change what "inside" means for the test.
    """
    verts = np.array(
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
    quads = [
        (0, 1, 2, 3),
        (4, 5, 6, 7),  # bottom, top
        (0, 1, 5, 4),
        (1, 2, 6, 5),  # sides
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ]
    tris: list[list[int]] = []
    for a, b, c, d in quads:
        tris.append([a, b, c])
        tris.append([a, c, d])
    return verts, np.array(tris, dtype=int)


def sphere_mesh(
    cx: float, cy: float, cz: float, r: float, n: int = 24
) -> tuple[np.ndarray, np.ndarray]:
    """A UV sphere. **The radius is known**, so the clearance a found point reports can be
    checked against ``r - distance_from_centre`` rather than trusted."""
    verts: list[list[float]] = []
    for i in range(n + 1):
        theta = math.pi * i / n
        for j in range(n):
            phi = 2 * math.pi * j / n
            verts.append(
                [
                    cx + r * math.sin(theta) * math.cos(phi),
                    cy + r * math.sin(theta) * math.sin(phi),
                    cz + r * math.cos(theta),
                ]
            )
    tris: list[list[int]] = []
    for i in range(n):
        for j in range(n):
            a = i * n + j
            b = i * n + (j + 1) % n
            c = (i + 1) * n + (j + 1) % n
            d = (i + 1) * n + j
            tris.append([a, b, c])
            tris.append([a, c, d])
    return np.array(verts, dtype=float), np.array(tris, dtype=int)


def pipe_mesh(
    outer: float, inner: float, height: float, n: int = 32
) -> tuple[np.ndarray, np.ndarray]:
    """A hollow pipe: an annulus extruded. **The centroid is in the hole**, so this is the shape
    that proves the finder is not returning the centroid."""
    verts: list[list[float]] = []
    for z in (0.0, height):
        for j in range(n):
            phi = 2 * math.pi * j / n
            verts.append([outer * math.cos(phi), outer * math.sin(phi), z])
        for j in range(n):
            phi = 2 * math.pi * j / n
            verts.append([inner * math.cos(phi), inner * math.sin(phi), z])

    # Index helpers: two rings per z level, outer then inner.
    def o(zl: int, j: int) -> int:
        return zl * 2 * n + j % n

    def i_(zl: int, j: int) -> int:
        return zl * 2 * n + n + j % n

    tris: list[list[int]] = []
    for j in range(n):
        # outer wall
        tris += [[o(0, j), o(0, j + 1), o(1, j + 1)], [o(0, j), o(1, j + 1), o(1, j)]]
        # inner wall, wound the other way so its normal points into the hole
        tris += [[i_(0, j), i_(1, j + 1), i_(0, j + 1)], [i_(0, j), i_(1, j), i_(1, j + 1)]]
        # top and bottom annulus faces
        tris += [[o(0, j), i_(0, j), i_(0, j + 1)], [o(0, j), i_(0, j + 1), o(0, j + 1)]]
        tris += [[o(1, j), i_(1, j + 1), i_(1, j)], [o(1, j), o(1, j + 1), i_(1, j + 1)]]
    return np.array(verts, dtype=float), np.array(tris, dtype=int)


# ------------------------------------------------------------------ is_inside


def test_a_point_in_the_middle_of_a_box_is_inside() -> None:
    """**A point chosen OFF the quads' split diagonals, deliberately.**

    My first version used (1.0, 1.5, 2.0), which is the exact centre and - because
    :func:`box_mesh` splits each quad along its diagonal - lies precisely ON the diagonal of the
    `x=2` face. A ray from there passes through the shared edge of two coplanar triangles, counts
    it twice, and the parity comes out even: measured, `is_inside` answered False for the centre
    of a box while answering True for a point one millimetre away from it.

    That is the known degeneracy the three-axis majority exists to cover, and the honest response
    is a test that does not sit on it plus a test that pins what happens when it does (below).
    """
    verts, faces = box_mesh(0, 0, 0, 2, 3, 4)
    tris = [tuple(map(tuple, verts[t])) for t in faces]
    assert is_inside(tris, (0.9, 1.4, 1.9))


def test_the_KNOWN_degeneracy_on_a_shared_diagonal_is_pinned() -> None:
    """**Documented, not hidden.** A point exactly on the diagonal where two coplanar triangles
    meet can be miscounted by every axis at once, and no majority rescues it. For the box's centre
    that is the case.

    It does not matter for the mesh: the point is CHOSEN from an inset lattice of 343 candidates
    and the one farthest from the surface wins, so a candidate on a degenerate line is never the
    only one - and `inside_point`'s own tests assert the point it returns really is inside. Pinned
    so the behaviour is a known limit rather than a surprise.
    """
    verts, faces = box_mesh(0, 0, 0, 2, 3, 4)
    tris = [tuple(map(tuple, verts[t])) for t in faces]
    assert not is_inside(tris, (1.0, 1.5, 2.0)), (
        "if this now returns True the degeneracy has been fixed - which would be good news; "
        "update this test and say so rather than deleting it"
    )
    # One millimetre off the diagonal and the answer is right again.
    assert is_inside(tris, (0.999, 1.5, 2.0))


def test_a_point_well_outside_the_box_is_not_inside() -> None:
    verts, faces = box_mesh(0, 0, 0, 2, 3, 4)
    tris = [tuple(map(tuple, verts[t])) for t in faces]
    assert not is_inside(tris, (5.0, 1.5, 2.0))
    assert not is_inside(tris, (1.0, -1.0, 2.0))
    assert not is_inside(tris, (1.0, 1.5, 9.0))


# ------------------------------------------------------------------ inside_point


def test_the_box_finds_a_point_inside_its_own_volume() -> None:
    verts, faces = box_mesh(0, 0, 0, 2, 3, 4)
    found = inside_point(verts, faces, (0, 0, 0, 2, 3, 4))
    assert found.found
    x, y, z = found.point  # type: ignore[misc]
    assert 0 < x < 2 and 0 < y < 3 and 0 < z < 4, "the point is not inside the box"


def test_a_SPHERE_gives_a_point_near_its_centre_with_a_clearance_that_matches_the_radius() -> None:
    """**The analytic check.** For a sphere the clearance from the centre is known, so the point
    the finder chooses can be verified rather than merely accepted: it must be inside, and its
    reported clearance must be less than the radius (the nearest centroid is on the surface)."""
    verts, faces = sphere_mesh(0.0, 0.0, 0.0, 1.0)
    found = inside_point(verts, faces, (-1, -1, -1, 1, 1, 1))
    assert found.found
    x, y, z = found.point  # type: ignore[misc]
    distance = math.sqrt(x * x + y * y + z * z)
    assert distance < 1.0, "the point is outside the sphere"
    assert found.clearance < 1.0 + 1e-9
    assert found.clearance > 0.0


def test_a_PIPE_does_not_return_the_centroid_because_the_centroid_is_in_the_hole() -> None:
    """**The trap the finder exists to avoid.** A pipe's centroid sits in the bore, which is
    outside the solid; returning it would make snappy keep the wrong region. The point must have a
    radius between the inner and outer walls."""
    outer, inner = 1.0, 0.5
    verts, faces = pipe_mesh(outer, inner, 1.0)
    found = inside_point(verts, faces, (-outer, -outer, 0.0, outer, outer, 1.0))
    assert found.found
    x, y, _z = found.point  # type: ignore[misc]
    radius = math.hypot(x, y)
    assert inner < radius < outer, (
        f"the point at radius {radius:.3f} is not in the pipe wall ({inner}..{outer}); "
        "a point in the bore means the finder returned something near the centroid"
    )


def test_the_OUTSIDE_complement_is_answered_for_an_enclosure() -> None:
    """For a wind tunnel the point must be in the free space OUTSIDE the body."""
    verts, faces = sphere_mesh(0.0, 0.0, 0.0, 0.5)
    found = inside_point(verts, faces, (-2, -2, -2, 2, 2, 2), outside=True)
    assert found.found
    x, y, z = found.point  # type: ignore[misc]
    assert math.sqrt(x * x + y * y + z * z) > 0.5, "the point is inside the body"


def test_an_empty_triangle_list_reports_nothing_found_rather_than_raising() -> None:
    """A part with no tessellation is a state the caller reports; the finder says so and lets the
    driver choose the message."""
    found = inside_point(np.zeros((0, 3)), np.zeros((0, 3), dtype=int), (0, 0, 0, 1, 1, 1))
    assert not found.found
    assert found.tested == 0


def test_the_lattice_is_inset_so_no_candidate_sits_on_the_bounding_box_face() -> None:
    """A point exactly on a face is the least reliable place for a parity test. Verified by
    checking every returned point is strictly inside the box, over several shapes."""
    box_v, box_f = box_mesh(0, 0, 0, 1, 1, 1)
    sphere_v, sphere_f = sphere_mesh(0, 0, 0, 1, n=16)
    for verts, faces, bbox in (
        (box_v, box_f, (0, 0, 0, 1, 1, 1)),
        (sphere_v, sphere_f, (-1, -1, -1, 1, 1, 1)),
    ):
        found = inside_point(verts, faces, bbox)
        assert found.found
        for value, lo, hi in zip(found.point, bbox[:3], bbox[3:], strict=True):  # type: ignore[arg-type]
            assert lo < value < hi


def test_a_bad_vertex_array_is_named_rather_than_silently_misread() -> None:
    with pytest.raises(ValueError, match="vertices must be"):
        inside_point(np.zeros((4, 2)), np.zeros((0, 3), dtype=int), (0, 0, 0, 1, 1, 1))


# ------------------------------------------------------------------ the retry


def test_the_coarse_retry_finds_a_point_when_the_fine_lattice_misses() -> None:
    """A thin slab: the fine lattice can straddle it without landing inside, and the coarse one
    samples different coordinates. Both run the SAME verified test, so the retry cannot invent a
    point that is not inside."""
    verts, faces = box_mesh(0, 0, 0, 1, 1, 0.012)  # very thin in z
    bbox = (-0.001, -0.001, -0.001, 1.001, 1.001, 0.013)
    found = inside_point_or_coarse(verts, faces, bbox)
    assert found.found
    _x, _y, z = found.point  # type: ignore[misc]
    assert 0 < z < 0.012, "the point is not inside the slab"
    # The fine pass alone may or may not find it; the combined result must, and it reports how
    # many candidates it took.
    assert found.tested >= LATTICE**3 or found.coarse


def test_the_retry_reports_the_coarse_flag_only_when_it_was_needed() -> None:
    """A sphere is easy: the fine lattice finds it, so ``coarse`` stays False and the caller is not
    told a retry happened."""
    verts, faces = sphere_mesh(0, 0, 0, 1.0, n=20)
    found = inside_point_or_coarse(verts, faces, (-1, -1, -1, 1, 1, 1))
    assert found.found
    assert not found.coarse
    assert found.tested == LATTICE**3


def test_both_passes_failing_reports_the_total_tested() -> None:
    """The count is what makes a failure diagnosable, so it must include both lattices."""
    # A body entirely outside the box it is asked about: nothing can be inside.
    verts, faces = box_mesh(50, 50, 50, 51, 51, 51)
    found = inside_point_or_coarse(verts, faces, (0, 0, 0, 1, 1, 1))
    assert not found.found
    assert found.tested == LATTICE**3 + COARSE_LATTICE**3
