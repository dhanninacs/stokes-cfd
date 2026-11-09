"""units.* commands: unit conversion.

``units.convert`` converts a bare number from one unit to another, so the CLI and agents can
convert without carrying unit tables of their own. The Explore front end calls it too, once per
unit pair, for the SI-to-display factors of its design table
(``web/src/panels/explore/conversions.ts``).
"""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import UnitConversionError

MAX_UNIT_TEXT = 64
"""Longest unit text accepted, in characters; a unit name is short, and the text is untrusted."""


def _convert(value: float, from_unit: str, to_unit: str) -> tuple[float, float, float]:
    """Return ``(converted, factor, offset)`` for ``converted = value * factor + offset``.

    pint does every conversion, so it owns the unit tables and no dimension is named here. A
    unit transform is affine, so the factor and offset come from a one-unit step: the converted
    0 is the offset (0 degC is 273.15 K) and the converted 1 less the offset is the factor. The
    value is converted directly, never through the factor and offset: a value far below the
    offset (1e-12 degC) would otherwise lose its digits in the subtraction.
    """
    from stokes.units.registry import ureg

    for text in (from_unit, to_unit):
        if len(text) > MAX_UNIT_TEXT:
            raise UnitConversionError(
                f"unit text is longer than {MAX_UNIT_TEXT} characters",
                details={"length": len(text), "limit": MAX_UNIT_TEXT},
            )
    registry = ureg()

    def convert(x: float) -> float:
        return float(registry.Quantity(x, from_unit).to(to_unit).magnitude)

    try:
        offset = convert(0.0)
        factor = convert(1.0) - offset
        converted = convert(value)
    except Exception as exc:  # pint's own errors, and the parser's for text it cannot read
        raise UnitConversionError(
            f"cannot convert {from_unit!r} to {to_unit!r}: {exc}",
            details={"from": from_unit, "to": to_unit},
        ) from exc
    return converted, factor, offset


@command(
    "units.convert",
    params=(
        ParamDef(
            "value", "number", description="The numeric value to convert, in the source unit."
        ),
        ParamDef("from_unit", "string", description="The source unit, e.g. 'm', 'K', 'm/s'."),
        ParamDef("to_unit", "string", description="The target unit, e.g. 'mm', 'degC', 'ft/s'."),
    ),
    mutates=False,
    requires_project=False,
)
def units_convert(
    ctx: CommandContext, value: float, from_unit: str = "", to_unit: str = ""
) -> dict[str, Any]:
    """Convert a number from one unit to another.

    Returns the converted value with the factor and offset of the conversion
    (``converted = value * factor + offset``), so a caller can convert more values without asking
    again. Temperatures have an offset: 25 degC is 298.15 K, factor 1, offset 273.15. Fails with
    ``units.convert`` for an unknown unit, units of different dimensions, or unit text over 64
    characters.
    """
    converted, factor, offset = _convert(value, from_unit, to_unit)
    return {
        "value": converted,
        "from": from_unit,
        "to": to_unit,
        "factor": factor,
        "offset": offset,
    }
