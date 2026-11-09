"""``units.convert`` supplies every SI-to-display factor the Explore table needs.

The design table showed its numbers through a hand-written factor table in
``web/src/panels/explore/format.ts``. That table is gone: the front end now asks
``units.convert`` once per unit pair and caches the answer. This test is the guard that the
command returns the same factor and offset for each pair, so the numbers on screen do not move.
The expected values are the ones the removed table carried.
"""

from __future__ import annotations

import pytest

from stokes.api import Session

# (si unit, display unit, factor, offset): exactly the pairs the Explore table covered.
PAIRS: list[tuple[str, str, float, float]] = [
    ("m", "mm", 1000, 0),
    ("m", "in", 39.3700787401575, 0),
    ("m**2", "mm**2", 1000000, 0),
    ("m**2", "in**2", 1550.0031000062, 0),
    ("m**3", "mm**3", 1000000000, 0),
    ("m**3", "in**3", 61023.7440947323, 0),
    ("kg", "g", 1000, 0),
    ("kg", "lb", 2.20462262184878, 0),
    ("K", "degC", 1, -273.15),
    ("K", "degF", 1.8, -459.67),
    ("K", "delta_degF", 1.8, 0),
    ("m/s", "ft/s", 3.28083989501312, 0),
    ("m/s**2", "ft/s**2", 3.28083989501312, 0),
    ("rad", "deg", 57.2957795130823, 0),
    ("rad/s", "rpm", 9.54929658551372, 0),
    ("N", "lbf", 0.224808943099711, 0),
    ("Pa", "kPa", 0.001, 0),
    ("Pa", "psi", 0.000145037737730209, 0),
    ("J", "BTU", 0.000947816987913438, 0),
    ("W", "BTU/h", 3.41214115648838, 0),
    ("kg/m**3", "lb/ft**3", 0.0624279605761446, 0),
    ("Pa*s", "mPa*s", 1000, 0),
    ("Pa*s", "lb/(ft*s)", 0.671968975139507, 0),
    ("m**2/s", "mm**2/s", 1000000, 0),
    ("m**2/s", "ft**2/s", 10.7639104167097, 0),
    ("W/(m*K)", "BTU/(h*ft*delta_degF)", 0.577789235832032, 0),
    ("J/(kg*K)", "BTU/(lb*delta_degF)", 0.000238845863263288, 0),
    ("W/m**2", "BTU/(h*ft**2)", 0.316998286346886, 0),
    ("W/(m**2*K)", "BTU/(h*ft**2*delta_degF)", 0.176110159081603, 0),
    ("kg/s", "g/s", 1000, 0),
    ("kg/s", "lb/s", 2.20462262184878, 0),
    ("m**3/s", "L/min", 60000, 0),
    ("m**3/s", "gal/min", 15850.3231414889, 0),
]


@pytest.mark.parametrize(("si_unit", "display_unit", "factor", "offset"), PAIRS)
def test_the_command_returns_the_tables_factor_and_offset(
    si_unit: str, display_unit: str, factor: float, offset: float
) -> None:
    result = Session().execute("units.convert", value=1.0, from_unit=si_unit, to_unit=display_unit)
    assert result["factor"] == pytest.approx(factor, rel=1e-9)
    assert result["offset"] == pytest.approx(offset, rel=1e-9, abs=1e-12)


@pytest.mark.parametrize(("si_unit", "display_unit", "factor", "offset"), PAIRS)
def test_the_conversion_is_affine(
    si_unit: str, display_unit: str, factor: float, offset: float
) -> None:
    """``display = si * factor + offset``, checked at a second point so an offset cannot hide."""
    result = Session().execute("units.convert", value=7.0, from_unit=si_unit, to_unit=display_unit)
    assert result["value"] == pytest.approx(7.0 * factor + offset, rel=1e-9, abs=1e-9)
