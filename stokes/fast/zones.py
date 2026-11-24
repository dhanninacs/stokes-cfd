"""The zones of a Fast case as the lattice reads them, and what each stream carries (ADR-0047
addendum 2).

A case is one fluid, or several bodies: fluids that each have their own flow and never mix, and
solids that only conduct heat. ``zone_rows`` packs each zone's relaxation rates and thermal
properties behind the port table; ``streams`` turns the samples at the ports into the figures the
Results stage's Streams table shows for a solved case (flow, pressure drop, temperatures, heat), so
the two can be compared.

**Temperatures are carried less a reference** (the mean of the inlets'), for the reason densities
are carried less 1: at 300 K an f32 holds 3e-5 K, more than a slow conduction step changes.

**A solid's heat capacity is not its own.** The temperature's step is explicit, stable up to a
lattice diffusivity of 1/6, and aluminium beside water would need 600 times that; a steady field
depends on the conductivities only, so every solid is stepped at ``DIFFUSIVITY_LATTICE_MAX`` and
reaches the same steady state. The transient through a solid is therefore not physical; the
fluids' is.

Pure numpy, so it is tested without a GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.fast.case import FastCase, Zone
from stokes.fast.errors import FastInvalid
from stokes.fast.lattice import DIFFUSIVITY_LATTICE_MAX, Scaling
from stokes.fast.ports import INLET, OUTLET, PORT_STRIDE, Port, layer_thickness

SOLID = "solid"
#: The outside faces' conditions as the temperature's shader numbers them.
WALL_MODES = {"adiabatic": 0.0, "temperature": 1.0, "heat_flux": 2.0}
#: The temperature a case with no inlet temperature is referred to, K.
DEFAULT_TEMPERATURE = 300.0


def zone_texts(case: FastCase) -> list[tuple[str, bool]]:
    """Each zone's field text and whether it is a solid, in the order of the cell codes."""
    return [(zone.field_text, zone.kind == SOLID) for zone in case.all_zones]


def reference_temperature(case: FastCase) -> float:
    """The temperature the lattice's are carried less: the mean of the inlets', K."""
    found = [p.temperature for p in case.inlets if p.temperature is not None]
    if found:
        return float(np.mean(found))
    starts = [z.start for z in case.all_zones if z.start is not None]
    return float(np.mean(starts)) if starts else DEFAULT_TEMPERATURE


def start_temperature(case: FastCase, index: int) -> float:
    """Where a zone starts: its own start, else a fluid at the mean of its inlets and a solid (or a
    fluid with no inlet temperature) at the reference, as the solve starts its regions."""
    zone = case.all_zones[index]
    if zone.start is not None:
        return zone.start
    own = [p.temperature for p in case.inlets if p.zone == index and p.temperature is not None]
    return float(np.mean(own)) if own else reference_temperature(case)


def fluid_diffusivity(case: FastCase) -> float:
    """The largest thermal diffusivity of the case's fluids, m2/s; 0 when none conducts."""
    found = [
        z.kappa / (z.rho * z.cp)
        for z in case.all_zones
        if z.kind != SOLID and z.kappa > 0.0 and z.rho > 0.0 and z.cp > 0.0
    ]
    return max(found, default=0.0)


def zone_rows(case: FastCase, scaling: Scaling) -> NDArray[np.float32]:
    """One row per zone, as the shaders read them (``zoneF``): the relaxation rates (a fluid's), the
    conductivity (W/m/K), the heat capacity of a cell per step (the conductivity over the lattice
    diffusivity), the outside faces' condition and value, the kind, the starting temperature."""
    reference = reference_temperature(case)
    rows = np.zeros((len(case.all_zones), PORT_STRIDE), dtype=np.float32)
    for index, (row, zone) in enumerate(zip(rows, case.all_zones, strict=True)):
        solid = zone.kind == SOLID
        if not solid and zone.nu > 0.0:
            row[0], row[1] = scaling.rates(zone.nu)
        kappa = zone.kappa if zone.kappa > 0.0 else 1.0
        row[2] = kappa
        diffusivity = DIFFUSIVITY_LATTICE_MAX
        if not solid and zone.rho > 0.0 and zone.cp > 0.0:
            diffusivity = kappa / (zone.rho * zone.cp) * scaling.dt / (scaling.dx * scaling.dx)
        row[3] = kappa / diffusivity
        row[4] = WALL_MODES.get(zone.wall, 0.0)
        if zone.wall == "temperature":
            row[5] = zone.wall_value - reference
        elif zone.wall == "heat_flux":
            row[5] = zone.wall_value * scaling.dx
        row[6] = 1.0 if solid else 0.0
        row[7] = start_temperature(case, index) - reference
    return rows


@dataclass(frozen=True)
class PortSample:
    """What the cells next to one port hold: velocities (lattice units), the density's deviation
    from 1 and the temperature less the reference."""

    port: Port
    velocity: NDArray[np.float32]
    excess: NDArray[np.float32]
    temperature: NDArray[np.float32]


def port_flow(sample: PortSample, scaling: Scaling) -> float:
    """The volume flow through a port along its outward normal, m3/s: the cells' own, summed over
    the layer next to it and divided by the layer's thickness."""
    normal = np.asarray(sample.port.normal, dtype=np.float64)
    along = float((sample.velocity.astype(np.float64) @ normal).sum())
    face = scaling.dx * scaling.dx
    return along * scaling.speed * face / layer_thickness(sample.port.normal)


def streams(
    case: FastCase, samples: list[PortSample], scaling: Scaling, pressure_reference: float
) -> list[dict[str, Any]]:  # Any: JSON
    """One entry per fluid zone, as the Results stage's Streams table has them: ``flow`` (the
    volume flow in, m3/s) and ``flow_out``, ``mass_flow`` (kg/s), ``dp`` (inlets less outlets, Pa),
    and with the temperature solved ``t_in``, ``t_out`` (the outflow's mixing-cup mean, K) and
    ``heat`` (W, what the stream gained: mass flow times specific heat times the rise)."""
    reference = reference_temperature(case)
    found: list[dict[str, Any]] = []  # Any: JSON
    for index, zone in enumerate(case.all_zones):
        if zone.kind == SOLID:
            continue
        own = [s for s in samples if s.port.zone == index and s.excess.size]
        inlets = [s for s in own if s.port.kind == INLET]
        outlets = [s for s in own if s.port.kind == OUTLET]
        flow_in = -sum(port_flow(s, scaling) for s in inlets)
        flow_out = sum(port_flow(s, scaling) for s in outlets)
        entry: dict[str, Any] = {  # Any: JSON
            "name": zone.name,
            "flow": flow_in,
            "flow_out": flow_out,
            "mass_flow": zone.rho * flow_in,
            "inlets": [s.port.name for s in inlets],
            "outlets": [s.port.name for s in outlets],
        }
        if inlets and outlets:
            high = np.mean([scaling.pressure(1.0 + float(s.excess.mean())) for s in inlets])
            low = np.mean([scaling.pressure(1.0 + float(s.excess.mean())) for s in outlets])
            entry["dp"] = float(high - low) * zone.rho
        if case.thermal and zone.cp > 0.0:
            entering = [s.port.temperature for s in inlets if s.port.temperature is not None]
            carried = weight = 0.0
            for s in outlets:
                out = np.maximum(s.velocity.astype(np.float64) @ np.asarray(s.port.normal), 0.0)
                carried += float((out * s.temperature).sum())
                weight += float(out.sum())
            if entering and weight > 0.0:
                t_in = float(np.mean(entering))
                t_out = carried / weight + reference
                entry["t_in"] = t_in
                entry["t_out"] = t_out
                entry["heat"] = zone.rho * flow_in * zone.cp * (t_out - t_in)
        found.append(entry)
    return found


def describe(zones: tuple[Zone, ...]) -> list[dict[str, str]]:
    """The zones' names and kinds, for the status."""
    return [{"name": zone.name, "kind": zone.kind} for zone in zones]


#: Cells across a fluid's passages below which its figures are not to be relied on: the walls'
#: half-cell uncertainty is then a tenth of the passage, and a pressure drop goes as its fourth
#: power.
RESOLVED_CELLS = 10.0


def cells_across(codes: NDArray[np.integer[Any]], zones: tuple[Zone, ...]) -> list[dict[str, Any]]:
    """How many cells span each fluid's passages: its hydraulic diameter in cells, four times its
    cells over its wall faces (32 for a duct 32 cells square, whatever its length).

    ``codes`` is the cells' codes as ``(nz, ny, nx)``. A mean over the zone: a single throat is
    narrower than it says.
    """
    from stokes.fast.wgsl import zone_code

    found: list[dict[str, Any]] = []  # Any: JSON
    for index, zone in enumerate(zones):
        if zone.kind == SOLID:
            continue
        inside = codes == zone_code(index, False)
        cells = int(np.count_nonzero(inside))
        faces = sum(
            int(np.count_nonzero(np.diff(inside, axis=axis))) for axis in range(inside.ndim)
        )
        across = 4.0 * cells / faces if faces else 0.0
        found.append({"name": zone.name, "cells": cells, "cells_across": across})
    return found


def disconnected(
    codes: NDArray[np.integer[Any]],
    cells_of_port: list[NDArray[np.uint32]],
    case: FastCase,
    fidelity: int,
) -> FastInvalid | None:
    """The refusal of a lattice on which a fluid's inlets and outlets are not joined through its
    own cells (ADR-0047 addendum 5), or None when every fluid's are.

    ``codes`` is the cells' codes as ``(nz, ny, nx)``, ``cells_of_port`` each port's cells (flat
    indices). Cells are joined as the lattice's links join them (faces and edges). A fluid in
    pieces runs with no outflow and a pressure that climbs without end, and says nothing: measured
    2026-11-23 on a union whose parts shared a face (``stokes.fast.wgsl.SEAM_TOLERANCE``), and it
    is what a passage narrower than a cell does too. Every inlet must reach an outlet and every
    outlet an inlet; a port with no cells is not judged here.
    """
    from stokes.fast.wgsl import zone_code

    for index, zone in enumerate(case.all_zones):
        if zone.kind == SOLID:
            continue
        ports = [
            (port, cells)
            for port, cells in zip(case.ports, cells_of_port, strict=False)
            if port.zone == index and port.kind in (INLET, OUTLET) and cells.size
        ]
        if not any(p.kind == INLET for p, _ in ports) or not any(
            p.kind == OUTLET for p, _ in ports
        ):
            continue
        labels, pieces = connected(codes == zone_code(index, False))
        flat = labels.reshape(-1)
        touched = {p.name: set(np.unique(flat[cells]).tolist()) - {0} for p, cells in ports}
        reach = {
            kind: set().union(*(touched[p.name] for p, _ in ports if p.kind == kind))
            for kind in (INLET, OUTLET)
        }
        cut = [
            p.name
            for p, _ in ports
            if not touched[p.name] & reach[OUTLET if p.kind == INLET else INLET]
        ]
        if cut:
            return FastInvalid(
                f"the fluid {zone.name!r} is in {pieces} pieces on the lattice at fidelity "
                f"{fidelity}: {_cut_off(ports, cut)} through its cells, so nothing would flow. "
                "A passage is narrower than a cell or the parts do not touch: raise the fidelity "
                "or check the geometry",
                details={
                    "reason": "fast.disconnected",
                    "fluid": zone.name,
                    "pieces": pieces,
                    "ports": cut,
                    "fidelity": fidelity,
                },
            )
    return None


def _cut_off(ports: list[tuple[Port, NDArray[np.uint32]]], cut: list[str]) -> str:
    """What the cut ports miss, for the message: "'in' reaches no outlet and 'out' no inlet"."""
    parts: list[str] = []
    for kind, other in ((INLET, "outlet"), (OUTLET, "inlet")):
        names = [n for n in cut if _kind(ports, n) == kind]
        if names:
            verb = "" if parts else ("reaches " if len(names) == 1 else "reach ")
            parts.append(f"{', '.join(repr(n) for n in names)} {verb}no {other}")
    return " and ".join(parts)


def _kind(ports: list[tuple[Port, NDArray[np.uint32]]], name: str) -> str:
    return next(p.kind for p, _ in ports if p.name == name)


#: Half of the lattice's links between rows of cells along x (the other half are their
#: opposites), as (dz, dy, dx).
_ROW_LINKS = (
    (0, 1, -1),
    (0, 1, 0),
    (0, 1, 1),
    (1, 0, -1),
    (1, 0, 0),
    (1, 0, 1),
    (1, 1, 0),
    (1, -1, 0),
)


def connected(mask: NDArray[np.bool_]) -> tuple[NDArray[np.int64], int]:
    """The pieces of ``mask`` (``(nz, ny, nx)``) as the lattice's links join cells (faces and
    edges): each cell's piece label (0 outside the mask) and how many pieces.

    numpy only (scipy is the explore package's): the runs of cells along x are the nodes, the links
    between runs of neighbouring rows the edges, and the labels settle by hooking and pointer
    jumping in a few passes. About 0.5 s on 7 million cells.
    """
    m = np.pad(np.asarray(mask, dtype=bool), 1)
    nz, ny, nx = m.shape
    start = m.copy()
    start[:, :, 1:] &= ~m[:, :, :-1]
    run = np.cumsum(start.reshape(-1), dtype=np.int64).reshape(m.shape)
    runs = int(run[-1, -1, -1])
    centre = (slice(1, -1), slice(1, -1), slice(1, -1))
    us: list[NDArray[np.int64]] = []
    vs: list[NDArray[np.int64]] = []
    for dz, dy, dx in _ROW_LINKS:
        other = (
            slice(1 + dz, nz - 1 + dz),
            slice(1 + dy, ny - 1 + dy),
            slice(1 + dx, nx - 1 + dx),
        )
        both = m[centre] & m[other]
        # One link per pair of runs: where either run starts, or the pair's overlap does.
        fresh = start[centre] | start[other]
        fresh[:, :, 1:] |= ~both[:, :, :-1]
        fresh[:, :, 0] = True
        chosen = both & fresh
        us.append(run[centre][chosen])
        vs.append(run[other][chosen])
    u, v = np.concatenate(us), np.concatenate(vs)
    label = np.arange(runs + 1, dtype=np.int64)
    while True:
        lu, lv = label[u], label[v]
        apart = lu != lv
        if not apart.any():
            break
        lu, lv = lu[apart], lv[apart]
        np.minimum.at(label, np.maximum(lu, lv), np.minimum(lu, lv))
        while True:
            jumped = label[label]
            if np.array_equal(jumped, label):
                break
            label = jumped
    roots, dense = np.unique(label[1:], return_inverse=True)
    numbered = np.concatenate(([0], dense + 1))
    cells = np.where(m, numbered[run], 0)[centre]
    return cells, int(roots.size)
