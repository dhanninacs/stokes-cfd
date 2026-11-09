"""study.* through Session.execute (ADR-0016 points 1, 2, 6, 8, 9): definitions and their undo,
sweep and DOE generation, the function engine's streamed update, cancel, hand-added and cleared
points, results, the Pareto front, export and journal replay."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Journal, Session, default_registry, replay
from stokes.errors import (
    ExpressionError,
    ModelError,
    ReferencedNode,
    UnitMismatch,
    UnknownNode,
    ValidationError,
)
from stokes.explore.definition import StudyDefinitionError
from stokes.explore.study_store import StudyBusy, StudyStore
from stokes.model import SCHEMA_VERSION
from tests.test_study_support import AREA, PARAMS, SWEEP, define_pipe, dp, make_sweep, recorder

COMMANDS = {
    "study.create": True,
    "study.add_variable": True,
    "study.add_response": True,
    "study.set_objective": True,
    "study.add_constraint": True,
    "study.generate": True,
    "study.add_point": True,
    "study.update": True,
    "study.cancel": False,
    "study.results": False,
    "study.pareto": False,
    "study.clear": True,
    "study.export": False,
}


@pytest.fixture
def pipe(session: Session) -> Session:
    define_pipe(session)
    return session


def rows(session: Session, study: str = SWEEP) -> list[dict[str, Any]]:
    return list(session.execute("study.results", study=study)["points"])


def test_commands_are_registered_with_their_schemas() -> None:
    schema = {c["name"]: c for c in default_registry().json_schema()}
    assert {name: schema[name]["mutates"] for name in COMMANDS} == COMMANDS
    assert all(schema[name]["requires_project"] for name in COMMANDS)
    assert all(schema[name]["description"] for name in COMMANDS)
    assert schema["study.create"]["input_schema"]["required"] == ["name"]
    assert schema["study.add_variable"]["input_schema"]["required"] == ["study", "parameter"]
    assert schema["study.add_constraint"]["input_schema"]["required"] == [
        "study",
        "expression",
        "operator",
        "limit",
    ]
    assert schema["study.update"]["input_schema"]["properties"]["points"]["default"] == "all"


# ---------------------------------------------------------------- definitions


def test_create_is_one_undo_step(pipe: Session) -> None:
    seen = recorder(pipe, "node.added")
    created = pipe.execute("study.create", name="sweep", description="velocities")
    assert created["path"] == SWEEP and created["params"]["status"] == "idle"
    assert list(created["groups"]) == ["variables", "responses", "objectives", "constraints"]
    assert [d["path"] for _, d in seen] == [
        SWEEP,
        f"{SWEEP}/variables",
        f"{SWEEP}/responses",
        f"{SWEEP}/objectives",
        f"{SWEEP}/constraints",
    ]
    assert pipe.execute("history.state")["undo"] == "create study sweep"
    pipe.execute("history.undo")
    with pytest.raises(UnknownNode):
        pipe.execute("node.get", node=SWEEP)
    pipe.execute("history.redo")
    tree = pipe.execute("node.tree", root=SWEEP)
    assert tree["id"] == created["id"]
    assert {c["name"]: c["id"] for c in tree["children"]} == created["groups"]


def test_create_validates_its_input(pipe: Session) -> None:
    with pytest.raises(ValidationError):
        pipe.execute("study.create", name="s", type="optimisation")
    with pytest.raises(ValidationError):
        pipe.execute("study.create", name="s", type="doe", sampler="lhs")
    with pytest.raises(ValidationError):
        pipe.execute("study.create", name="bad name")
    pipe.execute("study.create", name="s", type="doe", seed=4, count=12)
    node = pipe.execute("node.get", node="/studies/s")
    assert node["params"]["sampler"] == "latin_hypercube" and node["params"]["count"] == 12
    with pytest.raises(ModelError):
        pipe.execute("study.create", name="s")


def test_create_adds_studies_to_a_project_saved_without_it(tmp_path: Path) -> None:
    old = Session()
    old.execute("project.new", name="old", path=str(tmp_path / "old"))
    folder = tmp_path / "old.stokes"
    data = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    data["root"]["children"] = [c for c in data["root"]["children"] if c["name"] != "studies"]
    (folder / "project.json").write_text(json.dumps(data), encoding="utf-8")
    session = Session()
    session.execute("project.open", path=str(folder))
    with pytest.raises(UnknownNode):
        session.execute("node.get", node="/studies")
    seen = recorder(session, "node.added")
    session.execute("study.create", name="s")
    assert seen[0][1]["path"] == "/studies"
    session.execute("history.undo")  # removes the study; the skeleton group stays
    assert session.execute("node.get", node="/studies")["structural"] is True


def test_add_variable_converts_values_and_takes_the_dimension(pipe: Session) -> None:
    pipe.execute("study.create", name="sweep")
    added = pipe.execute(
        "study.add_variable", study="sweep", parameter="U_in", values=["36 [km/h]", 2, "3 [m/s]"]
    )
    assert added["path"] == f"{SWEEP}/variables/U_in"
    assert added["params"]["dimension"] == "velocity"
    assert added["params"]["values"] == pytest.approx([10.0, 2.0, 3.0])
    assert added["params"]["min"] == {"value": 0.0, "unit": "m/s"}
    assert pipe.execute("history.state")["undo"] == "add variable U_in"
    path_form = pipe.execute(
        "study.add_variable",
        study=SWEEP,
        parameter="/physics/parameters/D",
        domain="range",
        min="40 [mm]",
        max="D * 1.5",
        count=3,
    )
    assert path_form["name"] == "D" and path_form["params"]["max"]["expr"] == "D * 1.5"
    assert path_form["params"]["max"]["value"] == pytest.approx(0.09)


def test_a_dimension_change_through_the_api_undoes_redoes_and_replays(
    pipe: Session, tmp_path: Path
) -> None:
    pipe.execute("study.create", name="sweep")
    pipe.execute(
        "study.add_variable",
        study="sweep",
        parameter="U_in",
        domain="range",
        min="1 [m/s]",
        max="U_in * 2",
        count=3,
    )
    path = f"{SWEEP}/variables/U_in"

    def params() -> dict[str, Any]:
        found: dict[str, Any] = pipe.execute("node.get", node=path)["params"]
        return found

    before = params()
    assert before["min"] == {"value": 1.0, "unit": "m/s"}
    assert before["max"]["expr"] == "U_in * 2"
    pipe.execute("parameter.set", node=path, name="dimension", value="length")
    after = params()
    assert after["min"] == {"value": 0.0, "unit": "m"} and after["max"] == {
        "value": 0.0,
        "unit": "m",
    }
    pipe.execute("history.undo")
    assert params() == before  # the bounds and the expression are back
    pipe.execute("history.redo")
    assert params() == after
    records = Journal.read(tmp_path / "journal.jsonl")
    assert [r["command"] for r in records[-3:]] == ["parameter.set", "history.undo", "history.redo"]
    assert records[-3]["params"] == {"node": path, "name": "dimension", "value": "length"}
    fresh = Session()
    fresh.execute("project.new", name="other")
    replay(fresh, records[1:])  # the undo and the redo replay too, and end in the same place
    assert fresh.execute("node.get", node=path)["params"] == after


def test_add_variable_refuses_bad_definitions(pipe: Session) -> None:
    pipe.execute("study.create", name="sweep")
    cases: list[dict[str, Any]] = [
        {"parameter": "nowhere", "values": [1]},
        {"parameter": "U_in"},  # values domain without values
        {"parameter": "U_in", "values": [1, 1]},
        {"parameter": "U_in", "domain": "range", "min": 3, "max": 1},
        {"parameter": "U_in", "domain": "range", "min": 1, "max": 3, "count": 3, "step": 1},
        {"parameter": "U_in", "domain": "range", "min": 1, "max": 3, "count": 1},
        {"parameter": "U_in", "domain": "normal", "mean": 2, "std": 0},
        {"parameter": "U_in", "domain": "categorical", "values": [1, 2], "labels": ["a"]},
        {"parameter": "U_in", "domain": "triangular", "min": 1, "max": 2},
    ]
    for case in cases:
        with pytest.raises(StudyDefinitionError) as caught:
            pipe.execute("study.add_variable", study="sweep", **case)
        assert caught.value.code == "explore.study_definition", case
    with pytest.raises(UnitMismatch):
        pipe.execute("study.add_variable", study="sweep", parameter="U_in", values=["1 [Pa]"])
    with pytest.raises(ValidationError):
        pipe.execute(
            "study.add_variable",
            study="sweep",
            parameter="U_in",
            domain="categorical",
            values=[1, 2],
            labels=["a,b", "c"],
        )
    pipe.execute("study.add_variable", study="sweep", parameter="U_in", values=[1, 2])
    with pytest.raises(StudyDefinitionError, match="already varied"):
        pipe.execute(
            "study.add_variable", study="sweep", parameter="physics.parameters.U_in", values=[3]
        )
    assert pipe.execute("history.state")["undo"] == "add variable U_in"


def test_add_response_infers_dimension_and_name(pipe: Session) -> None:
    pipe.execute("study.create", name="sweep")
    pipe.execute("study.add_variable", study="sweep", parameter="U_in", values=[1, 2])
    plain = pipe.execute("study.add_response", study="sweep", expression="dp_dyn")
    assert (plain["name"], plain["params"]["dimension"]) == ("dp_dyn", "pressure")
    labelled = pipe.execute(
        "study.add_response",
        study="sweep",
        expression="0.5 * rho * U_in**2 / 1000",
        label="Dynamic pressure",
    )
    assert labelled["name"] == "Dynamic_pressure"
    assert (
        pipe.execute("study.add_response", study="sweep", expression="2 * Q")["name"] == "response"
    )
    again = pipe.execute("study.add_response", study="sweep", expression="U_in")
    assert again["name"] == "U_in_2"  # the variable has the name U_in
    with pytest.raises(UnitMismatch):
        pipe.execute("study.add_response", study="sweep", expression="Q", dimension="pressure")
    with pytest.raises(ExpressionError):
        pipe.execute("study.add_response", study="sweep", expression="nothing * 2")
    with pytest.raises(StudyDefinitionError):
        pipe.execute("study.add_response", study="sweep", expression="Q", name="dp_dyn")


def test_set_objective_adds_then_changes_direction(pipe: Session) -> None:
    path = make_sweep(pipe)
    added = pipe.execute("study.set_objective", study=path, response="dp_dyn")
    response_id = pipe.execute("node.get", node=f"{path}/responses/dp_dyn")["id"]
    assert added["params"] == {"response": response_id, "direction": "minimise"}
    changed = pipe.execute("study.set_objective", study=path, response=response_id, direction="max")
    assert changed["id"] == added["id"] and changed["params"]["direction"] == "maximise"
    pipe.execute("history.undo")
    assert pipe.execute("node.get", node=added["id"])["params"]["direction"] == "minimise"
    with pytest.raises(StudyDefinitionError):
        pipe.execute("study.set_objective", study=path, response="U_in")  # a variable
    with pytest.raises(ValidationError):
        pipe.execute("study.set_objective", study=path, response="Q", direction="up")


def test_add_constraint_infers_the_dimension(pipe: Session) -> None:
    path = make_sweep(pipe)
    added = pipe.execute(
        "study.add_constraint", study=path, expression="dp_dyn", operator="<=", limit="3 [kPa]"
    )
    assert added["name"] == "dp_dyn_max" and added["params"]["dimension"] == "pressure"
    assert added["params"]["limit"]["value"] == pytest.approx(3000.0)
    ratio = pipe.execute(
        "study.add_constraint",
        study=path,
        expression="dp_dyn / (rho * U_in**2)",
        operator="==",
        limit=0.5,
    )
    assert (ratio["name"], ratio["params"]["dimension"]) == ("constraint", "dimensionless")
    with pytest.raises(ValidationError):
        pipe.execute("study.add_constraint", study=path, expression="Q", operator="<", limit=1)
    with pytest.raises(UnitMismatch):
        pipe.execute(
            "study.add_constraint", study=path, expression="Q", operator=">=", limit="1 [Pa]"
        )


# ---------------------------------------------------------------- generation


def test_generate_a_sweep_is_the_full_factorial(pipe: Session) -> None:
    path = make_sweep(pipe)
    pipe.execute("study.add_variable", study=path, parameter="D", values=["60 [mm]", "40 [mm]"])
    seen = recorder(pipe, "study.generated")
    generated = pipe.execute("study.generate", study=path)
    assert generated["points"] == 6 and generated["status"] == "generated"
    assert generated["sampler"] == "full_factorial" and generated["seed"] is None
    assert seen == [("study.generated", {"study": generated["study"], "path": path, "points": 6})]
    inputs = [(p["inputs"]["U_in"], p["inputs"]["D"]) for p in rows(pipe)]
    wanted = [(1, 0.04), (1, 0.06), (2, 0.04), (2, 0.06), (3, 0.04), (3, 0.06)]
    assert len(inputs) == len(wanted)
    # The first variable varies slowest; a value list is sorted. No nested `approx`.
    for got, expect in zip(inputs, wanted, strict=True):
        assert got == pytest.approx(expect)
    assert {p["status"] for p in rows(pipe)} == {"pending"}
    assert pipe.execute("node.get", node=path)["params"]["status"] == "generated"
    pipe.execute("history.undo")  # reverts the last definition, not the table
    assert len(rows(pipe)) == 6


def test_generate_a_doe_is_seeded_and_deterministic(pipe: Session) -> None:
    pipe.execute("study.create", name="doe", type="doe", sampler="latin_hypercube", count=8, seed=7)
    pipe.execute(
        "study.add_variable", study="doe", parameter="U_in", domain="uniform", min=1, max=3
    )
    pipe.execute(
        "study.add_variable",
        study="doe",
        parameter="D",
        domain="normal",
        mean="60 [mm]",
        std="2 [mm]",
    )
    first = pipe.execute("study.generate", study="doe")
    assert (first["sampler"], first["seed"], first["points"]) == ("latin_hypercube", 7, 8)
    points = [p["inputs"] for p in rows(pipe, "doe")]
    assert all(1.0 <= p["U_in"] <= 3.0 for p in points)
    assert pipe.execute("study.generate", study="doe") == first
    assert [p["inputs"] for p in rows(pipe, "doe")] == points
    pipe.execute("parameter.set", node="/studies/doe", name="seed", value=8)
    pipe.execute("study.generate", study="doe")
    assert [p["inputs"] for p in rows(pipe, "doe")] != points
    stored = pipe.execute("study.results", study="doe")
    assert stored["design"]["sampler"] == "latin_hypercube" and stored["seed"] == 8


@pytest.mark.parametrize(
    ("sampler", "count", "expected"),
    [
        ("full_factorial", 0, 8),
        ("sobol", 8, 8),
        ("halton", 5, 5),
        ("optimal_space_filling", 6, 6),
        ("monte_carlo", 4, 4),
        ("central_composite", 0, 15),
        ("box_behnken", 0, 13),
    ],
)
def test_generate_with_every_sampler(
    pipe: Session, sampler: str, count: int, expected: int
) -> None:
    pipe.execute("study.create", name="doe", type="doe", sampler=sampler, count=count, seed=1)
    for name, low, high in (("U_in", 1, 3), ("rho", 990, 1000), ("D", 0.04, 0.08)):
        pipe.execute(
            "study.add_variable",
            study="doe",
            parameter=name,
            domain="uniform",
            min=low,
            max=high,
            count=2,
        )
    assert pipe.execute("study.generate", study="doe")["points"] == expected


def test_generate_refuses_what_it_cannot_sample(pipe: Session) -> None:
    pipe.execute("study.create", name="sweep")
    with pytest.raises(StudyDefinitionError, match="no variables"):
        pipe.execute("study.generate", study="sweep")
    pipe.execute(
        "study.add_variable", study="sweep", parameter="U_in", domain="uniform", min=1, max=3
    )
    with pytest.raises(StudyDefinitionError, match="levels"):
        pipe.execute("study.generate", study="sweep")
    pipe.execute("study.create", name="doe", type="doe")
    pipe.execute(
        "study.add_variable", study="doe", parameter="U_in", domain="uniform", min=1, max=3
    )
    with pytest.raises(StudyDefinitionError, match="count"):
        pipe.execute("study.generate", study="doe")


# ---------------------------------------------------------------- update


def test_update_streams_events_in_order(pipe: Session) -> None:
    path = make_sweep(pipe)
    study_id = pipe.execute("study.generate", study=path)["study"]
    seen = recorder(pipe)
    updated = pipe.execute("study.update", study=path)
    assert {k: updated[k] for k in ("evaluated", "done", "failed", "pending", "cancelled")} == {
        "evaluated": 3,
        "done": 3,
        "failed": 0,
        "pending": 0,
        "cancelled": False,
    }
    assert updated["status"] == "done"
    order = [(e, d.get("index"), d.get("status") or d.get("param")) for e, d in seen]
    assert order == [
        ("node.changed", None, "status"),
        ("study.point", 0, "running"),
        ("study.point", 0, "done"),
        ("study.point", 1, "running"),
        ("study.point", 1, "done"),
        ("study.point", 2, "running"),
        ("study.point", 2, "done"),
        ("node.changed", None, "status"),
        ("study.updated", None, "done"),
    ]
    point = seen[2][1]
    assert point["study"] == study_id and point["error"] is None
    assert point["outputs"] == pytest.approx({"dp_dyn": dp(1.0), "Q": AREA})
    assert seen[-1][1] == {k: v for k, v in updated.items() if k != "points"}
    outputs = [p["outputs"] for p in rows(pipe)]
    assert len(outputs) == 3
    for got, u in zip(outputs, (1, 2, 3), strict=True):  # pytest.approx has no nested form
        assert got == pytest.approx({"dp_dyn": dp(u), "Q": u * AREA})
    assert pipe.execute("parameter.get", node=f"{PARAMS}/U_in", name="value")["value"] == 2.0
    assert pipe.execute("node.get", node=path)["params"]["status"] == "done"


def test_update_all_means_every_point_still_to_do(pipe: Session) -> None:
    path = make_sweep(pipe)
    pipe.execute("study.generate", study=path)
    assert [p["index"] for p in pipe.execute("study.update", study=path, points=[1])["points"]] == [
        1
    ]
    assert pipe.execute("node.get", node=path)["params"]["status"] == "generated"
    assert [p["index"] for p in pipe.execute("study.update", study=path)["points"]] == [0, 2]
    assert pipe.execute("study.update", study=path)["evaluated"] == 0
    pipe.execute("study.add_response", study=path, expression="rho * U_in**2")
    again = pipe.execute("study.update", study=path, points="all")
    assert again["evaluated"] == 3  # every point lacked the new response
    assert pipe.execute("study.update", study=path, points=[2, 0, 2])["evaluated"] == 2
    with pytest.raises(ValidationError):
        pipe.execute("study.update", study=path, points=[7])
    with pytest.raises(ValidationError):
        pipe.execute("study.update", study=path, points="some")


def test_a_failing_expression_fails_its_point_only(pipe: Session) -> None:
    path = make_sweep(pipe)
    # Fine at the base case (U_in = 2 m/s), a division by zero at the first point.
    pipe.execute("study.add_response", study=path, expression="1 [Pa] / (U_in - 1 [m/s]) * 1 [m/s]")
    pipe.execute("study.generate", study=path)
    failures = recorder(pipe, "study.point")
    updated = pipe.execute("study.update", study=path)
    assert (updated["done"], updated["failed"], updated["status"]) == (2, 1, "failed")
    failed = [d for _, d in failures if d["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["index"] == 0 and failed[0]["outputs"] == {}
    assert "division by zero" in failed[0]["error"]
    assert rows(pipe)[0]["error"].startswith("response response:")
    assert rows(pipe)[1]["outputs"]["response"] == pytest.approx(1.0)


def test_update_needs_responses_and_a_valid_definition(pipe: Session) -> None:
    pipe.execute("study.create", name="sweep")
    pipe.execute("study.add_variable", study="sweep", parameter="U_in", values=[1, 2])
    pipe.execute("study.generate", study="sweep")
    with pytest.raises(StudyDefinitionError, match="no responses"):
        pipe.execute("study.update", study="sweep")
    pipe.execute("study.add_response", study="sweep", expression="dp_dyn")
    pipe.execute("node.remove", node=f"{PARAMS}/U_in")
    with pytest.raises(StudyDefinitionError, match="names no parameter"):
        pipe.execute("study.update", study="sweep")


def test_cancel_stops_after_the_current_point(pipe: Session) -> None:
    path = make_sweep(pipe)
    pipe.execute("study.generate", study=path)
    assert pipe.execute("study.cancel") == {"cancelled": []}
    answers: list[Any] = []

    def cancel_after_first(event: str, data: dict[str, Any]) -> None:
        if data["status"] == "done" and data["index"] == 0:
            answers.append(pipe.execute("study.cancel", study=path))

    pipe.events.subscribe("study.point", cancel_after_first)
    updated = pipe.execute("study.update", study=path)
    assert answers == [{"cancelled": [updated["study"]]}]
    assert (updated["evaluated"], updated["pending"], updated["cancelled"]) == (1, 2, True)
    assert updated["status"] == "generated"
    assert [p["status"] for p in rows(pipe)] == ["done", "pending", "pending"]


def test_one_update_at_a_time(pipe: Session) -> None:
    path = make_sweep(pipe)
    pipe.execute("study.generate", study=path)
    errors: list[str] = []

    def nested(event: str, data: dict[str, Any]) -> None:
        if data["status"] == "running" and data["index"] == 0:
            for name in ("study.update", "study.generate", "study.clear"):
                try:
                    pipe.execute(name, study=path)
                except StudyBusy as exc:
                    errors.append(exc.code)

    pipe.events.subscribe("study.point", nested)
    pipe.execute("study.update", study=path)
    assert errors == ["explore.study_busy"] * 3


# ---------------------------------------------------------------- points by hand, clear


def test_add_point_and_clear(pipe: Session) -> None:
    path = make_sweep(pipe)
    pipe.execute("study.generate", study=path)
    pipe.execute("study.update", study=path)
    seen = recorder(pipe, "study.point")
    added = pipe.execute("study.add_point", study=path, inputs={"U_in": "4 [m/s]"})
    assert (added["index"], added["status"], added["inputs"]) == (3, "pending", {"U_in": 4.0})
    assert seen[-1][1]["status"] == "pending"
    base = pipe.execute("study.add_point", study=path)
    assert base["inputs"] == {"U_in": 2.0}  # the base case's value
    assert pipe.execute("node.get", node=path)["params"]["status"] == "generated"
    with pytest.raises(ValidationError):
        pipe.execute("study.add_point", study=path, inputs={"speed": 1})
    assert pipe.execute("study.update", study=path)["evaluated"] == 2
    cleared = recorder(pipe, "study.cleared")
    reset = pipe.execute("study.clear", study=path, points=[3])
    assert reset["points"] == [3] and rows(pipe)[3]["outputs"] == {}
    pipe.execute("study.clear", study=path, points=[4], remove=True)
    assert [p["index"] for p in rows(pipe)] == [0, 1, 2, 3]
    pipe.execute("study.clear", study=path)
    assert {p["status"] for p in rows(pipe)} == {"pending"}
    emptied = pipe.execute("study.clear", study=path, remove=True)
    assert emptied["points"] == [0, 1, 2, 3] and emptied["status"] == "idle"
    assert [d["removed"] for _, d in cleared] == [False, True, False, True]
    with pytest.raises(ValidationError):
        pipe.execute("study.clear", study=path, points=[9])


# ---------------------------------------------------------------- results, pareto, export


def test_results_as_json_and_csv(pipe: Session) -> None:
    path = make_sweep(pipe)
    pipe.execute("project.set_units", system="mm")
    pipe.execute("study.generate", study=path)
    pipe.execute("study.update", study=path)
    result = pipe.execute("study.results", study=path)
    assert result["status"] == "done" and result["engine"] == "function"
    assert result["counts"] == {"pending": 0, "running": 0, "done": 3, "failed": 0}
    assert result["variables"] == [
        {
            "name": "U_in",
            "dimension": "velocity",
            "unit": "m/s",
            "source": "U_in",
            "display_unit": "m/s",
        }
    ]
    assert [c["display_unit"] for c in result["responses"]] == ["kPa", "L/min"]
    assert result["points"][2]["outputs"]["dp_dyn"] == pytest.approx(dp(3.0))
    text = pipe.execute("study.results", study=path, format="csv")["text"]
    table = list(csv.reader(io.StringIO(text)))
    assert table[0] == [
        "index",
        "status",
        "U_in [m/s]",
        "dp_dyn [Pa]",
        "Q [m**3/s]",
        "retain",
        "run",
        "error",
    ]
    assert table[1][:4] == ["0", "done", "1.0", "499.0"] and float(table[3][4]) == pytest.approx(
        3 * AREA
    )
    with pytest.raises(ValidationError):
        pipe.execute("study.results", study=path, format="xlsx")
    empty = pipe.execute("study.create", name="empty")
    assert pipe.execute("study.results", study=empty["id"])["status"] == "idle"


def test_pareto_with_two_objectives_and_a_constraint(pipe: Session) -> None:
    path = make_sweep(pipe)
    pipe.execute("study.add_variable", study=path, parameter="D", values=["40 [mm]", "60 [mm]"])
    pipe.execute("study.generate", study=path)
    with pytest.raises(StudyDefinitionError, match="no objectives"):
        pipe.execute("study.pareto", study=path)
    pipe.execute("study.update", study=path, points=[0, 1, 2, 3, 4])
    pipe.execute("study.set_objective", study=path, response="dp_dyn", direction="minimise")
    pipe.execute("study.set_objective", study=path, response="Q", direction="maximise")
    front = pipe.execute("study.pareto", study=path)
    # Per velocity the wider pipe moves more fluid at the same dynamic pressure, so it dominates.
    assert front["front"] == [1, 3]
    assert front["excluded"] == [5]
    ranks = {p["index"]: p["rank"] for p in front["points"]}
    assert ranks == {0: 1, 1: 0, 2: 1, 3: 0, 4: 1}
    assert [o["direction"] for o in front["objectives"]] == ["minimise", "maximise"]
    pipe.execute("study.update", study=path)
    assert pipe.execute("study.pareto", study=path)["front"] == [1, 3, 5]
    pipe.execute(
        "study.add_constraint", study=path, expression="dp_dyn", operator="<=", limit="3 [kPa]"
    )
    constrained = pipe.execute("study.pareto", study=path)
    assert constrained["front"] == [1, 3]
    infeasible = {p["index"] for p in constrained["points"] if not p["feasible"]}
    assert infeasible == {4, 5}
    assert all(p["violation"] == 0.0 for p in constrained["points"] if p["feasible"])
    json.dumps(constrained, allow_nan=False)  # strict JSON: infinite crowding is null


def test_pareto_refuses_an_objective_whose_response_is_gone(pipe: Session) -> None:
    path = make_sweep(pipe)
    pipe.execute("study.set_objective", study=path, response="Q", direction="max")
    with pytest.raises(ReferencedNode):  # node.remove keeps the reference whole (ADR-0022 2.8)
        pipe.execute("node.remove", node=f"{path}/responses/Q")
    # A project edited by hand can still lose the response: the model removes it unguarded.
    pipe.require_project().remove(f"{path}/responses/Q")
    with pytest.raises(StudyDefinitionError, match="refer to no response"):
        pipe.execute("study.pareto", study=path)


def test_export_writes_csv_and_json(pipe: Session, tmp_path: Path) -> None:
    path = make_sweep(pipe)
    pipe.execute("study.generate", study=path)
    pipe.execute("study.update", study=path)
    written = pipe.execute("study.export", study=path, path=str(tmp_path / "table.csv"))
    assert written["format"] == "csv"
    expected = pipe.execute("study.results", study=path, format="csv")["text"]
    assert (tmp_path / "table.csv").read_text(encoding="utf-8") == expected
    pipe.execute("study.export", study=path, path=str(tmp_path / "table.json"))
    data = json.loads((tmp_path / "table.json").read_text(encoding="utf-8"))
    assert data["points"] == rows(pipe)
    for bad in ("table.txt", "folder"):
        (tmp_path / "folder").mkdir(exist_ok=True)
        with pytest.raises(ValidationError):
            pipe.execute("study.export", study=path, path=str(tmp_path / bad))
    with pytest.raises(ValidationError):
        pipe.execute("study.export", study=path, path=str(tmp_path / "t.csv"), format="json")
    with pytest.raises(ValidationError):
        pipe.execute("study.export", study=path, path=str(tmp_path / "missing" / "t.csv"))
    project_file = (pipe.project_path or tmp_path) / "project.json"
    with pytest.raises(ValidationError, match="outside the project folder"):
        pipe.execute("study.export", study=path, path=str(project_file))
    stored = json.loads(project_file.read_text(encoding="utf-8"))
    assert stored["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------- history and journals


def test_status_follows_the_table_through_undo_and_redo(pipe: Session) -> None:
    path = make_sweep(pipe)
    pipe.execute("study.generate", study=path)
    pipe.execute("study.update", study=path)
    for _ in range(4):  # the two responses, the variable, the study
        pipe.execute("history.undo")
    with pytest.raises(UnknownNode):
        pipe.execute("node.get", node=path)
    pipe.execute("history.redo")
    assert pipe.execute("node.get", node=path)["params"]["status"] == "done"
    assert len(rows(pipe)) == 3


def test_journal_replays_the_study_exactly(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    first = Session(journal_path=journal)
    first.execute("project.new", name="demo")
    define_pipe(first)
    path = make_sweep(first)
    first.execute("study.set_objective", study=path, response="dp_dyn")
    first.execute("study.generate", study=path)
    first.execute("study.update", study=path, points=[0, 2])
    first.execute("study.add_point", study=path, inputs={"U_in": 5})
    first.execute("study.update", study=path)
    first.execute("study.results", study=path)  # queries are not journaled
    records = Journal.read(journal)
    assert "study.results" not in [r["command"] for r in records]
    second = Session()
    second.execute("project.new", name="other")
    replay(second, records[1:])
    assert second.execute("node.tree", root="/studies") == first.execute(
        "node.tree", root="/studies"
    )
    assert rows(second) == rows(first)
    assert isinstance(second.extensions["study"], StudyStore)


def test_the_demo_has_an_evaluated_velocity_sweep() -> None:
    from stokes.app.demo import build_demo

    session = Session()
    build_demo(session)
    result = session.execute("study.results", study="velocity_sweep")
    assert result["status"] == "done" and result["counts"]["done"] == 3
    assert [p["inputs"]["U_in"] for p in result["points"]] == [1.0, 2.0, 3.0]
    assert [r["name"] for r in result["responses"]] == ["dp_dyn", "Q"]
    assert [r["label"] for r in result["responses"]] == ["Dynamic pressure", "Volume flow"]
    assert result["points"][0]["outputs"] == pytest.approx({"dp_dyn": dp(1.0), "Q": AREA})
    assert session.execute("history.state")["can_undo"] is False


@pytest.fixture(autouse=True)
def _demo_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """**Keep the demo out of the real user folder** (Claude's defect 9).

    `build_demo` writes its STEP through `demo_folder()`, which is under `STOKES_HOME` (or the
    user's
    home). These tests called it with no redirect, so they read and wrote `~/.stokes/demo` — which
    is
    why a demo built by an EARLIER RUN, with different tags, was served to them: the content-hashed
    name matched a file left behind, so the current generator's tags never appeared and the tests
    failed on the previous run's data. Two failures in the full suite were that, not the code.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path_factory.mktemp("stokes-home")))
