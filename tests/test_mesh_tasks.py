"""The guided-meshing tasks and the dry-run precheck (brief M-501).

The tasks answer "what is next" without a mesh run; the precheck answers "what will go wrong
before it runs". Both are read-only and both go through `Session.execute` like every other
command, so a refusal or a wrong tree is caught the same way the mesh commands are.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.errors import StokesError


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """A session on a fresh project, with a home of its own so nothing reads a real one."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    made = Session()
    made.execute("project.new", name="mesh")
    return made


def _task_ids(session: Session) -> list[str]:
    return [task["id"] for task in session.execute("mesh.tasks")["tasks"]]


def test_tasks_lists_the_nine_tasks_in_the_briefs_order(session: Session) -> None:
    """`mesh.tasks` is the stepper's whole input, so its order and ids are the plan's."""
    assert _task_ids(session) == [
        "geometry",
        "bodies",
        "boundaries",
        "sizing",
        "surface",
        "layers",
        "volume",
        "quality",
        "fidelity",
    ]


def test_tasks_says_what_is_current_and_next(session: Session) -> None:
    """On an empty project nothing is done, so the stepper points at the first task."""
    reply = session.execute("mesh.tasks")
    assert reply["current"] == "geometry"
    assert reply["next"] == "geometry"
    assert reply["tasks"][0]["state"] in ("todo", "idle")


def test_tasks_says_the_mesher_and_host(session: Session) -> None:
    """The stepper's head shows the mesher and host, so they come from the settings."""
    session.execute("mesh.configure", mesher="snappy")
    reply = session.execute("mesh.tasks")
    assert reply["mesher"] == "snappy"
    assert reply["host"] == "local"


def test_precheck_refuses_nothing_on_an_empty_project(session: Session) -> None:
    """Nothing is configured yet, so the precheck reports the missing domain, not an error."""
    reply = session.execute("mesh.precheck")
    assert "findings" in reply
    assert isinstance(reply["findings"], list)
    codes = [finding["code"] for finding in reply["findings"]]
    assert "mesh.no_domain" in codes


def test_body_role_assigns_the_fluid_and_clears_it_from_the_previous_holder(
    session: Session,
) -> None:
    """**Fluid is one slot**: assigning it to a second body moves it, never duplicates it."""
    first = session.execute(
        "operation.create",
        type="primitive",
        params={
            "shape": "box",
            "size": ["10 [mm]", "10 [mm]", "10 [mm]"],
            "position": ["0 [mm]", "0 [mm]", "0 [mm]"],
        },
        wait=True,
    )["outputs"][0]
    second = session.execute(
        "operation.create",
        type="primitive",
        params={
            "shape": "box",
            "size": ["20 [mm]", "20 [mm]", "20 [mm]"],
            "position": ["30 [mm]", "0 [mm]", "0 [mm]"],
        },
        wait=True,
    )["outputs"][0]

    session.execute("mesh.body_role", part=first, role="fluid")
    assert session.require_project().find(first).params["simulate"] is True
    settings = session.require_project().find("/mesh/settings")
    assert settings is not None
    assert str(settings.params.get("domain_part")) == first

    session.execute("mesh.body_role", part=second, role="fluid")
    assert str(settings.params.get("domain_part")) == second
    # The first body is no longer the fluid, but it is not silently left out either:
    assert session.require_project().find(first).params["simulate"] is True


def test_body_role_left_out_clears_the_domain_slot(session: Session) -> None:
    """A body that becomes left out must not keep holding the domain slot."""
    made = session.execute(
        "operation.create",
        type="primitive",
        params={
            "shape": "box",
            "size": ["10 [mm]", "10 [mm]", "10 [mm]"],
            "position": ["0 [mm]", "0 [mm]", "0 [mm]"],
        },
        wait=True,
    )["outputs"][0]
    session.execute("mesh.body_role", part=made, role="fluid")
    session.execute("mesh.body_role", part=made, role="left_out")
    settings = session.require_project().find("/mesh/settings")
    assert settings is not None
    assert settings.params.get("domain_part") in (None, "")
    assert session.require_project().find(made).params["simulate"] is False


def test_body_role_refuses_an_unknown_role(session: Session) -> None:
    """The four roles are the brief's vocabulary; anything else is a refusal, not a guess."""
    with pytest.raises(StokesError) as excinfo:
        session.execute("mesh.body_role", part="x", role="hole_in_the_flow")
    assert "fluid, hole, solid, left_out" in str(excinfo.value)


def _three_boxes(session: Session) -> list[str]:
    """Three parts, made the way the UI's own operations make them."""
    out = []
    for index in range(3):
        made = session.execute(
            "operation.create",
            type="primitive",
            params={
                "shape": "box",
                "size": ["10 [mm]", "10 [mm]", "10 [mm]"],
                "position": [f"{20 * index} [mm]", "0 [mm]", "0 [mm]"],
            },
            wait=True,
        )["outputs"][0]
        out.append(str(made))
    return out


def test_body_role_bodies_applies_one_choice_to_all_in_one_undo_step(session: Session) -> None:
    """**The multi-select's one call** (brief M-502): `bodies` names several parts, one choice
    lands on all of them, and ONE undo step takes it all back."""
    a, b, c = _three_boxes(session)
    session.execute("mesh.body_role", bodies=[a, b, c], role="solid")
    project = session.require_project()
    assert all(project.find(pid).params["simulate"] is True for pid in (a, b, c))
    # One undo step: every body is back to simulated-by-default in one go.
    session.execute("history.undo")
    project = session.require_project()
    assert all(project.find(pid).params["simulate"] is True for pid in (a, b, c))


def test_body_role_bodies_refuses_fluid_for_more_than_one(session: Session) -> None:
    """The fluid slot is one part by definition; a multi-selection asking for it is refused with
    the reason the page shows."""
    a, b, _ = _three_boxes(session)
    with pytest.raises(StokesError, match="only one body can be the fluid"):
        session.execute("mesh.body_role", bodies=[a, b], role="fluid")


def test_body_role_bodies_and_part_are_either_or(session: Session) -> None:
    a, _, _ = _three_boxes(session)
    with pytest.raises(StokesError, match="either"):
        session.execute("mesh.body_role", part=a, bodies=[a], role="solid")
    with pytest.raises(StokesError, match="part or parts"):
        session.execute("mesh.body_role", role="solid")
    with pytest.raises(StokesError, match="non-empty list"):
        session.execute("mesh.body_role", bodies=[], role="solid")


def test_the_layers_row_says_what_the_settings_hold(session: Session) -> None:
    """The row said "off" whatever the settings held, so Full mode contradicted the Guided
    card's "3 layers on every wall" on the same project."""

    def row() -> str:
        tasks = session.execute("mesh.tasks")["tasks"]
        return str(next(t for t in tasks if t["id"] == "layers")["row"])

    session.execute("mesh.configure", mesher="snappy", values={"layers": 3})
    assert row() == "3 layers on walls"
    session.execute("mesh.configure", values={"layers": 0})
    assert row() == "off"
