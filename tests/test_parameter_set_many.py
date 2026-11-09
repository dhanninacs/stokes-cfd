"""`parameter.set_many`: one undo step for many nodes, and an atomic refusal.

Claude's 1400 file: *"editing a field applies the new value to ALL selected items (one undo step,
one journal record)"* and *"partial failure refuses the whole change with the reason (no
half-applied edits)"*.

**Why a command and not a loop in the page.** Rule 4: user-visible behaviour is an API command
before it has a widget. A loop in the front end would be one undo step per row, so Undo would give
back one part at a time - which for "set the size on three parts" is four undos, not one.
"""

from __future__ import annotations

import pytest

from stokes.api import Session
from stokes.errors import StokesError


@pytest.fixture
def three_parts(session: Session) -> list[str]:
    session.execute("node.add", parent="/geometry", type="group", name="parts")
    return [
        session.execute("node.add", parent="/geometry/parts", type="part", name=name)["id"]
        for name in ("pipe", "inlet_flange", "outlet_flange")
    ]


def test_it_sets_the_same_parameter_on_every_node(session: Session, three_parts: list[str]) -> None:
    out = session.execute("parameter.set_many", nodes=three_parts, name="color", value="#ff0000")
    assert out["nodes"] == three_parts
    project = session.require_project()
    for node_id in three_parts:
        assert project.find(node_id).params["color"] == "#ff0000"


def test_it_is_ONE_undo_step(session: Session, three_parts: list[str]) -> None:
    """**The whole point**: ONE undo gives back all three at once, not one at a time.

    Asserted by comparing against what the parts held BEFORE the change rather than against `None`:
    a part has a default colour, so "unset" is a value and guessing it in the test would pin the
    default instead of the behaviour.
    """
    project = session.require_project()
    before = [project.find(i).params["color"] for i in three_parts]
    session.execute("parameter.set_many", nodes=three_parts, name="color", value="#ff0000")
    after = [session.require_project().find(i).params["color"] for i in three_parts]
    assert after == ["#ff0000"] * 3
    session.execute("history.undo")
    assert [session.require_project().find(i).params["color"] for i in three_parts] == before


def test_a_partial_failure_refuses_the_whole_change(
    session: Session, three_parts: list[str]
) -> None:
    """**No half-applied edits.** The third node has no such parameter, so nothing changes at all.

    `Composite.apply` reverts what it applied before re-raising, which is why the first two are
    untouched and not merely left as they were mid-loop.
    """
    session.execute("node.add", parent="/geometry", type="group", name="other")
    group = session.execute("node.add", parent="/geometry/other", type="group", name="g")["id"]
    project = session.require_project()
    before = [project.find(i).params["color"] for i in three_parts]
    with pytest.raises(StokesError):
        session.execute(
            "parameter.set_many", nodes=[*three_parts, group], name="color", value="#00ff00"
        )
    after = [session.require_project().find(i).params["color"] for i in three_parts]
    assert after == before, "a refused bulk change must leave every node as it was"


def test_an_empty_list_is_refused(session: Session) -> None:
    with pytest.raises(StokesError):
        session.execute("parameter.set_many", nodes=[], name="color", value="#000000")
