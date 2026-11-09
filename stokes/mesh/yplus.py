"""The first-layer height from a target y+ (brief M-505): a flat-plate estimate, pure.

The chain, each step stated so a hand calculation can be checked against the test:

    Re = U * L / nu                       (the plate's chord Reynolds number)
    Cf = 0.026 * Re^(-1/7)                (flat-plate skin friction, turbulent from the edge)
    tau_w = 0.5 * Cf * rho * U^2          (the wall shear stress)
    u_tau = sqrt(tau_w / rho)             (the friction velocity)
    y = y+ * nu / u_tau                   (the first CELL CENTRE that gives the wanted y+)
    height = 2 * y                        (the first CELL HEIGHT: the centre sits half-way up)

**The factor 2 is the one that trips hand calculations**: y+ is defined at the centre of the
first cell, so the cell's HEIGHT is twice the centre's distance from the wall. Both numbers are
returned (`y_centre` and `first_height`) and the page says so.

Everything is SI in and out: velocities in m/s, lengths in metres, densities in kg/m³,
viscosities in Pa s. Parsing quantity text is the command's job; this module never sees a unit.

**An estimate, and the page says so**: turbulent from the leading edge on a flat plate. A
laminar run, a pressure gradient, or a rough wall moves Cf, and the height moves with it — the
page carries the assumptions line, and the number is a starting point to check, not a
measurement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Cf = 0.026 Re^(-1/7): the flat-plate turbulent skin-friction correlation the brief names.
CF_COEFFICIENT = 0.026
CF_EXPONENT = -1.0 / 7.0

#: The method line the reply and the page carry, so the number always says where it came from.
METHOD = "flat plate: Cf = 0.026 Re^(-1/7)"


@dataclass(frozen=True)
class YPlusResult:
    """The estimate's numbers, all SI.

    ``first_height`` is what the user sets on the page: the first cell's HEIGHT, twice the
    centre distance ``y_centre`` that the y+ definition gives.
    """

    first_height: float
    y_centre: float
    reynolds: float
    skin_friction: float
    density: float
    viscosity: float
    method: str = METHOD


def yplus_height(
    *,
    y_plus: float,
    velocity: float,
    length: float,
    density: float,
    viscosity: float,
) -> YPlusResult:
    """The first-layer height that gives ``y_plus`` at the wall, from the flat-plate estimate.

    ``velocity`` is the bulk or free-stream speed (m/s), ``length`` the reference length (m) the
    Reynolds number is formed on, ``density`` (kg/m³) and ``viscosity`` (Pa s) the fluid's at
    running temperature. Every argument must be finite and positive — a zero or negative one is
    not a physical input and would produce a nonsense height with no error to explain it.
    """
    for name, value in (
        ("y+", y_plus),
        ("the velocity", velocity),
        ("the length", length),
        ("the density", density),
        ("the viscosity", viscosity),
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number; got {value!r}")

    nu = viscosity / density
    reynolds = velocity * length / nu
    skin_friction = CF_COEFFICIENT * reynolds**CF_EXPONENT
    tau_w = 0.5 * skin_friction * density * velocity * velocity
    u_tau = math.sqrt(tau_w / density)
    y_centre = y_plus * nu / u_tau
    return YPlusResult(
        first_height=2.0 * y_centre,
        y_centre=y_centre,
        reynolds=reynolds,
        skin_friction=skin_friction,
        density=density,
        viscosity=viscosity,
    )
