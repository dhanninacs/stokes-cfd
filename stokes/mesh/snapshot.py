"""What blockMesh needs, read from the tree once (T-145a, T-206).

## The two halves, and why they are separate

`side_mapping` is **pure**: six arrays of triangle data in, a side-to-tag map out. It has no
tree, no
session and no store, so it can be tested on a synthetic box and reasoned about without knowing
what a
`tag` node is.

`build_snapshot` reads the tree and the geometry store **on the session thread** and nothing
else. It
writes nothing and starts nothing, which is what lets T-207 (the mesh service) call it from a
command and
hand the result to a worker.

## Why the mapping refuses a conflict rather than picking

A `blockMeshDict` gives each of its six sides exactly **one** patch. If two tags have faces on
the same
side — `inlet` and `walls` both touching `xmin` — then one of them is not going to be in the
mesh, and
choosing quietly would produce a mesh whose boundary is not what the user tagged. So a conflict is
`mesh.invalid` with both names in the message, and the user decides.

An **unmapped** tag is different: it does not raise. A tag whose faces lie inside the domain (a heat
sink's surfaces, say) genuinely cannot be a blockMesh patch, and the honest answer is to report
it so
T-207 can put it in the result's warnings. Silently dropping it would be the same mistake as
guessing.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stokes.cores import core_count
from stokes.log import fields, get_logger
from stokes.mesh.errors import MeshInvalid, MeshPartExcluded
from stokes.mesh.two_d import TwoD, check_radius, from_settings, refuse_reserved, with_thickness

if TYPE_CHECKING:  # pragma: no cover - typing only, so numpy stays off the import path
    from stokes.api.session import Session
    from stokes.model.nodes import Node

log = get_logger(__name__)

#: The six sides of a box, in the order the boundary file lists them.
SIDES: tuple[str, ...] = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")


@dataclass(frozen=True)
class MeshSnapshot:
    """Everything one blockMesh job needs, as a plain record.

    ``sides`` maps a side to the NAME of the tag whose faces lie on it — that name becomes the
    patch name,
    so a user's tag called `inlet` produces a patch called `inlet`. A side no tag holds is
    absent here, and the driver writes it as the patch `unassigned` **of type `wall`**.

    ``side_kinds`` maps a side to the **kind** of the `boundary` node that references its tag,
    which is
    what decides the patch TYPE in the boundary file: a `wall` kind is `wall`, `symmetry` is
    `symmetryPlane`, and everything else is `patch`. The `unassigned` patch is not in `sides`, so
    it has no kind: its type is the driver's `DEFAULT_PATCH_TYPE` (`wall`).
    """

    settings_id: str
    mesher: str
    base_size: float
    bbox: tuple[float, float, float, float, float, float]
    sides: dict[str, str]
    side_tags: dict[str, str]
    side_kinds: dict[str, str]
    unmapped: tuple[str, ...]
    max_cells: int
    cores: int
    max_non_ortho: float
    max_skewness: float
    domain_part: str | None
    part_basis: str
    #: tag id -> its stored members JSON, kept for the basis. Only the tags that reached a side.
    tag_members: dict[str, Any]
    #: What shape the domain part is, from `stokes.mesh.shape_class` (T-180).
    #:
    #: **Defaulted to None so every existing construction keeps working**, and read by a driver that
    #: needs to know whether a box describes the part at all. A box-domain mesh has no part, so None
    #: is the honest value there rather than a fabricated `BOX`.
    shape: Any = None  # Any: ShapeReport, kept untyped so numpy/OCP stay off this import path
    #: The domain spelling this snapshot was built for: `part`, `box` or `enclosure`.
    #:
    #: **In the basis's `geometry` part** (brief M-501): `enclosure` meshes the OUTSIDE of the part
    #: in a grown box, so the same part under a different domain is a different mesh. Defaulted so
    #: every existing construction keeps working; the builder always passes it.
    domain: str = ""
    #: Tag name -> the sha1 of the tag's FULL sorted face-id list (brief M-501).
    #:
    #: **Computed where the full list exists** (`_tag_faces`), never from `tag_members` — that
    #: record is cut to 64 ids per tag for the result node's UI, so a re-picked face beyond the
    #: 64th would be invisible in a basis read from it. Defaulted so tests that build a bare
    #: `MeshSnapshot` keep working.
    tag_digests: dict[str, str] = field(default_factory=dict)
    #: Side -> the CAD face count the side's tag holds (brief M-501's dry-run precheck, which
    #: reports a patch's faces before anything runs). Empty where no side was mapped.
    side_faces: dict[str, int] = field(default_factory=dict)
    #: The per-body mesh controls that reached this mesh, as `{name: {size, refinement}}`.
    #:
    #: **In the snapshot so it can be in the BASIS** (brief M-501): a user who sets a body's size
    #: expects Generate to notice, and `mesh_basis` hashes this record's fields. Without it the
    #: staleness check could not see a per-body edit at all - it hashed `base_size`, the box and the
    #: side kinds, none of which a per-body control touches.
    body_mesh: dict[str, Any] = field(default_factory=dict)
    #: The 2D mode (ADR-0031) as `stokes.mesh.two_d.TwoD.record` gives it; **empty in 3D**, so a
    #: 3D snapshot, its basis and its dictionaries are what they were before 2D existed.
    two_d: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------- the pure half


def side_mapping(
    positions: Any,
    indices: Any,
    face_ids: Any,
    tag_faces: dict[str, set[int]],
    bbox: tuple[float, float, float, float, float, float],
    tol: float,
) -> tuple[dict[str, str], tuple[str, ...], dict[str, list[str]]]:
    """Map the box's six sides to tag names.

    A face lies on a side when **every** vertex of **every** triangle with that face id is
    within ``tol``
    of that side's plane. Every vertex, not the centroid: a face that spans the box corner is on
    two sides
    at once by its centroid and on neither by this rule, and "on neither" is the honest answer
    for a face
    that is not a side.

    Returns ``(sides, unmapped, conflicts)``. Tags are processed in **name order** so the result
    does not
    depend on the order the tree happens to hold them.
    """
    import numpy as np

    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    planes = {
        "xmin": (0, xmin),
        "xmax": (0, xmax),
        "ymin": (1, ymin),
        "ymax": (1, ymax),
        "zmin": (2, zmin),
        "zmax": (2, zmax),
    }
    # The vertex ids of each face id, gathered once: a face id owns several triangles and the same
    # corner appears in more than one of them.
    vertex_of_face: dict[int, set[int]] = {}
    face_of_triangle = np.asarray(face_ids)
    corners = np.asarray(indices)
    for triangle, face in enumerate(face_of_triangle):
        vertex_of_face.setdefault(int(face), set()).update(int(v) for v in corners[triangle])
    coordinates = np.asarray(positions, dtype="float64")

    def on_side(face: int, side: str) -> bool:
        axis, value = planes[side]
        ids = vertex_of_face.get(face)
        if not ids:
            return False
        return bool(np.all(np.abs(coordinates[list(ids), axis] - value) <= tol))

    sides: dict[str, str] = {}
    conflicts: dict[str, list[str]] = {}
    unmapped: list[str] = []
    # Name order, so two runs on equal input agree even if the tree's order differs.
    for name in sorted(tag_faces):
        faces = tag_faces[name]
        touched = [side for side in SIDES if any(on_side(face, side) for face in faces)]
        if not touched:
            unmapped.append(name)
            continue
        for side in touched:
            holder = sides.get(side)
            if holder is None:
                sides[side] = name
            elif holder != name:
                names = conflicts.setdefault(side, [holder])
                if name not in names:
                    names.append(name)
    return sides, tuple(unmapped), conflicts


# ---------------------------------------------------------------------- the tree half


def _two_d_sides(
    sides: dict[str, str],
    unmapped: tuple[str, ...],
    conflicts: dict[str, list[str]],
    two: TwoD,
    bbox: tuple[float, float, float, float, float, float],
    tol: float,
) -> tuple[dict[str, str], tuple[str, ...], dict[str, list[str]]]:
    """Take the 2D mesher's own sides out of the user's mapping (ADR-0031 decision 6).

    The two sides normal to the plane are the `empty`/`wedge` front and back, and an axisymmetric
    block's low radial side at radius 0 is the collapsed `axis`. A tag holding faces there loses
    those sides (and two tags meeting there are no conflict); a tag left holding no side at all is
    reported as unmapped, like any tag blockMesh cannot reach.
    """
    owned = set(two.normal_sides())
    if two.axisymmetric and abs(bbox[two.radial]) <= tol:
        owned.add(two.radial_low_side())
    kept = {side: name for side, name in sides.items() if side not in owned}
    lost = {name for side, name in sides.items() if side in owned} - set(kept.values())
    still = {side: names for side, names in conflicts.items() if side not in owned}
    return kept, tuple(sorted(set(unmapped) | lost)), still


def snapshot_for(session: Session, settings: Node) -> Any:
    """The snapshot the configured mesher needs. **The one entry point callers should use.**

    A `MeshSnapshot` describes six box sides, which is all blockMesh can mesh; snappyHexMesh follows
    the part's own surfaces and needs `SnappySnapshot` instead. Choosing between them here means the
    service never has to know which driver it is about to run, and a new mesher adds one branch in
    one place rather than a check at every call site.
    """
    from stokes.mesh.model import CFMESH_MESHERS
    from stokes.model import registry

    mesher = str(_params(settings, registry)["mesher"])
    if mesher == "snappy":
        return build_snappy_snapshot(session, settings)
    if mesher in CFMESH_MESHERS:
        return build_cfmesh_snapshot(session, settings, mesher)
    return build_snapshot(session, settings)


def build_cfmesh_snapshot(session: Session, settings: Node, mesher: str) -> Any:
    """The snappy record, re-labelled for a cfMesh mesher and with its surfaces joined (ADR-0032).

    **cfMesh needs what snappy needs** - a surface per tag, per-surface sizes, wall layers - so the
    record is built by the same reader and differs in two fields: `mesher`, and `stl_files`, which
    holds ONE multi-solid STL because cfMesh reads a single `surfaceFile` and names a patch after
    each `solid`. Joining here (not in the driver) keeps the staged files and the fidelity
    measurement's list of surfaces the same thing.

    **Only a `part` domain** in this slice: cfMesh meshes the inside of the closed surface, so an
    external (`enclosure`) case needs a box surface around the part, and a `box` domain has no
    surface at all. Both are refused by name rather than meshed as something else.
    """
    import dataclasses

    from stokes.mesh.drivers.cfmesh import SURFACE_FILE
    from stokes.mesh.drivers.snappy import GEOMETRY_DIR
    from stokes.model import registry

    domain = str(_params(settings, registry)["domain"])
    if domain != "part":
        raise MeshInvalid(
            f"{mesher} meshes a part's own volume; the {domain!r} domain needs snappyHexMesh",
            details={"code": "mesh.domain_unsupported", "mesher": mesher, "domain": domain},
        )
    snap = build_snappy_snapshot(session, settings)
    joined = "".join(
        text
        for surface in snap.surfaces
        if (text := snap.stl_files.get(f"{GEOMETRY_DIR}/{surface.file}"))
    )
    return dataclasses.replace(snap, mesher=mesher, stl_files={SURFACE_FILE: joined})


def build_snappy_snapshot(session: Session, settings: Node) -> Any:  # noqa: PLR0915 - one read, in order
    """Read the tree and the geometry store into a `SnappySnapshot`. Writes nothing.

    **The snappy path needs a different record from blockMesh's, and this is it.** `MeshSnapshot`
    describes six box sides; snappyHexMesh follows the part's own surfaces, so it needs one surface
    per boundary tag, a background box to grow cells in, and a point that is inside the region to
    keep - none of which a `MeshSnapshot` carries.

    The three things that make this more than a translation:

    * **One surface per boundary tag**, so snappy's patch names are the user's tag names. Faces in
      no tag are gathered into an `unassigned` surface of type `wall`, exactly as the blockMesh
      driver does, so the boundary file has an entry for them.
    * **A point inside the region**, found and verified by `stokes.geometry.inside` rather than
      guessed: snappy keeps the cells reachable from it, so a wrong point silently meshes the wrong
      region. A part with no interior point is refused in plain words.
    * **`domain` decides which side of the surface is the fluid.** `part` means the part IS the
      fluid (internal flow), so the point must be inside it; `enclosure` means the part is an
      obstacle in free space (external flow), so the point must be outside it and the box is grown
      by the margin.

    Returns a `SnappySnapshot`; typed `Any` because importing the driver here would pull the
    dictionary machinery into `import stokes.mesh.snapshot`, which the API budget cares about.
    """
    from stokes.geometry.inside import inside_point_or_coarse
    from stokes.geometry.store import GeometryStore
    from stokes.mesh.drivers.snappy import SNAP_CONTROLS, Feature, SnappySnapshot
    from stokes.model import registry

    project = session.require_project()
    own = _params(settings, registry)
    base_size = float(own["base_size"])
    margin = float(own["margin"])
    domain = str(own["domain"])
    max_cells = int(own["max_cells"])
    two = from_settings(own)
    if two is not None:
        _refuse_snappy_two_d(two, domain)

    # **A `box` domain has no part at all**, and snappy can still mesh one: the background block is
    # the whole mesh, with no surface to follow. My first version called `_part` unconditionally and
    # refused a perfectly valid box case with "domain is 'part' but no domain part is set" -
    # measured, on the very first click of Generate.
    if domain == "box":
        plain = _box_of(own["box"], "the box")
        if plain is None:
            raise MeshInvalid(
                "domain is 'box' but the box is empty; give six numbers",
                details={"code": "mesh.no_domain"},
            )
        grown = _grow(plain, margin)
        nx, ny, nz = _snappy_cells(grown, base_size, max_cells)
        centre = (
            (grown[0] + grown[3]) / 2,
            (grown[1] + grown[4]) / 2,
            (grown[2] + grown[5]) / 2,
        )
        return SnappySnapshot(
            cores=core_count(own["cores"]),
            surfaces=(),
            background_min=(grown[0], grown[1], grown[2]),
            background_max=(grown[3], grown[4], grown[5]),
            background_cells=(nx, ny, nz),
            inside_point=centre,
            base_size=base_size,
            max_cells=max_cells,
            max_non_ortho=float(own["max_non_ortho"]),
            max_skewness=float(own["max_skewness"]),
            settings_id=str(settings.id),
            domain_part=None,
            domain="box",
            snap_controls=SNAP_CONTROLS,
        )

    part_node = _part(project, own["domain_part"])
    part_bbox = _box_of(part_node.params.get("bbox"), f"part {part_node.name!r}")
    if part_bbox is None:
        raise MeshInvalid(
            f"the domain part {part_node.name!r} has no bounding box",
            details={"code": "mesh.invalid", "part": str(part_node.id)},
        )
    store = GeometryStore.of(session)
    part_basis = store.basis(part_node)
    mesh = store.mesh(str(part_node.id))
    if mesh is None:
        raise MeshInvalid(
            "the domain part has no display mesh yet; wait for its import",
            details={"code": "mesh.invalid", "part": str(part_node.id)},
        )
    positions, indices, face_ids = _arrays(mesh)
    if len(indices) == 0:
        raise MeshInvalid(
            f"the domain part {part_node.name!r} has no triangles to tessellate",
            details={"code": "mesh.invalid", "part": str(part_node.id)},
        )

    tag_faces, tag_kinds, _members, tag_digests = _tag_faces(
        project, part_node, len(face_ids), part_basis
    )
    two_d: dict[str, Any] = {}
    if two is not None:
        tag_faces, tag_kinds, two_d = _slab_surfaces(
            positions, indices, face_ids, tag_faces, tag_kinds, part_bbox, two, part_node.name
        )
    tag_ids = _tag_node_ids(project)
    # **The body's own mesh controls** (T-232..T-236): a local size, extra levels, and a layer
    # count its WALL tags inherit (empty = the settings', 0 = none, n = n). A project that sets
    # none of them produces the dictionary it always did.
    local = _body_mesh_settings(part_node)
    surfaces, surface_files, surface_areas, surface_targets = _sized_surfaces(
        project,
        part_node,
        positions,
        indices,
        face_ids,
        tag_faces,
        tag_kinds,
        base_size,
        own,
        local,
    )
    from stokes.mesh.layers import effective_layers, inherit_layers_for_unassigned

    layer_counts = effective_layers(project, part_node)
    if any(surface.name == "unassigned" for surface in surfaces):
        unassigned = inherit_layers_for_unassigned(project, part_node, unassigned_faces=True)
        if unassigned is not None:
            layer_counts["unassigned"] = unassigned
    stl_files, surface_triangles, surface_faces = _snappy_stl(
        positions, indices, face_ids, surfaces, surface_files, tag_faces
    )
    if two is not None:
        # **The slab's faces across the plane are not staged** (ADR-0031 decision 5): the
        # background block's own `front`/`back` faces bound the slab there, exactly planar, which
        # a snapped surface is not (measured: snapping left the front face ±0.1 mm out of plane,
        # and the extruded mesh had no 2D direction at all). They were claimed as surfaces only so
        # `unassigned` would not take them; now they are dropped.
        surfaces, surface_files, stl_files = _drop_slab_surfaces(surfaces, surface_files, stl_files)

    # **The background box.** `part` hugs the body (5 % breathing room so castellation has cells to
    # cut); `enclosure` is the wind tunnel, grown by the margin the user set.
    inside = domain != "enclosure"
    grown = _aligned_box(part_bbox, 0.05 if inside else (margin or 5.0), base_size)

    found = inside_point_or_coarse(positions, indices, grown, outside=not inside)
    point = found.point
    if point is None:
        where = "inside" if inside else "outside"
        raise MeshInvalid(
            f"could not find a point {where} {part_node.name!r} after {found.tested} tries; "
            "check that the part is a closed solid",
            details={
                "code": "mesh.no_inside_point",
                "part": str(part_node.id),
                "tested": found.tested,
                "domain": domain,
            },
        )

    nx, ny, nz = _snappy_cells(grown, base_size, max_cells)
    if two is not None:
        grown, point, cells = _slab_background(
            grown, point, (nx, ny, nz), part_bbox, two, base_size
        )
        nx, ny, nz = cells
    features = tuple(
        Feature(file=f"{name}.eMesh", level=level)
        for name, level in _snappy_features(surface_files)
    )
    return SnappySnapshot(
        cores=core_count(own["cores"]),
        surfaces=surfaces,
        features=features,
        background_min=(grown[0], grown[1], grown[2]),
        background_max=(grown[3], grown[4], grown[5]),
        background_cells=(nx, ny, nz),
        inside_point=point,
        base_size=base_size,
        max_cells=max_cells,
        max_non_ortho=float(own["max_non_ortho"]),
        max_skewness=float(own["max_skewness"]),
        stl_files=stl_files,
        settings_id=str(settings.id),
        domain_part=str(part_node.id),
        part_basis=part_basis,
        side_kinds={
            name: ("wall" if kind in ("wall", "symmetry") else "patch")
            for name, kind in tag_kinds.items()
        },
        tag_members={name: sorted(ids)[:64] for name, ids in tag_faces.items()},
        # **The per-body controls, in the record the basis hashes** (brief M-501).
        body_mesh={part_node.name: local} if local else {},
        # **The basis's `geometry` part** (brief M-501): the domain spelling and each tag's FULL
        # face digest, so a re-picked face moves the hash the 64-id cut cannot.
        domain=domain,
        tag_digests=tag_digests,
        # **The resolved layer table and the settings that shape it** (brief M-504): the writer
        # emits `addLayersControls` from these; the basis hashes them into the `layers` part
        # only when a count is above zero.
        layer_counts=layer_counts,
        layer_settings=_layer_settings(own),
        # **The sizing inputs the estimate re-resolves under** (brief M-503): per surface, the
        # boundary and body sizes the precedence used, so trying a new base or surface size
        # re-resolves every surface honestly instead of scaling the old targets.
        surface_size=float(own["surface_size"]),
        feature_angle=float(own["feature_angle"]),  # brief M-515: the sharp-edge threshold
        domain_bbox=part_bbox,
        surface_areas=surface_areas,
        surface_targets=surface_targets,
        snap_controls=SNAP_CONTROLS,
        surface_triangles=surface_triangles,
        surface_faces=surface_faces,
        two_d=two_d,
        # Tag name -> the boundary node that references it, which is what `derives_from` records.
        side_tags=tag_ids,
        # **No tag is "unmapped" for snappy**: blockMesh maps a tag only when its faces lie on a box
        # side, and a tag whose faces are inside the box is unreachable. snappy follows the surfaces
        # themselves, so every tagged face is meshed and the warning does not apply.
        unmapped=(),
    )


def _refuse_snappy_two_d(two: TwoD, domain: str) -> None:
    """What snappyHexMesh's 2D recipe does not cover yet (ADR-0031 decision 5), refused by name."""
    if two.axisymmetric:
        raise MeshInvalid(
            "axisymmetric 2D is built for blockMesh only; choose blockMesh, or planar 2D",
            details={"code": "mesh.invalid", "two_d": two.mode, "mesher": "snappy"},
        )
    if domain != "part":
        raise MeshInvalid(
            f"2D with snappyHexMesh meshes a slab part as the fluid (domain 'part'); "
            f"domain {domain!r} is not supported in 2D - use blockMesh for a box",
            details={"code": "mesh.invalid", "two_d": two.mode, "domain": domain},
        )


def _slab_surfaces(
    positions: Any,
    indices: Any,
    face_ids: Any,
    tag_faces: dict[str, set[int]],
    tag_kinds: dict[str, str],
    part_bbox: tuple[float, float, float, float, float, float],
    two: TwoD,
    part_name: str,
) -> tuple[dict[str, set[int]], dict[str, str], dict[str, Any]]:
    """The slab's two faces across the plane as surfaces `front` and `back` (ADR-0031).

    A CAD face is `front` when every vertex of its triangles lies on the part's high bounding plane
    along the normal, `back` on the low one. Those faces are **taken from any tag** that held them,
    because the extrusion and the `empty` patch are the mesher's, not the user's. Returns the tags
    and kinds with the two surfaces added, and the snapshot's 2D record with the extrusion's
    thickness resolved (the `thickness` setting, or the slab's own).
    """
    import numpy as np

    refuse_reserved(tag_faces)
    n = two.normal
    lo, hi = part_bbox[n], part_bbox[n + 3]
    tol = 1e-6 * _diagonal(part_bbox)
    pos = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    tri = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
    owner = np.asarray(face_ids, dtype=np.int64).reshape(-1)
    coord = pos[tri][:, :, n]  # (triangles, 3) normal coordinates
    on_hi = np.all(np.abs(coord - hi) <= tol, axis=1)
    on_lo = np.all(np.abs(coord - lo) <= tol, axis=1)
    faces = np.unique(owner)
    # A face is on a plane when ALL its triangles are.
    hi_count = np.bincount(owner[on_hi], minlength=int(faces.max()) + 1)
    lo_count = np.bincount(owner[on_lo], minlength=int(faces.max()) + 1)
    all_count = np.bincount(owner, minlength=int(faces.max()) + 1)
    front = {int(f) for f in faces if all_count[f] and hi_count[f] == all_count[f]}
    back = {int(f) for f in faces if all_count[f] and lo_count[f] == all_count[f]}
    if not front or not back:
        side = "high" if not front else "low"
        raise MeshInvalid(
            f"2D needs {part_name!r} to be a slab with a flat face on each side of the plane "
            f"{two.plane}; nothing lies on its {side} bounding plane across the plane",
            details={"code": "mesh.invalid", "part": part_name, "plane": two.plane},
        )
    slab = hi - lo
    thickness = two.thickness if two.thickness > 0 else slab
    owned = front | back
    tags = {name: set(ids) - owned for name, ids in tag_faces.items()}
    tags = {name: ids for name, ids in tags.items() if ids}
    kinds = {name: kind for name, kind in tag_kinds.items() if name in tags}
    from stokes.mesh.two_d import BACK, FRONT

    tags[FRONT] = front
    tags[BACK] = back
    kinds[FRONT] = "patch"
    kinds[BACK] = "patch"
    record = two.record()
    record["thickness"] = float(thickness)
    return tags, kinds, record


def _drop_slab_surfaces(
    surfaces: tuple[Any, ...], files: dict[str, str], stl: dict[str, str]
) -> tuple[tuple[Any, ...], dict[str, str], dict[str, str]]:
    """The surfaces, file names and staged STL without the slab's `front` and `back`."""
    from stokes.mesh.drivers.snappy import GEOMETRY_DIR
    from stokes.mesh.two_d import BACK, FRONT

    gone = {FRONT, BACK}
    kept = tuple(surface for surface in surfaces if surface.name not in gone)
    paths = {f"{GEOMETRY_DIR}/{files[name]}" for name in gone if name in files}
    return (
        kept,
        {name: file for name, file in files.items() if name not in gone},
        {path: text for path, text in stl.items() if path not in paths},
    )


def _slab_background(
    grown: tuple[float, float, float, float, float, float],
    point: tuple[float, float, float],
    cells: tuple[int, int, int],
    part_bbox: tuple[float, float, float, float, float, float],
    two: TwoD,
    base_size: float,
) -> tuple[
    tuple[float, float, float, float, float, float],
    tuple[float, float, float],
    tuple[int, int, int],
]:
    """The background box spanning EXACTLY the slab across the plane (ADR-0031 decision 5).

    As the OF14 wingMotion tutorial lays it out: the block's two faces across the plane are the
    slab's, so its `front`/`back` patches are the 2D planes and stay exactly flat through
    snapping. Cells across: the slab over the base size, at least one. The inside point moves to
    the slab's mid-plane, where it is certainly inside the block.
    """
    n = two.normal
    lo, hi = part_bbox[n], part_bbox[n + 3]
    box = list(grown)
    box[n], box[n + 3] = lo, hi
    where = list(point)
    where[n] = (lo + hi) / 2
    counts = list(cells)
    counts[n] = max(1, round((hi - lo) / base_size)) if base_size > 0 else 1
    return (
        (box[0], box[1], box[2], box[3], box[4], box[5]),
        (where[0], where[1], where[2]),
        (counts[0], counts[1], counts[2]),
    )


def _face_digest(ids: set[int], part_faces: int) -> str:
    """The sha1 of a tag's FULL sorted face-id list (brief M-501), ids past the part's dropped.

    **Full, not the 64-id cut**: `tag_members` is the result node's UI budget, and two tags that
    agree on their first 64 ids are different surfaces to the mesher — the digest must tell them
    apart when the cut cannot. Ids past the part's face count are dropped because the surface
    builder drops them too: a member naming a face the part no longer has must not change the hash
    of a mesh it cannot change.
    """
    text = ",".join(str(i) for i in sorted(ids) if 0 <= i < part_faces)
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def _tag_node_ids(project: Any) -> dict[str, str]:
    """Tag name -> its node id, for the result's `derives_from`.

    A mesh derives from the tags that describe it: editing one makes the mesh stale. The blockMesh
    snapshot carries the same relationship through `side_tags`; here it is by name, because snappy
    takes every tag's faces rather than the six that reach a box side.
    """
    return {str(node.name): str(node.id) for node in project.root.walk() if node.type == "tag"}


def _snappy_surfaces(
    positions: Any,
    indices: Any,
    face_ids: Any,
    tag_faces: dict[str, set[int]],
    tag_kinds: dict[str, str],
    base_size: float,
    local: dict[str, Any] | None = None,
    boundary_sizes: dict[str, float | None] | None = None,
    surface_size: float = 0.0,
) -> tuple[tuple[Any, ...], dict[str, str]]:
    """One `Surface` per boundary tag, plus `unassigned` for the faces no tag holds.

    **Returns the surfaces and the `{surface name: file name}` map** the STL writer staging needs:
    the dictionary's `file` entries and the files actually written have to come from one place, or
    a case can reference a surface that was never staged.

    Refinement levels come from the boundary's KIND, not from a single global setting: a `wall`
    benefits from a finer surface than a far-field `patch`, and this is the size-based route into
    the `level (min max)` pair that OF14 uses for curvature refinement (there is no
    `curvatureLevel` key - verified against the installed source).

    **The surface size follows `stokes.mesh.sizing`'s precedence** (brief M-503): boundary size >
    body size > `surface_size` (when > 0) > the rule. `boundary_sizes` is `{tag name: the
    boundary's stored size or None}` — read once by the builder, passed here, so the surfaces and
    the Sizing page's placeholders resolve through the same function.

    A face can belong to more than one tag when a user tags overlapping selections; it is given to
    the FIRST tag that claims it, in tag-name order, so the surfaces partition the part and snappy
    sees no duplicated face. The alternative - writing it twice - makes snappy complain about a
    non-manifold surface with a message that names no tag.
    """
    from stokes.mesh.drivers.snappy import Surface
    from stokes.mesh.levels import level_pair

    claimed: dict[int, str] = {}
    surfaces: list[Surface] = []
    files: dict[str, str] = {}
    # **The bound is the number of FACES, not the number of TRIANGLES.**
    #
    # `tag_faces` holds face ids (`0..n_faces-1`); `face_ids` is per TRIANGLE, so a quad is two
    # entries and `len(face_ids)` is larger than the face count. Comparing a face id against the
    # triangle count therefore left the ids above the face count looking unclaimed, and
    # `unassigned` was added for faces that were already tagged - so the dictionary declared a
    # surface whose STL was never written and `surfaceFeatures` died with
    # `Cannot read ".../unassigned.stl"` (measured: the demo's box, 6 faces, 12 triangles).
    import numpy as _np

    total = int(_np.unique(_np.asarray(face_ids)).size)
    for name in sorted(tag_faces):
        ids = sorted(i for i in tag_faces[name] if 0 <= i < total and i not in claimed)
        if not ids:
            continue
        for i in ids:
            claimed[i] = name
        kind = tag_kinds.get(name, "patch")
        # A symmetry BC uses the non-planar `symmetry` patch in snappy. blockMesh has planar
        # faces and uses `symmetryPlane`; layer eligibility is resolved separately and remains
        # wall-only.
        patch_type = {"wall": "wall", "symmetry": "symmetry"}.get(kind, "patch")
        # **The size precedence lives in `stokes.mesh.sizing`** (brief M-503): boundary size >
        # body size > `surface_size` (when > 0) > the rule. One function, two callers — this and
        # the placeholders — so the page cannot disagree with the dictionary. An untouched project
        # resolves exactly as it did (the golden test says so byte for byte).
        from stokes.mesh.sizing import surface_target

        target = surface_target(
            boundary_size=(boundary_sizes or {}).get(name),
            body_size=(local or {}).get("size"),
            surface_size=surface_size,
            base_size=base_size,
            patch_type=patch_type,
        )
        extra = int((local or {}).get("refinement") or 0)
        low, high = level_pair(base_size, target)
        # **`refinement` raises only the CURVATURE level**, not the minimum: `min` is what every
        # cell touching the surface gets, and raising it as well would refine a flat face for no
        # reason.
        high += extra
        file_name = f"{name}.stl"
        files[name] = file_name
        surfaces.append(
            Surface(
                name=name,
                file=file_name,
                region=name,
                patch_type=patch_type,
                level_min=low,
                level_max=high,
                # **The size the levels answer to** (brief M-501): the dry-run precheck reports
                # what was asked against what the levels achieve, and only the reader of the
                # sizes knows the rule that produced this target.
                requested=float(target),
            )
        )

    # **The faces in no tag still need a surface.** They are the part's untagged skin; without them
    # snappy would leave a hole where the mesh should follow the body, and the boundary file would
    # have no entry for those faces at all.
    leftover = [i for i in range(total) if i not in claimed]
    if leftover:
        name = "unassigned"
        file_name = f"{name}.stl"
        files[name] = file_name
        low, high = level_pair(base_size, base_size)
        surfaces.append(
            Surface(
                name=name,
                file=file_name,
                region=name,
                patch_type="wall",
                level_min=low,
                level_max=high,
                requested=float(base_size),
            )
        )
    return tuple(surfaces), files


def _snappy_stl(
    positions: Any,
    indices: Any,
    face_ids: Any,
    surfaces: tuple[Any, ...],
    files: dict[str, str],
    tag_faces: dict[str, set[int]],
) -> tuple[dict[str, str], dict[str, int], dict[str, int]]:
    """The staged ASCII STL text for each surface, keyed by path under `constant/geometry/`, with
    each surface's triangle count and its CAD face count.

    **One file per surface, holding only that surface's faces.** snappy makes a patch per staged
    surface, so a file holding another tag's triangles would merge two patches the user separated,
    and the fidelity measurement compares the mesh against THIS text - so it must be the same
    subset the dictionary refines.

    **The counts ride along** (brief M-501): the dry-run precheck reports each surface's size
    (triangles) and its patch's faces without building any STL text, and this loop is the one
    place both counts already exist.

    **Refused when too large for the agent to stage**, naming the size and the limit:
    `stokes.geometry.export.MAX_STAGED_BYTES`, 500 MiB — not the 8 MiB this docstring used to
    claim (a stale figure the [F] review flagged; the linter's staged-text limit is not the
    staging limit), and a failure there would otherwise reach the user as a generic staging error
    instead of "this part is too finely tessellated to mesh".
    """
    import numpy as np

    from stokes.geometry.export import MAX_STAGED_BYTES, Solid, ascii_stl_size, ascii_stl_text
    from stokes.mesh.drivers.snappy import GEOMETRY_DIR

    tri = np.asarray(indices).reshape(-1, 3)
    ids = np.asarray(face_ids).reshape(-1)
    # **Face ids, not triangle indices**: `np.unique` is the face set, and using
    # `range(len(ids))` here would offer the same unclaimed-id defect as `_snappy_surfaces` - a
    # directory of files that does not match the dictionary that names them.
    out: dict[str, str] = {}
    counts: dict[str, int] = {}
    face_counts: dict[str, int] = {}
    claimed: set[int] = set()
    all_faces = {int(i) for i in np.unique(ids)}
    for surface in surfaces:
        if surface.name == "unassigned":
            wanted = all_faces - claimed
        else:
            wanted = set(tag_faces.get(surface.name, set())) - claimed
        if not wanted:
            # **No faces means no file**, and a surface with no file must not be declared. The
            # caller builds the surface list and this dict from the same faces, so the two agree;
            # `_validate` refuses a mismatch rather than letting `surfaceFeatures` fail on the host.
            continue
        claimed |= wanted
        # `np.isin` rather than a Python loop: a flange can carry tens of thousands of faces and
        # `isin` is the whole reason numpy is on this path.
        rows = np.flatnonzero(np.isin(ids, np.fromiter(wanted, dtype=ids.dtype)))
        if rows.size == 0:
            continue
        corners = np.asarray(positions)[tri[rows]].astype("float32")
        size = ascii_stl_size(len(rows))
        if size > MAX_STAGED_BYTES:
            raise MeshInvalid(
                f"the surface for {surface.name!r} would be {size / 1048576:.1f} MiB of ASCII STL, "
                f"over the {MAX_STAGED_BYTES / 1048576:.1f} MiB the agent can stage; simplify the "
                "part or raise the mesh base size",
                details={
                    "code": "mesh.too_large_to_stage",
                    "surface": surface.name,
                    "bytes": size,
                    "limit": MAX_STAGED_BYTES,
                },
            )
        text = ascii_stl_text(Solid(name=surface.name, corners=corners, faces=len(rows)))
        out[f"{GEOMETRY_DIR}/{files[surface.name]}"] = text
        counts[surface.name] = int(rows.size)
        face_counts[surface.name] = len(wanted)
    return out, counts, face_counts


def _surface_areas(
    positions: Any,
    indices: Any,
    face_ids: Any,
    surfaces: tuple[Any, ...],
    tag_faces: dict[str, set[int]],
    local: dict[str, Any] | None,
    boundary_sizes: dict[str, float | None],
) -> tuple[dict[str, float], dict[str, tuple[float | None, float | None, str]]]:
    """Each surface's tessellated area in m², and the sizing inputs its target came from.

    **The same grouping the STL staging uses** — a surface's faces, claimed in the same order —
    so the estimate's area and the staged file cannot disagree about what the surface is. The
    area is the triangle cross-product sum, vectorised: a flange carries tens of thousands of
    triangles and a Python loop here would be the estimate's own budget.
    """
    import numpy as np

    tri = np.asarray(indices).reshape(-1, 3)
    ids = np.asarray(face_ids).reshape(-1)
    all_faces = {int(i) for i in np.unique(ids)}
    areas: dict[str, float] = {}
    targets: dict[str, tuple[float | None, float | None, str]] = {}
    claimed: set[int] = set()
    body_size = (local or {}).get("size")
    for surface in surfaces:
        if surface.name == "unassigned":
            wanted = all_faces - claimed
        else:
            wanted = set(tag_faces.get(surface.name, set())) - claimed
        if not wanted:
            continue
        claimed |= wanted
        rows = np.flatnonzero(np.isin(ids, np.fromiter(wanted, dtype=ids.dtype)))
        if rows.size == 0:
            continue
        corners = np.asarray(positions)[tri[rows]].astype("float64")
        a = corners[:, 1] - corners[:, 0]
        b = corners[:, 2] - corners[:, 0]
        areas[surface.name] = float(np.linalg.norm(np.cross(a, b), axis=1).sum() / 2.0)
        targets[surface.name] = (
            (boundary_sizes or {}).get(surface.name),
            body_size,
            surface.patch_type,
        )
    return areas, targets


def _snappy_cells(
    bbox: tuple[float, float, float, float, float, float], base: float, max_cells: int
) -> tuple[int, int, int]:
    """The background block's cell counts, reduced until the block fits the limit.

    **The block is the floor of the cell count, not the total**: castellation refines it upwards, so
    a background already at the cap has nowhere to go and the job would die on the host. Reducing
    the background here, before anything runs, is what turns an impossible request into a mesh that
    is merely coarser.
    """
    # The 1e-9 keeps an aligned box (`_aligned_box`, a whole number of cells) from rounding up one.
    counts = [max(1, math.ceil((bbox[i + 3] - bbox[i]) / base - 1e-9)) for i in range(3)]
    while counts[0] * counts[1] * counts[2] > max_cells and max(counts) > 1:
        longest = counts.index(max(counts))
        counts[longest] = max(1, int(counts[longest] * 0.8))
    return counts[0], counts[1], counts[2]


def _snappy_features(surface_files: dict[str, str]) -> list[tuple[str, int]]:
    """One feature-edge mesh per staged surface, at the surface's own max level.

    `surfaceFeatures` writes `<surface>.eMesh` beside the STL, and the snappy dictionary refines
    cells along it. Deriving the list from the staged files keeps the two in step: a surface that
    has no file cannot have a feature mesh.
    """
    return [(name, 2) for name in surface_files]


def build_snapshot(session: Session, settings: Node) -> MeshSnapshot:
    """Read the tree and the geometry store into a `MeshSnapshot`. Writes nothing.

    Runs on the calling (session) thread: it reads nodes and a mesh, and every part of it is
    bounded by
    the size of the model.
    """
    from stokes.geometry.store import GeometryStore
    from stokes.mesh.model import SETTINGS_PATH  # noqa: F401 - documents where settings live
    from stokes.model import registry

    project = session.require_project()
    own = _params(settings, registry)
    mesher = str(own["mesher"])
    base_size = float(own["base_size"])
    margin = float(own["margin"])
    domain = str(own["domain"])
    domain_part = own["domain_part"]
    two = from_settings(own)
    if two is not None:
        refuse_reserved(_tag_names(project))

    if domain == "box":
        bbox = _box_of(own["box"], "the box")
        if bbox is None:
            raise MeshInvalid(
                "domain is 'box' but the box is empty; give six numbers",
                details={"code": "mesh.no_domain"},
            )
        grown = _grow(bbox, margin)
        if two is not None:
            check_radius(grown, two, 1e-9 * _diagonal(grown))
            grown = with_thickness(grown, two)
        return MeshSnapshot(
            settings_id=str(settings.id),
            mesher=mesher,
            base_size=base_size,
            bbox=grown,
            two_d=two.record() if two is not None else {},
            sides={},
            side_tags={},
            side_kinds={},
            unmapped=(),
            max_cells=int(own["max_cells"]),
            cores=core_count(own["cores"]),
            max_non_ortho=float(own["max_non_ortho"]),
            max_skewness=float(own["max_skewness"]),
            domain_part=None,
            part_basis="",
            tag_members={},
            domain=domain,
        )

    part_node = _part(project, domain_part)
    part_bbox = _box_of(part_node.params.get("bbox"), f"part {part_node.name!r}")
    if part_bbox is None:
        raise MeshInvalid(
            f"the domain part {part_node.name!r} has no bounding box",
            details={"code": "mesh.invalid", "part": str(part_node.id)},
        )
    grown = _grow(part_bbox, margin)
    if two is not None:
        check_radius(grown, two, 1e-6 * _diagonal(grown))
    store = GeometryStore.of(session)
    part_basis = store.basis(part_node)

    # **With a margin the sides no longer touch the part**, so nothing can be mapped and every
    # tag is
    # unmapped. Saying so is better than mapping a side the margin moved away from.
    if margin != 0:
        names = tuple(sorted(_tag_names(project)))
        return MeshSnapshot(
            settings_id=str(settings.id),
            mesher=mesher,
            base_size=base_size,
            bbox=with_thickness(grown, two) if two is not None else grown,
            two_d=two.record() if two is not None else {},
            sides={},
            side_tags={},
            side_kinds={},
            unmapped=names,
            max_cells=int(own["max_cells"]),
            cores=core_count(own["cores"]),
            max_non_ortho=float(own["max_non_ortho"]),
            max_skewness=float(own["max_skewness"]),
            domain_part=str(part_node.id),
            part_basis=part_basis,
            tag_members={},
            domain=domain,
        )

    mesh = store.mesh(str(part_node.id))
    if mesh is None:
        raise MeshInvalid(
            "the domain part has no display mesh yet; wait for its import",
            details={"code": "mesh.invalid", "part": str(part_node.id)},
        )
    positions, indices, faces = _arrays(mesh)
    part_faces = int(faces.max()) + 1 if faces.size else 0
    tag_faces, tag_kinds, members, digests = _tag_faces(project, part_node, part_faces, part_basis)

    tol = 1e-6 * _diagonal(grown)
    sides, unmapped, conflicts = side_mapping(positions, indices, faces, tag_faces, grown, tol)
    if two is not None:
        sides, unmapped, conflicts = _two_d_sides(sides, unmapped, conflicts, two, grown, tol)
    if conflicts:
        # The first side in name order, so the message is the same on every run.
        side = sorted(conflicts)[0]
        holders = conflicts[side]
        raise MeshInvalid(
            f"the {side} side holds faces of {' and '.join(holders)}; "
            "blockMesh gives a side one patch",
            details={"code": "mesh.invalid", "side": side, "tags": holders},
        )
    side_tags = {side: _tag_id(project, name) for side, name in sides.items()}
    side_kinds = {side: tag_kinds.get(name, "") for side, name in sides.items()}
    return MeshSnapshot(
        settings_id=str(settings.id),
        mesher=mesher,
        base_size=base_size,
        bbox=with_thickness(grown, two) if two is not None else grown,
        sides=sides,
        side_tags=side_tags,
        side_kinds=side_kinds,
        unmapped=unmapped,
        max_cells=int(own["max_cells"]),
        cores=core_count(own["cores"]),
        max_non_ortho=float(own["max_non_ortho"]),
        max_skewness=float(own["max_skewness"]),
        domain_part=str(part_node.id),
        part_basis=part_basis,
        tag_members=members,
        two_d=two.record() if two is not None else {},
        shape=classify_part(session, part_node, part_basis),
        domain=domain,
        tag_digests=digests,
        side_faces={
            side: len(tag_faces[name]) for side, name in sides.items() if name in tag_faces
        },
    )


def classify_part(session: Session, part_node: Node, basis: str) -> Any:
    """What shape the part is, or None when the B-rep is not readable here (T-180).

    **None, not a guess.** The classification decides whether a box is a fair description of the
    part, so a fabricated answer would be worse than no answer: a driver that needs it refuses when
    it is absent, and one that does not (the box domain) ignores it.

    The reading is **delegated to `stokes.geometry.shape_report`**, because OCP may only be imported
    inside `stokes.geometry` (CLAUDE.md rule 5) - and `tools/check_forbidden_imports.py` refused my
    first version, which read the shape here. This function does the session-side work: find the
    project's cached B-rep, verify it against its sidecar, and scale the answer to SI.
    """
    try:
        from stokes.geometry.shape_report import classify
        from stokes.geometry.shapes import brep_path, read_entry, read_shape
        from stokes.geometry.store import GeometryStore

        folder = GeometryStore.of(session).shape_dir()
        source = brep_path(folder, str(part_node.id))
        if not source.is_file():
            return None
        # **The sidecar is what `read_shape` verifies the bytes against** - its signature takes the
        # entry, not a path alone, so a truncated or stale B-rep is refused rather than classified.
        # My first version passed None, which mypy caught as an incompatible argument.
        entry = read_entry(source)
        if entry is None:
            return None
        shape = read_shape(source, entry)
        if shape is None:
            return None
        report = classify(shape)
        # **Converted to SI, because everything else in the model is.** The B-rep is stored in the
        # unit the FILE declared - millimetres for STEP - so `classify` reports the model's own
        # numbers: a 5 mm bore comes back as radius 5.0. The snapshot's bbox beside it is in metres,
        # and a driver mixing the two would build a block structure a thousand times too large.
        # Measured: the cylinder test expected 0.005 and got 5.0.
        return _si(report, float(getattr(entry, "scale", 1.0)))
    except Exception as exc:  # Any: a classification is never worth failing a mesh for
        log.warning(
            "the part's shape could not be classified",
            extra=fields(part=str(part_node.id), error=str(exc)[:200]),
        )
        return None


def _si(report: Any, scale: float) -> Any:
    """A shape report with its lengths in metres. `scale` is metres per unit of the stored shape.

    **Only the lengths move.** A radius and a bbox are lengths; the axis is a direction and the face
    kinds are words, so scaling those would be wrong. `scale` is 1.0 for a shape already in metres,
    which is why this is a no-op for most parts rather than a conversion that always runs.
    """
    if not report or math.isclose(scale, 1.0, rel_tol=1e-12):
        return report
    from dataclasses import replace

    extra = dict(report.extra)
    if "bbox" in extra:
        extra["bbox"] = tuple(float(value) * scale for value in extra["bbox"])
    if "radii" in extra:
        extra["radii"] = [float(value) * scale for value in extra["radii"]]
    if "bores" in extra:
        extra["bores"] = [float(value) * scale for value in extra["bores"]]
    return replace(report, radius=float(report.radius) * scale, extra=extra)


def _params(settings: Node, registry: Any) -> dict[str, Any]:
    """The settings node's parameters, unwrapped from `QuantityValue` to plain floats.

    **A stored None reads as the spec's default** — a parameter with no default materialises
    nothing at node creation (the no-default convention), so a project saved before a
    parameter existed (the demo project predates M-515's `feature_angle`) carries None, and
    the snapshot builders' `float(own[...])` would crash. The writer's own validation still
    guards the numbers that reach the dictionary (the M-504 review's F2).
    """
    spec = registry.get("mesh_settings")
    out: dict[str, Any] = {}
    for param in spec.params:
        value = settings.params.get(param.name)
        unwrapped = getattr(value, "value", value)
        out[param.name] = param.default if unwrapped is None else unwrapped
    return out


def _layer_settings(own: dict[str, Any]) -> dict[str, Any]:
    """The settings' layer controls, as the writer and the basis read them (brief M-504).

    Read from the same unwrapped `_params` dict as everything else, so the quantities arrive as
    SI floats. **Only carried, never decided here**: whether any of it reaches the dictionary is
    the writer's rule (nothing does when no wall got a count).

    **A stored None means the setting was never set** — a project saved before the
    null-clear convention existed carries `expansion_ratio: null`, and `own.get(key, default)`
    returns that None (the key IS present), so the default never applies and `float(None)`
    crashed `mesh.generate` on the demo project (measured, Oct 21). A None reads as the
    default; the writer's own validation (the M-504 review's F2) still guards the numbers that
    reach the dictionary.
    """

    def _or(key: str, default: float) -> float:
        value = own.get(key)
        return float(value) if value is not None else default

    return {
        "mode": str(own.get("layer_mode") or "final_ratio"),
        "expansion_ratio": _or("expansion_ratio", 1.2),
        "final_ratio": _or("final_ratio", 0.3),
        "first_layer_height": _or("first_layer_height", 0.0),
        "min_thickness_ratio": _or("min_thickness_ratio", 0.1),
        "feature_angle": _or("layer_feature_angle", 100.0),
    }


def _box_of(value: Any, what: str) -> tuple[float, float, float, float, float, float] | None:
    """Six numbers as a bbox tuple, or None when the value is empty."""
    if not value:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise MeshInvalid(
            f"{what} needs six numbers (xmin ymin zmin xmax ymax zmax); got {value!r}",
            details={"code": "mesh.invalid", "value": str(value)},
        )
    return (
        float(value[0]),
        float(value[1]),
        float(value[2]),
        float(value[3]),
        float(value[4]),
        float(value[5]),
    )


def _aligned_box(
    bbox: tuple[float, ...], pad: float, base: float
) -> tuple[float, float, float, float, float, float]:
    """The bbox grown by at least ``pad``, on a grid of exactly ``base`` anchored at the part.

    **The part's own bounding planes fall on cell faces.** The box used to be the bbox plus the pad,
    divided into ``ceil(length / base)`` cells, so the spacing was only near the base size and the
    grid lines landed anywhere. The elbow's symmetry plane (z = 0, the bbox's own face) fell a third
    of the way into a cell, and snapping left it bumpy by ±2 mm: the plane then acted as an
    obstacle, and the velocity on it dipped at the pipe's axis. A plane at a cell face is snapped
    flat. The pad is rounded up to whole cells, so the box is never smaller than it was.
    """
    if not base > 0:
        return _grow(bbox, pad)
    lows, highs = [], []
    steps = math.ceil(pad / base - 1e-9)
    for axis in range(3):
        low = bbox[axis] - steps * base
        cells = max(1, math.ceil((bbox[axis + 3] + pad - low) / base - 1e-9))
        lows.append(low)
        highs.append(low + cells * base)
    return (lows[0], lows[1], lows[2], highs[0], highs[1], highs[2])


def _grow(
    bbox: tuple[float, ...], margin: float
) -> tuple[float, float, float, float, float, float]:
    """The bbox with ``margin`` added on every side."""
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    return (
        xmin - margin,
        ymin - margin,
        zmin - margin,
        xmax + margin,
        ymax + margin,
        zmax + margin,
    )


def _diagonal(bbox: tuple[float, ...]) -> float:
    """The box's diagonal, for a tolerance that scales with the model rather than a fixed number."""
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    return float(((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) ** 0.5) or 1.0


def _part(project: Any, domain_part: Any) -> Node:
    """The domain part node, or a refusal naming what is wrong with the setting."""
    if not domain_part:
        raise MeshInvalid(
            "domain is 'part' but no domain part is set",
            details={"code": "mesh.no_domain"},
        )
    # A `ref` is stored as an id or a path, and `find` takes either.
    node = project.find(str(domain_part))
    if node is None or getattr(node, "type", "") != "part":
        raise MeshInvalid(
            f"the domain part {domain_part!r} is not a part in this project",
            details={"code": "mesh.invalid", "part": str(domain_part)},
        )
    # **A body left out of the simulation cannot be the one a mesh is built on** (T-221). An
    # explicit refusal rather than a silent fallback to another part: meshing a body the user
    # excluded would produce a mesh they did not ask for, and silently picking a different one
    # would be worse - the mesh would look right and describe something else.
    if node.params.get("simulate", True) is False:
        raise MeshPartExcluded(
            f"{node.name!r} is left out of the simulation, so it cannot be the part this mesh is "
            "built on; use it in the simulation again, or choose another domain part",
            details={"part": str(node.id), "path": getattr(node, "path", "")},
        )
    return node  # type: ignore[no-any-return]  # `find` returns the model's own Node


def _tag_names(project: Any) -> list[str]:
    """Every tag node's name that could be a boundary patch.

    **A region's whole-part tag is not one** (Opus review item 15). It was listed, so `unmapped`
    reported `fluid` as a side the margin left unassigned — a side that does not exist, since the
    region tag describes cells. The reader of `unmapped` is deciding which patches to look at, and a
    name that can never be one is noise at best.

    The test is the same `_is_region_membership` the side mapping uses, so the two cannot disagree
    about what a patch is.
    """
    out: list[str] = []
    for node in project.root.walk():
        if node.type != "tag":
            continue
        if _is_region_membership(project, str(node.id)):
            continue
        out.append(str(node.name))
    return out


def _tag_id(project: Any, name: str) -> str:
    for node in project.root.walk():
        if node.type == "tag" and str(node.name) == name:
            return str(node.id)
    return ""


def _arrays(mesh: Any) -> tuple[Any, Any, Any]:
    """The mesh's vertices, triangles and per-triangle face ids, concatenated over its solids."""
    import numpy as np

    positions: list[Any] = []
    indices: list[Any] = []
    faces: list[Any] = []
    offset = 0
    for solid in mesh.solids:
        pos = np.asarray(solid.positions)
        idx = np.asarray(solid.indices)
        positions.append(pos)
        indices.append(idx + offset)
        faces.append(np.asarray(solid.face_ids))
        offset += len(pos)
    if not positions:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype="uint32"), np.zeros(0, dtype="uint32")
    return (
        np.concatenate(positions),
        np.concatenate(indices),
        np.concatenate(faces),
    )


SizedSurfaces = tuple[
    tuple[Any, ...],
    dict[str, str],
    dict[str, float],
    dict[str, tuple[float | None, float | None, str]],
]
"""What `_sized_surfaces` returns: the surfaces, their files, areas and precedence inputs."""


def _sized_surfaces(
    project: Any,
    part_node: Any,
    positions: Any,
    indices: Any,
    face_ids: Any,
    tag_faces: dict[str, set[int]],
    tag_kinds: dict[str, str],
    base_size: float,
    own: dict[str, Any],
    local: dict[str, Any],
) -> SizedSurfaces:
    """The surfaces with their sizing read (brief M-503): sizes, refusal, areas and targets.

    One helper so the builder stays under the statement budget and the sizing half — boundary
    sizes, the negative refusal, the surfaces, the areas and the precedence inputs the estimate
    re-resolves under — is one line to call and one place to read.
    """
    # **A stored size arrives as a quantity in SI; the parameter has no default**, so absence is
    # None. A NEGATIVE size would silently mean "do not refine" through the level rule - the
    # model has no range hook on a quantity, so the snapshot is where it is refused, naming the
    # boundary.
    boundary_sizes = _boundary_sizes(project, part_node)
    _refuse_negative_sizes(part_node, local, boundary_sizes)
    surfaces, surface_files = _snappy_surfaces(
        positions,
        indices,
        face_ids,
        tag_faces,
        tag_kinds,
        base_size,
        local,
        boundary_sizes,
        surface_size=float(own["surface_size"]),
    )
    # **The estimate's inputs ride the same pass** (brief M-503): each surface's area (the
    # tessellation is already grouped here) and the sizing inputs its target was resolved from.
    surface_areas, surface_targets = _surface_areas(
        positions, indices, face_ids, surfaces, tag_faces, local, boundary_sizes
    )
    return surfaces, surface_files, surface_areas, surface_targets


def _stored_size(node: Any) -> float | None:
    """A node's stored ``mesh_size`` as SI metres, or None for the empty = inherit state.

    A quantity parameter with no default **materialises a zero** at node creation — the same
    "zero means not set" convention ``_body_mesh_settings`` records for the part's size — so a
    stored 0 reads as unset here too. Absent and null are unset for the same reason. A NEGATIVE
    value is returned as it is: the snapshot refuses it by name, rather than quietly treating a
    wrong length as no decision.
    """
    stored = getattr(node.params.get("mesh_size"), "value", node.params.get("mesh_size"))
    if isinstance(stored, bool) or not isinstance(stored, (int, float)):
        return None
    value = float(stored)
    return None if value == 0.0 else value


def _boundary_sizes(project: Any, part_node: Any) -> dict[str, float | None]:
    """Each tag's boundary-level size on ``part_node``: ``{tag name: size or None}``.

    The boundary node referencing the tag carries it; a tag with no boundary, or one whose
    boundary has no stored size, maps to None (inherit). Read once per snapshot build and passed
    to ``_snappy_surfaces``, so the surfaces and the page resolve through the same function.
    """
    from stokes.mesh.layers import _boundary_of

    sizes: dict[str, float | None] = {}
    for node in project.root.walk():
        if node.type != "tag":
            continue
        boundary = _boundary_of(project, str(node.id))
        sizes[str(node.name)] = _stored_size(boundary) if boundary is not None else None
    return sizes


def _refuse_negative_sizes(
    part_node: Any, local: dict[str, Any], boundary_sizes: dict[str, float | None]
) -> None:
    """A negative size is refused, naming what carries it (brief M-503).

    The model has no range hook on a quantity, so the snapshot is where a stored negative meets
    its refusal — the same place ``_positive`` guards ``mesh.configure``'s values, one level
    closer to the dictionary. A negative would otherwise ride the level rule to "do not refine",
    which is a wrong mesh with no error anywhere.
    """
    from stokes.mesh.errors import MeshInvalid

    if local.get("size") is not None and local["size"] < 0:
        raise MeshInvalid(
            f"the mesh size of the part {part_node.name!r} must be a positive length; "
            f"got {local['size']}",
            details={"code": "mesh.invalid", "part": str(part_node.id)},
        )
    for name, size in boundary_sizes.items():
        if size is not None and size < 0:
            raise MeshInvalid(
                f"the mesh size of the boundary {name!r} must be a positive length; got {size}",
                details={"code": "mesh.invalid", "boundary": name},
            )


def _body_mesh_settings(part_node: Any) -> dict[str, Any]:
    """The per-body mesh controls of ``part_node``, with "inherit" read as absent.

    **Returns only what the user set**, so every branch downstream keeps one shape: a key that is
    present is a decision, and a key that is absent is the mesh settings'. That is what makes the
    byte-for-byte guarantee easy to reason about - a project with none of the three produces an
    empty dict here, and every caller's default is the old behaviour.

    **`layers` keeps a stored `0`**, unlike the other two: for a layer count `0` is a meaning
    (NONE on this body's walls, brief M-504), and the EMPTY state is the parameter being null or
    absent - which is why the two read differently here. A `0` stored under the withdrawn
    2026-10-18 parameter (its default 0 meant INHERIT) never reaches this function as a
    decision: the persist v2->v3 migration drops it at load.
    """
    params = part_node.params if part_node is not None else {}
    out: dict[str, Any] = {}
    size = params.get("mesh_size")
    # A quantity in SI; zero means "not set", which is what the parameter's empty default stores.
    value = getattr(size, "value", size)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        out["size"] = float(value)
    levels = params.get("refinement")
    if isinstance(levels, int) and not isinstance(levels, bool) and levels > 0:
        out["refinement"] = levels
    layers = params.get("layers")
    if isinstance(layers, int) and not isinstance(layers, bool):
        out["layers"] = layers
    return out


def _tag_faces(
    project: Any, part_node: Node, part_faces: int, part_basis: str
) -> tuple[dict[str, set[int]], dict[str, str], dict[str, Any], dict[str, str]]:
    """Each tag's face ids on the domain part, the kind of the boundary that references it, the
    members JSON for the basis, and the per-tag face digest for it.

    **A stale member raises.** A tag picked on an older B-rep names faces that no longer exist,
    and the
    face ids it holds would map to whichever faces now carry those numbers — a mesh whose inlet is
    somewhere else entirely, with no error anywhere. Refusing names the tag instead.
    """
    from stokes.api.commands.setup_plan import stored_members

    tag_faces: dict[str, set[int]] = {}
    tag_kinds: dict[str, str] = {}
    members: dict[str, Any] = {}
    digests: dict[str, str] = {}
    for node in project.root.walk():
        if node.type != "tag":
            continue
        name = str(node.name)
        # **A region holds whole bodies; that is CELL-ZONE membership, not a boundary patch.**
        # `region.assign` gives its tag every face of the part (`whole_bodies`), which made it
        # compete
        # with the real boundary tags for a block side - so the GUI's own order (define the fluid
        # region, then press Mesh) failed with "the xmax side holds faces of fluid and outlet".
        # A region tag names which cells are fluid; it never describes a patch, so it is left out of
        # the side mapping entirely. Boundaries and walls are unaffected.
        if _is_region_membership(project, str(node.id)):
            continue
        ids: set[int] = set()
        stale = False
        for member in stored_members(node):
            if str(getattr(member, "part", "")) != str(part_node.id):
                continue
            if getattr(member, "whole", False):
                ids.update(range(part_faces))
            else:
                ids.update(int(i) for i in member.ids(part_faces))
            basis = getattr(member, "basis", None)
            if basis and part_basis and basis != part_basis:
                stale = True
        if stale:
            raise MeshInvalid(
                f"the tag {name!r} was picked on an older version of the part; run tag.rematch",
                details={"code": "mesh.stale_tag", "tag": name},
            )
        tag_kinds[name] = _kind_of(project, str(node.id))
        if ids:
            tag_faces[name] = ids
            members[str(node.id)] = _members_json(node)
            digests[name] = _face_digest(ids, part_faces)
    return tag_faces, tag_kinds, members, digests


def _kind_of(project: Any, tag_id: str) -> str:
    """The `kind` of the boundary node that references this tag, or ``""``.

    The patch TYPE depends on this: `wall` for a wall kind, `symmetryPlane` for symmetry, and
    `patch` for everything else. A tag no boundary uses is a patch. (A side no TAG holds is not
    this function's business: the driver makes it the `unassigned` wall.)
    """
    for node, param in project.referrers(tag_id):
        if getattr(node, "type", "") == "boundary" and param == "tag":
            return str(node.params.get("kind", ""))
    return ""


def _members_json(node: Node) -> Any:
    """The tag's stored members, as JSON, for the basis."""
    try:
        return node.params.get("members")
    except Exception:
        return None


def _is_region_membership(project: Any, tag_id: str) -> bool:
    """Is this tag cell membership ONLY - a region holds it and no boundary does?

    **Structural, not a name or a kind string.** `region.assign` creates the tag with
    whole-body members, and a `region` node is what points at it; a boundary node is the other
    kind of referrer. The referrer's node type is what distinguishes them without depending on
    either side's spelling - a user is free to name a region `walls`.

    **A boundary that ALSO uses the tag wins, and that is the whole of Opus item 15.** This
    asked only "does a region hold it?", so a tag held by both was filtered out - and its side
    fell to the unassigned wall, **so an inlet could vanish with no error at all**. Measured:
    pointing a region at an existing boundary's tag (which `region.assign` accepts through
    `tag_node_id`, and a hand-edited or older file can hold) gave
    `[('region', 'tag'), ('boundary', 'tag')]` and the tag was skipped.

    A tag a boundary uses IS a boundary patch, whatever else holds it. Only a tag no boundary points
    at is pure cell membership.
    """
    held_by_region = False
    for node, param in project.referrers(tag_id):
        if param != "tag":
            continue
        kind = getattr(node, "type", "")
        if kind == "boundary":
            # **The stronger claim wins immediately.** A boundary describes patches; a region
            # describes cells, and naming a tag from both means the boundary's meaning is wanted.
            return False
        if kind == "region":
            held_by_region = True
    return held_by_region
