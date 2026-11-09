"""Design-of-experiments samplers (stokes.explore.sampling)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from stokes.explore.domain import Categorical, Normal, Range, Uniform, ValueList
from stokes.explore.errors import SamplerError
from stokes.explore.sampling import (
    box_behnken,
    central_composite,
    full_factorial,
    generate,
    halton,
    latin_hypercube,
    min_distance,
    monte_carlo,
    optimal_space_filling,
    sampler_names,
    sobol,
    unit_latin_hypercube,
)
from stokes.explore.space import DesignSpace, Variable


def box(k: int, low: float = 0.0, high: float = 1.0) -> list[Variable]:
    return [Variable(f"x{i + 1}", Range(low, high)) for i in range(k)]


MIXED = [
    Variable("length", Range(10.0, 50.0)),
    Variable("speed", Uniform(-1.0, 1.0)),
    Variable("temp", Normal(300.0, 10.0, 270.0, 330.0)),
    Variable("pitch", ValueList([1.0, 2.0, 4.0])),
    Variable("model", Categorical(["a", "b", "c"])),
]


# ---------------------------------------------------------------- determinism and bounds


@pytest.mark.parametrize(
    "sampler", ["latin_hypercube", "sobol", "halton", "optimal_space_filling", "monte_carlo"]
)
def test_samplers_are_deterministic_for_a_seed(sampler: str) -> None:
    first = generate(sampler, MIXED, n=24, seed=5)
    again = generate(sampler, MIXED, n=24, seed=5)
    other = generate(sampler, MIXED, n=24, seed=6)
    assert np.array_equal(first.points, again.points)
    assert not np.array_equal(first.points, other.points)
    assert first.metadata["sampler"] in {sampler, "latin_hypercube", "optimal_space_filling"}
    assert first.metadata["seed"] == 5 and first.metadata["n"] == 24


@pytest.mark.parametrize(
    "sampler", ["latin_hypercube", "sobol", "halton", "optimal_space_filling", "monte_carlo"]
)
def test_every_sampler_stays_inside_the_domains(sampler: str) -> None:
    table = generate(sampler, MIXED, n=64, seed=1)
    assert table.points.shape == (64, 5)
    assert table.space.contains(table.points).all()
    assert set(table.points[:, 3]) <= {1.0, 2.0, 4.0}  # explicit values only
    assert set(table.points[:, 4]) <= {0.0, 1.0, 2.0}  # category indices only


def test_latin_hypercube_stratification() -> None:
    n = 50
    table = latin_hypercube(MIXED[:3], n, seed=3)
    strata = np.floor(table.unit_points() * n).astype(int)
    for j in range(3):  # exactly one point per equal-probability stratum, for any distribution
        assert sorted(strata[:, j]) == list(range(n))
    centred = unit_latin_hypercube(4, 10, seed=1, centered=True)
    assert np.allclose(np.sort(centred, axis=0), (np.arange(10) + 0.5)[:, None] / 10)


def test_latin_hypercube_optimization_option() -> None:
    plain = unit_latin_hypercube(3, 20, seed=2)
    tuned = unit_latin_hypercube(3, 20, seed=2, optimization="random-cd")
    assert plain.shape == tuned.shape and not np.array_equal(plain, tuned)
    with pytest.raises(SamplerError):
        unit_latin_hypercube(3, 20, optimization="nonsense")


@pytest.mark.parametrize("n", [1, 7, 16, 100, 128])
def test_sobol_and_halton_point_counts_and_bounds(n: int) -> None:
    for sampler in (sobol, halton):
        table = sampler(box(4, -2.0, 3.0), n, seed=0)
        assert table.points.shape == (n, 4)
        assert table.points.min() >= -2.0 and table.points.max() <= 3.0


def test_sobol_power_of_two_is_balanced() -> None:
    unit = sobol(box(3), 64, seed=1).points
    # a scrambled Sobol' set of 2**m points puts exactly 2**(m-1) in each half of every axis
    assert np.all((unit < 0.5).sum(axis=0) == 32)
    unscrambled = sobol(box(2), 8, scramble=False).points
    assert unscrambled[0].tolist() == [0.0, 0.0]


def test_sample_counts_must_be_positive_integers() -> None:
    for bad in (0, -3, 2.5, "ten"):
        with pytest.raises(SamplerError):
            latin_hypercube(box(2), bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------- Monte Carlo


def test_monte_carlo_follows_the_distributions() -> None:
    table = monte_carlo(
        [Variable("u", Uniform(2.0, 4.0)), Variable("n", Normal(10.0, 2.0, 0.0, 20.0))],
        20000,
        seed=4,
    )
    u, n = table.points[:, 0], table.points[:, 1]
    assert u.mean() == pytest.approx(3.0, abs=0.03) and u.min() >= 2.0 and u.max() <= 4.0
    assert n.mean() == pytest.approx(10.0, abs=0.06) and n.std() == pytest.approx(2.0, abs=0.06)
    assert n.min() >= 0.0 and n.max() <= 20.0


# ---------------------------------------------------------------- full factorial


def test_full_factorial_levels_order_and_count() -> None:
    table = full_factorial(
        [
            Variable("a", ValueList([3, 1, 2])),
            Variable("b", Range(0, 1, count=2)),
            Variable("c", Categorical(["p", "q"])),
        ]
    )
    assert table.n_points == 12 and table.metadata["levels"] == [3, 2, 2]
    assert table.points[:4].tolist() == [
        [1, 0, 0],
        [1, 0, 1],
        [1, 1, 0],
        [1, 1, 1],
    ]  # first slowest
    assert len({tuple(row) for row in table.points}) == 12


def test_full_factorial_level_counts_for_continuous_variables() -> None:
    variables = [Variable("u", Uniform(0, 1)), Variable("r", Range(0, 2, count=3))]
    with pytest.raises(SamplerError, match="level count"):
        full_factorial(variables)
    assert full_factorial(variables, 4).n_points == 12  # int: default for those without levels
    table = full_factorial(variables, {"u": 2, "r": 5})  # mapping overrides, even a range's grid
    assert table.n_points == 10 and table.points[:, 1].max() == 2.0
    with pytest.raises(SamplerError, match="unknown"):
        full_factorial(variables, {"zz": 2})
    with pytest.raises(SamplerError):
        full_factorial([Variable("a", ValueList([1, 2]))], {"a": 3})


def test_full_factorial_refuses_an_explosion() -> None:
    variables = box(8)
    with pytest.raises(SamplerError) as info:
        full_factorial(variables, 10, max_points=1000)
    assert info.value.code == "explore.sampler" and info.value.details["points"] == 10**8


# ---------------------------------------------------------------- central composite


@pytest.mark.parametrize(("k", "expected"), [(2, 9), (3, 15), (4, 25)])
def test_central_composite_point_counts_match_the_textbook(k: int, expected: int) -> None:
    # 2**k cube points + 2k axial points + 1 centre point
    for kind in ("faced", "circumscribed", "inscribed"):
        assert central_composite(box(k), kind).n_points == expected  # type: ignore[arg-type]
    assert central_composite(box(k), center_points=5).n_points == expected + 4
    assert central_composite(box(k), center_points=0).n_points == expected - 1


def test_central_composite_fractional_cube_from_five_variables() -> None:
    assert central_composite(box(5)).n_points == 16 + 10 + 1  # half fraction, resolution V
    assert central_composite(box(6)).n_points == 32 + 12 + 1
    assert central_composite(box(5), half_fraction=False).n_points == 32 + 10 + 1
    assert central_composite(box(3), half_fraction=True).n_points == 4 + 6 + 1
    cube = central_composite(box(5)).points[:16]
    assert np.all(np.prod(np.sign(cube - 0.5), axis=1) > 0)  # defining relation I = x1...x5


def test_central_composite_geometry() -> None:
    bounds = [Variable("x", Range(0.0, 2.0)), Variable("y", Range(10.0, 14.0))]
    faced = central_composite(bounds, "faced")
    assert faced.space.contains(faced.points).all()  # axial points on the faces of the box
    assert faced.points[:4].tolist() == [[0, 10], [0, 14], [2, 10], [2, 14]]
    assert [1.0, 10.0] in faced.points.tolist() and [1.0, 12.0] in faced.points.tolist()

    circumscribed = central_composite(bounds, "circumscribed")
    alpha = 4**0.25  # rotatable: F**(1/4) with F = 4 cube points
    assert circumscribed.metadata["alpha"] == pytest.approx(alpha)
    assert circumscribed.points[:, 0].max() == pytest.approx(1.0 + alpha)  # star beyond the bounds
    assert not circumscribed.space.contains(circumscribed.points).all()

    inscribed = central_composite(bounds, "inscribed")
    assert inscribed.space.contains(inscribed.points).all()
    assert (
        inscribed.points[:, 0].min() == pytest.approx(0.0) and inscribed.points[:, 0].max() == 2.0
    )
    assert inscribed.points[0, 0] == pytest.approx(1.0 - 1.0 / alpha)  # cube shrunk by 1/alpha

    custom = central_composite(bounds, "circumscribed", alpha=2.0)
    assert custom.points[:, 0].max() == pytest.approx(3.0)


def test_central_composite_errors() -> None:
    with pytest.raises(SamplerError, match="at least two"):
        central_composite(box(1))
    with pytest.raises(SamplerError, match="continuous"):
        central_composite([Variable("a", Range(0, 1)), Variable("m", Categorical(["x", "y"]))])
    with pytest.raises(SamplerError, match="alpha"):
        central_composite(box(2), "faced", alpha=1.5)
    with pytest.raises(SamplerError):
        central_composite(box(2), "star")  # type: ignore[arg-type]
    with pytest.raises(SamplerError):
        central_composite(box(2), center_points=-1)


# ---------------------------------------------------------------- Box-Behnken


@pytest.mark.parametrize(("k", "edges"), [(3, 12), (4, 24), (5, 40)])
def test_box_behnken_point_counts_match_the_textbook(k: int, edges: int) -> None:
    assert edges == 4 * k * (k - 1) // 2
    assert box_behnken(box(k)).n_points == edges + 1
    assert box_behnken(box(k), center_points=3).n_points == edges + 3  # 15 and 27 runs for k=3, 4
    table = box_behnken(box(k, -1.0, 1.0), center_points=0)
    coded = table.points
    assert np.all((np.abs(coded) == 1.0).sum(axis=1) == 2)  # two factors at +/-1, the rest at 0
    assert table.space.contains(coded).all()


def test_box_behnken_needs_three_continuous_variables() -> None:
    with pytest.raises(SamplerError, match="three"):
        box_behnken(box(2))
    with pytest.raises(SamplerError, match="continuous"):
        box_behnken([*box(2), Variable("m", Categorical(["x", "y"]))])


# ---------------------------------------------------------------- optimal space filling


def test_optimal_space_filling_beats_plain_latin_hypercube() -> None:
    variables = box(4)
    gains = []
    for seed in range(4):
        tuned = optimal_space_filling(variables, 30, seed=seed)
        plain = latin_hypercube(variables, 30, seed=seed)
        assert tuned.metadata["min_distance"] == pytest.approx(min_distance(tuned.points))
        assert tuned.metadata["min_distance"] >= tuned.metadata["initial_min_distance"]
        gains.append(tuned.metadata["min_distance"] / min_distance(plain.points))
        strata = np.floor(tuned.points * 30).astype(int)
        for j in range(4):  # still a Latin hypercube
            assert sorted(strata[:, j]) == list(range(30))
    assert np.mean(gains) > 1.4  # much better spread than a random Latin hypercube


def test_optimal_space_filling_discrepancy_criterion_and_errors() -> None:
    table = optimal_space_filling(box(3), 16, seed=1, criterion="discrepancy")
    assert table.metadata["criterion"] == "discrepancy" and table.n_points == 16
    assert optimal_space_filling(box(2), 2, seed=1).n_points == 2  # too few points to optimise
    with pytest.raises(SamplerError):
        optimal_space_filling(box(2), 10, criterion="best")  # type: ignore[arg-type]


# ---------------------------------------------------------------- dispatcher


def test_generate_dispatches_by_name_and_alias() -> None:
    assert set(sampler_names()) == {
        "full_factorial",
        "latin_hypercube",
        "sobol",
        "halton",
        "central_composite",
        "box_behnken",
        "optimal_space_filling",
        "monte_carlo",
    }
    assert generate("LHS", box(2), n=5, seed=1).metadata["sampler"] == "latin_hypercube"
    assert generate("ccd", box(3), kind="inscribed").metadata["kind"] == "inscribed"
    assert generate("bbd", box(3)).n_points == 13
    assert generate("full-factorial", box(2), levels=3).n_points == 9
    with pytest.raises(SamplerError, match="unknown sampler"):
        generate("quantum", box(2), n=3)
    with pytest.raises(SamplerError, match="needs the number"):
        generate("sobol", box(2))
    with pytest.raises(SamplerError, match="fixed number"):
        generate("ccd", box(3), n=10)


def test_samplers_accept_a_design_space_and_record_metadata() -> None:
    space = DesignSpace.of(MIXED)
    table = sobol(space, 8, seed=9, scramble=True)
    assert table.variables == space.variables
    assert table.metadata == {"sampler": "sobol", "seed": 9, "n": 8, "scramble": True}
    assert math.isfinite(table.points.sum())


@settings(max_examples=25, deadline=None, derandomize=True)
@given(n=st.integers(1, 60), d=st.integers(1, 6), seed=st.integers(0, 10_000))
def test_latin_hypercube_is_stratified_for_any_size_and_seed(n: int, d: int, seed: int) -> None:
    table = latin_hypercube(box(d), n, seed=seed)
    strata = np.floor(table.points * n).astype(int)
    for j in range(d):
        assert sorted(strata[:, j]) == list(range(n))
    assert np.array_equal(table.points, latin_hypercube(box(d), n, seed=seed).points)
