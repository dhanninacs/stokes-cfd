"""`parameter.update`: several parameters of one node as one undo step (ADR-0036 addendum).

The sketcher's Finish writes a drawn sketch's plane, dimensions, entities and profile together. As
four `parameter.set` calls it was four undo steps, and the first undo ("set profile") changed
nothing visible. This command is one journal record and one undo step, and a refusal of any value
leaves the node as it was.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Journal, Session, replay
from stokes.errors import StokesError

SQUARE = [
    {"kind": "line", "a": [0, 0], "b": ["W", 0]},
    {"kind": "line", "a": ["W", 0], "b": ["W", "W"]},
    {"kind": "line", "a": ["W", "W"], "b": [0, "W"]},
    {"kind": "line", "a": [0, "W"], "b": [0, 0]},
]


def _sketch(session: Session) -> str:
    out = session.execute(
        "node.add", parent="/geometry", type="implicit_sketch", name="sk", params={}
    )
    return str(out["id"])


def _finish(session: Session, node: str) -> None:
    session.execute(
        "parameter.update",
        node=node,
        values={
            "plane": "yz",
            "dimensions": {"W": "20 [mm]"},
            "entities": SQUARE,
            "profile": "custom",
        },
    )


def test_it_sets_every_value_in_one_undo_step(session: Session) -> None:
    node = _sketch(session)
    project = session.require_project()
    before = {k: project.get(node).params.get(k) for k in ("plane", "entities", "profile")}
    _finish(session, node)
    sk = session.require_project().get(node)
    assert sk.params["plane"] == "yz"
    assert sk.params["profile"] == "custom"
    session.execute("history.undo")
    sk = session.require_project().get(node)
    assert {k: sk.params.get(k) for k in before} == before
    session.execute("history.redo")
    assert session.require_project().get(node).params["plane"] == "yz"


def test_a_refused_value_changes_nothing(session: Session) -> None:
    node = _sketch(session)
    plane = session.require_project().get(node).params["plane"]
    with pytest.raises(StokesError):
        session.execute(
            "parameter.update", node=node, values={"plane": "zx", "profile": "no such profile"}
        )
    assert session.require_project().get(node).params["plane"] == plane


def test_empty_values_are_refused(session: Session) -> None:
    node = _sketch(session)
    with pytest.raises(StokesError):
        session.execute("parameter.update", node=node, values={})


def test_the_journal_replays_it(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    s = Session(journal_path=journal)
    s.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    node = _sketch(s)
    _finish(s, node)
    records = Journal.read(journal)
    assert [r["command"] for r in records][-1] == "parameter.update"
    fresh = Session()
    fresh.execute("project.new", name="other")
    replay(fresh, records[1:])
    sk = fresh.require_project().get("/geometry/sk")
    assert sk.params["plane"] == "yz"
    assert sk.params["profile"] == "custom"
