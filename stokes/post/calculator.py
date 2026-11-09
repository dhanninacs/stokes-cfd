"""The field calculator: a custom field function evaluated point by point (Fluent's *custom field
functions*, Discovery's *expressions*).

``evaluate_field("0.5 * U_mag^2 + p", names)`` gives the total kinematic pressure at every point of
a surface, with its units. **The text is parsed by the one expression grammar**
(:func:`stokes.units.expressions.parse`, ADR-0012) - no ``eval`` - and this module walks that tree
with numpy arrays inside pint quantities, so a sum of a speed and a pressure is refused with the
same :class:`~stokes.errors.UnitMismatch` a model parameter would get.

Names a caller provides (``stokes.api.commands.results`` does): every field the run has (``U_mag``,
``p``, ``T``...), the velocity's components ``U_x``, ``U_y``, ``U_z``, the point coordinates ``x``,
``y``, ``z`` in metres, ``rho`` when the density is known, and the constants ``pi`` and ``e``.
Functions: ``sqrt abs min max clamp sin cos tan exp log log10``, element by element.

Units arrive as OpenFOAM writes them (``m2/s2``); :func:`unit_text` makes them pint's, and
:func:`foam_units` turns a result's units back into that compact form for a legend.

Pure and headless: numpy, pint and lark, all imported inside the functions.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stokes.errors import ExpressionError, UnitMismatch

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

    from stokes.units.registry import Quantity

_POWER = re.compile(r"([A-Za-z])(-?\d+)")


def unit_text(units: str) -> str:
    """OpenFOAM's compact unit text as pint reads it: ``m2/s2`` -> ``m**2/s**2``."""
    return _POWER.sub(r"\1**\2", units.strip())


def foam_units(units: str) -> str:
    """pint's unit text in OpenFOAM's compact form: ``meter ** 2 / second ** 2`` -> ``m2/s2``."""
    return units.replace(" ", "").replace("**", "")


@dataclass(frozen=True)
class FieldResult:
    """A calculated field: ``values (P,)`` in SI base units, and the units' compact text."""

    values: np.ndarray
    units: str


def _quantity(values: Any, units: str) -> Quantity:  # Any: a numpy array or a float
    from stokes.units.registry import ureg

    registry = ureg()
    try:
        return registry.Quantity(values, unit_text(units) or "dimensionless").to_base_units()
    except Exception as exc:
        raise ExpressionError(f"unknown unit {units!r}") from exc


def _same_dims(a: Quantity, b: Quantity, op: str) -> None:
    if a.dimensionality != b.dimensionality:
        raise UnitMismatch(
            f"cannot apply {op} to {a.units} and {b.units}",
            details={"left": str(a.units), "right": str(b.units)},
        )


def _plain(q: Quantity, what: str) -> Any:  # Any: a numpy array or a float
    if not q.dimensionless:
        raise UnitMismatch(f"{what} needs a dimensionless argument, got {q.units}")
    return q.to("dimensionless").magnitude


def _functions() -> dict[str, Callable[..., Quantity]]:
    import numpy as np

    from stokes.units.registry import ureg

    registry = ureg()

    def plain(fn: Callable[[Any], Any], name: str) -> Callable[[Quantity], Quantity]:
        return lambda x: registry.Quantity(fn(_plain(x, f"{name}()")))

    def angle(fn: Callable[[Any], Any], name: str) -> Callable[[Quantity], Quantity]:
        def go(x: Quantity) -> Quantity:
            if x.dimensionless:
                return registry.Quantity(fn(x.to("dimensionless").magnitude))
            try:
                return registry.Quantity(fn(x.to("rad").magnitude))
            except Exception as exc:
                raise UnitMismatch(f"{name}() needs an angle or a plain number") from exc

        return go

    def pairwise(fn: Callable[[Any, Any], Any], name: str) -> Callable[..., Quantity]:
        def go(*xs: Quantity) -> Quantity:
            if not xs:
                raise ExpressionError(f"{name}() needs at least one argument")
            out = xs[0]
            for x in xs[1:]:
                _same_dims(out, x, f"{name}()")
                out = registry.Quantity(fn(out.magnitude, x.to(out.units).magnitude), out.units)
            return out

        return go

    def clamp(x: Quantity, lo: Quantity, hi: Quantity) -> Quantity:
        _same_dims(x, lo, "clamp()")
        _same_dims(x, hi, "clamp()")
        return registry.Quantity(
            np.clip(x.magnitude, lo.to(x.units).magnitude, hi.to(x.units).magnitude), x.units
        )

    return {
        "sqrt": lambda x: x**0.5,
        "abs": abs,
        "min": pairwise(np.minimum, "min"),
        "max": pairwise(np.maximum, "max"),
        "clamp": clamp,
        "sin": angle(np.sin, "sin"),
        "cos": angle(np.cos, "cos"),
        "tan": angle(np.tan, "tan"),
        "exp": plain(np.exp, "exp"),
        "log": plain(np.log, "log"),
        "log10": plain(np.log10, "log10"),
    }


def names_of(text: str) -> list[str]:
    """The names an expression reads (constants left out), for a caller to fetch only those."""
    from stokes.units.expressions import references

    return references(text)


def evaluate_field(  # noqa: PLR0915 - one tree walk, one branch per grammar rule
    text: str,
    names: dict[str, tuple[Any, str]],  # Any: a numpy array (P,) or a float
    points: int,
) -> FieldResult:
    """Evaluate ``text`` at every point; ``names`` maps a name to ``(values, units text)``.

    The answer always has ``points`` values, so a constant expression fills the surface. Refused
    with :class:`~stokes.errors.ExpressionError` for a name not in ``names``, a non-finite result
    everywhere, or a vector (a name whose values are not one per point).
    """
    import numpy as np

    from stokes.units.expressions import CONSTANTS, parse
    from stokes.units.quantities import quantity
    from stokes.units.registry import ureg

    tree = parse(text)
    registry = ureg()
    functions = _functions()

    # One branch and one return per rule of `grammar.lark`; splitting it would hide the grammar.
    def walk(node: Any) -> Any:  # noqa: PLR0911, PLR0912  # Any: lark Tree -> Quantity / tuple
        kind = getattr(node, "data", None)
        kids = list(getattr(node, "children", ()))
        if kind == "number":
            return registry.Quantity(float(kids[0]))
        if kind == "quantity":
            try:
                return quantity(float(kids[0]), str(kids[1]).strip()).to_base_units()
            except Exception:
                return _quantity(float(kids[0]), str(kids[1]))
        if kind == "ref":
            return walk(kids[0])
        if kind == "path":
            name = ".".join(str(k) for k in kids)
            if name in CONSTANTS:
                return registry.Quantity(CONSTANTS[name])
            if name not in names:
                known = ", ".join(sorted(names))
                raise ExpressionError(
                    f"unknown name {name!r}; known: {known}", details={"name": name}
                )
            values, units = names[name]
            array = np.asarray(values, dtype=np.float64)
            if array.ndim not in (0, 1) or (array.ndim == 1 and array.shape[0] != points):
                raise ExpressionError(f"{name} is not one number per point; use a component")
            return _quantity(array, units)
        if kind == "args":
            return tuple(walk(k) for k in kids)
        if kind == "call":
            fn = functions.get(str(kids[0]))
            if fn is None:
                raise ExpressionError(
                    f"unknown function {kids[0]!s}(); known: {', '.join(sorted(functions))}"
                )
            args = walk(kids[1]) if kids[1] is not None else ()
            try:
                return fn(*args)
            except TypeError as exc:
                raise ExpressionError(f"wrong number of arguments for {kids[0]!s}()") from exc
        if kind in ("add", "sub"):
            a, b = walk(kids[0]), walk(kids[1])
            _same_dims(a, b, "+" if kind == "add" else "-")
            return a + b if kind == "add" else a - b
        if kind == "mul":
            return walk(kids[0]) * walk(kids[1])
        if kind == "div":
            a, b = walk(kids[0]), walk(kids[1])
            with np.errstate(divide="ignore", invalid="ignore"):
                return a / b
        if kind == "pow":
            base, exponent = walk(kids[0]), walk(kids[2])
            power = np.asarray(_plain(exponent, "an exponent"))
            if power.ndim != 0:
                raise ExpressionError("an exponent must be one number, not a field")
            return base ** float(power)
        if kind == "neg":
            return -walk(kids[0])
        if kind == "pos":
            return walk(kids[0])
        raise ExpressionError(f"cannot evaluate {text!r}")

    with np.errstate(invalid="ignore", over="ignore"):
        result = walk(tree)
    result = result.to_base_units()
    magnitude = np.asarray(result.magnitude, dtype=np.float64)
    values = np.broadcast_to(magnitude, (points,)).copy() if magnitude.ndim == 0 else magnitude
    if values.shape != (points,):
        raise ExpressionError("the expression does not give one number per point")
    if points and not np.isfinite(values).any():
        raise ExpressionError("the expression is not a finite number anywhere on the surface")
    units = "" if result.dimensionless else foam_units(f"{result.units:~C}")
    for named in _NAMED_UNITS:
        if (
            not result.dimensionless
            and result.dimensionality == registry.Quantity(1, named).dimensionality
        ):
            units = named
            break
    return FieldResult(values=values, units=units)


#: Derived SI units a result is labelled with when its dimension is theirs (the number is the same,
#: as everything here is in base SI): `rho * p` reads `Pa`, not `kg/m/s2`.
_NAMED_UNITS: tuple[str, ...] = ("Pa", "N", "J", "W")
