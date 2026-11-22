"""``FastCase``: everything the Fast solver needs, as plain data (ADR-0047).

Built from a session by ``stokes.api.fast_inputs``; read by ``stokes.fast.runner``. Two cases with
the same ``geometry_key`` have the same field, box and ports, so an edit that changes only a value
(a speed, a pressure, the viscosity) keeps the lattice and the flow as they are.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from stokes.fast.ports import INLET, OUTLET, Port


@dataclass(frozen=True)
class Zone:
    """One body of the case: a fluid with its own flow, or a solid that only conducts heat
    (ADR-0047 addendum 2). Two fluid zones never mix; every zone exchanges heat with its
    neighbours."""

    name: str
    kind: str
    """``fluid`` or ``solid``."""
    field_text: str
    """The body's field in Fidget's text format: negative inside, in mm, world frame."""
    nu: float = 0.0
    """Kinematic viscosity, m2/s (a fluid's)."""
    rho: float = 1000.0
    """Density, kg/m3."""
    cp: float = 0.0
    """Specific heat, J/kg/K; 0 when the temperature is not solved."""
    kappa: float = 0.0
    """Thermal conductivity, W/m/K."""
    wall: str = "adiabatic"
    """What the zone's outside faces do unless a thermal wall holds them: ``adiabatic``,
    ``temperature`` or ``heat_flux``."""
    wall_value: float = 0.0
    """The outside faces' temperature (K) or heat flux into the body (W/m2)."""
    start: float | None = None
    """The temperature the zone starts at, K; None for the case's reference."""


@dataclass(frozen=True)
class FastCase:
    field_text: str
    """The fluid's field in Fidget's text format: negative in the fluid, in mm, world frame."""
    low: tuple[float, float, float]
    """The fluid's bounding box, m."""
    high: tuple[float, float, float]
    ports: tuple[Port, ...]
    nu: float
    """Kinematic viscosity, m2/s."""
    smallest: float = 0.0
    """The fluid body's smallest dimension (m), for the thin-feature warning; 0 when unknown."""
    label: str = ""
    """What the fluid is, for messages: the part's path."""
    zones: tuple[Zone, ...] = ()
    """Every body of the case, fluids first; empty for one fluid, which is then
    ``field_text`` and ``nu``."""
    thermal: bool = False
    """True when the temperature is solved."""

    @property
    def all_zones(self) -> tuple[Zone, ...]:
        """The zones, with the one fluid of a case that names none."""
        return self.zones or (Zone("fluid", "fluid", self.field_text, self.nu),)

    def fastest_in(self, zone: int) -> float:
        """The fastest inlet of one zone, m/s; 0 with none."""
        speeds = [sum(c * c for c in p.velocity) ** 0.5 for p in self.inlets if p.zone == zone]
        return max(speeds, default=0.0)

    @property
    def inlets(self) -> tuple[Port, ...]:
        return tuple(p for p in self.ports if p.kind == INLET)

    @property
    def outlets(self) -> tuple[Port, ...]:
        return tuple(p for p in self.ports if p.kind == OUTLET)

    @property
    def fastest(self) -> float:
        """The fastest inlet's speed, m/s; 0 with no inlet."""
        speeds = [sum(c * c for c in p.velocity) ** 0.5 for p in self.inlets]
        return max(speeds, default=0.0)

    @property
    def geometry_key(self) -> str:
        """What the lattice's classification depends on: the field, the box and the port shapes."""
        digest = hashlib.sha256(self.field_text.encode("utf-8"))
        for zone in self.zones:
            digest.update(repr((zone.name, zone.kind)).encode())
            digest.update(zone.field_text.encode("utf-8"))
        digest.update(repr(self.thermal).encode())
        digest.update(repr((self.low, self.high)).encode("utf-8"))
        for p in self.ports:
            digest.update(
                repr(
                    (p.name, p.kind, p.zone, p.origin, p.normal, p.u_min, p.v_min, p.pixel)
                ).encode()
            )
            digest.update(p.mask.tobytes())
        return digest.hexdigest()[:16]
