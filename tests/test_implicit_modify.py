"""The direct-modelling tools (ADR-0036 decisions 5 to 8): split, offset, shell, patterns and
blends, as recipes (checked with the evaluator) and, for a split, through a session where the
operation makes two parts."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

import stokes.model  # noqa: F401 - the registry, before the geometry model
from stokes.geometry.errors import OperationInvalid
from stokes.geometry.operations.implicit import bodies_of, body_of
from stokes.implicit.evaluate import evaluate
from tests.test_implicit_recipes import recipe

BOX = recipe("implicit_primitive", kind="box", size=[0.01, 0.01, 0.01])
BALL = recipe("implicit_primitive", kind="sphere", radius=0.004)


def values(body: Any, *points: tuple[float, float, float]) -> np.ndarray:  # Any: Body
    return evaluate(body.field(), list(points))


def test_a_split_makes_the_two_sides_of_the_plane() -> None:
    parts = bodies_of(recipe("implicit_split", [BOX], normal="+x", position=[0.002, 0.0, 0.0]))
    assert [name for name, _ in parts] == ["below", "above"]
    below, above = (b for _, b in parts)
    assert below.bounded() == ((-5.0, -5.0, -5.0), (2.0, 5.0, 5.0))
    assert above.bounded() == ((2.0, -5.0, -5.0), (5.0, 5.0, 5.0))
    assert values(below, (0, 0, 0))[0] < 0 < values(below, (3, 0, 0))[0]
    assert values(above, (3, 0, 0))[0] < 0 < values(above, (0, 0, 0))[0]
    flipped = bodies_of(recipe("implicit_split", [BOX], normal="-x", position=[0.002, 0.0, 0.0]))
    assert flipped[0][1].bounded()[0][0] == 2.0  # behind a -x normal is the +x side
    with pytest.raises(OperationInvalid, match="does not cut"):
        bodies_of(recipe("implicit_split", [BOX], normal="+z", position=[0.0, 0.0, 0.02]))


def test_a_downstream_recipe_reads_the_split_output_it_names() -> None:
    split = recipe("implicit_split", [BOX], normal="+x", position=[0.0, 0.0, 0.0])
    moved = recipe(
        "implicit_transform", [{**split, "output": 1}], kind="translate", offset=[0.01, 0.0, 0.0]
    )
    body = body_of(moved)
    assert body.bounded()[0][0] == pytest.approx(10.0)  # the +x half, moved 10 mm
    with pytest.raises(OperationInvalid, match="output"):
        body_of({**split, "output": 2})


def test_an_offset_grows_or_shrinks_and_a_shell_keeps_a_wall() -> None:
    grown = body_of(recipe("implicit_offset", [BALL], kind="offset", distance=0.001))
    assert values(grown, (4.5, 0, 0))[0] < 0 < values(grown, (5.5, 0, 0))[0]
    shrunk = body_of(recipe("implicit_offset", [BALL], kind="offset", distance=-0.001))
    assert values(shrunk, (3.5, 0, 0))[0] > 0
    wall = body_of(recipe("implicit_offset", [BALL], kind="shell", distance=0.001))
    assert values(wall, (0, 0, 0))[0] > 0 and values(wall, (3.5, 0, 0))[0] < 0
    outside = body_of(recipe("implicit_offset", [BALL], kind="shell", distance=0.001, outward=True))
    assert values(outside, (4.5, 0, 0))[0] < 0 and values(outside, (3.5, 0, 0))[0] > 0
    with pytest.raises(OperationInvalid, match="thick"):
        body_of(recipe("implicit_offset", [BALL], kind="shell", distance=0.005))
    with pytest.raises(OperationInvalid, match="shrink the body away"):
        body_of(recipe("implicit_offset", [BALL], kind="offset", distance=-0.005))
    ellipse = recipe("implicit_sketch", profile="ellipse", width=0.01, height=0.005)
    with pytest.raises(OperationInvalid, match="field is a distance"):
        body_of(recipe("implicit_offset", [ellipse], kind="offset", distance=0.001))


def test_linear_and_polar_patterns_place_their_copies() -> None:
    pin = recipe(
        "implicit_primitive", kind="cylinder", radius=0.001, length=0.01, position=[0.005, 0.0, 0.0]
    )
    row = body_of(recipe("implicit_pattern", [pin], kind="linear", axis="y", count=4, pitch=0.003))
    assert (values(row, *[(5, 3 * k, 0) for k in range(4)]) < 0).all()
    assert values(row, (5, 12, 0))[0] > 0 and values(row, (5, 1.5, 0))[0] > 0
    ring = body_of(recipe("implicit_pattern", [pin], kind="polar", axis="z", count=6))
    corners = [(5 * math.cos(k * math.pi / 3), 5 * math.sin(k * math.pi / 3), 0) for k in range(6)]
    assert (values(ring, *corners) < 0).all()
    assert values(ring, (5 * math.cos(math.pi / 6), 5 * math.sin(math.pi / 6), 0))[0] > 0
    fan = body_of(
        recipe("implicit_pattern", [pin], kind="polar", axis="z", count=3, angle=math.pi / 2)
    )
    assert (values(fan, (5, 0, 0), (0, 5, 0), (3.5355, 3.5355, 0)) < 0).all()
    with pytest.raises(OperationInvalid, match="1 to 1000"):
        body_of(recipe("implicit_pattern", [pin], kind="linear", count=0))


def test_a_blend_rounds_the_seam_of_a_union() -> None:
    a = recipe("implicit_primitive", kind="box", size=[0.01, 0.01, 0.01])
    b = recipe("implicit_primitive", kind="box", size=[0.01, 0.01, 0.01], position=[0.0, 0.0, 0.01])
    sharp = body_of(recipe("implicit_boolean", [a], [b], op="union"))
    smooth = body_of(recipe("implicit_boolean", [a], [b], op="union", blend_radius=0.002))
    # Along the outer seam, the blend fills the crease the sharp union leaves.
    seam = (5.2, 0.0, 5.0)
    assert values(sharp, seam)[0] > 0 > values(smooth, seam)[0]
    inner = body_of(recipe("implicit_boolean", [a], [BALL], op="difference", blend_radius=0.001))
    assert values(inner, (0, 0, 0))[0] > 0
