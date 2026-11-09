"""Contour lines on a triangulated surface: Fluent's *contours, filled off* (marching triangles).

The field is linear on each triangle (its three vertex values), so where a level crosses a triangle
it crosses two of its edges, at the points found by linear interpolation along them, and the line
between those two points is exactly the level's line in that triangle. Doing it per triangle, all
levels at once, in numpy, keeps 15 levels over a 40 k-triangle wall well under 100 ms.

A vertex exactly on a level is nudged up by a relative ``1e-9`` of the range, so a level never
touches a vertex and every crossing triangle has exactly two crossing edges.

Pure and headless: numpy only, imported inside the functions.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from stokes.post.integrals import ReportInvalid

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

#: The most levels one call draws: past this a picture is a filled contour, not lines.
MAX_LEVELS = 64

#: The most segments one answer carries (six floats each); past this the call is refused.
MAX_SEGMENTS = 400_000


def even_levels(low: float, high: float, count: int) -> list[float]:
    """``count`` levels evenly inside ``(low, high)``, ends excluded: the bands' boundaries.

    With ``count`` levels the range splits into ``count + 1`` bands, as a banded legend shows them.
    """
    if not 1 <= int(count) <= MAX_LEVELS:
        raise ReportInvalid(f"levels must be 1 to {MAX_LEVELS}")
    if not (math.isfinite(low) and math.isfinite(high)) or high <= low:
        raise ReportInvalid("the contour range must be two finite numbers, low < high")
    step = (high - low) / (int(count) + 1)
    return [low + step * (i + 1) for i in range(int(count))]


def contour_segments(
    positions: np.ndarray, triangles: np.ndarray, values: np.ndarray, levels: list[float]
) -> list[dict[str, Any]]:  # Any: JSON
    """Per level ``{level, segments}``; ``segments`` is a flat ``[x0,y0,z0,x1,y1,z1, ...]``."""
    import numpy as np

    if values.ndim != 1 or values.shape[0] != positions.shape[0]:
        raise ReportInvalid("contour lines need one value per point")
    if len(levels) > MAX_LEVELS:
        raise ReportInvalid(f"at most {MAX_LEVELS} levels")
    pos = positions.astype(np.float64, copy=False)
    tri = triangles.astype(np.int64, copy=False)
    v = values.astype(np.float64, copy=False)
    span = float(v.max() - v.min()) if v.size else 0.0
    nudge = (span or 1.0) * 1e-9
    out: list[dict[str, Any]] = []
    total = 0
    vt = v[tri]  # (T, 3)
    for level in levels:
        lv = float(level)
        f = vt - lv
        f = np.where(f == 0.0, nudge, f)
        above = f > 0
        count = above.sum(axis=1)
        crossing = (count == 1) | (count == 2)
        if not crossing.any():
            out.append({"level": lv, "segments": []})
            continue
        t = tri[crossing]
        g = f[crossing]
        ends: list[np.ndarray] = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            cut = (g[:, i] > 0) != (g[:, j] > 0)
            denom = np.where(cut, g[:, i] - g[:, j], 1.0)
            s = np.where(cut, g[:, i] / denom, 0.0)
            point = pos[t[:, i]] + s[:, None] * (pos[t[:, j]] - pos[t[:, i]])
            ends.append(np.where(cut[:, None], point, np.nan))
        stacked = np.stack(ends, axis=1)  # (n, 3 edges, 3)
        # Exactly two edges are cut in every crossing triangle: keep them in edge order.
        keep = ~np.isnan(stacked[:, :, 0])
        pairs = stacked[keep].reshape(-1, 2, 3)
        total += pairs.shape[0]
        if total > MAX_SEGMENTS:
            raise ReportInvalid("too many contour segments; ask for fewer levels")
        out.append({"level": lv, "segments": [float(c) for c in pairs.reshape(-1)]})
    return out
