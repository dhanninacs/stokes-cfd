from __future__ import annotations

import pytest

from stokes.errors import ModelError
from stokes.model import (
    AddNode,
    Composite,
    Node,
    Project,
    RemoveNode,
    RenameNode,
    SetDisplayUnits,
    SetParam,
    UndoStack,
    command_from_record,
)

PARAMS = "/physics/parameters"


def test_add_undo_redo_keeps_id() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    cmd = AddNode(PARAMS, "parameter", "U", {"dimension": "velocity", "value": "1 [m/s]"})
    result = stack.push(cmd, p)
    node_id = result["id"]
    assert p.find(node_id) is not None
    stack.undo(p)
    assert p.find(node_id) is None
    stack.redo(p)
    assert p.get(node_id).name == "U"
    assert stack.undo_text == "add parameter U"


def test_set_param_undo_restores_expression() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    stack.push(AddNode(PARAMS, "parameter", "a", {"dimension": "length", "value": "1 [m]"}), p)
    stack.push(AddNode(PARAMS, "parameter", "b", {"dimension": "length", "value": "a * 2"}), p)
    b = p.get(f"{PARAMS}/b")
    stack.push(SetParam(f"{PARAMS}/b", "value", "5 [m]"), p)
    assert b.params["value"].value == 5.0 and b.params["value"].expr is None
    stack.undo(p)
    assert b.params["value"].expr == "a * 2" and b.params["value"].value == 2.0
    stack.push(SetParam(f"{PARAMS}/a", "value", "3 [m]"), p)
    assert b.params["value"].value == 6.0
    stack.undo(p)
    assert b.params["value"].value == 2.0


def quantity(node: Node, name: str = "value") -> tuple[float, str, str | None]:
    stored = node.params[name]
    return (stored.value, stored.unit, stored.expr)


def test_undoing_a_dimension_change_gives_back_the_quantities_it_reset() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    stack.push(AddNode(PARAMS, "parameter", "U", {"dimension": "velocity", "value": "2 [m/s]"}), p)
    stack.push(AddNode(PARAMS, "parameter", "V", {"dimension": "velocity", "value": "U * 2"}), p)
    pressure = "0.5 * 1.2 [kg/m**3] * U**2"
    stack.push(AddNode(PARAMS, "parameter", "dp", {"dimension": "pressure", "value": pressure}), p)
    u, v, dp = (p.get(f"{PARAMS}/{name}") for name in ("U", "V", "dp"))
    stack.push(SetParam(u.id, "dimension", "length"), p)
    assert quantity(u) == (0.0, "m", None)
    assert v.status == "failed" and dp.status == "failed"  # they take U, now a length
    stack.undo(p)
    assert u.params["dimension"] == "velocity" and quantity(u) == (2.0, "m/s", None)
    assert v.status == "ok" and v.params["value"].value == pytest.approx(4.0)
    assert dp.status == "ok" and dp.params["value"].value == pytest.approx(2.4)
    stack.redo(p)  # redo resets them again
    assert u.params["dimension"] == "length" and quantity(u) == (0.0, "m", None)
    assert v.status == "failed" and dp.status == "failed"
    stack.undo(p)
    assert quantity(u) == (2.0, "m/s", None) and v.status == "ok" and dp.status == "ok"


def test_undoing_a_dimension_change_brings_an_expression_back_alive() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    stack.push(AddNode(PARAMS, "parameter", "a", {"dimension": "velocity", "value": "1 [m/s]"}), p)
    stack.push(AddNode(PARAMS, "parameter", "b", {"dimension": "velocity", "value": "a * 2"}), p)
    a, b = p.get(f"{PARAMS}/a"), p.get(f"{PARAMS}/b")
    stack.push(SetParam(b.id, "dimension", "length"), p)
    assert quantity(b) == (0.0, "m", None)
    stack.undo(p)
    assert quantity(b) == (2.0, "m/s", "a * 2")
    stack.push(SetParam(a.id, "value", "3 [m/s]"), p)  # b follows a again
    assert b.params["value"].value == pytest.approx(6.0) and b.status == "ok"


def test_undoing_a_dimension_change_survives_an_expression_that_no_longer_holds() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    stack.push(AddNode(PARAMS, "parameter", "a", {"dimension": "velocity", "value": "1 [m/s]"}), p)
    stack.push(AddNode(PARAMS, "parameter", "b", {"dimension": "velocity", "value": "a * 2"}), p)
    b = p.get(f"{PARAMS}/b")
    stack.push(RemoveNode(f"{PARAMS}/a"), p)  # b's parameter is gone: it fails
    assert b.status == "failed"
    stack.push(SetParam(b.id, "dimension", "length"), p)  # the reset leaves nothing to fail
    assert b.status == "ok"
    stack.undo(p)  # does not raise: the expression comes back, and fails as it did
    assert b.params["dimension"] == "velocity" and b.params["value"].expr == "a * 2"
    assert b.status == "failed" and "a" in b.meta["error"]
    stack.undo(p)  # the parameter returns, and b with it
    assert b.status == "ok" and b.params["value"].value == pytest.approx(2.0)


def test_a_dimension_change_keeps_its_journal_record() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    stack.push(AddNode(PARAMS, "parameter", "U", {"dimension": "velocity", "value": "2 [m/s]"}), p)
    change = SetParam(f"{PARAMS}/U", "dimension", "length")
    stack.push(change, p)
    record = {
        "command": "set_param",
        "node": f"{PARAMS}/U",
        "param": "dimension",
        "value": "length",
    }
    assert change.to_record() == record
    stack.undo(p)
    stack.redo(p)
    assert change.to_record() == record  # what undo keeps is not part of it
    q = Project.new("demo")
    q.add(PARAMS, "parameter", "U", {"dimension": "velocity", "value": "2 [m/s]"})
    command_from_record(record).apply(q)  # the record replays the same reset
    assert quantity(q.get(f"{PARAMS}/U")) == (0.0, "m", None)


def test_remove_undo_restores_subtree() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    stack.push(AddNode(PARAMS, "parameter", "a", {"dimension": "length", "value": "1 [m]"}), p)
    stack.push(AddNode(PARAMS, "parameter", "b", {"dimension": "length", "value": "a * 2"}), p)
    a_id = p.get(f"{PARAMS}/a").id
    stack.push(RemoveNode(f"{PARAMS}/a"), p)
    assert p.find(a_id) is None and p.get(f"{PARAMS}/b").status == "failed"
    stack.undo(p)
    assert p.get(a_id).name == "a" and p.get(f"{PARAMS}/b").status == "ok"


def test_rename_undo() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    stack.push(AddNode(PARAMS, "parameter", "a"), p)
    stack.push(RenameNode(f"{PARAMS}/a", "alpha"), p)
    assert p.find(f"{PARAMS}/alpha") is not None
    stack.undo(p)
    assert p.find(f"{PARAMS}/a") is not None


def test_undoing_the_first_write_of_a_parameter_the_node_never_held() -> None:
    """A node saved before a parameter existed holds no value for it (part.opacity, T-021): the
    first write is undone by taking the parameter off again, not by writing None."""
    p = Project.new("demo")
    stack = UndoStack()
    p.add("/geometry", "group", "parts")
    part = p.add("/geometry/parts", "part", "old")
    del part.params["opacity"]  # as a file from before the parameter loads
    stack.push(SetParam(part.id, "opacity", 0.5), p)
    stack.push(SetParam(part.id, "opacity", 0.25), p)
    stack.undo(p)
    assert part.params["opacity"] == 0.5
    stack.undo(p)
    assert "opacity" not in part.params
    stack.redo(p)
    assert part.params["opacity"] == 0.5


def test_composite_rolls_back_on_failure() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    before = [n.id for n in p.nodes()]
    composite = Composite(
        "two adds",
        [AddNode(PARAMS, "parameter", "x"), AddNode(PARAMS, "parameter", "x")],
    )
    with pytest.raises(ModelError):
        stack.push(composite, p)
    assert [n.id for n in p.nodes()] == before
    assert not stack.can_undo


def test_records_replay_with_same_ids() -> None:
    p = Project.new("demo")
    stack = UndoStack()
    cmd = AddNode(PARAMS, "parameter", "a", {"dimension": "length", "value": "1 [m]"})
    stack.push(cmd, p)
    stack.push(SetDisplayUnits("mm"), p)
    records = [cmd.to_record(), SetDisplayUnits("mm").to_record()]
    q = Project.new("demo")
    for record in records:
        command_from_record(record).apply(q)
    assert q.get(f"{PARAMS}/a").id == cmd.node_id
    assert q.display_units == "mm"


def test_undo_stack_limit() -> None:
    p = Project.new("demo")
    stack = UndoStack(limit=2)
    for name in ("a", "b", "c"):
        stack.push(AddNode(PARAMS, "parameter", name), p)
    assert stack.undo(p) is not None
    assert stack.undo(p) is not None
    assert stack.undo(p) is None
    assert stack.redo_text == "add parameter b"
