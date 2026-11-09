"""Library fits (ADR-0018 decision 4; contract "Fits"): synthetic data with a known law is
recovered, the candidate order is the contract's, the tolerance refuses with the best form,
ill-conditioned raw coefficients are refused, forced forms behave, JANAF's a6 and a7 fix h and s
at 298.15 K, and coefficients survive a JSON round trip to the last digit."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest

from stokes.errors import ValidationError
from stokes.physics.materials.errors import FitTolerance
from stokes.physics.materials.fit import (
    VERIFY_POINTS,
    FitInput,
    Nasa7,
    candidates,
    fit_property,
    janaf_coefficients,
    verification_grid,
)
from stokes.physics.materials.methods import R_UNIVERSAL

AIR_M = 0.02896546


def inp(kind: str, t: np.ndarray, y: np.ndarray, phase: str = "liquid", **extra: Any) -> FitInput:
    t_ref = float(extra.pop("T_ref", 300.0))
    at_ref = float(np.interp(t_ref, t, y))
    return FitInput(
        kind=kind, phase=phase, t=t, values=y, p_ref=101325.0, T_ref=t_ref, at_ref=at_ref, **extra
    )


def test_the_grid_has_201_points_with_the_nodes_on_even_indices() -> None:
    t = verification_grid(280.0, 370.0)
    assert t.size == VERIFY_POINTS == 201
    assert t[0] == 280.0 and t[-1] == 370.0
    assert t[::2].size == 101 and t[1] == pytest.approx(280.0 + 0.45)
    with pytest.raises(ValidationError):
        verification_grid(370.0, 280.0)


def test_a_known_polynomial_is_recovered_to_1e_10() -> None:
    t = verification_grid(280.0, 370.0)
    exact = [1.2, -3.0e-3, 4.0e-6]
    y = np.polynomial.polynomial.polyval(t, exact)
    result = fit_property(inp("thermal_conductivity", t, y, tolerance=1e-9))
    assert result.method == "polynomial" and result.degree == 2
    assert result.max_rel_err < 1e-10
    assert result.params["coefficients"] == pytest.approx(exact, rel=1e-9)
    assert result.params["valid_min"] == 280.0 and result.params["valid_max"] == 370.0


def test_a_known_sutherland_law_is_recovered_to_1e_10() -> None:
    t = verification_grid(250.0, 1000.0)
    a_s, t_s = 1.458e-6, 110.4
    y = a_s * t**1.5 / (t + t_s)
    result = fit_property(inp("viscosity", t, y, phase="gas"))
    assert result.method == "sutherland"
    assert result.max_rel_err < 1e-10
    assert result.params["sutherland_coefficient"] == pytest.approx(a_s, rel=1e-9)
    assert result.params["sutherland_temperature"] == pytest.approx(t_s, rel=1e-9)


def test_the_candidate_orders_are_the_contracts() -> None:
    t = verification_grid(250.0, 750.0)
    y = np.ones_like(t)

    def order(kind: str, phase: str, **extra: Any) -> list[tuple[str, int]]:
        return [(c.method, c.degree) for c in candidates(inp(kind, t, y, phase=phase, **extra))]

    assert order("density", "gas")[:2] == [("ideal_gas", 0), ("polynomial", 1)]
    assert order("density", "liquid")[:3] == [
        ("constant", 0),
        ("rational_polynomial", 2),
        ("polynomial", 1),
    ]
    assert order("viscosity", "gas")[:2] == [("sutherland", 0), ("log_polynomial", 2)]
    assert ("polynomial", 1) not in order("viscosity", "gas")
    assert order("viscosity", "liquid")[-1] == ("polynomial", 4)
    assert ("polynomial", 2) not in order("viscosity", "liquid")
    gas_cp = order("specific_heat", "gas")
    assert gas_cp[:5] == [("constant", 0), ("janaf", 1), ("janaf", 2), ("janaf", 3), ("janaf", 4)]
    assert order("specific_heat", "liquid")[:2] == [("constant", 0), ("polynomial", 1)]
    assert order("thermal_conductivity", "gas", max_degree=6)[-1] == ("polynomial", 6)


def test_the_tolerance_refuses_with_the_best_form_and_the_advice() -> None:
    t = verification_grid(280.0, 370.0)
    y = np.exp(5000.0 / t) * 1e-9  # a liquid's viscosity: a straight line cannot hold it to 1 %
    with pytest.raises(FitTolerance) as refused:
        fit_property(inp("thermal_conductivity", t, y, max_degree=1))
    error = refused.value
    assert error.code == "materials.fit_tolerance"
    assert error.details["best"]["method"] == "polynomial"
    assert error.details["best"]["max_rel_err"] > 0.01
    assert "piecewise_linear" in error.message and "max_degree" in error.message
    assert [a["method"] for a in error.details["attempts"]] == ["constant", "polynomial"]


def test_ill_conditioned_raw_coefficients_lower_the_degree() -> None:
    # Far from T = 0 the high powers of T in kelvin cancel each other in float64: the degree-7
    # fit is exact in the scaled basis, but its raw coefficients cannot reproduce it, so it is
    # refused and the best form offered is of a lower, well-conditioned degree.
    t = np.linspace(5000.0, 5010.0, VERIFY_POINTS)
    y = 1.0 + 1e-3 * ((t - 5005.0) / 5.0) ** 7
    scaled = np.polynomial.Polynomial.fit(t[::2], y[::2], 7)
    assert np.max(np.abs(scaled(t) - y)) < 1e-12
    tight = inp("thermal_conductivity", t, y, max_degree=7, tolerance=1e-9, T_ref=5005.0)
    with pytest.raises(FitTolerance) as refused:
        fit_property(tight)
    attempts = refused.value.details["attempts"]
    ill = {a["degree"] for a in attempts if "ill-conditioned" in a["note"]}
    assert 7 in ill
    assert refused.value.details["best"]["degree"] < min(ill)


def test_forced_forms() -> None:
    t = verification_grid(280.0, 370.0)
    y = 1000.0 - 0.3 * (t - 280.0)
    constant = fit_property(inp("density", t, y, method="constant"))
    assert constant.method == "constant" and constant.forced
    assert constant.params["value"] == pytest.approx(float(np.interp(300.0, t, y)))
    assert constant.max_rel_err > 0.01  # taken as it is, whatever its deviation
    table = fit_property(inp("density", t, y, method="piecewise_linear"))
    assert table.params["table_temperatures"] == pytest.approx(list(t[::2]))
    assert table.max_rel_err < 1e-12  # exact for a straight line, nodes and midpoints
    linear = fit_property(inp("density", t, y, method="polynomial"))
    assert linear.method == "polynomial" and linear.degree == 1
    with pytest.raises(ValidationError):
        fit_property(inp("density", t, y, method="sutherland"))
    with pytest.raises(FitTolerance):
        fit_property(inp("viscosity", t, np.exp(5000.0 / t), method="sutherland"))


def test_an_ideal_gas_needs_its_molar_mass_and_claims_no_range() -> None:
    t = verification_grid(250.0, 750.0)
    y = 101325.0 * AIR_M / (R_UNIVERSAL * t)
    found = fit_property(inp("density", t, y, phase="gas", molar_mass=AIR_M))
    assert found.method == "ideal_gas" and found.params == {} and found.max_rel_err < 1e-14
    without = fit_property(inp("density", t, y, phase="gas"))
    assert without.method == "polynomial"
    assert without.attempts[0].note == "the molar mass is unknown"


def test_janaf_fixes_enthalpy_and_entropy_at_298_15_k() -> None:
    t = verification_grid(250.0, 1000.0)
    cp = 1000.0 + 0.1 * t + 1e-4 * t**2
    result = fit_property(
        inp(
            "specific_heat",
            t,
            cp,
            phase="gas",
            molar_mass=AIR_M,
            formation_enthalpy=1.5e4,
            formation_entropy=3.0e3,
            tolerance=1e-9,
        )
    )
    assert result.method == "janaf" and result.degree == 2
    a = result.params["coefficients"]
    assert a[:7] == a[7:] and result.params["breakpoints"] == [250.0, 625.0, 1000.0]
    scale = R_UNIVERSAL / AIR_M
    ts = 298.15
    h = scale * (a[0] * ts + a[1] * ts**2 / 2 + a[2] * ts**3 / 3 + a[5])
    s = scale * (a[0] * math.log(ts) + a[1] * ts + a[2] * ts**2 / 2 + a[6])
    assert h == pytest.approx(1.5e4, rel=1e-12) and s == pytest.approx(3.0e3, rel=1e-12)
    assert janaf_coefficients([3.5], AIR_M, 0.0, 0.0)[5] == pytest.approx(-3.5 * ts)


def test_exact_nasa7_coefficients_are_taken_as_they_are() -> None:
    low = [3.29867700, 1.40824040e-03, -3.96322200e-06, 5.64151500e-09, -2.44485400e-12]
    high = [2.92664000, 1.48797680e-03, -5.68476000e-07, 1.00970380e-10, -6.75335100e-15]
    coefficients = (*low, -1020.8999, 3.950372, *high, -922.7977, 5.980528)
    t = verification_grid(300.0, 2000.0)
    m = 0.0280134
    a = np.where(
        t < 1000.0,
        np.polynomial.polynomial.polyval(t, low),
        np.polynomial.polynomial.polyval(t, high),
    )
    cp = R_UNIVERSAL / m * a
    nasa = Nasa7(coefficients, (300.0, 1000.0, 5000.0))
    result = fit_property(inp("specific_heat", t, cp, phase="gas", molar_mass=m, nasa7=nasa))
    assert result.method == "janaf" and result.max_rel_err < 1e-12
    assert result.params["coefficients"] == list(coefficients)


def test_coefficients_survive_json_with_17_digits() -> None:
    t = verification_grid(275.0, 370.0)
    y = 1.0 / (1.2677e-3 - 2.03e-6 * t + 3.83e-9 * t**2)
    result = fit_property(inp("density", t, y))
    assert result.method == "rational_polynomial"
    text = json.dumps(result.entry())
    back = json.loads(text)
    assert back["coefficients"] == result.params["coefficients"]
    assert all(repr(c) in text for c in result.params["coefficients"] if c != 0.0)


def test_bad_inputs_are_refused() -> None:
    t = verification_grid(280.0, 370.0)
    with pytest.raises(ValidationError):
        fit_property(inp("density", t, -np.ones_like(t)))
    with pytest.raises(ValidationError):
        fit_property(inp("density", t[:50], np.ones(50)))
    with pytest.raises(ValidationError):
        fit_property(inp("density", t, np.ones_like(t), phase="plasma"))
    with pytest.raises(ValidationError):
        fit_property(inp("density", t, np.ones_like(t), tolerance=0.0))
