"""Implicit recipes to bodies (ADR-0035 decision 7): every kind of every slice 1 operation builds
from the node type's defaults, reads the parameters its kind names, and refuses bad recipes. No
worker and no mesher: the bodies are checked with the evaluator."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from stokes.geometry.errors import OperationInvalid
from stokes.geometry.model import IMPLICIT_PRIMITIVES, IMPLICIT_TRANSFORMS
from stokes.geometry.operations.implicit import MAX_RECIPE_DEPTH, body_of
from stokes.implicit.evaluate import evaluate
from stokes.implicit.features import Body


def defaults(op_type: str) -> dict[str, Any]:  # Any: JSON
    """The SI defaults of an operation type's own parameters, as a recipe carries them."""
    from stokes.geometry.model import OPERATION_COMMON
    from stokes.model import registry

    found: dict[str, Any] = {}
    for spec in registry.get(op_type).params:
        if spec.name in OPERATION_COMMON:
            continue
        value = spec.default
        if spec.kind == "array":
            value = [float(v) for v in value or ()]
        elif spec.kind == "quantity":
            value = float(value or 0.0)
        found[spec.name] = value
    return found


def recipe(
    op_type: str, inputs: list[Any] | None = None, tools: list[Any] | None = None, **params: Any
) -> dict[str, Any]:  # Any: JSON
    return {
        "type": op_type,
        "params": {**defaults(op_type), **params},
        "inputs": inputs or [],
        "tools": tools or [],
    }


def inside(body: Body, point: tuple[float, float, float]) -> bool:
    return bool(evaluate(body.field(), [point])[0] < 0.0)


@pytest.mark.parametrize("kind", IMPLICIT_PRIMITIVES)
def test_every_bounded_primitive_builds_from_its_defaults(kind: str) -> None:
    body = body_of(recipe("implicit_primitive", kind=kind))
    low, high = body.bounded()
    assert all(h > lo for lo, h in zip(low, high, strict=True))
    if kind == "half_space":  # the block lies below its plane (the default normal is +z)
        assert inside(body, (0.0, 0.0, -1.0))
    elif kind != "torus":  # a torus is hollow at its centre
        assert inside(body, (0.0, 0.0, 0.0))
    assert body.smallest > 0.0


def test_each_kind_reads_its_own_parameters_in_millimetres() -> None:
    sphere = body_of(
        recipe("implicit_primitive", kind="sphere", radius=0.002, size=[1.0, 1.0, 1.0])
    )
    assert sphere.bounded() == ((-2.0, -2.0, -2.0), (2.0, 2.0, 2.0))  # size is not a sphere's
    cylinder = body_of(
        recipe("implicit_primitive", kind="cylinder", radius=0.001, length=0.004, axis="x")
    )
    assert cylinder.bounded() == ((-2.0, -1.0, -1.0), (2.0, 1.0, 1.0))
    cone = body_of(
        recipe("implicit_primitive", kind="cone", radius=0.002, top_radius=0.0, length=0.01)
    )
    assert inside(cone, (1.5, 0.0, -4.9)) and not inside(
        cone, (1.5, 0.0, 4.9)
    )  # wide base, point on top
    torus = body_of(recipe("implicit_primitive", kind="torus", radius=0.005, tube_radius=0.001))
    assert inside(torus, (5.0, 0.0, 0.0)) and not inside(torus, (0.0, 0.0, 0.0))
    rounded = body_of(
        recipe(
            "implicit_primitive", kind="rounded_box", size=[0.01, 0.01, 0.01], corner_radius=0.002
        )
    )
    assert not inside(rounded, (4.9, 4.9, 4.9)) and inside(rounded, (4.9, 0.0, 0.0))


def test_a_half_space_is_a_block_on_its_inner_side_and_cuts_a_body() -> None:
    plane = recipe(
        "implicit_primitive",
        kind="half_space",
        normal="+z",
        position=[0.0, 0.0, 0.0],
        size=[0.1, 0.1, 0.1],
    )
    assert body_of(plane).bounded() == ((-50.0, -50.0, -100.0), (50.0, 50.0, 0.0))
    ball = recipe("implicit_primitive", kind="sphere", radius=0.005)
    half = body_of(recipe("implicit_boolean", [ball], [plane], op="intersection"))
    assert half.bounded()[1][2] == pytest.approx(0.0)
    assert inside(
        body_of(half_recipe := recipe("implicit_boolean", [ball], [plane], op="intersection")),
        (0.0, 0.0, -1.0),
    )
    assert not inside(body_of(half_recipe), (0.0, 0.0, 1.0))


@pytest.mark.parametrize("kind", IMPLICIT_TRANSFORMS)
def test_every_transform_builds(kind: str) -> None:
    ball = recipe("implicit_primitive", kind="sphere", radius=0.001, position=[0.003, 0.0, 0.0])
    params: dict[str, Any] = {"kind": kind}
    if kind == "translate":
        params["offset"] = [0.0, 0.004, 0.0]
    elif kind == "rotate":
        params["angle"] = float(np.pi / 2)
    elif kind == "scale":
        params["factor"] = 2.0
    moved = body_of(recipe("implicit_transform", [ball], **params))
    expected = {
        "translate": (3.0, 4.0, 0.0),
        "rotate": (0.0, 3.0, 0.0),
        "mirror": (3.0, 0.0, 0.0),  # the default mirror plane is z = 0: the sphere sits on it
        "scale": (6.0, 0.0, 0.0),
    }[kind]
    assert inside(moved, expected)


def test_booleans_combine_inputs_and_tools() -> None:
    block = recipe("implicit_primitive", kind="box", size=[0.01, 0.01, 0.01])
    pin = recipe("implicit_primitive", kind="cylinder", radius=0.002, length=0.02)
    drilled = body_of(recipe("implicit_boolean", [block], [pin], op="difference"))
    assert not inside(drilled, (0.0, 0.0, 0.0)) and inside(drilled, (4.0, 4.0, 0.0))
    joined = body_of(recipe("implicit_boolean", [block], [pin], op="union"))
    assert inside(joined, (0.0, 0.0, 9.0))
    common = body_of(recipe("implicit_boolean", [block], [pin], op="intersection"))
    assert inside(common, (0.0, 0.0, 4.0)) and not inside(common, (4.0, 4.0, 0.0))


@pytest.mark.parametrize(
    ("bad", "match"),
    [
        (
            {"type": "implicit_primitive", "params": {"kind": "gyroid"}},
            "unknown implicit primitive",
        ),
        ({"type": "boolean", "params": {}}, "is not an implicit operation"),
        ({"type": "implicit_primitive"}, "no type or parameters"),
        ("box", "not an object"),
        (
            {"type": "implicit_primitive", "params": {"kind": "sphere", "radius": "big"}},
            "not a number",
        ),
        (
            {"type": "implicit_primitive", "params": {"kind": "sphere", "radius": -0.001}},
            "positive",
        ),
    ],
)
def test_bad_recipes_are_refused(bad: Any, match: str) -> None:
    # A primitive's bad value sits on top of the defaults, so the one value is what is wrong.
    if isinstance(bad, dict) and bad.get("type") == "implicit_primitive" and "params" in bad:
        bad = {**bad, "params": {**defaults("implicit_primitive"), **bad["params"]}}
    with pytest.raises(OperationInvalid, match=match):
        body_of(bad)


def test_a_recipe_chained_deeper_than_the_limit_is_refused() -> None:
    chain: dict[str, Any] = recipe("implicit_primitive", kind="sphere")
    for _ in range(MAX_RECIPE_DEPTH + 2):
        chain = recipe("implicit_transform", [chain], kind="translate", offset=[0.0, 0.0, 0.0])
    with pytest.raises(OperationInvalid, match="chained"):
        body_of(chain)


# ------------------------------------------------------------------------------- sketches
PROFILES = ["rectangle", "rounded_rectangle", "circle", "ellipse", "slot", "polygon"]


@pytest.mark.parametrize("method", ["extrude", "revolve"])
@pytest.mark.parametrize("profile", PROFILES)
def test_every_profile_pulls_and_revolves(profile: str, method: str) -> None:
    body = body_of(
        recipe(
            "implicit_sketch",
            profile=profile,
            method=method,
            width=0.004,
            height=0.004,
            radius=0.002,
            slot_length=0.002,
            axis_distance=0.01,
        )
    )
    low, high = body.bounded()
    assert all(h > lo for lo, h in zip(low, high, strict=True))
    point = (0.0, 0.0, 1.0) if method == "extrude" else (10.0, 0.0, 0.0)
    assert inside(body, point)


def test_a_pull_goes_along_the_plane_normal_from_the_origin() -> None:
    plate = body_of(
        recipe(
            "implicit_sketch",
            profile="circle",
            plane="yz",
            radius=0.003,
            depth=0.002,
            origin=[0.001, 0.0, 0.0],
        )
    )
    # yz: the normal is +x, the pull runs from x = 1 mm to x = 3 mm.
    assert plate.bounded() == ((1.0, -3.0, -3.0), (3.0, 3.0, 3.0))
    assert inside(plate, (2.0, 0.0, 2.5)) and not inside(plate, (0.5, 0.0, 0.0))
    both = body_of(recipe("implicit_sketch", profile="rectangle", depth=0.004, symmetric=True))
    assert both.bounded()[0][2] == pytest.approx(-2.0) and both.bounded()[1][2] == pytest.approx(
        2.0
    )


def test_a_partial_revolve_keeps_its_wedge() -> None:
    import math

    half = body_of(
        recipe(
            "implicit_sketch",
            profile="circle",
            method="revolve",
            radius=0.001,
            axis_distance=0.005,
            angle=math.pi,
        )
    )
    # xy: the turn is about y, from +x towards +z.
    assert (
        inside(half, (5.0, 0.0, 0.1))
        and inside(half, (0.0, 0.0, 5.0))
        and inside(half, (-5.0, 0.0, 0.1))
    )
    assert not inside(half, (0.0, 0.0, -5.0))
    quarter = body_of(
        recipe(
            "implicit_sketch",
            profile="circle",
            method="revolve",
            radius=0.001,
            axis_distance=0.005,
            angle=math.pi / 2,
        )
    )
    assert inside(quarter, (3.5, 0.0, 3.5)) and not inside(quarter, (-3.5, 0.0, 3.5))
    most = body_of(
        recipe(
            "implicit_sketch",
            profile="circle",
            method="revolve",
            radius=0.001,
            axis_distance=0.005,
            angle=1.5 * math.pi,
        )
    )
    assert inside(most, (-3.5, 0.0, -3.5)) and not inside(most, (3.5, 0.0, -3.5))


@pytest.mark.parametrize(
    ("params", "match"),
    [
        (
            {"method": "revolve", "profile": "rectangle", "width": 0.03, "axis_distance": 0.01},
            "crosses the revolve axis",
        ),
        ({"profile": "polygon", "sides": 2}, "3 to 64 sides"),
        ({"profile": "hexagram"}, "unknown profile"),
        ({"plane": "xz"}, "unknown sketch plane"),
        ({"method": "loft"}, "unknown sketch method"),
        ({"depth": 0.0}, "positive"),
        ({"method": "revolve", "angle": 7.0}, "full turn"),
    ],
)
def test_bad_sketches_are_refused(params: dict[str, Any], match: str) -> None:  # Any: JSON
    with pytest.raises(OperationInvalid, match=match):
        body_of(recipe("implicit_sketch", **params))
