"""The default step of a gradient check (`shape.check`): large enough for the mesh to see, and
never so large that the "minus" point is a negative length. The units are spelled as the model
spells them ("m", not "meter"): the length branch was once keyed on the long spelling and never
ran."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from stokes.api.commands.implicit import _default_step


class _Project:
    """What `_default_step` reads of a project: the quantity's unit and the mesh's base size."""

    def __init__(self, units: str, base_size: float) -> None:
        self._units, self._base = units, base_size

    def resolve_param(self, reference: str) -> tuple[Any, str]:
        return SimpleNamespace(id="n_1"), reference

    def value_of(self, node: str, param: str) -> Any:
        dimension = "[length]" if self._units == "m" else ""
        quantity = SimpleNamespace(units=self._units, dimensionality=dimension)
        return SimpleNamespace(to_base_units=lambda: quantity)

    def find(self, path: str) -> Any:
        return SimpleNamespace(params={"base_size": self._base})


def test_a_length_steps_by_four_base_cells_when_it_is_long_enough() -> None:
    assert _default_step(_Project("m", 0.001), "depth", 0.020) == pytest.approx(0.004)


def test_a_short_length_steps_by_half_of_itself_so_the_minus_point_stays_positive() -> None:
    # A 3 mm corner cut on a 1 mm mesh: four cells would take it to -1 mm.
    step = _default_step(_Project("m", 0.001), "c", 0.003)
    assert step == pytest.approx(0.0015)
    assert 0.003 - step > 0.0


def test_a_length_at_zero_and_an_angle_keep_their_floors() -> None:
    assert _default_step(_Project("m", 0.001), "offset", 0.0) == pytest.approx(0.004)
    assert _default_step(_Project("rad", 0.001), "angle", 0.2) == pytest.approx(0.05)
