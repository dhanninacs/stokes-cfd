"""Surface integrals, histograms and XY-on-surface data: Fluent's *Reports* on a triangulated
surface.

A surface here is what the results already hold: the **points and outward triangles of a boundary
patch** (the mesh's surfaces artifact) or of a cut plane, and a field's **values at those points**
(the run's `.stkfld` cache). Every quantity is an integral over the triangles with the field taken
as linear on each one, so a triangle contributes ``area x mean of its three vertex values`` - exact
for a linear field, and the same rule Fluent's "vertex-based" integrals use.

**Measured against the solver** on the Flanged pipe sample (500 iterations, `patchFlowRate`
function object): the inlet flow rate from these triangles is ``-0.0056447`` m3/s where the solver
wrote ``-0.00564474``, and the inlet's area-weighted ``p`` is 0.18210 against the solver's 0.18175
(face-centre vs point values). The sign rule is OpenFOAM's and Fluent's: **positive leaving the
domain**, because a patch's triangles face out of the fluid. A cut plane's triangles face along its
normal, so its flow rate is positive along the normal.

The quantities and their definitions, as Fluent names them (`report.surface_integrals`):

* ``area``: sum of triangle areas.
* ``integral``: sum of ``A_i phi_i``.
* ``area_avg``: ``integral / area``.
* ``mass_avg``: ``sum(rho |U.n| A phi) / sum(rho |U.n| A)`` - weighted by the mass crossing; with a
  constant density the density cancels, so it needs only ``U``.
* ``flow_rate`` / ``mass_flow``: ``sum(U.n A)`` and ``rho`` times it.
* ``facet_min`` / ``facet_max`` / ``facet_avg``: over the triangles' mean values.
* ``vertex_min`` / ``vertex_max`` / ``vertex_avg``: over the points.
* ``std_dev``: area-weighted standard deviation about ``area_avg``.
* ``uniformity_area`` / ``uniformity_mass``: Fluent's uniformity index,
  ``1 - sum(|phi_i - phi_avg| w_i) / (2 |phi_avg| sum(w_i))`` with ``w`` the area or the mass flux.
  1 is perfectly uniform; undefined (None) when the average is zero.

Pure and headless: numpy only, imported inside the functions (``docs/engineering/performance.md``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stokes.errors import StokesError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np


class ReportInvalid(StokesError):
    """A report, histogram or plot was asked for that cannot be computed as asked."""

    code = "results.report_invalid"


#: Every quantity :func:`surface_integrals` answers, in the order a report table lists them.
QUANTITIES: tuple[str, ...] = (
    "area",
    "area_avg",
    "mass_avg",
    "integral",
    "flow_rate",
    "mass_flow",
    "facet_min",
    "facet_max",
    "facet_avg",
    "vertex_min",
    "vertex_max",
    "vertex_avg",
    "std_dev",
    "uniformity_area",
    "uniformity_mass",
)

#: The most bins a histogram may ask for; a chart cannot show more.
MAX_BINS = 200

#: The most points an XY-on-surface answer carries; past this it is thinned evenly.
MAX_PLOT_POINTS = 4000


@dataclass(frozen=True)
class Surface:
    """One surface's geometry: ``positions (P, 3)`` metres and ``triangles (T, 3)`` indices.

    ``name`` is what a report row is called (a patch name, or ``slice``).
    """

    name: str
    positions: np.ndarray
    triangles: np.ndarray

    def check(self) -> None:
        """Refuse arrays that cannot be a surface, before any arithmetic reads past them."""
        import numpy as np

        pos, tri = self.positions, self.triangles
        if pos.ndim != 2 or pos.shape[1] != 3:
            raise ReportInvalid(f"{self.name}: positions must be (P, 3)")
        if tri.ndim != 2 or tri.shape[1] != 3:
            raise ReportInvalid(f"{self.name}: triangles must be (T, 3)")
        if tri.size and (int(np.max(tri)) >= pos.shape[0] or int(np.min(tri)) < 0):
            raise ReportInvalid(f"{self.name}: a triangle names a point the surface does not have")


def triangle_vectors(surface: Surface) -> np.ndarray:
    """Each triangle's area vector ``(T, 3)``: normal times area, in the triangle's winding."""
    import numpy as np

    pos = surface.positions.astype(np.float64, copy=False)
    tri = surface.triangles.astype(np.int64, copy=False)
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    return 0.5 * np.cross(b - a, c - a)


def facet_values(surface: Surface, values: np.ndarray) -> np.ndarray:
    """A point field's value on each triangle: its three vertices' mean, ``(T,)`` or ``(T, k)``."""
    import numpy as np

    tri = surface.triangles.astype(np.int64, copy=False)
    v = values.astype(np.float64, copy=False)
    return (v[tri[:, 0]] + v[tri[:, 1]] + v[tri[:, 2]]) / 3.0


def _check_values(surface: Surface, values: np.ndarray, what: str) -> None:
    if values.shape[0] != surface.positions.shape[0]:
        raise ReportInvalid(
            f"{surface.name}: {what} has {values.shape[0]} values for "
            f"{surface.positions.shape[0]} points"
        )


def _finite_or_none(value: float) -> float | None:
    return value if math.isfinite(value) else None


def surface_integrals(
    surface: Surface,
    values: np.ndarray | None,
    velocity: np.ndarray | None = None,
    density: float | None = None,
) -> dict[str, float | None]:
    """Every quantity in :data:`QUANTITIES` for one scalar point field on one surface.

    ``values`` is ``(P,)``, or None for a report of geometry and flow only (the value columns are
    then None). ``velocity`` ``(P, 3)`` enables ``flow_rate``, ``mass_avg`` and
    ``uniformity_mass``; ``density`` (kg/m3) enables ``mass_flow``. A quantity that cannot be
    computed is None, never a guess.
    """
    import numpy as np

    surface.check()
    vectors = triangle_vectors(surface)
    areas = np.linalg.norm(vectors, axis=1)
    total = float(areas.sum())
    out: dict[str, float | None] = dict.fromkeys(QUANTITIES)
    out["area"] = total
    flux: np.ndarray | None = None
    if velocity is not None:
        _check_values(surface, velocity, "the velocity")
        if velocity.ndim != 2 or velocity.shape[1] != 3:
            raise ReportInvalid("the velocity must have three components")
        flux = np.einsum("ij,ij->i", facet_values(surface, velocity), vectors)
        rate = float(flux.sum())
        out["flow_rate"] = rate
        if density is not None and density > 0:
            out["mass_flow"] = rate * float(density)
    if values is None or areas.size == 0 or total <= 0:
        return out
    _check_values(surface, values, "the field")
    if values.ndim != 1:
        raise ReportInvalid("a report needs one component; ask for a magnitude or a component")
    point = values.astype(np.float64, copy=False)
    facet = facet_values(surface, point)
    integral = float((areas * facet).sum())
    average = integral / total
    out["integral"] = integral
    out["area_avg"] = average
    out["facet_min"] = float(facet.min())
    out["facet_max"] = float(facet.max())
    out["facet_avg"] = float(facet.mean())
    out["vertex_min"] = float(point.min())
    out["vertex_max"] = float(point.max())
    out["vertex_avg"] = float(point.mean())
    out["std_dev"] = math.sqrt(max(0.0, float((areas * (facet - average) ** 2).sum()) / total))
    out["uniformity_area"] = _uniformity(facet, areas, average)
    if flux is not None:
        weights = np.abs(flux)
        mass = float(weights.sum())
        if mass > 0:
            mass_average = float((weights * facet).sum()) / mass
            out["mass_avg"] = mass_average
            out["uniformity_mass"] = _uniformity(facet, weights, mass_average)
    return {k: (None if v is None else _finite_or_none(float(v))) for k, v in out.items()}


def _uniformity(facet: np.ndarray, weights: np.ndarray, average: float) -> float | None:
    """Fluent's uniformity index; None when the average is zero (the index divides by it)."""
    total = float(weights.sum())
    if total <= 0 or average == 0:
        return None
    return 1.0 - float((abs(facet - average) * weights).sum()) / (2.0 * abs(average) * total)


def combine(rows: list[dict[str, float | None]]) -> dict[str, float | None]:
    """The report's *Net* row over several surfaces, from their rows alone.

    Sums add; averages re-weight by area (or by mass flow for ``mass_avg``); extremes take the
    extreme. ``std_dev`` and the uniformity indices are not combinable from rows and are None -
    report the surfaces together as one surface for those.
    """
    out: dict[str, float | None] = dict.fromkeys(QUANTITIES)
    if not rows:
        return out

    def present(key: str) -> list[float]:
        return [float(v) for r in rows if (v := r.get(key)) is not None]

    for key in ("area", "integral", "flow_rate", "mass_flow"):
        got = present(key)
        out[key] = sum(got) if len(got) == len(rows) else None
    area = out["area"] or 0.0
    if out["integral"] is not None and area > 0:
        out["area_avg"] = out["integral"] / area
    for key, pick in (
        ("facet_min", min),
        ("facet_max", max),
        ("vertex_min", min),
        ("vertex_max", max),
    ):
        got = present(key)
        out[key] = pick(got) if got else None
    pairs = [
        (float(avg), abs(float(rate)))
        for r in rows
        if (avg := r.get("mass_avg")) is not None and (rate := r.get("flow_rate")) is not None
    ]
    mass = sum(w for _, w in pairs)
    if pairs and len(pairs) == len(rows) and mass > 0:
        out["mass_avg"] = sum(v * w for v, w in pairs) / mass
    return out


def histogram(
    surfaces: list[tuple[Surface, np.ndarray]],
    bins: int = 20,
    low: float | None = None,
    high: float | None = None,
) -> dict[str, Any]:  # Any: JSON
    """An **area-weighted** histogram of a scalar point field over one or more surfaces.

    Each triangle counts with its area at its mean value, so a fine-meshed corner does not outweigh
    a coarse-meshed bulk. Answers ``{edges, area, fraction, min, max, total}``: ``bins + 1`` edges,
    the area in each bin, its fraction of the total, the values' range and the total area. Values
    outside ``[low, high]`` (when given) are left out, as Fluent's histogram does.
    """
    import numpy as np

    if not 1 <= int(bins) <= MAX_BINS:
        raise ReportInvalid(f"bins must be 1 to {MAX_BINS}")
    facets: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for surface, values in surfaces:
        surface.check()
        _check_values(surface, values, "the field")
        if values.ndim != 1:
            raise ReportInvalid("a histogram needs one component")
        facets.append(facet_values(surface, values))
        weights.append(np.linalg.norm(triangle_vectors(surface), axis=1))
    if not facets or sum(f.size for f in facets) == 0:
        raise ReportInvalid("the surfaces have no triangles")
    facet = np.concatenate(facets)
    weight = np.concatenate(weights)
    lo = float(facet.min()) if low is None else float(low)
    hi = float(facet.max()) if high is None else float(high)
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi < lo:
        raise ReportInvalid("the histogram's range must be two finite numbers, low <= high")
    if hi == lo:
        hi = lo + (abs(lo) * 1e-6 or 1e-12)
    counts, edges = np.histogram(facet, bins=int(bins), range=(lo, hi), weights=weight)
    total = float(weight.sum())
    return {
        "edges": [float(e) for e in edges],
        "area": [float(c) for c in counts],
        "fraction": [float(c) / total if total > 0 else 0.0 for c in counts],
        "min": float(facet.min()),
        "max": float(facet.max()),
        "total": total,
    }


def weighted_range(
    surfaces: list[tuple[Surface, np.ndarray]], share: float = 0.02
) -> dict[str, float]:
    """A field's range over surfaces, and its **area-weighted** robust range: the values below which
    ``share`` and above which ``share`` of the AREA lies (a range fitted to what is shown).

    A thin band of no-slip zeros along a cut's edge is a few percent of its points but almost none
    of its area, so the robust range is the core flow's - what a colour map should spread over.
    """
    import numpy as np

    if not 0.0 <= share < 0.5:
        raise ReportInvalid("share must be at least 0 and below 0.5")
    facets: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    lows: list[float] = []
    highs: list[float] = []
    for surface, values in surfaces:
        surface.check()
        _check_values(surface, values, "the field")
        if values.ndim != 1:
            raise ReportInvalid("a range needs one component")
        finite = values[np.isfinite(values)]
        if finite.size:
            lows.append(float(finite.min()))
            highs.append(float(finite.max()))
        facets.append(facet_values(surface, values))
        weights.append(np.linalg.norm(triangle_vectors(surface), axis=1))
    if not lows:
        raise ReportInvalid("the field has no finite value on these surfaces")
    facet = np.concatenate(facets)
    weight = np.concatenate(weights)
    keep = np.isfinite(facet) & (weight > 0)
    facet, weight = facet[keep], weight[keep]
    low, high = min(lows), max(highs)
    if facet.size == 0:
        return {"min": low, "max": high, "low": low, "high": high}
    order = np.argsort(facet, kind="stable")
    cumulative = np.cumsum(weight[order])
    total = float(cumulative[-1])
    lo_at = int(np.searchsorted(cumulative, share * total, side="left"))
    hi_at = int(np.searchsorted(cumulative, (1.0 - share) * total, side="left"))
    robust_low = float(facet[order][min(lo_at, facet.size - 1)])
    robust_high = float(facet[order][min(hi_at, facet.size - 1)])
    if not robust_high > robust_low:
        robust_low, robust_high = low, high
    return {"min": low, "max": high, "low": robust_low, "high": robust_high}


def coordinate(
    positions: np.ndarray,
    axis: str,
    direction: list[float] | None = None,
    origin: list[float] | None = None,
) -> np.ndarray:
    """The x-axis of an XY-on-surface plot for each point, in metres.

    ``axis`` is ``x``, ``y`` or ``z`` (that coordinate); ``direction`` (distance along a unit
    vector from ``origin``, Fluent's *plot direction*); or ``radius`` (distance from the line
    through ``origin`` along ``direction``, for a profile across a pipe).
    """
    import numpy as np

    pos = positions.astype(np.float64, copy=False)
    if axis in ("x", "y", "z"):
        return pos[:, "xyz".index(axis)]
    if axis not in ("direction", "radius"):
        raise ReportInvalid("axis must be x, y, z, direction or radius")
    d = np.asarray(direction if direction is not None else [1.0, 0.0, 0.0], dtype=np.float64)
    o = np.asarray(origin if origin is not None else [0.0, 0.0, 0.0], dtype=np.float64)
    if (
        d.shape != (3,)
        or o.shape != (3,)
        or not np.all(np.isfinite(d))
        or not np.all(np.isfinite(o))
    ):
        raise ReportInvalid("direction and origin are [x, y, z]")
    length = float(np.linalg.norm(d))
    if length == 0:
        raise ReportInvalid("the direction must not be zero")
    d = d / length
    along = (pos - o) @ d
    if axis == "direction":
        return along
    across = (pos - o) - along[:, None] * d[None, :]
    return np.linalg.norm(across, axis=1)


def centre_and_normal(surface: Surface) -> tuple[list[float], list[float] | None]:
    """The surface's area-weighted centre and its mean unit normal (None when they cancel, as
    on a closed or curved wall): the axis a radial profile across a flat end is measured from."""
    import numpy as np

    surface.check()
    vectors = triangle_vectors(surface)
    areas = np.linalg.norm(vectors, axis=1)
    total = float(areas.sum())
    if total <= 0:
        raise ReportInvalid(f"{surface.name} has no area")
    tri = surface.triangles.astype(np.int64, copy=False)
    pos = surface.positions.astype(np.float64, copy=False)
    centres = (pos[tri[:, 0]] + pos[tri[:, 1]] + pos[tri[:, 2]]) / 3.0
    centre = (centres * areas[:, None]).sum(axis=0) / total
    mean = vectors.sum(axis=0)
    length = float(np.linalg.norm(mean))
    normal = None if length < 0.5 * total else [float(v) for v in mean / length]
    return [float(v) for v in centre], normal


def surface_plot(
    surface: Surface,
    values: np.ndarray,
    axis: str,
    direction: list[float] | None = None,
    origin: list[float] | None = None,
) -> dict[str, Any]:  # Any: JSON
    """One surface's points as an XY scatter: the field against :func:`coordinate`, sorted by x.

    More than :data:`MAX_PLOT_POINTS` points are thinned evenly in x order (every n-th), so the
    shape of the cloud stays and the answer stays small.
    """
    import numpy as np

    surface.check()
    _check_values(surface, values, "the field")
    if values.ndim != 1:
        raise ReportInvalid("an XY plot needs one component")
    x = coordinate(surface.positions, axis, direction, origin)
    order = np.argsort(x, kind="stable")
    if order.size > MAX_PLOT_POINTS:
        order = order[np.linspace(0, order.size - 1, MAX_PLOT_POINTS).astype(np.int64)]
    return {
        "surface": surface.name,
        "x": [float(v) for v in x[order]],
        "y": [float(v) for v in values.astype(np.float64)[order]],
        "points": int(values.shape[0]),
    }
