"""`geometry.simulate`: what goes into the simulation (the plan §2, T-221).

**A different control from the eye**, which is the whole reason it exists: `visible` is display-only
and the mesher never reads it, while this decides whether a body is meshed, compiled
and checked.
One
word meaning two things is exactly how the eye and the row menu came to disagree about Hide, so the
two are separate parameters with separate commands.

The tests are mostly about the three ways this can quietly do the wrong thing: resolving a group to
nothing, accepting something that is not a body, and pushing an undo step for a call that changed
nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.errors import StokesError


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOKES_USER_HOME", str(tmp_path / "user"))
    found = Session()
    found.execute("project.new", name="duct", path=str(tmp_path / "p.stokes"))
    project = found.require_project()
    # The parts folder is made by an import in real life; here it is made directly, because what is
    # under test is the command rather than the importer.
    project.add("/geometry", "group", "parts", node_id="g_parts")
    for name, node_id in (("housing", "n_h"), ("fan", "n_f")):
        project.add("g_parts", "part", name, node_id=node_id)
    return found


def flags(session: Session) -> dict[str, bool]:
    project = session.require_project()
    return {n.name: bool(n.params.get("simulate", True)) for n in project.find("g_parts").children}


def test_bodies_are_IN_the_simulation_by_default(session: Session) -> None:
    """The default matters: a user who never touches this must get every body meshed, so the flag
    defaults to true rather than being absent until asked for.
    """
    assert flags(session) == {"housing": True, "fan": True}


def test_leaving_one_body_out_changes_only_that_body(session: Session) -> None:
    reply = session.execute("geometry.simulate", nodes=["/geometry/parts/fan"], simulate=False)
    assert reply["changed"] == ["n_f"]
    assert flags(session) == {"housing": True, "fan": False}
    # And putting it back is the same command with the other value.
    session.execute("geometry.simulate", nodes=["/geometry/parts/fan"], simulate=True)
    assert flags(session) == {"housing": True, "fan": True}


def test_a_call_that_changes_NOTHING_pushes_no_undo_step(session: Session) -> None:
    """**The idempotence the journal depends on.** A repeated call that pushed a step would fill the
    undo stack with no-ops, and Ctrl+Z would stop doing anything visible.
    """
    before = session.undo.depth() if hasattr(session.undo, "depth") else None
    reply = session.execute("geometry.simulate", nodes=["/geometry/parts/fan"], simulate=True)
    assert reply["changed"] == []
    assert reply["unchanged"] == ["n_f"]
    if before is not None:
        assert session.undo.depth() == before


def test_a_group_resolves_to_the_parts_beneath_it(session: Session) -> None:
    """**The sentence a user says is "leave this assembly out".** A multi-solid import makes
    a group, and resolving it to nothing would report success while changing nothing - which
    is the failure this test exists for.
    """
    reply = session.execute("geometry.simulate", nodes=["/geometry/parts"], simulate=False)
    assert sorted(reply["changed"]) == ["n_f", "n_h"]
    assert flags(session) == {"housing": False, "fan": False}


def test_a_tag_is_refused_with_a_reason(session: Session) -> None:
    """A tag is not a body, so there is nothing to leave out.

    **Refused rather than ignored**, because "I left the walls out" and nothing happening is the
    worst outcome: the user believes a mesh will skip them and it will not.
    """
    session.execute("tag.create", name="wall")
    with pytest.raises(StokesError) as caught:
        session.execute("geometry.simulate", nodes=["/geometry/tags/wall"], simulate=False)
    assert "not a part" in str(caught.value)


def test_the_command_needs_a_node_and_a_value(session: Session) -> None:
    """An empty list is a mistake rather than "every body", which is the reading that would silently
    leave out the whole project.
    """
    with pytest.raises(StokesError):
        session.execute("geometry.simulate", nodes=[], simulate=False)
