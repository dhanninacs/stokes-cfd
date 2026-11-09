"""Fingerprints, face adjacency, feature-angle growth and re-matching (ADR-0020 slice 1) on OCP
fixtures tessellated in this process: the bracket and pin of the import tests (a 10 x 20 x 30 mm
box and a 5 mm radius, 40 mm cylinder) and cylinders whose side is split into several faces."""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("OCP")

from stokes.geometry.fingerprints import Fingerprints, fingerprints_of, match_faces
from stokes.geometry.mesh import PartMesh, TriMesh
from stokes.geometry.selection import grow_faces, ranges_from_mask
from stokes.geometry.topology import adjacency_of, edges_of, facets_of, surface_of
from tests import geometry_fixtures as fx
from tests.selection_support import (
    corners_of,
    cube,
    mesh_from_corners,
    permuted,
    split_cylinder,
    tessellated,
)

MM = 1e-3


def prints(*meshes: PartMesh) -> list[Fingerprints]:
    surface = surface_of(meshes)
    return fingerprints_of(surface, facets_of(surface))


def grow(mesh: PartMesh, seed: int, angle: float) -> tuple[tuple[int, int], ...]:
    surface = surface_of([mesh])
    facets = facets_of(surface)
    adjacency = adjacency_of(surface, facets, edges_of(surface))
    mask = grow_faces(adjacency, surface.face_total, np.array([seed]), angle)
    return ranges_from_mask(mask)


@pytest.fixture(scope="module")
def bracket() -> PartMesh:
    return PartMesh((tessellated(fx.box(10.0, 20.0, 30.0), name="bracket"),))


@pytest.fixture(scope="module")
def pin() -> PartMesh:
    return PartMesh((tessellated(fx.moved(fx.cylinder(5.0, 40.0), 50.0, 0.0, 0.0), name="pin"),))


def test_fingerprints_of_the_box(bracket: PartMesh) -> None:
    (box,) = prints(bracket)
    assert box.faces == 6 and box.triangles.tolist() == [2] * 6
    assert box.scale == pytest.approx(math.sqrt(10**2 + 20**2 + 30**2) * MM, rel=1e-6)
    size = np.array([10.0, 20.0, 30.0]) * MM
    for face in range(6):
        normal = box.normal[face]
        axis = int(np.argmax(np.abs(normal)))
        assert np.linalg.norm(normal) == pytest.approx(1.0, abs=1e-6)  # a plane: unit mean normal
        others = [a for a in range(3) if a != axis]
        assert box.area[face] == pytest.approx(size[others[0]] * size[others[1]], rel=1e-5)
        expected = size / 2.0
        expected[axis] = size[axis] if normal[axis] > 0 else 0.0
        assert box.centroid[face] == pytest.approx(expected, abs=1e-7)


def test_fingerprints_of_the_cylinder(pin: PartMesh) -> None:
    (cylinder,) = prints(pin)
    assert cylinder.faces == 3
    side = int(np.argmax(cylinder.area))
    caps = [f for f in range(3) if f != side]
    assert cylinder.area[side] == pytest.approx(2 * math.pi * 5 * 40 * MM**2, rel=0.01)
    assert np.linalg.norm(cylinder.normal[side]) < 0.02  # a closed face: the normals cancel
    assert cylinder.centroid[side] == pytest.approx([50 * MM, 0.0, 20 * MM], abs=1e-5)
    heights = sorted(float(cylinder.centroid[c][2]) for c in caps)
    assert heights == pytest.approx([0.0, 40 * MM], abs=1e-7)
    for cap in caps:
        assert cylinder.area[cap] == pytest.approx(math.pi * 25 * MM**2, rel=0.01)
        assert abs(cylinder.normal[cap][2]) == pytest.approx(1.0, abs=1e-6)
    assert cylinder.triangles.sum() == pin.triangles


def test_a_batch_gives_each_part_its_own_fingerprints(bracket: PartMesh, pin: PartMesh) -> None:
    together = prints(bracket, pin)
    alone = prints(bracket) + prints(pin)
    for joint, single in zip(together, alone, strict=True):
        assert joint.faces == single.faces and joint.scale == pytest.approx(single.scale)
        assert joint.area == pytest.approx(single.area)
        assert joint.centroid == pytest.approx(single.centroid)


def test_growth_stops_at_every_box_edge(bracket: PartMesh) -> None:
    for face in range(6):
        assert grow(bracket, face, 30.0) == ((face, face),)
        assert grow(bracket, face, 89.0) == ((face, face),)
        assert grow(bracket, face, 91.0) == ((0, 5),)  # 90 degree edges now count as smooth


@pytest.mark.parametrize(("cuts", "sides"), [(1, 2), (3, 4)])
def test_growth_merges_a_split_cylinder_side(cuts: int, sides: int) -> None:
    mesh = PartMesh((tessellated(split_cylinder(5.0, 40.0, cuts), name="split"),))
    (found,) = prints(mesh)
    side = [f for f in range(found.faces) if abs(found.normal[f][2]) < 0.5]
    caps = [f for f in range(found.faces) if f not in side]
    assert len(side) == sides and len(caps) == 2
    for face in side:
        assert grow(mesh, face, 30.0) == ranges_from_mask(np.isin(np.arange(found.faces), side))
        assert grow(mesh, face, 2.0) == ((face, face),)  # the facets jump ~5.6 degrees there
    for cap in caps:
        assert grow(mesh, cap, 30.0) == ((cap, cap),)


def test_adjacency_never_crosses_parts() -> None:
    # Two cubes sharing a face plane and four corners, in one batch: the weld is per part.
    left, right = part_of(cube(1.0)), part_of(cube(1.0, (1.0, 0.0, 0.0)))
    surface = surface_of([left, right])
    adjacency = adjacency_of(surface, facets_of(surface), edges_of(surface))
    owner = np.searchsorted(surface.face_offset, adjacency.pairs, side="right") - 1
    assert (owner[:, 0] == owner[:, 1]).all() and adjacency.pairs.shape[0] == 24


def part_of(mesh: TriMesh) -> PartMesh:
    return PartMesh((mesh,))


def test_rematch_after_retessellation_is_the_identity(bracket: PartMesh) -> None:
    shape = fx.box(10.0, 20.0, 30.0)
    split = split_cylinder(5.0, 40.0, 3)
    for coarse, fine in (
        (tessellated(shape, "coarse"), tessellated(shape, "fine")),
        (tessellated(split, "coarse"), tessellated(split, "fine")),
    ):
        old, new = prints(PartMesh((coarse,)))[0], prints(PartMesh((fine,)))[0]
        matches = match_faces(old, new, range(old.faces))
        assert [m.new for m in matches] == list(range(old.faces))
        assert min(m.confidence for m in matches) > 0.9


def test_rematch_follows_renumbered_faces(bracket: PartMesh) -> None:
    order = [3, 0, 5, 1, 2, 4]
    old = prints(bracket)[0]
    new = prints(PartMesh((permuted(bracket.solids[0], order),)))[0]
    matches = match_faces(old, new, [0, 2, 5])
    assert [(m.old, m.new) for m in matches] == [(0, 3), (2, 5), (5, 4)]
    assert all(m.confidence > 0.95 for m in matches)


def test_a_face_that_went_is_listed_unmatched(bracket: PartMesh) -> None:
    solid = bracket.solids[0]
    assert solid.face_ids is not None
    keep = solid.face_ids != 2
    renumber = np.array([0, 1, 0, 2, 3, 4])  # face 2 is gone; the ones after it move down
    shorter = mesh_from_corners(corners_of(solid)[keep], renumber[solid.face_ids[keep]])
    matches = match_faces(prints(bracket)[0], prints(PartMesh((shorter,)))[0], range(6))
    assert [m.new for m in matches] == [0, 1, None, 2, 3, 4]
    assert matches[2].confidence < 0.5


def test_ambiguous_faces_are_not_guessed() -> None:
    # Two faces with the same fingerprint (a face split into equal halves in the new version)
    # leave the old face without a confident match.
    old = Fingerprints(
        np.array([2.0]),
        np.array([[0.5, 0.0, 0.0]]),
        np.array([[0.0, 0.0, 1.0]]),
        np.array([2]),
        1.0,
    )
    new = Fingerprints(
        np.array([2.0, 2.0]),
        np.array([[0.5, 0.0, 0.0], [0.5, 0.0, 0.0]]),
        np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0]]),
        np.array([2, 2]),
        1.0,
    )
    (match,) = match_faces(old, new, [0])
    assert match.new is None and match.confidence == 0.0
