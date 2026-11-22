"""The shown plane as triangles (ADR-0047 decision 8): ``plane_mesh``.

The lattice's values live at cell centres, so triangles over the fluid cells alone stop half a cell
short of every wall and leave a staircase round a curved body. The mesh therefore takes one more
ring of vertices: the solid cells next to a fluid cell, pulled half a cell towards the fluid (onto
the wall of a lattice-aligned duct, and to within half a cell of a curved one), at rest and at
their fluid neighbours' pressure. So the colours reach the walls and end at zero speed there.

Pure numpy over one sample of the plane, so it is tested without a GPU.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.fast.grid import Grid
from stokes.fast.lattice import Scaling

#: What the plane's sample holds in place of a density for a solid cell (``slice_shader`` writes
#: 1e30).
SOLID = 1e29
#: The code of a cell outside every body (``stokes.fast.wgsl.VOID``).
OUTSIDE = 1
#: The eight in-plane neighbours of a cell, as (second, first) steps.
_AROUND = tuple((dj, di) for dj in (-1, 0, 1) for di in (-1, 0, 1) if (dj, di) != (0, 0))


def _moved(padded: NDArray[Any], dj: int, di: int) -> NDArray[Any]:  # Any: bool or float cells
    """An array padded by one cell, read one cell away: the neighbour at (j + dj, i + di)."""
    n2, n1 = padded.shape[0] - 2, padded.shape[1] - 2
    return padded[1 + dj : 1 + dj + n2, 1 + di : 1 + di + n1]


def _wall_ring(
    fluid: NDArray[np.bool_],
) -> tuple[NDArray[np.bool_], NDArray[np.float32], NDArray[np.float32]]:
    """The solid cells next to a fluid cell, and how far (in cells, along the plane's first and
    second axis) each one's vertex moves towards the fluid: half a cell, or not at all with fluid
    on both sides."""
    padded = np.pad(fluid, 1)
    near = {step: _moved(padded, *step).astype(bool) for step in _AROUND}
    ring = ~fluid & np.logical_or.reduce(list(near.values()))
    # Which way the fluid lies, per axis: -1, 0 or 1. A cell with fluid only across a corner moves
    # along both.
    step_a = near[(0, 1)].astype(np.int8) - near[(0, -1)].astype(np.int8)
    step_b = near[(1, 0)].astype(np.int8) - near[(-1, 0)].astype(np.int8)
    corner_only = ring & ~(near[(0, 1)] | near[(0, -1)] | near[(1, 0)] | near[(-1, 0)])
    corner_a = np.zeros_like(step_a)
    corner_b = np.zeros_like(step_b)
    for dj in (-1, 1):
        for di in (-1, 1):
            corner_a += di * near[(dj, di)].astype(np.int8)
            corner_b += dj * near[(dj, di)].astype(np.int8)
    step_a = np.where(corner_only, np.sign(corner_a), step_a)
    step_b = np.where(corner_only, np.sign(corner_b), step_b)
    shift_a = np.where(ring, 0.5 * step_a, 0.0).astype(np.float32)
    shift_b = np.where(ring, 0.5 * step_b, 0.0).astype(np.float32)
    return ring, shift_a, shift_b


def _positions(
    grid: Grid,
    axis: int,
    plane: int,
    shift_a: NDArray[np.float32],
    shift_b: NDArray[np.float32],
) -> NDArray[np.float32]:
    """Every plane cell's vertex (m): its centre, moved by its shift (in cells)."""
    n2, n1 = shift_a.shape
    ga, gb = np.meshgrid(np.arange(n1, dtype=np.float32), np.arange(n2, dtype=np.float32))
    ox, oy, oz = grid.origin
    dx = grid.dx
    first = (ga + 0.5 + shift_a) * dx
    second = (gb + 0.5 + shift_b) * dx
    level = np.full_like(ga, (plane + 0.5) * dx)
    if axis == 0:
        xyz = (ox + level, oy + first, oz + second)
    elif axis == 1:
        xyz = (ox + first, oy + level, oz + second)
    else:
        xyz = (ox + first, oy + second, oz + level)
    return np.stack(xyz, axis=-1).reshape(-1, 3).astype(np.float32)


def _ring_mean(
    values: NDArray[np.float64], inside: NDArray[np.bool_], ring: NDArray[np.bool_]
) -> NDArray[np.float64]:
    """``values`` with each ring cell given the mean of the inside cells round it."""
    held = np.where(inside, values, 0.0)
    total = np.zeros(inside.shape)
    count = np.zeros(inside.shape)
    for dj, di in _AROUND:
        total += _moved(np.pad(held, 1), dj, di)
        count += _moved(np.pad(inside, 1), dj, di)
    return np.where(ring, total / np.maximum(count, 1.0), held)


def _quads(
    inside: NDArray[np.bool_], ring: NDArray[np.bool_]
) -> tuple[NDArray[np.uint32], NDArray[np.uint32]]:
    """The vertices used and the triangles over them: a quad of four neighbouring vertices is drawn
    when all four are inside or ring and one at least is inside."""
    n1 = inside.shape[1]
    drawn = inside | ring
    quad = drawn[:-1, :-1] & drawn[:-1, 1:] & drawn[1:, :-1] & drawn[1:, 1:]
    quad &= inside[:-1, :-1] | inside[:-1, 1:] | inside[1:, :-1] | inside[1:, 1:]
    jj, ii = np.nonzero(quad)
    v00 = (jj * n1 + ii).astype(np.uint32)
    v10, v01, v11 = v00 + 1, v00 + n1, v00 + n1 + 1
    triangles_all = np.concatenate([np.stack([v00, v10, v11], 1), np.stack([v00, v11, v01], 1)])
    used = np.unique(triangles_all) if triangles_all.size else np.zeros(0, np.uint32)
    remap = np.full(inside.size, 0, dtype=np.uint32)
    remap[used] = np.arange(used.size, dtype=np.uint32)
    return used.astype(np.uint32), remap[triangles_all].astype(np.uint32)


def plane_mesh(
    grid: Grid,
    axis: int,
    plane: int,
    cut: NDArray[np.float32],
    scaling: Scaling,
    reference: float,
) -> tuple[NDArray[np.float32], NDArray[np.uint32], dict[str, NDArray[np.float32]]]:
    """Triangles over the plane's fluid cells and the ring of wall vertices round them (m), and
    their values: ``U``, ``Ux``, ``Uy``, ``Uz`` (m/s) and ``p`` (kinematic, m2/s2).

    ``cut`` is ``(n2, n1, 4)``: velocity (lattice units) and the density's deviation from 1, at
    least ``SOLID`` for a cell that is not a fluid.
    """
    fluid = cut[:, :, 3] < SOLID
    ring, shift_a, shift_b = _wall_ring(fluid)
    positions_all = _positions(grid, axis, plane, shift_a, shift_b)
    # A ring cell is at rest, at the mean pressure of the fluid cells round it.
    excess = _ring_mean(cut[:, :, 3].astype(np.float64), fluid, ring)
    velocity_all = np.where(fluid[:, :, None], cut[:, :, :3], 0.0).reshape(-1, 3)
    used, triangles = _quads(fluid, ring)
    velocity = velocity_all[used] * scaling.speed
    pressure = reference + excess.reshape(-1)[used] / 3.0 * scaling.speed**2
    values = {
        "U": np.linalg.norm(velocity, axis=1).astype(np.float32),
        "Ux": velocity[:, 0].astype(np.float32),
        "Uy": velocity[:, 1].astype(np.float32),
        "Uz": velocity[:, 2].astype(np.float32),
        "p": pressure.astype(np.float32),
    }
    return positions_all[used], triangles, values


def thermal_mesh(
    grid: Grid,
    axis: int,
    plane: int,
    scalars: NDArray[np.float32],
    reference_temperature: float,
) -> tuple[NDArray[np.float32], NDArray[np.uint32], NDArray[np.float32]]:
    """Triangles over every cell of the plane that lies in a body, fluid or solid, with the ring
    round them (m), and the temperature there (K).

    ``scalars`` is ``(n2, n1, 4)``: each cell's temperature less the reference, and its code (1
    outside every body).
    """
    inside = np.rint(scalars[:, :, 1]).astype(np.int64) != OUTSIDE
    ring, shift_a, shift_b = _wall_ring(inside)
    positions_all = _positions(grid, axis, plane, shift_a, shift_b)
    held = _ring_mean(scalars[:, :, 0].astype(np.float64), inside, ring)
    used, triangles = _quads(inside, ring)
    temperature = (held.reshape(-1)[used] + reference_temperature).astype(np.float32)
    return positions_all[used], triangles, temperature
