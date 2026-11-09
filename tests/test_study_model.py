"""Study node types (stokes.explore.model, ADR-0016 point 1): schemas, defaults, validation,
persistence, the /studies skeleton group and the import discipline they keep."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from stokes.errors import ModelError, UnitMismatch, ValidationError
from stokes.explore import model
from stokes.explore.errors import SamplerError
from stokes.model import Project, SetParam, UndoStack, load_project, registry, save_project
from stokes.model.ids import stable_id
from tests.test_study_support import PARAMS

TYPES = ("study", "variable", "response", "objective", "constraint")


def study_project() -> Project:
    project = Project.new("demo")
    project.add(PARAMS, "parameter", "U_in", {"dimension": "velocity", "value": "2 [m/s]"})
    study = project.add("/studies", "study", "sweep", {"type": "doe", "count": 8})
    for group in model.STUDY_GROUPS:
        project.add(study.id, "group", group)
    variable = project.add(
        "/studies/sweep/variables",
        "variable",
        "U_in",
        {"parameter": "U_in", "dimension": "velocity", "domain": "range", "min": "1 [m/s]"},
    )
    project.set_param(variable.id, "max", "U_in * 2")
    response = project.add("/studies/sweep/responses", "response", "u2", {"expression": "U_in * 2"})
    project.set_param(response.id, "dimension", "velocity")
    project.add("/studies/sweep/objectives", "objective", "u2", {"response": response.id})
    project.add(
        "/studies/sweep/constraints",
        "constraint",
        "u2_max",
        {"expression": "u2", "dimension": "velocity", "operator": "<=", "limit": "5 [m/s]"},
    )
    return project


def test_the_study_types_are_registered_with_generated_schemas() -> None:
    assert set(TYPES) <= set(registry.types())
    schema = registry.json_schema()
    variable = schema["variable"]["properties"]
    assert variable["min"]["x-dimension"] == "dynamic"
    assert variable["domain"]["enum"] == list(model.DOMAINS)
    assert variable["dimension"]["default"] == "dimensionless"
    assert schema["study"]["properties"]["sampler"]["enum"] == list(model.SAMPLERS)
    assert schema["study"]["properties"]["status"]["enum"] == list(model.STUDY_STATUSES)
    assert schema["objective"]["properties"]["response"]["x-ref-type"] == "response"
    assert schema["constraint"]["properties"]["operator"]["enum"] == ["<=", ">=", "=="]
    assert schema["study"]["x-children"] == ["group"]
    assert all(schema[t]["description"] for t in TYPES)


def test_registration_is_idempotent() -> None:
    before = registry.json_schema()
    model.register_study_types(registry)
    assert registry.json_schema() == before


def test_sampler_names_match_the_numerics() -> None:
    from stokes.explore.domain import Range
    from stokes.explore.sampling import generate, sampler_names
    from stokes.explore.space import Variable

    assert sampler_names() == model.SAMPLERS
    assert set(model.SAMPLERS) >= model.SIZED_SAMPLERS
    box = [Variable(f"x{i}", Range(0.0, 1.0, count=2)) for i in range(3)]
    for name in model.SAMPLERS:
        if name in model.SIZED_SAMPLERS:
            with pytest.raises(SamplerError, match="number of points"):
                generate(name, box)
            assert generate(name, box, n=4, seed=1).n_points == 4
        else:
            assert generate(name, box).n_points > 0


def test_defaults_are_valid_for_every_type() -> None:
    project = Project.new("demo")
    study = project.add("/studies", "study", "bare")
    assert study.params == {
        "type": "sweep",
        "sampler": "full_factorial",
        "seed": 0,
        "count": 0,
        "status": "idle",
        "description": "",
    }
    group = project.add(study.id, "group", "variables")
    variable = project.add(group.id, "variable", "v")
    assert variable.params["values"] == [] and variable.params["domain"] == "values"
    assert variable.params["min"].value == 0.0
    response = project.add(study.id, "group", "responses")
    assert project.add(response.id, "response", "r").params["dimension"] == "dimensionless"
    holder = project.add(study.id, "group", "objectives")
    assert project.add(holder.id, "objective", "o").params == {
        "response": None,
        "direction": "minimise",
    }
    holder = project.add(study.id, "group", "constraints")
    assert project.add(holder.id, "constraint", "c").params["operator"] == "<="
    assert all(n.status == "ok" for n in project.nodes())


def test_quantities_take_the_dimension_of_their_node() -> None:
    project = study_project()
    variable = project.get("/studies/sweep/variables/U_in")
    assert variable.params["min"].value == pytest.approx(1.0)
    assert variable.params["max"].expr == "U_in * 2"
    assert variable.params["max"].value == pytest.approx(4.0)
    project.set_param(variable.id, "min", "36 [km/h]")
    assert variable.params["min"].value == pytest.approx(10.0)
    with pytest.raises(UnitMismatch):
        project.set_param(variable.id, "min", "1 [Pa]")
    project.set_param(f"{PARAMS}/U_in", "value", "3 [m/s]")
    assert variable.params["max"].value == pytest.approx(6.0)  # bounds follow the base case
    limit = project.get("/studies/sweep/constraints/u2_max").params["limit"]
    assert (limit.value, limit.unit) == (5.0, "m/s")


def test_a_dimension_change_resets_the_quantities_that_take_it() -> None:
    """The rule of the ``parameter`` node (its value is zeroed in the new dimension) holds for
    every node type with a ``dimension``: the variable's bounds, step, mean and deviation, and
    the constraint's limit."""
    project = study_project()
    variable = project.get("/studies/sweep/variables/U_in")
    assert variable.params["min"].value == pytest.approx(1.0)
    assert variable.params["max"].expr == "U_in * 2"
    project.set_param(variable.id, "dimension", "length")
    for name in ("min", "max", "step", "mean", "std"):
        reset = variable.params[name]
        assert (reset.value, reset.unit, reset.expr) == (0.0, "m", None), name
    project.set_param(variable.id, "min", "2 [mm]")  # and they take values of the new dimension
    assert variable.params["min"].value == pytest.approx(0.002)
    with pytest.raises(UnitMismatch):
        project.set_param(variable.id, "max", "1 [m/s]")
    constraint = project.get("/studies/sweep/constraints/u2_max")
    project.set_param(constraint.id, "dimension", "pressure")
    limit = constraint.params["limit"]
    assert (limit.value, limit.unit, limit.expr) == (0.0, "Pa", None)
    assert constraint.params["expression"] == "u2" and constraint.params["operator"] == "<="
    response = project.get("/studies/sweep/responses/u2")  # a dimension and no quantity to reset
    project.set_param(response.id, "dimension", "length")
    assert response.params["dimension"] == "length" and response.params["expression"] == "U_in * 2"
    assert all(n.status == "ok" for n in project.nodes())


def test_a_dimension_change_evaluates_again_what_refers_to_a_reset_quantity() -> None:
    project = study_project()
    variable = project.get("/studies/sweep/variables/U_in")
    top = project.add(
        PARAMS,
        "parameter",
        "top",
        {"dimension": "velocity", "value": "studies.sweep.variables.U_in.max"},
    )
    assert top.params["value"].value == pytest.approx(4.0) and top.status == "ok"
    project.set_param(variable.id, "dimension", "length")
    assert top.status == "failed" and "expected velocity" in top.meta["error"]
    project.set_param(variable.id, "dimension", "velocity")
    assert variable.params["max"].value == 0.0  # reset again: the old bound is gone for good
    project.set_param(variable.id, "max", "U_in * 2")
    assert top.status == "ok" and top.params["value"].value == pytest.approx(4.0)


def test_a_dimension_change_clears_the_failure_of_an_expression_it_reset() -> None:
    project = study_project()
    variable = project.get("/studies/sweep/variables/U_in")
    project.remove(f"{PARAMS}/U_in")  # the bound U_in * 2 has lost its parameter
    assert variable.status == "failed" and "error" in variable.meta
    project.set_param(variable.id, "dimension", "length")
    assert variable.status == "ok" and "error" not in variable.meta
    project.add(PARAMS, "parameter", "a", {"dimension": "length", "value": "1 [m]"})
    parameter = project.add(PARAMS, "parameter", "x", {"dimension": "length", "value": "a * 2"})
    project.remove(f"{PARAMS}/a")
    assert parameter.status == "failed"  # a parameter loses its failure the same way
    project.set_param(parameter.id, "dimension", "velocity")
    assert parameter.status == "ok" and parameter.params["value"].value == 0.0


def test_setting_the_dimension_a_node_already_has_changes_nothing() -> None:
    project = study_project()
    variable = project.get("/studies/sweep/variables/U_in")
    project.set_param(variable.id, "dimension", "velocity")
    assert variable.params["min"].value == pytest.approx(1.0)
    assert variable.params["max"].expr == "U_in * 2"
    parameter = project.get(f"{PARAMS}/U_in")
    project.set_param(parameter.id, "dimension", "velocity")
    assert parameter.params["value"].value == pytest.approx(2.0)


def test_undoing_a_dimension_change_gives_back_a_variables_bounds_and_a_constraints_limit() -> None:
    project = study_project()
    stack = UndoStack()
    variable = project.get("/studies/sweep/variables/U_in")
    constraint = project.get("/studies/sweep/constraints/u2_max")
    top = project.add(
        PARAMS,
        "parameter",
        "top",
        {"dimension": "velocity", "value": "studies.sweep.variables.U_in.max"},
    )
    stack.push(SetParam(variable.id, "dimension", "length"), project)
    stack.push(SetParam(constraint.id, "dimension", "pressure"), project)
    assert variable.params["max"].value == 0.0 and variable.params["max"].expr is None
    assert constraint.params["limit"].value == 0.0 and top.status == "failed"
    stack.undo(project)  # the constraint
    limit = constraint.params["limit"]
    assert constraint.params["dimension"] == "velocity" and (limit.value, limit.unit) == (
        5.0,
        "m/s",
    )
    stack.undo(project)  # the variable
    assert variable.params["dimension"] == "velocity"
    for name, expected in {"min": 1.0, "max": 4.0, "step": 0.0, "mean": 0.0, "std": 0.0}.items():
        restored = variable.params[name]
        assert (restored.value, restored.unit) == (pytest.approx(expected), "m/s"), name
    assert variable.params["max"].expr == "U_in * 2" and variable.params["min"].expr is None
    assert top.status == "ok" and top.params["value"].value == pytest.approx(4.0)
    project.set_param(f"{PARAMS}/U_in", "value", "3 [m/s]")  # the restored bound is live again
    assert variable.params["max"].value == pytest.approx(6.0)
    assert top.params["value"].value == pytest.approx(6.0)
    stack.redo(project)  # redo resets the variable again, then the constraint
    stack.redo(project)
    assert variable.params["dimension"] == "length" and variable.params["max"].value == 0.0
    assert constraint.params["limit"].value == 0.0 and top.status == "failed"


def test_enums_and_references_are_validated() -> None:
    project = study_project()
    with pytest.raises(ValidationError):
        project.set_param("/studies/sweep", "type", "optimisation")
    with pytest.raises(ValidationError):
        project.set_param("/studies/sweep", "sampler", "lhs")
    with pytest.raises(ValidationError):
        project.set_param("/studies/sweep/variables/U_in", "domain", "triangular")
    with pytest.raises(ValidationError):  # an objective refers to a response, not a variable
        project.set_param(
            "/studies/sweep/objectives/u2", "response", "/studies/sweep/variables/U_in"
        )
    with pytest.raises(ValidationError):
        project.add("/studies/sweep/responses", "response", "bad name")


def test_study_nodes_round_trip_through_persistence(tmp_path: Path) -> None:
    project = study_project()
    loaded = load_project(save_project(project, tmp_path / "demo"))
    assert loaded.to_json()["root"] == project.to_json()["root"]
    variable = loaded.get("/studies/sweep/variables/U_in")
    loaded.set_param(f"{PARAMS}/U_in", "value", "4 [m/s]")
    assert variable.params["max"].value == pytest.approx(8.0)


def test_studies_is_a_structural_skeleton_group() -> None:
    project = Project.new("demo")
    studies = project.get(model.STUDIES_PATH)
    assert studies.id == stable_id("g", "/studies") and project.is_structural(studies)
    with pytest.raises(ModelError):
        project.remove(model.STUDIES_PATH)
    with pytest.raises(ModelError):
        project.rename(model.STUDIES_PATH, "explore")


def test_the_api_and_its_commands_load_no_numerics() -> None:
    code = (
        "import sys; from stokes.api import Session; s = Session(); "
        "s.execute('project.new', name='x'); "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('numpy', 'scipy') or "
        "(m.startswith('stokes.explore') and m not in ('stokes.explore', 'stokes.explore.model')));"
        " print(bad); sys.exit(1 if bad else 0)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=180
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_only_study_numerics_brings_numpy_and_none_brings_scipy() -> None:
    light = ("definition", "authoring", "evaluate", "engine_function", "study_table", "study_store")
    imports = "; ".join(f"import stokes.explore.{m}" for m in light)
    code = (
        f"import sys; {imports}; "
        "early = sorted(m for m in ('numpy', 'scipy') if m in sys.modules); "
        "import stokes.explore.study_numerics; "
        "late = sorted(m for m in ('scipy', 'sklearn', 'SALib', 'pymoo') if m in sys.modules); "
        "print(early, late); sys.exit(1 if early or late else 0)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=180
    )
    assert done.returncode == 0, done.stdout + done.stderr
