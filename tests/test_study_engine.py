"""The function engine and the study definition it evaluates (ADR-0016 points 3 and 8):
overrides on a private copy of the base case, derived parameters evaluated afresh, unit checks,
per-point failures with their reason, cancel, constraint values and reference resolution."""

from __future__ import annotations

import json
import math

import pytest

from stokes.api import Session
from stokes.explore.definition import (
    StudyDefinition,
    StudyDefinitionError,
    find_study,
    read_definition,
    resolve_target,
)
from stokes.explore.engine_function import FunctionEngine, constraint_values
from stokes.explore.evaluate import CancelToken, PointRequest, PointResult
from tests.test_study_support import AREA, PARAMS, SWEEP, define_pipe, dp, make_sweep


@pytest.fixture
def pipe() -> Session:
    session = Session()
    session.execute("project.new", name="demo")
    define_pipe(session)
    make_sweep(session)
    return session


def definition(session: Session, path: str = SWEEP) -> StudyDefinition:
    project = session.require_project()
    return read_definition(project, find_study(project, path))


def response(session: Session, name: str, expression: str, dimension: str) -> None:
    """A response added as a plain node, so nothing checks it against the base case."""
    session.execute(
        "node.add",
        parent=f"{SWEEP}/responses",
        type="response",
        name=name,
        params={"expression": expression, "dimension": dimension},
    )


def evaluate(session: Session, *points: dict[str, float]) -> list[PointResult]:
    requests = [PointRequest(i, p) for i, p in enumerate(points)]
    return FunctionEngine().evaluate(definition(session), requests)


def test_derived_parameters_follow_the_overrides(pipe: Session) -> None:
    results = evaluate(pipe, {"U_in": 1.0}, {"U_in": 3.0})
    assert [r.status for r in results] == ["done", "done"]
    assert results[0].outputs == pytest.approx({"dp_dyn": dp(1.0), "Q": AREA})
    assert results[1].outputs == pytest.approx({"dp_dyn": dp(3.0), "Q": 3 * AREA})
    assert results[0].run is None and FunctionEngine().name == "function"


def test_chains_of_derived_parameters_are_evaluated_afresh(pipe: Session) -> None:
    pipe.execute("parameter.define", name="dp2", dimension="pressure", value="dp_dyn * 2")
    response(pipe, "dp2", "dp2 + 0 [Pa]", "pressure")
    assert evaluate(pipe, {"U_in": 3.0})[0].outputs["dp2"] == pytest.approx(2 * dp(3.0))


def test_a_derived_parameter_can_itself_be_varied(pipe: Session) -> None:
    pipe.execute("study.create", name="direct")
    pipe.execute("study.add_variable", study="direct", parameter="dp_dyn", values=[100, 200])
    pipe.execute("study.add_response", study="direct", expression="dp_dyn * 2")
    pipe.execute("study.add_response", study="direct", expression="U_in")
    study = definition(pipe, "/studies/direct")
    results = FunctionEngine().evaluate(study, [PointRequest(0, {"dp_dyn": 150.0})])
    assert results[0].outputs == pytest.approx({"response": 300.0, "U_in": 2.0})


def test_the_live_project_is_never_touched(pipe: Session) -> None:
    project = pipe.require_project()
    before = json.dumps(project.to_json(), sort_keys=True)
    evaluate(pipe, {"U_in": 5.0}, {"U_in": 7.0})
    assert json.dumps(project.to_json(), sort_keys=True) == before
    assert project.get(f"{PARAMS}/U_in").params["value"].value == 2.0


def test_the_snapshot_is_a_copy(pipe: Session) -> None:
    study = definition(pipe)
    study.project["root"]["children"].clear()
    assert pipe.execute("node.get", node=f"{PARAMS}/U_in")["params"]["value"]["value"] == 2.0
    assert (
        read_definition(
            pipe.require_project(), find_study(pipe.require_project(), SWEEP), base=False
        ).project
        == {}
    )


def test_responses_are_unit_checked(pipe: Session) -> None:
    response(pipe, "speed", "dp_dyn", "velocity")
    result = evaluate(pipe, {"U_in": 1.0})[0]
    assert result.status == "failed" and result.outputs == {}
    assert result.error is not None and result.error.startswith("response speed: expected velocity")


@pytest.mark.parametrize(
    ("expression", "dimension", "reason"),
    [
        ("1 [Pa] / (U_in - 1 [m/s]) * 1 [m/s]", "pressure", "division by zero"),
        ("sqrt(U_in**2 - 4 [m**2/s**2])", "velocity", "has no real value"),
        ("exp(U_in / 1 [m/s] * 1000)", "dimensionless", "math range error"),
        ("U_in * nowhere", "velocity", "unknown name 'nowhere'"),
    ],
)
def test_failures_are_per_point_and_say_why(
    pipe: Session, expression: str, dimension: str, reason: str
) -> None:
    response(pipe, "r", expression, dimension)
    first, second = evaluate(pipe, {"U_in": 1.0}, {"U_in": 3.0})
    assert first.status == "failed" and first.error is not None and reason in first.error
    assert first.error.startswith("response r:")
    if "nowhere" not in expression and "exp" not in expression:
        assert second.status == "done"


def test_a_failing_derived_parameter_names_itself(pipe: Session) -> None:
    pipe.execute(
        "parameter.define",
        name="inv",
        dimension="dimensionless",
        value="1 [m/s] / (U_in - 1 [m/s])",
    )
    response(pipe, "inv", "inv * 2", "dimensionless")
    failed, done = evaluate(pipe, {"U_in": 1.0}, {"U_in": 3.0})
    assert failed.error == "response inv: inv: division by zero"
    assert done.outputs["inv"] == pytest.approx(1.0)


def test_a_missing_or_bad_input_fails_the_point(pipe: Session) -> None:
    missing, nan, good = evaluate(pipe, {}, {"U_in": math.nan}, {"U_in": 2.0})
    assert missing.error == "the point has no value for variable U_in"
    assert nan.status == "failed" and "no finite value" in (nan.error or "")
    assert good.status == "done"


def test_cancel_stops_between_points(pipe: Session) -> None:
    token = CancelToken()
    started: list[int] = []
    results = FunctionEngine().evaluate(
        definition(pipe),
        [PointRequest(i, {"U_in": float(i + 1)}) for i in range(3)],
        cancel=token,
        on_start=started.append,
        on_result=lambda result: token.cancel(),
    )
    assert [r.index for r in results] == [0] and started == [0] and token.cancelled


def test_constraint_values_use_outputs_first(pipe: Session) -> None:
    pipe.execute(
        "study.add_constraint", study=SWEEP, expression="dp_dyn", operator="<=", limit="3 [kPa]"
    )
    pipe.execute(
        "study.add_constraint", study=SWEEP, expression="rho * U_in**2", operator=">=", limit=0
    )
    pipe.execute(
        "study.add_constraint",
        study=SWEEP,
        expression="1 [Pa] / (U_in - 3 [m/s]) * 1 [m/s]",
        operator="<=",
        limit=1,
    )
    study = definition(pipe)
    values = constraint_values(
        study,
        [
            (PointRequest(0, {"U_in": 1.0}), {"dp_dyn": 123.0, "Q": AREA}),
            (PointRequest(1, {"U_in": 2.0}), {}),
            (PointRequest(2, {"U_in": 3.0}), {}),
        ],
    )
    first, second = values[0], values[1]
    assert isinstance(first, list) and first[:2] == pytest.approx([123.0, 998.0])
    assert isinstance(second, list) and second[0] == pytest.approx(dp(2.0))  # the parameter
    assert isinstance(values[2], str) and "division by zero" in values[2]


def test_resolve_target_accepts_names_paths_and_node_quantities(pipe: Session) -> None:
    project = pipe.require_project()
    u_in = project.get(f"{PARAMS}/U_in")
    for ref in ("U_in", f"{PARAMS}/U_in", "physics.parameters.U_in", u_in.id):
        node, param = resolve_target(project, ref)
        assert (node, param) == (u_in, "value"), ref
    variable = project.get(f"{SWEEP}/variables/U_in")
    assert resolve_target(project, "studies.sweep.variables.U_in.min") == (variable, "min")
    assert resolve_target(project, f"{SWEEP}/variables/U_in.max") == (variable, "max")
    for bad in ("nowhere", SWEEP, f"{SWEEP}/variables/U_in.parameter", ""):
        with pytest.raises(StudyDefinitionError):
            resolve_target(project, bad)


def test_read_definition_reports_what_to_fix(pipe: Session) -> None:
    pipe.execute(
        "parameter.set", node=f"{SWEEP}/variables/U_in", name="dimension", value="pressure"
    )
    with pytest.raises(StudyDefinitionError, match="is a pressure but U_in is a velocity"):
        definition(pipe)
    pipe.execute("history.undo")
    response(pipe, "U_in", "U_in", "velocity")  # same name as the variable
    with pytest.raises(StudyDefinitionError, match="distinct names"):
        definition(pipe)
    pipe.execute("history.undo")
    response(pipe, "empty", "", "dimensionless")
    with pytest.raises(StudyDefinitionError, match="has no expression"):
        definition(pipe)
    pipe.execute("history.undo")
    pipe.execute(
        "node.add",
        parent=f"{SWEEP}/variables",
        type="variable",
        name="again",
        params={"parameter": "physics.parameters.U_in", "dimension": "velocity", "values": [1]},
    )
    with pytest.raises(StudyDefinitionError, match="two variables vary"):
        definition(pipe)
