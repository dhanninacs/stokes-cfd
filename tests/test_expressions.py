from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from stokes.errors import ExpressionError, UnitMismatch
from stokes.units import dimension, evaluate, quantity, references, to_si


def resolver(**values: Any) -> Callable[[str], Any]:
    def resolve(name: str) -> Any:
        return values[name]

    return resolve


EMPTY = resolver()


def test_arithmetic_with_units() -> None:
    v = evaluate("2 [m/s] * 1.5", EMPTY, dimension("velocity"))
    assert to_si(v, dimension("velocity")) == pytest.approx(3.0)
    p = evaluate("0.5 * rho * U**2", resolver(rho=quantity(1.2, "kg/m**3"), U=quantity(2, "m/s")))
    assert to_si(p, dimension("pressure")) == pytest.approx(2.4)


def test_functions() -> None:
    length = dimension("length")
    assert to_si(evaluate("sqrt(4 [m**2])", EMPTY, length), length) == pytest.approx(2.0)
    assert to_si(evaluate("min(1 [m], 50 [cm])", EMPTY, length), length) == pytest.approx(0.5)
    assert to_si(evaluate("max(1 [m], 50 [cm])", EMPTY, length), length) == pytest.approx(1.0)
    assert float(evaluate("sin(90 [deg])", EMPTY).magnitude) == pytest.approx(1.0)
    assert float(evaluate("cos(pi)", EMPTY).magnitude) == pytest.approx(-1.0)
    assert to_si(evaluate("clamp(5 [m], 0 [m], 2 [m])", EMPTY, length), length) == pytest.approx(
        2.0
    )
    assert to_si(evaluate("abs(-3 [m])", EMPTY, length), length) == pytest.approx(3.0)


def test_unary_and_precedence() -> None:
    length = dimension("length")
    assert to_si(evaluate("-3 [m] + +1 [m]", EMPTY, length), length) == pytest.approx(-2.0)
    assert float(evaluate("2 ** 3 ** 2", EMPTY).magnitude) == pytest.approx(512.0)
    assert float(evaluate("2 ^ 3 * 2", EMPTY).magnitude) == pytest.approx(16.0)
    assert float(evaluate("(1 + 2) * 3", EMPTY).magnitude) == pytest.approx(9.0)


def test_temperature_arithmetic() -> None:
    temp = dimension("temperature")
    assert to_si(evaluate("40 [degC] + 10 [K]", EMPTY, temp), temp) == pytest.approx(323.15)


def test_dotted_references() -> None:
    velocity = dimension("velocity")
    res = resolver(**{"regions.fluid.inlet.velocity": quantity(4, "m/s")})
    assert to_si(evaluate("regions.fluid.inlet.velocity / 2", res, velocity), velocity) == 2.0
    assert references("0.5*rho*U_in**2 + sin(pi/2) + regions.fluid.inlet.velocity") == [
        "rho",
        "U_in",
        "regions.fluid.inlet.velocity",
    ]


def test_errors() -> None:
    with pytest.raises(UnitMismatch):
        evaluate("1 [m] + 2 [s]", EMPTY)
    with pytest.raises(UnitMismatch):
        evaluate("2 [m] ** 1 [m]", EMPTY)
    with pytest.raises(UnitMismatch):
        evaluate("2 [m]", EMPTY, dimension("pressure"))
    with pytest.raises(ExpressionError):
        evaluate("2 [m] / 0", EMPTY)
    with pytest.raises(ExpressionError):
        evaluate("foo(1)", EMPTY)
    with pytest.raises(ExpressionError):
        evaluate("2 m/s * 3", EMPTY)
    with pytest.raises(ExpressionError):
        evaluate("U_in * 2", EMPTY)
    with pytest.raises(ExpressionError):
        evaluate("sqrt(1, 2)", EMPTY)
    with pytest.raises(ExpressionError):
        evaluate("2 [blorp]", EMPTY)


@given(
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
def test_addition_property(a: float, b: float) -> None:
    length = dimension("length")
    result = evaluate(f"{a!r} [m] + {b!r} [m]", EMPTY, length)
    assert to_si(result, length) == pytest.approx(a + b, rel=1e-12, abs=1e-9)
