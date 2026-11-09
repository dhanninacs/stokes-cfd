from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from stokes.errors import UnitMismatch, ValidationError
from stokes.units import (
    DIMENSIONS,
    dimension,
    display_unit,
    format_quantity,
    is_quantity_text,
    parse_quantity,
    quantity,
    to_si,
)

LENGTH = dimension("length")


def test_parse_forms() -> None:
    assert to_si(parse_quantity("25 mm", LENGTH), LENGTH) == pytest.approx(0.025)
    assert to_si(parse_quantity("25 [mm]", LENGTH), LENGTH) == pytest.approx(0.025)
    assert to_si(parse_quantity("25", LENGTH, default_unit="mm"), LENGTH) == pytest.approx(0.025)
    assert to_si(parse_quantity("2.5e-3", LENGTH), LENGTH) == pytest.approx(0.0025)
    assert to_si(parse_quantity(" -1.5 [in] ", LENGTH), LENGTH) == pytest.approx(-0.0381)


def test_temperature_offsets() -> None:
    temp = dimension("temperature")
    q = parse_quantity("40 degC", temp)
    assert to_si(q, temp) == pytest.approx(313.15)
    assert format_quantity(q, "degC").startswith("40 ")
    assert to_si(parse_quantity("32 degF", temp), temp) == pytest.approx(273.15)


def test_dimension_mismatch() -> None:
    with pytest.raises(UnitMismatch):
        parse_quantity("2 m/s", dimension("pressure"))


def test_unknown_unit() -> None:
    with pytest.raises(ValidationError):
        parse_quantity("2 blorps", LENGTH)


def test_volume_flow_and_pressure() -> None:
    flow = dimension("volume_flow")
    assert to_si(parse_quantity("60 L/min", flow), flow) == pytest.approx(0.001)
    pressure = dimension("pressure")
    assert to_si(parse_quantity("1 bar", pressure), pressure) == pytest.approx(1e5)
    assert format_quantity(quantity(1500, "Pa"), "kPa") == "1.5 kPa"


def test_is_quantity_text() -> None:
    assert is_quantity_text("2 [m/s]")
    assert is_quantity_text("3")
    assert is_quantity_text("1e-3 Pa*s")
    assert not is_quantity_text("U_in*2")
    assert not is_quantity_text("2 m/s * 3")
    assert not is_quantity_text("sqrt(2)")


def test_display_units() -> None:
    assert display_unit("pressure", "imperial") == "psi"
    assert display_unit("length", "mm") == "mm"
    with pytest.raises(ValidationError):
        display_unit("pressure", "martian")
    with pytest.raises(ValidationError):
        dimension("spin")


def test_every_dimension_has_consistent_units() -> None:
    for name, dim in DIMENSIONS.items():
        for system, unit in dim.display.items():
            q = quantity(1.0, unit) if unit else quantity(1.0, "")
            assert q.dimensionality == dim.dimensionality, (name, system, unit)


@given(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False))
def test_millimetre_roundtrip(x: float) -> None:
    assert to_si(parse_quantity(f"{x!r} mm", LENGTH), LENGTH) == pytest.approx(x / 1000)
