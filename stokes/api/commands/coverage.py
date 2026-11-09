"""boundary.overlap and boundary.uncovered: which faces a boundary already holds, and which none
does, asked before an assign rather than found by ``setup.check`` afterwards.

``boundary.overlap`` takes members in the tag commands' form (the live pick) and names every
boundary-kind tag that already holds some of those faces, with the boundaries that use it. A
pick assigned anyway makes a second tag on the same faces: decisions log G10 allows that while
preparing, with a ``tag.overlap`` warning, and the case compiler refuses it. So a client warns
and lets the user go on.

``boundary.uncovered`` names, per part, the faces no boundary's tag holds; the case compiler
makes them a wall called ``unassigned``. In 2D (ADR-0031) the domain part's faces across the
plane are the mesher's front and back, never ``unassigned``, so they are not listed. Coverage is
by the boundaries of every region, which for a body in one region is what ``region.list``'s
``uncovered_faces`` counts. Faces come back
as merged inclusive ranges, so a client can pick them without the server expanding a large part.

Both are queries (``mutates=False``): nothing is journaled, and a replayed journal answers the
same. Range arithmetic over the stored members, never expanded to face ids.

Import rule: the setup helpers and the geometry model (standard library only) at module level;
the tag internals (numpy) inside the handlers, as the other tag commands do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stokes.api.commands.setup_plan import (
    boundary_nodes,
    ref_node,
    region_nodes,
    stored_members,
    users_of,
)
from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import StokesError
from stokes.geometry.model import BOUNDARY_TYPE, TAG_TYPE, TAGS_PATH

if TYPE_CHECKING:
    from stokes.api.commands.selection import _Geometry
    from stokes.geometry.selection import Member
    from stokes.model import Node

MEMBERS_HELP = (
    'Faces to test, as the tag commands take them: [{"part": <part id or path>, "faces": "all" '
    "or [ids and [first, last] ranges]}]."
)

DESCRIBED = 64
"""Faces described per part: a guided list shows a handful, and a large part must not cost a
fingerprint walk over every face for a list nobody scrolls."""

FLAT = 0.999
"""Mean facet-normal length above which a face reads as flat: 1 on a plane, well below on a
cylinder or a torus segment (``stokes.geometry.fingerprints``)."""


def _boundary_tags(geo: _Geometry) -> list[tuple[Node, tuple[Member, ...]]]:
    """Every boundary-kind tag with its members; a malformed tag is skipped (``tag.list`` and
    ``setup.check`` report it)."""
    group = geo.project.find(TAGS_PATH)
    found: list[tuple[Node, tuple[Member, ...]]] = []
    for tag in group.children if group is not None else []:
        if tag.type != TAG_TYPE or tag.params.get("kind") != "boundary":
            continue
        try:
            found.append((tag, stored_members(tag)))
        except StokesError:
            continue
    return found


@command(
    "boundary.overlap",
    params=(ParamDef("members", "array", description=MEMBERS_HELP),),
    mutates=False,
)
def boundary_overlap(ctx: CommandContext, members: list[Any]) -> dict[str, Any]:  # Any: JSON
    """The boundary-kind tags that already hold some of the given faces, per tag and part, with
    the boundaries that use each tag: what an assign of those faces would overlap (G10 allows it
    with a warning; the case compiler refuses it)."""
    from stokes.api.commands.selection import _Geometry, _given, _shared

    geo = _Geometry(ctx)
    given = _given(geo, members)
    users = users_of(geo.project, (BOUNDARY_TYPE,))
    found: list[dict[str, Any]] = []  # Any: JSON
    total: int | None = 0
    for tag, held in _boundary_tags(geo):
        for member in held:
            for asked in given:
                if asked.part != member.part:
                    continue
                shared = _shared(geo, asked, member)
                if shared == 0:
                    continue
                total = None if shared is None or total is None else total + shared
                found.append(
                    {
                        "tag": {
                            "id": tag.id,
                            "name": tag.name,
                            "path": tag.path,
                            "color": tag.params.get("color"),
                        },
                        "boundaries": [
                            {
                                "id": b.id,
                                "name": b.name,
                                "path": b.path,
                                "kind": b.params.get("kind"),
                            }
                            for b in users.get(tag.id, [])
                        ],
                        "part": member.part,
                        "faces": shared,
                    }
                )
    return {"overlaps": found, "faces": total}


@command(
    "boundary.uncovered",
    params=(
        ParamDef(
            "part",
            "string",
            required=False,
            description="A part, or a group of parts (id or path). Default: the bodies of every "
            "region.",
        ),
        ParamDef(
            "describe",
            "boolean",
            required=False,
            default=False,
            description="Also describe the first unassigned faces of each part (area in m², "
            "flat or curved, centre and mean normal), so a client can say which face is which. "
            f"At most {DESCRIBED} faces per part; none while a part's mesh is not loaded.",
        ),
    ),
    mutates=False,
)
def boundary_uncovered(
    ctx: CommandContext, part: str | None = None, describe: bool = False
) -> dict[str, Any]:  # Any: JSON
    """Per part, the faces no boundary's tag holds (the compiler makes them the ``unassigned``
    wall), as merged inclusive ranges with their count; ``faces`` is None while a part's face
    count is unknown. With ``describe``, each entry also carries ``described``: what the first
    of those faces look like, because "Face 3" alone does not tell a user which end is the
    inlet."""
    from stokes.api.commands.selection import _Geometry

    geo = _Geometry(ctx)
    project = geo.project
    if part is not None:
        parts = [p.id for p in geo.part_nodes(part)]
    else:
        parts = []
        for region in region_nodes(project):
            tag = ref_node(project, region, "tag", TAG_TYPE)
            try:
                bodies = stored_members(tag) if tag is not None else ()
            except StokesError:
                continue
            parts.extend(m.part for m in bodies if m.part not in parts)
    whole, covered = _covered(geo)
    for owner, faces in _two_d_owned(geo).items():
        covered.setdefault(owner, []).extend(faces)
    entries = [_entry(geo, p, p in whole, covered.get(p, [])) for p in parts]
    if describe:
        for entry in entries:
            entry["described"] = _describe(geo, entry["part"], entry["ranges"])
    known = [e["faces"] for e in entries]
    total = None if any(f is None for f in known) else sum(f for f in known if f is not None)
    return {"parts": entries, "faces": total}


def _describe(
    geo: _Geometry, part: str, ranges: list[list[int]]
) -> list[dict[str, Any]]:  # Any: JSON
    """Area, flatness, centre and mean normal of the first unassigned faces of ``part``, in SI
    units; empty when the part's mesh is not loaded (a replayed session, say)."""
    import numpy as np

    mesh = geo.mesh(part)
    if mesh is None:
        return []
    fp = geo.cache.fingerprints({part: mesh})[part]
    ids: list[int] = []
    for first, last in ranges:
        ids.extend(range(first, min(last + 1, first + DESCRIBED - len(ids))))
        if len(ids) >= DESCRIBED:
            break
    found: list[dict[str, Any]] = []
    for face in ids:
        if not 0 <= face < fp.faces:
            continue
        normal = fp.normal[face]
        length = float(np.linalg.norm(normal))
        found.append(
            {
                "face": face,
                "area": float(fp.area[face]),
                "flat": length > FLAT,
                "centroid": [float(v) for v in fp.centroid[face]],
                "normal": [float(v) / length if length > 1e-9 else 0.0 for v in normal],
            }
        )
    return found


def _two_d_owned(geo: _Geometry) -> dict[str, list[tuple[int, int]]]:
    """In 2D (ADR-0031), the domain part's faces the MESHER owns: the flat faces on the part's two
    bounding planes across the 2D plane, which become the `empty` (or `wedge`) front and back
    whatever tag holds them. They are not "unassigned", so they are counted as covered.

    Read from the face fingerprints (centroid and mean normal); empty when 2D is off, the domain
    part is not set, or its mesh is not loaded.
    """
    from stokes.mesh.model import SETTINGS_PATH
    from stokes.mesh.two_d import from_settings

    settings = geo.project.find(SETTINGS_PATH)
    if settings is None:
        return {}
    own = {
        name: getattr(settings.params.get(name), "value", settings.params.get(name))
        for name in ("two_d", "plane")
    }
    try:
        two = from_settings(own)
    except StokesError:
        return {}
    part = settings.params.get("domain_part")
    if two is None or not part:
        return {}
    mesh = geo.mesh(str(part))
    if mesh is None:
        return {}
    import numpy as np

    fp = geo.cache.fingerprints({str(part): mesh})[str(part)]
    if fp.faces == 0:
        return {}
    n = two.normal
    centre = np.asarray(fp.centroid, dtype=float)[: fp.faces, n]
    normal = np.asarray(fp.normal, dtype=float)[: fp.faces]
    length = np.linalg.norm(normal, axis=1)
    lo, hi = float(centre.min()), float(centre.max())
    tol = 1e-6 * max(1.0, abs(hi - lo), float(np.abs(centre).max()))
    across = (length > FLAT) & (np.abs(normal[:, n]) > FLAT * np.maximum(length, 1e-12))
    on_plane = (np.abs(centre - lo) <= tol) | (np.abs(centre - hi) <= tol)
    ids = [int(i) for i in np.nonzero(across & on_plane)[0]]
    return {str(part): [(i, i) for i in ids]} if ids else {}


def _covered(geo: _Geometry) -> tuple[set[str], dict[str, list[tuple[int, int]]]]:
    """The parts some boundary holds whole, and the face ranges boundaries hold of the others."""
    project = geo.project
    whole: set[str] = set()
    covered: dict[str, list[tuple[int, int]]] = {}
    for boundary in boundary_nodes(project):
        if boundary.type != BOUNDARY_TYPE:
            continue
        tag = ref_node(project, boundary, "tag", TAG_TYPE)
        try:
            members = stored_members(tag) if tag is not None else ()
        except StokesError:
            continue
        for member in members:
            if member.faces is None:
                whole.add(member.part)
            else:
                covered.setdefault(member.part, []).extend(member.faces)
    return whole, covered


def _entry(
    geo: _Geometry, part: str, whole: bool, covered: list[tuple[int, int]]
) -> dict[str, Any]:  # Any: JSON
    from stokes.geometry.selection import complement, merge

    node = geo.node(part)
    base = {"part": part, "name": node.name if node else part, "path": geo.path(part)}
    count = geo.count(part)
    if whole:
        return {**base, "total": count, "faces": 0, "ranges": []}
    if count is None:
        return {**base, "total": None, "faces": None, "ranges": []}
    left = complement(merge(covered), count)
    return {
        **base,
        "total": count,
        "faces": sum(last - first + 1 for first, last in left),
        "ranges": [[first, last] for first, last in left],
    }
