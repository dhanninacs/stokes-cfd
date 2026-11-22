"""Free sketch profiles (ADR-0036 decisions 1 to 3 and the addendum of 2026-11-14): entities
chained into loops, nesting, and a field exact in sign (a bound in value, exact at the points
checked here), checked on a grid against the shapes' areas and against distances measured by hand.
No mesher."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from stokes.implicit.errors import ImplicitInvalid
from stokes.implicit.evaluate import evaluate
from stokes.implicit.graph import X, Y
from stokes.implicit.profile2d import OpenProfile, SelfIntersecting, custom_profile


def line(a: tuple[float, float], b: tuple[float, float]) -> dict[str, Any]:  # Any: JSON
    return {"kind": "line", "a": list(a), "b": list(b)}


def polyline(*points: tuple[float, float]) -> list[dict[str, Any]]:  # Any: JSON
    return [line(p, q) for p, q in zip(points, (*points[1:], points[0]), strict=True)]


def field_at(entities: list[dict[str, Any]], points: Any) -> np.ndarray:  # Any: JSON, array
    shape = custom_profile(entities, 1.0)
    return evaluate(shape.field(X, Y), np.column_stack([np.asarray(points), np.zeros(len(points))]))


def area(
    entities: list[dict[str, Any]], box: tuple[float, float, float, float], n: int = 300
) -> float:  # Any: JSON
    u0, u1, v0, v1 = box
    us, vs = np.meshgrid(np.linspace(u0, u1, n), np.linspace(v0, v1, n))
    inside = field_at(entities, np.column_stack([us.ravel(), vs.ravel()])) < 0
    return float(inside.mean() * (u1 - u0) * (v1 - v0))


L_SHAPE = polyline((0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3))


def test_a_non_convex_polygon_has_its_area_and_exact_distances() -> None:
    assert area(L_SHAPE, (-1, 5, -1, 4)) == pytest.approx(4 + 2, rel=0.02)
    values = field_at(L_SHAPE, [(2, 2), (0.5, 2), (2, 0.5), (5, 0.5), (2, -1)])
    # (2, 2) is in the notch, 1 from the inner wall u = 1; the others by hand.
    assert values == pytest.approx([1.0, -0.5, -0.5, 1.0, 1.0], abs=1e-9)


def test_the_winding_does_not_care_how_the_loop_was_drawn() -> None:
    backwards = polyline((0, 3), (1, 3), (1, 1), (4, 1), (4, 0), (0, 0))
    shuffled = [L_SHAPE[3], L_SHAPE[0], L_SHAPE[5], L_SHAPE[2], L_SHAPE[4], L_SHAPE[1]]
    flipped = [
        {"kind": "line", "a": e["b"], "b": e["a"]} if i % 2 else e for i, e in enumerate(L_SHAPE)
    ]
    probe = [(0.5, 2), (2, 2), (3, 0.5)]
    reference = field_at(L_SHAPE, probe)
    for other in (backwards, shuffled, flipped):
        assert field_at(other, probe) == pytest.approx(reference, abs=1e-9)


def test_a_loop_inside_another_is_a_hole() -> None:
    washer = [
        *polyline((-2, -2), (2, -2), (2, 2), (-2, 2)),
        {"kind": "circle", "c": [0, 0], "r": 1},
    ]
    assert field_at(washer, [(0, 0)])[0] > 0  # in the hole
    assert field_at(washer, [(1.5, 0)])[0] < 0  # in the material
    assert area(washer, (-3, 3, -3, 3)) == pytest.approx(16 - math.pi, rel=0.02)


def test_arcs_and_splines_close_loops_with_lines() -> None:
    half_disc = [line((-1, 0), (1, 0)), {"kind": "arc", "a": [1, 0], "m": [0, 1], "b": [-1, 0]}]
    assert area(half_disc, (-1.5, 1.5, -0.5, 1.5)) == pytest.approx(math.pi / 2, rel=0.02)
    # within the chord tolerance (1e-3 of the size, 2) of the arc
    assert field_at(half_disc, [(0, 0.5)])[0] == pytest.approx(-0.5, abs=3e-3)
    bulge = [line((0, 0), (2, 0)), {"kind": "spline", "points": [[2, 0], [1, 1], [0, 0]]}]
    values = field_at(bulge, [(1, 0.3), (1, 1.5), (1, -0.3)])
    assert values[0] < 0 < values[1] and values[2] > 0


@pytest.mark.parametrize(
    ("entities", "error", "match"),
    [
        ([line((0, 0), (1, 0)), line((1, 0), (1, 1))], OpenProfile, "not closed"),
        (
            [*polyline((0, 0), (2, 0), (2, 2), (0, 2)), line((2, 0), (3, -1))],
            OpenProfile,
            "joins 3 entities",
        ),
        (polyline((0, 0), (2, 2), (2, 0), (0, 2)), SelfIntersecting, "crosses itself"),
        (
            [*polyline((0, 0), (2, 0), (2, 2), (0, 2)), *polyline((1, 1), (3, 1), (3, 3), (1, 3))],
            SelfIntersecting,
            "two loops",
        ),
        ([line((0, 0), (0, 0))], ImplicitInvalid, "no length"),
        ([{"kind": "arc", "a": [0, 0], "m": [1, 1], "b": [2, 2]}], ImplicitInvalid, "on a line"),
        ([{"kind": "spline", "points": [[0, 0], [1, 1]]}], ImplicitInvalid, "three points"),
        ([{"kind": "bezier"}], ImplicitInvalid, "unknown kind"),
        ([], ImplicitInvalid, "empty"),
    ],
)
def test_profiles_that_are_not_closed_or_cross_are_refused(
    entities: list[dict[str, Any]],
    error: type[Exception],
    match: str,  # Any: JSON
) -> None:
    with pytest.raises(error, match=match):
        custom_profile(entities, 1.0)


def test_a_custom_sketch_pulls_and_revolves_through_the_operation() -> None:
    import stokes.model  # noqa: F401 - the registry, before the geometry model
    from stokes.geometry.operations.implicit import body_of
    from tests.test_implicit_recipes import recipe

    # The L in metres (4 x 3 mm), pulled 2 mm: inside at (2, 0.5, 1) mm, not in the notch.
    metres = polyline(
        (0, 0), (0.004, 0), (0.004, 0.001), (0.001, 0.001), (0.001, 0.003), (0, 0.003)
    )
    pulled = body_of(recipe("implicit_sketch", profile="custom", entities=metres, depth=0.002))
    assert pulled.bounded() == ((0.0, 0.0, 0.0), (4.0, 3.0, 2.0))
    assert evaluate(pulled.field(), [(2, 0.5, 1)])[0] < 0 < evaluate(pulled.field(), [(2, 2, 1)])[0]
    # Revolved about the plane's v axis: the profile must lie at u > 0, so move it out first.
    away = polyline((0.002, 0), (0.004, 0), (0.004, 0.002), (0.002, 0.002))
    ring = body_of(recipe("implicit_sketch", profile="custom", entities=away, method="revolve"))
    assert evaluate(ring.field(), [(0, 1, 3), (0, 1, -3), (3, 1, 0)]).max() < 0
    with pytest.raises(Exception, match="crosses the revolve axis"):
        body_of(recipe("implicit_sketch", profile="custom", entities=metres, method="revolve"))
