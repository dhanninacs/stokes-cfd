"""Flat faces that a feature-angle split merged through a curved surface, taken apart again.

:func:`stokes.geometry.meshfile.split_faces` links two triangles whenever their dihedral angle is
below the feature angle and takes connected components, so the rule is transitive. Where a curved
surface crosses flat faces at a shallow angle the regions chain: measured on a 20 x 10 x 10 mm
block joined with a 6 mm sphere (2026-11-17), the block's +y, -y, +z and -z faces and the sphere
came out as ONE face of 15,272 triangles, although the four flat faces meet each other at 90
degrees. Shading is smooth inside a face, so the block's edges drew serrated, and no boundary could
name one of those faces.

The repair is local to such a region. A *flat* region is a set of triangles joined edge to edge
whose normals agree to :data:`FLAT_LINK_DEG` with their neighbour and to :data:`FLAT_SPREAD_DEG`
with the region's mean, that is at least :data:`FLAT_WIDTH_CELLS` cells wide, and that ends in
sharp edges along most of its border (the facet strips of a tessellated cylinder are flat too, but
run on smoothly into each other: :data:`SMOOTH_BORDER_SHARE`). A face that holds two flat regions,
or a flat region beside a curved one that is a surface in its own right, is split: each flat region
becomes a face, and the rest is split again by the feature angle among itself. What is left over
as narrow strips is reported to the caller, which merges them like any other sliver.

Import rule: numpy and ``stokes.geometry.selection`` only.
"""

from __future__ import annotations

from typing import Any

import numpy as np

FLAT_LINK_DEG = 0.1
"""Two neighbouring triangles of one flat region agree to this angle. Dual contouring puts a flat
face's vertices on its plane exactly, so its triangles agree to float precision; a tessellated
curve of radius R at cell h turns h / R between strips, above this for any R below 570 cells."""
FLAT_SPREAD_DEG = 0.5
"""No triangle of a flat region is further than this from the region's mean normal (the
viewport's own test for a planar face)."""
FLAT_WIDTH_CELLS = 2.0
"""A flat region is at least this many cells wide (twice its area over its border length)."""
SMOOTH_BORDER_SHARE = 0.5
"""A flat region is a face of its own only when less than this share of its border runs on into
its neighbour below the feature angle. A tessellated cylinder is made of flat strips several cells
wide where the mesher simplified it; they continue each other along their whole length. A block's
face ends in sharp edges all round, bar the stretch where another surface leaked into it."""
SURFACE_WIDTH_CELLS = 1.0
"""A curved remainder at least this wide is a surface of its own, not a sliver along an edge."""


def separate_flat_faces(  # noqa: PLR0915 - one pass over arrays every step shares
    groups: Any,  # Any: I64 (m,), the face of every triangle
    normal: Any,  # Any: F64 (m, 3), unit triangle normals (zero for a degenerate triangle)
    area: Any,  # Any: F64 (m,)
    left: Any,  # Any: I64 (e,), the two triangles of every shared edge
    right: Any,
    span: Any,  # Any: F64 (e,), the length of every shared edge
    cell: float,
    angle_deg: float,
    on_box: Any = None,  # Any: bool (m,), the triangle lies on a side of the body's bounding box
) -> tuple[Any, Any]:  # Any: I64 (m,) faces, I64 the faces that are leftover strips
    """``groups`` with every leaked face split, and the ids of the narrow leftovers.

    Faces that hold no leak keep their id, so a mesh without one comes back unchanged with no
    leftovers."""
    from stokes.geometry.selection import components

    count = int(groups.shape[0])
    none = np.zeros(0, dtype=np.int64)
    if count == 0 or left.shape[0] == 0:
        return groups, none
    agree = np.einsum("ij,ij->i", normal[left], normal[right])
    inside = groups[left] == groups[right]
    strict = inside & (agree > np.cos(np.radians(FLAT_LINK_DEG)))
    patch = components(np.stack([left[strict], right[strict]], axis=1), count)
    patch = np.unique(patch, return_inverse=True)[1].reshape(-1)
    patches = int(patch.max()) + 1

    patch_area = np.bincount(patch, area, minlength=patches)
    mean = np.stack(
        [np.bincount(patch, normal[:, k] * area, minlength=patches) for k in range(3)], 1
    )
    length = np.linalg.norm(mean, axis=1)
    mean = np.divide(mean, length[:, None], out=np.zeros_like(mean), where=length[:, None] > 0)
    live = area > 0
    spread = np.ones(patches)
    np.minimum.at(spread, patch[live], np.einsum("ij,ij->i", normal[live], mean[patch[live]]))
    edge = patch[left] != patch[right]
    border = np.bincount(patch[left][edge], span[edge], minlength=patches) + np.bincount(
        patch[right][edge], span[edge], minlength=patches
    )
    width = np.divide(2.0 * patch_area, border, out=np.full(patches, np.inf), where=border > 0)
    # How much of a patch's border runs on smoothly into its neighbour: nearly all of it for a
    # facet strip of a tessellated cylinder, a small share for a face of a block.
    smooth = edge & (agree > np.cos(np.radians(angle_deg)))
    smooth_border = np.bincount(patch[left][smooth], span[smooth], minlength=patches) + np.bincount(
        patch[right][smooth], span[smooth], minlength=patches
    )
    flat = (
        (spread >= np.cos(np.radians(FLAT_SPREAD_DEG)))
        & (width >= FLAT_WIDTH_CELLS * cell)
        & (length > 0)
    )
    ends_sharp = smooth_border < SMOOTH_BORDER_SHARE * border
    if on_box is not None:
        # A flat region lying on the body's bounding box is a face however its rim runs on: a
        # lattice channel cut by a plane meets its mouth at a shallow angle over more than half
        # the rim (measured 2026-11-20 on a gyroid), and a facet of a curved surface never lies
        # on the box (it sits a sagitta inside it).
        boxed = np.ones(patches, dtype=bool)
        np.logical_and.at(boxed, patch, on_box)
        ends_sharp |= boxed
    flat &= ends_sharp
    if not flat.any():
        return groups, none

    faces = int(groups.max()) + 1
    is_flat = flat[patch]
    # Flat regions per face (a patch lies inside one face: strict links never leave it).
    flat_patches = np.flatnonzero(flat)
    face_of_patch = np.zeros(patches, dtype=np.int64)
    face_of_patch[patch] = groups
    flats_in = np.bincount(face_of_patch[flat_patches], minlength=faces)
    # The curved rest of every face that has a flat region: is any of it a surface of its own?
    rest = ~is_flat & (flats_in[groups] > 0)
    soft = inside & rest[left] & rest[right] & (agree > np.cos(np.radians(angle_deg)))
    piece = components(np.stack([left[soft], right[soft]], axis=1), count)
    piece = np.unique(piece, return_inverse=True)[1].reshape(-1)
    pieces = int(piece.max()) + 1
    piece_area = np.bincount(piece[rest], area[rest], minlength=pieces)
    cut = piece[left] != piece[right]
    use = cut & (rest[left] | rest[right])
    piece_border = np.zeros(pieces)
    np.add.at(piece_border, piece[left][use & rest[left]], span[use & rest[left]])
    np.add.at(piece_border, piece[right][use & rest[right]], span[use & rest[right]])
    piece_width = np.divide(
        2.0 * piece_area, piece_border, out=np.zeros(pieces), where=piece_border > 0
    )
    surface = piece_width >= SURFACE_WIDTH_CELLS * cell
    has_surface = np.zeros(faces, dtype=bool)
    has_surface[np.unique(groups[rest & surface[piece]])] = True
    leaked = (flats_in >= 2) | ((flats_in >= 1) & has_surface)
    if not leaked.any():
        return groups, none

    out = groups.copy()
    touched = leaked[groups]
    flat_here = touched & is_flat
    rest_here = touched & ~is_flat
    # New ids after the existing ones: one per flat region, one per curved piece.
    flat_ids = np.unique(patch[flat_here], return_inverse=True)[1].reshape(-1)
    out[flat_here] = faces + flat_ids
    after = faces + (int(flat_ids.max()) + 1 if flat_ids.size else 0)
    piece_keys, piece_ids = np.unique(piece[rest_here], return_inverse=True)
    out[rest_here] = after + piece_ids.reshape(-1)
    narrow = ~surface[piece_keys]
    return out, (after + np.flatnonzero(narrow)).astype(np.int64)
