"""Planar inlets and outlets, found from their tagged triangles (ADR-0047 decision 4).

A port is a plane, an outward normal (out of the fluid) and a footprint: the tag's triangles
rasterised into a small mask in the plane's own axes. On the GPU, a boundary link whose wall
crossing lies on the plane (within a cell) and inside the footprint takes the port's condition;
every other boundary link is a wall. So a port may face any direction, anywhere, as the model
has it.

A tag whose triangles are not one plane is refused (``fast.port_not_planar``): Fast mode's
velocity and pressure conditions are written for a flat opening, as the engines' are in practice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from stokes.fast.errors import FastInvalid, FastPortNotPlanar
from stokes.fast.lattice import CX, CY, CZ

#: The most a port's triangle normal may turn away from the port's mean normal.
PLANAR_DEGREES = 2.0
#: The most a port's faces may lie off its plane (area-weighted RMS), as a share of the port's size:
#: room for the slivers a mesher leaves along a flat face's rim, while two parallel faces apart (a
#: stepped opening) are not one plane.
PLANAR_OFFSET = 0.02
#: Triangles turned further than this from a port's mean normal are its rim's, not its own; they
#: are left out when the rest carry at least ``RIM_SHARE`` of the tagged area.
RIM_DEGREES = 20.0
RIM_SHARE = 0.9
#: Pixels along the longer side of a port's footprint mask.
MASK_PIXELS = 128
#: The most ports one case may have; the shader loops over them on every boundary link.
MAX_PORTS = 16
#: Floats per port in the packed table the shader reads (`stokes.fast.wgsl` PORT_STRIDE).
PORT_STRIDE = 28

INLET = "inlet"
OUTLET = "outlet"
#: Thermal walls: a flat tagged face held at a temperature, or given a heat flux. They are
#: found as the openings are (a plane and a footprint) but are walls to the flow.
WALL_TEMPERATURE = "wall_temperature"
WALL_FLUX = "wall_flux"
#: The kinds as the shaders number them.
KIND_CODES = {INLET: 0.0, OUTLET: 1.0, WALL_TEMPERATURE: 2.0, WALL_FLUX: 3.0}


@dataclass(frozen=True)
class Port:
    """One planar opening. Lengths in metres, the velocity in m/s, the pressure kinematic."""

    name: str
    kind: str
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]
    """Unit, out of the fluid."""
    u: tuple[float, float, float]
    v: tuple[float, float, float]
    u_min: float
    v_min: float
    pixel: float
    mask: NDArray[np.uint8] = field(repr=False, compare=False)
    """(height, width): 1 where the port is."""
    area: float
    velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)
    pressure: float = 0.0
    zone: int = 0
    """The zone (``FastCase.all_zones``) the port belongs to."""
    temperature: float | None = None
    """An inlet's or a thermal wall's temperature, K; None when the case gives none."""
    heat_flux: float = 0.0
    """A heat-flux wall's flux into the body, W/m2."""

    @property
    def width(self) -> int:
        return int(self.mask.shape[1])

    @property
    def height(self) -> int:
        return int(self.mask.shape[0])


def port_from_triangles(
    triangles: NDArray[np.floating],
    name: str,
    kind: str,
    outward: tuple[float, float, float] | None = None,
    velocity: tuple[float, float, float] = (0.0, 0.0, 0.0),
    pressure: float = 0.0,
    zone: int = 0,
    temperature: float | None = None,
    heat_flux: float = 0.0,
) -> Port:
    """The port of a tag's triangles (``(n, 3, 3)``, metres).

    ``outward`` is the tag's normal out of the part (``tag.anchor``); the mean normal is turned to
    agree with it. Without it, the triangles' own winding decides.
    """
    if kind not in KIND_CODES:
        raise FastInvalid(
            f"a port is an inlet, an outlet or a thermal wall; got {kind!r}", details={"kind": kind}
        )
    tris = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
    if tris.shape[0] == 0:
        raise FastInvalid(f"{name} has no faces to make a port of", details={"port": name})
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    doubled = np.linalg.norm(cross, axis=1)
    area = 0.5 * float(doubled.sum())
    if area <= 0.0:
        raise FastInvalid(f"{name} has no area", details={"port": name})
    mean = cross.sum(axis=0)
    length = float(np.linalg.norm(mean))
    if length <= 1e-300:
        raise FastPortNotPlanar(
            f"{name} is not a plane: its faces' normals cancel out", details={"port": name}
        )
    normal = mean / length
    # **A tag that is a flat opening plus a few faces round its rim is the opening.** A face that
    # wraps the rim (most of it on the side wall, a sliver on the opening's plane) is tagged with
    # the opening when the opening is picked by its plane, and the solve takes it; here the
    # triangles off the plane are left out when those on it carry most of the area (measured on a
    # gyroid core's inlets, 2026-11-22: 2 % of the tagged area turned by 90 degrees).
    # Twice: the rim's faces turn the first mean normal a little, the second is the opening's own.
    tagged = float(doubled.sum())
    for degrees in (RIM_DEGREES, PLANAR_DEGREES):
        facing = np.abs((cross / np.maximum(doubled, 1e-300)[:, None]) @ normal)
        flat = facing > math.cos(math.radians(degrees))
        if flat.all() or float(doubled[flat].sum()) < RIM_SHARE * tagged:
            break
        tris, cross, doubled = tris[flat], cross[flat], doubled[flat]
        area = 0.5 * float(doubled.sum())
        mean = cross.sum(axis=0)
        normal = mean / float(np.linalg.norm(mean))
    if outward is not None and float(np.dot(normal, np.asarray(outward, dtype=np.float64))) < 0.0:
        normal = -normal
    # Every triangle that carries area must face the same way, within PLANAR_DEGREES.
    keep = doubled > 1e-9 * float(doubled.max())
    cosines = np.abs((cross[keep] / doubled[keep, None]) @ normal)
    worst = float(np.degrees(np.arccos(np.clip(cosines.min(), -1.0, 1.0))))
    centroid = (tris.mean(axis=1) * doubled[:, None]).sum(axis=0) / float(doubled.sum())
    points = tris.reshape(-1, 3)
    extent = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    # Area-weighted RMS distance of the triangles' centres from the plane: the slivers a mesher
    # leaves along the rim of a flat face sit a little off it, and one of them must not set it.
    centres = tris.mean(axis=1)
    off_plane = float(
        np.sqrt((doubled * ((centres - centroid) @ normal) ** 2).sum() / doubled.sum())
    )
    if worst > PLANAR_DEGREES or off_plane > PLANAR_OFFSET * extent:
        raise FastPortNotPlanar(
            f"{name} is not one plane (faces turn by up to {worst:.1f} degrees): Fast mode needs "
            "flat inlets and outlets",
            details={"port": name, "degrees": worst, "off_plane": off_plane},
        )
    u_axis, v_axis = _axes(normal)
    a = (points - centroid) @ u_axis
    b = (points - centroid) @ v_axis
    span = max(float(a.max() - a.min()), float(b.max() - b.min()))
    pixel = span / MASK_PIXELS
    # One pixel of margin on every side, so the dilation below has room.
    u_min = float(a.min()) - pixel
    v_min = float(b.min()) - pixel
    width = math.ceil((float(a.max()) - u_min) / pixel) + 2
    height = math.ceil((float(b.max()) - v_min) / pixel) + 2
    mask = _rasterise(tris, centroid, u_axis, v_axis, u_min, v_min, pixel, width, height)
    return Port(
        name=name,
        kind=kind,
        origin=_vec(centroid),
        normal=_vec(normal),
        u=_vec(u_axis),
        v=_vec(v_axis),
        u_min=u_min,
        v_min=v_min,
        pixel=pixel,
        mask=_dilate(mask),
        area=area,
        velocity=velocity,
        pressure=pressure,
        zone=zone,
        temperature=temperature,
        heat_flux=heat_flux,
    )


def pack_ports(
    ports: list[Port],
    to_lattice: float,
    density_of: dict[str, float],
    length_scale: float = 1e3,
    gains: dict[str, float] | None = None,
    zone_rows: NDArray[np.float32] | None = None,
    reference_temperature: float = 0.0,
    cell_size: float = 1.0,
) -> tuple[NDArray[np.float32], NDArray[np.uint32]]:
    """The port table and the masks as the shader reads them (``stokes.fast.wgsl``).

    Lengths are multiplied by ``length_scale`` (the field's units: millimetres); an inlet's velocity
    is divided by ``to_lattice`` (m/s per lattice unit). An outlet carries its lattice density's
    deviation from 1 (the shaders never hold a density itself) from ``density_of``, and an inlet its
    ``gains`` entry (1 without one): what its profile is multiplied by so that the cells deliver
    the port's flow rate (``inlet_gains``). Never empty, because a zero-sized buffer cannot be
    bound: a case with no port still sends one unused row and one mask word.
    """
    if len(ports) > MAX_PORTS:
        raise FastInvalid(
            f"Fast mode takes at most {MAX_PORTS} inlets and outlets; this case has {len(ports)}",
            details={"ports": len(ports), "max": MAX_PORTS},
        )
    table = np.zeros((zone_base(len(ports)), PORT_STRIDE), dtype=np.float32)
    masks: list[NDArray[np.uint32]] = []
    offset = 0
    for row, port in zip(table, ports, strict=False):
        row[0:3] = np.asarray(port.origin) * length_scale
        row[3] = KIND_CODES[port.kind]
        row[4:7] = port.normal
        row[7] = offset
        row[8:11] = port.u
        row[11] = port.width
        row[12:15] = port.v
        row[15] = port.height
        row[16] = port.u_min * length_scale
        row[17] = port.v_min * length_scale
        row[18] = port.pixel * length_scale
        row[19] = density_of.get(port.name, 0.0)
        row[20:23] = np.asarray(port.velocity) / to_lattice
        row[23] = (gains or {}).get(port.name, 1.0)
        # What the temperature's step reads: a temperature less the reference, or a heat-flux
        # wall's flux times the cell size (W/m, the unit of a conductivity times a kelvin).
        if port.kind == WALL_FLUX:
            row[24] = port.heat_flux * cell_size
        elif port.temperature is not None:
            row[24] = port.temperature - reference_temperature
        row[25] = port.zone
        flat = port.mask.reshape(-1).astype(np.uint32)
        masks.append(flat)
        offset += int(flat.size)
    joined = np.concatenate(masks) if masks else np.zeros(1, dtype=np.uint32)
    if zone_rows is not None:
        table = np.concatenate([table, np.asarray(zone_rows, dtype=np.float32)])
    return table, joined


def zone_base(ports: int) -> int:
    """The row of the packed table where the zones' rows start: after the ports' (one at least)."""
    return max(1, ports)


#: The range an inlet's gain is held to: outside it the cells do not describe the port at all.
GAIN_RANGE = (0.5, 2.0)


def layer_thickness(normal: tuple[float, float, float]) -> float:
    """How thick, in cells along ``normal``, the layer of cells next to a port is.

    A cell is next to a port when one of its links crosses the port's plane, so the layer reaches
    as far as the longest link does along the normal: 1 for a port square to the lattice, up to
    1.41 for one at 45 degrees. The layer's cell count times a cell's face, divided by this, is
    the port's area (measured 2026-11-22 on ducts at 17, 30 and 45 degrees: within 4 %).
    """
    return max(
        cx * normal[0] + cy * normal[1] + cz * normal[2]
        for cx, cy, cz in zip(CX, CY, CZ, strict=True)
    )


def inlet_gains(
    ports: list[Port], shares: list[NDArray[np.float64]], cell_size: float
) -> dict[str, float]:
    """What each inlet's profile is multiplied by so that its cells carry the port's flow rate.

    ``shares[k]`` holds the profile's share (0 at a wall to 1) of every cell next to port ``k``.
    The area those cells cover with their shares (``layer_thickness``) is less than the port's:
    the profile falls off at the walls, and cubes do not fill a round or a tilted opening. The gain
    is the ratio, held to ``GAIN_RANGE``.
    """
    gains: dict[str, float] = {}
    for port, share in zip(ports, shares, strict=False):
        if port.kind != INLET:
            continue
        covered = float(np.sum(share)) * cell_size * cell_size / layer_thickness(port.normal)
        gain = port.area / covered if covered > 0.0 else 1.0
        gains[port.name] = min(max(gain, GAIN_RANGE[0]), GAIN_RANGE[1])
    return gains


def _axes(normal: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Two unit axes in the plane, right-handed with the normal."""
    helper = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(normal, helper)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    return u, v


def _rasterise(
    tris: NDArray[np.float64],
    centroid: NDArray[np.float64],
    u_axis: NDArray[np.float64],
    v_axis: NDArray[np.float64],
    u_min: float,
    v_min: float,
    pixel: float,
    width: int,
    height: int,
) -> NDArray[np.uint8]:
    """1 at every pixel whose centre is inside one of the triangles."""
    mask = np.zeros((height, width), dtype=np.uint8)
    flat = np.stack([(tris - centroid) @ u_axis, (tris - centroid) @ v_axis], axis=-1)
    for (ax, ay), (bx, by), (cx, cy) in flat:
        lo_i = max(0, int((min(ax, bx, cx) - u_min) / pixel) - 1)
        hi_i = min(width - 1, int((max(ax, bx, cx) - u_min) / pixel) + 1)
        lo_j = max(0, int((min(ay, by, cy) - v_min) / pixel) - 1)
        hi_j = min(height - 1, int((max(ay, by, cy) - v_min) / pixel) + 1)
        if hi_i < lo_i or hi_j < lo_j:
            continue
        px = u_min + (np.arange(lo_i, hi_i + 1) + 0.5) * pixel
        py = v_min + (np.arange(lo_j, hi_j + 1) + 0.5) * pixel
        gx, gy = np.meshgrid(px, py)
        det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(det) < 1e-300:
            continue
        l1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / det
        l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / det
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l1 + l2 <= 1.0 + 1e-9)
        mask[lo_j : hi_j + 1, lo_i : hi_i + 1] |= inside.astype(np.uint8)
    return mask


def _dilate(mask: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """The mask grown by one pixel, so a crossing on the rim still counts as the port."""
    grown = mask.copy()
    grown[1:, :] |= mask[:-1, :]
    grown[:-1, :] |= mask[1:, :]
    grown[:, 1:] |= mask[:, :-1]
    grown[:, :-1] |= mask[:, 1:]
    return grown


def _vec(values: NDArray[np.float64]) -> tuple[float, float, float]:
    return (float(values[0]), float(values[1]), float(values[2]))
