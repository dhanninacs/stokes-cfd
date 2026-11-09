"""Node summaries: the one shape ``node.get``, ``node.tree`` and the node events share.

A summary is a node without its children: id, type, name, status, params in JSON form, ``meta``
when there is any, path, ``children_count``, ``structural`` (the server refuses to rename or delete
the skeleton groups) and, for the quantity parameters, ``displays`` (the value as text in the
project's display unit). ``node.tree`` nests them under ``children``.

Three things are built here and nowhere else:

* :func:`summarise` and :func:`tree_dict`, the plain dict forms the commands return;
* :func:`tree_stream`, the same tree as JSON text in fragments, so that the server can hand the
  event loop back between them (a tree of thousands of nodes is tens of milliseconds of work and
  must not hold the loop for that long);
* :func:`node_event`, which adds to a node event the summary of the node it names (and of the
  nodes the change reached), so that a client can patch its copy of the tree without asking for
  all of it again.

Why this is not ``Node.summary`` plus a loop: a tree of 4,500 nodes spent most of ``node.tree`` in
repeated path walks, in ``spec_of`` and ``param_dimension`` per node, and in pint for the display
strings. The path is now built from the parent's, the quantity parameters of a node type are looked
up once per type, and a display string is remembered for the value, unit and display unit it was
made from.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from stokes.model import Node, Project, QuantityValue
from stokes.units import format_quantity, quantity
from stokes.units.registry import ureg

if TYPE_CHECKING:
    from stokes.model.schema import ParamSpec

TypeQuantities = dict[str, tuple["ParamSpec", ...]]
"""Per call: the quantity parameter specs of each node type seen so far."""

NODE_EVENTS = ("node.added", "node.changed", "node.removed")
"""The events that name a node whose summary a client needs to keep its tree current."""

STATE_EVENTS = ("geometry.part_ready", "geometry.import_failed")
"""Events that follow a change of a node's status that no node event announced: a part that
finished its mesh has its derived parameters announced by ``node.changed``, but its status moves
to ``ok`` (or ``failed``) afterwards. They carry the summary too, so the client need not refresh."""

ALSO_LIMIT = 64
"""Most nodes a single event reports as reached by the change. A change that reached more than
this (a parameter that hundreds of expressions read) sets ``resync`` instead and the client
fetches the tree."""

_ENCODER = json.JSONEncoder(separators=(",", ":"), default=str)
_BATCH = 64
"""Nodes between two fragments of :func:`tree_stream`: about half a millisecond of work."""


# ---------------------------------------------------------------- summaries
@lru_cache(maxsize=16384)
def _display(value: float, si_unit: str, unit: str | None) -> str:
    """A quantity as text in ``unit``. Pure in its arguments, which is what makes it cacheable:
    pint costs about 60 microseconds a value and a refreshed tree repeats the same values."""
    q = quantity(value, si_unit) if si_unit else ureg().Quantity(value)
    return format_quantity(q, unit)


def _quantity_specs(
    project: Project, node_type: str, seen: TypeQuantities
) -> tuple[ParamSpec, ...]:
    found = seen.get(node_type)
    if found is None:
        found = tuple(p for p in project.registry.get(node_type).params if p.kind == "quantity")
        seen[node_type] = found
    return found


def _head(project: Project, node: Node, path: str, seen: TypeQuantities) -> dict[str, Any]:
    """The summary of ``node`` whose path is already known."""
    # list(...) copies in one step: another thread (an import) assigns parameter values while this
    # runs, and iterating the live dict could meet a change.
    params = {
        k: (v.to_json() if isinstance(v, QuantityValue) else v)
        for k, v in list(node.params.items())
    }
    data: dict[str, Any] = {
        "id": node.id,
        "type": node.type,
        "name": node.name,
        "status": node.status,
        "params": params,
    }
    if node.meta:
        data["meta"] = dict(node.meta)
    data["path"] = path
    data["children_count"] = len(node.children)
    # **The model's own predicate**, so the flag a client reads and the rule the server enforces are
    # one definition. `is_structural` knows about both sets; this used to re-derive it from
    # `STRUCTURAL_PATHS` alone, which is how the tree came to offer Rename on `/geometry/tags`.
    data["structural"] = project.is_structural(node)
    quantities = _quantity_specs(project, node.type, seen)
    if quantities:
        displays: dict[str, str] = {}
        for spec in quantities:
            stored = node.params.get(spec.name)
            if isinstance(stored, QuantityValue):
                dim = project.param_dimension(node, spec)
                unit = dim.unit_for(project.display_units) or None
                displays[spec.name] = _display(stored.value, dim.si_unit, unit)
        if displays:
            data["displays"] = displays
    return data


def summarise(project: Project, node: Node) -> dict[str, Any]:
    """Node summary plus display-unit strings for its quantity parameters."""
    return _head(project, node, node.path, {})


def _child_path(parent_path: str, name: str) -> str:
    return f"/{name}" if parent_path == "/" else f"{parent_path}/{name}"


def tree_dict(project: Project, node: Node, depth: int | None = None) -> dict[str, Any]:
    """The tree (or subtree) below ``node`` with children nested, ``depth`` levels of it."""
    seen: TypeQuantities = {}

    def build(current: Node, path: str, left: int | None) -> dict[str, Any]:
        data = _head(project, current, path, seen)
        if left is None or left > 0:
            below = None if left is None else left - 1
            data["children"] = [
                build(c, _child_path(path, c.name), below) for c in list(current.children)
            ]
        return data

    return build(node, node.path, depth)


# ---------------------------------------------------------------- streaming
class _TreeStream:
    """One tree written as JSON text: the pieces so far, and how many nodes they hold."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.seen: TypeQuantities = {}
        self.pieces: list[str] = []
        self.counted = 0

    def spill(self) -> str | None:
        """The text so far, once there is enough of it to be worth handing over."""
        if self.counted < _BATCH:
            return None
        self.counted = 0
        fragment = "".join(self.pieces)
        self.pieces.clear()
        return fragment

    def walk(self, node: Node, path: str, left: int | None, first: bool) -> Iterator[str]:
        """``node`` and, while ``left`` levels allow, what is below it."""
        text = _ENCODER.encode(_head(self.project, node, path, self.seen))
        lead = "" if first else ","
        self.counted += 1
        if left is None or left > 0:
            self.pieces.append(f'{lead}{text[:-1]},"children":[')
            yield from self.children(node, path, None if left is None else left - 1)
            self.pieces.append("]}")
        else:
            self.pieces.append(lead + text)
        if (fragment := self.spill()) is not None:
            yield fragment

    def children(self, parent: Node, parent_path: str, below: int | None) -> Iterator[str]:
        """The items of ``parent``'s ``children`` array, separated by commas."""
        expands = below is None or below > 0
        leaves: list[dict[str, Any]] = []
        written = False
        for child in list(parent.children):
            path = _child_path(parent_path, child.name)
            if expands and child.children:
                written = self.flush(leaves, written)
                yield from self.walk(child, path, below, not written)
                written = True
            else:
                data = _head(self.project, child, path, self.seen)
                if expands:
                    data["children"] = []
                leaves.append(data)
                if len(leaves) < _BATCH:
                    continue
                written = self.flush(leaves, written)
            if (fragment := self.spill()) is not None:
                yield fragment
        self.flush(leaves, written)

    def flush(self, leaves: list[dict[str, Any]], written: bool) -> bool:
        """Write the waiting leaves with one call of the encoder; True once anything is written."""
        if not leaves:
            return written
        self.pieces.append(("," if written else "") + _ENCODER.encode(leaves)[1:-1])
        self.counted += len(leaves)
        leaves.clear()
        return True


def tree_stream(project: Project, node: Node, depth: int | None = None) -> Iterator[str]:
    """The JSON text of :func:`tree_dict`, in fragments of at least :data:`_BATCH` nodes.

    Joined, the fragments are the document ``json.dumps(tree_dict(...))`` makes, compactly
    written; one fragment alone is not a document. The caller gives the event loop back between
    two of them. The tree may change between two fragments (a command, an import), so a streamed
    tree is a snapshot of each fragment and not of the whole; the events that follow a change
    repair what a client got of it.

    Siblings that have no children below them are encoded together, in one call: the encoder costs
    about seven microseconds to set up and a node about nine to write, and the usual large tree
    is one group of thousands of such leaves.
    """
    stream = _TreeStream(project)
    yield from stream.walk(node, node.path, depth, True)
    if stream.pieces:
        yield "".join(stream.pieces)


# ---------------------------------------------------------------- events
def announce(project: Project, event: str, data: dict[str, Any]) -> dict[str, Any]:
    """``data`` as ``event`` should leave the session: the node events with their summaries.

    The geometry events that follow a silent change of status carry them too (see
    :data:`STATE_EVENTS`; a part's coarse mesh changes nothing of its node). The marks of a change
    that a client answers by fetching the whole tree (an undo, a project opened) are dropped.
    """
    if event in NODE_EVENTS or (event in STATE_EVENTS and data.get("complete") is not False):
        return node_event(project, event, data)
    if event.startswith(("history.", "project.")):
        project.drain_touched()
    return data


def node_event(project: Project, event: str, data: dict[str, Any]) -> dict[str, Any]:
    """``data`` of a node event with what a client needs to patch its tree.

    ``node`` is the summary of the node the event names; ``parent`` and, unless the node is the
    last of its siblings, ``index`` say where an added node goes; ``also`` holds the summaries of
    the other nodes this thread's change reached (the expressions that read an edited parameter,
    an expression that failed and now resolves) and ``resync`` says there were too many to send.
    Anything that cannot be found is left out, and a client that misses what it needs fetches the
    tree. The caller's dict is not changed.
    """
    node_id = data.get("node_id")
    if not isinstance(node_id, str):
        return data
    reached, lost = project.drain_touched()
    out = dict(data)
    node = project.find(node_id)
    if event == "node.removed":
        if "parent" not in out:
            path = data.get("path")
            parent = project.find(path.rsplit("/", 1)[0] or "/") if isinstance(path, str) else None
            if parent is not None:
                out["parent"] = parent.id
    elif node is None:
        return data  # the node went away since: nothing to say about it
    else:
        out["node"] = summarise(project, node)
        if event == "node.added" and node.parent is not None:
            out["parent"] = node.parent.id
            siblings = node.parent.children
            if siblings and siblings[-1] is not node:
                out["index"] = siblings.index(node)
    others = [other for other in reached if other != node_id]
    if lost or len(others) > ALSO_LIMIT:
        out["resync"] = True
    else:
        also = [summarise(project, n) for n in map(project.find, others) if n is not None]
        if also:
            out["also"] = also
    return out
