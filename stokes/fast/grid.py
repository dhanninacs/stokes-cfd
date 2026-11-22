"""The lattice's size and placement (ADR-0047 decision 6).

One setting, the fidelity (1 to 5, as Discovery's slider), sets the number of cells across the
smallest side of the fluid's bounding box. The cells are cubes; the grid is the box plus
``PADDING`` cells on every side, so every fluid cell has a solid neighbour layer around it and no
step ever reads past the grid. A cap on the cell count (and the adapter's buffer limit) coarsens
the grid rather than refusing it, and the grid says it did.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from stokes.fast.errors import FastInvalid

#: Cells across the fluid box's smallest side, per fidelity.
CELLS_ACROSS = {1: 24, 2: 32, 3: 48, 4: 64, 5: 96}
FIDELITY_DEFAULT = 2
PADDING = 2
#: Cells a grid may hold by default: about 1.2 GB of populations in f32.
MAX_CELLS = 8_000_000
#: Bytes a lattice cell needs on the device: two sets of 19 populations, a flag, the velocity
#: with the stress's mean, and two temperatures.
BYTES_PER_CELL = (2 * 19 + 1 + 4 + 2) * 4


@dataclass(frozen=True)
class Grid:
    """A box of cubic cells; positions in metres."""

    nx: int
    ny: int
    nz: int
    origin: tuple[float, float, float]
    """The low corner of cell (0, 0, 0), m."""
    dx: float
    """The cell size, m."""
    fidelity: int
    coarsened: bool = False
    """True when the cell cap made the cells larger than the fidelity asked for."""

    @property
    def cells(self) -> int:
        return self.nx * self.ny * self.nz

    def centre(self, i: int, j: int, k: int) -> tuple[float, float, float]:
        """The centre of cell (i, j, k), m."""
        return (
            self.origin[0] + (i + 0.5) * self.dx,
            self.origin[1] + (j + 0.5) * self.dx,
            self.origin[2] + (k + 0.5) * self.dx,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "nx": self.nx,
            "ny": self.ny,
            "nz": self.nz,
            "cells": self.cells,
            "origin": list(self.origin),
            "dx": self.dx,
            "fidelity": self.fidelity,
            "coarsened": self.coarsened,
        }


def grid_for(
    low: tuple[float, float, float],
    high: tuple[float, float, float],
    fidelity: int = FIDELITY_DEFAULT,
    max_cells: int = MAX_CELLS,
    max_bytes: int | None = None,
) -> Grid:
    """The grid over the box ``low``..``high`` (m) at ``fidelity``."""
    if fidelity not in CELLS_ACROSS:
        raise FastInvalid(
            f"the fidelity is 1 to 5; got {fidelity}",
            details={"fidelity": fidelity, "allowed": sorted(CELLS_ACROSS)},
        )
    sides = [high[a] - low[a] for a in range(3)]
    if not all(math.isfinite(s) and s > 0.0 for s in sides):
        raise FastInvalid("the fluid has no finite, non-empty box", details={"box": [low, high]})
    limit = max_cells
    if max_bytes is not None:
        limit = min(limit, max_bytes // BYTES_PER_CELL)
    dx = min(sides) / CELLS_ACROSS[fidelity]
    coarsened = False
    while _count(sides, dx) > limit:
        dx *= 1.05
        coarsened = True
    counts = [math.ceil(s / dx) + 2 * PADDING for s in sides]
    # Centre the box in the grid, so the padding is the same on both sides of every axis.
    origin = tuple(low[a] - 0.5 * (counts[a] * dx - sides[a]) for a in range(3))
    return Grid(counts[0], counts[1], counts[2], origin, dx, fidelity, coarsened)  # type: ignore[arg-type]  # three floats


def _count(sides: list[float], dx: float) -> int:
    return math.prod(math.ceil(s / dx) + 2 * PADDING for s in sides)
