"""The analytic test problems (stokes.explore.functions) really have the answers they claim."""

from __future__ import annotations

import math

import numpy as np
import pytest

from stokes.explore.errors import InvalidInput
from stokes.explore.functions import (
    binh_korn,
    branin,
    dtlz2,
    function,
    function_names,
    ishigami,
    ishigami_indices,
    rosenbrock,
    rosenbrock_disk,
    schaffer1,
    zdt1,
    zdt2,
)
from stokes.explore.pareto import hypervolume, non_dominated_mask


def test_branin_has_three_global_minima() -> None:
    f = branin()
    values = f.evaluate(np.array(f.minimizers))[:, 0]
    assert np.allclose(values, 0.397887, atol=2e-6)
    corners = f.evaluate(np.array([[-5.0, 0.0], [10.0, 15.0]]))[:, 0]
    assert np.all(corners > 1.0) and f.minimum == 0.397887
    assert [v.name for v in f.variables] == ["x1", "x2"]
    assert f.problem().bounds.tolist() == [[-5.0, 10.0], [0.0, 15.0]]


def test_rosenbrock_family() -> None:
    for n in (2, 3, 6):
        f = rosenbrock(n)
        assert f.evaluate(np.ones((1, n)))[0, 0] == 0.0 and f.n_var == n
        assert f.evaluate(np.zeros((1, n)))[0, 0] == n - 1.0
    assert rosenbrock().evaluate([[-1.2, 1.0]])[0, 0] == pytest.approx(24.2)
    with pytest.raises(InvalidInput):
        rosenbrock(1)


def test_constrained_rosenbrock_has_its_minimum_on_the_circle() -> None:
    f = rosenbrock_disk()
    optimum = f.evaluate(np.array(f.minimizers))
    assert optimum[0, 0] == pytest.approx(f.minimum, abs=1e-6)
    assert optimum[0, 1] == pytest.approx(1.0, abs=1e-5)  # on the constraint boundary
    problem = f.problem()
    assert problem.feasible(optimum - np.array([0.0, 1e-6])).all()
    assert not problem.feasible(f.evaluate([[1.0, 1.0]])).any()  # the unconstrained minimum is out
    assert problem.constraints[0].kind == "<="


@pytest.mark.parametrize("make", [zdt1, zdt2])
def test_zdt_fronts_and_hypervolumes(make: object) -> None:
    f = make(10)  # type: ignore[operator]
    front = f.front(11)
    x = np.zeros((11, 10))
    x[:, 0] = front[:, 0]  # the rest at zero puts g at 1, on the front
    assert np.allclose(f.evaluate(x), front)
    assert non_dominated_mask(front).all()
    off = x.copy()
    off[:, 1:] = 0.5
    assert np.all(f.evaluate(off)[:, 1] > front[:, 1])  # anywhere else is worse
    exact = f.true_hypervolume([1.1, 1.1])
    assert hypervolume(f.front(50000), [1.1, 1.1]) == pytest.approx(exact, abs=1e-4)
    assert f.problem().n_objectives == 2 and f.response_names == ("f1", "f2")


def test_zdt1_is_convex_and_zdt2_concave() -> None:
    f1, f2 = zdt1().front(3)[1], zdt2().front(3)[1]  # the point at f1 = 0.5
    assert f1[1] == pytest.approx(1 - math.sqrt(0.5)) and f2[1] == pytest.approx(0.75)


@pytest.mark.parametrize("n_obj", [2, 3, 4])
def test_dtlz2_front_is_the_unit_sphere(n_obj: int) -> None:
    f = dtlz2(n_obj)
    assert f.n_var == n_obj + 9
    front = f.front(30)
    assert front.shape[1] == n_obj and front.shape[0] >= 30
    assert np.allclose((front**2).sum(axis=1), 1.0)
    rng = np.random.default_rng(0)
    x = np.full((20, f.n_var), 0.5)  # tail at 0.5 makes g = 0
    x[:, : n_obj - 1] = rng.random((20, n_obj - 1))
    assert np.allclose((f.evaluate(x) ** 2).sum(axis=1), 1.0)
    x[:, n_obj - 1 :] = 0.0
    assert np.all((f.evaluate(x) ** 2).sum(axis=1) > 1.0)  # a distance above the front
    if n_obj == 3:
        reference = [1.1, 1.1, 1.1]
        assert f.true_hypervolume(reference) == pytest.approx(1.331 - math.pi / 6)


def test_schaffer_and_binh_korn() -> None:
    s = schaffer1()
    assert s.evaluate([[0.0], [2.0], [1.0]]).tolist() == [[0.0, 4.0], [4.0, 0.0], [1.0, 1.0]]
    assert np.allclose(s.front(3), [[0, 4], [1, 1], [4, 0]])
    b = binh_korn()
    problem = b.problem()
    front = b.front(1000)
    segment = np.array([[1.0, 1.0], [3.0, 3.0], [4.0, 3.0], [5.0, 3.0]])  # on the Pareto set
    responses = b.evaluate(segment)
    assert problem.feasible(responses).all()
    for objectives in responses[:, :2]:  # each lies on the sampled true front
        assert np.min(np.linalg.norm(front - objectives, axis=1)) < 0.5
    assert not problem.feasible(b.evaluate([[0.0, 3.0]])).any()  # outside (x-5)^2 + y^2 <= 25
    assert front.shape[0] >= 900 and non_dominated_mask(front).all()
    assert front[:, 0].min() == 0.0 and front[:, 0].max() == 136.0


def test_ishigami_exact_sobol_indices() -> None:
    first, total = ishigami_indices()
    assert first.round(4).tolist() == [0.3139, 0.4424, 0.0]
    assert total.round(4).tolist() == [0.5576, 0.4424, 0.2437]
    assert np.all(total >= first) and total[2] == pytest.approx(0.2437, abs=1e-4)
    f = ishigami()
    rng = np.random.default_rng(1)
    x = rng.uniform(-math.pi, math.pi, size=(200000, 3))
    y = f.evaluate(x)[:, 0]
    assert y.var() == pytest.approx(13.845, rel=0.02) and y.mean() == pytest.approx(3.5, abs=0.05)


def test_registry_and_input_checks() -> None:
    assert set(function_names()) == {
        "branin",
        "rosenbrock",
        "rosenbrock_disk",
        "zdt1",
        "zdt2",
        "dtlz2",
        "schaffer1",
        "binh_korn",
        "ishigami",
    }
    assert function("ZDT1", n_var=5).n_var == 5 and function("dtlz2", n_obj=2, k=4).n_var == 5
    with pytest.raises(InvalidInput, match="unknown test function"):
        function("sphere")
    with pytest.raises(InvalidInput, match="columns"):
        branin().evaluate(np.zeros((3, 3)))
    for name in function_names():
        f = function(name)
        responses = f.evaluate(f.problem().space.from_unit(np.full((2, f.n_var), 0.5)))
        assert responses.shape == (2, len(f.response_names)) and np.isfinite(responses).all()
