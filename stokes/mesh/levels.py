"""Refinement levels from sizes: how many times a cell must halve to reach a target size.

**Pure arithmetic, and that is the point.** A refinement level in snappyHexMesh is a power of two:
a cell of size ``base`` refined ``n`` times is ``base / 2**n`` across. The user thinks in
millimetres ("resolve the wall at 0.5 mm"), the dictionary needs a level, and the conversion is
where a mesher quietly stops doing what was asked - a level that is off by one halves or doubles
the cell count and the user sees only "it took longer" or "the surface is coarse".

Nothing here imports numpy, OCP or the session: it is arithmetic and a warning, so it can be
tested and reasoned about on its own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The highest level any emitted dictionary uses. 2**10 = 1024 cells across one base cell, which is
#: already far past what a background box of any practical size can carry; the cap exists so a
#: nonsensical target (a micron on a metre part) produces a bounded level and a warning rather than
#: a 2**30 request that would exhaust memory on the host.
MAX_LEVEL = 10

#: How far the achieved size may differ from the requested one before the caller is told. A level
#: is an integer, so the achieved size is quantised: 3 mm asked from an 8 mm base lands on either
#: 4 mm (level 1) or 2 mm (level 2), and neither is 3 mm. Rounding is unavoidable; **silently**
#: rounding by more than a fifth is not, because that is a mesh the user did not ask for.
DEVIATION_WARNING = 0.20


@dataclass(frozen=True)
class LevelChoice:
    """A level and the honest account of what it achieves.

    ``deviation`` is the relative error of the achieved size against the requested one, as a
    fraction (0.25 is 25 %). ``clamped`` says the level hit :data:`MAX_LEVEL`, so ``deviation``
    describes a size the mesh cannot reach however the dictionary is written.
    """

    level: int
    requested: float
    achieved: float
    deviation: float
    clamped: bool = False

    @property
    def warns(self) -> bool:
        """Whether this choice is far enough off to be worth telling the user about."""
        return self.clamped or self.deviation > DEVIATION_WARNING


def level_for(base: float, size: float) -> LevelChoice:
    """The refinement level that brings ``base`` closest to ``size``.

    ``level = round(log2(base / size))``, clamped to ``0..MAX_LEVEL``. Both arguments are
    lengths in the same SI unit; the ratio is what matters, so millimetres work as well as
    metres.

    **A size at or above the base is level 0**, not a negative level: a cell cannot be coarser
    than the background block, and asking for 20 mm from an 8 mm base means "do not refine",
    which is level 0.
    """
    if not math.isfinite(base) or base <= 0:
        raise ValueError(f"base size must be a positive length, got {base!r}")
    if not math.isfinite(size) or size <= 0:
        raise ValueError(f"target size must be a positive length, got {size!r}")

    raw = math.log2(base / size)
    level = round(raw)
    clamped = False
    if level > MAX_LEVEL:
        level, clamped = MAX_LEVEL, True
    elif level < 0:
        level = 0
    achieved = base / (2.0**level)
    return LevelChoice(
        level=level,
        requested=size,
        achieved=achieved,
        deviation=abs(achieved - size) / size,
        clamped=clamped,
    )


def level_pair(base: float, surface: float, *, fine: float | None = None) -> tuple[int, int]:
    """The ``level (min max)`` pair snappyHexMesh takes for one surface.

    ``min`` is what every cell touching the surface gets; ``max`` is what cells get where the
    surface curves or meets another surface at more than ``resolveFeatureAngle``. **The pair is
    how curvature refinement is expressed in OpenFOAM 14** - there is no ``curvatureLevel`` key
    (verified against the installed source; see
    ``tests/fixtures/of14/inventory-thinkyboy.md``).

    ``fine`` defaults to one level above ``surface``, which is the ordinary "refine the curved
    parts further" request. A ``fine`` at or below ``surface`` is returned unchanged rather than
    refused: ``level (2 2)`` is a legitimate "uniform at 2" and snappy accepts it.
    """
    low = level_for(base, surface).level
    if fine is None:
        return low, min(low + 1, MAX_LEVEL)
    return low, min(level_for(base, fine).level, MAX_LEVEL)
