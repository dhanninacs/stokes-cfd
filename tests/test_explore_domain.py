"""Variable domains, the design space and the design table (stokes.explore.domain, space, table)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from stokes.errors import ValidationError
from stokes.explore.domain import (
    Categorical,
    Normal,
    Range,
    Uniform,
    ValueList,
    domain_from_dict,
)
from stokes.explore.errors import DomainError, InvalidInput
from stokes.explore.space import DesignSpace, Variable
from stokes.explore.table import DesignTable

UNIT = np.linspace(0.0, 1.0, 11)


# ---------------------------------------------------------------- value lists


def test_value_list_is_sorted_and_maps_equal_probability_strata() -> None:
    domain = ValueList([30, 10, 20])
    assert domain.values == (10.0, 20.0, 30.0)
    assert (domain.low, domain.high, domain.is_discrete) == (10.0, 30.0, True)
    values = domain.from_unit(np.array([0.0, 0.2, 0.34, 0.66, 0.67, 1.0]))
    assert values.tolist() == [10.0, 10.0, 20.0, 20.0, 30.0, 30.0]
    # to_unit is the inverse on the levels: the middle of the level's stratum
    assert np.allclose(domain.to_unit([10, 20, 30]), [1 / 6, 0.5, 5 / 6])
    assert domain.snap([12.0, 16.0, 26.0, 99.0]).tolist() == [10.0, 20.0, 30.0, 30.0]


def test_value_list_rejects_duplicates_empty_and_non_finite() -> None:
    for bad in ([], [1, 1], [1, math.nan], [math.inf]):
        with pytest.raises(DomainError):
            ValueList(bad)
    with pytest.raises(DomainError):
        ValueList([1, 2]).levels(3)  # its levels are fixed


# ---------------------------------------------------------------- ranges and distributions


def test_range_levels_from_count_and_step() -> None:
    assert Range(0, 1, count=5).levels().tolist() == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert np.allclose(Range(0, 1, step=0.3).levels(), [0.0, 0.3, 0.6, 0.9])  # high only if on grid
    assert Range(0, 1, step=0.25).levels().tolist() == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert Range(0, 1).levels(3).tolist() == [0.0, 0.5, 1.0]  # a count passed in wins
    with pytest.raises(DomainError):
        Range(0, 1).levels()  # no intrinsic grid


def test_range_snap_to_grid_rounds_samples_to_levels() -> None:
    domain = Range(0, 1, step=0.25, snap_to_grid=True)
    assert set(domain.from_unit(UNIT)) <= {0.0, 0.25, 0.5, 0.75, 1.0}
    assert Range(0, 1, count=3).snap([0.4, 2.0, -1.0]).tolist() == [0.4, 1.0, 0.0]  # clip only


@pytest.mark.parametrize(
    "kwargs",
    [
        {"low": 1, "high": 1},
        {"low": 2, "high": 1},
        {"low": 0, "high": 1, "count": 1},
        {"low": 0, "high": 1, "count": 3, "step": 0.5},
        {"low": 0, "high": 1, "step": 2.0},
        {"low": 0, "high": 1, "snap_to_grid": True},
        {"low": 0, "high": math.inf},
    ],
)
def test_range_validation(kwargs: dict[str, float]) -> None:
    with pytest.raises(DomainError):
        Range(**kwargs)  # type: ignore[arg-type]


def test_uniform_and_normal_quantiles() -> None:
    assert np.allclose(Uniform(-1, 3).from_unit(UNIT), -1 + 4 * UNIT)
    normal = Normal(10.0, 2.0, low=4.0, high=20.0)
    values = normal.from_unit(UNIT)
    assert values[0] == 4.0 and values[-1] == 20.0 and np.all(np.diff(values) > 0)
    symmetric = Normal(10.0, 2.0, low=4.0, high=16.0)
    assert symmetric.from_unit(0.5) == pytest.approx(10.0, abs=1e-9)  # median of a symmetric one
    # default truncation is six standard deviations, resolved at construction
    assert (Normal(1.0, 0.1).low, Normal(1.0, 0.1).high) == pytest.approx((0.4, 1.6))
    resolved = Normal(1.0, 0.1)
    assert Normal(1.0, 0.1, low=resolved.low, high=resolved.high) == resolved
    with pytest.raises(DomainError):
        Normal(0.0, 0.0)
    with pytest.raises(DomainError):
        Normal(0.0, 1.0, low=2.0, high=1.0)


@settings(max_examples=40, deadline=None, derandomize=True)
@given(
    u=st.lists(st.floats(0.0, 1.0), min_size=1, max_size=20),
    low=st.floats(-100, 100),
    width=st.floats(1e-3, 100),
)
def test_unit_round_trip_on_continuous_domains(u: list[float], low: float, width: float) -> None:
    unit = np.array(u)
    for domain in (Range(low, low + width), Uniform(low, low + width)):
        assert np.allclose(domain.to_unit(domain.from_unit(unit)), unit, atol=1e-9)
    normal = Normal(low, width / 6.0)
    assert np.allclose(normal.to_unit(normal.from_unit(unit)), unit, atol=1e-6)


def test_levels_of_distributions_need_a_count() -> None:
    assert Uniform(0, 1).levels(3).tolist() == [0.0, 0.5, 1.0]
    levels = Normal(0.0, 1.0).levels(4)
    assert np.allclose(levels, -levels[::-1])  # symmetric quantile midpoints
    for domain in (Uniform(0, 1), Normal(0.0, 1.0)):
        with pytest.raises(DomainError):
            domain.levels()


# ---------------------------------------------------------------- categories


def test_categorical_index_levels() -> None:
    domain = Categorical(["k-epsilon", "k-omega", "SST"])
    assert domain.levels().tolist() == [0.0, 1.0, 2.0]
    assert (domain.low, domain.high, domain.is_discrete) == (0.0, 2.0, True)
    assert domain.from_unit(np.array([0.0, 0.4, 0.7, 1.0])).tolist() == [0.0, 1.0, 2.0, 2.0]
    assert domain.index("SST") == 2 and domain.label(1.0) == "k-omega"
    assert np.allclose(domain.to_unit([0.0, 1.0, 2.0]), [1 / 6, 0.5, 5 / 6])
    assert domain.snap([0.4, 1.6, 9.0]).tolist() == [0.0, 2.0, 2.0]
    for bad in ([], ["a", "a"], ["a", " "]):
        with pytest.raises(DomainError):
            Categorical(bad)
    with pytest.raises(DomainError):
        domain.index("laminar")
    with pytest.raises(DomainError):
        domain.label(3.0)


# ---------------------------------------------------------------- serialisation


@pytest.mark.parametrize(
    "domain",
    [
        ValueList([3, 1, 2]),
        Range(0, 2),
        Range(0, 2, count=5),
        Range(0, 2, step=0.5, snap_to_grid=True),
        Uniform(-1, 1),
        Normal(5, 1, 2, 9),
        Categorical(["a", "b"]),
    ],
)
def test_domain_dict_round_trip(domain: object) -> None:
    data = domain.to_dict()  # type: ignore[attr-defined]
    assert domain_from_dict(data) == domain
    variable = Variable("x", domain)  # type: ignore[arg-type]
    assert Variable.from_dict(variable.to_dict()) == variable


def test_domain_from_dict_errors_are_validation_errors() -> None:
    for bad in ({"kind": "triangular"}, {"kind": "range", "low": 0}, {"kind": "values"}, {}):
        with pytest.raises(DomainError) as info:
            domain_from_dict(bad)
        assert isinstance(info.value, ValidationError) and info.value.code == "explore.domain"
    with pytest.raises(DomainError):
        Variable.from_dict({"domain": {"kind": "uniform", "low": 0, "high": 1}})
    with pytest.raises(DomainError):
        Variable(" ", Uniform(0, 1))


# ---------------------------------------------------------------- design space


def _space() -> DesignSpace:
    return DesignSpace.of(
        [
            Variable("a", Range(0, 10)),
            Variable("b", Uniform(-1, 1)),
            Variable("c", Normal(5, 1, 2, 8)),
            Variable("d", ValueList([1, 2, 4])),
            Variable("e", Categorical(["p", "q"])),
        ]
    )


def test_design_space_scaling_and_properties() -> None:
    space = _space()
    assert space.names == ("a", "b", "c", "d", "e") and space.size == 5
    assert space.bounds.tolist() == [[0, 10], [-1, 1], [2, 8], [1, 4], [0, 1]]
    assert space.discrete.tolist() == [False, False, False, True, True]
    unit = np.random.default_rng(0).random((50, 5))
    points = space.from_unit(unit)
    assert space.contains(points).all()
    again = space.from_unit(space.to_unit(points))
    assert np.allclose(again, points)  # quantile maps invert each other, discrete ones on levels
    assert space.contains([[11, 0, 5, 2, 0]]).tolist() == [False]
    assert space.snap([[3.0, 9.0, 1.0, 3.2, 0.7]]).tolist() == [[3.0, 1.0, 2.0, 4.0, 1.0]]
    assert DesignSpace.of(space) is space
    assert space.column("c") == 2
    with pytest.raises(DomainError):
        space.column("zz")


def test_design_space_validation_and_from_bounds() -> None:
    with pytest.raises(DomainError):
        DesignSpace(())
    with pytest.raises(DomainError):
        DesignSpace.of([Variable("a", Range(0, 1)), Variable("a", Range(0, 1))])
    box = DesignSpace.from_bounds([[0, 1], [2, 4]], ["u", "v"])
    assert box.names == ("u", "v") and box.bounds.tolist() == [[0, 1], [2, 4]]
    assert DesignSpace.from_bounds([[0, 1]]).names == ("x1",)
    with pytest.raises(InvalidInput):
        space = box.from_unit(np.zeros((3, 3)))  # wrong column count
        del space


# ---------------------------------------------------------------- design table


def _table() -> DesignTable:
    variables = [Variable("a", Range(0, 1)), Variable("m", Categorical(["x", "y"]))]
    points = np.array([[0.1, 0.0], [0.5, 1.0], [0.9, 1.0]])
    return DesignTable(variables, points, {"sampler": "hand", "seed": None})


def test_design_table_holds_variables_points_and_metadata() -> None:
    table = _table()
    assert (table.n_points, table.n_variables, table.names) == (3, 2, ("a", "m"))
    assert table.metadata == {"sampler": "hand", "seed": None}
    assert "3 points" in repr(table)
    assert table.column("a").tolist() == [0.1, 0.5, 0.9]
    assert table.unit_points().shape == (3, 2)
    with pytest.raises(ValueError, match="read-only"):
        table.points[0, 0] = 5.0
    records = table.to_records()
    assert records[1] == {"a": 0.5, "m": "y"}  # categorical indices read back as labels


def test_design_table_validates_shapes_and_values() -> None:
    variables = [Variable("a", Range(0, 1))]
    with pytest.raises(InvalidInput):
        DesignTable(variables, np.zeros((3, 2)))
    with pytest.raises(InvalidInput):
        DesignTable(variables, np.array([[math.nan]]))
    with pytest.raises(InvalidInput):
        DesignTable(variables, np.zeros((2, 1)), responses=np.zeros((3, 1)), response_names=("y",))
    with pytest.raises(InvalidInput):
        DesignTable(variables, np.zeros((2, 1)), responses=np.zeros((2, 1)), response_names=("a",))
    with pytest.raises(InvalidInput):
        DesignTable(variables, np.zeros((2, 1)), response_names=("y",))


def test_design_table_responses_selection_and_json() -> None:
    table = _table().with_responses([[1.0, 2.0], [math.nan, 3.0], [4.0, 5.0]], ["p", "q"])
    assert table.valid_rows().tolist() == [True, False, True]
    assert table.column("q").tolist() == [2.0, 3.0, 5.0]
    kept = table.select(table.valid_rows())
    assert kept.n_points == 2 and kept.responses is not None and kept.responses.shape == (2, 2)
    assert table.select(slice(1, 3)).n_points == 2
    assert table.to_records()[1]["p"] is None  # NaN is null in the record form
    data = table.to_dict()
    assert data["responses"][1] == [None, 3.0]  # type: ignore[index]
    again = DesignTable.from_dict(data)
    assert again.names == table.names and again.response_names == ("p", "q")
    assert np.array_equal(again.points, table.points)
    assert again.responses is not None and np.isnan(again.responses[1, 0])
    with pytest.raises(ValidationError):
        DesignTable.from_dict({"variables": [{"name": "a"}], "points": []})
    with pytest.raises(InvalidInput):
        DesignTable.from_dict({"points": []})
