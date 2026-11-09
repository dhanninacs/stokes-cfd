"""Tag members (ADR-0020 decisions 1 to 3; ADR-0022 §15.1): the stored JSON, member arithmetic and
feature-angle growth.

A tag's members are a tuple of :class:`Member`, one per part, in the order the parts were
first added. The stored form (the ``members`` parameter of a ``tag``) is
:func:`dump_members`: compact JSON with sorted, merged, inclusive id ranges, so equal
tags have equal text and a replayed journal stores the same bytes. ``faces`` None
(``"all"`` in JSON) means every face of the part whatever its count, so a tag of whole parts
needs no mesh and survives re-imports untouched. ``basis`` names the B-rep version explicit ids
refer to (:mod:`stokes.geometry.fingerprints`); the commands stamp it, the arithmetic carries it.

**Edges and vertices (ADR-0022 §15.1).** A member may also carry ``edges`` and ``vertices``, B-rep
topological ids numbered in ``TopExp.MapShapes`` order (:mod:`stokes.geometry.topology_brep`). Both
are optional and default to none, so text written before they existed parses to exactly the same
:class:`Member` it did then, and a face-only reader is unaffected. The three id sets are independent
and share the member's one ``basis``: faces, edges and vertices of a part are numbered against the
same B-rep version, so a second basis could only ever disagree with itself. A member that names
*only* edges or vertices is not a whole-part member: the legacy rule is that a member with no
``faces`` key means "all faces", so a member with no ``faces`` key but with edge ids is read as
having no faces, and :func:`Member.empty` tells the two apart.

Arithmetic (:func:`union`, :func:`difference`, :func:`invert`) works on ranges in plain Python:
tags hold few ranges, and only a complement needs the set's count. Faces complement as before; an
edge or vertex set is never complemented behind the caller's back, because its count comes from a
topology cache that may be absent and a silent empty result would delete a tag's contents.

Growth (:func:`grow_faces`) floods the face adjacency of :mod:`stokes.geometry.topology`: from the
seed faces it crosses every face boundary whose two sides meet at a dihedral angle below the
threshold, the angle between the facet normals on either side of the shared triangle edges
(their cosines averaged, weighted by edge length). A face's own mean normal would be no guide:
it vanishes on a closed face such as a whole cylinder, and on half a cylinder it points across
the axis, so the two halves of one cylinder would seem to meet at 180 degrees.

Import rule: numpy only; no OCP.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.geometry.errors import SelectionError
from stokes.geometry.model import ALL_FACES
from stokes.geometry.topology import Adjacency
from stokes.model.ids import is_id

I64 = NDArray[np.int64]
Mask = NDArray[np.bool_]
Ranges = tuple[tuple[int, int], ...]
CountOf = Callable[[str], int | None]
"""A part id's face count, or None while it is not known (the part has not been read yet)."""

MAX_MEMBERS = 100_000
"""Parts in one tag."""
MAX_RANGES = 1_000_000
"""Ranges, or single ids, given for one part."""
MAX_FACE_ID = 2**31 - 1
MAX_TEXT = 16 * 1024 * 1024
"""Bytes of stored members text read back."""
_BASIS_RE = re.compile(r"^[0-9a-f]{1,32}$")
_MEMBER_KEYS = frozenset({"part", "faces", "edges", "vertices", "basis"})
_ID_SETS = ("faces", "edges", "vertices")


@dataclass(frozen=True)
class Member:
    """One part's faces, edges and vertices in a tag.

    Each of the three id sets is ``None`` for *every* id of that kind, ``()`` for none, or a tuple
    of inclusive ranges. ``faces`` keeps its historical meaning exactly (``None`` = the whole part),
    so every face-only reader is unaffected by the addition of edge and vertex members.

    ``basis`` names the single B-rep version all three sets refer to (ADR-0022 §15.1): faces, edges
    and vertices of one part are numbered against the same version, so one basis per member is both
    sufficient and the only thing that cannot disagree with itself.
    """

    part: str
    faces: Ranges | None
    """inclusive (first, last) ranges, sorted and merged; None for every face of the part"""
    basis: str | None = None
    edges: Ranges | None = ()
    """B-rep edge ids: None for every edge, () for none (ADR-0022 §15.1)"""
    vertices: Ranges | None = ()
    """B-rep vertex ids: None for every vertex, () for none"""

    @property
    def whole(self) -> bool:
        """True when the member names every face of its part."""
        return self.faces is None

    @property
    def explicit(self) -> bool:
        """True when the member names at least one id of any kind.

        This is what decides whether a ``basis`` is meaningful (ADR-0022 §15.1): a basis names the
        B-rep version that explicit ids refer to, so a member that is *entirely* whole ("all faces",
        no edges, no vertices) has none, while "all faces plus these edges" has one.
        """
        return not (self.faces is None and self.edges == () and self.vertices == ())

    @property
    def empty(self) -> bool:
        """True when the member names nothing at all (no faces, no edges, no vertices)."""
        return self.faces == () and self.edges == () and self.vertices == ()

    def size(self, count: int | None) -> int | None:
        """How many faces; None for a whole part whose face count is unknown."""
        if self.faces is None:
            return count
        return sum(last - first + 1 for first, last in self.faces)

    def ids(self, count: int | None) -> I64:
        """The face ids, ascending (a whole part needs its count)."""
        return ids_of(self.faces, count)

    def edge_ids(self, count: int | None) -> I64:
        """The B-rep edge ids, ascending (all edges need their count)."""
        return ids_of(self.edges, count)

    def vertex_ids(self, count: int | None) -> I64:
        """The B-rep vertex ids, ascending (all vertices need their count)."""
        return ids_of(self.vertices, count)

    def beyond(self, count: int | None) -> list[int]:
        """Face ids at or past ``count`` (faces the part no longer has)."""
        if self.faces is None or count is None:
            return []
        ids = self.ids(None)
        return [int(i) for i in ids[ids >= count]]

    def to_json(self, *, basis: bool = True) -> dict[str, Any]:  # Any: JSON
        """The stored member.

        ``faces`` is written unless the member has no faces and does name edges or vertices, in
        which case ADR-0022 §15.4 says an edge- or point-only member omits it ("absent fields mean
        none"). A face-only tag's text is therefore byte-for-byte what it was before edges existed.
        """
        data: dict[str, Any] = {"part": self.part}
        if not (self.faces == () and (self.edges != () or self.vertices != ())):
            data["faces"] = _dump_set(self.faces)
        if self.edges != ():
            data["edges"] = _dump_set(self.edges)
        if self.vertices != ():
            data["vertices"] = _dump_set(self.vertices)
        if basis and self.basis and self.explicit:
            data["basis"] = self.basis
        return data


def _dump_set(ids: Ranges | None) -> Any:  # Any: JSON
    """One id set as stored JSON: ``"all"``, or a list of ``[first, last]`` ranges."""
    if ids is None:
        return ALL_FACES
    return [[a, b] for a, b in ids]


def ids_of(ids: Ranges | None, count: int | None) -> I64:
    """The ids of one set, ascending; ``None`` (all of them) needs the count."""
    if ids is None:
        return np.arange(count or 0, dtype=np.int64)
    if not ids:
        return np.zeros(0, dtype=np.int64)
    return np.concatenate([np.arange(first, last + 1, dtype=np.int64) for first, last in ids])


@dataclass(frozen=True)
class Target:
    """What a part reference in a command's members resolves to."""

    part: str
    faces: int | None
    """the part's face count, None while unknown"""


# ---------------------------------------------------------------- the stored text


def dump_members(members: Sequence[Member]) -> str:
    """The canonical JSON text stored in ``tag.members``."""
    return json.dumps([m.to_json() for m in members], separators=(",", ":"))


def load_members(text: str, *, where: str = "the tag") -> tuple[Member, ...]:
    """Members from stored text; :class:`SelectionError` when it is not what the commands
    write (someone set it by hand). Remembered for recent texts: a command reads a tag's
    members more than once, and a tag of thousands of parts takes a tenth of a second to
    parse and check (the members are immutable, so sharing them is safe)."""
    return _load(text, where)


@lru_cache(maxsize=32)
def _load(text: str, where: str) -> tuple[Member, ...]:
    if len(text) > MAX_TEXT:
        raise SelectionError(f"the members of {where} exceed {MAX_TEXT} bytes")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise _malformed(where, f"not JSON ({exc})") from exc
    if not isinstance(data, list) or len(data) > MAX_MEMBERS:
        raise _malformed(where, f"not a list of at most {MAX_MEMBERS} members")
    found: list[Member] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or not set(item) <= _MEMBER_KEYS:
            raise _malformed(
                where, "a member is not an object of part, faces, edges, vertices and basis"
            )
        part, basis = item.get("part"), item.get("basis")
        if not isinstance(part, str) or not is_id(part) or part in seen:
            raise _malformed(where, f"bad or repeated part id {part!r}")
        if basis is not None and not (isinstance(basis, str) and _BASIS_RE.match(basis)):
            raise _malformed(where, f"bad basis {basis!r}")
        seen.add(part)
        # ADR-0022 §15.4: "absent fields mean none, `faces: "all"` still means the whole body, and
        # new edge/point-only members omit `faces`". A member that names *nothing at all* stays
        # malformed as it always was ("stored text is always explicit"), which is what keeps an old
        # bare {"part": x} document an error rather than a silently empty member.
        if "faces" in item:
            try:
                faces = _faces(item["faces"], None, part)
            except SelectionError as exc:
                raise _malformed(where, exc.message) from exc
        else:
            faces = ()
        try:
            edges = _ids(item.get("edges", []), part, "edge")
            vertices = _ids(item.get("vertices", []), part, "vertex")
        except SelectionError as exc:
            raise _malformed(where, exc.message) from exc
        if "faces" not in item and not edges and not vertices:
            raise _malformed(
                where, f"the member for {part} names nothing: give faces, edges or vertices"
            )
        if basis is not None and faces is None and not edges and not vertices:
            raise _malformed(
                where,
                f"part {part} carries a basis but names no explicit ids; a basis names the "
                "version explicit ids refer to (give faces, edges or vertices, or drop the basis)",
            )
        if faces is None or faces or edges or vertices:
            found.append(Member(part, faces, basis, edges, vertices))
    return tuple(found)


def _ids(value: Any, part: str, what: str) -> Ranges | None:  # Any: JSON
    """One stored edge/vertex id set: ``"all"`` → None, a list of ids and ranges → merged ranges.

    Shares :func:`_faces` so an edge range gets the same validation as a face range; only the error
    wording differs, and the caller re-wraps it with the tag's name.
    """
    try:
        return _faces(value, None, part)
    except SelectionError as exc:
        raise SelectionError(
            exc.message.replace("face ", f"{what} ").replace("faces ", f"{what}s "),
            details=exc.details,
        ) from exc


def _malformed(where: str, why: str) -> SelectionError:
    return SelectionError(
        f"the members of {where} are malformed: {why}; set them again with tag.set",
        details={"tag": where},
    )


# ---------------------------------------------------------------- command input


def parse_members(
    raw: Any,  # Any: JSON from a client
    resolve: Callable[[str], Sequence[Target]],
) -> tuple[Member, ...]:
    """Members from a command argument: ``[{"part": <id or path>, "faces": "all" | [id or
    [first, last], ...]}, ...]``. ``faces`` defaults to ``"all"``; a group stands for every part
    under it and takes only ``"all"``. ``resolve`` turns a reference into parts (and raises
    for an unknown one); a ``basis`` given here is ignored, the commands stamp their own."""
    if not isinstance(raw, list):
        raise SelectionError("members must be a list of {part, faces} objects")
    if len(raw) > MAX_MEMBERS:
        raise SelectionError(f"at most {MAX_MEMBERS} members in one call")
    found: list[Member] = []
    for item in raw:
        if not isinstance(item, dict) or not set(item) <= _MEMBER_KEYS or "part" not in item:
            raise SelectionError(
                'each member is an object {"part": <part id or path>, "faces": "all" or a list '
                "of face ids and [first, last] ranges}",
                details={"member": _preview(item)},
            )
        ref = item["part"]
        if not isinstance(ref, str) or not ref:
            raise SelectionError("a member's part must be a node id or path")
        targets = resolve(ref)
        edge_ids = item.get("edges", [])
        vertex_ids = item.get("vertices", [])
        # `faces` defaults to "all" ONLY for a member that names no other kind. A member naming only
        # edges or only vertices must not become a whole-part member: §15.4 says "absent fields mean
        # none", and defaulting to all here turned an edge-only tag into every face of the part —
        # which is what `tag.create` did before this fix, silently discarding the edges.
        # An empty LIST, not an empty tuple: `_faces` accepts "all" or a list, and `[]` is the
        # stored spelling of "no faces" (`_dump_set` writes "all" for None).
        # A member that names NOTHING is malformed (the owner's answer 4, via Claude 2026-10-12):
        # `{"part": x}` alone is not "no members of interest" but a client that forgot to say which.
        # This path used to default it to "all faces" while `geometry.model` already rejected the
        # same input, so the reader accepted what the writer refused — see that module's message.
        if "faces" not in item and not edge_ids and not vertex_ids:
            raise SelectionError(
                f"the member for {ref} names nothing; give faces, edges or vertices",
                details={"part": ref},
            )
        # `get` with the computed default: an explicit `faces` wins, else an edge- or vertex-only
        # member gets `[]` (none), and anything else the whole part.
        given = item.get("faces", [] if edge_ids or vertex_ids else ALL_FACES)
        if given != ALL_FACES and len(targets) != 1:
            raise SelectionError(
                f"{ref} holds {len(targets)} parts; face ids need one part (or faces 'all')",
                details={"part": ref},
            )
        if (edge_ids or vertex_ids) and len(targets) != 1:
            raise SelectionError(
                f"{ref} holds {len(targets)} parts; edge and vertex ids need one part",
                details={"part": ref},
            )
        for target in targets:
            faces = _faces(given, target.faces, ref)
            edges = _ids(edge_ids, ref, "edge")
            vertices = _ids(vertex_ids, ref, "vertex")
            # Keep whole-part members (`faces` is None) as well as explicit ones; only a member
            # that names nothing at all is dropped.
            if faces is None or faces or edges or vertices:
                found.append(Member(target.part, faces, None, edges, vertices))
    return union((), found)


def _faces(value: Any, count: int | None, ref: str) -> Ranges | None:  # Any: JSON
    """``"all"`` → None; a list of ids and [first, last] pairs → merged ranges."""
    if value == ALL_FACES:
        return None
    if not isinstance(value, list):
        raise SelectionError(f"faces of {ref} must be 'all' or a list", details={"part": ref})
    if len(value) > MAX_RANGES:
        raise SelectionError(f"at most {MAX_RANGES} face ranges per part", details={"part": ref})
    ranges: list[tuple[int, int]] = []
    for entry in value:
        if _is_int(entry):
            first = last = int(entry)
        elif isinstance(entry, list | tuple) and len(entry) == 2 and all(map(_is_int, entry)):
            first, last = int(entry[0]), int(entry[1])
        else:
            raise SelectionError(
                f"a face of {ref} is {_preview(entry)}: give an id or a [first, last] range",
                details={"part": ref},
            )
        if not 0 <= first <= last <= MAX_FACE_ID:
            raise SelectionError(
                f"face range [{first}, {last}] of {ref} is not 0 <= first <= last",
                details={"part": ref},
            )
        if count is not None and last >= count:
            raise SelectionError(
                f"{ref} has {count} faces (ids 0 to {count - 1}); face {last} does not exist",
                details={"part": ref, "faces": count, "face": last},
            )
        ranges.append((first, last))
    return merge(ranges)


def _is_int(value: Any) -> bool:  # Any: JSON
    return isinstance(value, int) and not isinstance(value, bool)


def _preview(value: Any) -> str:  # Any: JSON
    text = json.dumps(value, default=str)
    return text if len(text) <= 80 else text[:77] + "..."


# ---------------------------------------------------------------- ranges


def merge(ranges: Iterable[tuple[int, int]]) -> Ranges:
    """Sorted, merged inclusive ranges (adjacent ranges join)."""
    out: list[list[int]] = []
    for first, last in sorted(ranges):
        if out and first <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], last)
        else:
            out.append([first, last])
    return tuple((a, b) for a, b in out)


def subtract(have: Ranges, drop: Ranges) -> Ranges:
    """``have`` without ``drop`` (both merged)."""
    result: list[tuple[int, int]] = []
    j = 0
    for first, last in have:
        while j < len(drop) and drop[j][1] < first:
            j += 1
        current = first
        k = j
        while k < len(drop) and drop[k][0] <= last and current <= last:
            if drop[k][0] > current:
                result.append((current, drop[k][0] - 1))
            current = max(current, drop[k][1] + 1)
            k += 1
        if current <= last:
            result.append((current, last))
    return tuple(result)


def complement(have: Ranges, count: int) -> Ranges:
    """Face ids 0 to ``count`` - 1 that ``have`` lacks."""
    return subtract(((0, count - 1),), have) if count > 0 else ()


def ranges_from_mask(mask: Mask) -> Ranges:
    """Ranges of the True entries of a boolean array indexed by face id."""
    flags = np.asarray(mask, dtype=np.int8)
    if not flags.any():
        return ()
    steps = np.diff(np.concatenate(([0], flags, [0])))
    starts = np.flatnonzero(steps == 1)
    ends = np.flatnonzero(steps == -1) - 1
    return tuple(zip(starts.tolist(), ends.tolist(), strict=True))


def ranges_from_ids(ids: Iterable[int] | I64) -> Ranges:
    unique = np.unique(np.asarray(list(ids) if not isinstance(ids, np.ndarray) else ids))
    if unique.size == 0:
        return ()
    breaks = np.flatnonzero(np.diff(unique) != 1)
    starts = np.concatenate(([unique[0]], unique[breaks + 1]))
    ends = np.concatenate((unique[breaks], [unique[-1]]))
    return tuple(zip(starts.tolist(), ends.tolist(), strict=True))


# ---------------------------------------------------------------- member arithmetic


def union(have: Sequence[Member], add: Sequence[Member]) -> tuple[Member, ...]:
    """Every id in either, per id set; parts keep the order they were first added in."""
    out = {m.part: m for m in have}
    for member in add:
        old = out.get(member.part)
        if old is None:
            out[member.part] = member
            continue
        if old.faces is None or member.faces is None:
            # A whole-part member (either side) absorbs the explicit face ids. No basis is stored
            # with it: a basis names the version explicit ids refer to. Edge and vertex sets are
            # still unioned, so "all faces plus these edges" keeps its edges.
            faces: Ranges | None = None
            basis: str | None = None
        else:
            faces = merge(old.faces + member.faces)
            basis = member.basis or old.basis
        out[member.part] = Member(
            member.part,
            faces,
            basis,
            _union_set(old.edges, member.edges),
            _union_set(old.vertices, member.vertices),
        )
    return tuple(out.values())


def _union_set(first: Ranges | None, second: Ranges | None) -> Ranges | None:
    """One id set unioned: ``None`` (every id) absorbs everything, as a whole face set does."""
    if first is None or second is None:
        return None
    return merge(first + second)


def difference(
    have: Sequence[Member], drop: Sequence[Member], count: CountOf
) -> tuple[Member, ...]:
    """The ids of ``have`` that ``drop`` lacks, per id set. Taking ids out of a whole set needs
    that set's count, which only faces have a way to ask for here; edges and vertices are only
    ever explicit in a member, so a whole edge set is removed wholesale rather than complemented."""
    removed = {m.part: m for m in drop}
    out: list[Member] = []
    for member in have:
        gone = removed.get(member.part)
        if gone is None:
            out.append(member)
            continue
        if gone.faces is None and gone.edges is None and gone.vertices is None:
            continue  # everything of this part is gone
        if gone.faces is None:
            faces: Ranges | None = None
        elif member.faces is None:
            faces = complement(gone.faces, _count(count, member.part))
        else:
            faces = subtract(member.faces, gone.faces)
        edges = _subtract_set(member.edges, gone.edges)
        vertices = _subtract_set(member.vertices, gone.vertices)
        if faces or edges or vertices:
            out.append(Member(member.part, faces, gone.basis or member.basis, edges, vertices))
    return tuple(out)


def _subtract_set(have: Ranges | None, drop: Ranges | None) -> Ranges | None:
    """One id set without ``drop``.

    ``drop`` = None means every id of that set is removed, so nothing is left whatever ``have``
    was. ``have`` = None means "all of them", and "all except an explicit few" cannot be written as
    ranges without knowing the total, which this call is not given: that case raises rather than
    inventing an answer, because either silent choice (keep all, or drop all) corrupts the tag.
    """
    if drop is None:
        return ()
    if have is None:
        raise SelectionError(
            "cannot remove named edge or vertex ids from a member that holds all of them: "
            "the count is unknown; select explicit ids first",
            details={"kind": "edges/vertices"},
        )
    return subtract(have, drop)


def invert(have: Sequence[Member], parts: Sequence[str], count: CountOf) -> tuple[Member, ...]:
    """Within ``parts``, the faces the tag lacks; other parts keep their members.

    Only faces are inverted: complementing an edge or vertex set needs that set's count, which this
    call is not given, and inventing one would delete a tag's contents on a missing cache. A part
    with none of its faces selected becomes a whole member, a whole member goes; edge and vertex
    members are carried through untouched."""
    within = dict.fromkeys(parts)
    out: list[Member] = []
    for member in have:
        if member.part not in within:
            out.append(member)
        elif member.faces is not None:
            faces = complement(member.faces, _count(count, member.part))
            if faces or member.edges or member.vertices:
                out.append(Member(member.part, faces, member.basis, member.edges, member.vertices))
    present = {m.part for m in have}
    out.extend(Member(part, None) for part in within if part not in present)
    return tuple(out)


def _count(count: CountOf, part: str) -> int:
    found = count(part)
    if found is None:
        raise SelectionError(
            f"the face count of part {part} is not known yet (its file has not been read); "
            "wait for the import or select faces by id",
            details={"part": part},
        )
    return found


# ---------------------------------------------------------------- growth


def grow_faces(adjacency: Adjacency, face_total: int, seeds: I64, angle: float) -> Mask:
    """Faces reachable from ``seeds`` without crossing a boundary sharper than ``angle``
    degrees: a mask over the batch faces of the adjacency's surface, seeds included."""
    if not 0.0 < angle <= 180.0:
        raise SelectionError(f"the feature angle must lie in (0, 180] degrees; got {angle}")
    if seeds.size == 0 or face_total == 0:
        return np.zeros(face_total, dtype=bool)
    limit = math.cos(math.radians(angle))
    with np.errstate(invalid="ignore"):
        smooth = adjacency.cos > limit  # NaN (no measurable edge) never counts as smooth
    labels = components(adjacency.pairs[smooth], face_total)
    return np.asarray(np.isin(labels, labels[seeds]), dtype=bool)


def grow_parts(
    adjacency: Mapping[str, Adjacency],
    counts: Mapping[str, int],
    seeds: Mapping[str, I64],
    angle: float,
) -> dict[str, Ranges]:
    """:func:`grow_faces` over several parts at once: their face graphs side by side, so
    thousands of parts cost one pass, not one each. Seeds past a part's faces are ignored."""
    parts = list(seeds)
    offsets = np.cumsum([0, *(counts[p] for p in parts)]).astype(np.int64)
    pairs = [adjacency[p].pairs + offsets[i] for i, p in enumerate(parts)]
    starts = [
        ids[(ids >= 0) & (ids < counts[p])] + offsets[i] for i, (p, ids) in enumerate(seeds.items())
    ]
    joined = Adjacency(
        np.concatenate(pairs) if pairs else np.zeros((0, 2), dtype=np.int64),
        np.concatenate([adjacency[p].cos for p in parts]) if parts else np.zeros(0),
    )
    found = np.concatenate(starts) if starts else np.zeros(0, dtype=np.int64)
    mask = grow_faces(joined, int(offsets[-1]), found, angle)
    return dict(zip(parts, ranges_by_part(mask, offsets), strict=True))


def ranges_by_part(mask: Mask, offsets: I64) -> list[Ranges]:
    """:func:`ranges_from_mask` for each part of a mask laid out part after part (``offsets``:
    each part's first index, then the total), in one pass: no run crosses a part boundary."""
    total = int(offsets[-1])
    if total == 0 or not mask.any():
        return [() for _ in range(offsets.shape[0] - 1)]
    edge_start = np.zeros(total, dtype=bool)
    edge_end = np.zeros(total, dtype=bool)
    sizes = np.diff(offsets)
    edge_start[offsets[:-1][sizes > 0]] = True
    edge_end[offsets[1:][sizes > 0] - 1] = True
    before = np.concatenate(([False], mask[:-1]))
    after = np.concatenate((mask[1:], [False]))
    firsts = np.flatnonzero(mask & (~before | edge_start))
    lasts = np.flatnonzero(mask & (~after | edge_end))
    owner = np.searchsorted(offsets, firsts, side="right") - 1
    base = offsets[owner]
    lows, highs = (firsts - base).tolist(), (lasts - base).tolist()
    bounds = np.searchsorted(owner, np.arange(offsets.shape[0]), side="left").tolist()
    return [
        tuple(zip(lows[bounds[i] : bounds[i + 1]], highs[bounds[i] : bounds[i + 1]], strict=True))
        for i in range(offsets.shape[0] - 1)
    ]


def components(links: I64, count: int) -> I64:
    """Connected-component labels of ``count`` nodes joined by ``links`` (Q, 2): each node gets
    the smallest node id of its component.

    Hook and compress in numpy: every link hooks the root of its larger end under the smaller
    root, then pointer jumping flattens the trees, until no link joins two labels. A face graph
    has at most tens of thousands of nodes, so a few rounds of whole-array work beat importing
    scipy's graph module, which costs seconds the first time on a busy laptop.
    """
    labels = np.arange(count, dtype=np.int64)
    if links.size == 0:
        return labels
    first, second = links[:, 0], links[:, 1]
    while True:
        a, b = labels[first], labels[second]
        apart = a != b
        if not apart.any():
            return labels
        low, high = np.minimum(a[apart], b[apart]), np.maximum(a[apart], b[apart])
        np.minimum.at(labels, high, low)
        while True:
            jumped = labels[labels]
            if np.array_equal(jumped, labels):
                break
            labels = jumped
