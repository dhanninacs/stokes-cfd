"""The layer-count resolution (brief M-504, the 2026-10-19 1130 decision).

The counts a user set become the per-wall counts snappy's ``addLayersControls`` carries, through
three levels, strongest first:

1. **The boundary override** (``boundary.layers``): empty = inherit, ``0`` = none on this wall,
   ``n`` = n. Set on a boundary whose kind is not ``wall`` it is refused - "layers need a wall;
   inlet is an inlet" - and the refusal happens here, so the snapshot builder that calls
   :func:`effective_layers` raises before a dictionary is written.
2. **The domain body's default** (``part.layers``), applied to its wall tags only.
3. **The mesh settings' default** (``mesh_settings.layers``), applied to wall tags only.

A symmetry, cyclic, interface, inlet or outlet never inherits - the layers block keys walls, and
writing an entry for a patch that is not a wall would either do nothing (snappy skips it) or lie.
``unassigned`` - the part's untagged skin, which the driver makes a wall - inherits like any
wall, so :func:`inherit_layers` answers the count a wall with no override and no body default
gets and the snapshot builder adds the ``unassigned`` entry with it.

## The stored shapes, and why they are what they are

Both ``part.layers`` and ``boundary.layers`` are ``int`` parameters with **no default**: the
parameter's absence (or a stored null) is the EMPTY state, and ``parameter.set`` with null
clears them back to it. ``0`` is a meaning (none), not "not set" - which is why the range hook
(``stokes.model.schema.validate_layer_count``) and the persist v2->v3 migration exist: a ``0``
stored while the withdrawn 2026-10-18 parameter ran (its default 0 meant INHERIT) is dropped at
load, so an old project keeps its meaning and a stored ``0`` from this one on means none.

## Pure, and what that costs

Every function here reads only the project tree - no geometry store, no session, no face
counts. The consequence is that :func:`effective_layers` cannot see whether the part HAS
untagged faces (that number lives in the display mesh), so it resolves tags only and the
snapshot builder adds ``unassigned`` itself.
"""

from __future__ import annotations

from typing import Any

from stokes.errors import StokesError
from stokes.mesh.errors import MeshInvalid

#: The only kinds a layer count means anything on. A wall is where layers grow; the
#: catalogue's other kinds (ADR-0022) name places a layer cannot be, and a symmetry plane is
#: written as patch type ``wall`` in snappy (it still never inherits - the decision says so).
WALL_KIND = "wall"

#: The surface the driver creates for the part's untagged faces; it is a wall, so it inherits.
UNASSIGNED = "unassigned"


def inherit_layers(project: Any, part: Any) -> int | None:
    """The count a wall with no override and no body default gets, or None when nothing does.

    The body's ``layers`` when it holds one, else the mesh settings'. **Both empty means no
    default at all** - the caller writes no entry, and the dictionary stays as if layers were
    never mentioned.
    """
    own = _stored_count(part)
    if own is not None:
        return own
    settings = project.find("/mesh/settings")
    if settings is None:
        return None
    return _stored_count(settings)


def effective_layers(project: Any, part: Any) -> dict[str, int]:
    """The resolved per-wall counts of ``part``'s tags: ``{surface name: count}``.

    The surface names are the tags' names (the snappy surfaces are named after them) plus,
    added by the caller, ``unassigned``. An override is always an entry - including ``0``, so
    an explicit "none on this wall" is distinguishable from "no decision" - and an inherited
    count appears only above zero: the writer keys walls that get layers, and an entry of 0
    there would be a patch asked for no layers, which is what its absence already says.

    A **non-wall override raises** with the boundary's name and kind, and the snapshot passes
    it through: "layers need a wall; inlet is an inlet".
    """
    from stokes.api.commands.setup_plan import stored_members  # the tag commands own the value

    default = inherit_layers(project, part)
    counts: dict[str, int] = {}
    part_id = str(part.id)
    for node in project.root.walk():
        if node.type != "tag":
            continue
        if not _member_of(node, part_id, stored_members):
            continue
        boundary = _boundary_of(project, str(node.id))
        name = str(node.name)
        override: int | None = None
        if boundary is not None:
            override = _stored_count(boundary)
            kind = str(boundary.params.get("kind", ""))
            if override is not None and kind != WALL_KIND:
                raise MeshInvalid(
                    f"layers need a wall; {boundary.name!r} is a {kind or 'patch'}",
                    details={"code": "mesh.not_wall", "boundary": str(boundary.name)},
                )
        if override is not None:
            counts[name] = override
        elif _is_wall(boundary) and default is not None and default > 0:
            counts[name] = default
    return counts


def inherit_layers_for_unassigned(project: Any, part: Any, unassigned_faces: bool) -> int | None:
    """The count for the ``unassigned`` surface, or None when it gets no entry.

    ``unassigned`` is a wall and inherits like any wall - but only when it EXISTS (there are
    untagged faces) and something above it asks for layers. The ``unassigned_faces`` flag is
    the one thing the project tree cannot answer, which is why this function takes it and
    :func:`effective_layers` does not carry the surface at all.
    """
    if not unassigned_faces:
        return None
    default = inherit_layers(project, part)
    if default is not None and default > 0:
        return default
    return None


def _member_of(tag: Any, part_id: str, stored_members: Any) -> bool:
    """Whether any member of ``tag`` names ``part_id`` (faces, edges or vertices)."""
    try:
        members = stored_members(tag)
    except StokesError:
        # A malformed tag is the tag commands' business and the snapshot's check, not the
        # layer resolution's: a tag that cannot be read contributes no counts.
        return False
    return any(str(member.part) == part_id for member in members)


def _boundary_of(project: Any, tag_id: str) -> Any | None:
    """The ``boundary`` node that references ``tag_id``, or None."""
    for node, param in project.referrers(tag_id):
        if getattr(node, "type", "") == "boundary" and param == "tag":
            return node
    return None


def _is_wall(boundary: Any | None) -> bool:
    """Whether the boundary is a wall. A tag no boundary uses is not a wall to inherit on.

    Symmetry uses a ``symmetry`` mesh patch but is still not a wall for layer purposes: symmetry
    never inherits layers, so only the ``wall`` kind is a wall here.
    """
    return boundary is not None and str(boundary.params.get("kind", "")) == WALL_KIND


def _stored_count(node: Any) -> int | None:
    """The node's stored ``layers``: an int 0..10, or None for the empty state."""
    stored = node.params.get("layers")
    if isinstance(stored, bool) or not isinstance(stored, int):
        return None
    return int(stored)
