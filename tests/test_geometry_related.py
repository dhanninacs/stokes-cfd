"""The pure predicates of ``stokes.geometry.related`` on synthetic arrays (ADR-0022 decision
6.4): same_area, parallel, coplanar, axis_of, axis_point, picked_radius and coaxial.

No session and no OCP: the facet arrays are built by hand (rings of quads on a cylinder, a
patch of the sphere), in the same layout the tessellation gives the commands: per facet its
unit normal, area and centroid, and per candidate face its fingerprint centroid."""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from stokes.geometry import related
from stokes.geometry.related import (
    AXIS_ANGLE,
    AXIS_FRACTION,
    AXIS_RATIO,
    OPEN,
    axis_of,
    axis_point,
    coaxial,
    coplanar,
    distance_to_axis,
    parallel,
    picked_radius,
    same_area,
    wraps_axis,
)


def half_cylinder(radius: float, z0: float, z1: float, *, side: int, n: int = 72) -> tuple:
    """The facets of one half (a split face) of a cylindrical side, ``side`` +1 for the half
    around +x: per facet its outward radial normal, its area and its centroid."""
    low = -np.pi / 2 if side > 0 else np.pi / 2
    mids = np.linspace(low, low + np.pi, n, endpoint=False) + np.pi / (2 * n)
    normals = np.column_stack([np.cos(mids), np.sin(mids), np.zeros(n)])
    areas = np.full(n, np.pi * radius * (z1 - z0) / n)
    centroids = np.column_stack(
        [radius * np.cos(mids), radius * np.sin(mids), np.full(n, (z0 + z1) / 2)]
    )
    return normals, areas, centroids


def ring_cylinder(radius: float, z0: float, z1: float, n: int = 72) -> tuple:
    """The facets of a whole cylindrical side (one face, face id 0), both halves."""
    right = half_cylinder(radius, z0, z1, side=1, n=n // 2)
    left = half_cylinder(radius, z0, z1, side=-1, n=n // 2)
    normals = np.vstack([right[0], left[0]])
    areas = np.concatenate([right[1], left[1]])
    centroids = np.vstack([right[2], left[2]])
    face = np.zeros(normals.shape[0], dtype=np.int64)
    return normals, areas, centroids, face


# ---------------------------------------------------------------- same_area


def test_same_area_matches_within_the_relative_tolerance() -> None:
    mask = same_area(np.array([1.0, 1.019, 1.021, 2.0]), np.array([1.0]), 0.02)
    assert mask.tolist() == [True, True, False, False]
    # the tolerance is relative to a picked face's area
    assert same_area(np.array([1.0]), np.array([2.0]), 0.6).tolist() == [True]
    assert same_area(np.array([1.0]), np.array([2.0]), 0.4).tolist() == [False]
    # no picked faces, and picked faces without area, give no reference
    assert same_area(np.array([1.0, 2.0]), np.array([]), 0.02).tolist() == [False, False]
    assert same_area(np.array([1.0, 2.0]), np.array([0.0, 0.0]), 0.02).tolist() == [False, False]


def test_same_area_keeps_each_picked_face_its_own_reference() -> None:
    picked = np.array([0.5, 1.0])
    mask = same_area(np.array([0.509, 0.99, 1.5]), picked, 0.02)
    assert mask.tolist() == [True, True, False]  # 1.5 is within 2 % of nothing


# ---------------------------------------------------------------- parallel


def test_parallel_needs_open_faces_and_the_angle() -> None:
    picked = np.array([[0.0, 0.0, 1.0]])
    normals = np.array(
        [
            [0.0, 0.0, 1.0],  # the same: yes
            [0.0, 0.0, -1.0],  # opposite: no
            [0.03, 0.0, np.sqrt(1.0 - 0.03**2)],  # 0.03 rad: yes
            [0.04, 0.0, np.sqrt(1.0 - 0.04**2)],  # 0.04 rad: no
            [0.0, 0.0, OPEN - 0.05],  # a closed face: no
        ]
    )
    mask = parallel(normals, picked, 0.035)
    assert mask.tolist() == [True, False, True, False, False]
    # a picked face without a readable normal gives nothing
    assert parallel(normals, np.array([[0.0, 0.0, OPEN - 0.05]]), 0.035).sum() == 0


def test_parallel_against_any_picked_normal() -> None:
    picked = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0]])
    normals = np.array([[0.70710678, 0.70710678, 0.0], [0.0, 0.0, -1.0]])
    assert parallel(normals, picked, 0.035).tolist() == [False, False]
    assert parallel(normals[:1], picked, 0.8).tolist() == [True]  # 45 degrees, wide tolerance


# ---------------------------------------------------------------- coplanar


def test_coplanar_is_parallel_and_close_to_the_plane() -> None:
    picked_normals = np.array([[0.0, 0.0, 1.0]])
    picked_centroids = np.array([[0.0, 0.0, 0.0]])
    normals = np.array(
        [
            [0.001, 0.0, np.sqrt(1.0 - 1e-6)],  # nearly parallel
            [0.0, 0.0, 1.0],  # parallel, off the plane
            [0.0, 0.0, -1.0],  # opposite
        ]
    )
    centroids = np.array(
        [
            [0.5, 0.3, 0.0005],  # 0.5 mm from the plane: inside 0.1 % of a 1 m part
            [0.0, 0.0, 0.002],  # 2 mm: outside
            [0.0, 0.0, 0.0],
        ]
    )
    mask = coplanar(
        normals, centroids, picked_normals, picked_centroids, scale=1.0, offset_tol=0.001
    )
    assert mask.tolist() == [True, False, False]
    # the threshold scales with the part's diagonal
    far = coplanar(
        normals[1:2], centroids[1:2], picked_normals, picked_centroids, scale=10.0, offset_tol=0.001
    )
    assert far.tolist() == [True]  # 2 mm is inside 0.1 % of a 10 m part


# ---------------------------------------------------------------- the axis


def test_axis_of_recovers_the_cylinder_axis() -> None:
    normals, areas, _, _ = ring_cylinder(0.05, 0.0, 0.1)
    axis, ratio = axis_of(normals, areas)
    assert abs(axis[2]) == pytest.approx(1.0, abs=1e-9)
    assert ratio < 1e-9
    # a patch of the sphere is not cylindrical
    theta = np.linspace(0.0, 0.698, 12)  # 40 degrees
    phi = np.linspace(0.0, 2 * np.pi, 24, endpoint=False)
    t, p = np.meshgrid(theta, phi)
    # one row per (theta, phi): column_stack alone would keep the meshgrid's two dimensions
    sphere = np.stack([np.sin(t) * np.cos(p), np.sin(t) * np.sin(p), np.cos(t)], axis=-1).reshape(
        -1, 3
    )
    _, ratio = axis_of(sphere, np.ones(sphere.shape[0]))
    assert ratio > AXIS_RATIO
    # no area gives no data
    axis, ratio = axis_of(normals, np.zeros(areas.shape))
    assert ratio > AXIS_RATIO and axis.tolist() == [0.0, 0.0, 0.0]


def test_axis_of_a_plane_leaves_the_axis_in_the_plane() -> None:
    normals = np.tile([0.0, 0.0, 1.0], (4, 1))
    axis, ratio = axis_of(normals, np.ones(4))
    assert ratio == pytest.approx(0.0, abs=1e-9)
    assert abs(axis @ np.array([0.0, 0.0, 1.0])) < 1e-9


# ---------------------------------------------------------------- the picked radius


def test_picked_radius_of_a_split_hole_is_the_arc_centroid_distance() -> None:
    r, z0, z1 = 0.05, 0.0, 0.1
    right = half_cylinder(r, z0, z1, side=1)
    left = half_cylinder(r, z0, z1, side=-1)
    normals = np.vstack([right[0], left[0]])
    areas = np.concatenate([right[1], left[1]])
    axis, _ = axis_of(normals, areas)
    origin = axis_point(normals, np.vstack([right[2], left[2]]), areas, axis)
    # both halves picked: the mean centroid sits on the axis
    assert np.abs(origin).max() < 1e-12
    # a face's centroid is 2R/pi from the axis of its cylinder
    face_centroids = np.array(
        [[2 * r / np.pi, 0.0, (z0 + z1) / 2], [-2 * r / np.pi, 0.0, (z0 + z1) / 2]]
    )
    face_areas = np.array([np.pi * r * (z1 - z0)] * 2)
    radius = picked_radius(face_centroids, face_areas, origin, axis)
    assert radius == pytest.approx(2 * r / np.pi, rel=1e-6)
    assert picked_radius(np.zeros((0, 3)), np.array([]), origin, axis) == 0.0


def test_distance_to_axis() -> None:
    origin = np.array([0.0, 0.0, 1.0])
    axis = np.array([0.0, 0.0, 1.0])
    d = distance_to_axis(
        np.array([[0.0, 0.0, 2.0], [0.3, 0.4, 5.0], [0.3, 0.4, 1.0]]), origin, axis
    )
    assert d.tolist() == pytest.approx([0.0, 0.5, 0.5])


# ---------------------------------------------------------------- coaxial


def test_coaxial_finds_the_counterpart_hole_and_rejects_another_radius() -> None:
    r, z0, z1 = 0.05, 0.0, 0.1
    # picked: both halves of the first hole's side
    p_right = half_cylinder(r, z0, z1, side=1)
    p_left = half_cylinder(r, z0, z1, side=-1)
    normals = np.vstack([p_right[0], p_left[0]])
    areas = np.concatenate([p_right[1], p_left[1]])
    axis, _ = axis_of(normals, areas)
    origin = axis_point(normals, np.vstack([p_right[2], p_left[2]]), areas, axis)
    face_centroids = np.array(
        [[2 * r / np.pi, 0.0, (z0 + z1) / 2], [-2 * r / np.pi, 0.0, (z0 + z1) / 2]]
    )
    face_areas = np.array([np.pi * r * (z1 - z0)] * 2)
    radius = picked_radius(face_centroids, face_areas, origin, axis)
    # candidates: the second hole (same radius) and a third of another, two faces each
    q_right = half_cylinder(r, 0.2, 0.3, side=1)
    q_left = half_cylinder(r, 0.2, 0.3, side=-1)
    o_right = half_cylinder(0.07, 0.2, 0.3, side=1)
    o_left = half_cylinder(0.07, 0.2, 0.3, side=-1)
    n = q_right[0].shape[0]
    cand_normals = np.vstack([q_right[0], q_left[0], o_right[0], o_left[0]])
    cand_areas = np.concatenate([q_right[1], q_left[1], o_right[1], o_left[1]])
    cand_face = np.concatenate(
        [
            np.zeros(n, dtype=np.int64),
            np.ones(n, dtype=np.int64),
            np.full(n, 2, dtype=np.int64),
            np.full(n, 3, dtype=np.int64),
        ]
    )
    cand_centroids = np.array(
        [
            [2 * r / np.pi, 0.0, 0.25],
            [-2 * r / np.pi, 0.0, 0.25],
            [2 * 0.07 / np.pi, 0.0, 0.25],
            [-2 * 0.07 / np.pi, 0.0, 0.25],
        ]
    )
    mask = coaxial(
        cand_normals, cand_areas, cand_face, cand_centroids, 4, axis, origin, radius, 0.02
    )
    assert mask.tolist() == [True, True, False, False]


def test_coaxial_wants_mostly_perpendicular_facets() -> None:
    # a face half of whose facets run along the axis: too little of its area is cylindrical
    mixed = np.vstack([np.tile([0.0, 1.0, 0.0], (4, 1)), np.tile([0.0, 0.0, 1.0], (4, 1))])
    centroids = np.array([[0.05, 0.0, 0.05]])
    axis = np.array([0.0, 0.0, 1.0])
    origin = np.array([0.0, 0.0, 0.0])
    mask = coaxial(
        mixed, np.full(8, 0.01), np.zeros(8, dtype=np.int64), centroids, 1, axis, origin, 0.05, 0.02
    )
    assert mask.tolist() == [False]
    # a face whose facets are only half around the axis: too little area
    ring = half_cylinder(0.05, 0.2, 0.3, side=1, n=4)
    centroids = np.array([[2 * 0.05 / np.pi, 0.0, 0.25]])
    mask = coaxial(
        ring[0],
        ring[1],
        np.zeros(ring[0].shape[0], dtype=np.int64),
        centroids,
        1,
        axis,
        origin,
        2 * 0.05 / np.pi,
        0.02,
        angle=AXIS_ANGLE,
        fraction=AXIS_FRACTION,
    )
    assert mask.tolist() == [True]  # a half arc is still mostly perpendicular to z


def test_coaxial_of_a_whole_ring_matches_only_faces_centred_on_the_axis() -> None:
    picked = ring_cylinder(0.05, 0.0, 0.1)
    axis, _ = axis_of(picked[0], picked[1])
    origin = axis_point(picked[0], picked[2], picked[1], axis)
    # a whole face keeps its centroid on the axis: its picked radius is ~0
    radius = picked_radius(
        np.array([[0.0, 0.0, 0.05]]), np.array([np.pi * 0.1 * 0.1]), origin, axis
    )
    assert radius < 1e-9
    same = ring_cylinder(0.05, 0.2, 0.3)
    other = ring_cylinder(0.07, 0.2, 0.3)
    centroids = np.array([[0.0, 0.0, 0.25], [0.0, 0.0, 0.25]])
    face = np.zeros(same[3].shape[0] + other[3].shape[0], dtype=np.int64)
    face[same[3].shape[0] :] = 1
    normals = np.vstack([same[0], other[0]])
    areas = np.concatenate([same[1], other[1]])
    mask = coaxial(normals, areas, face, centroids, 2, axis, origin, radius, 0.02)
    assert mask.tolist() == [True, True]  # both keep their centroid on the axis


def test_coaxial_without_face_ids_finds_nothing() -> None:
    """A mesh without face ids gives facets named -1: no bincount crash, no matches."""
    normals, areas, _, _ = ring_cylinder(0.05, 0.0, 0.1)
    mask = coaxial(
        normals,
        areas,
        np.full(normals.shape[0], -1, dtype=np.int64),
        np.zeros((3, 3)),
        3,
        np.array([0.0, 0.0, 1.0]),
        np.zeros(3),
        0.05,
        0.02,
    )
    assert mask.tolist() == [False, False, False]


# ---------------------------------------------------------------- the axis line


def test_axis_point_of_one_half_sits_on_the_axis() -> None:
    """One half of a cylinder is enough: the least-squares normal lines cross on the axis,
    while the mean of that half's facet centroids is 2R/pi away from it."""
    r, z0, z1 = 0.05, 0.0, 0.1
    normals, areas, centroids = half_cylinder(r, z0, z1, side=1)
    axis = np.array([0.0, 0.0, 1.0])
    origin = axis_point(normals, centroids, areas, axis)
    assert np.abs(origin).max() < 1e-12
    mean = (centroids * areas[:, None]).sum(axis=0) / areas.sum()
    assert np.linalg.norm(mean[:2]) == pytest.approx(2 * r / np.pi, rel=1e-3)
    # a cylinder off the origin follows it
    moved = axis_point(normals, centroids + np.array([0.01, 0.0, 0.0]), areas, axis)
    assert moved[:2] == pytest.approx([0.01, 0.0], abs=1e-12)


def test_wraps_axis_sees_a_cylinder_and_not_a_plane() -> None:
    normals, areas, _, _ = ring_cylinder(0.05, 0.0, 0.1)
    assert wraps_axis(normals, areas) is True
    plane = np.tile([0.0, 0.0, 1.0], (4, 1))
    assert wraps_axis(plane, np.ones(4)) is False
    # two coplanar patches share the direction all the same: still no axis
    assert wraps_axis(np.vstack([plane, plane]), np.ones(8)) is False
    assert wraps_axis(normals, np.zeros(areas.shape)) is False  # no area: no data


def test_blocks_give_the_answer_of_one_piece(monkeypatch: pytest.MonkeyPatch) -> None:
    """A big pick over a big part is worked in blocks, not as one faces-by-picked matrix."""
    rng = np.random.default_rng(3)
    areas = rng.uniform(0.5, 2.0, 120)
    picked_areas = rng.uniform(0.5, 2.0, 9)
    unit = rng.normal(size=(120, 3))
    normals = unit / np.linalg.norm(unit, axis=1, keepdims=True)
    centroids = rng.uniform(-1.0, 1.0, (120, 3))
    args = (normals, centroids, normals[:9], centroids[:9], 2.0, 0.3)

    def answers() -> list[NDArray[np.bool_]]:
        return [
            same_area(areas, picked_areas, 0.1),
            parallel(normals, normals[:9], 0.5),
            coplanar(*args),
        ]

    whole = answers()
    monkeypatch.setattr(related, "BLOCK", 7)
    for one, blocked in zip(whole, answers(), strict=True):
        assert np.array_equal(one, blocked)
    assert any(m.any() for m in whole)  # the comparison is not between empty masks
