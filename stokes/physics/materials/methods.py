"""The evaluators of the method catalogue: pure numpy functions of (T, p, inputs).

Each evaluator takes temperatures ``T`` in kelvin and absolute pressures ``p`` in pascal (arrays of
one shape) and the property's parameters in SI (:class:`Inputs`), and returns the property in SI
on that shape; the anisotropic conductivity returns three rows, one per principal axis. They do
not validate: ``stokes.physics.materials.evaluate`` checks the parameters against the catalogue
first, and turns non-finite results into gaps or findings. Formulas and sources:
``docs/engineering/materials-schema.md`` (Fluent ``[fu pN]``, OpenFOAM Foundation 14).

Coefficients multiply powers of T in kelvin. The universal gas constant is the exact CODATA 2018
value, so ``ideal_gas`` and ``janaf`` agree with Cantera and CoolProp to rounding.

Import rule: numpy, the standard library and ``errors``; imported lazily by the catalogue and the
commands.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import numpy as np
from numpy.polynomial import polynomial as poly
from numpy.typing import NDArray

from stokes.physics.materials.errors import EvaluationFailed

Array = NDArray[np.float64]

R_UNIVERSAL = 8.31446261815324
"""J/(mol K): the Avogadro constant times the Boltzmann constant, exact since SI 2019."""
CHAPMAN_ENSKOG = 2.67e-6
"""Fluent's kinetic-theory viscosity constant [fu p1684]: Pa s with M in g/mol and sigma in
angstrom."""
_NEUFELD = (1.16145, 0.14874, 0.52487, 0.77320, 2.16178, 2.43787)
"""Neufeld, Janzen and Aziz (1972) fit of the collision integral Omega(2,2)*."""
_PR_A = 0.45724
_PR_B = 0.07780


@dataclass(frozen=True)
class Inputs:
    """What an evaluator reads: the property's parameters in SI and the material's state.

    ``viscosity`` and ``specific_heat`` are the material's own values on the same grid, given to
    the methods that are built on them (the kinetic-theory conductivity).
    """

    values: Mapping[str, float] = field(default_factory=dict)
    arrays: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    molar_mass: float = 0.0
    T_ref: float = 300.0
    p_ref: float = 101325.0
    viscosity: Array | None = None
    specific_heat: Array | None = None

    def number(self, name: str) -> float:
        return float(self.values.get(name, 0.0))

    def array(self, name: str) -> Array:
        return np.asarray(self.arrays.get(name, ()), dtype=np.float64)

    @property
    def gas_constant(self) -> float:
        """R_u / M in J/(kg K); the caller has refused a molar mass of 0."""
        return R_UNIVERSAL / self.molar_mass


Evaluator = Callable[[Array, Array, Inputs], Array]


def _like(t: Array, p: Array, value: float) -> Array:
    return np.full(np.broadcast(t, p).shape, value, dtype=np.float64)


def _segments(breakpoints: Array, t: Array) -> NDArray[np.intp]:
    """Index of the segment each temperature falls in; the end segments extend outward."""
    index = np.searchsorted(breakpoints, t, side="right") - 1
    return np.clip(index, 0, breakpoints.size - 2)


# ---------------------------------------------------------------- shared forms


def constant(t: Array, p: Array, inp: Inputs) -> Array:
    return _like(t, p, inp.number("value"))


def polynomial(t: Array, p: Array, inp: Inputs) -> Array:
    return poly.polyval(t, inp.array("coefficients")) + 0.0 * p


def log_polynomial(t: Array, p: Array, inp: Inputs) -> Array:
    return np.exp(poly.polyval(np.log(t), inp.array("coefficients"))) + 0.0 * p


def power_law(t: Array, p: Array, inp: Inputs) -> Array:
    """value (T / T0)^n: viscosity power law, specific-heat power, exponential conductivity."""
    t0 = inp.number("reference_temperature")
    return inp.number("value") * (t / t0) ** inp.number("exponent") + 0.0 * p


def piecewise_linear(t: Array, p: Array, inp: Inputs) -> Array:
    """Linear between the table's points, held at the end values outside them [fu p1665]."""
    return np.interp(t, inp.array("table_temperatures"), inp.array("table_values")) + 0.0 * p


def piecewise_polynomial(t: Array, p: Array, inp: Inputs) -> Array:
    """One polynomial per segment of ``breakpoints``; ``degree + 1`` coefficients each."""
    breakpoints = inp.array("breakpoints")
    width = int(inp.number("degree")) + 1
    table = inp.array("coefficients").reshape(breakpoints.size - 1, width)
    rows = table[_segments(breakpoints, t)]
    result = np.zeros(t.shape, dtype=np.float64)
    for k in range(width - 1, -1, -1):  # Horner, each point with its own segment's coefficients
        result = result * t + rows[..., k]
    return result + 0.0 * p


# ---------------------------------------------------------------- density


def ideal_gas(t: Array, p: Array, inp: Inputs) -> Array:
    return p / (inp.gas_constant * t)


def incompressible_ideal_gas(t: Array, p: Array, inp: Inputs) -> Array:
    return inp.p_ref / (inp.gas_constant * t) + 0.0 * p


def boussinesq(t: Array, p: Array, inp: Inputs) -> Array:
    beta = inp.number("expansion_coefficient")
    return inp.number("value") * (1.0 - beta * (t - inp.T_ref)) + 0.0 * p


def rational_polynomial(t: Array, p: Array, inp: Inputs) -> Array:
    c0, c1, c2, c3, c4 = (float(c) for c in inp.array("coefficients"))
    return 1.0 / (c0 + c1 * t + c2 * t**2 - c3 * p - c4 * p * t)


def perfect_fluid(t: Array, p: Array, inp: Inputs) -> Array:
    return inp.number("value") + p / (inp.gas_constant * t)


def compressible_liquid(t: Array, p: Array, inp: Inputs) -> Array:
    """Tait: rho0 (1 + n (p - p_ref) / K0)^(1/n), the law of OpenFOAM's adiabaticPerfectFluid."""
    n = inp.number("exponent")
    base = 1.0 + n * (p - inp.p_ref) / inp.number("bulk_modulus")
    with np.errstate(invalid="ignore"):
        return inp.number("value") * base ** (1.0 / n) + 0.0 * t


def _largest_root(a2: Array, a1: Array, a0: Array) -> Array:
    """The largest real root of z^3 + a2 z^2 + a1 z + a0 = 0, element by element."""
    q = (3.0 * a1 - a2**2) / 9.0
    r = (9.0 * a2 * a1 - 27.0 * a0 - 2.0 * a2**3) / 54.0
    d = q**3 + r**2
    out = np.empty_like(a2)
    three = d <= 0.0
    if np.any(three):
        s = np.sqrt(-q[three])
        ratio = np.divide(r[three], s**3, out=np.zeros_like(s), where=s > 0.0)
        theta = np.arccos(np.clip(ratio, -1.0, 1.0))
        roots = np.stack([2.0 * s * np.cos((theta + 2.0 * np.pi * k) / 3.0) for k in range(3)])
        out[three] = roots.max(axis=0) - a2[three] / 3.0
    one = ~three
    if np.any(one):
        root_d = np.sqrt(d[one])
        out[one] = np.cbrt(r[one] + root_d) + np.cbrt(r[one] - root_d) - a2[one] / 3.0
    return out


def peng_robinson(t: Array, p: Array, inp: Inputs) -> Array:
    """Peng-Robinson with the vapour (largest) compressibility root, as OpenFOAM's
    PengRobinsonGas takes it; NaN where no root lies above the covolume."""
    rs = inp.gas_constant
    tc = inp.number("critical_temperature")
    pc = inp.number("critical_pressure")
    omega = inp.number("acentric_factor")
    kappa = 0.37464 + 1.54226 * omega - 0.26992 * omega**2
    alpha = (1.0 + kappa * (1.0 - np.sqrt(t / tc))) ** 2
    a = _PR_A * (rs * tc) ** 2 / pc
    b = _PR_B * rs * tc / pc
    big_a = a * alpha * p / (rs * t) ** 2
    big_b = b * p / (rs * t) + 0.0 * t
    z = _largest_root(
        big_b - 1.0, big_a - 3.0 * big_b**2 - 2.0 * big_b, -(big_a * big_b - big_b**2 - big_b**3)
    )
    z = np.where(z > big_b, z, np.nan)
    return p / (z * rs * t)


# ---------------------------------------------------------------- viscosity


def sutherland(t: Array, p: Array, inp: Inputs) -> Array:
    a_s = inp.number("sutherland_coefficient")
    return a_s * t**1.5 / (t + inp.number("sutherland_temperature")) + 0.0 * p


def andrade(t: Array, p: Array, inp: Inputs) -> Array:
    c0, c1, c2, c3, c4 = (float(c) for c in inp.array("coefficients"))
    return np.exp(c0 + c1 * t + c2 * t**2 + c3 / (c4 + t)) + 0.0 * p


def wlf(t: Array, p: Array, inp: Inputs) -> Array:
    c1, c2 = (float(c) for c in inp.array("coefficients"))
    shift = t - inp.number("reference_temperature")
    return inp.number("value") * np.exp(-c1 * shift / (c2 + shift)) + 0.0 * p


def collision_integral(t_star: Array) -> Array:
    """Omega(2,2)* of the Lennard-Jones potential (Neufeld et al. 1972), for viscosity."""
    a, b, c, d, e, f = _NEUFELD
    return a * t_star ** (-b) + c * np.exp(-d * t_star) + e * np.exp(-f * t_star)


def kinetic_theory_viscosity(t: Array, p: Array, inp: Inputs) -> Array:
    """Chapman-Enskog: 2.67e-6 sqrt(M T) / (sigma^2 Omega), M in g/mol, sigma in angstrom."""
    sigma = inp.number("collision_diameter") * 1e10
    omega = collision_integral(t / inp.number("well_depth_temperature"))
    return CHAPMAN_ENSKOG * np.sqrt(inp.molar_mass * 1e3 * t) / (sigma**2 * omega) + 0.0 * p


# ---------------------------------------------------------------- specific heat


def janaf(t: Array, p: Array, inp: Inputs) -> Array:
    """NASA-7 in two ranges: c_p = (R/M)(a1 + a2 T + a3 T^2 + a4 T^3 + a5 T^4), the low set
    below T_common and the high set from it on, as OpenFOAM's janaf selects them."""
    coeffs = inp.array("coefficients")
    t_common = float(inp.array("breakpoints")[1])
    low = poly.polyval(t, coeffs[0:5])
    high = poly.polyval(t, coeffs[7:12])
    return inp.gas_constant * np.where(t < t_common, low, high) + 0.0 * p


def nasa9_polynomial(t: Array, p: Array, inp: Inputs) -> Array:
    """NASA-9 per segment: c_p/R = a1/T^2 + a2/T + a3 + a4 T + a5 T^2 + a6 T^3 + a7 T^4."""
    breakpoints = inp.array("breakpoints")
    table = inp.array("coefficients").reshape(breakpoints.size - 1, 9)
    a = table[_segments(breakpoints, t)]
    cp_r = (
        a[..., 0] / t**2
        + a[..., 1] / t
        + a[..., 2]
        + a[..., 3] * t
        + a[..., 4] * t**2
        + a[..., 5] * t**3
        + a[..., 6] * t**4
    )
    return inp.gas_constant * cp_r + 0.0 * p


def kinetic_theory_specific_heat(t: Array, p: Array, inp: Inputs) -> Array:
    """c_p = (R/M)(f + 2)/2 with f degrees of freedom [fu p1715]."""
    return _like(t, p, 0.5 * inp.gas_constant * (inp.number("degrees_of_freedom") + 2.0))


# ---------------------------------------------------------------- conductivity


def anisotropic(t: Array, p: Array, inp: Inputs) -> Array:
    """The three principal conductivities, one row each, constant in temperature."""
    k = inp.array("principal_values")
    shape = np.broadcast(t, p).shape
    return np.stack([np.full(shape, value, dtype=np.float64) for value in k])


def kinetic_theory_conductivity(t: Array, p: Array, inp: Inputs) -> Array:
    """kappa = (15/4)(R/M) mu ((4/15) c_p M / R + 1/3) [fu p1692], from the material's own
    viscosity and specific heat on the same grid."""
    if inp.viscosity is None or inp.specific_heat is None:
        raise EvaluationFailed(
            "the kinetic-theory conductivity needs the viscosity and specific heat"
        )
    rs = inp.gas_constant
    return 3.75 * rs * inp.viscosity * (4.0 / 15.0 * inp.specific_heat / rs + 1.0 / 3.0) + 0.0 * p


EVALUATORS: dict[str, Evaluator] = {
    fn.__name__: fn
    for fn in (
        constant,
        polynomial,
        log_polynomial,
        power_law,
        piecewise_linear,
        piecewise_polynomial,
        ideal_gas,
        incompressible_ideal_gas,
        boussinesq,
        rational_polynomial,
        perfect_fluid,
        compressible_liquid,
        peng_robinson,
        sutherland,
        andrade,
        wlf,
        kinetic_theory_viscosity,
        janaf,
        nasa9_polynomial,
        kinetic_theory_specific_heat,
        anisotropic,
        kinetic_theory_conductivity,
    )
}
"""Evaluators by the name the catalogue gives (``MethodSpec.evaluator``)."""
