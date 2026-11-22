"""D3Q19 constants and the scaling between SI and lattice units (ADR-0047 decision 5).

A lattice step moves information one cell, so the cell size ``dx`` and the step ``dt`` fix every
conversion: a velocity is ``u_lattice * dx / dt``, a kinematic viscosity ``nu_lattice * dx**2 /
dt``, and a kinematic pressure ``(rho - 1) / 3 * (dx / dt)**2`` (the lattice's speed of sound is
``1 / sqrt(3)``). ``dt`` is chosen so that the fastest inlet moves ``u_lattice`` cells per step:
small enough for the lattice's weak compressibility, large enough for the relaxation time ``tau``
to stay away from its stability limit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from stokes.fast.errors import FastInvalid, FastTooFast

Q = 19

#: Lattice velocities: rest, the six axis directions, then the twelve edge diagonals.
CX = (0, 1, -1, 0, 0, 0, 0, 1, -1, 1, -1, 1, -1, 1, -1, 0, 0, 0, 0)
CY = (0, 0, 0, 1, -1, 0, 0, 1, -1, -1, 1, 0, 0, 0, 0, 1, -1, 1, -1)
CZ = (0, 0, 0, 0, 0, 1, -1, 0, 0, 0, 0, 1, -1, -1, 1, 1, -1, -1, 1)
#: The direction opposite each one.
OPP = (0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 18, 17)
#: The weights: 1/3 at rest, 1/18 along the axes, 1/36 on the diagonals.
W = (1 / 3, *(1 / 18,) * 6, *(1 / 36,) * 12)

#: The two-relaxation-time collision's magic parameter. 3/16 puts bounce-back walls exactly half
#: way whatever the viscosity, which a laminar preview wants (fRe within 0.1 % on the square duct).
#: The turbulent preview does not use it: its collision is the regularised one (the step shader).
MAGIC = 3.0 / 16.0


#: The lattice speed of the fastest inlet by default, and the one used for a restrictive core.
SPEED = 0.05
SPEED_RESTRICTIVE = 0.02
#: The slowest lattice speed a restrictive core is given.
SPEED_MIN = 0.004
#: **The largest lattice viscosity** (a relaxation time of 1). A slow, viscous flow at the default
#: lattice speed would have a relaxation time far above 1 and a pressure drop of the order of the
#: lattice's own density: measured on a square duct at a cell Reynolds number of 0.016 (2026-11-22),
#: tau was 10.1, the density reached the guard's cap of 2 and a fifth of the flow got through. Below
#: a cell Reynolds number of ``SPEED / NU_LATTICE_MAX`` (0.3) the lattice speed is lowered to hold
#: this viscosity instead (diffusive scaling).
NU_LATTICE_MAX = 1.0 / 6.0
#: **Stability is set by the cell Reynolds number** ``U dx / nu``, which the lattice speed does not
#: change (it scales the lattice velocity and viscosity alike). Measured on a square duct
#: (2026-11-21): stable and within 1.5 % of fRe = 14.227 at 3.1, stable at 6.3, unstable at 15.6. Up
#: to ``RE_CELL_LAMINAR`` the lattice runs as it is; above it a Smagorinsky subgrid viscosity (LES)
#: keeps it stable, as a turbulent preview; above ``RE_CELL_MAX`` the cells are refused as coarse.
RE_CELL_LAMINAR = 8.0
RE_CELL_MAX = 200.0
#: The Smagorinsky constant of the LES, and the factor ``18 sqrt(2) Cs^2`` the shader takes.
SMAGORINSKY = 0.17
SMAGORINSKY_FACTOR = 18.0 * math.sqrt(2.0) * SMAGORINSKY * SMAGORINSKY
#: The turbulent preview's closures, as the shader numbers them.
CLOSURES = {"smagorinsky": 0, "shear_improved": 1}
#: Lattice steps the shear-improved closure's running mean of the stress spans.
MEAN_STEPS = 2000.0
#: **Which closure a turbulent preview takes by itself.** Up to this cell Reynolds number the
#: shear-improved one: a duct that is laminar in truth reads fRe +2.3 % with it (Re 300, the
#: entrance's share) where plain Smagorinsky read +7.4 %, and a pipe -0.1 % where it read
#: +5.1 %. Above it plain Smagorinsky, which is the smoother of the two: at 62 to 195 the
#: shear-improved flow rate wanders 7 to 9 % along a duct against 1.3 % (measured 2026-11-22).
RE_CELL_SHEAR_IMPROVED = 30.0
#: The largest lattice diffusivity of the temperature's explicit step (stable to 1/6), which
#: a solid is given whatever its own: only its conductivity matters once the field is steady.
DIFFUSIVITY_LATTICE_MAX = 1.0 / 8.0


@dataclass(frozen=True)
class Scaling:
    """How SI and lattice units relate for one grid and one flow."""

    dx: float
    """Cell size, m."""
    dt: float
    """Time step, s."""
    nu: float
    """Kinematic viscosity, m2/s."""
    les: bool = False
    """True when the cell Reynolds number needs the Smagorinsky model."""
    inlet_lattice_speed: float = SPEED
    """The fastest inlet's speed in lattice units (cells per step)."""
    closure: str = "smagorinsky"
    """The turbulent preview's closure: a key of ``CLOSURES``."""
    reynolds: float = 0.0
    """The largest cell Reynolds number of the fluid zones."""

    def rates(self, nu: float) -> tuple[float, float]:
        """The symmetric and antisymmetric relaxation rates of a fluid of viscosity ``nu``."""
        relax = 3.0 * nu * self.dt / (self.dx * self.dx)
        return 1.0 / (relax + 0.5), 1.0 / (MAGIC / relax + 0.5)

    @property
    def speed(self) -> float:
        """A lattice velocity of 1 in m/s: ``dx / dt``."""
        return self.dx / self.dt

    @property
    def nu_lattice(self) -> float:
        return self.nu * self.dt / (self.dx * self.dx)

    @property
    def tau(self) -> float:
        return 3.0 * self.nu_lattice + 0.5

    @property
    def omega_plus(self) -> float:
        """The symmetric relaxation rate, 1 / tau."""
        return 1.0 / self.tau

    @property
    def omega_minus(self) -> float:
        """The antisymmetric rate of the laminar collision, from the magic parameter."""
        return 1.0 / (MAGIC / (self.tau - 0.5) + 0.5)

    def to_lattice(self, velocity: float) -> float:
        return velocity / self.speed

    def to_si(self, velocity: float) -> float:
        return velocity * self.speed

    def pressure(self, density: float) -> float:
        """The kinematic pressure (m2/s2) of a lattice density, relative to density 1."""
        return (density - 1.0) / 3.0 * self.speed * self.speed

    def density(self, pressure: float) -> float:
        """The lattice density of a kinematic pressure (m2/s2), relative to pressure 0."""
        return 1.0 + 3.0 * pressure / (self.speed * self.speed)


def scaling_for(
    dx: float,
    fastest: float,
    nu: float,
    speed: float = SPEED,
    les: bool = False,
    closure: str | None = None,
    zones: Sequence[tuple[float, float]] = (),
    diffusivity: float = 0.0,
) -> Scaling:
    """The scaling that moves the fastest inlet ``speed`` cells per step (fewer in a viscous flow:
    ``NU_LATTICE_MAX``), with the LES switched on
    above ``RE_CELL_LAMINAR`` (or always, with ``les``: the flow sped up somewhere past what the
    inlet says); ``FastTooFast`` above ``RE_CELL_MAX``."""
    for name, value in (("cell size", dx), ("inlet speed", fastest), ("viscosity", nu)):
        if not (math.isfinite(value) and value > 0.0):
            raise FastInvalid(f"the {name} must be positive; got {value}", details={name: value})
    # Every fluid zone as (its fastest inlet, its viscosity): the cell Reynolds number is the
    # largest of theirs, the lattice viscosity that is capped the most viscous one's, and
    # ``fastest`` the fastest inlet of them all.
    flows = [(u, v) for u, v in zones if v > 0.0] or [(fastest, nu)]
    fastest = max(fastest, *(u for u, _ in flows))
    reynolds = max(cell_reynolds(dx, u, v) for u, v in flows)
    stiffest = max(v for _, v in flows)
    if reynolds > RE_CELL_MAX:
        raise FastTooFast(
            "the flow is too fast for these cells even as a turbulent preview: raise the fidelity, "
            "or lower the inlet speed "
            f"(cell Reynolds number {reynolds:.3g}, at most {RE_CELL_MAX:g})",
            details={"cell_reynolds": reynolds, "max": RE_CELL_MAX},
        )
    speed = min(speed, cell_reynolds(dx, fastest, stiffest) * NU_LATTICE_MAX)
    if diffusivity > 0.0:
        # A fluid that conducts faster than the temperature's explicit step allows (a liquid
        # metal, a slow gas) lowers the lattice speed the same way.
        speed = min(speed, fastest * dx / diffusivity * DIFFUSIVITY_LATTICE_MAX)
    if closure is None:
        closure = "shear_improved" if reynolds <= RE_CELL_SHEAR_IMPROVED else "smagorinsky"
    if closure not in CLOSURES:
        raise FastInvalid(
            f"the turbulence closure is one of {sorted(CLOSURES)}; got {closure!r}",
            details={"closure": closure},
        )
    return Scaling(
        dx=dx,
        dt=speed * dx / fastest,
        nu=nu,
        les=les or reynolds > RE_CELL_LAMINAR,
        inlet_lattice_speed=speed,
        closure=closure,
        reynolds=reynolds,
    )


def cell_reynolds(dx: float, speed: float, nu: float) -> float:
    """``U dx / nu``: what decides whether the lattice is stable as it is."""
    return speed * dx / nu
