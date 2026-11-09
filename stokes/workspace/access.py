"""Reading, writing and removing values in workspace documents by path.

A parsed path (:mod:`stokes.workspace.paths`) is resolved against a document
before it is used. Keys that contain dots, the action ids of ``shortcuts``, are
found by trying the longest run of segments that names an existing key, so
``shortcuts.view.fit`` and ``shortcuts.history.redo.1`` work without brackets.

Writes follow the merge rules: a list or the theme replaces as a whole when
layers merge, so writing inside one first copies it whole from the view the
caller sees (the layers below the scope plus the scope), then changes the copy.

Standard library only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stokes.workspace.errors import WorkspacePathError
from stokes.workspace.paths import JSON, Loc, Segment, as_index, format_path
from stokes.workspace.rules import ListOf, MapOf, Nullable, Spec, json_copy
from stokes.workspace.schema import ATOMIC, DOCUMENT, METADATA, Binding


def _where(loc: Sequence[Segment]) -> str:
    return format_path(loc) or "the document"


def _match_key(
    node: dict[str, JSON], segments: Sequence[Segment], start: int
) -> tuple[str | None, int]:
    """The key at ``segments[start]``, or the longest dotted run of segments that is a key."""
    first = segments[start]
    if isinstance(first, str) and first in node:
        return first, 1
    for end in range(len(segments), start + 1, -1):
        run = segments[start:end]
        if all(isinstance(s, str) for s in run):
            candidate = ".".join(str(s) for s in run)
            if candidate in node:
                return candidate, end - start
    return None, 0


def resolve_existing(document: JSON, segments: Sequence[Segment]) -> Loc:
    """Resolve a path that must exist in ``document`` (reads); raises WorkspacePathError."""
    out: list[Segment] = []
    node = document
    i = 0
    while i < len(segments):
        if isinstance(node, list):
            index = as_index(segments[i])
            if index is None or not 0 <= index < len(node):
                raise WorkspacePathError(
                    f"{_where(out)} is a list of {len(node)}; "
                    f"{segments[i]!r} is not an index into it"
                )
            out.append(index)
            node = node[index]
            i += 1
        elif isinstance(node, dict):
            key, step = _match_key(node, segments, i)
            if key is None:
                raise WorkspacePathError(
                    f"{_where(out)} has no key {segments[i]!r}",
                    details={"path": _where(out), "keys": sorted(node)[:64]},
                )
            out.append(key)
            node = node[key]
            i += step
        else:
            raise WorkspacePathError(f"{_where(out)} is a value; there is nothing below it")
    return tuple(out)


def value_at(document: JSON, loc: Sequence[Segment]) -> JSON:
    node = document
    for segment in loc:
        node = node[segment]
    return node


@dataclass(frozen=True)
class Target:
    """A resolved write: the path, the rule for the value, and the atomic container it is in."""

    loc: Loc
    spec: Spec
    atomic: Loc | None
    """The list or theme that must be copied whole before the write, when the path is inside one."""

    @property
    def root(self) -> Loc:
        """Everything a write can change: the atomic container, or the path itself."""
        return self.atomic if self.atomic is not None else self.loc


def resolve_write(view: JSON, segments: Sequence[Segment]) -> Target:
    """Resolve a path for a write against the scope's view and the schema.

    Raises WorkspacePathError for metadata, unknown keys, bad list indices and paths below a value.
    """
    if isinstance(segments[0], str) and segments[0] in METADATA:
        raise WorkspacePathError(f"{segments[0]} is managed by Stokes and cannot be set")
    out: list[Segment] = []
    node: JSON = view
    spec: Spec = DOCUMENT
    atomic: Loc | None = None
    i = 0
    while i < len(segments):
        if isinstance(spec, Nullable):
            spec = spec.inner
            continue
        if tuple(out) in ATOMIC and atomic is None:
            atomic = tuple(out)
        if isinstance(spec, ListOf) or (isinstance(spec, Binding) and isinstance(node, list)):
            if atomic is None:
                atomic = tuple(out)
            index = _write_index(node, segments, i, out)
            out.append(index)
            node = node[index] if isinstance(node, list) and index < len(node) else None
            i += 1
        elif isinstance(spec, MapOf):
            key, step = _map_key(spec, node, segments, i, out)
            out.append(key)
            node = node.get(key) if isinstance(node, dict) else None
            i += step
        else:
            segment = segments[i]
            child = spec.child(segment) if isinstance(segment, str) else None
            if child is None:
                raise WorkspacePathError(_no_child(spec, out, segment))
            out.append(segment)
            node = node.get(segment) if isinstance(node, dict) else None
            i += 1
        spec = _next_spec(spec, out[-1])
    return Target(tuple(out), spec, atomic)


def _next_spec(spec: Spec, segment: Segment) -> Spec:
    child = spec.child(segment)
    if child is None:  # only a Binding indexed as a list gets here without a child rule
        raise WorkspacePathError(f"nothing in the schema lies below {segment!r}")
    return child


def _write_index(node: JSON, segments: Sequence[Segment], i: int, out: list[Segment]) -> int:
    index = as_index(segments[i])
    length = len(node) if isinstance(node, list) else 0
    last = i == len(segments) - 1
    if index is None or index < 0 or index > length or (index == length and not last):
        raise WorkspacePathError(
            f"{_where(out)} is a list of {length}; use an index from 0 to {length - 1}, "
            f"or {length} to append an entry"
        )
    return index


def _map_key(
    spec: MapOf, node: JSON, segments: Sequence[Segment], i: int, out: list[Segment]
) -> tuple[str, int]:
    key: str | None = None
    step = 0
    if isinstance(node, dict):
        key, step = _match_key(node, segments, i)
    if key is None:
        rest = segments[i:] if spec.dotted else segments[i : i + 1]
        if not all(isinstance(s, str) for s in rest):
            raise WorkspacePathError(f"{_where(out)} takes a key here, not {segments[i]!r}")
        key, step = ".".join(str(s) for s in rest), len(rest)
    verdict, why = spec.key_rule(key)
    if verdict is not None:
        raise WorkspacePathError(f"{_where([*out, key])}: {why}")
    return key, step


def _no_child(spec: Spec, out: list[Segment], segment: Segment) -> str:
    fields = getattr(spec, "fields", None) or getattr(spec, "FIELDS", None)
    if fields:
        return f"{_where(out)} has no key {segment!r}; known keys: {', '.join(fields)}"
    return f"{_where(out)} is a value; there is nothing below it"


def assign(document: dict[str, JSON], target: Target, value: JSON, view: JSON) -> None:
    """Write ``value`` at ``target`` in a layer document; atomic containers come from ``view``."""
    node: JSON = document
    shadow: JSON = view
    loc = target.loc
    for depth, segment in enumerate(loc[:-1]):
        shadow = _child(shadow, segment)
        prefix = loc[: depth + 1]
        if isinstance(node, dict):
            current = node.get(segment)
            if target.atomic is not None and prefix == target.atomic:
                empty: JSON = [] if isinstance(loc[depth + 1], int) else {}
                current = json_copy(shadow) if shadow is not None else empty
                node[segment] = current
            elif not isinstance(current, dict | list):
                current = {}
                node[segment] = current
            node = current
        else:
            node = node[segment]
    last = loc[-1]
    if isinstance(node, list) and isinstance(last, int):
        if last == len(node):
            node.append(value)
        else:
            node[last] = value
    elif isinstance(node, dict) and isinstance(last, str):
        node[last] = value
    else:
        raise WorkspacePathError(f"cannot write at {_where(loc)}")


def _child(node: JSON, segment: Segment) -> JSON:
    if isinstance(node, dict) and isinstance(segment, str):
        return node.get(segment)
    if isinstance(node, list) and isinstance(segment, int) and 0 <= segment < len(node):
        return node[segment]
    return None


def remove(document: dict[str, JSON], segments: Sequence[Segment]) -> Loc | None:
    """Remove a key from a layer document and prune objects left empty.

    Returns the removed path, or None when the document does not hold it. List
    entries cannot be removed this way: a list is one value.
    """
    out: list[Segment] = []
    trail: list[dict[str, JSON]] = []
    node: JSON = document
    i = 0
    while i < len(segments):
        if isinstance(node, list):
            raise WorkspacePathError(
                f"{_where(out)} is a list, which is one value: reset {_where(out)} as a whole"
            )
        if not isinstance(node, dict):
            return None
        key, step = _match_key(node, segments, i)
        if key is None:
            return None
        trail.append(node)
        out.append(key)
        node = node[key]
        i += step
    del trail[-1][str(out[-1])]
    for depth in range(len(trail) - 1, 0, -1):
        if not trail[depth]:
            del trail[depth - 1][str(out[depth - 1])]
    return tuple(out)
