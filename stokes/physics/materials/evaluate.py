"""Evaluation of materials on a temperature grid (ADR-0018 decision 3; contract "Evaluation").

A property is read into a :class:`Definition` (its method and its parameters in SI), from a
``property`` node or from a library record, and a material's level inputs into a :class:`State`
(kind, reference state, range, molar mass). :func:`validate` checks a definition against its
catalogue entry before anything is evaluated, so the evaluators in ``methods`` never see a
malformed table or a missing molar mass; :func:`evaluate_all` evaluates several properties of one
material, giving the kinetic-theory conductivity the material's own viscosity and specific heat.

``material.evaluate`` (previews and plots), ``material.check`` (findings), the built-in provider
and, later, the fits, the compiler's tabulation and the ``function`` engine all go through here.

Import rule: numpy, ``stokes.model``, ``stokes.units`` and this package.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np

from stokes.errors import ValidationError
from stokes.model.nodes import Node, QuantityValue
from stokes.physics.materials import catalogue
from stokes.physics.materials.catalogue import MethodSpec
from stokes.physics.materials.errors import EvaluationFailed, NeedsMolarMass
from stokes.physics.materials.methods import Array, Inputs
from stokes.physics.materials.model import KIND_PROPERTIES

MAX_COEFFICIENTS = 8
"""OpenFOAM's and Fluent's polynomials take eight coefficients."""
MAX_DEGREE = 7
MAX_SEGMENTS = 8
MAX_TABLE = 1000
MAX_POINTS = 10001
"""Largest temperature grid ``material.evaluate`` accepts."""
ARRAY_PARAMS = frozenset(
    {"coefficients", "breakpoints", "table_temperatures", "table_values", "principal_values"}
)
POSITIVE_PROPERTIES = frozenset({"density", "viscosity", "specific_heat", "thermal_conductivity"})
_FIXED_COEFFICIENTS = {"andrade": 5, "rational_polynomial": 5, "wlf": 2}


@dataclass(frozen=True)
class State:
    """A material's own inputs: its kind, reference state, range and molar mass (kg/mol, 0 when
    unknown)."""

    kind: str
    T_ref: float = 300.0
    p_ref: float = 101325.0
    T_min: float = 273.15
    T_max: float = 373.15
    molar_mass: float = 0.0


@dataclass(frozen=True)
class Definition:
    """One property as a method and its parameters in SI."""

    property: str
    method: str
    values: Mapping[str, float] = field(default_factory=dict)
    arrays: Mapping[str, tuple[float, ...]] = field(default_factory=dict)


# ---------------------------------------------------------------- reading


def _si(node: Node, name: str, default: float = 0.0) -> float:
    value = node.params.get(name)
    return float(value.value) if isinstance(value, QuantityValue) else default


def split_params(
    params: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, tuple[float, ...]]]:  # Any: JSON
    """Scalars and arrays of a parameter mapping, in SI; strings and enums are left out."""
    values: dict[str, float] = {}
    arrays: dict[str, tuple[float, ...]] = {}
    for name, raw in params.items():
        if isinstance(raw, QuantityValue):
            values[name] = float(raw.value)
        elif isinstance(raw, bool):
            continue
        elif isinstance(raw, int | float):
            values[name] = float(raw)
        elif isinstance(raw, list | tuple):
            arrays[name] = tuple(float(v) for v in raw)
    return values, arrays


def definition_of(node: Node) -> Definition:
    """The definition a ``property`` node holds."""
    values, arrays = split_params(node.params)
    return Definition(str(node.params.get("kind")), str(node.params.get("method")), values, arrays)


def definition_from_entry(property_kind: str, entry: Mapping[str, Any]) -> Definition:  # Any: JSON
    """The definition a library record holds for one property (``{"method": ..., ...}``)."""
    values, arrays = split_params({k: v for k, v in entry.items() if k != "method"})
    return Definition(property_kind, str(entry.get("method", "")), values, arrays)


def molar_mass_of(definitions: Mapping[str, Definition]) -> float:
    found = definitions.get("molar_mass")
    if found is None or found.method != "constant":
        return 0.0
    return float(found.values.get("value", 0.0))


def state_of(material: Node) -> State:
    """The material-level inputs of a ``material`` node."""
    child = material.child("molar_mass")
    molar_mass = 0.0
    if child is not None and child.type == "property" and child.params.get("method") == "constant":
        molar_mass = _si(child, "value")
    return State(
        kind=str(material.params.get("kind", "fluid")),
        T_ref=_si(material, "T_ref", 300.0),
        p_ref=_si(material, "p_ref", 101325.0),
        T_min=_si(material, "T_min", 273.15),
        T_max=_si(material, "T_max", 373.15),
        molar_mass=molar_mass,
    )


def definitions_of(material: Node) -> dict[str, Definition]:
    """The definitions of a material's property children, by property kind. A child whose name
    and kind disagree is left out: ``material.check`` reports it."""
    taken = KIND_PROPERTIES.get(str(material.params.get("kind")), ())
    found: dict[str, Definition] = {}
    for child in material.children:
        if (
            child.type == "property"
            and child.name in taken
            and child.params.get("kind") == child.name
        ):
            found[child.name] = definition_of(child)
    return found


# ---------------------------------------------------------------- validation


def _increasing(values: tuple[float, ...]) -> bool:
    return all(b > a for a, b in pairwise(values))


def _fail(spec: MethodSpec, message: str) -> ValidationError:
    return ValidationError(
        f"{spec.property_kind} method {spec.id}: {message}",
        details={"property": spec.property_kind, "method": spec.id},
    )


def _check_scalars(definition: Definition, spec: MethodSpec) -> None:
    for use in spec.params:
        if not use.required or use.name in ARRAY_PARAMS:
            continue
        value = definition.values.get(use.name, 0.0)
        if not math.isfinite(value):
            raise _fail(spec, f"{use.name} is not a finite number")
        if use.rule == "positive" and value <= 0.0:
            raise _fail(spec, f"{use.name} must be positive; got {value!r}")
        if use.rule == "nonnegative" and value < 0.0:
            raise _fail(spec, f"{use.name} must not be negative; got {value!r}")
        if use.rule == "nonzero" and value == 0.0:
            raise _fail(spec, f"{use.name} must not be zero")


def _segments(spec: MethodSpec, breakpoints: tuple[float, ...]) -> int:
    count = len(breakpoints) - 1
    if not 1 <= count <= MAX_SEGMENTS:
        raise _fail(spec, f"breakpoints must bound 1 to {MAX_SEGMENTS} segments; got {count}")
    if not _increasing(breakpoints) or breakpoints[0] <= 0.0:
        raise _fail(spec, "breakpoints must be positive temperatures in increasing order")
    return count


def _polynomial(definition: Definition, spec: MethodSpec) -> None:
    count = len(definition.arrays.get("coefficients", ()))
    if not 1 <= count <= MAX_COEFFICIENTS:
        raise _fail(spec, f"needs 1 to {MAX_COEFFICIENTS} coefficients; got {count}")


def _fixed(definition: Definition, spec: MethodSpec) -> None:
    count = len(definition.arrays.get("coefficients", ()))
    wanted = _FIXED_COEFFICIENTS[spec.id]
    if count != wanted:
        raise _fail(spec, f"needs {wanted} coefficients; got {count}")


def _janaf(definition: Definition, spec: MethodSpec) -> None:
    count = len(definition.arrays.get("coefficients", ()))
    if count != 14:
        raise _fail(spec, f"needs 14 coefficients (7 low, then 7 high); got {count}")
    breakpoints = definition.arrays.get("breakpoints", ())
    if len(breakpoints) != 3:
        raise _fail(spec, "breakpoints must be T_low, T_common and T_high")
    _segments(spec, breakpoints)


def _nasa9(definition: Definition, spec: MethodSpec) -> None:
    segments = _segments(spec, definition.arrays.get("breakpoints", ()))
    if len(definition.arrays.get("coefficients", ())) != 9 * segments:
        raise _fail(spec, f"needs 9 coefficients per segment ({9 * segments})")


def _piecewise_polynomial(definition: Definition, spec: MethodSpec) -> None:
    segments = _segments(spec, definition.arrays.get("breakpoints", ()))
    degree = int(definition.values.get("degree", 0.0))
    if not 0 <= degree <= MAX_DEGREE:
        raise _fail(spec, f"degree must be 0 to {MAX_DEGREE}; got {degree}")
    if len(definition.arrays.get("coefficients", ())) != segments * (degree + 1):
        raise _fail(spec, f"needs degree + 1 coefficients per segment ({segments * (degree + 1)})")


def _piecewise_linear(definition: Definition, spec: MethodSpec) -> None:
    temperatures = definition.arrays.get("table_temperatures", ())
    if not 2 <= len(temperatures) <= MAX_TABLE:
        raise _fail(spec, f"the table needs 2 to {MAX_TABLE} points; got {len(temperatures)}")
    if len(definition.arrays.get("table_values", ())) != len(temperatures):
        raise _fail(spec, "table_values needs one value per table temperature")
    if not _increasing(temperatures) or temperatures[0] <= 0.0:
        raise _fail(spec, "table_temperatures must be positive and increasing")


def _anisotropic(definition: Definition, spec: MethodSpec) -> None:
    principal = definition.arrays.get("principal_values", ())
    if len(principal) != 3 or min(principal) <= 0.0:
        raise _fail(spec, "needs three positive principal_values")


_SHAPES: dict[str, Callable[[Definition, MethodSpec], None]] = {
    "polynomial": _polynomial,
    "log_polynomial": _polynomial,
    "andrade": _fixed,
    "rational_polynomial": _fixed,
    "wlf": _fixed,
    "janaf": _janaf,
    "nasa9_polynomial": _nasa9,
    "piecewise_polynomial": _piecewise_polynomial,
    "piecewise_linear": _piecewise_linear,
    "anisotropic": _anisotropic,
}
"""The array rules of the methods that take arrays, by method id."""


def _check_arrays(definition: Definition, spec: MethodSpec) -> None:
    for name in ARRAY_PARAMS:
        if not all(math.isfinite(v) for v in definition.arrays.get(name, ())):
            raise _fail(spec, f"{name} holds a value that is not finite")
    shape = _SHAPES.get(spec.id)
    if shape is not None:
        shape(definition, spec)


def validate(definition: Definition, spec: MethodSpec, state: State) -> None:
    """Refuse a definition the method cannot be evaluated with: ``ValidationError`` for a
    parameter, :class:`NeedsMolarMass` when the method needs the molar mass and it is 0."""
    _check_scalars(definition, spec)
    _check_arrays(definition, spec)
    if spec.needs_molar_mass and state.molar_mass <= 0.0:
        raise NeedsMolarMass(
            f"{spec.property_kind} method {spec.id} needs the material's molar mass, which is 0; "
            "set it with material.set_method on molar_mass",
            details={"property": spec.property_kind, "method": spec.id},
        )


def resolve(definition: Definition, state: State) -> MethodSpec:
    """The catalogue entry of a definition, checked for the material kind and validated."""
    spec = catalogue.method(definition.property, definition.method)
    catalogue.check_choice(spec, state.kind)
    validate(definition, spec, state)
    return spec


# ---------------------------------------------------------------- evaluation


def temperature_grid(t_min: float, t_max: float, count: int) -> Array:
    if not 2 <= count <= MAX_POINTS:
        raise ValidationError(f"count must be 2 to {MAX_POINTS}; got {count}")
    if not (math.isfinite(t_min) and math.isfinite(t_max) and 0.0 < t_min < t_max):
        raise ValidationError(
            f"the temperature range needs 0 < T_min < T_max; got {t_min!r} to {t_max!r} K"
        )
    return np.linspace(t_min, t_max, count)


def evaluate_one(
    definition: Definition,
    state: State,
    t: Array,
    p: Array,
    *,
    viscosity: Array | None = None,
    specific_heat: Array | None = None,
) -> Array:
    """One property on the grid; non-finite values are left in for the caller to judge."""
    spec = resolve(definition, state)
    inputs = Inputs(
        values=definition.values,
        arrays=definition.arrays,
        molar_mass=state.molar_mass,
        T_ref=state.T_ref,
        p_ref=state.p_ref,
        viscosity=viscosity,
        specific_heat=specific_heat,
    )
    with np.errstate(all="ignore"):
        result = spec.evaluate(t, p, inputs)
    return np.asarray(result, dtype=np.float64)


def evaluate_all(
    definitions: Mapping[str, Definition],
    state: State,
    kinds: Sequence[str],
    t: Array,
    p: Array,
) -> dict[str, Array]:
    """Several properties of one material on one grid, each evaluated once; a property built on
    others (the kinetic-theory conductivity) is given theirs."""
    results: dict[str, Array] = {}
    pending: set[str] = set()

    def get(kind: str) -> Array:
        if kind in results:
            return results[kind]
        definition = definitions.get(kind)
        if definition is None:
            raise EvaluationFailed(
                f"the material has no {kind} property", details={"property": kind}
            )
        if kind in pending:
            raise EvaluationFailed(f"{kind} depends on itself", details={"property": kind})
        pending.add(kind)
        spec = catalogue.method(definition.property, definition.method)
        extra: dict[str, Array] = {}
        for need in ("viscosity", "specific_heat"):
            if need in spec.uses:
                extra[need] = get(need)
        results[kind] = evaluate_one(definition, state, t, p, **extra)
        pending.discard(kind)
        return results[kind]

    return {kind: get(kind) for kind in kinds}


def json_values(values: Array) -> list[Any]:  # Any: floats, None for gaps, or rows of them
    """A grid result as JSON: non-finite values become null (a gap in a plot)."""
    if values.ndim > 1:
        return [json_values(row) for row in values]
    return [float(v) if math.isfinite(v) else None for v in values.tolist()]


def validity(definition: Definition) -> tuple[float, float] | None:
    """Where a method is trusted: ``valid_min``..``valid_max`` when set, else the range its own
    table or segments span, else None (no claim)."""
    low = definition.values.get("valid_min", 0.0)
    high = definition.values.get("valid_max", 0.0)
    if high > 0.0:
        return low, high
    arrays = definition.arrays
    if definition.method == "piecewise_linear" and len(arrays.get("table_temperatures", ())) >= 2:
        table = arrays["table_temperatures"]
        return table[0], table[-1]
    if definition.method in ("piecewise_polynomial", "nasa9_polynomial", "janaf"):
        breakpoints = arrays.get("breakpoints", ())
        if len(breakpoints) >= 2:
            return breakpoints[0], breakpoints[-1]
    return None
