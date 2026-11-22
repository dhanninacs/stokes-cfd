"""The lattice operation (ADR-0035 decision 7, slice 3): a body filled with a triply periodic
minimal surface, as recipes checked with the evaluator. The volume fractions are the known ones:
a network at no bias is half the volume, and a gyroid sheet of thickness t in a cell a holds about
3.09 t / a of it (the gyroid's area in a unit cell is about 3.09 a squared)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import stokes.model  # noqa: F401 - the registry, before the geometry model
from stokes.geometry.errors import OperationInvalid
from stokes.geometry.model import IMPLICIT_LATTICES
from stokes.geometry.operations.implicit import body_of
from stokes.implicit.evaluate import evaluate
from tests.test_implicit_recipes import recipe

BLOCK = recipe("implicit_primitive", kind="box", size=[0.02, 0.02, 0.02])


def fraction(body: Any, half: float = 10.0, n: int = 48) -> float:  # Any: Body
    """The share of the cube of half-width ``half`` (mm) the body fills, on an n^3 grid."""
    at = (np.arange(n) + 0.5) / n * 2 * half - half
    x, y, z = np.meshgrid(at, at, at, indexing="ij")
    points = list(zip(x.ravel().tolist(), y.ravel().tolist(), z.ravel().tolist(), strict=True))
    return float((evaluate(body.field(), points) < 0.0).mean())


@pytest.mark.parametrize("kind", IMPLICIT_LATTICES)
def test_a_network_at_no_bias_is_half_the_body(kind: str) -> None:
    body = body_of(recipe("implicit_lattice", [BLOCK], kind=kind, form="network", cell=0.005))
    assert body.metric == "scaled"
    assert body.bounded() == ((-10.0, -10.0, -10.0), (10.0, 10.0, 10.0))
    assert fraction(body) == pytest.approx(0.5, abs=0.03)


def test_a_bias_moves_the_network_towards_one_side() -> None:
    def share(bias: float) -> float:
        return fraction(
            body_of(
                recipe(
                    "implicit_lattice",
                    [BLOCK],
                    kind="gyroid",
                    form="network",
                    cell=0.005,
                    bias=bias,
                )
            )
        )

    assert share(-0.4) < 0.35 < 0.65 < share(0.4)


def test_a_gyroid_sheet_holds_the_volume_its_thickness_says() -> None:
    body = body_of(
        recipe(
            "implicit_lattice", [BLOCK], kind="gyroid", form="sheet", cell=0.005, thickness=0.0005
        )
    )
    # 3.09 * 0.5 / 5 = 0.31; the slope that turns the level into a thickness is a mean.
    assert fraction(body, n=64) == pytest.approx(0.31, abs=0.05)
    assert body.smallest == pytest.approx(0.5)


def test_the_lattice_stays_inside_its_body() -> None:
    ball = recipe("implicit_primitive", kind="sphere", radius=0.008)
    body = body_of(recipe("implicit_lattice", [ball], kind="schwarz_p", form="network", cell=0.004))
    outside = evaluate(body.field(), [(9.0, 0.0, 0.0), (0.0, 8.5, 0.0), (6.0, 6.0, 6.0)])
    assert (outside > 0.0).all()


def test_the_origin_moves_the_cells() -> None:
    def at_centre(origin: list[float]) -> float:
        body = body_of(
            recipe(
                "implicit_lattice",
                [BLOCK],
                kind="schwarz_p",
                form="network",
                cell=0.005,
                origin=origin,
            )
        )
        return float(evaluate(body.field(), [(0.0, 0.0, 0.0)])[0])

    # Schwarz P is cos + cos + cos: 3 at a cell's corner (outside), -3 half a cell away (inside).
    assert at_centre([0.0, 0.0, 0.0]) > 0.0 > at_centre([0.0025, 0.0025, 0.0025])


def test_what_a_lattice_refuses() -> None:
    def build(**params: Any) -> Any:
        return body_of(recipe("implicit_lattice", [BLOCK], **params))

    with pytest.raises(OperationInvalid, match="third of the cell"):
        build(kind="gyroid", form="sheet", cell=0.003, thickness=0.001)
    with pytest.raises(OperationInvalid, match="at most 48"):
        build(kind="gyroid", form="sheet", cell=0.0002, thickness=0.00002)
    with pytest.raises(OperationInvalid, match="bias"):
        build(kind="gyroid", form="network", cell=0.005, bias=0.95)
    with pytest.raises(OperationInvalid, match="unknown lattice"):
        build(kind="octet", form="sheet", cell=0.005)
    with pytest.raises(OperationInvalid, match="exactly one"):
        body_of(recipe("implicit_lattice", [], kind="gyroid"))
    # Its field is not a distance, so it cannot be offset: the wall thickness is its own.
    filled = recipe("implicit_lattice", [BLOCK], kind="gyroid", form="sheet", cell=0.005)
    with pytest.raises(OperationInvalid, match="field is a distance"):
        body_of(recipe("implicit_offset", [filled], kind="offset", distance=0.0005))


def test_the_wall_and_its_two_channels_fill_the_body_without_overlap() -> None:
    """A two-fluid heat exchanger: the sheet is the wall, the channels the hot and the cold side.
    Every point of the block is in exactly one of the three."""

    def part(form: str) -> Any:
        return body_of(
            recipe(
                "implicit_lattice",
                [BLOCK],
                kind="gyroid",
                form=form,
                cell=0.008,
                thickness=0.001,
            )
        )

    n = 40
    at = (np.arange(n) + 0.5) / n * 20.0 - 10.0
    x, y, z = np.meshgrid(at, at, at, indexing="ij")
    points = list(zip(x.ravel().tolist(), y.ravel().tolist(), z.ravel().tolist(), strict=True))
    inside = [
        evaluate(part(form).field(), points) < 0.0 for form in ("sheet", "channel_a", "channel_b")
    ]
    count = inside[0].astype(int) + inside[1].astype(int) + inside[2].astype(int)
    assert (count == 1).mean() > 0.999  # points exactly on a surface may belong to none
    assert (count <= 1).all()
    # The two channels are the same size (the gyroid is symmetric) and the wall is the rest.
    a, b, wall = inside[1].mean(), inside[2].mean(), inside[0].mean()
    assert a == pytest.approx(b, abs=0.01)
    assert wall == pytest.approx(3.09 * 1.0 / 8.0, abs=0.06)
