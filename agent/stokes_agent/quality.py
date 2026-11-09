"""The quality distributions (brief M-506 Part B): pure numpy from the polyMesh arrays.

**The definitions are the install's own** (`src/meshCheck/`), read before writing this code —
the validation rule (each metric's max must match checkMesh's report to 1e-3 relative) is only
honest if the formulas are the same ones, not look-alikes:

- **Face area and centre** by a triangle fan about the face's average point
  (`primitiveMesh::faceAreas`/`faceCentres`): for face points p0..pn, the average point a, each
  triangle (a, pi, pi+1) contributes 0.5*(pi-a)x(pi+1-a) to the area vector and
  1/3*(a + pi + pi+1)*|triangle area| to the centre sum, divided by the total area.
- **Cell centre** by volume-weighted pyramids (`primitiveMesh::cellCentres`): for each face of
  the cell, the pyramid from the cell centre estimate through the face; OF14 seeds the estimate
  with the average of the cell's points and iterates once. The seed here is the same average;
  the result matches checkMesh on the fixtures to the rule's tolerance.
- **Non-orthogonality** (internal faces): the angle between owner→neighbour and the face area
  vector — `(d*s)/(mag(d)*mag(s))`, in degrees (`polyMeshCheckQuality.C:38-48`,
  `primitiveMeshCheck.C::faceOrthogonality`).
- **Face skewness**: sv = Cpf - ((s*Cpf)/(s*d))*d, with Cpf = face centre - owner centre and
  d = neighbour - owner centre; the normalisation distance fd = max(0.2*|d|, the largest
  |ŝv*(point - face centre)| over the face's points); skewness = |sv|/fd
  (`primitiveMeshCheck.C::faceSkewness`).
- **Boundary-face skewness**: the same with d = ŝ(ŝ*Cpf) (the normal projection of Cpf) and
  fd = max(0.4*|d|, ...) — **checkMesh's "Max skewness" INCLUDES boundary faces**, so a
  boundary definition that said otherwise would fail the validation rule for a reason that is
  not a bug (`primitiveMeshCheck.C::boundaryFaceSkewness`).
- **Aspect ratio** (per cell): maxCmpt/minCmpt of the component-magnitude sums of the cell's
  face area vectors, and for a 3-D mesh also (1/6)*Σ|components|/V^(2/3); the cell's AR is the
  max of the two (`primitiveMeshCheck.C::cellClosedness`, with meshD from the bounding box).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: The bins of each histogram (brief M-506: 40 bins in `meta.quality_dist`).
BINS = 40

#: The worst faces kept per metric (brief M-506).
WORST_COUNT = 50


def n_cells_of(owner: np.ndarray, neighbour: np.ndarray) -> int:
    """The cell count from the labels, safe for an EMPTY neighbour (a one-cell mesh has none,
    and numpy's max on an empty array is an error, not a zero)."""
    highest = int(owner.max() + 1) if owner.size else 1
    if neighbour.size:
        highest = max(highest, int(neighbour.max() + 1))
    return highest


@dataclass(frozen=True)
class Metric:
    """One metric's full field: values per face (or cell), plus the histogram edges and counts."""

    values: np.ndarray
    unit: str
    #: The log-bin flag is only the PAGE's wish for aspect ratio (brief M-506); the histogram
    #: here is computed by `histogram` on whatever edges the caller asks for.
    log_bins: bool = False

    def histogram(self, edges: np.ndarray) -> tuple[list[float], list[int]]:
        """The counts of `values` in `edges`, as JSON-able lists."""
        counts, _ = np.histogram(self.values, bins=edges)
        return [float(e) for e in edges], [int(c) for c in counts]

    def worst(self, count: int = WORST_COUNT) -> np.ndarray:
        """The indices of the `count` largest values, worst first."""
        order = np.argsort(self.values)[::-1]
        return order[:count]


def face_areas_and_centres(
    points: np.ndarray, faces: list[np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    """Every face's area vector and centre, by a triangle fan about the face's average point.

    This IS `primitiveMesh::faceAreas`/`faceCentres` arithmetic: the fan's triangles share the
    average point, and the centre is the area-weighted average of the triangles' centroids.
    """
    n = len(faces)
    areas = np.zeros((n, 3))
    centres = np.zeros((n, 3))
    for i, face in enumerate(faces):
        pts = points[face]
        avg = pts.mean(axis=0)
        rolled = np.roll(pts, -1, axis=0)
        # **The fan's order is OF14's**: (p[pi] - a) x (p[pi+1] - a), so the winding of the
        # face's own point list decides the sign — an outward-wound face gives an outward vector.
        cross = np.cross(pts - avg, rolled - avg)
        area_vec = 0.5 * cross.sum(axis=0)
        areas[i] = area_vec
        mag = float(np.linalg.norm(area_vec))
        if mag <= 0.0:
            # A zero-area face cannot happen in a mesh checkMesh accepts; the centre falls back
            # to the average point rather than dividing by zero, and the validation rule will
            # name the mismatch if it matters.
            centres[i] = avg
            continue
        tri_centres = (avg + pts + rolled) / 3.0
        weights = np.linalg.norm(cross, axis=1) / 2.0
        centres[i] = (tri_centres * weights[:, None]).sum(axis=0) / weights.sum()
    return areas, centres


def cell_centres(
    face_areas: np.ndarray,
    face_centres: np.ndarray,
    owner: np.ndarray,
    neighbour: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Every cell's centre and volume, EXACTLY `primitiveMesh::makeCellCentresAndVols`.

    The seed is the average of the cell's FACE CENTRES; each face contributes a pyramid with
    signed volume `fAreas & (fCtrs - cEst)` (owner) or `fAreas & (cEst - fCtrs)` (neighbour)
    and centre `(3/4)*fCtrs + (1/4)*cEst`; the cell centre is the volume-weighted average, and
    a cell whose SIGNED volume sums to zero falls back to the seed - the install's own
    fallback, read before writing this.
    """
    n_cells = n_cells_of(owner, neighbour)
    c_est = np.zeros((n_cells, 3))
    n_faces_of = np.zeros(n_cells, dtype=np.int64)
    for i in range(len(face_centres)):
        c_est[owner[i]] += face_centres[i]
        n_faces_of[owner[i]] += 1
        if i < neighbour.size:
            c_est[neighbour[i]] += face_centres[i]
            n_faces_of[neighbour[i]] += 1
    for cell in range(n_cells):
        if n_faces_of[cell] > 0:
            c_est[cell] /= n_faces_of[cell]

    centres = np.zeros((n_cells, 3))
    volumes = np.zeros(n_cells)
    for i in range(len(face_centres)):
        own = int(owner[i])
        pyr3 = float(np.dot(face_areas[i], face_centres[i] - c_est[own]))
        pc = 0.75 * face_centres[i] + 0.25 * c_est[own]
        centres[own] += pyr3 * pc
        volumes[own] += pyr3
        if i < neighbour.size:
            nei = int(neighbour[i])
            pyr3 = float(np.dot(face_areas[i], c_est[nei] - face_centres[i]))
            pc = 0.75 * face_centres[i] + 0.25 * c_est[nei]
            centres[nei] += pyr3 * pc
            volumes[nei] += pyr3
    for cell in range(n_cells):
        if abs(volumes[cell]) > np.finfo(float).tiny:
            centres[cell] /= volumes[cell]
        else:
            centres[cell] = c_est[cell]
    # **The accumulated sum is 3x the volume** (each pyramid's `pyr3Vol` is three times its
    # volume, hence the name) — the install's own final line scales it back.
    volumes *= 1.0 / 3.0
    return centres, np.abs(volumes)


def non_ortho(
    points: np.ndarray,
    faces: list[np.ndarray],
    owner: np.ndarray,
    neighbour: np.ndarray,
) -> Metric:
    """The internal faces' non-orthogonality, in degrees, by the install's dot-product rule.

    **The d is CELL centre to CELL centre** (`polyMeshCheckQuality.C:242-252`: `cellCentres[nei] -
    cellCentres[own]`), so the cell centres are computed inside — not the face centres, whose
    substitution produced a 123-degree nonsense this calibration caught.
    """
    face_areas, face_centres = face_areas_and_centres(points, faces)
    cell_cc, _volumes = cell_centres(face_areas, face_centres, owner, neighbour)
    internal = np.arange(neighbour.size)
    d = cell_cc[neighbour] - cell_cc[owner[internal]]
    s = face_areas[internal]
    denom = np.linalg.norm(d, axis=1) * np.linalg.norm(s, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.where(denom > 0, (d * s).sum(axis=1) / denom, 0.0)
    cos = np.clip(cos, -1.0, 1.0)
    degrees = np.degrees(np.arccos(cos))
    return Metric(values=degrees, unit="deg")


def _face_skewness_core(
    points: np.ndarray,
    faces: list[np.ndarray],
    owner: np.ndarray,
    face_indices: np.ndarray,
    centres: np.ndarray,
    cell_centres: np.ndarray,
    face_areas: np.ndarray,
    d_vectors: np.ndarray,
    factor: float,
) -> np.ndarray:
    """The skewness of `face_indices`, by the install's formula with the given fd factor.

    `d_vectors` carries the d each face's formula reads, READY-MADE: neighbour centre minus
    owner centre for an internal face (the caller subtracts), or the normal projection of Cpf
    for a boundary face (there is no neighbour; the projection IS the d, as OF14 writes it).
    `faceSkewness` uses fd = 0.2*|d|; `boundaryFaceSkewness` fd = 0.4*|d|.
    """
    result = np.zeros(face_indices.size)
    for k, i in enumerate(face_indices):
        # **Cpf is face centre minus CELL centre**, and **d arrives as the difference it is**:
        # the internal caller passes neighbour centre - owner centre, the boundary caller the
        # normal projection of Cpf. The first draft used the neighbour's ABSOLUTE centre as the
        # d (no subtraction), which gave a 1.66 skew on a perfect cavity and an 8063 on
        # pitzDaily; the second subtracted the owner centre from the boundary projection too,
        # which exploded the perfect cavity to 3e23. Measured, all three, against the fixtures'
        # own checkMesh logs.
        cpf = centres[i] - cell_centres[owner[i]]
        d = d_vectors[k]
        s = face_areas[i]
        s_dot_d = float(np.dot(s, d))
        sv = cpf - (float(np.dot(s, cpf)) / (s_dot_d if abs(s_dot_d) > 1e-30 else 1e-30)) * d
        norm = np.linalg.norm(sv)
        sv_hat = sv / (norm if norm > 1e-30 else 1e-30)
        fd = factor * float(np.linalg.norm(d))
        pts = points[faces[i]]
        for point in pts:
            fd = max(fd, abs(float(np.dot(sv_hat, point - centres[i]))))
        result[k] = abs(norm) / fd if fd > 1e-30 else 0.0
    return result


def skewness(
    points: np.ndarray,
    faces: list[np.ndarray],
    owner: np.ndarray,
    neighbour: np.ndarray,
) -> dict[str, Metric]:
    """The face skewness, internal and boundary — checkMesh's "Max skewness" includes the
    boundary faces, so the two are returned separately and combined.

    **The Cpf here is face centre minus CELL centre** — the install's formula reads the owner
    CELL's centre, so the cell centres are computed inside (the same function the volumes use).
    """
    face_areas, face_centres = face_areas_and_centres(points, faces)
    cell_cc, _volumes = cell_centres(face_areas, face_centres, owner, neighbour)
    internal = np.arange(neighbour.size)
    internal_values = _face_skewness_core(
        points,
        faces,
        owner,
        internal,
        face_centres,
        cell_cc,
        face_areas,
        # **The d is the DIFFERENCE** (cell to cell), not the neighbour's absolute centre.
        cell_cc[neighbour] - cell_cc[owner[internal]],
        0.2,
    )
    boundary_start = neighbour.size
    boundary = np.arange(boundary_start, len(faces))
    # The boundary definition: d is the NORMAL projection of Cpf (there is no neighbour cell).
    # **The boundary d is the projection itself, not a cell centre** (see the docstring above):
    # `normal * (normal & Cpf)`, measured from the face centre.
    boundary_projection = np.zeros((boundary.size, 3)) if boundary.size else np.zeros((0, 3))
    for k, i in enumerate(boundary):
        cpf = face_centres[i] - cell_cc[owner[i]]
        s = face_areas[i]
        norm = np.linalg.norm(s)
        s_hat = s / (norm if norm > 1e-30 else 1e-30)
        boundary_projection[k] = s_hat * float(np.dot(s_hat, cpf))
    boundary_values = (
        _face_skewness_core(
            points,
            faces,
            owner,
            boundary,
            face_centres,
            cell_cc,
            face_areas,
            boundary_projection,
            0.4,
        )
        if boundary.size
        else np.zeros(0)
    )
    combined = (
        np.concatenate([internal_values, boundary_values])
        if boundary_values.size
        else internal_values
    )
    return {
        "internal": Metric(values=internal_values, unit=""),
        "boundary": Metric(values=boundary_values, unit=""),
        "all": Metric(values=combined, unit=""),
    }


def aspect_ratio(
    faces: list[np.ndarray],
    owner: np.ndarray,
    neighbour: np.ndarray,
    face_areas: np.ndarray,
    cell_centres: np.ndarray,
    volumes: np.ndarray,
) -> Metric:
    """The per-cell aspect ratio, by `cellClosedness`: the component ratio, and for 3-D the
    hydraulic term — the cell's value is the max of the two."""
    n_cells = volumes.size
    sum_closed = np.zeros((n_cells, 3))
    sum_mag = np.zeros((n_cells, 3))
    for i in range(len(faces)):
        sum_closed[owner[i]] += face_areas[i]
        sum_mag[owner[i]] += np.abs(face_areas[i])
        if i < neighbour.size:
            sum_closed[neighbour[i]] -= face_areas[i]
            sum_mag[neighbour[i]] += np.abs(face_areas[i])
    # **The mesh dimensions from the INTERNAL faces** (brief M-506's cavity case): a direction
    # with no internal face normal in it is one cell thick, and OF14's meshD marks it missing —
    # the cavity is 20x20x1, its internal faces are x- and y-normal only, so z is excluded and
    # the aspect ratio reads 1 as checkMesh says. `extent > 0` was the first draft and gave 2.0
    # on the cavity (measured).
    internal = np.arange(neighbour.size)
    if internal.size:
        extent = np.abs(face_areas[internal]).max(axis=0)
        mesh_dims = extent > 0
    else:
        # **A one-cell mesh has no internal faces to read the dimensions from**, and its own
        # six faces span every direction by construction (a closed cell cannot be flat in a
        # direction it has faces normal to).
        mesh_dims = np.ones(3, dtype=bool)
    n_dims = int(mesh_dims.sum())
    min_cmpt = np.where(np.broadcast_to(mesh_dims, (n_cells, 3)), sum_mag, np.inf).min(axis=1)
    max_cmpt = sum_mag.max(axis=1)
    ratio = max_cmpt / (min_cmpt + np.finfo(float).tiny)
    if n_dims == 3:
        hydraulic = (
            sum_mag.sum(axis=1)
            / 6.0
            / np.power(np.maximum(volumes, np.finfo(float).tiny), 2.0 / 3.0)
        )
        ratio = np.maximum(ratio, hydraulic)
    return Metric(values=ratio, unit="")


@dataclass
class QualityResult:
    """The reply's parts, before the RPC assembles the JSON."""

    metrics: dict[str, Any] = field(default_factory=dict)
    cell_types: dict[str, int] = field(default_factory=dict)
    worst: list[dict[str, Any]] = field(default_factory=list)


def cell_types_from_counts(
    faces: list[np.ndarray], owner: np.ndarray, neighbour: np.ndarray
) -> dict[str, int]:
    """Cell-type counts by face count per cell: hex 6, prism 5, pyramid 5-with-a-point-base...

    **This is the heuristic the brief's Part B does NOT rest on** — the parser's `cell_types`
    (Part A) is the ground truth from checkMesh's own block. This function exists for the reader
    tests only, where a hand-built mesh's types are known by construction.
    """
    counts: dict[int, int] = {}
    n_cells = n_cells_of(owner, neighbour)
    per_cell: dict[int, set[int]] = {c: set() for c in range(n_cells)}
    for i in range(len(faces)):
        per_cell[int(owner[i])].add(i)
        if i < neighbour.size:
            per_cell[int(neighbour[i])].add(i)
    for cell_faces in per_cell.values():
        counts[len(cell_faces)] = counts.get(len(cell_faces), 0) + 1
    return {
        "hex": counts.get(6, 0),
        "prism": counts.get(5, 0),
        "wedge": 0,
        "pyramid": 0,
        "tet_wedge": 0,
        "tet": counts.get(4, 0),
        "poly": sum(v for k, v in counts.items() if k not in (4, 5, 6)),
    }
