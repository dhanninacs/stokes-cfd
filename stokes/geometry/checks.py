"""Tessellation checks of parts (ADR-0020 decision 6): what a mesher needs from a part's surface.

:func:`check_meshes` takes part meshes and returns one :class:`PartCheck` each, with findings
``{code, severity, count, message, suggestion}`` and the statistics they came from. The parts are
checked in one batch over the welded topology of :mod:`stokes.geometry.topology`.

| Code | Severity | Meaning |
|---|---|---|
| ``geometry.empty`` | error | the part has no triangles |
| ``geometry.non_finite`` | error | triangles with a NaN or infinite coordinate |
| ``geometry.size`` | warning | the bounding box is under 1 micrometre or over 10 km across |
| ``geometry.untriangulated_faces`` | warning | B-rep faces the tessellation has no triangle for |
| ``geometry.free_edges`` | warning | edges of one triangle only: the surface is not closed |
| ``geometry.non_manifold_edges`` | warning | edges shared by more than two triangles |
| ``geometry.inconsistent_orientation`` | warning | neighbours wound in the same direction |
| ``geometry.inverted`` | warning | a closed surface enclosing negative volume: normals inward |
| ``geometry.degenerate_triangles`` | warning | triangles without area, or with corners welded |
| ``geometry.sliver_triangles`` | info | triangles of quality under 0.001 (1 is equilateral) |

The B-rep checks of the ADR (self-intersection, tiny edges and faces, shell closure) need the
part's shape, which in this release only the import worker holds while it reads the file; the
command reports them as not run.

Import rule: numpy only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.geometry.mesh import PartMesh
from stokes.geometry.topology import Edges, Surface, edges_of, facets_of, surface_of

I64 = NDArray[np.int64]

SEVERITIES: tuple[str, ...] = ("error", "warning", "info")
MIN_SIZE = 1e-6
"""Smallest plausible part, metres across (a smaller one usually means wrong file units)."""
MAX_SIZE = 1e4
SLIVER_QUALITY = 1e-3
"""4 sqrt(3) area / (sum of squared sides): 1 for an equilateral triangle; display tessellations
of long thin faces reach 0.01 to 0.05, so only far thinner triangles are reported."""
DEGENERATE_AREA = 1e-12
"""Twice a triangle's area below this fraction of the part's squared size counts as none."""


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    count: int
    message: str
    suggestion: str | None = None

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "code": self.code,
            "severity": self.severity,
            "count": self.count,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class PartCheck:
    findings: tuple[Finding, ...]
    stats: dict[str, Any] = field(default_factory=dict)  # Any: JSON numbers

    @property
    def worst(self) -> str:
        """The most severe finding's severity, or ``ok``."""
        found = {f.severity for f in self.findings}
        return next((s for s in SEVERITIES if s in found), "ok")

    def summary(self) -> dict[str, Any]:  # Any: JSON
        """What a part node keeps of its last check (``meta["check"]``)."""
        return {
            "worst": self.worst,
            "counts": {f.code: f.count for f in self.findings},
        }

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "worst": self.worst,
            "ok": self.worst in ("ok", "info"),
            "findings": [f.to_json() for f in self.findings],
            "stats": self.stats,
        }


def check_meshes(meshes: Sequence[PartMesh]) -> list[PartCheck]:
    """Check every part's tessellation; one :class:`PartCheck` per mesh, in order."""
    if not meshes:
        return []
    surface = surface_of(meshes)
    facets = facets_of(surface)
    edges = edges_of(surface)
    parts = surface.parts
    owner = surface.owner

    def per_part(mask: NDArray[np.bool_], which: I64 = owner) -> I64:
        return np.bincount(which[mask], minlength=parts).astype(np.int64)

    triangles = np.bincount(owner, minlength=parts)
    finite_vertex = np.isfinite(surface.positions).all(axis=1)
    finite = (
        finite_vertex[surface.triangles].all(axis=1)
        if surface.triangle_count
        else np.zeros(0, dtype=bool)
    )
    low, high = _boxes(surface, finite_vertex)
    size = np.linalg.norm(high - low, axis=1)
    twice = 2.0 * facets.area
    collapsed = edges.collapsed.reshape(-1, 3).any(axis=1)
    degenerate = finite & (collapsed | (twice <= DEGENERATE_AREA * size[owner] ** 2))
    quality = np.zeros(surface.triangle_count)
    perimeter2 = facets.edge2.sum(axis=1)
    np.divide(2.0 * math.sqrt(3.0) * twice, perimeter2, out=quality, where=perimeter2 > 0.0)
    sliver = finite & ~degenerate & (quality < SLIVER_QUALITY)
    free, crowded, turned = _edge_counts(edges, parts)
    edge_total = np.bincount(edges.owner, minlength=parts)
    volume = np.bincount(owner[finite], weights=facets.signed_volume[finite], minlength=parts)
    area = np.bincount(owner[finite], weights=facets.area[finite], minlength=parts)
    meshed = np.zeros(surface.face_total, dtype=bool)
    meshed[surface.face[surface.face >= 0]] = True
    face_owner = np.repeat(np.arange(parts), np.diff(surface.face_offset))
    bare = np.bincount(face_owner[~meshed], minlength=parts)
    counts = {
        "non_finite": per_part(~finite),
        "degenerate": per_part(degenerate),
        "slivers": per_part(sliver),
    }
    results = []
    for part, mesh in enumerate(meshes):
        closed = bool(triangles[part]) and not (free[part] or crowded[part] or turned[part])
        stats: dict[str, Any] = {
            "triangles": int(triangles[part]),
            "vertices": int(surface.vertex_offset[part + 1] - surface.vertex_offset[part]),
            "faces": surface.faces_of(part),
            "edges": int(edge_total[part]),
            "free_edges": int(free[part]),
            "non_manifold_edges": int(crowded[part]),
            "inconsistent_edges": int(turned[part]),
            "degenerate_triangles": int(counts["degenerate"][part]),
            "sliver_triangles": int(counts["slivers"][part]),
            "non_finite_triangles": int(counts["non_finite"][part]),
            "untriangulated_faces": int(bare[part]),
            "closed": closed,
            "volume": float(volume[part]) if closed else None,
            "area": float(area[part]),
            "bbox": [float(v) for v in (*low[part], *high[part])] if triangles[part] else [],
            "size": float(size[part]),
            "quality": mesh.solids[0].quality if mesh.solids else "",
        }
        results.append(PartCheck(_findings(stats), stats))
    return results


def _boxes(
    surface: Surface, finite: NDArray[np.bool_]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Per-part bounding boxes over finite vertices (zeros for a part without any)."""
    low = np.zeros((surface.parts, 3))
    high = np.zeros((surface.parts, 3))
    points = surface.positions.astype(np.float64)
    owner = np.repeat(np.arange(surface.parts), np.diff(surface.vertex_offset))
    keep = finite.copy()
    if keep.any():
        order = owner[keep]
        values = points[keep]
        starts = np.flatnonzero(np.diff(np.concatenate(([-1], order))) != 0)
        present = order[starts]
        low[present] = np.minimum.reduceat(values, starts, axis=0)
        high[present] = np.maximum.reduceat(values, starts, axis=0)
    return low, high


def _edge_counts(edges: Edges, parts: int) -> tuple[I64, I64, I64]:
    """Per part: free edges, non-manifold edges, and manifold edges whose two triangles are wound
    the same way."""
    free = np.bincount(edges.owner[edges.count == 1], minlength=parts)
    crowded = np.bincount(edges.owner[edges.count > 2], minlength=parts)
    pairs = edges.count == 2
    first = edges.side[edges.start[pairs]]
    second = edges.side[edges.start[pairs] + 1]
    same = edges.forward[first] == edges.forward[second]
    turned = np.bincount(edges.owner[pairs][same], minlength=parts)
    return free.astype(np.int64), crowded.astype(np.int64), turned.astype(np.int64)


def _findings(s: dict[str, Any]) -> tuple[Finding, ...]:  # Any: the stats above
    found: list[Finding] = []

    def add(code: str, severity: str, count: int, message: str, hint: str | None) -> None:
        found.append(Finding(code, severity, count, message, hint))

    if s["triangles"] == 0:
        add("geometry.empty", "error", 1, "the part has no triangles", "import it again")
        return tuple(found)
    if s["non_finite_triangles"]:
        add(
            "geometry.non_finite",
            "error",
            s["non_finite_triangles"],
            f"{s['non_finite_triangles']} triangles have a NaN or infinite coordinate",
            "repair the CAD file",
        )
    if not MIN_SIZE <= s["size"] <= MAX_SIZE:
        add(
            "geometry.size",
            "warning",
            1,
            f"the part is {s['size']:.3g} m across",
            "check the units the CAD file declares",
        )
    if s["untriangulated_faces"]:
        add(
            "geometry.untriangulated_faces",
            "warning",
            s["untriangulated_faces"],
            f"{s['untriangulated_faces']} faces have no triangles (OCCT could not mesh them)",
            "repair or simplify those faces in CAD",
        )
    if s["free_edges"]:
        add(
            "geometry.free_edges",
            "warning",
            s["free_edges"],
            f"{s['free_edges']} edges belong to one triangle only: the surface is not closed",
            "close the gaps; a mesher needs a watertight surface around each volume",
        )
    if s["non_manifold_edges"]:
        add(
            "geometry.non_manifold_edges",
            "warning",
            s["non_manifold_edges"],
            f"{s['non_manifold_edges']} edges are shared by more than two triangles",
            "separate the touching bodies or faces",
        )
    if s["inconsistent_edges"]:
        add(
            "geometry.inconsistent_orientation",
            "warning",
            s["inconsistent_edges"],
            f"{s['inconsistent_edges']} edges join triangles wound in the same direction",
            "re-orient the faces in CAD",
        )
    if s["closed"] and s["volume"] is not None and s["volume"] < 0.0:
        add(
            "geometry.inverted",
            "warning",
            1,
            "the surface is closed but its normals point inwards (negative volume)",
            "reverse the solid in CAD",
        )
    if s["degenerate_triangles"]:
        add(
            "geometry.degenerate_triangles",
            "warning",
            s["degenerate_triangles"],
            f"{s['degenerate_triangles']} triangles have no area",
            None,
        )
    if s["sliver_triangles"]:
        add(
            "geometry.sliver_triangles",
            "info",
            s["sliver_triangles"],
            f"{s['sliver_triangles']} triangles are slivers (quality under {SLIVER_QUALITY})",
            None,
        )
    return tuple(found)
