"""The undoable commands behind ``operation.create`` and ``operation.delete`` (ADR-0021
decision 8.8), modelled on :class:`stokes.geometry.commands.ImportStep`.

:class:`CreateOperation` is one undo step: the ``/geometry/operations`` group when missing, the op
node (its id kept for replay) and ``visible: false`` on the parts it reads; applying it starts the
run, whose outputs are added as the job reports them and are not part of the step. Undo cancels
the run, takes away what its runs made (outputs, meshes, their faces in tags, the tags they
created: :meth:`OperationStore.discard`) and reverts the step; redo applies it again, which runs
again: the same ids, the same versions, the same tags.

:class:`DeleteOperation` removes the op node and, unless ``keep_outputs``, its outputs, showing
the parts it read again; kept outputs become plain parts (``derived`` cleared) whose cached
B-rep stays. Undo puts the nodes back with their meshes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from stokes.errors import ModelError, StokesError
from stokes.geometry.model import GEOMETRY_PATH, OPERATIONS_GROUP, OPERATIONS_PATH
from stokes.model.commands import AddNode, Command, Composite, RemoveNode, SetParam
from stokes.model.ids import stable_id

if TYPE_CHECKING:
    from stokes.geometry.opstore import OperationStore
    from stokes.geometry.store import MeshEntry
    from stokes.model.project import Project


@dataclass(eq=False)
class CreateOperation(Command):
    """Add an operation node (and its group when missing), hide the parts it reads, run it."""

    name: ClassVar[str] = "create_operation"
    store: OperationStore
    op_type: str
    op_name: str
    params: dict[str, Any]  # Any: JSON values, inputs and auto_run among them
    hide: list[str] = field(default_factory=list)
    node_id: str | None = None
    group_id: str | None = None
    created_group: bool = False
    steps: Composite | None = field(default=None, repr=False)

    def apply(self, project: Project) -> Any:  # Any: JSON summary
        commands: list[Command] = []
        group: AddNode | None = None
        if project.find(OPERATIONS_PATH) is None:
            wanted = stable_id("g", OPERATIONS_PATH)
            group = AddNode(
                GEOMETRY_PATH,
                "group",
                OPERATIONS_GROUP,
                node_id=None if project.find(wanted) else wanted,
            )
            commands.append(group)
        add = AddNode(
            OPERATIONS_PATH, self.op_type, self.op_name, self.params, node_id=self.node_id
        )
        commands.append(add)
        for part in self.hide:
            node = project.find(part)
            if node is not None and node.type == "part" and node.params.get("visible") is not False:
                commands.append(SetParam(part, "visible", False))
        steps = Composite(self.describe(), commands)
        steps.apply(project)
        self.steps = steps
        self.node_id = add.node_id
        self.created_group = group is not None
        self.group_id = group.node_id if group is not None else None
        node = project.get(self._id())
        node.status = "new"
        self.store.start(node.id, reason="create")
        return node.summary()

    def revert(self, project: Project) -> None:
        op_id = self._id()
        self.store.cancel(op_id, reason="undo")
        self.store.discard(op_id)
        if self.steps is not None:
            self.steps.revert(project)

    def to_record(self) -> dict[str, Any]:
        """The model commands of the step; the run rebuilds the outputs."""
        if self.steps is None:
            raise ModelError("the operation was never created")
        return self.steps.to_record()

    def hidden(self) -> list[str]:
        """The parts this step hid (they were visible before it)."""
        if self.steps is None:
            return []
        return [c.node for c in self.steps.commands if isinstance(c, SetParam)]

    def describe(self) -> str:
        return f"create {self.op_type} {self.op_name}"

    def _id(self) -> str:
        if self.node_id is None:
            raise ModelError("the operation was never created")
        return self.node_id


@dataclass(eq=False)
class DeleteOperation(Command):
    """Remove an operation node and its outputs (or keep them as plain parts)."""

    name: ClassVar[str] = "delete_operation"
    store: OperationStore
    op_id: str
    keep_outputs: bool = False
    steps: Composite | None = field(default=None, repr=False)
    held: dict[str, MeshEntry] = field(default_factory=dict, repr=False)

    def apply(self, project: Project) -> Any:  # Any: JSON summary
        from stokes.geometry.opplan import consumed
        from stokes.geometry.store import parts_under

        op = project.get(self.op_id)
        summary = op.summary()
        self.store.cancel(op.id, reason="removed")
        outputs = [n.id for n in parts_under(project.root) if n.params.get("derived") == op.id]
        commands: list[Command] = []
        if self.keep_outputs:
            commands.extend(SetParam(part, "derived", "") for part in outputs)
        else:
            # The carried tags first, and as model commands, so undo puts them back with the
            # outputs: removing a part does not prune a tag's `members` (they are JSON, not `ref`
            # parameters, `Project.referrers` says so), so a deleted operation used to leave a tag
            # pointing at parts that were gone.
            commands.extend(self._strip_commands(project, outputs, op.id))
            commands.extend(RemoveNode(part) for part in outputs)
            for part in consumed(op):
                node = project.find(part)
                if node is not None and node.type == "part" and node.params.get("visible") is False:
                    commands.append(SetParam(part, "visible", True))
        commands.append(RemoveNode(op.id))
        steps = Composite(self.describe(), commands)
        held: dict[str, MeshEntry] = {}
        if not self.keep_outputs:  # the meshes go with the nodes; undo gives both back
            for part in outputs:
                entry = self.store.geometry.take(part)
                if entry is not None:
                    held[part] = entry
        try:
            steps.apply(project)
        except Exception:
            for part, entry in held.items():
                self.store.geometry.put(part, entry)
            raise
        self.steps, self.held = steps, held
        return {**summary, "outputs": outputs, "kept": self.keep_outputs}

    def _strip_commands(
        self, project: Project, outputs: Sequence[str], op_id: str
    ) -> list[Command]:
        """The commands that take ``outputs`` out of every tag, and remove the tags this
        operation's runs created that are left empty and unreferenced (``carry.strip``, written as
        model commands so the delete's undo restores them through the ordinary revert)."""
        from stokes.geometry.carry import tags_of
        from stokes.geometry.model import TAG_TYPE
        from stokes.geometry.operations import tag_id
        from stokes.geometry.selection import dump_members, load_members

        gone = set(outputs)
        commands: list[Command] = []
        for tag in list(tags_of(project)):
            raw = tag.params.get("members")
            try:
                members = load_members(raw if isinstance(raw, str) else "", where=tag.path)
            except StokesError:
                continue  # malformed members: the tag commands report those
            if not any(m.part in gone for m in members):
                continue
            kept = dump_members([m for m in members if m.part not in gone])
            if kept != raw:
                commands.append(SetParam(tag.id, "members", kept))
        created = list(project.get(op_id).meta.get("created_tags") or [])
        # A run cut short never wrote created_tags: its tags carry ids derived from the op's.
        created += [t.id for t in tags_of(project) if t.id == tag_id(op_id, t.name)]
        for made_id in dict.fromkeys(created):
            made = project.find(made_id)
            if made is None or made.type != TAG_TYPE or project.referrers(made.id):
                continue
            raw = made.params.get("members")
            if not raw or raw == "[]":
                commands.append(RemoveNode(made.id))
        return commands

    def revert(self, project: Project) -> None:
        if self.steps is None:
            raise ModelError("the operation was never deleted")
        self.steps.revert(project)
        held, self.held = self.held, {}
        for part, entry in held.items():
            self.store.geometry.put(part, entry)

    def to_record(self) -> dict[str, Any]:
        if self.steps is None:
            raise ModelError("the operation was never deleted")
        return self.steps.to_record()

    def describe(self) -> str:
        return "delete operation"
