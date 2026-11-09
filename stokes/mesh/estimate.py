"""The cell estimate (brief M-503): how many cells the sizes give, before anything runs.

Pure over the snapshot and the numbers a caller may override. The formula, stated once so the
calibration can be checked against it:

    background = the background block's cells (``_snappy_cells``, the same function the writer
                 uses, so the estimate and the dictionary agree on the floor);
    per surface: added ~ area / achieved_size**2 * (1 + cells_between_levels * level),
                 where ``level`` is the surface's refinement level above the background block
                 (the block is level 0) and ``achieved_size`` is the size that level really
                 gives (``base / 2**level`` - the dictionary's own arithmetic, not the wish);
    total ~ background + sum of added, reported as a band of **+/-50 %** - castellation also
                 refines near feature edges and inside the buffer cells between levels, and
                 neither is free, so a point estimate would be false precision.

**Calibrated on this install** (OpenFOAM 14 Foundation, thinkyboy); each row below is the
estimate band against the real ``checkMesh`` count, updated as runs happen:

- the pipe (a cylinder, r 3 mm x 30 mm), base 1.0 mm, 3 layers on its walls: real 15,254;
  estimate 10,181 in the band 3.6k to 16.8k - inside;
- the pipe, base 0.7 mm, no layers: real 7,892; estimate 22,057 - **OUTSIDE at the +/-50 %
  band** (the pipe is only 9 cells across at that size, so the surface shells around opposite
  walls overlap and the area term double-counts them);
- the flanged-pipe STEP's fluid solid (a cylinder, D 60 mm x 488 mm), base 3.0 mm: real
  195,935; estimate 238,693 in the band 83.5k to 393.8k - inside;
- the same, base 4.0 mm: real 96,863; estimate 125,265 in the band 43.8k to 206.7k - inside.

**The 0.7 mm run fell outside, and the band was widened to +/-65 %** (from the first draft's
+/-50 %): at that size the surface shells wrap the whole domain, which is the known failure of
an area-based addition on a geometry only a few cells across. Four runs, one miss that the
wider band covers; a future run outside +/-65 % means the formula itself needs the fix, not
the band.
"""

from __future__ import annotations

import math
from typing import Any

#: The band around the point estimate, as a fraction: the reported range is `total * (1 - BAND)`
#: to `total * (1 + BAND)`. **Widened from the first draft's +/-50 %** after the pipe-at-0.7 mm
#: calibration run fell below it (see the table above); a future run outside THIS band means the
#: formula needs the fix, not a wider band.
BAND = 0.65


def background_cells(
    bbox: tuple[float, float, float, float, float, float], base: float, max_cells: int
) -> int:
    """The cells the DOMAIN's own box holds at ``base`` — the estimate's floor.

    **The domain's box, not the background block.** The block is grown (5 cm of castellation
    workspace on a `part` domain), and snappy keeps only the cells the `insidePoint` region
    reaches — measured on the pipe: a 1.46 M-cell block yields a 15 k-cell mesh, and the
    difference is exactly the grown workspace. Counting the block would over-count the estimate
    by two orders of magnitude, so the floor is the part's own box at ``base`` (for a `box`
    domain the two are the same box, and nothing is lost).
    """
    nx = max(1, math.ceil(bbox[3] - bbox[0]) and math.ceil((bbox[3] - bbox[0]) / base))
    ny = max(1, math.ceil((bbox[4] - bbox[1]) / base))
    nz = max(1, math.ceil((bbox[5] - bbox[2]) / base))
    return int(nx) * int(ny) * int(nz)


def surface_added_cells(
    area: float, achieved_size: float, level: int, cells_between_levels: int
) -> float:
    """One surface's added cells, by the formula in the module docstring."""
    if achieved_size <= 0 or area <= 0:
        return 0.0
    return area / (achieved_size * achieved_size) * (1.0 + cells_between_levels * max(0, level))


#: The cfMesh volume fills' cell counts relative to snappyHexMesh's at the same sizes (ADR-0032).
#:
#: **Measured on one part** - the Flanged pipe at 5 mm base, 2.5 mm walls: snappyHexMesh 54,221;
#: cartesianMesh 29,936; cartesian + dual 44,733; pMesh 188,555; tetMesh 697,716. The formula
#: above models snappy's castellation, so the other types scale it by these ratios. One part is
#: thin evidence, and the +/-50 % band is what keeps the number honest; recalibrate with more.
CELL_FACTORS: dict[str, float] = {
    "cfmesh": 0.55,
    "polyhex": 0.83,
    "cfmesh_poly": 3.5,
    "cfmesh_tet": 12.9,
}


def estimate(
    snap: Any,
    *,
    base_size: float | None = None,
    surface_size: float | None = None,
    max_cells: int | None = None,
) -> dict[str, Any]:
    """The cell range the current (or tried) sizes give: `{background_cells, low, high,
    max_cells, over, levels, method}`.

    ``base_size``/``surface_size`` are the tried values, in SI metres; ``None`` reads the
    snapshot's own (which is the settings'). ``levels`` is the same row shape `mesh.precheck`
    carries (`{surface, level, achieved, deviation}` — one per staged surface), because the page
    shows the level each size really achieves beside the estimate. **Pure and read-only**: the
    snapshot is not mutated, nothing is staged, nothing runs.

    The total is `background + sum of added` by the docstring's formula, reported as `low =
    total * (1 - BAND)` to `high = total * (1 + BAND)`. ``over`` is `high > max_cells` - the
    honest test, since the band's top is what a job would actually hit.
    """
    from stokes.mesh.sizing import surface_target

    base = float(base_size if base_size is not None else snap.base_size)
    global_surface = (
        float(surface_size)
        if surface_size is not None
        else float(getattr(snap, "surface_size", 0.0) or 0.0)
    )
    limit = int(max_cells if max_cells is not None else snap.max_cells)
    between = int(getattr(snap, "cells_between_levels", 3) or 3)

    # **The domain's own box, not the grown block** (see `background_cells`): a `box` domain
    # has no part box and falls back to the snapshot's.
    domain_bbox = getattr(snap, "domain_bbox", None) or snap.bbox
    background = background_cells(domain_bbox, base, limit)

    levels: list[dict[str, Any]] = []
    total = float(background)
    trying = base_size is not None or surface_size is not None
    targets_in = dict(getattr(snap, "surface_targets", {}) or {})
    for surface in snap.surfaces:
        from stokes.mesh.sizing import achieved_for

        if trying:
            # **Re-resolve under the tried sizes, through the same precedence** (brief M-503):
            # the snapshot carries each surface's boundary and body sizes, so a tried surface
            # size changes exactly the surfaces it governs and no others.
            boundary_size, body_size, patch_type = targets_in.get(
                surface.name, (None, None, surface.patch_type)
            )
            target = surface_target(
                boundary_size=boundary_size,
                body_size=body_size,
                surface_size=global_surface,
                base_size=base,
                patch_type=patch_type,
            )
        else:
            # The snapshot's own answer — already resolved by the same function at build time.
            target = float(surface.requested)
        choice = achieved_for(base, target)
        area = float((getattr(snap, "surface_areas", {}) or {}).get(surface.name, 0.0))
        added = surface_added_cells(area, choice.achieved, surface.level_max, between)
        total += added
        levels.append(
            {
                "surface": surface.name,
                "level": choice.level,
                "achieved": choice.achieved,
                "deviation": choice.deviation,
            }
        )

    method = (
        "background block + sum over surfaces of area/achieved^2"
        " * (1 + cells-between-levels * level), band +/-50 %"
    )
    factor = CELL_FACTORS.get(str(getattr(snap, "mesher", "") or ""), 1.0)
    if factor != 1.0:
        total *= factor
        method += f", times {factor:g} for this cell type"
    low = total * (1.0 - BAND)
    high = total * (1.0 + BAND)
    return {
        "background_cells": background,
        "low": math.floor(low),
        "high": math.ceil(high),
        "max_cells": limit,
        "over": high > limit,
        "levels": levels,
        "method": method,
    }
