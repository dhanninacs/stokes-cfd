"""How flat a mesh's symmetry planes came out, measured from the patch's own points.

## Why this exists

**Measured on the mixing elbow (2026-11-08).** The CAD's symmetry face is exactly the plane
z = 0, and the surface Stokes hands the mesher holds it to the last digit. cfMesh's tetrahedral
and polyhedral fills still left 57-58 points along the edge where a pipe wall meets that face
below the plane: up to 0.4 mm off on the polyhedral mesh and 2.8 mm on the tetrahedral one, with
5 mm cells. `checkMesh` called the tetrahedral mesh "Mesh OK". k-omega SST on the polyhedral mesh
and realizable k-epsilon on the tetrahedral one then diverged from exactly those cells (velocity
ten times the inlet speed within twenty iterations), while the same SST case converged on the
snappyHexMesh and cfMesh hex-dominant meshes and on the polyhedral mesh once the plane was
flattened. Nothing else in a mesh report says this, so the mesh service measures it.

## What is done about it

The polyhedral type is flattened by `improveSymmetryPlanes`, and the tetrahedral and hex-dominant
types by the agent's own `flatten` action (`stokes_agent.flatten`), as far as the cells around each
point allow. This module then measures what came out, so a plane that is still bent says so.

## How

A symmetry patch is planar when a plane fitted to its points holds nearly all of them to within
`PLANAR_TOLERANCE` of the patch's extent; a patch that is not (a curved symmetry surface, which
OpenFOAM's `symmetry` type allows) has no plane to be off, and is not reported. The fit is
repeated on the closest 90 % of the points, so a few stray points do not tilt the plane they are
measured against. The positions come from the surface artifact as float32: at a 0.2 m coordinate
that is about 2e-8 m of noise, well under the tolerance.

Pure numpy; no OpenFOAM, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: A point further than this share of the patch's extent from the fitted plane is "off" it.
PLANAR_TOLERANCE = 1e-6

#: The share of the points the plane must hold for the patch to count as a plane at all.
PLANAR_SHARE = 0.9

#: A plane whose worst point is further off than this share of the cell size gets a warning. The
#: flattened elbow mesh was 0.04 mm off with 5 mm cells (0.8 %) and converged; the polyhedral mesh
#: that diverged was 0.4 mm off (8 %).
WARN_FRACTION = 0.02

#: The patch types whose points should lie on a plane.
SYMMETRY_TYPES = frozenset({"symmetry", "symmetryPlane"})


@dataclass(frozen=True)
class Flatness:
    """One planar symmetry patch: how many of its points left the plane, and how far."""

    patch: str
    points: int
    off: int
    max_offset: float

    def to_dict(self) -> dict[str, float | int | str]:
        """The record stored in the mesh result's `meta.symmetry_flatness`."""
        return {
            "patch": self.patch,
            "points": self.points,
            "off": self.off,
            "max_offset": self.max_offset,
        }


def _fit(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A plane through ``points``: its centroid and unit normal (least squares)."""
    centre = points.mean(axis=0)
    _u, _s, vt = np.linalg.svd(points - centre, full_matrices=False)
    return centre, vt[-1]


def offsets(positions: np.ndarray) -> np.ndarray | None:
    """Each point's distance from the patch's plane, or None when the patch is not a plane."""
    points = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    if len(points) < 4 or not np.isfinite(points).all():
        return None
    extent = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    if extent <= 0.0:
        return None
    centre, normal = _fit(points)
    distance = np.abs((points - centre) @ normal)
    near = points[distance <= np.quantile(distance, PLANAR_SHARE)]
    if len(near) >= 3:
        centre, normal = _fit(near)
        distance = np.abs((points - centre) @ normal)
    if float(np.quantile(distance, PLANAR_SHARE)) > PLANAR_TOLERANCE * extent:
        return None
    result: np.ndarray = np.asarray(distance, dtype=np.float64)
    return result


def measure(patch: str, positions: np.ndarray) -> Flatness | None:
    """The flatness of one symmetry patch, or None when it is not planar to begin with."""
    distance = offsets(positions)
    if distance is None:
        return None
    points = np.asarray(positions).reshape(-1, 3)
    extent = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    off = int(np.count_nonzero(distance > PLANAR_TOLERANCE * extent))
    return Flatness(patch=patch, points=len(distance), off=off, max_offset=float(distance.max()))


def warning(flat: Flatness, cell_size: float | None) -> str | None:
    """The sentence the mesh result carries when the plane is bent, or None when it is flat.

    ``cell_size`` is the mesh's base size; without one the plane is judged against nothing and a
    point off it by any measurable amount is reported.
    """
    if flat.off == 0:
        return None
    if cell_size is not None and cell_size > 0:
        share = flat.max_offset / cell_size
        if share <= WARN_FRACTION:
            return None
        size = f" ({share * 100:.0f} % of the cell size)"
    else:
        size = ""
    points = "point lies" if flat.off == 1 else "points lie"
    # A patch named for its role reads "the symmetry plane", not "symmetry plane symmetry".
    plane = "the symmetry plane" if flat.patch == "symmetry" else f"symmetry plane {flat.patch}"
    return (
        f"{plane} is not flat: {flat.off} {points} up to "
        f"{flat.max_offset * 1000:.2g} mm off it{size}. A run can diverge from the cells there; "
        "snappyHexMesh and the polyhedral type keep the plane flat"
    )
