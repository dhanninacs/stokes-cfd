"""What a part's surface measures (ADR-0037): the quantities a study's responses and the app read.

From a closed triangle mesh with outward normals, in float64:

- ``volume`` (m³) and ``centroid`` (m), by the divergence theorem over signed tetrahedra;
- ``area`` (m²);
- ``bbox_min``, ``bbox_max`` and ``extent`` (m);
- ``closed``: every edge is shared by exactly two triangles, so the volume means what it says.

A study names them after the part: ``geometry.parts.duct.volume``,
``geometry.parts.duct.extent.x`` (:data:`SCALARS`, :data:`VECTORS`). Nothing here is stored on
the part: the ``geometry`` study engine measures each design point, and ``geometry.measure``
measures a part's current mesh.

Headless: numpy only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stokes.geometry.mesh import PartMesh

SCALARS: dict[str, str] = {"volume": "volume", "area": "area"}
"""Scalar measures and their dimensions."""
VECTORS: tuple[str, ...] = ("centroid", "bbox_min", "bbox_max", "extent")
"""Vector measures (lengths), named with a component: ``extent.x``."""
AXES = ("x", "y", "z")


@dataclass(frozen=True)
class Measures:
    volume: float
    area: float
    centroid: tuple[float, float, float]
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    closed: bool
    triangles: int

    @property
    def extent(self) -> tuple[float, float, float]:
        a, b = self.bbox_min, self.bbox_max
        return (b[0] - a[0], b[1] - a[1], b[2] - a[2])

    def get(self, name: str) -> tuple[float, str] | None:
        """``name`` (``volume``, ``extent.x``) as (SI value, dimension), or None."""
        if name in SCALARS:
            return float(getattr(self, name)), SCALARS[name]
        base, _, axis = name.partition(".")
        if base in VECTORS and axis in AXES:
            return float(getattr(self, base)[AXES.index(axis)]), "length"
        return None

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "volume": self.volume,
            "area": self.area,
            "centroid": list(self.centroid),
            "bbox_min": list(self.bbox_min),
            "bbox_max": list(self.bbox_max),
            "extent": list(self.extent),
            "closed": self.closed,
            "triangles": self.triangles,
        }


def measure(pieces: Iterable[tuple[Any, Any]]) -> Measures:  # Any: (positions, triangles) arrays
    """The measures of the union of ``pieces``, each (positions (n, 3) in metres, triangles)."""
    import numpy as np

    volume = 0.0
    area = 0.0
    moment = np.zeros(3)
    lows: list[Any] = []
    highs: list[Any] = []
    closed = True
    count = 0
    for positions, triangles in pieces:
        p = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
        t = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
        if len(t) == 0:
            continue
        a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
        signed = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
        volume += float(signed.sum())
        moment += ((a + b + c) / 4.0 * signed[:, None]).sum(axis=0)
        area += float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2.0)
        used = p[np.unique(t)]
        lows.append(used.min(axis=0))
        highs.append(used.max(axis=0))
        closed = closed and _closed(t)
        count += len(t)
    if count == 0:
        zero = (0.0, 0.0, 0.0)
        return Measures(0.0, 0.0, zero, zero, zero, False, 0)
    centroid = moment / volume if volume != 0.0 else np.zeros(3)
    low = np.min(lows, axis=0)
    high = np.max(highs, axis=0)
    return Measures(
        volume,
        area,
        (float(centroid[0]), float(centroid[1]), float(centroid[2])),
        (float(low[0]), float(low[1]), float(low[2])),
        (float(high[0]), float(high[1]), float(high[2])),
        closed,
        count,
    )


def measure_part(mesh: PartMesh) -> Measures:
    """The measures of a part's display mesh (every solid)."""
    return measure((solid.positions, solid.indices) for solid in mesh.solids)


def _closed(triangles: Any) -> bool:  # Any: (n, 3) int array
    """Whether every edge is used by exactly two triangles."""
    import numpy as np

    edges = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    edges.sort(axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return bool((counts == 2).all())
