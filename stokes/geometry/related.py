"""The predicates of ``tag.related`` (ADR-0022 decision 6.4), over the fingerprints and the
facets that :func:`stokes.geometry.fingerprints.fingerprints_of` reads from a part's mesh.

Pure numpy, no OCP, no session: each function takes arrays indexed by face (or by facet) and
returns a mask or a fitted quantity; the commands in ``stokes/api/commands/selection.py``
assemble these over the parts to search and drop the picked faces.

The relations (their default tolerances live with the commands):

- ``same_area``: the area within ``tol`` (relative to a picked face's area) of the area of any
  picked face.
- ``parallel``: the angle between the mean normals below ``tol`` radians; both normals must
  read as open faces (length above :data:`OPEN`).
- ``coplanar``: ``parallel`` at the fixed angle, and the centroid within ``tol`` of the part's
  bounding-box diagonal of the plane of a picked face.
- ``coaxial``: the picked faces' facets wrap one axis, fitted as the eigenvector of the
  smallest eigenvalue of their area-weighted normal covariance (the mean outer product, whose
  scatter is zero along a cylinder's axis); a candidate face has at least :data:`AXIS_FRACTION`
  of its facet area within :data:`AXIS_ANGLE` radians of perpendicular to the axis, and its
  centroid at the picked radius, within ``tol`` (relative). The picked radius is the
  area-weighted mean of the picked faces' centroid distances to the axis line, so split (arc)
  faces match the counterparts of their cylinder; a whole cylindrical face keeps its centroid
  on the axis and matches only other whole faces on the same axis. The axis line itself comes
  from :func:`axis_point`, the least-squares intersection of the facets' normal lines: the
  mean of their centroids sits on the axis only when the facets are symmetrically spread, and
  misses it on one half of a cylinder. Facets that share one direction (one plane) wrap no
  axis (:func:`wraps_axis`), the fit would be an arbitrary direction, and the pick is coaxial
  with nothing. Cylinder detection from a tessellation is approximate (ADR-0022 6.4); the
  B-rep surface type refines it.

Import rule: numpy only; no OCP.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
from numpy.typing import NDArray

F64 = NDArray[np.float64]
I64 = NDArray[np.int64]
Mask = NDArray[np.bool_]

_TINY = 1e-30
OPEN = 0.5
"""Mean-normal length that still reads as an open (planar) face."""
COPLANAR_ANGLE = 0.035
"""Radians: coplanar keeps this angle; its tolerance is the plane offset."""
AXIS_ANGLE = 0.035
"""Radians: a facet within this of perpendicular to the axis counts as cylindrical."""
AXIS_FRACTION = 0.95
"""Share of a face's facet area that must be cylindrical for it to match coaxial."""
AXIS_RATIO = 0.05
"""smallest/largest eigenvalue above which a pick is not cylindrical."""
BLOCK = 4_000_000
"""Most elements of a candidates-by-picked temporary built at one time (32 MB of float64)."""


def _blocks(rows: int, width: int) -> Iterator[slice]:
    """Row slices sized so that a (rows, width) temporary stays within :data:`BLOCK` elements:
    a big pick over a big part must not allocate faces x picked in one piece."""
    step = max(1, BLOCK // max(1, width))
    for lo in range(0, rows, step):
        yield slice(lo, lo + step)


def same_area(areas: F64, picked: F64, tol: float) -> Mask:
    """Faces whose area is within ``tol`` (relative to a picked face's area) of the area of any
    picked face; a picked face without area gives no reference."""
    areas = np.asarray(areas, dtype=np.float64)
    picked = np.atleast_1d(np.asarray(picked, dtype=np.float64))
    picked = picked[picked > 0.0]
    if picked.size == 0:
        return np.zeros(areas.shape, dtype=bool)
    off = np.empty(areas.shape[0])
    for rows in _blocks(areas.shape[0], picked.size):
        off[rows] = (np.abs(areas[rows, None] - picked[None, :]) / picked[None, :]).min(axis=1)
    return off <= tol


def parallel(normals: F64, picked: F64, tol: float) -> Mask:
    """Faces whose mean normal makes an angle below ``tol`` radians with a picked face's mean
    normal; both normals must have length above :data:`OPEN` (open faces)."""
    normals = np.atleast_2d(np.asarray(normals, dtype=np.float64))
    picked = np.atleast_2d(np.asarray(picked, dtype=np.float64))
    out = np.zeros(normals.shape[0], dtype=bool)
    p_len = np.linalg.norm(picked, axis=1)
    usable = p_len > OPEN
    if not usable.any():
        return out
    p = picked[usable] / p_len[usable, None]
    n_len = np.linalg.norm(normals, axis=1)
    open_ = n_len > OPEN
    if not open_.any():
        return out
    unit = normals[open_] / n_len[open_, None]
    best = np.empty(unit.shape[0])
    for rows in _blocks(unit.shape[0], p.shape[0]):
        best[rows] = np.clip(unit[rows] @ p.T, -1.0, 1.0).max(axis=1)
    out[open_] = best > np.cos(tol)
    return out


def coplanar(
    normals: F64,
    centroids: F64,
    picked_normals: F64,
    picked_centroids: F64,
    scale: float,
    offset_tol: float,
    angle: float = COPLANAR_ANGLE,
) -> Mask:
    """``parallel`` at the fixed ``angle``, and the centroid within ``offset_tol`` of the
    part's bounding-box diagonal of the plane of a picked face."""
    base = parallel(normals, picked_normals, angle)
    p_len = np.linalg.norm(picked_normals, axis=1)
    usable = p_len > OPEN
    if not base.any() or not usable.any():
        return np.zeros_like(base)
    p = picked_normals[usable] / p_len[usable, None]
    near = np.full(
        base.shape[0], np.inf
    )  # offset from the nearest picked plane, parallel faces only
    parallel_faces = np.flatnonzero(base)
    for rows in _blocks(parallel_faces.size, 3 * p.shape[0]):
        faces = parallel_faces[rows]
        diff = centroids[faces, None, :] - picked_centroids[usable]  # (faces, picked, 3)
        near[faces] = np.abs(np.einsum("fpj,pj->fp", diff, p)).min(axis=1)
    return base & (near < offset_tol * scale)


def _covariance(normals: F64, areas: F64) -> F64 | None:
    """The area-weighted covariance of the facet normals (the mean outer product), or None
    without data: every normal of a cylinder lies in the plane perpendicular to its axis, so
    that covariance scatters not at all along the axis."""
    normals = np.asarray(normals, dtype=np.float64)
    areas = np.asarray(areas, dtype=np.float64)
    if normals.size == 0 or not (areas > 0.0).any():
        return None
    return (normals.T * areas) @ normals / float(areas.sum())


def axis_of(normals: F64, areas: F64) -> tuple[F64, float]:
    """The axis the facets wrap, and its eigenvalue ratio (smallest over largest).

    The axis is the eigenvector of the smallest eigenvalue of :func:`_covariance`; a pick
    whose ratio exceeds :data:`AXIS_RATIO` is not cylindrical. Facets without area give no
    data and an un-cylindrical ratio. The covariance is positive semi-definite, so a ratio
    below zero is float noise and reads as zero."""
    cov = _covariance(normals, areas)
    if cov is None:
        return np.zeros(3), 1.0
    values, vectors = np.linalg.eigh(cov)
    ratio = max(0.0, float(values[0] / values[-1])) if values[-1] > 0.0 else 1.0
    return np.ascontiguousarray(vectors[:, 0]), ratio


def wraps_axis(normals: F64, areas: F64) -> bool:
    """Whether the facets spread around an axis instead of sharing one direction.

    The fit of :func:`axis_of` needs the normals to span the plane perpendicular to the
    axis: the middle eigenvalue of their covariance then exceeds :data:`AXIS_RATIO` of the
    largest. Facets of one plane share a normal, so any direction in that plane would fit
    the smallest eigenvalue equally well, the picked radius falls to zero, and the pick is
    coaxial with nothing; without area the answer is also False."""
    cov = _covariance(normals, areas)
    if cov is None:
        return False
    values = np.linalg.eigvalsh(cov)
    return bool(values[1] > AXIS_RATIO * values[-1])


def axis_point(normals: F64, centroids: F64, areas: F64, axis: F64) -> F64:
    """A point on the fitted axis: the least-squares intersection of the facets' normal lines,
    projected onto the plane through the origin perpendicular to ``axis``.

    Every normal line of a cylinder passes through its axis, so the axis solves
    ``(sum w) x - (sum w n n^T) x = sum w (p - (p.n) n)`` for the facet areas ``w``. The mean
    of the facet centroids, projected the same way, is on the axis only when the facets are
    symmetrically spread around it, and sits ``2R/pi`` off it on one half of a cylinder.
    Projecting keeps the component distances ignore: a distance to the axis line does not
    change along the axis."""
    n = np.asarray(normals, dtype=np.float64)
    p = np.asarray(centroids, dtype=np.float64)
    w = np.asarray(areas, dtype=np.float64)
    if p.size == 0 or not (w > 0.0).any():
        return np.zeros(3)
    total = float(w.sum())
    scatter = (n.T * w) @ n
    rhs = (p * w[:, None]).sum(axis=0) - (n * (w * (p * n).sum(axis=1))[:, None]).sum(axis=0)
    point = np.linalg.lstsq(total * np.eye(3) - scatter, rhs, rcond=None)[0]
    return point - float(point @ np.asarray(axis, dtype=np.float64)) * axis


def distance_to_axis(centroids: F64, origin: F64, axis: F64) -> F64:
    """The perpendicular distance of each point from the line through ``origin`` in direction
    ``axis``."""
    d = np.asarray(centroids, dtype=np.float64) - np.asarray(origin, dtype=np.float64)
    return np.linalg.norm(d - (d @ axis)[:, None] * axis, axis=1)


def picked_radius(face_centroids: F64, face_areas: F64, origin: F64, axis: F64) -> float:
    """The picked radius: the area-weighted mean of the picked faces' centroid distances to the
    axis line, the same quantity the candidate test measures on its faces."""
    w = np.asarray(face_areas, dtype=np.float64)
    if face_centroids.size == 0 or not (w > 0.0).any():
        return 0.0
    d = distance_to_axis(face_centroids, origin, axis)
    return float((d * w).sum() / w.sum())


def coaxial(
    normals: F64,
    areas: F64,
    face: I64,
    face_centroids: F64,
    face_count: int,
    axis: F64,
    origin: F64,
    radius: float,
    tol: float,
    angle: float = AXIS_ANGLE,
    fraction: float = AXIS_FRACTION,
) -> Mask:
    """Faces whose facets wrap the axis line: at least ``fraction`` of the face's facet area has
    its normal within ``angle`` radians of perpendicular to ``axis``, and the face's centroid is
    within ``tol`` (relative to ``radius``) of the line through ``origin`` in direction
    ``axis``. ``face`` names, per facet, which candidate face it belongs to; a facet named -1
    (a mesh without face ids) gives no data."""
    if face_count == 0:
        return np.zeros(0, dtype=bool)
    w = np.asarray(areas, dtype=np.float64)
    face_ids = np.asarray(face, dtype=np.int64)
    named = face_ids >= 0
    ids, weights = face_ids[named], w[named]
    normal = np.asarray(normals, dtype=np.float64)[named]
    total = np.bincount(ids, weights=weights, minlength=face_count)
    if ids.size == 0 or not (total > 0.0).any():
        return np.zeros(face_count, dtype=bool)
    perp = np.abs(normal @ axis) <= np.sin(angle)
    around = np.bincount(ids, weights=weights * perp, minlength=face_count)
    frac = np.zeros(face_count)
    np.divide(around, total, out=frac, where=total > 0.0)
    dist = distance_to_axis(face_centroids, origin, axis)
    band = max(tol * radius, 1e-12)
    return (frac >= fraction) & (np.abs(dist - radius) <= band) & (total > 0.0)
