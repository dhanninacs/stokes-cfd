"""Where a population lives when the lattice has one set of them (ADR-0047 addendum 4,
decisions 22 and 24): in-place streaming, and the links at a wall or a port.

**The scheme** was derived for Stokes from the principle Geier and Schoenherr state for their
Esoteric Twist (Computation 5(2):19, 2017): what a cell sends after its collision is written where
the population moving the opposite way was read, and the two swap roles every step. Here whole
opposite pairs are used. The buffer has nineteen slots a cell, ``S[k][x]``. Call the odd one of
each opposite pair ``(i, j = OPP[i])`` the positive direction, and let ``y = x + c_i``.

*A link* is a pair of neighbouring cells ``(x, y)`` and has two places: ``S[i][y]`` and
``S[j][y]``. Two populations cross it every step, one each way, and each cell owns one place:

====== =============================== ===============================
step   cell ``x`` (reads ``j``, sends ``i``)   cell ``y`` (reads ``i``, sends ``j``)
====== =============================== ===============================
even   ``S[j][y]``                     ``S[i][y]``
odd    ``S[i][y]``                     ``S[j][y]``
====== =============================== ===============================

In an even step ``x`` reads what arrives against ``i`` from ``S[j][y]`` and, after its collision,
writes what it sends along ``i`` to the same place; ``y`` does the same with ``S[i][y]``. In the
next step the roles of the two places are exchanged, so what ``x`` wrote is what ``y`` reads:
it has moved one cell without being copied. *Proof that no two cells share a place*: a place
belongs to one link, a link to two cells, and in each parity the table gives them different
places. So every invocation reads and writes its own nineteen places and the order the GPU runs
the cells in does not matter.

Seen from one cell, for an arriving direction ``d`` (from ``s = x - c_d``): the place is at the
cell itself when ``d`` is positive and at ``s`` when it is not, in slot ``d`` on an even step and
``OPP[d]`` on an odd one (``regular_slot``).

**A link to a wall or a port does not swap** (decision 24, ours). A fluid cell's neighbour that is
not its own fluid may be another fluid's cell, which computes too, so the cell cannot use both of
the link's places as an untouched wall cell would let it. It keeps one, always the same: slot
``OPP[d]`` (``boundary_slot``), at the same cell as above. What it keeps there between two steps
is what it sent to the wall, ``a f_o + (1 - a) f_d`` with ``a = 1``, or, where the interpolated
wall is more than half a cell away, with ``a = 1 / (2 q)``: then the returning population is
complete when it is stored, because the second population it needs leaves the cell in the same
step. Where the wall is nearer than half a cell the stored population is completed on reading,
with the population that arrives from the fluid side. Both are the interpolated bounce-back the
two-buffer lattice did, term for term.

Pure functions, and a numpy reference of the scheme the tests hold against plain streaming.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from stokes.fast.lattice import CX, CY, CZ, OPP, Q

Populations = NDArray[np.float64]


def positive(d: int) -> bool:
    """Whether ``d`` is the positive direction of its opposite pair (the odd one)."""
    return d % 2 == 1


def at_cell(d: int) -> bool:
    """Whether the place of the link direction ``d`` arrives over is at the cell itself (else at
    the neighbour it arrives from)."""
    return positive(d)


def regular_slot(d: int, parity: int) -> int:
    """The slot a cell reads arriving direction ``d`` from, and writes ``OPP[d]`` to, over a
    link to its own fluid."""
    return d if parity == 0 else OPP[d]


def boundary_slot(d: int) -> int:
    """The slot a cell keeps for the link direction ``d`` would arrive over, when the neighbour
    there is not its own fluid: the same on every step."""
    return OPP[d]


def _moved(values: Populations, d: int, sign: int) -> Populations:
    """``values`` shifted so that cell ``x`` holds what cell ``x - sign c_d`` held (periodic)."""
    return np.roll(values, (sign * CZ[d], sign * CY[d], sign * CX[d]), axis=(0, 1, 2))


def step_in_place(
    stored: Populations, parity: int, collide: Callable[[Populations], Populations]
) -> Populations:
    """One step of the scheme on a periodic box, in numpy: the reference the tests hold against
    plain streaming. ``stored`` is ``(19, nz, ny, nx)``; returns the new buffer. ``collide`` maps
    the arriving populations ``(19, ...)`` of every cell to the leaving ones."""
    arriving = np.empty_like(stored)
    arriving[0] = stored[0]
    for d in range(1, Q):
        held = stored[regular_slot(d, parity)]
        arriving[d] = held if at_cell(d) else _moved(held, d, 1)
    leaving = collide(arriving)
    out = np.empty_like(stored)
    out[0] = leaving[0]
    for d in range(1, Q):
        sent = leaving[OPP[d]]
        out[regular_slot(d, parity)] = sent if at_cell(d) else _moved(sent, d, -1)
    return out


def arriving_of(stored: Populations, parity: int) -> Populations:
    """What arrives at every cell in the step of ``parity``, read from the buffer."""
    arriving = np.empty_like(stored)
    arriving[0] = stored[0]
    for d in range(1, Q):
        held = stored[regular_slot(d, parity)]
        arriving[d] = held if at_cell(d) else _moved(held, d, 1)
    return arriving


def step_plain(arriving: Populations, collide: Callable[[Populations], Populations]) -> Populations:
    """Plain streaming on the same box: what arrives next is what the neighbour sent."""
    leaving = collide(arriving)
    out = np.empty_like(arriving)
    out[0] = leaving[0]
    for d in range(1, Q):
        out[d] = _moved(leaving[d], d, 1)
    return out
