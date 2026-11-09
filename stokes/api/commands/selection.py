"""tag.* commands (ADR-0020 slice 1): tags of B-rep faces; and the geometry
tools that read parts and tags: ``geometry.check``, ``geometry.export_stl`` and
``geometry.export_step``.

A tag is a ``tag`` node under ``/geometry/tags`` whose
``members`` list faces per part (contract: ``docs/engineering/geometry-schema.md``). Every change
of a definition is a model command (AddNode, SetParam, RenameNode, RemoveNode), one undo step;
``node.*`` and ``parameter.set`` work on these nodes as on any other (colour, visibility, kind).
Commands that compute members (``add``, ``remove``, ``invert``, ``grow``, ``rematch``) journal
what they produced as ``result``, and a replay applies it as it was: it needs no mesh, so it gives
the same tag while imports are still running.

Members with explicit faces carry the B-rep version (``basis``) of their part; once the part's
file changes and it is re-imported, the member is stale: commands that would mix numberings
refuse with ``tag.stale`` until ``tag.rematch`` has mapped the faces by fingerprint
(:mod:`stokes.geometry.fingerprints`), listing the faces it could not place.

Events::

    node.added {node_id, path}           tag.create: the group when new, then the tag
    node.changed {node_id, path, param}  members (param "members"); a rename (no param);
                                         geometry.check, per part checked (meta.check): once by the
                                         command, once when the B-rep job finishes
    node.removed {node_id, path}         tag.delete (with force: the boundaries and regions too)
    geometry.check_progress {node_id, path, index, count}    the B-rep job, one per part
    geometry.check_failed {node_id, path, code, message}     a part the B-rep job could not check
    tag.changed {node_id, path}    after every change of a tag's definition
    boundary.changed, region.changed     tag.delete with force, per boundary and region deleted

``tag.list`` names the boundaries and regions that use each tag and warns (``tag.overlap``) where
two boundary-kind tags share faces of a part; ``tag.delete`` refuses a tag in use
(``model.referenced``) unless ``force`` (ADR-0022 decision 2.8).

Errors: ``tag.invalid``, ``tag.stale``, ``geometry.no_mesh``, ``geometry.unreadable``
(STEP export), ``model.referenced``, and ``validation``, ``model.unknown_node``, ``model.error``
as everywhere.

The geometry tools live here, not in ``geometry.py`` beside the import, because they belong to
this slice and consume tags; the module is long for the same reason (nineteen commands).

Import rule: ``stokes.geometry.model`` (standard library) at module level, to register the node
type: ``stokes.model.schema`` may not import ``stokes.geometry`` (layering gate), so the API
registers it. numpy, scipy, the store and the rest of ``stokes.geometry`` load in the handlers.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import UnknownNode, ValidationError
from stokes.geometry.errors import SelectionError
from stokes.geometry.model import (
    GEOMETRY_PATH,
    GROW_ANGLE,
    SELECTION_KINDS,
    SELECTION_PALETTE,
    TAG_TYPE,
    TAGS_GROUP,
    TAGS_PATH,
)
from stokes.model import AddNode, Composite, RemoveNode, RenameNode, SetParam
from stokes.model.ids import stable_id

if TYPE_CHECKING:
    from stokes.geometry.export import Solid
    from stokes.geometry.mesh import PartMesh
    from stokes.geometry.related import F64, I64, Mask
    from stokes.geometry.selection import Member, Target
    from stokes.geometry.selection_cache import SelectionCache
    from stokes.geometry.store import GeometryStore
    from stokes.model import Node, Project


PARTS_PATH = f"{GEOMETRY_PATH}/parts"
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TAG = ParamDef(
    "tag",
    "string",
    description="Tag: node id, path (/geometry/tags/inlet) or name.",
)
MEMBERS_HELP = (
    'A list of {"part": <part id or path>, "faces": "all" or [face id or [first, last], ...]} '
    '(inclusive ranges); a group with faces "all" stands for every part under it.'
)
RESULT = ParamDef(
    "result",
    "array",
    required=False,
    description="Journals only: the members this command produced; a replay applies them "
    "instead of computing them again.",
)
NODE_ID = ParamDef("node_id", "string", required=False, description="Reuse an id (journals).")


# ---------------------------------------------------------------- lookups


class _Geometry:
    """What one command needs to know about parts: references, face counts, meshes, versions."""

    def __init__(self, ctx: CommandContext) -> None:
        from stokes.geometry.selection_cache import SelectionCache
        from stokes.geometry.store import GeometryStore

        self.session = ctx.session
        self.project: Project = ctx.session.require_project()
        self.store: GeometryStore = GeometryStore.of(ctx.session)
        self.cache: SelectionCache = SelectionCache.of(ctx.session)
        self._meshes: dict[str, PartMesh | None] = {}
        self._bases: dict[str, str | None] = {}

    def part_nodes(self, ref: str) -> list[Node]:
        """The parts a reference names: a part, or every part under a group."""
        from stokes.geometry.errors import SelectionError
        from stokes.geometry.store import parts_under

        node = self.project.get(ref)
        if node.type == "part":
            return [node]
        if node.type == "group":
            found = list(parts_under(node))
            if not found:
                raise SelectionError(f"{node.path} holds no parts", details={"node": node.path})
            return found
        raise ValidationError(
            f"{node.path} is a {node.type}, not a part or a group of parts",
            details={"node": node.path},
        )

    def targets(self, ref: str) -> list[Target]:
        from stokes.geometry.selection import Target

        return [Target(p.id, self.count(p.id)) for p in self.part_nodes(ref)]

    def node(self, part: str) -> Node | None:
        node = self.project.find(part)
        return node if node is not None and node.type == "part" else None

    def count(self, part: str) -> int | None:
        """The part's face count: its ``faces`` parameter, else its mesh's; None if unknown."""
        from stokes.geometry.topology import part_face_count

        node = self.node(part)
        if node is None:
            return None
        faces = node.params.get("faces")
        if isinstance(faces, int) and not isinstance(faces, bool) and faces > 0:
            return faces
        mesh = self.mesh(part)
        return part_face_count(mesh) if mesh is not None and mesh.faces else None

    def mesh(self, part: str, *, load: bool = True) -> PartMesh | None:
        """The part's current mesh; with ``load`` false only one already in memory (a mesh read
        from the cache costs milliseconds, thousands of them seconds)."""
        if part not in self._meshes:
            if self.node(part) is None:
                self._meshes[part] = None
            elif load or self.store.status(part).get("status") == "ready":
                self._meshes[part] = self.store.mesh(part)
            else:
                return None
        return self._meshes[part]

    def need_mesh(self, part: str) -> PartMesh:
        from stokes.geometry.errors import MeshUnavailable

        mesh = self.mesh(part)
        if mesh is None:
            node = self.node(part)
            where = node.path if node is not None else part
            raise MeshUnavailable(
                f"{where} has no mesh yet: wait for its import, or import it again",
                details={"part": part},
            )
        return mesh

    def basis(self, part: str, *, load: bool = True) -> str | None:
        """The B-rep version of the part's current mesh: **the store's answer, not a second one**.

        Claude's defect 6. This used to compute its own through `basis_of(mesh, solid, node)`,
        which returns the node's **stored** `basis` — while `GeometryStore.basis` **recomputes** it
        from the node's current units. Two definitions that can disagree, and disagreeing is
        exactly the failure they exist to prevent: a tag stamped against the stored value and
        checked
        against the recomputed one reads as stale for no reason, which is how a main-era
        BREP/STL/OBJ project with non-default units (an STL in metres is common) had every tag go
        stale on a re-import.

        Delegating also means the units alias change (`inch` and `in` now hash alike) reaches both
        sides at once, because there is only one side.
        """
        if part not in self._bases:
            node = self.node(part)
            if node is None:
                # No node: nothing to ask the store about. `load=False` means "do not open files",
                # and an unknown part is the same answer either way.
                return None
            if not load and self.store.status(part).get("status") != "ready":
                return None  # not in memory: unknown, and not remembered as such
            stored = self.store.basis(node)
            self._bases[part] = stored or None
        return self._bases[part]

    def path(self, part: str) -> str:
        node = self.node(part)
        return node.path if node is not None else part

    def stale(self, member: Member, *, load: bool = True) -> bool:
        """Whether the member's faces refer to an earlier B-rep version of its part; with
        ``load`` false, known only for meshes in memory (summaries and lists)."""
        if member.faces is None or member.basis is None:
            return False
        now = self.basis(member.part, load=load)
        return now is not None and now != member.basis


def _selection(project: Project, ref: str) -> Node:
    """A tag by id, path or name."""
    node = project.find(ref)
    if node is None and "/" not in ref and "." not in ref:
        group = project.find(TAGS_PATH)
        node = group.child(ref) if group is not None else None
    if node is None:
        raise UnknownNode(f"no tag {ref!r}", details={"tag": ref})
    if node.type != TAG_TYPE:
        legacy = node.parent is not None and node.parent.path == TAGS_PATH
        hint = (
            "; it is a plain group from before tags had a type: delete it and create a tag"
            if legacy
            else ""
        )
        raise ValidationError(
            f"{node.path} is a {node.type}, not a tag{hint}",
            details={"tag": node.path},
        )
    return node


def _members(node: Node) -> tuple[Member, ...]:
    from stokes.geometry.selection import load_members

    raw = node.params.get("members")
    if not isinstance(raw, str):  # a list set through parameter.set: the commands write text
        raw = json.dumps(raw if raw is not None else [])
    return load_members(raw or "[]", where=node.path)


def _color(text: str) -> str:
    if not _HEX_RE.match(text):
        raise ValidationError(f"a colour is #rrggbb; got {text!r}", details={"color": text})
    return text.lower()


def _part_refs(geo: _Geometry, refs: Any) -> list[str]:  # Any: JSON array
    if not isinstance(refs, list) or not all(isinstance(r, str) and r for r in refs):
        raise ValidationError("parts must be a list of part or group ids or paths")
    found: dict[str, None] = {}
    for ref in refs:
        for node in geo.part_nodes(ref):
            found.setdefault(node.id)
    return list(found)


# ---------------------------------------------------------------- members of a tag


def _require_current(
    geo: _Geometry, node: Node, members: Iterable[Member], parts: set[str]
) -> None:
    """Refuse to combine face ids of an earlier B-rep version with today's."""
    from stokes.geometry.errors import SelectionStale

    for member in members:
        if member.part in parts and geo.stale(member):
            raise SelectionStale(
                f"{node.path} refers to an earlier version of {geo.path(member.part)} (its file "
                "changed); run tag.rematch first",
                details={"tag": node.path, "part": member.part},
            )


def _changed(old: Sequence[Member], new: Sequence[Member]) -> set[str]:
    before = {m.part: m.faces for m in old}
    return {m.part for m in new if m.part not in before or before[m.part] != m.faces}


def _stamp(geo: _Geometry, members: Sequence[Member], parts: set[str]) -> tuple[Member, ...]:
    """Explicit members of ``parts`` take their part's current B-rep version; the fingerprints
    of that version are kept for a later re-match.

    A member is explicit when it names any id at all — faces, edges or vertices — because all
    three are numbered against the same basis (ADR-0022 §15.1). An edge-only member must be
    stamped too, or its ids would be read against whatever version preceded the re-import.
    """
    from stokes.geometry.selection import Member

    stamped: list[Member] = []
    keep: dict[str, str] = {}
    for member in members:
        if member.explicit and member.part in parts:
            basis = geo.basis(member.part)
            stamped.append(Member(member.part, member.faces, basis, member.edges, member.vertices))
            if basis is not None:
                keep[member.part] = basis
        else:
            stamped.append(member)
    if keep:
        meshes = {p: m for p in keep if (m := geo.mesh(p)) is not None}
        prints = geo.cache.fingerprints(meshes)
        geo.cache.keep({(p, b): prints[p] for p, b in keep.items() if p in prints})
    return tuple(stamped)


def _size(geo: _Geometry, members: Sequence[Member]) -> int | None:
    total = 0
    for member in members:
        size = member.size(geo.count(member.part))
        if size is None:
            return None
        total += size
    return total


def _summary(geo: _Geometry, node: Node, members: Sequence[Member] | None = None) -> dict[str, Any]:
    found = _members(node) if members is None else members
    return {
        "id": node.id,
        "path": node.path,
        "name": node.name,
        "type": node.type,
        "kind": node.params.get("kind"),
        "color": node.params.get("color"),
        "visible": node.params.get("visible"),
        "members": [m.to_json() for m in found],
        "parts": len(found),
        "faces": _size(geo, found),
        "stale": [m.part for m in found if geo.stale(m, load=False)],
    }


def _apply(
    ctx: CommandContext,
    geo: _Geometry,
    node: Node,
    members: Sequence[Member],
    *,
    verb: str,
    stamp: set[str] | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """Store new members as one undo step (when they differ), journal them as ``result``
    (unless the command's input is the result already), emit the events.

    Members whose faces did not change keep the version they had: a replayed ``result`` carries
    none, and its unchanged members must not lose theirs."""
    from stokes.geometry.selection import Member, dump_members

    old = _members(node)
    before = {m.part: m for m in old}
    kept: list[Member] = []
    for member in members:
        prior = before.get(member.part)
        # Compare the id sets only: on replay `member.basis` is None (the recorded result drops it)
        # and restoring the prior basis is exactly what this block is for.
        same = (
            member.basis is None
            and prior is not None
            and prior.faces == member.faces
            and prior.edges == member.edges
            and prior.vertices == member.vertices
        )
        kept.append(
            Member(member.part, member.faces, prior.basis, member.edges, member.vertices)
            if same and prior is not None
            else member
        )
    final = _stamp(geo, kept, _changed(old, kept) if stamp is None else stamp)
    if record:
        ctx.record(result=[m.to_json(basis=False) for m in final])
    text = dump_members(final)
    changed = text != node.params.get("members")
    if changed:
        step = Composite(f"{verb} {node.name}", [SetParam(node.id, "members", text)])
        ctx.session.undo.push(step, geo.project)
        ctx.session.emit("node.changed", node_id=node.id, path=node.path, param="members")
        ctx.session.emit("tag.changed", node_id=node.id, path=node.path)
    return {**_summary(geo, node, final), "changed": changed}


def _given(geo: _Geometry, raw: Any) -> tuple[Member, ...]:  # Any: JSON members
    from stokes.geometry.selection import parse_members

    return parse_members(raw, geo.targets)


# ---------------------------------------------------------------- tag.*


@command(
    "tag.create",
    params=(
        ParamDef("name", "string", description="Tag name (letters, digits, underscores)."),
        ParamDef("members", "array", required=False, description=MEMBERS_HELP),
        ParamDef(
            "kind",
            "string",
            required=False,
            default=SELECTION_KINDS[0],
            description="boundary (one mesh patch), region (whole parts: one mesh region) or "
            "plain (a working set).",
        ),
        ParamDef(
            "color",
            "string",
            required=False,
            description="#rrggbb; default: the next colour of the tag palette.",
        ),
        ParamDef("visible", "boolean", required=False, default=True),
        NODE_ID,
    ),
)
def selection_create(
    ctx: CommandContext,
    name: str,
    members: list[Any] | None = None,  # Any: JSON members
    kind: str = SELECTION_KINDS[0],
    color: str | None = None,
    visible: bool = True,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Create a tag under /geometry/tags (and the group when missing),
    as one undo step."""
    from stokes.geometry.selection import dump_members

    if kind not in SELECTION_KINDS:
        raise ValidationError(f"kind must be one of {', '.join(SELECTION_KINDS)}; got {kind!r}")
    geo = _Geometry(ctx)
    project = geo.project
    group = project.find(TAGS_PATH)
    if group is not None and group.type != "group":
        raise ValidationError(f"{TAGS_PATH} is a {group.type}, not a group")
    if color is None:
        taken = sum(1 for c in group.children if c.type == TAG_TYPE) if group else 0
        color = SELECTION_PALETTE[taken % len(SELECTION_PALETTE)]
    color = _color(color)
    chosen = _given(geo, members or [])
    final = _stamp(geo, chosen, {m.part for m in chosen})
    steps: list[Any] = []  # Any: model commands
    group_step: AddNode | None = None
    if group is None:
        group_id = stable_id("g", TAGS_PATH)
        group_step = AddNode(
            GEOMETRY_PATH,
            "group",
            TAGS_GROUP,
            node_id=None if project.find(group_id) else group_id,
        )
        steps.append(group_step)
    params = {"members": dump_members(final), "kind": kind, "color": color, "visible": visible}
    add = AddNode(TAGS_PATH, TAG_TYPE, name, params, node_id=node_id)
    steps.append(add)
    ctx.session.undo.push(Composite(f"create tag {name}", steps), project)
    ctx.record(node_id=add.node_id, color=color)
    if group_step is not None:
        ctx.session.emit("node.added", node_id=group_step.node_id, path=TAGS_PATH)
    node = project.get(str(add.node_id))
    ctx.session.emit("node.added", node_id=node.id, path=node.path)
    ctx.session.emit("tag.changed", node_id=node.id, path=node.path)
    return _summary(geo, node, final)


@command(
    "tag.add",
    params=(TAG, ParamDef("members", "array", description=MEMBERS_HELP), RESULT),
)
def selection_add(
    ctx: CommandContext,
    tag: str,
    members: list[Any],  # Any: JSON members
    result: list[Any] | None = None,  # Any: JSON members
) -> dict[str, Any]:
    """Add faces or whole parts to a tag."""
    from stokes.geometry.selection import union

    geo = _Geometry(ctx)
    node = _selection(geo.project, tag)
    if result is not None:
        return _apply(ctx, geo, node, _given(geo, result), verb="add to")
    current, extra = _members(node), _given(geo, members)
    _require_current(geo, node, current, {m.part for m in extra})
    return _apply(ctx, geo, node, union(current, extra), verb="add to")


@command(
    "tag.remove",
    params=(TAG, ParamDef("members", "array", description=MEMBERS_HELP), RESULT),
)
def selection_remove(
    ctx: CommandContext,
    tag: str,
    members: list[Any],  # Any: JSON members
    result: list[Any] | None = None,  # Any: JSON members
) -> dict[str, Any]:
    """Take faces or whole parts out of a tag (faces out of a whole part leave the rest)."""
    from stokes.geometry.selection import difference

    geo = _Geometry(ctx)
    node = _selection(geo.project, tag)
    if result is not None:
        return _apply(ctx, geo, node, _given(geo, result), verb="remove from")
    current, drop = _members(node), _given(geo, members)
    _require_current(geo, node, current, {m.part for m in drop})
    return _apply(ctx, geo, node, difference(current, drop, geo.count), verb="remove from")


@command("tag.set", params=(TAG, ParamDef("members", "array", description=MEMBERS_HELP)))
def selection_set(
    ctx: CommandContext,
    tag: str,
    members: list[Any],  # Any: JSON members
) -> dict[str, Any]:
    """Replace a tag's members."""
    geo = _Geometry(ctx)
    node = _selection(geo.project, tag)
    chosen = _given(geo, members)
    return _apply(ctx, geo, node, chosen, verb="set", stamp={m.part for m in chosen}, record=False)


@command(
    "tag.invert",
    params=(
        TAG,
        ParamDef(
            "parts",
            "array",
            description="Parts or groups (ids or paths) to invert within, e.g. the parts in view.",
        ),
        RESULT,
    ),
)
def selection_invert(
    ctx: CommandContext,
    tag: str,
    parts: list[Any],  # Any: JSON references
    result: list[Any] | None = None,  # Any: JSON members
) -> dict[str, Any]:
    """Within the given parts, select exactly the faces the tag lacks."""
    from stokes.geometry.selection import invert

    geo = _Geometry(ctx)
    node = _selection(geo.project, tag)
    if result is not None:
        return _apply(ctx, geo, node, _given(geo, result), verb="invert")
    within = _part_refs(geo, parts)
    current = _members(node)
    _require_current(geo, node, current, set(within))
    return _apply(ctx, geo, node, invert(current, within, geo.count), verb="invert")


@command(
    "tag.grow",
    params=(
        TAG,
        ParamDef(
            "angle_deg",
            "number",
            required=False,
            default=GROW_ANGLE,
            description="Feature angle in degrees: growth crosses a face boundary whose sides "
            "meet at a smaller dihedral angle.",
        ),
        ParamDef(
            "seeds",
            "array",
            required=False,
            description="Faces to grow from, as members; default: the tag's own faces.",
        ),
        RESULT,
    ),
)
def selection_grow(
    ctx: CommandContext,
    tag: str,
    angle_deg: float = GROW_ANGLE,
    seeds: list[Any] | None = None,  # Any: JSON members
    result: list[Any] | None = None,  # Any: JSON members
) -> dict[str, Any]:
    """Add every face reachable from the seeds without crossing an edge sharper than the
    feature angle (over each part's tessellation)."""
    from stokes.geometry.errors import SelectionError
    from stokes.geometry.selection import Member, grow_parts, union
    from stokes.geometry.topology import part_face_count

    geo = _Geometry(ctx)
    node = _selection(geo.project, tag)
    if result is not None:
        return _apply(ctx, geo, node, _given(geo, result), verb="grow")
    current = _members(node)
    start = _given(geo, seeds) if seeds is not None else current
    if not start:
        raise SelectionError(
            "nothing to grow from: give seed faces, or select faces first",
            details={"tag": node.path},
        )
    explicit = [m for m in start if m.faces is not None]
    parts = {m.part for m in explicit}
    _require_current(geo, node, current, parts)
    meshes = {p: geo.need_mesh(p) for p in parts}
    regions = grow_parts(
        geo.cache.adjacency(meshes),
        {p: part_face_count(m) for p, m in meshes.items()},
        {m.part: m.ids(None) for m in explicit},
        float(angle_deg),
    )
    grown: list[Member] = [m for m in start if m.faces is None]  # whole parts are complete
    grown.extend(Member(part, faces) for part, faces in regions.items() if faces)
    before = _size(geo, current)
    reply = _apply(ctx, geo, node, union(current, grown), verb="grow")
    after = reply["faces"]
    reply["added"] = None if before is None or after is None else after - before
    return reply


@command(
    "tag.region",
    params=(
        ParamDef("members", "array", description=MEMBERS_HELP),
        ParamDef(
            "angle_deg",
            "number",
            required=False,
            default=GROW_ANGLE,
            description="Feature angle in degrees: the region grows across a face boundary "
            "whose sides meet at a smaller dihedral angle.",
        ),
    ),
    mutates=False,
)
def selection_region(
    ctx: CommandContext,
    members: list[Any],  # Any: JSON members
    angle_deg: float = GROW_ANGLE,
) -> dict[str, Any]:
    """The faces a tag over the given members would grow to: the region around them, grown
    across every edge no sharper than the feature angle, as members. Nothing is written, so
    the context toolbar's Grow and the double-click use it before a tag exists."""
    from stokes.geometry.errors import SelectionError
    from stokes.geometry.selection import Member, grow_parts
    from stokes.geometry.topology import part_face_count

    geo = _Geometry(ctx)
    start = _given(geo, members)
    if not start:
        raise SelectionError("nothing to grow from: give faces, or whole parts")
    explicit = [m for m in start if m.faces is not None]
    meshes = {p: geo.need_mesh(p) for p in {m.part for m in explicit}}
    regions = grow_parts(
        geo.cache.adjacency(meshes),
        {p: part_face_count(m) for p, m in meshes.items()},
        {m.part: m.ids(None) for m in explicit},
        float(angle_deg),
    )
    out = [m for m in start if m.faces is None]  # whole parts are complete
    out.extend(Member(part, faces) for part, faces in regions.items() if faces)
    return {"members": [m.to_json() for m in out]}


@command(
    "tag.rematch",
    params=(
        TAG,
        ParamDef(
            "min_confidence",
            "number",
            required=False,
            default=0.5,
            description="Faces matched with a lower confidence (0 to 1) count as unmatched.",
        ),
        RESULT,
    ),
)
def selection_rematch(
    ctx: CommandContext,
    tag: str,
    min_confidence: float = 0.5,
    result: list[Any] | None = None,  # Any: JSON members
) -> dict[str, Any]:
    """Carry the tag's faces over to the current version of each part whose B-rep changed,
    by fingerprint; lists every face it could not place (they leave the tag)."""
    from stokes.geometry.fingerprints import match_faces
    from stokes.geometry.selection import Member, ranges_from_ids

    if not 0.0 <= min_confidence <= 1.0:
        raise ValidationError(f"min_confidence must lie in [0, 1]; got {min_confidence}")
    geo = _Geometry(ctx)
    node = _selection(geo.project, tag)
    if result is not None:
        return {**_apply(ctx, geo, node, _given(geo, result), verb="re-match"), "report": []}
    report: list[dict[str, Any]] = []
    kept: list[Member] = []
    for member in _members(node):
        if member.faces is None or geo.node(member.part) is None or geo.mesh(member.part) is None:
            kept.append(member)  # whole, or nothing to match against: stays as it is
            continue
        count = geo.count(member.part)
        now = geo.basis(member.part)
        entry: dict[str, Any] = {
            "part": member.part,
            "path": geo.path(member.part),
            "from": member.basis,
            "to": now,
        }
        if not geo.stale(member):
            beyond = member.beyond(count)
            ids = [int(i) for i in member.ids(None) if count is None or i < count]
            entry.update(
                matched=len(ids),
                unmatched=beyond,
                reason="unchanged" if not beyond else "missing faces",
            )
            if ids:
                kept.append(Member(member.part, ranges_from_ids(ids), now))
        else:
            old = geo.cache.kept(member.part, str(member.basis))
            if old is None:
                entry.update(
                    matched=0,
                    unmatched=member.ids(None).tolist(),
                    reason="no fingerprints of the earlier version",
                )
            else:
                new = geo.cache.fingerprints({member.part: geo.need_mesh(member.part)})[member.part]
                matches = match_faces(old, new, member.ids(None).tolist(), min_confidence)
                placed = [m for m in matches if m.new is not None]
                entry.update(
                    matched=len(placed),
                    unmatched=[m.old for m in matches if m.new is None],
                    faces=[m.to_json() for m in matches],
                    reason="re-matched",
                )
                if placed:
                    # Edge and vertex ids refer to the member's one basis (ADR-0022 §15.1), and the
                    # faces have just moved to `now`; edge/vertex fingerprints are not archived yet
                    # (that is T-056's follow-up), so carrying them across a basis change would
                    # silently point ids at the wrong B-rep. They are dropped and reported instead.
                    dropped_edges = member.edges or ()
                    dropped_vertices = member.vertices or ()
                    if member.basis != now and (dropped_edges or dropped_vertices):
                        from stokes.geometry.selection import ids_of

                        entry["edges_dropped"] = int(ids_of(dropped_edges, None).size)
                        entry["vertices_dropped"] = int(ids_of(dropped_vertices, None).size)
                    kept.append(
                        Member(
                            member.part,
                            ranges_from_ids([m.new for m in placed if m.new is not None]),
                            now,
                            member.edges if member.basis == now else (),
                            member.vertices if member.basis == now else (),
                        )
                    )
        report.append(entry)
    reply = _apply(ctx, geo, node, tuple(kept), verb="re-match", stamp={m.part for m in kept})
    reply["report"] = report
    reply["unmatched"] = sum(len(e["unmatched"]) for e in report)
    return reply


@command(
    "tag.rename",
    params=(TAG, ParamDef("name", "string", description="New name.")),
)
def selection_rename(ctx: CommandContext, tag: str, name: str) -> dict[str, Any]:
    """Rename a tag (its STL solid and, from M3, its patch follow)."""
    geo = _Geometry(ctx)
    node = _selection(geo.project, tag)
    ctx.session.undo.push(RenameNode(node.id, name), geo.project)
    ctx.session.emit("node.changed", node_id=node.id, path=node.path)
    ctx.session.emit("tag.changed", node_id=node.id, path=node.path)
    return _summary(geo, node)


@command(
    "tag.delete",
    params=(
        TAG,
        ParamDef(
            "force",
            "boolean",
            required=False,
            default=False,
            description="Also delete the boundaries and regions that use the tag, in the same "
            "step; without it a tag in use is refused (model.referenced).",
        ),
    ),
)
def selection_delete(ctx: CommandContext, tag: str, force: bool = False) -> dict[str, Any]:
    """Delete a tag (undo brings it back). A tag a boundary or a region uses is refused unless
    force, which deletes them in the same step (a region with its boundaries) and clears the
    partners that named a deleted boundary (ADR-0022 decision 2.8)."""
    geo = _Geometry(ctx)
    node = _selection(geo.project, tag)
    path = node.path
    found = geo.project.referrers(node.id)
    if found:
        return _delete_used(ctx, node, found, force=force)
    ctx.session.undo.push(RemoveNode(node.id), geo.project)
    ctx.session.emit("node.removed", node_id=node.id, path=path)
    ctx.session.emit("tag.changed", node_id=node.id, path=path)
    return {"id": node.id, "path": path, "name": node.name, "deleted": True, "removed": []}


def _delete_used(
    ctx: CommandContext, node: Node, found: Sequence[tuple[Node, str]], *, force: bool
) -> dict[str, Any]:
    """``tag.delete`` of a tag in use: refused without ``force``; with it, one step deletes the
    regions and boundaries that use the tag, then the tag."""
    from stokes.api.commands.setup_plan import Plan, boundary_nodes, clear_references
    from stokes.geometry.model import BOUNDARY_TYPE, REGION_TYPE
    from stokes.model.project import referenced_error

    if not force:
        raise referenced_error(
            node, found, hint="delete them first, or pass force to delete them with the tag"
        )
    users = [n for n, p in found if p == "tag" and n.type in (BOUNDARY_TYPE, REGION_TYPE)]
    others = [(n, p) for n, p in found if not any(n is u for u in users)]
    if others:
        raise referenced_error(
            node, others, hint="force deletes only the boundaries and regions that use the tag"
        )
    path = node.path
    plan = Plan(ctx, f"delete tag {node.name}")
    removed: list[Node] = []
    for region in (n for n in users if n.type == REGION_TYPE):
        plan.remove(region)
        plan.region_changed(region)
        removed.append(region)
        for inner in boundary_nodes(plan.project, region):
            if inner.type == BOUNDARY_TYPE:
                plan.boundary_changed(inner)
    for boundary in (n for n in users if n.type == BOUNDARY_TYPE):
        if not plan.gone(boundary.id):
            plan.remove(boundary)
            plan.boundary_changed(boundary)
            removed.append(boundary)
    plan.remove(node)
    plan.tag_changed(node)
    clear_references(plan, removed)
    plan.push()
    return {
        "id": node.id,
        "path": path,
        "name": node.name,
        "deleted": True,
        "removed": [n.id for n in removed],
    }


def _intersection(a: Sequence[tuple[int, int]], b: Sequence[tuple[int, int]]) -> int:
    """Face ids in both of two sorted, merged lists of inclusive ranges."""
    i = j = total = 0
    while i < len(a) and j < len(b):
        low, high = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if low <= high:
            total += high - low + 1
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _shared(geo: _Geometry, a: Member, b: Member) -> int | None:
    """Faces of one part in both members; None when both hold the whole part and its face count
    is not known."""
    if a.faces is None and b.faces is None:
        return geo.count(a.part)
    if a.faces is None or b.faces is None:
        return (b if a.faces is None else a).size(None)
    return _intersection(a.faces, b.faces)


def tag_overlaps(geo: _Geometry) -> list[dict[str, Any]]:
    """Faces two boundary-kind tags share, per part and pair (T-033's ``tag.overlap``; a face in
    two patches is refused when the case is compiled, decisions log G10): ``[{code, tags: [a,
    b], part, faces, message}]``, ``faces`` None when both hold a whole part of unknown face
    count. Range arithmetic over the stored members, never expanded to face ids."""
    from stokes.geometry.errors import SelectionError

    group = geo.project.find(TAGS_PATH)
    by_part: dict[str, list[tuple[Node, Member]]] = {}
    for tag in group.children if group is not None else []:
        if tag.type != TAG_TYPE or tag.params.get("kind") != SELECTION_KINDS[0]:
            continue
        try:
            members = _members(tag)
        except SelectionError:
            continue
        for member in members:
            by_part.setdefault(member.part, []).append((tag, member))
    found: list[dict[str, Any]] = []
    for part, held in by_part.items():
        for index, (a, first) in enumerate(held):
            for b, second in held[index + 1 :]:
                shared = _shared(geo, first, second)
                if shared == 0:
                    continue
                count = "faces" if shared is None else f"{shared} face{'s' * (shared != 1)}"
                found.append(
                    {
                        "code": "tag.overlap",
                        "tags": [a.id, b.id],
                        "part": part,
                        "faces": shared,
                        "message": f"{a.name} and {b.name} share {count} of {geo.path(part)}",
                    }
                )
    return found


@command("tag.list", mutates=False)
def selection_list(ctx: CommandContext) -> list[dict[str, Any]]:
    """Every tag with its kind, colour, size and staleness, the boundaries and regions that
    use it, and its warnings (tag.overlap: faces it shares with another boundary tag); plain
    groups left in the tags group by earlier versions are listed as ``legacy``."""
    from stokes.api.commands.setup_plan import users_of
    from stokes.geometry.errors import SelectionError
    from stokes.geometry.model import BOUNDARY_TYPE, REGION_TYPE

    geo = _Geometry(ctx)
    group = geo.project.find(TAGS_PATH)
    users = users_of(geo.project)
    overlaps = tag_overlaps(geo)
    items: list[dict[str, Any]] = []
    for child in group.children if group is not None else []:
        if child.type != TAG_TYPE:
            items.append(
                {
                    "id": child.id,
                    "path": child.path,
                    "name": child.name,
                    "type": child.type,
                    "legacy": True,
                }
            )
            continue
        used = users.get(child.id, [])
        uses = {
            "boundaries": [n.id for n in used if n.type == BOUNDARY_TYPE],
            "regions": [n.id for n in used if n.type == REGION_TYPE],
            "warnings": [o for o in overlaps if child.id in o["tags"]],
        }
        try:
            summary = _summary(geo, child)
        except SelectionError as exc:
            items.append(
                {
                    "id": child.id,
                    "path": child.path,
                    "name": child.name,
                    "type": child.type,
                    "error": exc.to_dict(),
                    **uses,
                }
            )
            continue
        summary.pop("members")
        items.append({**summary, **uses})
    return items


def member_ids(member: Any, kind: str) -> list[int]:  # Any: Member; kind "edges" | "vertices"
    """One id set of a member, expanded, for a reply (T-056-fix).

    ``None`` means "every id of that kind"; the count is not known here (an edge count is not a
    parameter the client carries), so an all-edges member reports the empty list rather than a range
    that would claim ids the part may not have. The member's own stored text is the authority for
    the all-vs-none distinction; a client that needs the count asks the part.
    """
    from stokes.geometry.selection import ids_of

    value = getattr(member, kind)
    if value is None:
        return []
    return [int(i) for i in ids_of(value, None)]


@command("tag.members", params=(TAG,), mutates=False)
def selection_members(ctx: CommandContext, tag: str) -> dict[str, Any]:
    """A tag's faces, edges and vertices part by part, expanded to id lists, with what no longer
    exists.

    **Edges and vertices were missing from this reply** (found while fixing T-056): the member
    itself held them, but `tag.members` reported faces only, so a client could neither see them nor
    tell that a tag was edge-only. An edge-only member is now `faces: []` with `edges: [...]`
    rather than an empty-looking member.
    """
    geo = _Geometry(ctx)
    node = _selection(geo.project, tag)
    found = _members(node)
    parts = []
    for member in found:
        part = geo.node(member.part)
        count = geo.count(member.part)
        ids = member.ids(count)
        if count is not None:
            ids = ids[ids < count]
        parts.append(
            {
                "part": member.part,
                "path": part.path if part is not None else None,
                "name": part.name if part is not None else None,
                "exists": part is not None,
                "whole": member.whole,
                "faces": ids.tolist(),
                # T-056-fix: the edge and vertex ids, expanded the same way. `ids_of` with None
                # means "all of them", which needs a count — an edge count is not known here, so an
                # "all edges" member reports the empty list rather than a range that would lie.
                "edges": member_ids(member, "edges"),
                "vertices": member_ids(member, "vertices"),
                "count": int(ids.shape[0]) if count is not None or not member.whole else None,
                "part_faces": count,
                "missing": member.beyond(count),
                "stale": geo.stale(member),
            }
        )
    return {**_summary(geo, node, found), "members": parts}


def _parts_in_scope(geo: _Geometry, part: str | None) -> list[Node]:
    from stokes.geometry.store import parts_under

    if part is not None:
        return geo.part_nodes(part)
    root = geo.project.find(PARTS_PATH) or geo.project.get(GEOMETRY_PATH)
    return list(parts_under(root))


def _matches(parts: Sequence[Node]) -> dict[str, Any]:
    from stokes.geometry.model import ALL_FACES

    return {
        "members": [{"part": p.id, "faces": ALL_FACES} for p in parts],
        "parts": [
            {"id": p.id, "path": p.path, "name": p.name, "color": p.params.get("color")}
            for p in parts
        ],
        "count": len(parts),
        "level": "part",
    }


SCOPE = ParamDef(
    "part",
    "string",
    required=False,
    description="A part or group to search; default: every part.",
)


@command(
    "tag.by_color",
    params=(ParamDef("color", "string", description="#rrggbb."), SCOPE),
    mutates=False,
)
def selection_by_color(ctx: CommandContext, color: str, part: str | None = None) -> dict[str, Any]:
    """Parts of one colour, as members to pass to tag.create, add or set. A part's colour
    is its STEP solid's (unless changed since); face colours are not imported."""
    wanted = _color(color)
    geo = _Geometry(ctx)
    found = [
        p for p in _parts_in_scope(geo, part) if str(p.params.get("color", "")).lower() == wanted
    ]
    return {"color": wanted, **_matches(found)}


@command(
    "tag.by_name",
    params=(
        ParamDef(
            "pattern",
            "string",
            description="Shell-style pattern, case-insensitive (bolt*, *_inlet); with a / it is "
            "matched against the part's path.",
        ),
        SCOPE,
    ),
    mutates=False,
)
def selection_by_name(ctx: CommandContext, pattern: str, part: str | None = None) -> dict[str, Any]:
    """Parts whose name (from the STEP labels) matches, as members to pass to tag.create,
    add or set."""
    if not pattern or len(pattern) > 256:
        raise ValidationError("give a pattern of 1 to 256 characters")
    geo = _Geometry(ctx)
    wanted = pattern.lower()
    found = [
        p
        for p in _parts_in_scope(geo, part)
        if fnmatch.fnmatchcase((p.path if "/" in pattern else p.name).lower(), wanted)
    ]
    return {"pattern": pattern, **_matches(found)}


# ---------------------------------------------------------------- tag.anchor and tag.related

COPLANAR_NORMAL_SPREAD = 0.02
"""How far a member's face normals may differ and still count as one plane (T-046-fix-3).

Above this, the area-weighted centre of a tag's faces is not on the surface — an opposite pair of
box faces gives the centre of the box — so the anchor is instead placed on the member face nearest
that centre. About 1.15 degrees, tighter than `related.COPLANAR_ANGLE` (0.035 rad) because here the
question is only "is this one plane for placement", not "should these faces flood together".
"""


RELATIONS: dict[str, float | None] = {
    "same_area": 0.02,
    "coplanar": 0.001,
    "parallel": 0.035,
    "coaxial": 0.02,
    "tangent": GROW_ANGLE,
    "same_color": None,
}
"""``tag.related``: the relations and their default tolerances, relative: radians for the angle
relations, a fraction of the part's bounding-box diagonal for coplanar (its angle stays at
0.035 radians), degrees for the feature angle of tangent; same_color takes none."""


def _anchor_on_surface(
    centroid: Any,  # Any: np.ndarray (numpy is imported lazily, as everywhere in this module)
    normal: Any,  # Any: np.ndarray
    member_faces: list[tuple[Any, Any]],  # Any: (centroid, normal) numpy arrays
) -> tuple[Any, Any]:  # Any: (np.ndarray, np.ndarray)
    """The glyph's placement, moved onto a face when the members are not one plane (T-046-fix-3).

    §8.1 places the glyph "at the centroid, along the normal", area-weighted over the member
    faces. That is right for the common case — the faces of one inlet plane — but an area-weighted
    centre is on the surface **only when those faces are coplanar**. A tag holding two opposite
    faces of a box (what picking two patches gives) has its weighted centre in the middle of the
    solid, so the marker would be drawn inside the part and never seen.

    When the member normals disagree by more than `COPLANAR_NORMAL_SPREAD`, the anchor is placed on
    the member face **nearest that centre**, with that face's own centroid and normal — a point that
    is genuinely on the part, and the one closest to where the user's selection is centred.
    """
    import numpy as np

    if len(member_faces) < 2:
        return centroid, normal
    normals = np.array([face[1] for face in member_faces])
    spread = float(np.linalg.norm(normals - normals[0], axis=1).max())
    if spread <= COPLANAR_NORMAL_SPREAD:
        return centroid, normal
    centres = np.array([face[0] for face in member_faces])
    nearest = int(np.argmin(np.linalg.norm(centres - centroid, axis=1)))
    return centres[nearest], normals[nearest]


def _member_triangles(mesh: Any, ids: Any, whole: bool) -> Any:  # Any: PartMesh, np arrays
    """A member's triangles, shape (n, 3, 3) in metres; a solid without face ids gives none."""
    import numpy as np

    found = []
    for solid in mesh.solids:
        if solid.face_ids is None or solid.indices.size == 0:
            continue
        indices = solid.indices if whole else solid.indices[np.isin(solid.face_ids, ids)]
        if indices.size:
            found.append(np.asarray(solid.positions, dtype=np.float64)[indices])
    return np.concatenate(found) if found else np.zeros((0, 3, 3))


def _point_on_faces(point: Any, triangles: Any) -> Any:  # Any: np.ndarray
    """``point`` when it lies on one of ``triangles``, else the centre of the nearest triangle.

    **Why a glyph needs it**: the area-weighted centre of one flat face is on the face only when
    the face is convex. The mixing elbow's symmetry plane is an L, and its marker floated in the
    empty corner of the L, beside the part rather than on it (2026-11-08). The centroid stays the
    tag's `centroid` (the run snapshot reads it); this point is only where the marker is drawn.
    """
    import numpy as np

    if len(triangles) == 0:
        return point
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    ab, ac = b - a, c - a
    normal = np.cross(ab, ac)
    twice_area = np.linalg.norm(normal, axis=1)
    usable = twice_area > 1e-18
    unit = np.zeros_like(normal)
    unit[usable] = normal[usable] / twice_area[usable, None]
    offset = point - a
    height = np.einsum("ij,ij->i", offset, unit)
    flat = offset - height[:, None] * unit
    # Barycentric coordinates of the point's projection onto each triangle's plane.
    d00 = np.einsum("ij,ij->i", ab, ab)
    d01 = np.einsum("ij,ij->i", ab, ac)
    d11 = np.einsum("ij,ij->i", ac, ac)
    d20 = np.einsum("ij,ij->i", flat, ab)
    d21 = np.einsum("ij,ij->i", flat, ac)
    denominator = d00 * d11 - d01 * d01
    safe = np.where(np.abs(denominator) > 1e-30, denominator, 1.0)
    v = (d11 * d20 - d01 * d21) / safe
    w = (d00 * d21 - d01 * d20) / safe
    extent = float(np.linalg.norm(np.ptp(triangles.reshape(-1, 3), axis=0)))
    inside = usable & (v >= -1e-9) & (w >= -1e-9) & (v + w <= 1 + 1e-9)
    if np.any(inside & (np.abs(height) <= 1e-6 * max(extent, 1e-12))):
        return point
    centres = triangles.mean(axis=1)
    return centres[int(np.argmin(np.linalg.norm(centres - point, axis=1)))]


@command(
    "tag.anchor",
    params=(
        ParamDef(
            "tags",
            "array",
            required=False,
            description="Tag ids or paths; default: every tag.",
        ),
    ),
    mutates=False,
)
def selection_anchor(ctx: CommandContext, tags: list[Any] | None = None) -> dict[str, Any]:
    """One anchor per tag, in metres, for one glyph per boundary (ADR-0022 decision 8.1): the
    area-weighted centroid and mean normal of the tag's faces, the summed area, and the
    diagonal of the bounding box of the faces' centroids. Read-only, not journaled; a member
    part without a mesh yet is skipped and the anchor is ``partial``."""
    import numpy as np

    from stokes.geometry.topology import part_face_count

    geo = _Geometry(ctx)
    project = geo.project
    if tags:
        nodes = [_selection(project, ref) for ref in _refs(tags)]
    else:
        group = project.find(TAGS_PATH)
        nodes = [c for c in (group.children if group is not None else []) if c.type == TAG_TYPE]
    anchors: list[dict[str, Any]] = []
    for node in nodes:
        area = 0.0
        centroid = np.zeros(3)
        normal = np.zeros(3)
        low = np.full(3, np.inf)
        high = np.full(3, -np.inf)
        faces = 0
        parts = 0
        partial = False
        # (centroid, normal) of every contributing face, for the non-coplanar placement below.
        member_faces: list[tuple[np.ndarray, np.ndarray]] = []
        triangles: list[np.ndarray] = []
        for member in _members(node):
            mesh = geo.mesh(member.part)
            if mesh is None:
                partial = True
                continue
            fp = geo.cache.fingerprints({member.part: mesh})[member.part]
            count = part_face_count(mesh)
            ids = np.arange(count, dtype=np.int64) if member.whole else member.ids(None)
            ids = ids[(ids >= 0) & (ids < count)]
            if ids.size == 0:
                continue
            faces += int(ids.size)
            parts += 1
            a = fp.area[ids]
            area += float(a.sum())
            centroid += (a[:, None] * fp.centroid[ids]).sum(axis=0)
            normal += (a[:, None] * fp.normal[ids]).sum(axis=0)
            member_faces.extend(zip(fp.centroid[ids], fp.normal[ids], strict=True))
            triangles.append(_member_triangles(mesh, ids, member.whole))
            low = np.minimum(low, fp.centroid[ids].min(axis=0))
            high = np.maximum(high, fp.centroid[ids].max(axis=0))
        extent = 0.0
        if faces:
            extent = float(np.linalg.norm(high - low))
            if area > 0.0:
                centroid = centroid / area
        n_len = float(np.linalg.norm(normal))
        normal = normal / n_len if n_len > 1e-6 else np.array([0.0, 0.0, 1.0])

        # T-046-fix-3: the area-weighted centre is on the surface only when the member faces are
        # coplanar; see `_anchor_on_surface`.
        centroid, normal = _anchor_on_surface(centroid, normal, member_faces)
        anchors.append(
            {
                "tag": node.path,
                "centroid": [float(v) for v in centroid],
                # Where the glyph is drawn: on the tag's own faces (`_point_on_faces`).
                "position": [
                    float(v)
                    for v in _point_on_faces(
                        centroid, np.concatenate(triangles or [np.zeros((0, 3, 3))])
                    )
                ],
                "normal": [float(v) for v in normal],
                "area": area,
                "extent": extent,
                "faces": faces,
                "parts": parts,
                "partial": partial,
            }
        )
    return {"anchors": anchors}


@command(
    "tag.related",
    params=(
        ParamDef("members", "array", description=MEMBERS_HELP),
        ParamDef(
            "relation",
            "string",
            description="same_area, coplanar, parallel, coaxial, tangent or same_color.",
        ),
        ParamDef(
            "parts",
            "array",
            required=False,
            description="Parts or groups to search; default: every part with a mesh.",
        ),
        ParamDef(
            "tolerance",
            "number",
            required=False,
            description="Tolerance of the relation: relative, radians for the angle relations, "
            "a fraction of the part's bounding-box diagonal for coplanar, degrees for the "
            "feature angle of tangent; the default depends on the relation.",
        ),
    ),
    mutates=False,
)
def selection_related(
    ctx: CommandContext,
    members: list[Any],
    relation: str,
    parts: list[Any] | None = None,
    tolerance: float | None = None,
) -> dict[str, Any]:
    """Faces like the picked ones (ADR-0022 decision 6.4), over the fingerprints and facets
    the session holds: read-only, not journaled, and the picked faces are never in the
    result."""
    if relation not in RELATIONS:
        raise SelectionError(
            f"relation must be one of {', '.join(RELATIONS)}; got {relation!r}",
            details={"relation": relation},
        )
    tol = RELATIONS[relation]
    if tolerance is not None:
        if tol is None:
            raise ValidationError("same_color takes no tolerance", details={"relation": relation})
        tol = float(tolerance)
        if relation == "tangent":
            if not 0.0 < tol <= 180.0:
                raise SelectionError(f"the feature angle must lie in (0, 180] degrees; got {tol}")
        elif tol <= 0.0:
            raise ValidationError(f"the {relation} tolerance must be positive; got {tol}")
    geo = _Geometry(ctx)
    start = _given(geo, members)
    if not start:
        raise SelectionError("nothing to relate: give faces", details={"relation": relation})
    scope = _related_nodes(geo, _part_refs(geo, parts) if parts else [])
    ready = [n for n in scope if geo.mesh(n.id) is not None]
    if tol is None:  # only same_color: it takes none
        found, count = _related_color(geo, start, ready)
    elif relation == "tangent":
        found, count = _related_tangent(geo, start, tol)
    else:
        found, count = _related_fingerprint(geo, start, ready, relation, tol)
    return {
        "members": [m.to_json() for m in found],
        "count": count,
        "relation": relation,
        "tolerance": tol,
    }


def _related_nodes(geo: _Geometry, refs: list[str]) -> list[Node]:
    """The parts ``tag.related`` searches: the given parts or groups, else every part under
    /geometry/parts, each once."""
    if refs:
        found: dict[str, Node] = {}
        for part in refs:
            node = geo.project.get(part)
            if node is not None:
                found.setdefault(part, node)
        return [found[p] for p in found]
    return _parts_in_scope(geo, None)


def _related_tangent(
    geo: _Geometry, start: tuple[Member, ...], angle: float
) -> tuple[list[Member], int]:
    """The ``tag.region`` flood (``stokes.geometry.selection.grow_parts``) from the picked
    faces, minus the picked faces; whole-part picks leave nothing."""
    from stokes.geometry.selection import Member, grow_parts, subtract
    from stokes.geometry.topology import part_face_count

    explicit = [m for m in start if m.faces is not None]
    out: list[Member] = []
    total = 0
    if explicit:
        meshes = {m.part: geo.need_mesh(m.part) for m in explicit}
        regions = grow_parts(
            geo.cache.adjacency(meshes),
            {p: part_face_count(mesh) for p, mesh in meshes.items()},
            {m.part: m.ids(None) for m in explicit},
            angle,
        )
        for member in explicit:
            faces = member.faces  # not None: explicit holds the members with face ids
            if faces is None:
                continue
            remaining = subtract(regions.get(member.part, ()), faces)
            if remaining:
                out.append(Member(member.part, remaining))
                total += sum(last - first + 1 for first, last in remaining)
    return out, total


def _related_color(
    geo: _Geometry, start: tuple[Member, ...], ready: list[Node]
) -> tuple[list[Member], int]:
    """Whole parts of the picked faces' parts' colours (the ``tag.by_color`` path), minus the
    picked faces."""
    from stokes.geometry.selection import Member, complement
    from stokes.geometry.topology import part_face_count

    colors: set[str] = set()
    for part in {m.part for m in start}:
        node = geo.node(part)
        color = node.params.get("color") if node is not None else None
        if color:
            colors.add(str(color).lower())
    if not colors:
        raise SelectionError(
            "the picked faces' parts have no colour to match", details={"relation": "same_color"}
        )
    picked = {m.part: m for m in start}
    out: list[Member] = []
    total = 0
    for node in ready:
        color = node.params.get("color")
        if not color or str(color).lower() not in colors:
            continue
        count = part_face_count(geo.need_mesh(node.id))
        member = picked.get(node.id)
        if member is None:
            out.append(Member(node.id, None))
            total += count
        elif member.faces is None:  # a whole member: nothing is left of it
            continue
        else:
            remaining = complement(member.faces, count)
            if remaining:
                out.append(Member(node.id, remaining))
                total += sum(last - first + 1 for first, last in remaining)
    return out, total


def _picked_fingerprints(
    geo: _Geometry, start: tuple[Member, ...], meshes: dict[str, PartMesh]
) -> tuple[dict[str, I64], F64, F64, F64]:
    """The picked faces' ids and fingerprint columns, per part (a whole member takes every
    face; an id the part no longer has is ignored, as in ``tag.region``)."""
    import numpy as np

    from stokes.geometry.topology import part_face_count

    picked: dict[str, I64] = {}
    areas: list[F64] = []
    normals: list[F64] = []
    centroids: list[F64] = []
    for member in start:
        mesh = meshes[member.part]
        count = part_face_count(mesh)
        ids = np.arange(count, dtype=np.int64) if member.whole else member.ids(None)
        ids = ids[(ids >= 0) & (ids < count)]
        picked[member.part] = ids
        fp = geo.cache.fingerprints({member.part: mesh})[member.part]
        areas.append(fp.area[ids])
        normals.append(fp.normal[ids])
        centroids.append(fp.centroid[ids])
    picked_areas = np.concatenate(areas) if areas else np.zeros(0, dtype=np.float64)
    picked_normals = np.concatenate(normals) if normals else np.zeros((0, 3), dtype=np.float64)
    picked_centroids = (
        np.concatenate(centroids) if centroids else np.zeros((0, 3), dtype=np.float64)
    )
    if picked_areas.size == 0:
        raise SelectionError("nothing to relate: the picked faces are gone")
    return picked, picked_areas, picked_normals, picked_centroids


def _coaxial_fit(
    geo: _Geometry,
    start: tuple[Member, ...],
    meshes: dict[str, PartMesh],
    picked: dict[str, I64],
    picked_areas: F64,
    picked_centroids: F64,
) -> tuple[F64, F64, float] | None:
    """The axis, a point on it and the picked radius, from the picked faces' facets:
    ``tag.invalid`` when they do not wrap a cylinder, and None when they share one direction,
    so no axis line exists and the pick is coaxial with nothing."""
    import numpy as np

    from stokes.geometry import related
    from stokes.geometry.topology import facets_of, surface_of

    order = [m.part for m in start]
    surface = surface_of([meshes[p] for p in order])
    facets = facets_of(surface)
    sel = np.zeros(surface.triangle_count, dtype=bool)
    for index, part in enumerate(order):
        sel |= np.isin(surface.face, int(surface.face_offset[index]) + picked[part])
    sel &= surface.face >= 0
    axis, ratio = related.axis_of(facets.normal[sel], facets.area[sel])
    if ratio > related.AXIS_RATIO:
        raise SelectionError(
            "the picked faces are not cylindrical", details={"relation": "coaxial"}
        )
    if not related.wraps_axis(facets.normal[sel], facets.area[sel]):
        return None
    origin = related.axis_point(facets.normal[sel], facets.centroid[sel], facets.area[sel], axis)
    radius = related.picked_radius(picked_centroids, picked_areas, origin, axis)
    return axis, origin, radius


def _related_mask(
    geo: _Geometry,
    node: Node,
    mesh: PartMesh,
    relation: str,
    picked_areas: F64,
    picked_normals: F64,
    picked_centroids: F64,
    axis: F64 | None,
    origin: F64 | None,
    radius: float,
    tol: float,
) -> Mask:
    """The candidate mask of one part's faces for a relation (the picked faces are not yet
    dropped)."""
    from stokes.geometry import related
    from stokes.geometry.topology import facets_of, part_face_count, surface_of

    fp = geo.cache.fingerprints({node.id: mesh})[node.id]
    count = part_face_count(mesh)
    if relation == "coaxial":
        assert axis is not None and origin is not None
        s = surface_of([mesh])
        f = facets_of(s)
        return related.coaxial(
            f.normal, f.area, s.face, fp.centroid, count, axis, origin, radius, tol
        )
    if relation == "same_area":
        return related.same_area(fp.area, picked_areas, tol)
    if relation == "parallel":
        return related.parallel(fp.normal, picked_normals, tol)
    return related.coplanar(fp.normal, fp.centroid, picked_normals, picked_centroids, fp.scale, tol)


def _related_fingerprint(
    geo: _Geometry,
    start: tuple[Member, ...],
    ready: list[Node],
    relation: str,
    tol: float,
) -> tuple[list[Member], int]:
    """same_area, parallel, coplanar and coaxial, over the fingerprints the session keeps
    (coaxial also over the facets that ``fingerprints_of`` reads)."""
    import numpy as np

    from stokes.geometry.selection import Member, ranges_from_mask

    meshes: dict[str, PartMesh] = {m.part: geo.need_mesh(m.part) for m in start}
    picked, picked_areas, picked_normals, picked_centroids = _picked_fingerprints(
        geo, start, meshes
    )
    axis: F64 | None = None
    origin: F64 | None = None
    radius = 0.0
    if relation == "coaxial":
        fit = _coaxial_fit(geo, start, meshes, picked, picked_areas, picked_centroids)
        if fit is None:
            return [], 0  # the pick wraps no axis: coaxial with nothing
        axis, origin, radius = fit
    out: list[Member] = []
    total = 0
    for node in ready:
        mesh = geo.mesh(node.id)
        assert mesh is not None
        mask = _related_mask(
            geo,
            node,
            mesh,
            relation,
            picked_areas,
            picked_normals,
            picked_centroids,
            axis,
            origin,
            radius,
            tol,
        )
        ids = picked.get(node.id, np.zeros(0, dtype=np.int64))
        if ids.size:
            mask = mask & ~np.isin(np.arange(mask.shape[0], dtype=np.int64), ids)
        if mask.any():
            # a part that matches as a whole becomes a whole member, as by_color writes it
            out.append(Member(node.id, None if mask.all() else ranges_from_mask(mask)))
            total += int(mask.sum())
    return out, total


# ---------------------------------------------------------------- geometry tools


_NO_BREP: dict[str, Any] = {
    "checked": False,
    "reason": "the B-rep was not read (no source file, or the worker could not read it)",
}
_MESH_ONLY_BREP: dict[str, Any] = {
    "checked": False,
    "reason": "mesh-only part",
}
"""A mesh-only part has no B-rep to check (ADR-0021 decision 1.3)."""
_CHECK_RUNNING: dict[str, Any] = {
    "checked": False,
    "reason": "running",
}
"""The B-rep job covers this part and has not reported yet; follow geometry.check_progress."""


def _check_of(node: Node) -> dict[str, Any]:  # Any: JSON
    """A copy of the part's ``meta.check``, so that writing it replaces the whole sub-dict and the
    B-rep findings an earlier job stored under it are kept."""
    stored = node.meta.get("check")
    return dict(stored) if isinstance(stored, dict) else {}


def _checkable(geo: _Geometry, node: Node) -> bool:
    """Whether a check job has a B-rep to load: a derived part (its shape cache names the
    operation), a readable source file, or a fresh shape-cache entry."""
    from pathlib import Path

    from stokes.geometry.shapes import brep_path, fresh

    if node.params.get("derived"):
        return True
    source = str(node.params.get("source") or "")
    if source and Path(source).is_file():
        return True
    return fresh(brep_path(geo.store.shape_dir(), node.id), geo.store.basis(node))


def _stored_brep(geo: _Geometry, node: Node) -> dict[str, Any] | None:
    """The part's stored ``meta.check.brep`` when it is for the version the part is now, else
    None: a part re-imported since reads the file again."""
    check = node.meta.get("check")
    brep = check.get("brep") if isinstance(check, dict) else None
    if not isinstance(brep, dict) or brep.get("basis") != geo.store.basis(node):
        return None
    return brep


def _brep_now(
    geo: _Geometry,
    node: Node,
    *,
    started: set[str],
    wait: bool,
    stored: dict[str, Any] | None,
) -> dict[str, Any]:  # Any: JSON
    """What to report for a part's B-rep now: nothing to check, the job is running, or the
    findings of the version the part is at (``stored``)."""
    if node.params.get("family") == "mesh":
        return _MESH_ONLY_BREP
    if not _checkable(geo, node):
        return _NO_BREP
    if (node.id in started and not wait) or stored is None:
        return _CHECK_RUNNING
    if stored.get("checked") is True:
        return {"checked": True, **stored}  # the findings, the basis they belong to and when
    return {"checked": False, "reason": str(stored.get("reason") or _NO_BREP["reason"])}


@command(
    "geometry.check",
    params=(
        ParamDef("part", "string", description="Part or group: node id or path."),
        ParamDef(
            "wait",
            "boolean",
            required=False,
            default=False,
            description="Return only when the B-rep job has ended (scripts and tests); "
            "interactive clients follow the geometry.check_progress events instead.",
        ),
        ParamDef(
            "force",
            "boolean",
            required=False,
            default=False,
            description="Check the B-rep again even when it is already checked at the part's "
            "current version.",
        ),
    ),
    mutates=False,
)
def geometry_check(
    ctx: CommandContext, part: str, wait: bool = False, force: bool = False
) -> dict[str, Any]:
    """Check parts' tessellations for what a mesher needs (closed, manifold, oriented, no
    degenerate triangles, plausible size) and check their B-reps off the calling thread; the
    summary goes to each part's meta.check (ADR-0021 decision 5).

    The tessellation findings come back at once. The B-rep pass is one background job per batch of
    parts, whose findings carry the face ids a UI can highlight; the immediate result carries
    ``brep: {checked: false, reason: "running"}`` for those parts, unless ``wait`` is true, which
    returns only when the job has ended. A part whose B-rep was already checked at its current
    version is not checked again unless ``force``.
    """
    from stokes.geometry.checks import check_meshes
    from stokes.geometry.checkstore import CheckStore
    from stokes.geometry.shapes import part_spec

    geo = _Geometry(ctx)
    target = geo.project.get(part)
    nodes = geo.part_nodes(target.id)
    if wait and geo.store.blocks_session():
        raise ValidationError(
            "wait=true would block the server; follow the geometry.check_progress events instead"
        )
    ready = [n for n in nodes if geo.mesh(n.id) is not None]
    results = dict(
        zip([n.id for n in ready], check_meshes([geo.need_mesh(n.id) for n in ready]), strict=True)
    )
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    checks = CheckStore.of(ctx.session)
    cache = geo.store.shape_dir()
    specs = []
    bases: dict[str, str] = {}
    for node in ready:
        if node.params.get("family") == "mesh" or not _checkable(geo, node):
            continue
        if not force and _stored_brep(geo, node) is not None:
            continue  # already checked at the version the part is now
        basis = geo.store.basis(node)
        specs.append(part_spec(node, cache, basis))
        bases[node.id] = basis
    if specs:
        checks.start(specs, bases, project=geo.project)
    started = {part for part in bases if checks.running(part)}
    if wait:
        checks.wait()
    items = []
    for node in nodes:
        found = results.get(node.id)
        if found is None:
            items.append(
                {
                    "part": node.id,
                    "path": node.path,
                    "name": node.name,
                    "checked": False,
                    "reason": "no mesh yet",
                }
            )
            continue
        stored = _stored_brep(geo, node)
        check = _check_of(node)
        check.update(found.summary())
        check["at"] = stamp
        node.meta = {**node.meta, "check": check}
        brep = _brep_now(geo, node, started=started, wait=wait, stored=stored)
        # The result shows on the part's status dot (ADR-0020 decision 6): a part with findings is a
        # warning, and a clean one goes back to ok. Set through the model, as any other write.
        if node.status in ("ok", "warning"):  # never hide a failed, stale or new part
            warns = found.worst in ("error", "warning") or (
                brep.get("checked") is True and not brep.get("ok", True)
            )
            geo.project.set_status(node.id, "warning" if warns else "ok")
        ctx.session.emit("node.changed", node_id=node.id, path=node.path)
        items.append(
            {
                "part": node.id,
                "path": node.path,
                "name": node.name,
                "checked": True,
                **found.to_json(),
                "brep": brep,
            }
        )
    if target.type == "part":
        return items[0]
    severities = [i["worst"] for i in items if i["checked"]]
    worst = next((s for s in ("error", "warning", "info") if s in severities), "ok")
    return {
        "node": target.id,
        "path": target.path,
        "parts": items,
        "checked": len(ready),
        "unchecked": len(nodes) - len(ready),
        "worst": worst,
        "with_findings": sum(1 for s in severities if s != "ok"),
    }


@command(
    "geometry.cancel_check",
    params=(
        ParamDef(
            "part",
            "string",
            required=False,
            description="Part or group whose B-rep check to stop; default: every check.",
        ),
    ),
    mutates=False,
)
def geometry_cancel_check(ctx: CommandContext, part: str | None = None) -> dict[str, Any]:
    """Stop running B-rep checks; a part keeps the findings it already has."""
    from stokes.geometry.checkstore import CheckStore

    checks = CheckStore.of(ctx.session)
    if part is None:
        return {"cancelled": checks.cancel()}
    target = _Geometry(ctx).project.get(part)
    return {"cancelled": checks.cancel_many([n.id for n in target.walk()])}


EXPORT_FOLDER = ParamDef(
    "folder",
    "string",
    required=False,
    description="The export folder the user chose; path must lie inside it. Default: the project "
    "folder.",
)


@command(
    "geometry.export_stl",
    params=(
        ParamDef(
            "path",
            "string",
            description="A .stl file, or a folder for one file per solid; relative to the export "
            "folder.",
        ),
        ParamDef("tags", "array", required=False, description="Tags: one solid each."),
        ParamDef(
            "parts", "array", required=False, description="Parts or groups: one solid per part."
        ),
        ParamDef(
            "binary",
            "boolean",
            required=False,
            default=True,
            description="Binary STL (one solid per file) or ASCII (named solids).",
        ),
        EXPORT_FOLDER,
    ),
    mutates=False,
)
def geometry_export_stl(
    ctx: CommandContext,
    path: str,
    tags: list[Any] | None = None,  # Any: JSON references
    parts: list[Any] | None = None,  # Any: JSON references
    binary: bool = True,
    folder: str | None = None,
) -> dict[str, Any]:
    """Write one STL solid per tag or part, in metres, named for OpenFOAM."""
    from stokes.geometry.export import contained, export_root, write_stl

    geo = _Geometry(ctx)
    if not tags and not parts:
        raise ValidationError("give tags, parts, or both")
    root = export_root(ctx.session.project_path, folder)
    target = contained(path, root)
    taken: set[str] = set()
    solids = [_selection_solid(geo, ref, taken) for ref in _refs(tags)]
    for part_id in _part_refs(geo, parts or []):
        solids.append(_part_solid(geo, part_id, taken))
    written = write_stl(target, root, solids, binary=binary)
    return {
        "files": [w.to_json() for w in written],
        "solids": len(solids),
        "triangles": sum(s.triangles for s in solids),
        "binary": binary,
        "units": "m",
    }


def _refs(raw: Any) -> list[str]:  # Any: JSON array
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(r, str) and r for r in raw):
        raise ValidationError("tags must be a list of tag ids, paths or names")
    return list(dict.fromkeys(raw))


def _selection_solid(geo: _Geometry, ref: str, taken: set[str]) -> Solid:
    import numpy as np

    from stokes.geometry.errors import SelectionError
    from stokes.geometry.export import Solid, corners_of, solid_name

    node = _selection(geo.project, ref)
    members = _members(node)
    _require_current(geo, node, members, {m.part for m in members})
    chunks = []
    faces = 0
    for member in members:
        mesh = geo.need_mesh(member.part)
        count = geo.count(member.part)
        missing = member.beyond(count)
        if missing:
            raise SelectionError(
                f"{node.path} lists faces {missing[:10]} that {geo.path(member.part)} no longer "
                "has; run tag.rematch or set the tag again",
                details={"tag": node.path, "part": member.part},
            )
        ids = None if member.whole else member.ids(None)
        chunks.append(corners_of(mesh, ids))
        faces += member.size(count) or 0
    if not chunks or not sum(c.shape[0] for c in chunks):
        raise SelectionError(f"{node.path} has no faces to export", details={"tag": node.path})
    return Solid(solid_name(node.name, taken), np.concatenate(chunks), faces)


def _part_solid(geo: _Geometry, part: str, taken: set[str]) -> Solid:
    from stokes.geometry.export import Solid, corners_of, solid_name

    node = geo.node(part)
    name = node.name if node is not None else part
    return Solid(
        solid_name(name, taken), corners_of(geo.need_mesh(part), None), geo.count(part) or 0
    )


@command(
    "geometry.export_step",
    params=(
        ParamDef("parts", "array", description="Parts or groups to write."),
        ParamDef(
            "path", "string", description="A .step or .stp file, relative to the export folder."
        ),
        EXPORT_FOLDER,
    ),
    mutates=False,
)
def geometry_export_step(
    ctx: CommandContext,
    parts: list[Any],  # Any: JSON references
    path: str,
    folder: str | None = None,
) -> dict[str, Any]:
    """Write the chosen parts, named, as STEP in metres (ADR-0020 decision 7; ADR-0021
    decision 8.3).

    Every part is loaded through the shape cache's provider, so one call writes parts and groups
    from any number of source files together with the derived parts of operations, each named as
    its node names it. A mesh-only part has no B-rep to write: it refuses with
    ``geometry.mesh_only``. A part with neither a cached B-rep nor a readable source file refuses
    with ``geometry.unreadable``, naming the part."""
    from pathlib import Path

    from stokes.geometry.errors import GeometryFileError, MeshOnly
    from stokes.geometry.export import contained, export_root
    from stokes.geometry.shapes import brep_path, fresh, part_spec
    from stokes.geometry.worker import acquire

    geo = _Geometry(ctx)
    chosen = _part_refs(geo, parts)
    if not chosen:
        raise ValidationError("give parts to export")
    target = contained(path, export_root(ctx.session.project_path, folder))
    if target.suffix.lower() not in (".step", ".stp"):
        raise ValidationError(
            f"{target.name} is not a .step or .stp file", details={"path": str(target)}
        )
    nodes = [geo.project.get(part_id) for part_id in chosen]
    mesh_only = [n for n in nodes if n.params.get("family") == "mesh"]
    if mesh_only:
        raise MeshOnly(
            f"{mesh_only[0].path} is a mesh-only part: it has no B-rep. Export it to STL for the "
            "mesher, or import a STEP version.",
            details={"part": mesh_only[0].id, "parts": [n.id for n in mesh_only]},
        )
    cache = geo.store.shape_dir()
    for node in nodes:
        if node.params.get("derived"):
            # A derived part: the provider names the operation to run again when its B-rep is gone.
            continue
        source = str(node.params.get("source") or "")
        if (source and Path(source).is_file()) or fresh(
            brep_path(cache, node.id), geo.store.basis(node)
        ):
            continue
        raise GeometryFileError(
            f"{node.path} has no B-rep to export: it has no readable source file and no cached "
            "B-rep",
            details={"part": node.id},
        )
    specs = [part_spec(n, cache, geo.store.basis(n)) for n in nodes]
    payload = acquire().export_step(specs, target, names=[n.name for n in nodes])
    return {
        "file": str(target),
        "solids": int(payload.get("solids", 0)),
        "names": [str(name) for name in payload.get("names", [])],
        "units": "m",
    }
