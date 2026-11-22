"""What a shape optimisation tells a client while it runs (ADR-0038 decision 6): each step of a CFD
cycle names its cycle, its place among the steps and the time since the cycle began; a gradient
check numbers its three evaluations; ``shape.cancel`` stops a check between evaluations, or at once
with ``now``; ``shape.variables`` gives each label's body and quantity apart.

The CFD cycle itself is replaced by a stand-in with the same interface: these tests are about the
commands and their events, not about meshing or solving.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

pytest.importorskip("OCP")

import stokes.geometry.model  # noqa: F401 - the operation node types
from stokes.api import Session, shape_cycle
from stokes.geometry import sensitivity
from stokes.geometry.store import GeometryStore
from tests.test_implicit_operations import (  # noqa: F401 - the module's workers fixture
    implicit,
    new_session,
    reference_workers,
)

R = 0.008


def _sphere(session: Session) -> str:
    session.execute("parameter.define", name="R", dimension="length", value=f"{R * 1000} [mm]")
    info = implicit(session, "implicit_primitive", kind="sphere", radius="R", position=[0, 0, 0])
    return str(info["outputs"][0])


class FakeCycle:
    """An ``AdjointCycle`` that reports every step and returns a small sensitivity at once."""

    made: list[FakeCycle] = []
    during: Any = None  # called with the session inside the first evaluation, when set

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
    ) -> None:
        self.live = live
        self.part = part
        self.on_step = on_step
        self.count = 0
        self.aborted = False
        FakeCycle.made.append(self)

    def abort(self) -> bool:
        self.aborted = True
        return True

    def __call__(self, project: Any) -> tuple[float, Any, Any, Any, Any]:  # Any: Project, arrays
        self.count += 1
        for number, name in enumerate(shape_cycle.STEPS, start=1):
            info = {
                "cycle": self.count,
                "step_number": number,
                "steps": len(shape_cycle.STEPS),
                "elapsed": 0.0,
            }
            self.on_step(name, info)
        if self.count == 1 and FakeCycle.during is not None:
            FakeCycle.during(self.live, self.part)
        one = np.zeros((1, 3))
        return float(self.count), one, one, np.ones(1), np.ones(1)


@pytest.fixture
def fake_cycle(monkeypatch: pytest.MonkeyPatch) -> type[FakeCycle]:
    FakeCycle.made = []
    FakeCycle.during = None
    monkeypatch.setattr(shape_cycle, "AdjointCycle", FakeCycle)

    def gradient(*args: Any) -> dict[str, Any]:  # Any: the stand-in's arguments
        return {args[-1][0]: SimpleNamespace(value=1.0, covered=1.0)}

    monkeypatch.setattr(sensitivity, "wall_gradient", gradient)
    return FakeCycle


def test_a_gradient_check_numbers_its_three_evaluations(
    tmp_path: Path, fake_cycle: type[FakeCycle]
) -> None:
    session = new_session(tmp_path)
    part = _sphere(session)
    steps: list[dict[str, Any]] = []
    session.events.subscribe("shape.step", lambda event, data: steps.append(dict(data)))
    result = session.execute("shape.check", part=part, parameter="R", wait=True)
    assert result["status"] == "done"
    assert {s["check"] for s in steps} == {1, 2, 3}
    assert {s["checks"] for s in steps} == {3}
    assert [s["check_run"] for s in steps if s["step"] == "open"] == ["base", "plus", "minus"]
    assert all(s["run"] == "check" for s in steps)
    first = steps[0]
    assert first["step"] == "open" and first["step_number"] == 1
    assert first["steps"] == len(shape_cycle.STEPS) and "elapsed" in first
    GeometryStore.of(session).close()


def test_a_check_can_be_cancelled_between_and_during_evaluations(
    tmp_path: Path, fake_cycle: type[FakeCycle]
) -> None:
    session = new_session(tmp_path)
    part = _sphere(session)
    answers: list[dict[str, Any]] = []

    def cancel(live: Session, which: str) -> None:
        answers.append(live.execute("shape.cancel", part=which, now=True))

    fake_cycle.during = cancel
    checked: list[dict[str, Any]] = []
    session.events.subscribe("shape.checked", lambda event, data: checked.append(dict(data)))
    result = session.execute("shape.check", part=part, parameter="R", wait=True)
    assert result["status"] == "cancelled"
    assert result["completed"] == 1 and result["checks"] == 3
    assert answers == [{"part": part, "cancelling": True, "now": True, "aborted": True}]
    assert fake_cycle.made[0].aborted and fake_cycle.made[0].count == 1
    assert checked[0]["status"] == "cancelled"
    # Nothing runs now: a cancel finds no cycle to stop, and a new check starts afresh.
    assert session.execute("shape.cancel", part=part, now=True)["aborted"] is False
    fake_cycle.during = None
    assert session.execute("shape.check", part=part, parameter="R", wait=True)["status"] == "done"
    GeometryStore.of(session).close()


def test_a_cycle_reports_its_steps_and_stops_when_aborted() -> None:
    import threading

    from stokes.explore.errors import Cancelled

    heard: list[tuple[str, dict[str, Any]]] = []
    # The real class without its constructor (which needs a saved, tagged project).
    cycle = object.__new__(shape_cycle.AdjointCycle)
    cycle._count = 2
    cycle._began = 0.0
    cycle._on_step = lambda name, info: heard.append((name, info))
    cycle._aborted = threading.Event()
    cycle._lock = threading.Lock()
    cycle._session = None
    cycle._step("mesh")
    name, info = heard[0]
    assert name == "mesh" and info["cycle"] == 2
    assert info["step_number"] == shape_cycle.STEPS.index("mesh") + 1
    assert info["steps"] == len(shape_cycle.STEPS) and info["elapsed"] >= 0
    assert cycle.abort() is False  # no cycle session under way: nothing to cancel
    with pytest.raises(Cancelled):
        cycle._step("solve")


def test_shape_variables_give_the_body_and_the_quantity(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    part = _sphere(session)
    found = session.execute("shape.variables", part=part)["variables"]
    radius = next(v for v in found if v["reference"].endswith(".radius"))
    assert radius["quantity"] == "Radius" and radius["body"] != ""
    assert radius["label"] == f"{radius['body']} {radius['quantity']}"
    assert radius["unit"] == "m"
    parameter = next(v for v in found if v["reference"] == "R")
    assert parameter["body"] == "parameter" and parameter["quantity"] == "R"
    GeometryStore.of(session).close()
