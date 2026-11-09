"""The flat-plate y+ estimate (brief M-505): pure arithmetic, checked against a hand calculation.

The textbook case (air at 300 K: rho = 1.177 kg/m3, mu = 1.85e-5 Pa s; U = 10 m/s over L = 1 m;
y+ = 1) worked by hand, every step:

    nu = 1.85e-5 / 1.177 = 1.5718e-5 m2/s
    Re = 10 / 1.5718e-5 = 636,216
    Re^(1/7) = e^(ln(636216)/7) = 6.746
    Cf = 0.026 / 6.746 = 0.003854
    tau_w = 0.5 * 0.003854 * 1.177 * 100 = 0.22685 Pa
    u_tau = sqrt(0.22685 / 1.177) = 0.43915 m/s
    y = 1 * 1.5718e-5 / 0.43915 = 3.5794e-5 m
    height = 2y = 7.159e-5 m  (about 72 microns)
"""

from __future__ import annotations

import pytest

from stokes.mesh.yplus import METHOD, YPlusResult, yplus_height

#: The hand calculation's numbers, transcribed from the module docstring.
RHO = 1.177
MU = 1.85e-5
EXPECTED_HEIGHT = 7.159e-5
EXPECTED_RE = 636_216.0
EXPECTED_CF = 0.003854


def test_the_textbook_case_matches_the_hand_calculation() -> None:
    """Air 10 m/s over 1 m at y+ 1: within 1 % of the hand calculation (better than the
    brief's 5 %, which is the tolerance the correlation itself earns)."""
    result = yplus_height(y_plus=1.0, velocity=10.0, length=1.0, density=RHO, viscosity=MU)
    assert isinstance(result, YPlusResult)
    assert result.first_height == pytest.approx(EXPECTED_HEIGHT, rel=0.01)
    assert result.reynolds == pytest.approx(EXPECTED_RE, rel=0.001)
    assert result.skin_friction == pytest.approx(EXPECTED_CF, rel=0.01)


def test_the_height_is_TWICE_the_yplus_centre() -> None:
    """**The factor the hand calculations trip on**: y+ is at the first cell's CENTRE, so the
    height is 2y. Both numbers are returned and the relation holds."""
    result = yplus_height(y_plus=30.0, velocity=10.0, length=1.0, density=RHO, viscosity=MU)
    assert result.first_height == pytest.approx(2.0 * result.y_centre)
    # y+ 30 scales the centre linearly from the y+ 1 case.
    one = yplus_height(y_plus=1.0, velocity=10.0, length=1.0, density=RHO, viscosity=MU)
    assert result.y_centre == pytest.approx(30.0 * one.y_centre)


def test_the_method_line_is_carried() -> None:
    result = yplus_height(y_plus=1.0, velocity=10.0, length=1.0, density=RHO, viscosity=MU)
    assert result.method == METHOD == "flat plate: Cf = 0.026 Re^(-1/7)"


def test_the_units_round_trip() -> None:
    """The same physical inputs in different units give the same height: the command parses the
    text to SI before calling this, so the pure function sees only SI either way."""
    # 36 km/h is 10 m/s; 1000 mm is 1 m — hand-parsed to SI by the caller, exactly as the
    # command does.
    si = yplus_height(y_plus=1.0, velocity=10.0, length=1.0, density=RHO, viscosity=MU)
    resparsed = yplus_height(
        y_plus=1.0, velocity=36.0 / 3.6, length=1000.0 / 1000.0, density=RHO, viscosity=MU
    )
    assert si.first_height == pytest.approx(resparsed.first_height)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"y_plus": 0.0},
        {"velocity": -1.0},
        {"length": 0.0},
        {"density": 0.0},
        {"viscosity": -1e-5},
    ),
)
def test_a_non_physical_input_is_refused(kwargs: dict) -> None:
    """A zero or negative anything is not an input: it would produce a nonsense height with no
    error to explain it."""
    full = {"y_plus": 1.0, "velocity": 10.0, "length": 1.0, "density": RHO, "viscosity": MU}
    with pytest.raises(ValueError, match="positive finite"):
        yplus_height(**{**full, **kwargs})
