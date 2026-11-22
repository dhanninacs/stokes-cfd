"""When a Fast run is steady (ADR-0047 decision 8): a window in lattice steps, not seconds alone.

Pure functions over the runner's samples, so the rule is tested without a GPU.
"""

from __future__ import annotations

from dataclasses import dataclass

from stokes.fast.grid import Grid
from stokes.fast.lattice import Scaling

#: **Steady**: the pressure drop and the outflow each stay within ``STEADY_CHANGE`` of their value
#: over a window that is at least ``STEADY_SECONDS`` long and at least ``settle_steps`` lattice
#: steps (half the time the flow, or its viscosity, needs to cross the fluid). Wall-clock time alone
#: called a large grid steady after a few hundred steps: its two seconds were a twelfth of a
#: flow-through.
STEADY_CHANGE = 0.01
STEADY_SECONDS = 2.0


@dataclass(frozen=True)
class Sample:
    """One frame's part in the steady test: when, at which step, and the two values watched."""

    time: float
    step: int
    dp: float
    flow_out: float
    heat: float = 0.0
    """The streams' heats, their sizes summed (W); 0 when the temperature is not solved. Watched
    because a heated solid warms long after the flow has settled."""


def settle_steps(grid: Grid, scaling: Scaling) -> int:
    """Half the lattice steps the flow needs to cross the grid's longest side, or its viscosity
    the shortest, whichever is sooner: the least a window must span to call a run steady."""
    counts = (grid.nx, grid.ny, grid.nz)
    through = max(counts) / max(scaling.inlet_lattice_speed, 1e-12)
    viscous = min(counts) ** 2 / max(scaling.nu_lattice, 1e-12)
    return int(0.5 * min(through, viscous))


def steady(history: list[Sample], steps: int) -> bool:
    """Whether the newest sample ends a steady window, trimming ``history`` to that window.

    run is steady when, across it, the pressure drop, the outflow and (with a temperature) the
    streams' heat each vary by no more than
    ``STEADY_CHANGE`` of their newest value.
    """
    if not history:
        return False
    newest = history[-1]
    start = 0
    for index, sample in enumerate(history):
        if newest.time - sample.time >= STEADY_SECONDS and newest.step - sample.step >= steps:
            start = index
    del history[:start]
    oldest = history[0]
    if newest.time - oldest.time < STEADY_SECONDS or newest.step - oldest.step < steps:
        return False
    watched = [[h.dp for h in history], [h.flow_out for h in history]]
    if any(h.heat != 0.0 for h in history):
        watched.append([h.heat for h in history])
    for values in watched:
        if max(values) - min(values) > STEADY_CHANGE * max(abs(values[-1]), 1e-30):
            return False
    return True
