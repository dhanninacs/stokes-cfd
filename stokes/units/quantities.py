"""Quantity codec: text in, pint quantity out, SI floats for storage.

Accepted text forms for a plain value (no operators):

- ``25 mm``, ``25 [mm]``, ``1e-3 [Pa*s]``, ``40 degC``
- a bare number, interpreted in ``default_unit`` (the caller passes the
  display unit, or the SI unit for headless callers)

Anything else (operators, names, function calls) is an expression and goes
through :mod:`stokes.units.expressions`.
"""

from __future__ import annotations

import re
from typing import Any

from stokes.errors import UnitMismatch, ValidationError
from stokes.units.dimensions import Dimension
from stokes.units.registry import Quantity, ureg

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_QUANTITY_RE = re.compile(rf"^\s*({_NUMBER})\s*(?:\[([^\[\]]*)\]|([^\[\]]*?))\s*$")
_MAX_TEXT = 512


def is_quantity_text(text: str) -> bool:
    """True when ``text`` is a number with an optional unit (not an expression)."""
    match = _QUANTITY_RE.match(text)
    if not match:
        return False
    unit = (match.group(2) or match.group(3) or "").strip()
    if not unit:
        return True
    try:
        ureg().parse_units(unit)
    except Exception:
        return False
    return True


def quantity(value: float, unit: str) -> Quantity:
    """Build a quantity; a typed error for unknown units."""
    try:
        return ureg().Quantity(value, unit)
    except Exception as exc:
        raise ValidationError(f"unknown unit {unit!r}", details={"unit": unit}) from exc


def parse_quantity(text: str, dim: Dimension, default_unit: str | None = None) -> Quantity:
    """Parse ``text`` as a quantity of dimension ``dim``.

    A bare number takes ``default_unit`` (or the SI unit). Units in square
    brackets or after a space are both accepted here; expressions are not.
    """
    if len(text) > _MAX_TEXT:
        raise ValidationError("value text too long", details={"length": len(text)})
    match = _QUANTITY_RE.match(text)
    if not match:
        raise ValidationError(
            f"not a quantity: {text!r} (expected a number with an optional unit)",
            details={"text": text},
        )
    number = float(match.group(1))
    unit = (match.group(2) or match.group(3) or "").strip() or (default_unit or dim.si_unit)
    q = quantity(number, unit) if unit else ureg().Quantity(number)
    check_dimension(q, dim)
    return q


def check_dimension(q: Quantity, dim: Dimension) -> None:
    """Raise :class:`UnitMismatch` unless ``q`` has dimension ``dim``."""
    if q.dimensionality != dim.dimensionality:
        raise UnitMismatch(
            f"expected {dim.name} ({dim.si_unit or 'dimensionless'}), got {q.units}",
            details={"expected": dim.name, "got": str(q.units)},
        )


def to_si(q: Quantity, dim: Dimension) -> float:
    """Magnitude of ``q`` in the dimension's SI storage unit."""
    check_dimension(q, dim)
    if dim.si_unit == "":
        return float(q.to("dimensionless").magnitude)
    return float(q.to(dim.si_unit).magnitude)


def format_quantity(q: Quantity, unit: str | None = None, precision: int = 6) -> str:
    """Human text in ``unit`` (or the quantity's own unit), e.g. ``2.5 m/s``."""
    target = q.to(unit) if unit else q
    magnitude = float(target.magnitude)
    text = f"{magnitude:.{precision}g}"
    symbol = f"{target.units:~C}".strip()
    return f"{text} {symbol}".strip() if symbol and symbol != "dimensionless" else text


def as_float_si(value: Any, dim: Dimension) -> float:  # Any: raw JSON number or text
    """Coerce a JSON value (number or quantity text) to an SI float of ``dim``."""
    if isinstance(value, bool):
        raise ValidationError("a boolean is not a quantity")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return to_si(parse_quantity(value, dim), dim)
    raise ValidationError(f"cannot read a quantity from {type(value).__name__}")
