"""The project document: lookup, validated mutation, expression dependencies.

All mutation goes through the methods here, and in practice through
:mod:`stokes.model.commands` so that every change is undoable and journaled.
"""

from __future__ import annotations

import copy
import json
import math
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from stokes import __version__
from stokes.errors import (
    CycleError,
    ExpressionError,
    ModelError,
    PersistenceError,
    ReferencedNode,
    StokesError,
    UnknownNode,
    ValidationError,
)
from stokes.model.ids import is_id, new_id, stable_id
from stokes.model.nodes import STATUSES, Node, QuantityValue
from stokes.model.schema import (
    DYNAMIC,
    MAX_JSON_BYTES,
    NAME_RE,
    NodeSpec,
    NodeTypeRegistry,
    ParamSpec,
    registry,
)
from stokes.units.dimensions import UNIT_SYSTEMS, Dimension, dimension
from stokes.units.expressions import evaluate, references
from stokes.units.quantities import is_quantity_text, parse_quantity, quantity, to_si
from stokes.units.registry import Quantity, ureg

#: The longest node name the model accepts.
#:
#: **A name is not only text in the tree.** A tag name becomes `<tag>.stl` on the host, a patch name
#: in a generated `snappyHexMeshDict`, and a directory entry in the case; `NAME_RE` bounds the
#: characters but not the length, so a 200-character name produced a 204-character file name
#: (measured, Opus's R5). 64 matches what the STL export already caps a solid name to
#: (`stokes.geometry.export.MAX_NAME`).
MAX_NAME_CHARS = 64

ParamKey = tuple[str, str]
"""(node id, parameter name)"""

SKELETON: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("geometry", ()),
    ("mesh", ()),
    ("physics", ("parameters", "materials")),
    ("regions", ()),
    ("solver", ()),
    # **No `monitors` group** (claude-win2's 0530 item 1): nothing created or read one. The
    # evidence was a search rather than an impression - no command writes a node in it and none
    # reads one, and the only other `monitors` in the codebase is `monitors/residuals.jsonl`
    # INSIDE a run's case directory, which is a file path and not this folder.
    ("scenes", ()),
    ("runs", ()),
    ("studies", ()),
)
PARAMETERS_PATH = "/physics/parameters"
_SPLIT_RE = re.compile(r"[/.]")
_TOUCHED_LIMIT = 2000
"""Most changed nodes a thread keeps for :meth:`Project.drain_touched` before it stops counting."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Paths of the groups a new project is created with. The model and the UI look them up by
# path (``/physics/parameters``, ``/regions`` ...), so they can be neither removed nor renamed.
#: **Folders the CODE finds by path**, so a rename breaks the lookup.
#:
#: The guide's finding 5 named these, and the consequence was measured rather than reasoned
#: about: `tag.create` does `project.find(TAGS_PATH)`, and with `tags` renamed it found nothing,
#: so the next tag **created a second `/geometry/tags`** - the tree held `my_tags` and `tags`.
#: The same shape applies to the mesh settings (`mesh.configure` adds a new one) and to the
#: parts and operations folders (found by path on import and on `operation.run`).
#:
#: **Protected rather than re-keyed.** Re-keying every lookup to a stable id is the more robust fix,
#: but it touches import, operations, meshing and regions at once - and this package is presentation
#: only, so a rename must not become a behaviour change. Protecting the paths makes the rename
#: impossible, which is the same guarantee with a fraction of the blast radius; the id-based lookups
#: belong with G-305/G-306, where the outline model already changes how nodes are found.
_PATH_FOLDERS: tuple[str, ...] = (
    "/geometry/parts",
    "/geometry/tags",
    # `view.save` finds the group by path and would make a second `views` after a rename (the
    # Opus review of R4, 2026-11-01, measured `view.list` empty and every save refused).
    "/views",
    # `field_function.save` finds its group by path, as `view.save` does.
    "/field_functions",
)
#: **`/mesh/settings` and `/solver/settings` are NOT here**, a correction the suite made: a
#: rename of those was already made safe - `ensure_stage_settings` looks the node up by its
#: **stable id and by its type** before adding, so a rename is not a collision, and
#: `tests/test_stage_settings` pins that a renamed settings node does not break `project.open`.
#: Protecting them here would have refused a rename the product had deliberately made work.
#:
#: The two that remain are the ones with **no id-based lookup**: `tag.create` does
#: `project.find("/geometry/tags")` and, finding nothing, creates a second `tags` - measured
#: (`my_tags` and `tags` side by side). `/geometry/parts` is the same shape for the import path.
#:
#: The durable fix is to give those lookups the stable-id treatment the stage settings already
#: have; that belongs with G-305/G-306, where the outline model changes how nodes are found.

STRUCTURAL_PATHS: frozenset[str] = frozenset(
    {f"/{group}" for group, _ in SKELETON}
    | {f"/{group}/{sub}" for group, subs in SKELETON for sub in subs}
)

#: **Folders whose NAME the code depends on, which may still be removed.**
#:
#: The two are different questions and the first version of this conflated them, which the
#: suite caught with eleven failures: `/mesh/settings` is *created by* `mesh.configure` and
#: **recreated when removed**, so a delete is a supported way to reset it. What breaks is a
#: RENAME - the lookup by path then finds nothing and the next create makes a second folder,
#: which is exactly what a test measured (`my_tags` and `tags` side by side).
#:
#: So: `STRUCTURAL_PATHS` refuses rename AND removal (the skeleton), and this set refuses rename
#: alone.
RENAME_STABLE_PATHS: frozenset[str] = frozenset(_PATH_FOLDERS)


class Project:
    """A tree of nodes plus the expression dependency graph over quantity parameters."""

    def __init__(
        self,
        root: Node,
        *,
        name: str,
        display_units: str = "si",
        node_registry: NodeTypeRegistry | None = None,
        created: str | None = None,
        modified: str | None = None,
    ) -> None:
        if display_units not in UNIT_SYSTEMS:
            raise ValidationError(f"unknown unit system {display_units!r}")
        self.root = root
        self.name = name
        self.display_units = display_units
        self.registry = node_registry or registry
        self.created = created or _now()
        self.modified = modified or self.created
        self._index: dict[str, Node] = {}
        self._deps: dict[ParamKey, set[ParamKey]] = {}
        self._rev: dict[ParamKey, set[ParamKey]] = {}
        self._failed: set[ParamKey] = set()
        self._touched = threading.local()
        #: How many times a node has changed, ever.
        #:
        #: **The dirty test reads this, not `modified`.** `modified` is a wall-clock stamp that
        #: `_touch` moves only on the paths that remember to call it - measured, `node.add`
        #: does not move it, so a project with a new node still compared equal to its saved
        #: copy and the Close prompt would have offered to discard nothing. `_mark` is called
        #: from every path that changes the model, because the incremental-sync protocol
        #: already depends on it.
        self.revision = 0
        self._reindex()
        self.drain_touched()  # loading evaluated every expression; that is not a change to announce

    # ------------------------------------------------------------------ construction
    @classmethod
    def new(
        cls, name: str, *, display_units: str = "si", node_registry: NodeTypeRegistry | None = None
    ) -> Project:
        """A project with the fixed top-level groups."""
        if not name.strip():
            raise ValidationError("project name must not be empty")
        # Structural nodes get deterministic ids so journals replay across projects.
        root = Node(stable_id("p", "/"), "project", name)
        for group, subgroups in SKELETON:
            g = Node(stable_id("g", f"/{group}"), "group", group, parent=root)
            root.children.append(g)
            for sub in subgroups:
                s = Node(stable_id("g", f"/{group}/{sub}"), "group", sub, parent=g)
                g.children.append(s)
        return cls(root, name=name, display_units=display_units, node_registry=node_registry)

    # ------------------------------------------------------------------ lookup
    def nodes(self) -> Iterator[Node]:
        return self.root.walk()

    def find(self, ref: str) -> Node | None:
        """Find by id or by path (``/physics/parameters/U_in`` or ``physics.parameters.U_in``)."""
        if ref in self._index:
            return self._index[ref]
        if is_id(ref):
            return None
        return self._by_path(ref)

    def get(self, ref: str) -> Node:
        node = self.find(ref)
        if node is None:
            raise UnknownNode(f"no node {ref!r}", details={"ref": ref})
        return node

    def _by_path(self, path: str) -> Node | None:
        segments = [s for s in _SPLIT_RE.split(path) if s]
        node: Node | None = self.root
        for segment in segments:
            if node is None:
                return None
            node = node.child(segment)
        return node

    def _reindex(self) -> None:
        self._index = {n.id: n for n in self.nodes()}
        self._deps.clear()
        self._rev.clear()
        self._failed.clear()
        for node in self.nodes():
            for pname, value in node.params.items():
                if isinstance(value, QuantityValue) and value.expr:
                    self._reevaluate(node, pname)

    def is_structural(self, node: Node) -> bool:
        """True when the server refuses to RENAME or remove this node.

        **One predicate for the model, the API summary and the client's menu**, which is the point:
        the first version answered only for the skeleton, so a client asking "may I offer Rename?"
        got `yes` for `/geometry/tags` and the server then refused - a menu entry that fails, which
        claude-win2's rule 2 forbids.

        `RENAME_STABLE_PATHS` is included because a folder the code finds by path cannot be renamed
        either; the difference between the two sets is only that those may still be REMOVED (and
        recreated), which is a separate question `remove` asks for itself.
        """
        return (
            node.parent is None or node.path in STRUCTURAL_PATHS or node.path in RENAME_STABLE_PATHS
        )

    def referrers(self, node_id: str, *, subtree: bool = False) -> list[tuple[Node, str]]:
        """The nodes whose ``ref`` parameters point at the node (id or path), each with the
        parameter's name, in tree order (ADR-0022 decision 2.8). With ``subtree``, every
        reference into the node or anything below it from a node outside it: what removing the
        subtree would leave dangling.

        Only ``ref`` parameters count; a tag's ``members`` name parts in JSON and are not
        references. Built on call, one walk of the tree: the tree is small beside the meshes.
        """
        target = self.get(node_id)
        inside = {n.id for n in target.walk()} if subtree else {target.id}
        names_of: dict[str, tuple[str, ...]] = {}
        found: list[tuple[Node, str]] = []
        for node in self.nodes():
            if subtree and node.id in inside:
                continue
            names = names_of.get(node.type)
            if names is None:
                try:
                    spec = self.registry.get(node.type)
                except StokesError:  # a type this registry lacks holds no reference it knows
                    spec = None
                names = tuple(p.name for p in spec.params if p.kind == "ref") if spec else ()
                names_of[node.type] = names
            for name in names:
                value = node.params.get(name)
                if isinstance(value, str) and value in inside:
                    found.append((node, name))
        return found

    # ------------------------------------------------------------------ mutation
    def add(
        self,
        parent_ref: str,
        node_type: str,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        node_id: str | None = None,
        index: int | None = None,
    ) -> Node:
        """Create a node, apply defaults, then the given parameters."""
        parent = self.get(parent_ref)
        spec = self.registry.get(node_type)
        parent_spec = self.registry.get(parent.type)
        if not parent_spec.allows_child(node_type):
            raise ModelError(
                f"{parent_spec.type} {parent.path!r} does not accept {node_type!r} children",
                details={"parent": parent.path, "type": node_type},
            )
        self._check_name(parent, name)
        if node_id is not None and node_id in self._index:
            raise ModelError(f"node id {node_id!r} already exists")
        node = Node(node_id or new_id(), node_type, name, parent=parent)
        for pspec in spec.params:
            node.params[pspec.name] = self._default_value(node, pspec)
        position = (
            len(parent.children) if index is None else max(0, min(index, len(parent.children)))
        )
        parent.children.insert(position, node)
        self._index[node.id] = node
        try:
            # "dimension" first: it decides how the other quantities are parsed.
            ordered = sorted((params or {}).items(), key=lambda kv: kv[0] != "dimension")
            for pname, raw in ordered:
                self.set_param(node.id, pname, raw)
            self._require_references(node, spec)
        except StokesError:
            parent.children.remove(node)
            self._index.pop(node.id, None)
            for pname in node.params:  # what its expressions installed goes with it
                self._drop_deps((node.id, pname))
                self._failed.discard((node.id, pname))
            self._recompute_dependents_of_subtree(node)
            raise
        self._retry_failed()
        self._touch()
        self._unmark(node)  # its own node.added says it; only what it changed elsewhere is a ripple
        return node

    def attach(self, parent_ref: str, node: Node, index: int | None = None) -> None:
        """Re-attach a detached subtree (the undo of :meth:`remove`)."""
        parent = self.get(parent_ref)
        self._check_name(parent, node.name)
        for n in node.walk():
            if n.id in self._index:
                raise ModelError(f"node id {n.id!r} already exists")
        node.parent = parent
        position = (
            len(parent.children) if index is None else max(0, min(index, len(parent.children)))
        )
        parent.children.insert(position, node)
        for n in node.walk():
            self._index[n.id] = n
        for n in node.walk():
            for pname, value in n.params.items():
                if isinstance(value, QuantityValue) and value.expr:
                    self._reevaluate(n, pname)
        self._recompute_dependents_of_subtree(node)
        self._retry_failed()
        self._touch()

    def remove(self, ref: str) -> tuple[Node, str, int]:
        """Detach a subtree; returns it with its former parent id and index."""
        node = self.get(ref)
        if node.parent is None:
            raise ModelError("the project root cannot be removed")
        if node.path in STRUCTURAL_PATHS:
            raise ModelError(
                f"{node.path!r} is part of the project skeleton and cannot be removed",
                details={"path": node.path},
            )
        parent = node.parent
        index = parent.children.index(node)
        parent.children.remove(node)
        node.parent = None
        for n in node.walk():
            self._index.pop(n.id, None)
            for pname in n.params:
                self._drop_deps((n.id, pname))
                self._failed.discard((n.id, pname))
        self._recompute_dependents_of_subtree(node)
        self._touch()
        return node, parent.id, index

    def rename(self, ref: str, new_name: str) -> str:
        node = self.get(ref)
        if node.parent is None:
            raise ModelError("rename the project with project.rename, not the root node")
        if node.path in STRUCTURAL_PATHS or node.path in RENAME_STABLE_PATHS:
            # **Two sets, one message**: the skeleton cannot be renamed or removed, and a folder the
            # code finds by path cannot be renamed but may be removed (and recreated).
            raise ModelError(
                f"{node.path!r} is part of the project skeleton and cannot be renamed",
                details={"path": node.path},
            )
        old = node.name
        if new_name != old:
            self._check_name(node.parent, new_name)
            node.name = new_name
            self._mark(node)
            self._recompute_dependents_of_subtree(node)
            self._retry_failed()
            self._touch()
        return old

    def convert_group(
        self, ref: str, node_type: str, params: Mapping[str, Any] | None = None
    ) -> Node:
        """Give a plain group a type in place: the same id, name, place among its siblings and
        children; the type's defaults, then ``params`` (ADR-0022 decision 2.9: a boundary or a
        region made before typed ones existed becomes typed). The type must accept the group's
        children and its parent must accept the type. :meth:`restore_group` undoes it.

        Only a plain group converts: it has no parameters of its own, so its undo gives back
        little (its status, meta, and whatever a hand edit stored on it). Removing the group and
        adding a typed node would keep the id too, but it would drop the children (a region's
        ``boundaries`` group).
        """
        node = self.get(ref)
        if node.type != "group" or self.is_structural(node) or node.parent is None:
            raise ModelError(
                f"{node.path} is a {node.type}; only a plain group outside the skeleton can be "
                "given a type",
                details={"node": node.path, "type": node.type},
            )
        spec = self.registry.get(node_type)
        if spec.type == "group":
            raise ModelError(f"{node.path} is a plain group already", details={"node": node.path})
        if not self.registry.get(node.parent.type).allows_child(spec.type):
            raise ModelError(
                f"{node.parent.path} does not accept {spec.type!r} children",
                details={"parent": node.parent.path, "type": spec.type},
            )
        for child in node.children:
            if not spec.allows_child(child.type):
                raise ModelError(
                    f"a {spec.type} does not accept {child.type!r} children; {child.path} is one: "
                    "move or delete it first",
                    details={"node": node.path, "child": child.path, "type": spec.type},
                )
        status, meta, old = node.status, copy.deepcopy(node.meta), copy.deepcopy(node.params)
        node.type = spec.type
        node.params = {}
        for pspec in spec.params:
            node.params[pspec.name] = self._default_value(node, pspec)
        try:
            ordered = sorted((params or {}).items(), key=lambda kv: kv[0] != "dimension")
            for pname, raw in ordered:
                self.set_param(node.id, pname, raw)
            self._require_references(node, spec)
        except StokesError:
            self.restore_group(node.id, status, meta, old)
            raise
        self._mark(node)
        self._retry_failed()  # an expression that read the typed node's parameters resolves now
        self._touch()
        return node

    def restore_group(
        self,
        ref: str,
        status: str,
        meta: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
    ) -> None:
        """Undo :meth:`convert_group`: the node is a plain group again with the status, ``meta``
        and ``params`` it had (a group has none unless a file was edited by hand); the typed
        parameters go, and whatever read them fails."""
        node = self.get(ref)
        names = list(node.params)
        for pname in names:
            self._drop_deps((node.id, pname))
            self._failed.discard((node.id, pname))
        node.type = "group"
        node.params = copy.deepcopy(dict(params or {}))
        node.status = status
        node.meta = copy.deepcopy(dict(meta))
        self._mark(node)
        for pname in names:
            self._recompute_dependents((node.id, pname))
        self._touch()

    def set_display_units(self, system: str) -> str:
        if system not in UNIT_SYSTEMS:
            raise ValidationError(f"unknown unit system {system!r}")
        old = self.display_units
        self.display_units = system
        self._touch()
        return old

    def set_param(
        self, ref: str, name: str, raw: Any, *, validate: bool = True
    ) -> Any:  # Any: JSON-able values
        """Set a parameter from a JSON value or text; returns the previous JSON value.

        ``validate`` false skips the parameter's structural check (a
        :attr:`~stokes.model.schema.ParamSpec.validate` hook): undo restores a value as it was,
        and one written before the check began may no longer pass it.

        **``null`` clears a parameter with no default** (``part.layers``, ``boundary.layers``):
        for such a parameter the absence IS the empty state, so removing the key is the only way
        back to it, and the generic kind checks would otherwise refuse null as a bad value. A
        parameter WITH a default still refuses null - its default is its own "empty", and a
        stored null would be a second spelling of that one state.

        **Narrowed to the `int` kind** (the M-504 security review's F2 revert item): the first
        draft cleared every no-default parameter - `part.bbox`, `part.mesh_size`,
        `property.value` and 12 more - skipping their checks, a widening the brief did not ask
        for. The two layer counts are the only `int` parameters with no default, so the kind is
        the narrowest gate that keeps the clear where the brief needs it.
        """
        node = self.get(ref)
        spec = self.registry.get(node.type).param(name)
        old = node.params.get(name)
        old_json = old.to_json() if isinstance(old, QuantityValue) else old
        if spec.kind == "json":
            old_json = copy.deepcopy(old)
        if raw is None and spec.kind == "int" and spec.default is None:
            # The clear: drop the key so readers see the empty state again. A plain parameter
            # holds no expression, so there are no dependencies to drop - the call mirrors
            # `unset_param`, without its "never held it" meaning in the docstring.
            self._drop_deps((node.id, name))
            self._failed.discard((node.id, name))
            node.params.pop(name, None)
            self._ok(node)
            self._mark(node)
            self._touch()
            return old_json
        if spec.kind == "quantity":
            new = self._quantity_from_raw(node, spec, raw)
            key = (node.id, name)
            previous_deps = self._deps.get(key, set())
            if new.expr:
                self._install_deps(key, new.expr, previous=previous_deps)
            else:
                self._drop_deps(key)
            node.params[name] = new
            self._ok(node)
            self._recompute_dependents(key)
            self._retry_failed()
        else:
            stored = (
                _numbers(spec, raw)
                if spec.kind == "array"
                else _json_value(spec, raw)
                if spec.kind == "json"
                else self._validate_plain(node, spec, raw)
            )
            if validate and spec.validate is not None:
                stored = spec.validate(node, stored)
            node.params[name] = stored
            if spec.name == "dimension" and spec.kind == "enum" and node.params[name] != old:
                self._reset_dynamic(node)
        self._mark(node)
        self._touch()
        return old_json

    def unset_param(self, ref: str, name: str) -> None:
        """Take a parameter off a node that never held it: the undo of the first write to a node
        saved before the parameter existed (readers take the parameter's default)."""
        node = self.get(ref)
        node.params.pop(name, None)
        self._drop_deps((node.id, name))
        self._failed.discard((node.id, name))
        self._recompute_dependents((node.id, name))
        self._mark(node)
        self._touch()

    def _reset_dynamic(self, node: Node) -> None:
        """A node's ``dimension`` changed: every quantity that takes its dimension from it
        (:data:`~stokes.model.schema.DYNAMIC`) starts again at its default in the new dimension.

        What such a quantity held, number or expression, was a quantity of another kind and
        cannot be carried over. The reset is the one the ``parameter`` node's ``value`` always had,
        now for every node type that has a ``dimension``: a study's ``variable`` (bounds, step,
        mean and deviation) and ``constraint`` (limit). Whatever refers to the quantities is
        evaluated again, and an expression that failed there no longer holds the node failed.
        """
        keys: list[ParamKey] = []
        cleared = False
        self._mark(node)
        for spec in self._dynamic_specs(node):
            key = (node.id, spec.name)
            node.params[spec.name] = self._default_value(node, spec)
            self._drop_deps(key)
            if key in self._failed:
                self._failed.discard(key)
                cleared = True
            keys.append(key)
        if cleared and not any(node_id == node.id for node_id, _ in self._failed):
            self._ok(node)
        for key in keys:
            self._recompute_dependents(key)

    def _dynamic_specs(self, node: Node) -> list[ParamSpec]:
        """The quantity parameters of the node's type that read their dimension from the node."""
        return [
            spec
            for spec in self.registry.get(node.type).params
            if spec.kind == "quantity" and spec.dimension == DYNAMIC
        ]

    def dynamic_quantities(self, ref: str) -> dict[str, QuantityValue]:
        """Copies of the quantities of a node that take their dimension from its ``dimension``,
        by parameter name: what a change of ``dimension`` resets (``set_param``), kept so that
        :meth:`restore_quantities` can put it back, as the undo of that change does."""
        node = self.get(ref)
        found: dict[str, QuantityValue] = {}
        for spec in self._dynamic_specs(node):
            value = node.params.get(spec.name)
            if isinstance(value, QuantityValue):
                found[spec.name] = QuantityValue(value.value, value.unit, value.expr)
        return found

    def restore_quantities(self, ref: str, values: Mapping[str, QuantityValue]) -> None:
        """Put back quantities kept by :meth:`dynamic_quantities`, once ``dimension`` is back.

        They are stored as they were, not parsed again, and each expression is evaluated again as
        when a project loads: one that no longer holds (its parameter has gone) marks the node
        failed instead of raising, so an undo cannot stop halfway. Whatever refers to the
        quantities is evaluated again too.
        """
        node = self.get(ref)
        node_spec = self.registry.get(node.type)
        keys: list[ParamKey] = []
        self._mark(node)
        for name, value in values.items():
            if node_spec.param(name).kind != "quantity":
                raise ValidationError(f"{name!r} on {node.path} is not a quantity")
            key = (node.id, name)
            node.params[name] = QuantityValue(value.value, value.unit, value.expr)
            self._drop_deps(key)
            self._failed.discard(key)
            keys.append(key)
        failures: list[tuple[ParamKey, str]] = []
        for name, value in values.items():
            if value.expr:
                self._reevaluate(node, name)
                if (node.id, name) in self._failed:
                    failures.append(((node.id, name), str(node.meta.get("error", ""))))
        for key, message in failures:  # a later success must not hide an earlier failure
            self._fail(node, message, key)
        for key in keys:
            self._recompute_dependents(key)
        self._touch()

    # ------------------------------------------------------------------ quantities
    def param_dimension(self, node: Node, spec: ParamSpec) -> Dimension:
        if spec.dimension == DYNAMIC:
            return dimension(str(node.params.get("dimension", "dimensionless")))
        return dimension(str(spec.dimension))

    def value_of(self, ref: str, name: str) -> Quantity:
        """The quantity stored in ``node.name`` (SI)."""
        node = self.get(ref)
        spec = self.registry.get(node.type).param(name)
        if spec.kind != "quantity":
            raise ValidationError(f"{name!r} on {node.path} is not a quantity")
        value = node.params[name]
        dim = self.param_dimension(node, spec)
        return self._as_quantity(value, dim)

    def resolve(self, ref: str) -> Quantity:
        """Resolver for expressions: ``U_in`` or dotted node paths with a parameter."""
        node, pname = self._resolve_ref(ref)
        return self.value_of(node.id, pname)

    def _resolve_ref(self, ref: str) -> tuple[Node, str]:
        segments = [s for s in ref.split(".") if s]
        if not segments:
            raise ExpressionError("empty reference")
        node = self._by_path("/".join(segments))
        if node is not None and node.type == "parameter":
            return node, "value"
        if len(segments) == 1:
            params_group = self._by_path(PARAMETERS_PATH)
            candidate = params_group.child(segments[0]) if params_group else None
            if candidate is not None and candidate.type == "parameter":
                return candidate, "value"
        if len(segments) >= 2:
            owner = self._by_path("/".join(segments[:-1]))
            if owner is not None:
                spec = self.registry.get(owner.type)
                if any(p.name == segments[-1] and p.kind == "quantity" for p in spec.params):
                    return owner, segments[-1]
        raise ExpressionError(f"unknown reference {ref!r}", details={"ref": ref})

    def evaluate(self, text: str, dim: Dimension | None = None) -> Quantity:
        return evaluate(text, self.resolve, expect=dim)

    def _as_quantity(self, value: Any, dim: Dimension) -> Quantity:  # Any: stored param
        if isinstance(value, QuantityValue):
            if dim.si_unit == "":
                return ureg().Quantity(value.value)
            return quantity(value.value, dim.si_unit)
        raise ValidationError("stored value is not a quantity")

    def _quantity_from_raw(self, node: Node, spec: ParamSpec, raw: Any) -> QuantityValue:
        dim = self.param_dimension(node, spec)
        if isinstance(raw, QuantityValue):
            raw = raw.to_json()
        if isinstance(raw, bool):
            raise ValidationError(f"{spec.name!r} expects a quantity, got a boolean")
        if isinstance(raw, int | float):
            return QuantityValue(float(raw), dim.si_unit, None)
        if isinstance(raw, dict):
            expr = raw.get("expr")
            if expr:
                return self._quantity_from_expr(node, spec, str(expr), dim)
            unit = str(raw.get("unit", "") or dim.si_unit)
            q = (
                quantity(float(raw["value"]), unit)
                if unit
                else ureg().Quantity(float(raw["value"]))
            )
            return QuantityValue(to_si(q, dim), dim.si_unit, None)
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                raise ValidationError(f"{spec.name!r} expects a quantity, got an empty string")
            if is_quantity_text(text):
                return QuantityValue(to_si(parse_quantity(text, dim), dim), dim.si_unit, None)
            return self._quantity_from_expr(node, spec, text, dim)
        raise ValidationError(f"{spec.name!r} expects a quantity, got {type(raw).__name__}")

    def _quantity_from_expr(
        self, node: Node, spec: ParamSpec, expr: str, dim: Dimension
    ) -> QuantityValue:
        key = (node.id, spec.name)
        refs = references(expr)
        for ref in refs:
            target = self._resolve_ref(ref)
            if (target[0].id, target[1]) == key:
                raise CycleError(
                    f"{node.path}.{spec.name} refers to itself", details={"expr": expr}
                )
        result = evaluate(expr, self.resolve, expect=dim)
        return QuantityValue(to_si(result, dim), dim.si_unit, expr)

    # ------------------------------------------------------------------ plain params
    def _validate_plain(self, node: Node, spec: ParamSpec, raw: Any) -> Any:  # Any: JSON
        match spec.kind:
            case "number":
                if isinstance(raw, bool) or not isinstance(raw, int | float):
                    raise ValidationError(f"{spec.name!r} expects a number")
                return float(raw)
            case "int":
                if isinstance(raw, bool) or not isinstance(raw, int):
                    raise ValidationError(f"{spec.name!r} expects an integer")
                return int(raw)
            case "bool":
                if not isinstance(raw, bool):
                    raise ValidationError(f"{spec.name!r} expects true or false")
                return raw
            case "string":
                if not isinstance(raw, str):
                    raise ValidationError(f"{spec.name!r} expects a string")
                return _matching(spec, raw)
            case "enum":
                if raw not in spec.choices:
                    raise ValidationError(
                        f"{spec.name!r} must be one of {', '.join(spec.choices)}; got {raw!r}"
                    )
                return raw
            case "ref":
                return self._reference(node, spec, raw)
        raise ValidationError(f"unsupported parameter kind {spec.kind!r}")  # pragma: no cover

    def _reference(self, node: Node, spec: ParamSpec, raw: Any) -> str | None:  # Any: JSON
        """A ``ref`` value: the id of a node of the spec's type, or null, which clears an
        optional reference and is refused for a required one (ADR-0022 decision 2.8)."""
        if raw is None:
            if spec.required:
                raise ValidationError(
                    f"{spec.name!r} is a required reference of a {node.type}; it cannot be null",
                    details={"parameter": spec.name, "node": node.path},
                )
            return None
        if not isinstance(raw, str):
            raise ValidationError(f"{spec.name!r} expects a node reference")
        target = self.get(raw)
        if target.type != spec.ref_type:
            raise ValidationError(
                f"{spec.name!r} must reference a {spec.ref_type}, not a {target.type}"
            )
        return target.id

    def _default_value(self, node: Node, spec: ParamSpec) -> Any:  # Any: JSON
        if spec.kind == "quantity":
            dim = self.param_dimension(node, spec)
            base = 0.0 if spec.default is None else float(spec.default)
            return QuantityValue(base, dim.si_unit, None)
        if spec.kind == "array":
            return [float(v) for v in spec.default or ()]  # a fresh list per node
        if spec.kind == "json":
            return copy.deepcopy(spec.default)  # a fresh value per node
        if spec.default is not None:
            return spec.default
        # **A plain kind with no default materialises null, not a fabricated zero** — the "empty
        # support" the layer counts need (brief M-504): `0` is a MEANING there (layers off on
        # this wall), so the empty = inherit state must be null, the same way a `ref` with no
        # default materialises null. Every int parameter written before this rule existed
        # carries a default, so the `"int": None` entry changes nothing stored today.
        return {"number": 0.0, "int": None, "bool": False, "string": "", "enum": None, "ref": None}[
            spec.kind
        ]

    # ------------------------------------------------------------------ dependencies
    def _install_deps(
        self, key: ParamKey, expr: str, previous: set[ParamKey] | None = None
    ) -> None:
        refs: set[ParamKey] = set()
        for ref in references(expr):
            node, pname = self._resolve_ref(ref)
            refs.add((node.id, pname))
        if key in refs:
            raise CycleError("expression refers to itself", details={"expr": expr})
        for ref_key in refs:
            if self._reaches(ref_key, key):
                raise CycleError(
                    f"expression creates a cycle through {self._describe(ref_key)}",
                    details={"expr": expr},
                )
        self._drop_deps(key)
        self._deps[key] = refs
        for ref_key in refs:
            self._rev.setdefault(ref_key, set()).add(key)
        _ = previous

    def _drop_deps(self, key: ParamKey) -> None:
        for ref_key in self._deps.pop(key, set()):
            dependents = self._rev.get(ref_key)
            if dependents:
                dependents.discard(key)
                if not dependents:
                    del self._rev[ref_key]

    def _reaches(self, start: ParamKey, target: ParamKey) -> bool:
        """True when ``start`` (transitively) depends on ``target``."""
        stack = [start]
        seen: set[ParamKey] = set()
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self._deps.get(current, ()))
        return False

    def _recompute_dependents(self, key: ParamKey) -> None:
        order: list[ParamKey] = []
        seen: set[ParamKey] = set()
        stack = list(self._rev.get(key, ()))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            order.append(current)
            stack.extend(self._rev.get(current, ()))
        for node_id, pname in order:
            node = self._index.get(node_id)
            if node is not None:
                self._reevaluate(node, pname)

    def _recompute_dependents_of_subtree(self, node: Node) -> None:
        for n in node.walk():
            for pname in n.params:
                self._recompute_dependents((n.id, pname))

    def _reevaluate(self, node: Node, pname: str) -> None:
        """Re-resolve an expression's references, re-evaluate it, and set the node status."""
        value = node.params.get(pname)
        if not isinstance(value, QuantityValue) or not value.expr:
            return
        spec = self.registry.get(node.type).param(pname)
        dim = self.param_dimension(node, spec)
        key = (node.id, pname)
        try:
            self._install_deps(key, value.expr)
            result = evaluate(value.expr, self.resolve, expect=dim)
            value.value = to_si(result, dim)
            self._ok(node)
        except StokesError as exc:
            self._fail(node, f"{pname}: {exc.message}", key)

    def _retry_failed(self) -> None:
        """Expressions that failed earlier may resolve now (a parameter was added or renamed)."""
        for node_id, pname in list(self._failed):
            node = self._index.get(node_id)
            if node is not None:
                self._reevaluate(node, pname)

    def _describe(self, key: ParamKey) -> str:
        node = self._index.get(key[0])
        return f"{node.path}.{key[1]}" if node else f"{key[0]}.{key[1]}"

    # ------------------------------------------------------------------ status
    def _ok(self, node: Node) -> None:
        node.status = "ok"
        node.meta.pop("error", None)
        for pname in node.params:
            self._failed.discard((node.id, pname))
        self._mark(node)

    def _fail(self, node: Node, message: str, key: ParamKey | None = None) -> None:
        node.status = "failed"
        node.meta["error"] = message
        if key is not None:
            self._failed.add(key)
        self._mark(node)

    def set_status(self, ref: str, status: str) -> None:
        if status not in STATUSES:
            raise ValidationError(f"unknown status {status!r}")
        node = self.get(ref)
        node.status = status
        self._mark(node)

    # ------------------------------------------------------------------ what a change touched
    # A change reaches further than the node it names: an edit re-evaluates the expressions that
    # read it, a rename or a removal fails them, an added node lets a failed one resolve. The
    # nodes whose value or status moved are noted here, per thread (the thread that changes the
    # model is the one that announces it), for the session to send beside the event that names
    # the edited node, so that a client can patch its copy of the tree instead of fetching it.
    def _mark(self, node: Node) -> None:
        self.revision += 1
        touched: dict[str, None] | None = getattr(self._touched, "ids", None)
        if touched is None:
            touched = self._touched.ids = {}
        if len(touched) >= _TOUCHED_LIMIT:
            touched.clear()  # nobody asked for them: a client resyncs when it next hears of a node
            self._touched.lost = True
        touched[node.id] = None

    def _unmark(self, node: Node) -> None:
        touched: dict[str, None] | None = getattr(self._touched, "ids", None)
        if touched:
            touched.pop(node.id, None)

    def drain_touched(self) -> tuple[list[str], bool]:
        """The ids of the nodes this thread changed since it last asked, and whether it lost track
        (more changes than are kept). Asking forgets them."""
        touched: dict[str, None] | None = getattr(self._touched, "ids", None)
        lost = bool(getattr(self._touched, "lost", False))
        self._touched.lost = False
        if not touched:
            return [], lost
        ids = list(touched)
        touched.clear()
        return ids, lost

    # ------------------------------------------------------------------ helpers
    def _check_name(self, parent: Node, name: str) -> None:
        if not NAME_RE.match(name):
            raise ValidationError(
                f"invalid name {name!r}: letters, digits, underscores; no leading digit",
                details={"name": name},
            )
        # **A length cap, because a name travels further than the tree** (Opus's R5). A tag name
        # becomes a staged file name (`<tag>.stl`), a patch name in a generated dictionary, and a
        # directory entry in the case - and `NAME_RE` bounds the CHARACTERS but not the length.
        # Measured: a 200-character tag name was accepted and would have produced a 204-character
        # file name. 64 is what the OpenFOAM-related formats tolerate and what an STL solid name
        # uses elsewhere in this codebase (`stokes.geometry.export.MAX_NAME`).
        if len(name) > MAX_NAME_CHARS:
            raise ValidationError(
                f"the name is {len(name)} characters; the most is {MAX_NAME_CHARS}",
                details={"name": name[:32] + "...", "length": len(name)},
            )
        if parent.child(name) is not None:
            raise ModelError(
                f"{parent.path or '/'} already has a child named {name!r}",
                details={"parent": parent.path, "name": name},
            )

    def _require_references(self, node: Node, spec: NodeSpec) -> None:
        """Refuse a node whose required references are not set (ADR-0022 decision 2.8)."""
        for pspec in spec.params:
            if pspec.kind == "ref" and pspec.required and node.params.get(pspec.name) is None:
                raise ValidationError(
                    f"a {spec.type} needs {pspec.name!r}: it is a required reference",
                    details={"parameter": pspec.name, "type": spec.type},
                )

    def _touch(self) -> None:
        # **The revision is counted here and in `_mark`, because a change reaches the model by two
        # routes.** `_touch` accompanies a structural or parameter edit (including `add`, which
        # `_mark` does NOT cover - measured: `node.add` left the revision at 0); `_mark` records a
        # node whose value or status moved when an expression was re-evaluated, which need not go
        # through `_touch` at all. Counting both is what makes `dirty` see every change.
        self.revision += 1
        self.modified = _now()

    def spec_of(self, node: Node) -> NodeSpec:
        return self.registry.get(node.type)

    # ------------------------------------------------------------------ persistence form
    def to_json(self) -> dict[str, Any]:
        from stokes.model.persist import SCHEMA_VERSION

        return {
            "schema_version": SCHEMA_VERSION,
            "stokes_version": __version__,
            "name": self.name,
            "display_units": self.display_units,
            "created": self.created,
            "modified": self.modified,
            "root": self.root.to_json(),
        }

    @classmethod
    def from_json(cls, data: Any, node_registry: NodeTypeRegistry | None = None) -> Project:
        if not isinstance(data, dict) or "root" not in data:
            raise PersistenceError("project file must be an object with a 'root'")
        root = Node.from_json(data["root"], registry=node_registry or registry)
        return cls(
            root,
            name=str(data.get("name", root.name)),
            display_units=str(data.get("display_units", "si")),
            node_registry=node_registry,
            created=data.get("created"),
            modified=data.get("modified"),
        )


SHOWN_REFERRERS = 5
"""Referrers a :func:`referenced_error` message names; ``details`` lists up to a hundred."""


def referenced_error(
    node: Node, found: Sequence[tuple[Node, str]], *, hint: str = "remove or change them first"
) -> ReferencedNode:
    """The ``model.referenced`` refusal to remove ``node``, naming up to five of the nodes that
    refer to it (``found``, from :meth:`Project.referrers`) and the parameter each uses."""
    shown = ", ".join(f"{n.path} ({param})" for n, param in found[:SHOWN_REFERRERS])
    more = f" and {len(found) - SHOWN_REFERRERS} more" if len(found) > SHOWN_REFERRERS else ""
    return ReferencedNode(
        f"{node.path} is referenced by {shown}{more}; {hint}",
        details={
            "node": node.path,
            "id": node.id,
            "count": len(found),
            "referrers": [{"id": n.id, "path": n.path, "param": p} for n, p in found[:100]],
        },
    )


def _matching(spec: ParamSpec, raw: str) -> str:
    if spec.pattern is not None and not re.fullmatch(spec.pattern, raw):
        raise ValidationError(
            f"{spec.name!r} must match {spec.pattern}; got {raw!r}",
            details={"pattern": spec.pattern},
        )
    return raw


def _numbers(spec: ParamSpec, raw: Any) -> list[float]:  # Any: JSON value
    """An ``array`` parameter: a list of finite numbers (SI when the spec has a dimension)."""
    if not isinstance(raw, list | tuple) or not all(
        isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in raw
    ):
        raise ValidationError(f"{spec.name!r} expects a list of finite numbers")
    return [float(v) for v in raw]


def _json_value(spec: ParamSpec, raw: Any) -> Any:  # Any: JSON-compatible value
    """A ``json`` parameter: JSON-compatible data, deep-copied and size-limited.

    The value is taken as it comes and never shared with the caller, so a command that
    keeps or mutates what it passed cannot change the model behind undo's back.
    """
    limit = spec.max_bytes or MAX_JSON_BYTES
    value = _check_json(raw, spec.name, limit)
    if len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > limit:
        raise ValidationError(
            f"{spec.name!r} is over {limit} bytes of JSON",
            details={"parameter": spec.name, "max_bytes": limit},
        )
    return value


def _check_json(value: Any, name: str, limit: int) -> Any:  # Any: JSON value in and out
    """Reject anything JSON cannot hold; return a deep copy of what remains."""
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValidationError(f"{name!r} expects JSON object keys as strings")
        return {k: _check_json(v, name, limit) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_check_json(v, name, limit) for v in value]
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        if not math.isfinite(value):
            raise ValidationError(f"{name!r} expects finite numbers, got {value!r}")
        return value
    raise ValidationError(f"{name!r} expects JSON-compatible data, got {type(value).__name__}")
