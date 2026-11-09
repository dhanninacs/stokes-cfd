"""The undoable command behind ``geometry.import`` (ADR-0014, decision G1; renamed by
ADR-0021 decision 1.1, ``geometry.import_step`` kept as its alias).

It lives with the geometry store it drives: applying it adds the placeholder part and starts the
import; undo cancels the import and takes the meshes away with the nodes; redo gives both back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from stokes.errors import ModelError, StokesError
from stokes.geometry import formats
from stokes.geometry.store import (
    GEOMETRY_PATH,
    PARTS_PATH,
    GeometryStore,
    MeshEntry,
    parts_under,
    solid_index,
)
from stokes.model.commands import Command
from stokes.model.ids import stable_id

if TYPE_CHECKING:
    from stokes.model.nodes import Node
    from stokes.model.project import Project


@dataclass(eq=False)
class ImportStep(Command):
    """Add a placeholder part (and ``/geometry/parts`` when missing) and import into it.

    One undo step whatever the read makes of it: undo removes the part, or the group with all
    its parts, and drops their meshes; redo puts everything back, and imports again only the
    parts whose mesh had not finished.
    """

    name: ClassVar[str] = "import"
    store: GeometryStore
    source: str
    part_name: str
    units: str | None = None
    """The length unit for a format the file does not declare (BREP); None reads the file's."""
    node_id: str | None = None
    parts_group_id: str | None = None
    created_parts_group: bool = False
    detached: tuple[Node, str, int] | None = field(default=None, repr=False)
    held: dict[str, MeshEntry] = field(default_factory=dict, repr=False)

    def apply(self, project: Project) -> Any:  # Any: JSON summary
        if self.detached is not None:
            return self._redo(project)
        parts = project.find(PARTS_PATH)
        created = None
        if parts is None:
            group_id = stable_id("g", PARTS_PATH)
            created = project.add(
                GEOMETRY_PATH,
                "group",
                "parts",
                node_id=None if project.find(group_id) else group_id,
            )
            parts = created
        family = formats.family_for(Path(self.source))
        try:
            node = project.add(
                parts.id,
                "part",
                self.part_name,
                {"source": self.source, "family": family},
                node_id=self.node_id,
            )
        except StokesError:
            if created is not None:
                project.remove(created.id)
            raise
        if created is not None:
            self.created_parts_group, self.parts_group_id = True, created.id
        node.status = "new"
        self.node_id = node.id
        self.store.import_file(node.id, Path(self.source), units=self.units, family=family)
        return node.summary()

    def _redo(self, project: Project) -> Any:  # Any: JSON summary
        assert self.detached is not None
        subtree, parent_id, index = self.detached
        project.attach(parent_id, subtree, index)
        self.detached = None
        top = project.get(self._id())
        held, self.held = self.held, {}
        missing = []
        for part in parts_under(top):
            entry = held.get(part.id)
            if entry is not None:
                self.store.put(part.id, entry)
            else:
                missing.append(part)
        if missing and top.type == "part":
            self.store.import_file(top.id, Path(self.source), units=self.units)
        elif missing:
            self.store.reimport(
                Path(self.source), {solid_index(p): p.id for p in missing}, units=self.units
            )
        return top.summary()

    def revert(self, project: Project) -> None:
        top = project.get(self._id())
        self.store.cancel_many([n.id for n in top.walk()], reason="undo")
        self.held = {}
        for part in parts_under(top):
            entry = self.store.take(part.id)
            if entry is not None:
                self.held[part.id] = entry
        target = self.parts_group_id if self.created_parts_group and self.parts_group_id else top.id
        self.detached = project.remove(target)

    def to_record(self) -> dict[str, Any]:
        """The nodes as the command adds them; the read rebuilds the per-solid structure."""
        records: list[dict[str, Any]] = []
        if self.created_parts_group:
            records.append(
                {
                    "command": "add_node",
                    "parent": GEOMETRY_PATH,
                    "type": "group",
                    "name": "parts",
                    "params": {},
                    "node_id": self.parts_group_id,
                    "index": None,
                }
            )
        records.append(
            {
                "command": "add_node",
                "parent": PARTS_PATH,
                "type": "part",
                "name": self.part_name,
                "params": {"source": self.source, "family": formats.family_for(Path(self.source))},
                "node_id": self.node_id,
                "index": None,
            }
        )
        return {"command": "composite", "label": self.describe(), "commands": records}

    def describe(self) -> str:
        return f"import {self.part_name}"

    def _id(self) -> str:
        if self.node_id is None:
            raise ModelError("the import was never applied")
        return self.node_id
