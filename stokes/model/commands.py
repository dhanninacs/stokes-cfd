"""Undoable commands over a :class:`~stokes.model.project.Project`.

Every mutation the application performs is one of these. A command records
what it needs to revert itself and serialises to a journal record that
recreates the same change, including generated node ids, in another session.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from stokes.errors import ModelError, ValidationError
from stokes.model.nodes import Node, QuantityValue
from stokes.model.project import Project


class Command(ABC):
    """A reversible change. ``apply`` returns a JSON-able result for the caller."""

    name: ClassVar[str] = "command"

    @abstractmethod
    def apply(self, project: Project) -> Any: ...  # Any: JSON-able result

    @abstractmethod
    def revert(self, project: Project) -> None: ...

    @abstractmethod
    def to_record(self) -> dict[str, Any]: ...

    def describe(self) -> str:
        return self.name


@dataclass
class SetParam(Command):
    """Set one parameter. Changing a node's ``dimension`` resets its quantities that take that
    dimension (``Project.set_param``); this command keeps them, so undo gives back what the
    change took, and redo resets them again. The journal record is the same either way."""

    name: ClassVar[str] = "set_param"
    node: str
    param: str
    value: Any  # Any: JSON value or quantity text
    node_id: str | None = None
    node_path: str | None = None
    old: Any = None  # Any: previous JSON value
    reset: dict[str, QuantityValue] | None = field(default=None, repr=False, compare=False)
    absent: bool = field(default=False, repr=False, compare=False)

    def apply(self, project: Project) -> Any:
        target = project.get(self.node)
        self.node_id = target.id
        self.node_path = target.path
        self.reset = project.dynamic_quantities(target.id) if self.param == "dimension" else None
        # A node saved before the parameter existed holds no value for it: undo takes it off again.
        self.absent = self.param not in target.params
        self.old = project.set_param(target.id, self.param, self.value)
        return target.summary()

    def revert(self, project: Project) -> None:
        if self.absent:
            project.unset_param(self.node_id or self.node, self.param)
        else:
            # validate=False: undo restores the value as it was, and one written before a
            # structural check began may no longer pass it, so the check is skipped on the
            # way back.
            project.set_param(self.node_id or self.node, self.param, self.old, validate=False)
        if self.reset:
            project.restore_quantities(self.node_id or self.node, self.reset)

    def to_record(self) -> dict[str, Any]:
        # Records reference nodes by path (stable across projects); created ids are kept.
        return {
            "command": self.name,
            "node": self.node_path or self.node,
            "param": self.param,
            "value": self.value,
        }

    def describe(self) -> str:
        return f"set {self.param}"


@dataclass
class AddNode(Command):
    """Add one node. The old name of a renamed type (ADR-0021 decision 2.4) is stored, recorded
    and described as the current one, so the tree only ever holds current types."""

    name: ClassVar[str] = "add_node"
    parent: str
    type: str
    node_name: str
    params: dict[str, Any] = field(default_factory=dict)  # Any: JSON values
    node_id: str | None = None
    index: int | None = None
    parent_id: str | None = None
    parent_path: str | None = None

    def apply(self, project: Project) -> Any:
        parent = project.get(self.parent)
        self.parent_id = parent.id
        self.parent_path = parent.path
        self.type = project.registry.canonical(self.type)
        node = project.add(
            parent.id,
            self.type,
            self.node_name,
            self.params,
            node_id=self.node_id,
            index=self.index,
        )
        self.node_id = node.id
        self.index = parent.children.index(node)
        return node.summary()

    def revert(self, project: Project) -> None:
        if self.node_id is None:
            raise ModelError("cannot revert an AddNode that was never applied")
        project.remove(self.node_id)

    def to_record(self) -> dict[str, Any]:
        return {
            "command": self.name,
            "parent": self.parent_path or self.parent,
            "type": self.type,
            "name": self.node_name,
            "params": self.params,
            "node_id": self.node_id,
            "index": self.index,
        }

    def describe(self) -> str:
        return f"add {self.type} {self.node_name}"


@dataclass
class RemoveNode(Command):
    name: ClassVar[str] = "remove_node"
    node: str
    node_id: str | None = None
    node_path: str | None = None
    detached: Node | None = field(default=None, repr=False)
    parent_id: str | None = None
    index: int | None = None

    def apply(self, project: Project) -> Any:
        target = project.get(self.node)
        self.node_id = target.id
        self.node_path = target.path
        summary = target.summary()
        self.detached, self.parent_id, self.index = project.remove(target.id)
        return summary

    def revert(self, project: Project) -> None:
        if self.detached is None or self.parent_id is None:
            raise ModelError("cannot revert a RemoveNode that was never applied")
        project.attach(self.parent_id, self.detached, self.index)

    def to_record(self) -> dict[str, Any]:
        return {"command": self.name, "node": self.node_path or self.node}

    def describe(self) -> str:
        return "remove node"


@dataclass
class RenameNode(Command):
    name: ClassVar[str] = "rename_node"
    node: str
    new_name: str
    node_id: str | None = None
    node_path: str | None = None
    old_name: str | None = None

    def apply(self, project: Project) -> Any:
        target = project.get(self.node)
        self.node_id = target.id
        self.node_path = target.path
        self.old_name = project.rename(target.id, self.new_name)
        return target.summary()

    def revert(self, project: Project) -> None:
        if self.old_name is None:
            raise ModelError("cannot revert a RenameNode that was never applied")
        project.rename(self.node_id or self.node, self.old_name)

    def to_record(self) -> dict[str, Any]:
        return {"command": self.name, "node": self.node_path or self.node, "name": self.new_name}

    def describe(self) -> str:
        return f"rename to {self.new_name}"


@dataclass
class ConvertGroup(Command):
    """Give a plain group a type in place (``Project.convert_group``): a boundary or a region made
    as a plain group before typed ones existed becomes a ``boundary`` or a ``region`` with the
    same id, name, place and children (ADR-0022 decision 2.9). Undo makes it the plain group it
    was."""

    name: ClassVar[str] = "convert_group"
    node: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)  # Any: JSON values
    node_id: str | None = None
    node_path: str | None = None
    status: str = "ok"
    meta: dict[str, Any] = field(default_factory=dict, repr=False)  # Any: the group's meta
    old: dict[str, Any] = field(default_factory=dict, repr=False)  # Any: the group's params

    def apply(self, project: Project) -> Any:
        target = project.get(self.node)
        self.node_id = target.id
        self.node_path = target.path
        self.status = target.status
        self.meta = copy.deepcopy(target.meta)
        self.old = copy.deepcopy(target.params)
        node = project.convert_group(target.id, self.type, self.params)
        self.type = node.type
        return node.summary()

    def revert(self, project: Project) -> None:
        if self.node_id is None:
            raise ModelError("cannot revert a ConvertGroup that was never applied")
        project.restore_group(self.node_id, self.status, self.meta, self.old)

    def to_record(self) -> dict[str, Any]:
        return {
            "command": self.name,
            "node": self.node_path or self.node,
            "type": self.type,
            "params": self.params,
        }

    def describe(self) -> str:
        return f"make {self.type}"


@dataclass
class SetDisplayUnits(Command):
    name: ClassVar[str] = "set_display_units"
    system: str
    old: str | None = None

    def apply(self, project: Project) -> Any:
        self.old = project.set_display_units(self.system)
        return {"display_units": self.system}

    def revert(self, project: Project) -> None:
        if self.old is not None:
            project.set_display_units(self.old)

    def to_record(self) -> dict[str, Any]:
        return {"command": self.name, "system": self.system}


@dataclass
class Composite(Command):
    """Several commands applied as one undo step."""

    name: ClassVar[str] = "composite"
    label: str
    commands: list[Command] = field(default_factory=list)
    applied: int = 0

    def apply(self, project: Project) -> Any:
        results = []
        self.applied = 0
        try:
            for cmd in self.commands:
                results.append(cmd.apply(project))
                self.applied += 1
        except Exception:
            for cmd in reversed(self.commands[: self.applied]):
                cmd.revert(project)
            self.applied = 0
            raise
        return results

    def revert(self, project: Project) -> None:
        for cmd in reversed(self.commands[: self.applied]):
            cmd.revert(project)

    def to_record(self) -> dict[str, Any]:
        return {
            "command": self.name,
            "label": self.label,
            "commands": [c.to_record() for c in self.commands],
        }

    def describe(self) -> str:
        return self.label


def command_from_record(record: dict[str, Any]) -> Command:
    """Rebuild a command from its journal record."""
    kind = record.get("command")
    command: Command
    match kind:
        case "set_param":
            command = SetParam(record["node"], record["param"], record["value"])
        case "add_node":
            command = AddNode(
                record["parent"],
                record["type"],
                record["name"],
                dict(record.get("params") or {}),
                node_id=record.get("node_id"),
                index=record.get("index"),
            )
        case "remove_node":
            command = RemoveNode(record["node"])
        case "rename_node":
            command = RenameNode(record["node"], record["name"])
        case "convert_group":
            command = ConvertGroup(record["node"], record["type"], dict(record.get("params") or {}))
        case "set_display_units":
            command = SetDisplayUnits(record["system"])
        case "composite":
            command = Composite(
                str(record.get("label", "")), [command_from_record(r) for r in record["commands"]]
            )
        case _:
            raise ValidationError(f"unknown command record {kind!r}")
    return command


class UndoStack:
    """Applies commands and keeps undo and redo histories."""

    def __init__(self, limit: int = 1000) -> None:
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self._limit = limit

    def push(self, command: Command, project: Project) -> Any:
        result = command.apply(project)
        self._undo.append(command)
        if len(self._undo) > self._limit:
            del self._undo[0]
        self._redo.clear()
        return result

    def undo(self, project: Project) -> Command | None:
        if not self._undo:
            return None
        command = self._undo.pop()
        command.revert(project)
        self._redo.append(command)
        return command

    def redo(self, project: Project) -> Command | None:
        if not self._redo:
            return None
        command = self._redo.pop()
        command.apply(project)
        self._undo.append(command)
        return command

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_text(self) -> str | None:
        return self._undo[-1].describe() if self._undo else None

    @property
    def redo_text(self) -> str | None:
        return self._redo[-1].describe() if self._redo else None

    @property
    def undo_kind(self) -> str | None:
        """The stable command name (``remove_node`` ...) of what undo would revert."""
        return self._undo[-1].name if self._undo else None

    @property
    def redo_kind(self) -> str | None:
        return self._redo[-1].name if self._redo else None
