"""Tags through an operation's history (ADR-0021 decision 8.6).

When an output arrives, every tag that holds faces of the parts the operation read receives, under
the same tag, the output faces those faces became: the output's member of the tag is replaced by
the mapped faces, at the output's version (its ``basis``). Faces the history maps nowhere are
collected per tag and part and reported once the run is over (``report.unmapped``), never dropped
silently: faces the operation removed (the caps of a tool that cut through) are listed there by
design. A member picked on an earlier version of its part (stale) is not mapped: its ids number
another B-rep, and the report says so. Tags the output names itself (``OpOutput.tags``: the
enclosure's ``inlet``, the caps of ``cap``) are created, kind ``boundary``, or extended.

These are model edits outside the undo stack (the run's, not the user's): the operation's undo
strips the outputs from every tag again (:func:`strip`) and removes the tags its runs created.
The functions here run where commands run, and return the events to emit.

Import rule: the standard library, the model, and the light members module of the tag commands
(:mod:`stokes.geometry.selection`, numpy-backed); no OCP.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stokes.errors import StokesError
from stokes.geometry.model import (
    GEOMETRY_PATH,
    SELECTION_PALETTE,
    TAG_TYPE,
    TAGS_GROUP,
    TAGS_PATH,
)
from stokes.model.ids import new_id, stable_id

if TYPE_CHECKING:
    from stokes.geometry.selection import Member
    from stokes.geometry.worker import ShapeResult
    from stokes.model.nodes import Node
    from stokes.model.project import Project

Event = tuple[str, dict[str, Any]]  # Any: JSON payload


@dataclass
class Carried:
    """What one run carried, across its outputs: per (tag, input part), the input ids it was asked
    to map and the ones some output received; the tags it created; the stale members.

    **Edges and vertices are tracked beside faces (T-056-fix).** They used to be dropped silently: a
    tag holding edges lost them even when the operation never touched them, and nothing said so.
    §15.1
    requires the loss to be *reported*, and the owner's answer (via Claude, 2026-10-12) is that
    dropping them is not an accepted deviation — so each id set is carried and each is reported.
    """

    wanted: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    mapped: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)
    stale: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)  # Any: JSON
    """Per (tag, input part): the member not mapped because it was stale, once for all outputs."""

    # T-056-fix: the same two dictionaries per kind, so an edge or a vertex is carried and reported
    # exactly as a face is, rather than being dropped where nobody looks.
    wanted_edges: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    mapped_edges: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    wanted_vertices: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    mapped_vertices: dict[tuple[str, str], set[int]] = field(default_factory=dict)

    def unmapped(self, project: Project) -> list[dict[str, Any]]:  # Any: JSON
        """``[{tag, name, part, faces|edges|vertices: [ids], count}]``: ids of a tag no output
        received, and the members not mapped because they were stale.

        One row per kind rather than one per tag: a tag holding faces, edges and vertices whose
        members all failed reports three rows, because the caller acts on them differently (a lost
        face can be re-picked; a lost edge may need the part re-matched).
        """
        rows: list[dict[str, Any]] = []
        kinds = (
            ("faces", self.wanted, self.mapped),
            ("edges", self.wanted_edges, self.mapped_edges),
            ("vertices", self.wanted_vertices, self.mapped_vertices),
        )
        for label, wanted_all, mapped_all in kinds:
            for (tag_id, part), wanted in wanted_all.items():
                left = sorted(wanted - mapped_all.get((tag_id, part), set()))
                if not left:
                    continue
                tag = project.find(tag_id)
                name = tag.name if tag is not None else tag_id
                rows.append(
                    {"tag": tag_id, "name": name, "part": part, label: left, "count": len(left)}
                )
        return rows + list(self.stale.values())

    @property
    def mapped_faces(self) -> int:
        return sum(len(v) for v in self.mapped.values())

    @property
    def carried_edges(self) -> int:
        """How many edge ids were carried, for the run's report.

        `.values()` matters and I got it wrong first: iterating a dict yields its KEYS, so the first
        version summed the length of each (tag, part) tuple — always 2 — instead of the ids carried.
        """
        return sum(len(v) for v in self.mapped_edges.values())

    @property
    def carried_vertices(self) -> int:
        """How many vertex ids were carried, for the run's report."""
        return sum(len(v) for v in self.mapped_vertices.values())


def tags_of(project: Project) -> Iterator[Node]:
    """The tag nodes under ``/geometry/tags``, at any depth."""
    group = project.find(TAGS_PATH)
    if group is None:
        return
    for node in group.walk():
        if node.type == TAG_TYPE:
            yield node


def carry(
    project: Project,
    output: Node,
    shape: ShapeResult,
    bases: dict[str, str],
    carried: Carried,
) -> list[Event]:
    """Give every tag holding faces of the parts in ``shape.history`` the output faces they
    became; then the tags the output names itself. ``bases`` is each input part's version at the
    run, so stale members are told apart."""
    from stokes.geometry.selection import Member, merge

    events: list[Event] = []
    basis = str(output.params.get("basis") or "") or None
    for tag in list(tags_of(project)):
        members = _members(tag)
        if members is None:
            continue
        faces: set[int] = set()
        edges: set[int] = set()
        vertices: set[int] = set()
        touched = False
        for member in members:
            history = shape.history.get(member.part)
            if history is None:
                continue
            if member.basis is not None and member.basis != bases.get(member.part, member.basis):
                ids = _ids(project, member)
                row = {"tag": tag.id, "name": tag.name, "part": member.part, "faces": ids}
                carried.stale[(tag.id, member.part)] = {**row, "count": len(ids), "reason": "stale"}
                continue
            key = (tag.id, member.part)
            # Faces, then edges and vertices (T-056-fix). Each kind is wanted, mapped and carried
            # through the maps the worker sent, so an id that survives is kept and one that does not
            # is reported by `unmapped` rather than disappearing.
            for kind, ids, out_set, wanted_at, mapped_at in (
                ("faces", _ids(project, member), faces, carried.wanted, carried.mapped),
                ("edges", _member_edges(member), edges, carried.wanted_edges, carried.mapped_edges),
                (
                    "vertices",
                    _member_vertices(member),
                    vertices,
                    carried.wanted_vertices,
                    carried.mapped_vertices,
                ),
            ):
                if not ids:
                    continue
                wanted_at.setdefault(key, set()).update(ids)
                got = mapped_at.setdefault(key, set())
                table = {
                    "faces": shape.history,
                    "edges": shape.history_edges,
                    "vertices": shape.history_vertices,
                }[kind].get(member.part)
                if table is None:
                    continue
                for item in ids:
                    found = table.get(item)
                    if found:
                        got.add(item)
                        out_set.update(found)
            touched = True
        if not touched:
            continue
        # One member carrying all three kinds, so a tag's faces, edges and vertices move together
        # (a member may hold any combination — ADR-0022 §15.4). The field order is
        # `(part, faces, basis, edges, vertices)`.
        if not (faces or edges or vertices):
            events.extend(_set_member(project, tag, output.id, None))
            continue
        mapped = Member(
            output.id,
            merge((f, f) for f in sorted(faces)),
            basis,
            merge((e, e) for e in sorted(edges)),
            merge((v, v) for v in sorted(vertices)),
        )
        events.extend(_set_member(project, tag, output.id, mapped))
    for name, named in shape.tags.items():
        if named:
            ranges = merge((f, f) for f in sorted(set(named)))
            events.extend(_named(project, output, name, Member(output.id, ranges, basis), carried))
    return events


def strip(project: Project, parts: Iterable[str], created: Sequence[str] = ()) -> list[Event]:
    """Take ``parts`` (an operation's outputs) out of every tag, and remove the tags in
    ``created`` that are left empty and that nothing refers to: the undo of what runs carried."""
    gone = set(parts)
    events: list[Event] = []
    for tag in list(tags_of(project)):
        members = _members(tag)
        if members is None or not any(m.part in gone for m in members):
            continue
        events.extend(_store(project, tag, [m for m in members if m.part not in gone]))
    for tag_id in created:
        made = project.find(tag_id)
        if made is None or made.type != TAG_TYPE or _members(made):
            continue
        if project.referrers(made.id):
            continue  # a boundary or region uses it: it stays, empty
        path = made.path
        project.remove(made.id)
        events.append(("node.removed", {"node_id": tag_id, "path": path}))
    return events


# ---------------------------------------------------------------- helpers


def _members(tag: Node) -> tuple[Member, ...] | None:
    """A tag's members, or None when they are malformed (the tag commands report those)."""
    import json

    from stokes.geometry.selection import load_members

    raw = tag.params.get("members")
    if not isinstance(raw, str):
        raw = json.dumps(raw if raw is not None else [])
    try:
        return load_members(raw or "[]", where=tag.path)
    except StokesError:
        return None


def _ids(project: Project, member: Member) -> list[int]:
    """A member's face ids; a whole part needs its face count (none when it is unknown)."""
    if member.faces is None:
        node = project.find(member.part)
        count = node.params.get("faces") if node is not None else None
        return list(range(count)) if isinstance(count, int) and count > 0 else []
    return [int(i) for i in member.ids(None)]


def _member_edges(member: Member) -> list[int]:
    """A member's explicit edge ids, expanded (T-056-fix).

    `member.edges` is a tuple of **inclusive ranges**, not of ids, so iterating it yields pairs —
    which is what my first version did, and it failed the run with "int() argument must be … not
    'tuple'". `ids_of` does the expansion, and None ("every edge") needs a count the member does not
    carry, so an all-edges member is skipped rather than guessed at.
    """
    from stokes.geometry.selection import ids_of

    if member.edges is None:
        return []
    return [int(i) for i in ids_of(member.edges, None)]


def _member_vertices(member: Member) -> list[int]:
    """A member's explicit vertex ids, expanded (T-056-fix); see `_member_edges`."""
    from stokes.geometry.selection import ids_of

    if member.vertices is None:
        return []
    return [int(i) for i in ids_of(member.vertices, None)]


def _set_member(project: Project, tag: Node, part: str, member: Member | None) -> list[Event]:
    """Replace ``part``'s member of ``tag`` (in place, or appended), or take it out."""
    members = _members(tag) or ()
    out: list[Member] = []
    placed = False
    for old in members:
        if old.part == part:
            if member is not None:
                out.append(member)
            placed = True
        else:
            out.append(old)
    if not placed and member is not None:
        out.append(member)
    return _store(project, tag, out)


def _store(project: Project, tag: Node, members: Sequence[Member]) -> list[Event]:
    from stokes.geometry.selection import dump_members

    text = dump_members(members)
    if text == tag.params.get("members"):
        return []
    project.set_param(tag.id, "members", text)
    return [
        ("node.changed", {"node_id": tag.id, "path": tag.path, "param": "members"}),
        ("tag.changed", {"node_id": tag.id, "path": tag.path}),
    ]


def _named(
    project: Project, output: Node, name: str, member: Member, carried: Carried
) -> list[Event]:
    """A tag the output names: extended when one of that name exists, else created (kind
    boundary, the next palette colour, an id derived from the operation's)."""
    from stokes.geometry.files import node_name_for
    from stokes.geometry.operations import tag_id
    from stokes.geometry.selection import dump_members

    events: list[Event] = []
    tag_name = node_name_for(name, "tag")
    group = project.find(TAGS_PATH)
    if group is None:
        wanted = stable_id("g", TAGS_PATH)
        group = project.add(
            GEOMETRY_PATH, "group", TAGS_GROUP, node_id=None if project.find(wanted) else wanted
        )
        events.append(("node.added", {"node_id": group.id, "path": group.path}))
    existing = group.child(tag_name)
    if existing is not None:
        if existing.type != TAG_TYPE:
            return events  # a legacy plain group of that name: left alone
        return events + _set_member(project, existing, output.id, member)
    op = str(output.params.get("derived") or "")
    wanted_id = tag_id(op, tag_name) if op else new_id()
    taken = sum(1 for c in group.children if c.type == TAG_TYPE)
    params = {
        "members": dump_members([member]),
        "kind": "boundary",
        "color": SELECTION_PALETTE[taken % len(SELECTION_PALETTE)],
    }
    tag = project.add(
        group.id,
        TAG_TYPE,
        tag_name,
        params,
        node_id=None if project.find(wanted_id) else wanted_id,
    )
    carried.created.append(tag.id)
    events.append(("node.added", {"node_id": tag.id, "path": tag.path}))
    events.append(("tag.changed", {"node_id": tag.id, "path": tag.path}))
    return events
