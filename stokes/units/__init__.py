"""Quantities, unit systems and the expression language (ADR-0012).

Public surface:

- :func:`dimension` and :data:`DIMENSIONS` — the named dimensions a parameter can have.
- :func:`parse_quantity`, :func:`to_si`, :func:`format_quantity` — the text codec.
- :func:`evaluate`, :func:`references` — unit-checked expressions such as
  ``2 [m/s] * 1.5`` or ``0.5 * rho * U_in**2``.
- :data:`UNIT_SYSTEMS` — display unit presets (``si``, ``mm``, ``imperial``).

Import rule: headless; pint and lark are imported lazily so that
``import stokes.api`` stays under its budget.
"""

from stokes.units.dimensions import DIMENSIONS, UNIT_SYSTEMS, Dimension, dimension, display_unit
from stokes.units.expressions import EXPRESSION_FUNCTIONS, Resolver, evaluate, references
from stokes.units.quantities import (
    check_dimension,
    format_quantity,
    is_quantity_text,
    parse_quantity,
    quantity,
    to_si,
)
from stokes.units.registry import Quantity

__all__ = [
    "DIMENSIONS",
    "EXPRESSION_FUNCTIONS",
    "UNIT_SYSTEMS",
    "Dimension",
    "Quantity",
    "Resolver",
    "check_dimension",
    "dimension",
    "display_unit",
    "evaluate",
    "format_quantity",
    "is_quantity_text",
    "parse_quantity",
    "quantity",
    "references",
    "to_si",
]
