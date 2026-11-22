"""``shape.optimise`` with the flow split and finite differences, and ``shape.check`` over several
quantities (ADR-0040), and the flow split's adjoint where the host has adjointFoam (ADR-0041),
through ``Session.execute``. The CFD cycle is a stand-in whose outlet flows
follow the project's parameters, so the tests are about the commands, not about meshing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("OCP")

import stokes.geometry.model  # noqa: F401 - the operation node types
from stokes.api import Journal, Session, replay, shape_cycle
from stokes.errors import StokesError
from stokes.explore.shape import CycleResult
from stokes.geometry import sensitivity
from stokes.geometry.store import GeometryStore
from tests.test_implicit_operations import (  # noqa: F401 - the module's workers fixture
    implicit,
    new_session,
    reference_workers,
)

MM = 0.001


def _value(project: Any, name: str) -> float:  # Any: Project
    node, param = project.resolve_param(name)
    return float(project.value_of(node.id, param).to_base_units().magnitude)


class SplitCycle:
    """An ``AdjointCycle`` stand-in: outlet a's flow is R in mm, b's is 6 - W/2 in mm."""

    made: list[SplitCycle] = []

    def __init__(
        self,
        live: Session,
        part: str,
        workdir: Path,
        *,
        on_step: Any = None,
        adjoint: bool = True,
        objective: str = "pressure_loss",
        outlets: Any = (),
        solves_per_cycle: int = 1,
    ) -> None:
        self.solves_per_cycle = solves_per_cycle
        self.adjoint = adjoint
        self.objective = objective
        self.outlets = tuple(outlets)
        self.on_step = on_step
        self.count = 0
        SplitCycle.made.append(self)

    def abort(self) -> bool:
        return True

    def __call__(self, project: Any) -> CycleResult:  # Any: Project
        import numpy as np

        self.count += 1
        for number, name in enumerate(shape_cycle.STEPS, start=1):
            info = {"cycle": self.count, "step_number": number, "steps": 6, "elapsed": 0.0}
            self.on_step(name, info)
        a = _value(project, "R") / MM
        b = 6.0 - _value(project, "W") / MM / 2
        one = np.zeros((1, 3))
        return CycleResult(
            a + b,
            one,
            one,
            np.ones(1),
            np.ones(1),
            flows={"inlet": -(a + b), "a": a, "b": b},
            outlets=("a", "b"),
            # An adjoint cycle says where its wall sensitivity came from (ADR-0041 decision 8).
            detail={
                "cells": 100,
                "converged": True,
                **({"sensitivity_source": "solver"} if self.adjoint else {}),
            },
        )


def _program(found: bool | None) -> Any:  # Any: a stand-in for shape_cycle.adjoint_program
    why = "" if found else "the flow split's adjoint needs adjointFoam"
    return lambda ctx: {"host": "local", "program": "adjointFoam", "found": found, "why": why}


@pytest.fixture
def split_cycle(monkeypatch: pytest.MonkeyPatch) -> type[SplitCycle]:
    SplitCycle.made = []
    monkeypatch.setattr(shape_cycle, "AdjointCycle", SplitCycle)
    # No test asks a real host: by default it has no adjointFoam.
    monkeypatch.setattr(shape_cycle, "adjoint_program", _program(False))

    def gradient(*args: Any) -> dict[str, Any]:  # Any: the stand-in's arguments
        return {name: SimpleNamespace(value=2.0, covered=1.0) for name in args[-1]}

    monkeypatch.setattr(sensitivity, "wall_gradient", gradient)
    return SplitCycle


def _part(session: Session) -> str:
    session.execute("parameter.define", name="R", dimension="length", value="8 [mm]")
    session.execute("parameter.define", name="W", dimension="length", value="4 [mm]")
    info = implicit(session, "implicit_primitive", kind="sphere", radius="R", position=[0, 0, 0])
    return str(info["outputs"][0])


def test_flow_split_optimises_with_finite_differences(
    tmp_path: Path, split_cycle: type[SplitCycle]
) -> None:
    session = new_session(tmp_path)
    part = _part(session)
    probes: list[dict[str, Any]] = []
    session.events.subscribe("shape.probe", lambda event, data: probes.append(dict(data)))
    result = session.execute(
        "shape.optimise",
        part=part,
        objective="flow_split",
        variables=[{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}],
        fd_step=[0.05 * MM],
        max_cycles=20,
        wait=True,
    )
    assert split_cycle.made[0].adjoint is False  # ordinary solves only
    assert result["gradient"] == "finite_difference" and result["target"] == 0.01
    assert result["status"] == "target_reached"
    best = result["cycles"][-1]
    assert best["source"] == "finite_difference"
    assert best["detail"]["deviation"] <= 0.01
    assert best["detail"]["fractions"]["a"] == pytest.approx(0.5, abs=0.01)
    assert best["x"][0] == pytest.approx(4 * MM, abs=0.2 * MM)  # a = b = 4 mm
    assert len(probes) == 2 * len(result["cycles"])  # the point and one step per cycle
    # The live project did not move.
    assert _value(session.require_project(), "R") == pytest.approx(8 * MM)
    # The flow split's adjoint is refused, before any meshing, on a host without adjointFoam.
    with pytest.raises(StokesError, match="adjointFoam") as missing:
        session.execute(
            "shape.optimise",
            part=part,
            objective="flow_split",
            gradient="adjoint",
            variables=[{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}],
            wait=True,
        )
    assert missing.value.code == "solve.adjoint_missing"
    assert len(split_cycle.made) == 1  # no cycle was made for the refused run
    with pytest.raises(StokesError, match="fd_step"):
        session.execute(
            "shape.optimise",
            part=part,
            objective="flow_split",
            variables=[{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}],
            fd_step=[0.001, 0.002],
            wait=True,
        )
    GeometryStore.of(session).close()


def test_a_check_over_several_quantities_reports_the_gate(
    tmp_path: Path, split_cycle: type[SplitCycle]
) -> None:
    session = new_session(tmp_path)
    part = _part(session)
    steps: list[dict[str, Any]] = []
    session.events.subscribe("shape.step", lambda event, data: steps.append(dict(data)))
    checked = session.execute(
        "shape.check",
        part=part,
        parameters=["R", "W"],
        step=0.5 * MM,
        noise_floor=0.001,
        wait=True,
    )
    assert checked["status"] == "done" and checked["source"] == "adjoint"
    rows = {row["parameter"]: row for row in checked["gradients"]}
    # J = a + b = R/mm + 6 - W/(2 mm): dJ/dR = 1000, dJ/dW = -500 per metre.
    assert rows["R"]["finite_difference"] == pytest.approx(1000.0)
    assert rows["W"]["finite_difference"] == pytest.approx(-500.0)
    assert rows["R"]["ratio"] == pytest.approx(2.0 / 1000.0)
    assert checked["passed"] is False and checked["verdict"] == "failed"
    assert checked["cosine"] is not None
    assert all(row["consistent"] for row in checked["gradients"])
    # The same check with the default noise floor (1 % of J = 0.12): W's signal |J+ - J-| = 0.5
    # is under five floors, so the check concludes nothing rather than failing the adjoint.
    noisy = session.execute(
        "shape.check", part=part, parameters=["R", "W"], step=0.5 * MM, wait=True
    )
    assert noisy["verdict"] == "inconclusive" and noisy["passed"] is None
    assert noisy["suggested_step"] == [0.5 * MM, MM]  # only W is weak: twice its step
    assert {s["checks"] for s in steps} == {5}
    assert {s.get("parameter") for s in steps if s["check_run"] == "plus"} == {"R", "W"}
    assert session.execute("shape.history", part=part)["check"]["gradients"] == noisy["gradients"]
    # Without adjointFoam the flow split's check is the finite-difference gradient alone.
    split = session.execute(
        "shape.check", part=part, parameter="R", objective="flow_split", step=0.5 * MM, wait=True
    )
    assert split["source"] == "finite_difference" and split["passed"] is None
    assert "sensitivity_source" not in split  # finite differences use no wall sensitivity
    assert split_cycle.made[-1].adjoint is False
    assert split["split"]["fractions"]["a"] == pytest.approx(8 / 12)
    GeometryStore.of(session).close()


def test_the_journal_of_an_optimised_session_replays(
    tmp_path: Path, split_cycle: type[SplitCycle]
) -> None:
    session = new_session(tmp_path)
    part = _part(session)
    session.execute(
        "shape.optimise",
        part=part,
        objective="flow_split",
        variables=[{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}],
        fd_step=[0.05 * MM],
        max_cycles=3,
        wait=True,
    )
    records = Journal.read(tmp_path / "demo.jsonl")
    # shape.optimise reads (mutates=False), so it is not journaled: the journal replays to the
    # project as it was, with the parameters it started from.
    assert not any("shape.optimise" in str(r) for r in records)
    GeometryStore.of(session).close()
    again = new_session(tmp_path / "again")
    replay(again, records[1:])
    assert _value(again.require_project(), "R") == pytest.approx(8 * MM)
    GeometryStore.of(again).close()


def test_a_failed_check_blocks_the_adjoint(tmp_path: Path, split_cycle: type[SplitCycle]) -> None:
    session = new_session(tmp_path)
    part = _part(session)
    variables = [{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}]
    checked = session.execute(
        "shape.check", part=part, parameter="R", step=0.5 * MM, noise_floor=0.001, wait=True
    )
    assert checked["verdict"] == "failed"
    with pytest.raises(StokesError, match="failed the gate"):
        session.execute(
            "shape.optimise", part=part, objective="pressure_loss", variables=variables, wait=True
        )
    GeometryStore.of(session).close()


def test_the_adjoint_is_refused_for_a_quantity_that_resizes_a_port(tmp_path: Path) -> None:
    from stokes.geometry.retag import faces_of

    session = new_session(tmp_path)
    info = implicit(
        session, "implicit_primitive", kind="box", size=[0.06, 0.02, 0.02], position=[0, 0, 0]
    )
    part = str(info["outputs"][0])
    mesh = GeometryStore.of(session).mesh(part)
    assert mesh is not None
    faces, _ = faces_of(mesh)
    low = [i for i, f in faces.items() if f.normal[0] < -0.99]
    high = [i for i, f in faces.items() if f.normal[0] > 0.99]
    for name, ids, kind in (("inlet", low, "velocity_inlet"), ("outlet", high, "pressure_outlet")):
        session.execute(
            "boundary.assign", target=[{"part": part, "faces": ids}], kind=kind, name=name
        )
    session.execute("project.save")
    op = session.require_project().get(str(info["id"])).path.strip("/").replace("/", ".")
    with pytest.raises(StokesError, match="moves or resizes an inlet or outlet") as caught:
        session.execute(
            "shape.optimise",
            part=part,
            objective="pressure_loss",
            gradient="adjoint",
            variables=[{"reference": f"{op}.size.y", "lower": 0.01, "upper": 0.03}],
            wait=True,
        )
    assert f"{op}.size.y" in str(caught.value.details)
    GeometryStore.of(session).close()


def test_the_flow_split_takes_the_outlets_the_user_chose(
    tmp_path: Path, split_cycle: type[SplitCycle], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shape_cycle, "outlet_names", lambda session: ["a", "b"])
    session = new_session(tmp_path)
    part = _part(session)
    variables = [{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}]
    result = session.execute(
        "shape.optimise",
        part=part,
        objective="flow_split",
        variables=variables,
        outlets=["b", "a"],
        fd_step=[0.05 * MM],
        max_cycles=20,
        wait=True,
    )
    assert result["outlets"] == ["b", "a"] and result["status"] == "target_reached"
    assert list(result["cycles"][0]["detail"]["fractions"]) == ["b", "a"]
    for outlets, words in ((["a"], "at least two"), (["a", "x"], "not a pressure outlet")):
        with pytest.raises(StokesError, match=words):
            session.execute(
                "shape.optimise",
                part=part,
                objective="flow_split",
                variables=variables,
                outlets=outlets,
                wait=True,
            )
    with pytest.raises(StokesError, match="flow_split objective only"):
        session.execute(
            "shape.optimise",
            part=part,
            objective="volume",
            target_volume=1e-6,
            variables=variables,
            outlets=["a", "b"],
            wait=True,
        )
    GeometryStore.of(session).close()


# ------------------------------------------------------------------ ADR-0041: the split's adjoint
def _split_gradient(*args: Any) -> dict[str, Any]:  # Any: wall_gradient's arguments
    """dJ/dR of the stand-in's flow split, as an exact adjoint would give it (a = R/mm, b = 4)."""
    project, names = args[0], args[-1]
    a = _value(project, "R") / MM
    b = 6.0 - _value(project, "W") / MM / 2
    f = a / (a + b)
    d = {"R": 4.0 * (f - 0.5) * b / (a + b) ** 2 / MM}
    return {name: SimpleNamespace(value=d.get(name, 0.0), covered=1.0) for name in names}


@pytest.fixture
def split_adjoint(
    split_cycle: type[SplitCycle], monkeypatch: pytest.MonkeyPatch
) -> type[SplitCycle]:
    """A host with adjointFoam, outlets a and b, and an adjoint that is right."""
    monkeypatch.setattr(shape_cycle, "adjoint_program", _program(True))
    monkeypatch.setattr(shape_cycle, "outlet_names", lambda session: ["a", "b"])
    monkeypatch.setattr(sensitivity, "wall_gradient", _split_gradient)
    return split_cycle


def test_the_flow_split_optimises_with_its_adjoint(
    tmp_path: Path, split_adjoint: type[SplitCycle]
) -> None:
    session = new_session(tmp_path)
    part = _part(session)
    result = session.execute(
        "shape.optimise",
        part=part,
        objective="flow_split",
        gradient="adjoint",
        variables=[{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}],
        max_cycles=20,
        wait=True,
    )
    made = split_adjoint.made[-1]
    assert made.adjoint is True and made.objective == "flow_split"
    assert result["gradient"] == "adjoint" and result["status"] == "target_reached"
    rows = result["cycles"]
    assert all(row["source"] == "adjoint" for row in rows)
    # Unchecked: no gradient check of this part has passed in this session.
    assert all(row["detail"]["gate"] == "unchecked" for row in rows)
    assert all(row["detail"]["unverified"] is True for row in rows)
    assert all(row["detail"]["sensitivity_source"] == "solver" for row in rows)
    first = rows[0]["detail"]
    assert first["fractions"]["a"] == pytest.approx(8 / 12) and "deviation" in first
    assert first["pressure_loss"] == pytest.approx(12.0)  # the solver's own J, kept beside
    assert rows[0]["objective"] == pytest.approx(2 * (8 / 12 - 0.5) ** 2)
    assert rows[-1]["detail"]["deviation"] <= 0.01
    GeometryStore.of(session).close()


def test_a_passing_flow_split_check_verifies_the_adjoint(
    tmp_path: Path, split_adjoint: type[SplitCycle]
) -> None:
    session = new_session(tmp_path)
    part = _part(session)
    checked = session.execute(
        "shape.check",
        part=part,
        parameter="R",
        objective="flow_split",
        step=0.05 * MM,
        noise_floor=1e-4,
        wait=True,
    )
    assert split_adjoint.made[-1].adjoint is True
    assert checked["source"] == "adjoint" and checked["outlets"] == ["a", "b"]
    assert checked["ratio"] == pytest.approx(1.0, abs=0.02)
    assert checked["sensitivity_source"] == "solver"
    assert checked["verdict"] == "passed" and checked["passed"] is True
    variables = [{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}]
    result = session.execute(
        "shape.optimise",
        part=part,
        objective="flow_split",
        gradient="adjoint",
        variables=variables,
        max_cycles=2,
        wait=True,
    )
    assert result["cycles"][0]["detail"]["gate"] == "verified"
    assert result["cycles"][0]["detail"]["sensitivity_source"] == "solver"
    # The verdict is per objective: the pressure loss of the same quantity is still unchecked.
    loss = session.execute(
        "shape.optimise",
        part=part,
        objective="pressure_loss",
        variables=variables,
        max_cycles=1,
        wait=True,
    )
    assert loss["cycles"][0]["detail"]["gate"] == "unchecked"
    assert loss["cycles"][0]["detail"]["sensitivity_source"] == "solver"  # the pressure loss too
    GeometryStore.of(session).close()


def test_a_failed_flow_split_check_blocks_its_adjoint(
    tmp_path: Path, split_adjoint: type[SplitCycle], monkeypatch: pytest.MonkeyPatch
) -> None:
    session = new_session(tmp_path)
    part = _part(session)

    def wrong(*args: Any) -> dict[str, Any]:  # Any: wall_gradient's arguments
        right = _split_gradient(*args)
        return {n: SimpleNamespace(value=-r.value, covered=1.0) for n, r in right.items()}

    monkeypatch.setattr(sensitivity, "wall_gradient", wrong)
    checked = session.execute(
        "shape.check",
        part=part,
        parameter="R",
        objective="flow_split",
        step=0.05 * MM,
        noise_floor=1e-4,
        wait=True,
    )
    assert checked["verdict"] == "failed" and checked.get("wrong_sign") is True
    with pytest.raises(StokesError, match="failed the gate"):
        session.execute(
            "shape.optimise",
            part=part,
            objective="flow_split",
            gradient="adjoint",
            variables=[{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}],
            wait=True,
        )
    # Finite differences stay available.
    done = session.execute(
        "shape.optimise",
        part=part,
        objective="flow_split",
        variables=[{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}],
        fd_step=[0.05 * MM],
        max_cycles=1,
        wait=True,
    )
    assert done["gradient"] == "finite_difference"
    GeometryStore.of(session).close()


def test_the_split_adjoint_takes_every_outlet(
    tmp_path: Path, split_adjoint: type[SplitCycle], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shape_cycle, "outlet_names", lambda session: ["a", "b", "c"])
    session = new_session(tmp_path)
    part = _part(session)
    with pytest.raises(StokesError, match="c is left out"):
        session.execute(
            "shape.optimise",
            part=part,
            objective="flow_split",
            gradient="adjoint",
            outlets=["a", "b"],
            variables=[{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}],
            wait=True,
        )
    # A check of the subset falls back to the finite differences alone.
    checked = session.execute(
        "shape.check",
        part=part,
        parameter="R",
        objective="flow_split",
        outlets=["a", "b"],
        step=0.05 * MM,
        noise_floor=1e-4,
        wait=True,
    )
    assert checked["source"] == "finite_difference" and checked["verdict"] == "no_adjoint"
    GeometryStore.of(session).close()


def test_shape_gradients_says_what_the_host_allows(
    tmp_path: Path, split_cycle: type[SplitCycle], monkeypatch: pytest.MonkeyPatch
) -> None:
    session = new_session(tmp_path)
    without = session.execute("shape.gradients")
    assert without["objectives"]["flow_split"] == ["finite_difference"]
    assert without["objectives"]["pressure_loss"] == ["adjoint", "finite_difference"]
    assert without["defaults"]["flow_split"] == "finite_difference"
    assert (
        without["adjoint_program"]["found"] is False
        and "adjointFoam" in without["adjoint_program"]["why"]
    )
    monkeypatch.setattr(shape_cycle, "adjoint_program", _program(True))
    found = session.execute("shape.gradients")
    assert found["objectives"]["flow_split"] == ["finite_difference", "adjoint"]
    assert found["defaults"]["flow_split"] == "finite_difference"  # the default does not move
    GeometryStore.of(session).close()


def test_the_journal_of_a_split_adjoint_session_replays(
    tmp_path: Path, split_adjoint: type[SplitCycle]
) -> None:
    session = new_session(tmp_path)
    part = _part(session)
    session.execute(
        "shape.optimise",
        part=part,
        objective="flow_split",
        gradient="adjoint",
        variables=[{"reference": "R", "lower": 2 * MM, "upper": 10 * MM}],
        max_cycles=2,
        wait=True,
    )
    session.execute("shape.gradients")
    history = session.execute("shape.history", part=part)
    assert all(c["detail"]["sensitivity_source"] == "solver" for c in history["cycles"])
    records = Journal.read(tmp_path / "demo.jsonl")
    assert not any("shape." in str(r) for r in records)  # reads are not journaled
    assert not any("sensitivity_source" in str(r) for r in records)
    GeometryStore.of(session).close()
    again = new_session(tmp_path / "again")
    replay(again, records[1:])
    assert _value(again.require_project(), "R") == pytest.approx(8 * MM)
    GeometryStore.of(again).close()
