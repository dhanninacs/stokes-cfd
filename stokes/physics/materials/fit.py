"""Fits of library data to catalogue methods (ADR-0018 decision 4; contract "Fits").

A provider's values for one property, on the verification grid of 201 temperatures at the
material's reference pressure, become one method of the catalogue: the candidate forms are
tried in the contract's order and the first whose largest relative deviation on the whole
grid is within the tolerance wins. Every candidate is fitted on the 101 nodes (the even points
of the grid) and checked on all 201, so a fit is judged at its nodes, between them and at both
ends of the range. Nothing within the tolerance raises :class:`FitTolerance` with the best form
and the advice; ``piecewise_linear`` (the nodes themselves) is always available as a forced
form.

The numerics are the ones the built-in fluid records were generated with, so a regeneration
reproduces them: polynomials are least squares in relative residuals (weights 1/y) on the
scaled domain of ``numpy.polynomial.Polynomial.fit``; the rational density fits 1/rho weighted
by rho; the log-polynomial fits ln mu in ln T; Sutherland's law is Gauss-Newton on the relative
residuals from its linearised start; ``constant`` is the library's value at ``T_ref``; JANAF is
one NASA-7 range of degree at most four, repeated for both ranges, with a6 and a7 fixing the
formation enthalpy and entropy at 298.15 K. Every candidate is then evaluated through the
catalogue's own evaluator with the coefficients as they will be stored (raw powers of T), and a
polynomial whose raw coefficients do not reproduce the scaled fit within 1e-9 is refused as
ill-conditioned instead of being stored.

Import rule: numpy and this package (``evaluate``, ``methods``, ``errors``); no provider.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.polynomial import Polynomial

from stokes.errors import ValidationError
from stokes.physics.materials.errors import FitTolerance
from stokes.physics.materials.evaluate import Definition, State, evaluate_one
from stokes.physics.materials.methods import R_UNIVERSAL, Array

POINTS = 101
"""Fit nodes over [T_min, T_max]; the verification grid adds the midpoints between them."""
VERIFY_POINTS = 2 * POINTS - 1
RAW_TOLERANCE = 1e-9
"""Largest relative difference between the raw coefficients and the scaled fit they came from."""
MAX_DEGREE = 7
JANAF_DEGREE = 4
T_STANDARD = 298.15
"""Where a JANAF fit's a6 and a7 fix the formation enthalpy and entropy (OpenFOAM's Tstd)."""
SUTHERLAND_ITERATIONS = 50
FITTED_PROPERTIES: tuple[str, ...] = (
    "density",
    "viscosity",
    "specific_heat",
    "thermal_conductivity",
)
FORCEABLE: dict[str, tuple[str, ...]] = {
    "density": ("constant", "ideal_gas", "rational_polynomial", "polynomial", "piecewise_linear"),
    "viscosity": ("constant", "sutherland", "log_polynomial", "polynomial", "piecewise_linear"),
    "specific_heat": ("constant", "janaf", "polynomial", "piecewise_linear"),
    "thermal_conductivity": ("constant", "polynomial", "piecewise_linear"),
}
"""The forms ``methods`` may force per property; the others are not fitted from a library."""
TAKEN_AS_IS: tuple[str, ...] = ("constant", "piecewise_linear")
"""Forced forms that are accepted whatever their deviation: the value at T_ref, the grid."""
UNRANGED: tuple[str, ...] = ("ideal_gas",)
"""Forms that claim no validity range: the law holds wherever the gas is ideal."""
PHASES: tuple[str, ...] = ("gas", "liquid")


def verification_grid(t_min: float, t_max: float) -> Array:
    """The 201 temperatures a fit is checked on; ``grid[::2]`` are the 101 fit nodes."""
    if not (math.isfinite(t_min) and math.isfinite(t_max) and 0.0 < t_min < t_max):
        raise ValidationError(
            f"a fit needs 0 < T_min < T_max; got {t_min!r} K to {t_max!r} K",
            details={"T_min": t_min, "T_max": t_max},
        )
    return np.linspace(t_min, t_max, VERIFY_POINTS)


@dataclass(frozen=True)
class Nasa7:
    """A mechanism's own NASA-7 coefficients as the ``janaf`` method takes them: 7 low then 7
    high, and the breakpoints T_low, T_common, T_high (Cantera exports them; no fit needed)."""

    coefficients: tuple[float, ...]
    breakpoints: tuple[float, float, float]


@dataclass(frozen=True)
class FitInput:
    """One property's library values on the verification grid and what the fit may use.

    ``phase`` (``gas`` or ``liquid``) picks the candidate order; ``at_ref`` is the library's
    value at the material's T_ref (the ``constant`` candidate); ``method`` forces one form.
    """

    kind: str
    phase: str
    t: Array
    values: Array
    p_ref: float
    T_ref: float
    at_ref: float
    molar_mass: float = 0.0
    tolerance: float = 0.01
    max_degree: int = 4
    method: str | None = None
    formation_enthalpy: float = 0.0
    formation_entropy: float = 0.0
    nasa7: Nasa7 | None = None


@dataclass(frozen=True)
class Attempt:
    """One candidate tried: its error on the verification grid, or why it could not be used."""

    method: str
    degree: int
    max_rel_err: float | None
    note: str = ""

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "method": self.method,
            "degree": self.degree,
            "max_rel_err": self.max_rel_err,
            "note": self.note,
        }


@dataclass(frozen=True)
class FitResult:
    """The accepted form of one property: the property-node parameters to store and its
    errors on the verification grid."""

    kind: str
    method: str
    degree: int
    params: dict[str, Any]  # Any: JSON parameter values (numbers and coefficient lists)
    max_rel_err: float
    rms_rel_err: float
    tolerance: float
    forced: bool = False
    attempts: tuple[Attempt, ...] = field(default=(), repr=False)

    @property
    def ok(self) -> bool:
        return self.max_rel_err <= self.tolerance

    def entry(self) -> dict[str, Any]:  # Any: JSON
        """The property entry as ``create_command`` and the catalogue records take it."""
        return {"method": self.method, **self.params}

    def summary(self) -> dict[str, Any]:  # Any: JSON
        return {
            "method": self.method,
            "degree": self.degree,
            "max_rel_err": self.max_rel_err,
            "rms_rel_err": self.rms_rel_err,
            "tolerance": self.tolerance,
            "forced": self.forced,
        }


Built = tuple[dict[str, Any], int] | str
"""A candidate's parameters and degree, or why it cannot be used here."""


@dataclass(frozen=True)
class _Candidate:
    method: str
    degree: int
    build: Callable[[FitInput], Built]


# ---------------------------------------------------------------- numerics


def _relative(fitted: Array, values: Array) -> Array:
    with np.errstate(all="ignore"):
        return np.abs(fitted - values) / np.abs(values)


def _polyfit(x: Array, y: Array, degree: int, weights: Array | None) -> Polynomial | str:
    """A least-squares polynomial on the scaled domain, or why there is none."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", np.exceptions.RankWarning)
        try:
            series, (_, rank, _, _) = Polynomial.fit(x, y, degree, w=weights, full=True)
        except (np.linalg.LinAlgError, ValueError) as exc:
            return f"least squares failed: {exc}"
    if int(rank) < degree + 1:
        return "the nodes do not determine this degree"
    return series


def _raw(series: Polynomial, degree: int) -> list[float]:
    """Coefficients of the raw variable (T or ln T), ascending, ``degree + 1`` of them."""
    coef = np.zeros(degree + 1)
    converted = series.convert().coef
    coef[: converted.size] = converted
    return [float(c) for c in coef]


def _conditioned(series: Polynomial, raw: list[float], x: Array) -> bool:
    """Whether the raw coefficients reproduce the scaled fit on ``x`` within 1e-9."""
    scaled = series(x)
    direct = np.polynomial.polynomial.polyval(x, np.asarray(raw))
    with np.errstate(all="ignore"):
        error = np.abs(direct - scaled) / np.maximum(np.abs(scaled), np.finfo(float).tiny)
    return bool(np.all(np.isfinite(direct)) and float(error.max()) <= RAW_TOLERANCE)


def _nodes(inp: FitInput) -> tuple[Array, Array]:
    return inp.t[::2], inp.values[::2]


def _ranged(inp: FitInput, params: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    return {**params, "valid_min": float(inp.t[0]), "valid_max": float(inp.t[-1])}


# ---------------------------------------------------------------- candidates


def _constant(inp: FitInput) -> Built:
    if not (math.isfinite(inp.at_ref) and inp.at_ref > 0.0):
        return "no library value at T_ref"
    return _ranged(inp, {"value": float(inp.at_ref)}), 0


def _ideal_gas(inp: FitInput) -> Built:
    if inp.molar_mass <= 0.0:
        return "the molar mass is unknown"
    return {}, 0


def _polynomial(degree: int) -> Callable[[FitInput], Built]:
    def build(inp: FitInput) -> Built:
        t, y = _nodes(inp)
        series = _polyfit(t, y, degree, 1.0 / y)
        if isinstance(series, str):
            return series
        raw = _raw(series, degree)
        if not _conditioned(series, raw, inp.t):
            return "ill-conditioned in raw powers of T"
        return _ranged(inp, {"coefficients": raw}), degree

    return build


def _log_polynomial(degree: int) -> Callable[[FitInput], Built]:
    def build(inp: FitInput) -> Built:
        t, y = _nodes(inp)
        series = _polyfit(np.log(t), np.log(y), degree, None)
        if isinstance(series, str):
            return series
        raw = _raw(series, degree)
        if not _conditioned(series, raw, np.log(inp.t)):
            return "ill-conditioned in raw powers of ln T"
        return _ranged(inp, {"coefficients": raw}), degree

    return build


def _rational_polynomial(inp: FitInput) -> Built:
    """1/rho = C0 + C1 T + C2 T^2 (C3 = C4 = 0: no pressure terms in v1), relative weights."""
    t, y = _nodes(inp)
    series = _polyfit(t, 1.0 / y, 2, y)
    if isinstance(series, str):
        return series
    raw = _raw(series, 2)
    if not _conditioned(series, raw, inp.t):
        return "ill-conditioned in raw powers of T"
    return _ranged(inp, {"coefficients": [*raw, 0.0, 0.0]}), 2


def _sutherland(inp: FitInput) -> Built:
    """A_s and T_s by Gauss-Newton on the relative residuals, from the linearised law
    T^1.5 / mu = T / A_s + T_s / A_s."""
    t, y = _nodes(inp)
    design = np.column_stack([t, np.ones_like(t)])
    (slope, intercept), *_ = np.linalg.lstsq(design, t**1.5 / y, rcond=None)
    if slope <= 0.0:
        return "the data has no positive Sutherland coefficient"
    a_s, t_s = 1.0 / float(slope), float(intercept) / float(slope)
    with np.errstate(all="ignore"):
        for _ in range(SUTHERLAND_ITERATIONS):
            mu = a_s * t**1.5 / (t + t_s)
            residual = (mu - y) / y
            jacobian = np.column_stack([mu / (a_s * y), -mu / ((t + t_s) * y)])
            step, *_ = np.linalg.lstsq(jacobian, -residual, rcond=None)
            if not np.all(np.isfinite(step)):
                return "Gauss-Newton diverged"
            a_s, t_s = a_s + float(step[0]), t_s + float(step[1])
            if abs(step[0]) <= 1e-14 * abs(a_s) and abs(step[1]) <= 1e-14 * max(abs(t_s), 1.0):
                break
    if not (a_s > 0.0 and float(np.min(inp.t)) + t_s > 0.0):
        return "no physical Sutherland law fits"
    return _ranged(inp, {"sutherland_coefficient": a_s, "sutherland_temperature": t_s}), 0


def janaf_coefficients(
    cp_over_r: list[float], molar_mass: float, enthalpy: float, entropy: float
) -> list[float]:
    """NASA-7 a1..a7 from c_p/R coefficients (a1..a5) with h and s fixed at 298.15 K.

    ``enthalpy`` (J/kg) and ``entropy`` (J/(kg K)) are the formation values the material
    states; a6 and a7 make h(298.15 K) and s(298.15 K) equal them.
    """
    a = [*cp_over_r, *([0.0] * (5 - len(cp_over_r)))]
    t = T_STANDARD
    sensible = a[0] * t + a[1] * t**2 / 2 + a[2] * t**3 / 3 + a[3] * t**4 / 4 + a[4] * t**5 / 5
    entropic = a[0] * math.log(t) + a[1] * t + a[2] * t**2 / 2 + a[3] * t**3 / 3 + a[4] * t**4 / 4
    scale = molar_mass / R_UNIVERSAL
    return [*a, enthalpy * scale - sensible, entropy * scale - entropic]


def _janaf(degree: int) -> Callable[[FitInput], Built]:
    def build(inp: FitInput) -> Built:
        if inp.molar_mass <= 0.0:
            return "the molar mass is unknown"
        t, y = _nodes(inp)
        series = _polyfit(t, y, degree, 1.0 / y)
        if isinstance(series, str):
            return series
        raw = _raw(series, degree)
        if not _conditioned(series, raw, inp.t):
            return "ill-conditioned in raw powers of T"
        scale = inp.molar_mass / R_UNIVERSAL
        seven = janaf_coefficients(
            [c * scale for c in raw], inp.molar_mass, inp.formation_enthalpy, inp.formation_entropy
        )
        middle = 0.5 * (float(inp.t[0]) + float(inp.t[-1]))
        params = {
            "coefficients": seven + seven,
            "breakpoints": [float(inp.t[0]), middle, float(inp.t[-1])],
            "formation_entropy": inp.formation_entropy,
        }
        return _ranged(inp, params), degree

    return build


def _exact_janaf(inp: FitInput) -> Built:
    """The mechanism's own NASA-7 coefficients (Cantera): nothing is fitted."""
    nasa = inp.nasa7
    if nasa is None:
        return "the provider has no NASA-7 coefficients for this composition"
    params = {
        "coefficients": list(nasa.coefficients),
        "breakpoints": list(nasa.breakpoints),
        "formation_entropy": inp.formation_entropy,
    }
    return _ranged(inp, params), JANAF_DEGREE


def _piecewise_linear(inp: FitInput) -> Built:
    t, y = _nodes(inp)
    params = {
        "table_temperatures": [float(v) for v in t],
        "table_values": [float(v) for v in y],
    }
    return _ranged(inp, params), 1


# ---------------------------------------------------------------- the order


def _degrees(first: int, inp: FitInput, cap: int | None = None) -> range:
    last = min(inp.max_degree, MAX_DEGREE) if cap is None else min(inp.max_degree, cap)
    return range(first, last + 1)


def candidates(inp: FitInput) -> list[_Candidate]:
    """The contract's candidate order for this property and phase, or the forced form."""
    if inp.method is not None:
        return _forced(inp)
    gas = inp.phase == "gas"
    poly = [_Candidate("polynomial", d, _polynomial(d)) for d in _degrees(1, inp)]
    constant = _Candidate("constant", 0, _constant)
    logs = [_Candidate("log_polynomial", d, _log_polynomial(d)) for d in _degrees(2, inp)]
    if inp.kind == "density":
        first = (
            [_Candidate("ideal_gas", 0, _ideal_gas)]
            if gas
            else [constant, _Candidate("rational_polynomial", 2, _rational_polynomial)]
        )
        return [*first, *poly]
    if inp.kind == "viscosity":
        if gas:
            return [_Candidate("sutherland", 0, _sutherland), *logs, *poly[1:]]
        return [*logs, *poly[2:]]
    if inp.kind == "specific_heat" and gas:
        janaf = (
            [_Candidate("janaf", JANAF_DEGREE, _exact_janaf)]
            if inp.nasa7 is not None
            else [_Candidate("janaf", d, _janaf(d)) for d in _degrees(1, inp, JANAF_DEGREE)]
        )
        return [constant, *janaf, *poly]
    return [constant, *poly]


def _forced(inp: FitInput) -> list[_Candidate]:
    method = str(inp.method)
    allowed = FORCEABLE.get(inp.kind, ())
    if method not in allowed:
        raise ValidationError(
            f"{inp.kind} cannot be fitted as {method!r}; choose one of {', '.join(allowed)}",
            details={"property": inp.kind, "method": method},
        )
    single: dict[str, Callable[[FitInput], Built]] = {
        "constant": _constant,
        "ideal_gas": _ideal_gas,
        "rational_polynomial": _rational_polynomial,
        "sutherland": _sutherland,
        "piecewise_linear": _piecewise_linear,
    }
    if method in single:
        return [_Candidate(method, 0, single[method])]
    if method == "janaf":
        if inp.nasa7 is not None:
            return [_Candidate("janaf", JANAF_DEGREE, _exact_janaf)]
        return [_Candidate("janaf", d, _janaf(d)) for d in _degrees(1, inp, JANAF_DEGREE)]
    if method == "log_polynomial":
        return [_Candidate(method, d, _log_polynomial(d)) for d in _degrees(1, inp)]
    return [_Candidate(method, d, _polynomial(d)) for d in _degrees(0, inp)]


# ---------------------------------------------------------------- the fit


def _check_input(inp: FitInput) -> None:
    if inp.phase not in PHASES:
        raise ValidationError(f"phase must be gas or liquid for a fit; got {inp.phase!r}")
    if inp.t.shape != inp.values.shape or inp.t.size != VERIFY_POINTS:
        raise ValidationError(f"a fit needs {VERIFY_POINTS} temperatures and as many values")
    if not (np.all(np.isfinite(inp.values)) and np.all(inp.values > 0.0)):
        raise ValidationError(
            f"{inp.kind}: the library values must be finite and positive to be fitted",
            details={"property": inp.kind},
        )
    if not 0.0 < inp.tolerance < 1.0:
        raise ValidationError(f"tolerance must lie between 0 and 1; got {inp.tolerance!r}")
    if not 0 <= inp.max_degree <= MAX_DEGREE:
        raise ValidationError(f"max_degree must be 0 to {MAX_DEGREE}; got {inp.max_degree!r}")


def _evaluate(inp: FitInput, method: str, params: dict[str, Any]) -> Array:  # Any: JSON params
    """The candidate as the catalogue evaluates it, coefficients exactly as stored."""
    values: dict[str, float] = {}
    arrays: dict[str, tuple[float, ...]] = {}
    for name, raw in params.items():
        if isinstance(raw, list):
            arrays[name] = tuple(float(v) for v in raw)
        else:
            values[name] = float(raw)
    state = State(
        kind="fluid",
        T_ref=inp.T_ref,
        p_ref=inp.p_ref,
        T_min=float(inp.t[0]),
        T_max=float(inp.t[-1]),
        molar_mass=inp.molar_mass,
    )
    definition = Definition(inp.kind, method, values, arrays)
    return evaluate_one(definition, state, inp.t, np.full_like(inp.t, inp.p_ref))


def fit_property(inp: FitInput) -> FitResult:
    """The first candidate within the tolerance (a forced constant or table: that form)."""
    _check_input(inp)
    attempts: list[Attempt] = []
    best: FitResult | None = None
    forced = inp.method is not None
    for candidate in candidates(inp):
        built = candidate.build(inp)
        if isinstance(built, str):
            attempts.append(Attempt(candidate.method, candidate.degree, None, built))
            continue
        params, degree = built
        try:
            fitted = _evaluate(inp, candidate.method, params)
        except ValidationError as exc:
            attempts.append(Attempt(candidate.method, degree, None, exc.message))
            continue
        relative = _relative(fitted, inp.values)
        if not np.all(np.isfinite(relative)):
            attempts.append(Attempt(candidate.method, degree, None, "not finite on the range"))
            continue
        worst = float(relative.max())
        attempts.append(Attempt(candidate.method, degree, worst))
        result = FitResult(
            kind=inp.kind,
            method=candidate.method,
            degree=degree,
            params=params,
            max_rel_err=worst,
            rms_rel_err=float(np.sqrt(np.mean(relative**2))),
            tolerance=inp.tolerance,
            forced=forced,
        )
        if worst <= inp.tolerance or (forced and candidate.method in TAKEN_AS_IS):
            return _with_attempts(result, attempts)
        if best is None or worst < best.max_rel_err:
            best = result
    raise _refusal(inp, best, attempts)


def _with_attempts(result: FitResult, attempts: list[Attempt]) -> FitResult:
    return FitResult(
        kind=result.kind,
        method=result.method,
        degree=result.degree,
        params=result.params,
        max_rel_err=result.max_rel_err,
        rms_rel_err=result.rms_rel_err,
        tolerance=result.tolerance,
        forced=result.forced,
        attempts=tuple(attempts),
    )


def _refusal(inp: FitInput, best: FitResult | None, attempts: list[Attempt]) -> FitTolerance:
    span = f"{float(inp.t[0]):g} K to {float(inp.t[-1]):g} K"
    if best is None:
        found = "no form could be fitted"
        best_json: dict[str, Any] | None = None  # Any: JSON
    else:
        found = f"the best was {best.method} (degree {best.degree}) at {best.max_rel_err:.3g}"
        best_json = best.summary()
    return FitTolerance(
        f"{inp.kind}: no form meets the tolerance {inp.tolerance:.3g} over {span}; {found}. "
        f"Narrow the range, raise max_degree (now {inp.max_degree}), or choose "
        "piecewise_linear, which is exact at the 101 nodes",
        details={
            "property": inp.kind,
            "tolerance": inp.tolerance,
            "max_degree": inp.max_degree,
            "best": best_json,
            "attempts": [a.to_json() for a in attempts],
        },
    )
