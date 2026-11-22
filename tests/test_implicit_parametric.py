"""Parametric implicit geometry (ADR-0037): vectors and counts are quantities that expressions
drive, a drawn sketch follows its dimensions, a change of a project parameter re-runs the
operations that read it, and the shape derivatives of ADR-0035 decision 27 match the exact ones.

Like ``test_implicit_operations``, the session tests mesh with the built mesher or the reference
mesher, so they need OCP (the worker) but not the Rust program.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("OCP")

import stokes.geometry.model  # noqa: F401 - the operation node types
from stokes.api import Session
from stokes.errors import StokesError, ValidationError
from stokes.geometry.opstore import OperationStore
from stokes.geometry.store import GeometryStore
from stokes.implicit import features
from stokes.implicit.sensitivity import normal_velocity, surface_gradient
from stokes.model.project import Project
from tests.test_implicit_operations import (  # noqa: F401 - the module's workers fixture
    implicit,
    mesh_volume,
    new_session,
    reference_workers,
)
from tests.test_implicit_sketch_faces import needs_mesher

R = 0.008  # the sphere's radius, metres


# ------------------------------------------------------------------------------- the model
def test_a_vector_is_three_quantities_that_expressions_drive() -> None:
    project = Project.new("p")
    project.add("/physics/parameters", "parameter", "L", {"dimension": "length", "value": "5 [mm]"})
    op = project.add(
        "/geometry", "implicit_primitive", "cyl", {"position": ["L", "2 * L", "3 [mm]"]}
    )
    assert [op.params[f"position.{a}"].value for a in "xyz"] == pytest.approx([5e-3, 1e-2, 3e-3])
    project.set_param("/physics/parameters/L", "value", "1 [mm]")
    assert op.params["position.y"].value == pytest.approx(2e-3)
    assert project.evaluate("geometry.cyl.position.y * 3").magnitude == pytest.approx(6e-3)
    old = project.set_param(op.id, "position", {"x": 1, "y": 2, "z": 3})
    assert old[1]["expr"] == "2 * L"
    project.set_param(op.id, "position", old)  # the undo form puts the expressions back
    assert op.params["position.y"].expr == "2 * L"
    with pytest.raises(ValidationError, match="three values"):
        project.set_param(op.id, "position", [1, 2])
    with pytest.raises(StokesError):
        project.set_param(op.id, "position", [1, "nonsense +", 3])
    assert op.params["position.x"].value == pytest.approx(1e-3)  # nothing of the failed set kept


def test_a_count_is_a_whole_number_an_expression_may_give() -> None:
    project = Project.new("p")
    project.add(
        "/physics/parameters", "parameter", "N", {"dimension": "dimensionless", "value": "4"}
    )
    op = project.add("/geometry", "implicit_pattern", "row", {"count": "N"})
    assert op.params["count"].value == 4
    with pytest.raises(ValidationError, match="whole number"):
        project.set_param(op.id, "count", "2.5")
    project.set_param("/physics/parameters/N", "value", "4.5")
    assert op.status == "failed"  # the expression no longer gives a whole number


def test_sketch_dimensions_drive_the_entities() -> None:
    from stokes.geometry.opplan import _params

    project = Project.new("p")
    project.add("/physics/parameters", "parameter", "T", {"dimension": "length", "value": "4 [mm]"})
    sketch = project.add(
        "/geometry",
        "implicit_sketch",
        "sk",
        {
            "profile": "custom",
            "dimensions": {"W": "20 [mm]", "H": "T * 2"},
            "entities": [
                {"kind": "line", "a": [0, 0], "b": ["W", 0]},
                {"kind": "line", "a": ["W", 0], "b": ["W", "H"]},
                {"kind": "line", "a": ["W", "H"], "b": [0, "H"]},
                {"kind": "line", "a": [0, "H"], "b": [0, 0]},
            ],
        },
    )
    params, _ = _params(project, sketch)
    assert params["entities"][1]["b"] == pytest.approx([0.02, 0.008])
    project.set_param("/physics/parameters/T", "value", "5 [mm]")
    params, _ = _params(project, sketch)
    assert params["entities"][1]["b"] == pytest.approx([0.02, 0.01])
    project.set_param(sketch.id, "dimensions", {"W": "20 [mm]"})  # H removed
    with pytest.raises(StokesError, match="no dimension 'H'"):
        _params(project, sketch)


# ------------------------------------------------------------------------------- the kernel
def test_the_normal_velocity_of_a_radius_is_one_and_the_volume_derivative_exact() -> None:
    radius = 8.0  # mm, the kernel's units
    n = 2000
    rng = np.random.default_rng(1)
    directions = rng.normal(size=(n, 3))
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    points = directions * radius

    def field_at(value: float) -> Any:  # Any: Expr
        return features.sphere(value, (0.0, 0.0, 0.0)).field()

    velocity = normal_velocity(field_at, radius, 1e-3, points, 2 * radius)
    assert velocity.valid.all()
    assert velocity.speed == pytest.approx(np.ones(n), abs=1e-6)
    assert np.einsum("ij,ij->i", velocity.normal, directions) == pytest.approx(np.ones(n))
    areas = np.full(n, 4 * math.pi * radius**2 / n)
    found = surface_gradient(velocity, np.ones(n), directions, areas)
    assert found.value == pytest.approx(4 * math.pi * radius**2)
    assert (found.covered, found.left_out) == (1.0, 0)
    # A solid in the fluid: the patch normal points into the body, and the sign follows.
    inward = surface_gradient(velocity, np.ones(n), -directions, areas)
    assert inward.value == pytest.approx(-4 * math.pi * radius**2)


# ------------------------------------------------------------------------------- a session
def _sphere(session: Session) -> tuple[str, str]:
    session.execute("parameter.define", name="R", dimension="length", value=f"{R * 1000} [mm]")
    info = implicit(
        session, "implicit_primitive", kind="sphere", radius="R", position=["R / 2", 0, 0]
    )
    return info["id"], info["outputs"][0]


def test_a_parameter_change_re_runs_the_operation_that_reads_it(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    _, part = _sphere(session)
    before = mesh_volume(session, part)
    session.execute("parameter.set", node="/physics/parameters/R", name="value", value="10 [mm]")
    OperationStore.of(session).wait()
    after = mesh_volume(session, part)
    assert after / before == pytest.approx((10 / 8) ** 3, rel=0.03)
    GeometryStore.of(session).close()


def test_the_gradient_of_the_volume_matches_the_exact_derivative(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    op, part = _sphere(session)
    path = session.require_project().get(op).path.strip("/").replace("/", ".")
    result = session.execute(
        "implicit.gradient",
        part=part,
        parameters=["R", f"{path}.radius", f"{path}.position.y"],
    )
    gradients = result["gradients"]
    exact = 4 * math.pi * R**2  # dV/dR, m³ per m
    # R moves both the radius and the centre; the centre does not change the volume.
    assert gradients["R"]["value"] == pytest.approx(exact, rel=0.02)
    assert gradients[f"{path}.radius"]["value"] == pytest.approx(exact, rel=0.02)
    assert abs(gradients[f"{path}.position.y"]["value"]) < 0.01 * exact
    assert gradients["R"]["covered"] > 0.99
    speeds = session.execute("implicit.normal_velocity", part=part, parameter=f"{path}.radius")
    assert speeds["mean"] == pytest.approx(1.0, rel=0.02)  # a radius moves the surface 1:1
    assert len(speeds["speeds"]) == speeds["triangles"]
    GeometryStore.of(session).close()


def test_a_count_has_no_derivative(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    _op, part = _sphere(session)
    row = implicit(session, "implicit_pattern", [part], kind="linear", count=2, pitch=0.03)
    path = session.require_project().get(row["id"]).path.strip("/").replace("/", ".")
    with pytest.raises(StokesError, match="whole number") as caught:
        session.execute("implicit.gradient", part=row["outputs"][0], parameters=[f"{path}.count"])
    assert caught.value.code == "implicit.not_differentiable"
    GeometryStore.of(session).close()


def test_a_geometry_study_rebuilds_and_measures_each_point(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    _op, part = _sphere(session)
    project = session.require_project()
    part_path = project.get(part).path.strip("/").replace("/", ".")
    measured = session.execute("geometry.measure", part=part)
    assert measured["volume"] == pytest.approx(4 / 3 * math.pi * R**3, rel=0.02)
    assert measured["closed"] is True
    session.execute("study.create", name="size", engine="geometry")
    session.execute("study.add_variable", study="size", parameter="R", values=["6 [mm]", "10 [mm]"])
    session.execute("study.add_response", study="size", expression=f"{part_path}.volume")
    session.execute(
        "study.add_response", study="size", name="width", expression=f"{part_path}.extent.x"
    )
    session.execute("study.generate", study="size")
    result = session.execute("study.update", study="size")
    assert result["done"] == 2, result
    rows = session.execute("study.results", study="size")["points"]
    for row in rows:
        r = row["inputs"]["R"]
        assert row["outputs"]["volume"] == pytest.approx(4 / 3 * math.pi * r**3, rel=0.03)
        assert row["outputs"]["width"] == pytest.approx(2 * r, rel=0.03)
    # The live project is untouched: R and the part are as they were.
    assert project.get("/physics/parameters/R").params["value"].value == pytest.approx(R)
    GeometryStore.of(session).close()


# ------------------------------------------------------------------------------- shape optimisation
def test_the_gradient_driver_finds_a_bounded_minimum() -> None:
    from stokes.explore.optimise.gradient import Evaluation, optimise

    def evaluate(x: np.ndarray) -> Evaluation:
        return Evaluation(
            float((x[0] - 0.3) ** 2 + (x[1] + 2.0) ** 2),
            np.array([2 * (x[0] - 0.3), 2 * (x[1] + 2.0)]),
            "exact",
        )

    result = optimise(evaluate, [0.9, 0.5], [0.0, -1.0], [1.0, 1.0], max_cycles=30)
    best = result.best
    assert best is not None and result.status == "converged"
    assert best.x == pytest.approx((0.3, -1.0), abs=1e-4)  # y stops at its bound
    stopped = optimise(evaluate, [0.9, 0.5], [0.0, -1.0], [1.0, 1.0], on_cycle=lambda c: True)
    assert stopped.status == "cancelled" and len(stopped.cycles) == 1


def test_a_volume_objective_sizes_a_sphere(tmp_path: Path) -> None:
    from stokes.explore.optimise.gradient import optimise
    from stokes.explore.shape import Variable, VolumeObjective, current_values

    session = new_session(tmp_path)
    _, part = _sphere(session)
    project = session.require_project()
    variables = [Variable("R", 0.004, 0.02)]
    target = 4 / 3 * math.pi * 0.012**3
    objective = VolumeObjective(project, part, variables, target, cache=tmp_path / "cache")
    result = optimise(objective, current_values(project, variables), [0.004], [0.02], max_cycles=15)
    best = result.best
    assert best is not None and best.source == "exact"
    assert best.x[0] == pytest.approx(0.012, rel=0.02)
    assert project.get("/physics/parameters/R").params["value"].value == pytest.approx(R)
    GeometryStore.of(session).close()


def test_the_adjoint_gradient_sums_on_the_part_only(tmp_path: Path) -> None:
    from stokes.geometry.sensitivity import wall_gradient

    session = new_session(tmp_path)
    _, part = _sphere(session)
    project = session.require_project()
    rng = np.random.default_rng(3)
    n = 4000
    d = rng.normal(size=(n, 3))
    d /= np.linalg.norm(d, axis=1)[:, None]
    centre = np.array([R / 2, 0.0, 0.0])
    on = centre + d * R
    away = centre + d[:50] * 3 * R  # walls of something else: ignored
    centres = np.vstack([on, away])
    # The fluid is outside the sphere: the patch normal out of the fluid points into the body.
    normals = np.vstack([-d, d[:50]])
    areas = np.concatenate([np.full(n, 4 * math.pi * R**2 / n), np.full(50, 1.0)])
    found = wall_gradient(
        project, project.get(part), centres, normals, areas, np.ones(n + 50), ["R"]
    )
    # dJ/dR = sum s V_n (n_phi . n) A = -4 pi R^2 + the centre's share (zero over a sphere).
    assert found["R"].value == pytest.approx(-4 * math.pi * R**2, rel=0.03)
    GeometryStore.of(session).close()


def test_shape_optimise_streams_cycles_and_keeps_the_project(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    _, part = _sphere(session)
    seen: list[dict[str, Any]] = []
    session.events.subscribe("shape.cycle", lambda event, data: seen.append(data))
    result = session.execute(
        "shape.optimise",
        part=part,
        variables=[{"reference": "R", "lower": 0.004, "upper": 0.02}],
        target_volume=4 / 3 * math.pi * 0.01**3,
        max_cycles=12,
        wait=True,
    )
    assert result["best"]["x"][0] == pytest.approx(0.01, rel=0.02)
    assert len(seen) == len(result["cycles"]) >= 2
    assert session.execute("shape.history", part=part)["best"] == result["best"]
    # The adjoint objective needs the flow's boundaries tagged on the part first.
    with pytest.raises(StokesError, match="boundary tags"):
        session.execute(
            "shape.optimise",
            part=part,
            objective="pressure_loss",
            variables=[{"reference": "R", "lower": 0.004, "upper": 0.02}],
        )
    GeometryStore.of(session).close()


def test_parameter_assign_sets_by_reference_as_one_step(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    op, _ = _sphere(session)
    project = session.require_project()
    path = project.get(op).path.strip("/").replace("/", ".")
    session.execute("parameter.assign", values={"R": 0.009, f"{path}.position.y": "2 [mm]"})
    assert project.get("/physics/parameters/R").params["value"].value == pytest.approx(0.009)
    assert project.get(op).params["position.y"].value == pytest.approx(0.002)
    session.execute("history.undo")
    assert project.get("/physics/parameters/R").params["value"].value == pytest.approx(R)
    assert project.get(op).params["position.y"].value == pytest.approx(0.0)
    GeometryStore.of(session).close()


def test_shape_variables_lists_what_shapes_the_part(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    op, part = _sphere(session)
    path = session.require_project().get(op).path.strip("/").replace("/", ".")
    found = {v["reference"]: v for v in session.execute("shape.variables", part=part)["variables"]}
    assert {f"{path}.radius", f"{path}.position.x", "R"} <= set(found)
    assert found[f"{path}.radius"]["label"].endswith("Radius")
    assert found["R"]["value"] == pytest.approx(R)
    assert found["R"]["lower"] == pytest.approx(R / 2) and found["R"]["upper"] == pytest.approx(
        1.5 * R
    )
    GeometryStore.of(session).close()


@needs_mesher  # the reference mesher rounds the edges, so the planar caps are not found again
def test_boundary_rules_find_the_planes_and_the_rest_again(tmp_path: Path) -> None:
    from stokes.geometry.retag import apply_rules, faces_of, rules_for, untagged

    session = new_session(tmp_path)
    info = implicit(
        session, "implicit_primitive", kind="box", size=[0.06, 0.02, 0.02], position=[0, 0, 0]
    )
    mesh = GeometryStore.of(session).mesh(info["outputs"][0])
    assert mesh is not None
    faces, _ = faces_of(mesh)
    low = [i for i, f in faces.items() if f.normal[0] < -0.99]
    high = [i for i, f in faces.items() if f.normal[0] > 0.99]
    rest = sorted(set(faces) - set(low) - set(high))
    rules = rules_for(mesh, [("inlet", low), ("outlet", high), ("walls", rest)])
    assert [r.kind for r in rules] == ["plane", "plane", "rest"]
    assert rules[0].offset == pytest.approx(0.03, rel=1e-3)  # n = -x, so n . x = +0.03 at x = -0.03
    found = apply_rules(mesh, rules)
    assert found == {"inlet": sorted(low), "outlet": sorted(high), "walls": rest}
    # The faces nobody tagged are what a cycle makes the walls: here, with only the ends tagged.
    ends_only = apply_rules(mesh, rules[:2])
    assert untagged(mesh, ends_only) == rest
    with pytest.raises(StokesError, match="more than one boundary"):
        rules_for(mesh, [("a", rest[0:2]), ("b", rest[2:4])])
    GeometryStore.of(session).close()


def test_shape_optimise_runs_in_the_background_by_default(tmp_path: Path) -> None:
    import threading

    session = new_session(tmp_path)
    _, part = _sphere(session)
    done = threading.Event()
    finished: list[dict[str, Any]] = []

    def heard(event: str, data: dict[str, Any]) -> None:
        finished.append(data)
        done.set()

    session.events.subscribe("shape.finished", heard)
    reply = session.execute(
        "shape.optimise",
        part=part,
        variables=[{"reference": "R", "lower": 0.004, "upper": 0.02}],
        target_volume=4 / 3 * math.pi * 0.01**3,
        max_cycles=12,
    )
    assert reply == {"part": part, "started": True}  # the server is free while it runs
    assert done.wait(120)
    assert finished[0]["status"] == "converged"
    assert finished[0]["result"]["best"]["x"][0] == pytest.approx(0.01, rel=0.02)
    GeometryStore.of(session).close()
