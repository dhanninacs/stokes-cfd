"""The flow-split objective and the finite-difference loop (ADR-0040), without CFD: J and its
fractions, forward differences against a synthetic solve, the stop at the target spread, the
refusal of an unbalanced or back-flowing cycle, and coplanar outlets found again by their centres.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from stokes.api import Session
from stokes.explore.errors import InvalidInput
from stokes.explore.optimise.gradient import optimise
from stokes.explore.shape import (
    CycleResult,
    FiniteDifferenceObjective,
    Variable,
    as_cycle_result,
    flow_split,
    flow_split_derivative,
)

MM = 0.001


def test_flow_split_is_the_squared_spread_of_the_fractions() -> None:
    even = flow_split({"inlet": -4.0, "a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0})
    assert even.objective == pytest.approx(0.0) and even.deviation == pytest.approx(0.0)
    assert even.total == pytest.approx(4.0)
    skew = flow_split({"inlet": -1.0, "a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1})
    assert skew.fractions == pytest.approx({"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1})
    assert skew.objective == pytest.approx(0.15**2 + 0.05**2 + 0.05**2 + 0.15**2)
    assert skew.deviation == pytest.approx(0.15)
    # Named outlets: a port with inflow among them still counts (and makes J large).
    named = flow_split({"a": 0.6, "b": -0.1}, ["a", "b"])
    assert named.fractions["b"] == pytest.approx(-0.2)
    with pytest.raises(InvalidInput, match="no flow for"):
        flow_split({"a": 1.0}, ["a", "z"])
    with pytest.raises(InvalidInput):
        flow_split({"inlet": -1.0})


def test_an_older_cycle_tuple_is_still_read() -> None:
    one = np.zeros((1, 3))
    found = as_cycle_result((2.0, one, one, np.ones(1), np.ones(1)))
    assert found.objective == 2.0 and found.flows == {} and found.outlets == ()


def _project(tmp_path: Path) -> Any:  # Any: Project
    session = Session()
    session.execute("project.new", name="split", path=str(tmp_path / "split"))
    for name in ("h2", "h3"):
        session.execute("parameter.define", name=name, dimension="length", value="16 [mm]")
    return session.require_project()


def _flows(project: Any) -> CycleResult:  # Any: Project
    """A manifold that is no manifold: each outlet's flow grows with one height."""

    def value(name: str) -> float:
        node, param = project.resolve_param(name)
        return float(project.value_of(node.id, param).to_base_units().magnitude)

    h2, h3 = value("h2"), value("h3")
    a, b = h2 / MM, h3 / MM
    flows = {"inlet": -(a + b + 20.0), "o1": a, "o2": b, "o3": 10.0, "o4": 10.0}
    return CycleResult(1e-6, flows=flows, outlets=("o1", "o2", "o3", "o4"), detail={"cells": 7})


def test_finite_differences_give_the_gradient_of_the_split(tmp_path: Path) -> None:
    project = _project(tmp_path)
    variables = [Variable("h2", 5 * MM, 16 * MM), Variable("h3", 5 * MM, 16 * MM)]
    probes: list[dict[str, Any]] = []
    evaluate = FiniteDifferenceObjective(
        project, variables, _flows, steps=[1e-6, 1e-6], on_probe=probes.append
    )
    x = np.array([12 * MM, 8 * MM])
    found = evaluate(x)
    assert found.source == "finite_difference"
    assert found.detail["solves"] == 3 and len(probes) == 3
    assert found.detail["cells"] == 7 and set(found.detail["fractions"]) == {"o1", "o2", "o3", "o4"}

    def j(a: float, b: float) -> float:
        return flow_split({"o1": a, "o2": b, "o3": 10.0, "o4": 10.0}).objective

    h = 1e-4
    exact = [
        (j(12 + h, 8) - j(12 - h, 8)) / (2 * h * MM),
        (j(12, 8 + h) - j(12, 8 - h)) / (2 * h * MM),
    ]
    assert found.objective == pytest.approx(j(12, 8))
    assert found.gradient == pytest.approx(exact, rel=1e-3)
    # At the upper bound the step goes backwards, so no solve leaves the bounds.
    evaluate(np.array([16 * MM, 8 * MM]))
    assert probes[-2]["moved"] == "h2" and probes[-2]["step"] < 0
    with pytest.raises(InvalidInput, match="inside the bounds"):
        FiniteDifferenceObjective(project, variables, _flows, steps=[0.0, 1e-3])


def test_the_loop_stops_when_the_split_is_within_the_target(tmp_path: Path) -> None:
    project = _project(tmp_path)
    variables = [Variable("h2", 5 * MM, 16 * MM), Variable("h3", 5 * MM, 16 * MM)]
    evaluate = FiniteDifferenceObjective(project, variables, _flows, steps=[0.05 * MM, 0.05 * MM])
    result = optimise(
        evaluate,
        [16 * MM, 6 * MM],
        [5 * MM, 5 * MM],
        [16 * MM, 16 * MM],
        max_cycles=30,
        stop_when=lambda cycle: cycle.detail["deviation"] <= 0.01,
    )
    assert result.status == "target_reached"
    best = result.cycles[-1]
    assert best.detail["deviation"] <= 0.01
    assert best.x == pytest.approx([10 * MM, 10 * MM], abs=0.5 * MM)
    assert all(c.source == "finite_difference" for c in result.cycles)


def test_a_cycle_that_does_not_balance_or_takes_flow_back_is_refused() -> None:
    from stokes.api.shape_cycle import CycleFailed, check_flows

    check_flows({"in": -1.0, "a": 0.5, "b": 0.499}, {}, ["a", "b"])  # 0.1 %: accepted
    with pytest.raises(CycleFailed, match="do not balance"):
        check_flows({"in": -1.0, "a": 0.5, "b": 0.49}, {}, ["a", "b"])
    with pytest.raises(CycleFailed, match="back in"):
        check_flows({"in": -1.0, "a": 0.5, "b": 0.5}, {"b": 0.02}, ["a", "b"])
    with pytest.raises(CycleFailed, match="back in"):
        check_flows({"in": -1.0, "a": 1.1, "b": -0.1}, {}, ["a", "b"])


def _answer(**more: Any) -> dict[str, Any]:  # Any: job.sensitivity's JSON
    flow = {"backflow": 0.0, "pressure": None}
    return {
        "objective": 2.5,
        "flows": {
            "in": {"flow": -1.0, **flow},
            "a": {"flow": 0.5, **flow},
            "b": {"flow": 0.5, **flow},
        },
        "centres": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
        "normals": [0.0, 1.0, 0.0, 0.0, -1.0, 0.0],
        "areas": [1.0, 2.0],
        "values": [-3.0, 4.0],
        **more,
    }


def test_a_cycle_result_says_where_its_sensitivity_came_from() -> None:
    from stokes.api.shape_cycle import CycleFailed, cycle_result

    found = cycle_result(_answer(source="solver"), ["a", "b"], {"cycle": 3, "cells": 10})
    assert found.detail["sensitivity_source"] == "solver"
    assert found.detail["cycle"] == 3 and found.detail["mass_balance"] == 0.0
    assert found.centres.shape == (2, 3) and list(found.values) == [-3.0, 4.0]
    assert found.outlets == ("a", "b") and found.flows["a"] == 0.5
    # An agent from before ADR-0041 sends no source: it only reconstructed.
    older = cycle_result(_answer(), ["a", "b"], {})
    assert older.detail["sensitivity_source"] == "reconstructed"
    # A finite-difference cycle has no wall sensitivity to label.
    primal = cycle_result(_answer(source=None), ["a", "b"], {}, adjoint=False)
    assert "sensitivity_source" not in primal.detail
    with pytest.raises(CycleFailed, match="unknown sensitivity source"):
        cycle_result(_answer(source="guessed"), ["a", "b"], {})


# ---------------------------------------------------------------------------- retag
def _square(cx: float, y: float, size: float = 0.006) -> tuple[list[list[float]], list[list[int]]]:
    """A square in the plane y = ``y`` facing -y, centred on x = ``cx``."""
    h = size / 2
    points = [[cx - h, y, -h], [cx + h, y, -h], [cx + h, y, h], [cx - h, y, h]]
    return points, [[0, 1, 2], [0, 2, 3]]


def _mesh(squares: list[tuple[float, float]], wall: bool = True) -> Any:  # Any: a PartMesh stand-in
    positions: list[list[float]] = []
    indices: list[list[int]] = []
    ids: list[int] = []
    for face, (cx, y) in enumerate(squares):
        points, tris = _square(cx, y)
        base = len(positions)
        positions += points
        indices += [[base + i for i in t] for t in tris]
        ids += [face, face]
    if wall:  # a bent face elsewhere (facing +x and +z): a wall, two faces in no one plane
        base = len(positions)
        positions += [[0.2, 0.0, 0.0], [0.2, 0.01, 0.0], [0.2, 0.01, 0.01]]
        positions += [[0.3, 0.0, 0.0], [0.31, 0.0, 0.0], [0.31, 0.01, 0.0]]
        indices += [[base, base + 1, base + 2], [base + 3, base + 4, base + 5]]
        ids += [len(squares), len(squares) + 1]
    solid = SimpleNamespace(
        positions=np.asarray(positions, dtype=np.float32),
        indices=np.asarray(indices, dtype=np.uint32),
        face_ids=np.asarray(ids, dtype=np.uint32),
    )
    return SimpleNamespace(solids=(solid,))


def test_coplanar_outlets_keep_their_own_caps() -> None:
    from stokes.geometry.retag import apply_rules, rules_for

    caps = [(0.03, -0.03), (0.06, -0.03), (0.09, -0.03), (0.12, -0.03)]
    live = _mesh(caps)
    tags = [(f"out{i + 1}", [i]) for i in range(4)] + [("walls", [4, 5])]
    rules = rules_for(live, tags)
    assert [r.kind for r in rules] == ["plane"] * 4 + ["rest"]
    assert rules[1].centre == pytest.approx((0.06, -0.03, 0.0), abs=1e-6)
    # The rebuilt part numbers its faces afresh and one cap has moved 2 mm along the plane.
    moved = _mesh([caps[3], caps[2], (0.062, -0.03), caps[0]])
    found = apply_rules(moved, rules)
    assert found == {"out1": [3], "out2": [2], "out3": [1], "out4": [0], "walls": [4, 5]}
    # A cap that left the plane is refused, as an inlet that is gone would be.
    with pytest.raises(Exception, match="gone from the rebuilt part"):
        apply_rules(_mesh([caps[0], caps[1], caps[2], (0.12, -0.02)]), rules)


# ---------------------------------------------------------------------------- the gate
def _cycle(x: list[float], objective: float, gradient: list[float]) -> Any:  # Any: a Cycle
    from stokes.explore.optimise.gradient import Cycle

    return Cycle(0, tuple(x), objective, tuple(gradient), "adjoint", 1.0, {})


def test_the_secant_catches_a_wrong_sign_gradient() -> None:
    from stokes.api.shape_state import secant_disagrees

    first = _cycle([1.0], 1.0, [1.0])  # the gradient says: lower x lowers J
    assert secant_disagrees(first, _cycle([0.5], 1.2, [1.0]), 0.01)  # J rose
    assert not secant_disagrees(first, _cycle([0.5], 0.8, [1.0]), 0.01)  # J fell, as predicted
    assert not secant_disagrees(first, _cycle([0.5], 1.005, [1.0]), 0.01)  # under the noise


def test_a_flipped_gradient_stops_at_the_second_cycle() -> None:
    from stokes.api.shape_state import secant_disagrees
    from stokes.explore.optimise.gradient import Evaluation

    def wrong(x: Any) -> Evaluation:  # Any: array
        return Evaluation(float((x[0] - 2.0) ** 2), np.array([-2 * (x[0] - 2.0)]), "adjoint")

    result = optimise(
        wrong, [1.0], [0.0], [4.0], max_cycles=20, secant=lambda a, b: secant_disagrees(a, b, 0.01)
    )
    assert result.status == "gradient_disagrees" and len(result.cycles) == 2


def test_the_gate_needs_consistent_differences_and_agreeing_ratios() -> None:
    from stokes.api.shape_state import consistency, gate

    def row(plus: float, minus: float, adjoint: float | None, base: float = 1.0) -> dict[str, Any]:
        found: dict[str, Any] = {"step": 0.1, "plus": plus, "minus": minus}
        found["finite_difference"] = (plus - minus) / 0.2
        if adjoint is not None:
            found["adjoint"] = adjoint
            found["ratio"] = adjoint / found["finite_difference"]
        return {**found, **consistency(found, base, 0.01)}

    good = row(1.1, 0.9, 1.05)
    assert good["consistent"] and gate([good]) == {
        "cosine": pytest.approx(1.0),
        "verdict": "passed",
        "passed": True,
    }
    assert gate([row(1.1, 0.9, -1.0)])["wrong_sign"] is True
    assert gate([row(1.1, 0.9, 2.0)])["verdict"] == "failed"
    curved = row(1.2, 0.99, 1.05)  # forward 2.0, backward 0.1: curvature or noise
    assert not curved["consistent"] and gate([curved])["verdict"] == "inconclusive"
    assert curved["cause"] == "curvature" and curved["suggested_step"] == pytest.approx(0.05)
    weak = row(1.01, 0.995, 0.075)  # |J+ - J-| = 0.015 < 5 floors of 0.01
    assert weak["cause"] == "noise" and weak["suggested_step"] == pytest.approx(0.2)
    assert gate([weak]) == {"cosine": pytest.approx(1.0), "verdict": "inconclusive", "passed": None}
    assert gate([row(1.1, 0.9, None)]) == {"verdict": "no_adjoint", "passed": None}


def test_improvement_below_the_noise_floor_is_not_reported_as_one() -> None:
    from stokes.api.shape_state import improvement

    rows = [{"objective": 1.0}, {"objective": 0.995}]
    assert improvement(rows, 0.01)["significant"] is False
    assert improvement([*rows, {"objective": 0.9}], 0.01)["significant"] is True


def test_shape_state_is_per_session_and_records_the_verdict(tmp_path: Path) -> None:
    from stokes.api.shape_state import adjoint_verdict, state_of
    from stokes.errors import ValidationError

    one, two = Session(), Session()
    state_of(one).begin("n_same")
    state_of(two).begin("n_same")  # a copy of the project in another session: not blocked
    with pytest.raises(ValidationError, match="already"):
        state_of(one).begin("n_same")
    state_of(one).end("n_same")
    state = state_of(one)
    assert adjoint_verdict(state, "p", ["R"]) == "unchecked"
    state.record_check(
        "p", {"objective_name": "pressure_loss", "parameters": ["R"], "verdict": "failed"}
    )
    assert adjoint_verdict(state, "p", ["R"]) == "failed"
    assert adjoint_verdict(state, "p", ["R", "W"]) == "unchecked"
    state.record_check(
        "p", {"objective_name": "pressure_loss", "parameters": ["R"], "verdict": "passed"}
    )
    assert adjoint_verdict(state, "p", ["R"]) == "verified"


# ------------------------------------------------------------------ ADR-0041: dJ/dQ_i
_FLOWS = st.lists(st.floats(0.05, 10.0), min_size=2, max_size=6)


@settings(max_examples=60, deadline=None)
@given(_FLOWS)
def test_the_split_derivative_matches_numerical_differences(values: list[float]) -> None:
    """c_i, the outlet source of the open-source adjoint, is dJ/dQ_i of `flow_split`."""
    flows = {f"o{i}": q for i, q in enumerate(values)}
    c = flow_split_derivative(flows)
    for name, q in flows.items():
        h = 1e-6 * q
        up = flow_split({**flows, name: q + h}).objective
        down = flow_split({**flows, name: q - h}).objective
        assert c[name] == pytest.approx((up - down) / (2 * h), rel=1e-4, abs=1e-9)


@settings(max_examples=60, deadline=None)
@given(_FLOWS)
def test_the_split_derivative_satisfies_euler(values: list[float]) -> None:
    """J is homogeneous of degree zero in the flows, so sum_i c_i Q_i = 0 (self-check 1)."""
    flows = {f"o{i}": q for i, q in enumerate(values)}
    c = flow_split_derivative(flows)
    total = sum(flows.values())
    assert (
        abs(sum(c[n] * q for n, q in flows.items()))
        <= 1e-10 * max(map(abs, c.values()), default=1.0) * total + 1e-15
    )


@settings(max_examples=40, deadline=None)
@given(_FLOWS, st.floats(-5.0, 5.0))
def test_a_constant_added_to_every_source_leaves_the_flux_term_unchanged(
    values: list[float], shift: float
) -> None:
    """Gauge (self-check 2): with the inlet flux fixed, sum_i dQ_i = 0, so c_i + k predicts the same
    change of J as c_i for any admissible perturbation."""
    flows = {f"o{i}": q for i, q in enumerate(values)}
    c = flow_split_derivative(flows)
    names = list(flows)
    dq = {n: (1.0 if i == 0 else -1.0 / (len(names) - 1)) for i, n in enumerate(names)}
    plain = sum(c[n] * dq[n] for n in names)
    shifted = sum((c[n] + shift) * dq[n] for n in names)
    assert shifted == pytest.approx(plain, abs=1e-9 * (1 + abs(shift)))


def test_the_derivative_has_the_physical_sign() -> None:
    """An over-fed outlet has c_i > 0 (more flow there raises J), an under-fed one c_i < 0."""
    c = flow_split_derivative({"inlet": -1.0, "a": 0.6, "b": 0.4}, ["a", "b"])
    assert c["a"] > 0 > c["b"]
    assert flow_split_derivative({"a": 1.0, "b": 1.0}) == pytest.approx({"a": 0.0, "b": 0.0})


def test_the_verdict_is_kept_per_objective_and_outlet_set() -> None:
    """ADR-0040 decision 6 with ADR-0041: a passing flow-split check verifies the flow-split adjoint
    of the same outlets only; a check without an adjoint verifies nothing."""
    from stokes.api.shape_state import adjoint_verdict, state_of

    state = state_of(Session())
    passed = {
        "objective_name": "flow_split",
        "source": "adjoint",
        "parameters": ["R"],
        "outlets": ["a", "b"],
        "verdict": "passed",
    }
    state.record_check("p", passed)
    assert adjoint_verdict(state, "p", ["R"], "flow_split", ["b", "a"]) == "verified"
    assert adjoint_verdict(state, "p", ["R"], "flow_split", ["a", "b", "c"]) == "unchecked"
    assert adjoint_verdict(state, "p", ["R"], "pressure_loss") == "unchecked"
    state.record_check("p", {**passed, "source": "finite_difference", "verdict": "no_adjoint"})
    assert adjoint_verdict(state, "p", ["R"], "flow_split", ["a", "b"]) == "unchecked"
    state.record_check("p", {**passed, "verdict": "failed"})
    assert adjoint_verdict(state, "p", ["R"], "flow_split", ["a", "b"]) == "failed"
