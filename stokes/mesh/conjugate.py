"""The multi-region mesh snapshot (ADR-0042): several bodies, one snappyHexMesh run, then a split.

A project whose regions hold more than one simulated body is conjugate: fluids and the solids
between them. Every body is meshed in one run. This module decides, per triangle of every body,
whether it is an outer boundary (it bounds the mesh, and becomes a patch) or an interface with
another region's body (it becomes a zone surface inside the mesh), and records what the driver
needs to write the zones, rebuild each fluid from its openings and split the mesh into regions.

**Headless and numpy only**, like the rest of `stokes.mesh`: the bodies are read as the triangles
the geometry store already holds, and "inside another body" is `stokes.geometry.inside.Columns`.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from stokes.mesh.errors import MeshInvalid

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.api.session import Session
    from stokes.model.nodes import Node
    from stokes.model.project import Project

#: How far outside a triangle the test point sits, as a fraction of the base cell: far enough to be
#: clear of two bodies' surfaces not quite coinciding, near enough to stay inside a wall the mesh
#: can hold at all.
OFFSET_FRACTION = 0.25

#: How many rings of interface triangles the outer skin takes along each of its rims (`collar`).
COLLAR_RINGS = 2

#: Two corners are the same vertex when they agree to this, in metres.
WELD = 1e-9

#: Two bodies' triangles are one wall when their centres agree to this fraction of the base cell:
#: far finer than any triangle the mesh would resolve, far coarser than the float noise between two
#: bodies cut from one field (measured: 0.015 um median on the mini plate exchanger).
TOUCH_FRACTION = 1e-3

#: A boundary triangle lies on a zone surface when its centre is this fraction of the base cell
#: from a zone triangle's plane (and inside it, and near parallel; `_off_zones`): ten times the
#: float noise of two bodies cut from one field at a 0.5 mm cell, far under any wall the mesh holds.
ON_SURFACE_FRACTION = 0.02

#: Fewer triangles than this facing another body, or a smaller share of a body's interface, is
#: stray: a wall two bodies share is thousands.
MIN_SHARED = 8
STRAY_SHARE = 0.002

#: The most seed points one fluid region is rebuilt from (`regionToCell`'s `insidePoints`).
MAX_SEEDS = 48

#: The boundary kinds that are not openings: a fluid is the cells connected to the others.
CLOSED_KINDS = ("wall", "symmetry", "")

_WORD = re.compile(r"[^A-Za-z0-9_]")


def region_word(name: str) -> str:
    """A region's name as an OpenFOAM word: the folder, the cell zone and the interface names."""
    word = _WORD.sub("_", name.strip()) or "region"
    return word if word[0].isalpha() else f"r_{word}"


def region_bodies(project: Any) -> list[tuple[Any, str, list[Any], str]]:
    """`(region node, kind, [part node], name)` for every simulated body a region holds, fluids
    first and each kind in tree order, so the last entry is the last solid.

    **A mesh region is a body, not a region node.** A region node holds the bodies of one
    material, and two channels of the same water are two streams with their own inlets, so each
    body is solved as its own region and takes its material, kind and boundaries from the node
    that holds it. The name is the node's when it holds one body and the body's when it holds
    several, made a word and made unique.
    """
    from stokes.mesh.tasks import _region_parts

    held: list[tuple[Any, str, list[Any]]] = []
    for node in project.root.walk():
        if node.type != "region":
            continue
        tag = project.find(str(node.params.get("tag") or ""))
        if tag is None:
            continue
        parts = []
        for part_id in sorted(_region_parts(tag)):
            part = project.find(part_id)
            if part is None or part.type != "part":
                continue
            if part.params.get("simulate", True) is False:
                continue
            parts.append(part)
        if parts:
            held.append((node, str(node.params.get("kind") or "fluid"), parts))
    ordered = [r for r in held if r[1] != "solid"] + [r for r in held if r[1] == "solid"]
    found: list[tuple[Any, str, list[Any], str]] = []
    taken: set[str] = set()
    for node, kind, parts in ordered:
        for part in parts:
            word = region_word(str(node.name if len(parts) == 1 else part.name))
            name, count = word, 1
            while name in taken:
                count += 1
                name = f"{word}_{count}"
            taken.add(name)
            found.append((node, kind, [part], name))
    return found


def is_conjugate(project: Any) -> bool:
    """Whether the project's regions hold more than one body. A multi-region mesh needs this and
    more (:func:`meshes_regions`)."""
    return len(region_bodies(project)) > 1


def meshes_regions(mesher: str, domain: str, project: Any) -> bool:
    """**The one rule for a multi-region mesh, and so a multi-region (conjugate) solve**: the
    mesher is snappyHexMesh, the domain is the part, and the regions hold more than one body. The
    other meshers, and a box domain, mesh one region whatever the regions hold.
    ``stokes.mesh.snapshot.snapshot_for`` builds the conjugate snapshot on it, and what reads
    "is this case conjugate" before a mesh exists (:func:`is_multi_region`) asks the same."""
    return mesher == "snappy" and domain == "part" and is_conjugate(project)


def is_multi_region(project: Any) -> bool:
    """:func:`meshes_regions` with the project's mesh settings (their defaults when the project
    has no settings node or a value is unset)."""
    from stokes.mesh.model import SETTINGS_PATH
    from stokes.model import registry

    spec = registry.get("mesh_settings")
    settings = project.find(SETTINGS_PATH)

    def choice(name: str) -> str:
        value = settings.params.get(name) if settings is not None else None
        return str(spec.param(name).default if value is None else value)

    return meshes_regions(choice("mesher"), choice("domain"), project)


def columns_of(bodies: list[tuple[Any, Any]], base_size: float) -> list[Any]:
    """Each body as the heights it crosses on lines a sixth of a cell apart: an inside test good
    to 0.12 of a cell, for probes that sit a quarter of a cell off a surface."""
    from stokes.geometry.inside import Columns

    return [Columns(positions, triangles, base_size / 6.0) for positions, triangles in bodies]


def classify(
    bodies: list[tuple[Any, Any]], rows: list[Any], base_size: float, columns: list[Any]
) -> list[tuple[Any, Any]]:
    """For each body, which of its triangles in ``rows`` face another body, and which body.

    ``bodies`` is `(positions, triangles)` per body and ``rows`` the triangle rows to classify per
    body. Returns, per body, a boolean array over its rows (an interface) and an integer array
    with the index of the neighbouring body (-1 for an outer triangle). ``columns`` is
    :func:`columns_of` the same bodies.

    Three tests, in order (2026-11-24; the first two were added after the mini plate exchanger
    measured most of each fluid's wall as outer skin, so uncoupled):

    1. **A triangle another body repeats is an interface.** Bodies cut from one implicit field
       share their walls triangle for triangle: a centre within `TOUCH_FRACTION` of a cell of
       another body's triangle centre says so whatever the normals.
    2. **The probe goes out of the body.** The display mesh has triangles wound the wrong way;
       their probe landed inside their own body, so in no other, and they were taken for outer
       skin. Where the probe is inside the body and the opposite one is not, the opposite one
       is the outside.
    3. **The probe is inside another body**, as before.

    Then **small islands of outer skin inside an interface are interface too** (`_islands`).
    """
    import numpy as np

    centres = [
        np.asarray(positions, dtype="float64")[np.asarray(triangles)].mean(axis=1)
        for positions, triangles in bodies
    ]
    out: list[tuple[Any, Any]] = []
    for index, (positions, triangles) in enumerate(bodies):
        chosen = np.asarray(rows[index], dtype="int64")
        corners = np.asarray(positions, dtype="float64")[np.asarray(triangles)[chosen]]
        normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        length = np.linalg.norm(normal, axis=1)
        normal = normal / np.where(length > 0.0, length, 1.0)[:, None]
        centre = corners.mean(axis=1)
        neighbour = np.full(len(chosen), -1, dtype="int64")
        for other in range(len(bodies)):
            free = np.flatnonzero(neighbour < 0)
            if other == index or free.size == 0:
                continue
            same = coincident(centre[free], centres[other], TOUCH_FRACTION * base_size)
            neighbour[free[same]] = other
        free = np.flatnonzero(neighbour < 0)
        probe = centre[free] + OFFSET_FRACTION * base_size * normal[free]
        backward = centre[free] - OFFSET_FRACTION * base_size * normal[free]
        if free.size:
            flipped = columns[index].inside(probe) & ~columns[index].inside(backward)
            probe[flipped] = backward[flipped]
        for other in range(len(bodies)):
            if other == index:
                continue
            open_ = np.flatnonzero(neighbour[free] < 0)
            if open_.size == 0:
                break
            hit = columns[other].inside(probe[open_])
            neighbour[free[open_[hit]]] = other
        _islands(positions, triangles, chosen, neighbour, base_size)
        out.append((neighbour >= 0, neighbour))
    return out


def coincident(points: Any, reference: Any, tolerance: float) -> Any:
    """Which of ``points`` lie within ``tolerance`` (on every axis) of one of ``reference``.

    numpy only: both sets are binned on a grid four tolerances wide; a point is looked up in
    the bins its tolerance box touches (at most eight), so no pair closer than the tolerance is
    missed, and a match is never further apart than two bins.
    """
    import itertools

    import numpy as np

    points = np.asarray(points, dtype="float64").reshape(-1, 3)
    reference = np.asarray(reference, dtype="float64").reshape(-1, 3)
    found = np.zeros(len(points), dtype=bool)
    if len(points) == 0 or len(reference) == 0:
        return found
    low = np.minimum(points.min(axis=0), reference.min(axis=0))
    high = np.maximum(points.max(axis=0), reference.max(axis=0))
    bins = 1 << 20
    # The bin is four tolerances, widened when the model is so large the keys would overflow.
    width = max(4.0 * tolerance, float((high - low).max()) / (bins - 4))
    reach = width / 4.0
    origin = low - 2.0 * width

    def key(at: Any) -> Any:
        cell = np.floor((at - origin) / width).astype("int64")
        return (cell[:, 0] << 42) | (cell[:, 1] << 21) | cell[:, 2]

    known = np.unique(key(reference))
    for signs in itertools.product((-1.0, 1.0), repeat=3):
        wanted = key(points + reach * np.asarray(signs))
        at = np.minimum(np.searchsorted(known, wanted), len(known) - 1)
        found |= known[at] == wanted
    return found


#: An island of outer skin with no opening in it, smaller than this many base cells squared and
#: bordered by an interface, is a mistake of the probe: an interface.
ISLAND_CELLS = 4.0


def _islands(positions: Any, triangles: Any, rows: Any, neighbour: Any, base_size: float) -> None:
    """Make the small islands of outer triangles in an interface interface triangles, in place.

    Measured on the mini plate exchanger (2026-11-24): the probe alone, with the winding
    corrected, left 114 hot, 157 cold and 401 solid islands of a few triangles each on the
    shared walls (the largest 4 mm2, at the flat ends of the gyroid core), against one outer
    skin of 2,190 mm2 on the solid and one per port of 63 mm2 on each fluid. Each island seeded
    a collar (`collar`). An island is a piece of the outer triangles, joined by shared corners,
    that holds no tagged triangle (``rows`` are the untagged ones), is smaller than
    `ISLAND_CELLS` base cells squared, and touches the interface; it takes the body one of the
    interface triangles around it faces.
    """
    import numpy as np

    from stokes.geometry.surface_repair import _components

    outer = np.flatnonzero(neighbour < 0)
    if outer.size == 0 or outer.size == len(rows):
        return
    corners = np.round(np.asarray(positions, dtype="float64")[triangles] / WELD).astype("int64")
    _, vertex = np.unique(corners.reshape(-1, 3), axis=0, return_inverse=True)
    vertex = vertex.reshape(-1, 3)
    count = int(vertex.max()) + 1
    # A tagged triangle is skin with an opening in it: the islands joined to one are kept.
    tagged = np.ones(len(triangles), dtype=bool)
    tagged[rows] = False
    skin = np.concatenate([np.asarray(rows)[outer], np.flatnonzero(tagged)])
    label = _components(vertex[skin], count)
    pieces = int(label.max()) + 1
    wide = np.asarray(positions, dtype="float64")[triangles[skin]]
    area = 0.5 * np.linalg.norm(np.cross(wide[:, 1] - wide[:, 0], wide[:, 2] - wide[:, 0]), axis=1)
    sizes = np.bincount(label, weights=area, minlength=pieces)
    opened = np.zeros(pieces, dtype=bool)
    opened[label[outer.size :]] = True
    small = (sizes < ISLAND_CELLS * base_size * base_size) & ~opened
    # The body each corner's interface triangles face (the largest index, any will do).
    faces = np.full(count, -1, dtype="int64")
    inner = np.flatnonzero(neighbour >= 0)
    np.maximum.at(faces, vertex[np.asarray(rows)[inner]].ravel(), np.repeat(neighbour[inner], 3))
    touching = faces[vertex[np.asarray(rows)[outer]]].max(axis=1)
    island = label[: outer.size]
    choice = np.full(pieces, -1, dtype="int64")
    np.maximum.at(choice, island, touching)
    flip = small[island] & (choice[island] >= 0)
    neighbour[outer[flip]] = choice[island[flip]]


def collar(
    positions: Any, triangles: Any, rows: Any, is_interface: Any, largest: float | None = None
) -> Any:
    """Which of a body's interface triangles lie along a rim of its outer skin, as a mask over
    ``rows``: those within `COLLAR_RINGS` triangles of a triangle that is not an interface, and
    no longer on any edge than ``largest`` when it is given.

    **Why the outer skin needs them.** Where a channel mouth meets the wall, each body's mesh has
    thin triangles along the edge whose normal is between the mouth's and the wall's; pushed off
    the surface they test as inside the neighbour, so both bodies give them to the interface and
    neither to the skin, and the skin has a hairline gap along every rim. snappyHexMesh keeps the
    cells it can reach from the inside point, and it reached the whole background box through
    those gaps (measured: a `background` patch of 6,136 faces on a 530 k-cell mesh). With the
    collar, each body's skin runs a little way down the shared wall, the two collars lie on each
    other, and there is no way through. The collar's faces become `unassigned` wall faces, and
    are left out of the body's zone surface (one surface per face, `_off_zones`): a strip a
    fraction of a millimetre wide where the wall is adiabatic instead of coupled.

    **The collar grows only from the real rims** (2026-11-24): on the mini plate exchanger the
    probe had left a few thousand islands of "outer" triangles on every shared wall, each grew a
    collar, and 68 % of the hot wall's interface triangles became a collar. `classify` now
    removes those islands; the collar is then 0.6 % of each wall (measured).

    Triangles are neighbours when they share a corner by position: the display mesh repeats its
    vertices at every face edge, so the indices alone do not say.
    """
    import numpy as np

    corners = np.round(np.asarray(positions, dtype="float64")[triangles] / WELD).astype("int64")
    _, vertex = np.unique(corners.reshape(-1, 3), axis=0, return_inverse=True)
    vertex = vertex.reshape(-1, 3)
    skin = np.ones(len(triangles), dtype=bool)
    skin[np.asarray(rows)[is_interface]] = False
    count = int(vertex.max()) + 1 if vertex.size else 0
    for _ in range(COLLAR_RINGS):
        on = np.zeros(count, dtype=bool)
        on[vertex[skin].ravel()] = True
        skin |= on[vertex].any(axis=1)
    found: Any = is_interface & skin[np.asarray(rows)]
    if largest is not None:
        # **Only small triangles make a collar** (2026-11-24): a collar triangle is a boundary
        # face and no longer also a zone face, so on a coarse display mesh (a STEP box's face
        # is two triangles) a collar of two rings would take the whole shared face out of the
        # interface. The hairline gaps the collar closes are between thin rim triangles.
        corners = np.asarray(positions, dtype="float64")[np.asarray(triangles)[np.asarray(rows)]]
        edges = np.linalg.norm(corners - np.roll(corners, 1, axis=1), axis=2).max(axis=1)
        found = found & (edges <= largest)
    return found


#: The last few multi-region snapshots, by what they are built from (`_snapshot_key`).
_CACHE: OrderedDict[str, Any] = OrderedDict()
_CACHE_SIZE = 4
_CACHE_LOCK = threading.Lock()


def _snapshot_key(session: Session, settings: Node, project: Any) -> str:
    """Everything a multi-region snapshot is built from, as one hash: the mesh settings but the
    core count and host, every body's geometry basis, every tag's members, every boundary's kind
    and tag, and every region's kind, material and tag.

    **Why a cache.** Building the snapshot classifies every triangle of every body against the
    others: 14 s on the gyroid core. The staleness check rebuilds it after every model edit, on
    the session thread, so every command waited behind it: the Mesh card's fields stayed
    disabled for half a minute after a change of the core count (measured, the walkthrough).
    """
    import hashlib
    import json

    from stokes.geometry.store import GeometryStore

    store = GeometryStore.of(session)
    own = {
        k: getattr(v, "value", v)
        for k, v in sorted(settings.params.items())
        if k not in ("cores", "host")
    }
    nodes: list[Any] = []
    for node in project.root.walk():
        if node.type == "part":
            nodes.append(["part", node.id, store.basis(node), node.params.get("simulate", True)])
        elif node.type == "tag":
            nodes.append(["tag", node.id, node.name, str(node.params.get("members") or "")])
        elif node.type == "boundary":
            nodes.append(["boundary", node.id, node.params.get("kind"), node.params.get("tag")])
        elif node.type == "region":
            params = node.params
            nodes.append(["region", node.id, node.name, params.get("kind"), params.get("tag")])
    text = json.dumps([own, nodes], sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def build_conjugate_snapshot(
    session: Session, settings: Node, *, project: Project | None = None
) -> Any:
    """The multi-region `SnappySnapshot` (ADR-0042), built once per set of inputs.

    The core count is not an input of the build: it is applied to the cached record.
    """
    import dataclasses

    from stokes.cores import core_count
    from stokes.mesh import snapshot as base
    from stokes.model import registry

    tree = project if project is not None else session.require_project()
    key = _snapshot_key(session, settings, tree)
    cores = core_count(base._params(settings, registry)["cores"])
    with _CACHE_LOCK:
        found = _CACHE.get(key)
        if found is not None:
            _CACHE.move_to_end(key)
    if found is None:
        found = _build_conjugate_snapshot(session, settings, project=project)
        with _CACHE_LOCK:
            _CACHE[key] = found
            while len(_CACHE) > _CACHE_SIZE:
                _CACHE.popitem(last=False)
    return dataclasses.replace(found, cores=cores, settings_id=str(settings.id))


def _build_conjugate_snapshot(  # noqa: PLR0912, PLR0915 - one read of the tree, in order
    session: Session, settings: Node, *, project: Project | None = None
) -> Any:
    """Read the tree and the geometry store into a multi-region `SnappySnapshot` (ADR-0042)."""
    import numpy as np

    from stokes.cores import core_count
    from stokes.geometry.export import MAX_STAGED_BYTES, Solid, ascii_stl_size, ascii_stl_text
    from stokes.geometry.inside import inside_point_or_coarse
    from stokes.geometry.store import GeometryStore
    from stokes.mesh import snapshot as base
    from stokes.mesh.drivers.snappy import GEOMETRY_DIR, SNAP_CONTROLS, Feature, Surface, Zone
    from stokes.mesh.layers import effective_layers
    from stokes.mesh.levels import level_pair
    from stokes.mesh.two_d import from_settings
    from stokes.model import registry

    project = project if project is not None else session.require_project()
    own = base._params(settings, registry)
    base_size = float(own["base_size"])
    if str(own["domain"]) != "part":
        raise MeshInvalid(
            "several regions are meshed from their bodies; set the domain to the part",
            details={"code": "mesh.domain_unsupported", "domain": str(own["domain"])},
        )
    if from_settings(own) is not None:
        raise MeshInvalid(
            "a 2D mesh has one region; mesh the regions in 3D",
            details={"code": "mesh.two_d_unsupported"},
        )
    regions = [(node, kind, parts) for node, kind, parts, _name in region_bodies(project)]
    names = [name for _node, _kind, _parts, name in region_bodies(project)]

    store = GeometryStore.of(session)
    meshes: list[tuple[Any, Any]] = []
    face_ids: list[Any] = []
    bases: list[str] = []
    boxes: list[tuple[float, ...]] = []
    for region, _kind, parts in regions:
        part = parts[0]
        mesh = store.mesh(str(part.id))
        if mesh is None:
            raise MeshInvalid(
                f"the body {part.name!r} has no display mesh yet; wait for it",
                details={"code": "mesh.invalid", "part": str(part.id)},
            )
        positions, indices, ids = base._arrays(mesh)
        triangles = np.asarray(indices).reshape(-1, 3)
        if len(triangles) == 0:
            raise MeshInvalid(
                f"the body {part.name!r} has no triangles",
                details={"code": "mesh.invalid", "part": str(part.id)},
            )
        positions = np.asarray(positions, dtype="float64")
        meshes.append((positions, triangles))
        face_ids.append(np.asarray(ids).reshape(-1))
        bases.append(store.basis(part))
        boxes.append((*positions.min(axis=0).tolist(), *positions.max(axis=0).tolist()))
        del region

    # ---- the tagged faces of each body are its patches; the rest is classified per triangle
    surfaces: dict[str, Any] = {}
    corners_of: dict[str, list[Any]] = {}
    faces_of: dict[str, int] = {}
    tag_kinds: dict[str, str] = {}
    tag_members: dict[str, Any] = {}
    tag_digests: dict[str, str] = {}
    layer_counts: dict[str, int] = {}
    body_mesh: dict[str, Any] = {}
    region_patches: dict[str, list[str]] = {name: [] for name in names}
    untagged: list[Any] = []
    openings: list[list[Any]] = []
    for index, (_region, _kind, parts) in enumerate(regions):
        part = parts[0]
        positions, triangles = meshes[index]
        ids = face_ids[index]
        total = int(np.unique(ids).size)
        faces, kinds, _members, digests = base._tag_faces(project, part, total, bases[index])
        local = base._body_mesh_settings(part)
        if local:
            body_mesh[str(part.name)] = local
        sized, _files, _areas, _targets = base._sized_surfaces(
            project, part, positions, triangles, ids, faces, kinds, base_size, own, local
        )
        claimed = np.zeros(len(triangles), dtype=bool)
        mouths: list[Any] = []
        for surface in sized:
            if surface.name == "unassigned":
                continue
            wanted = np.fromiter(faces.get(surface.name, ()), dtype=ids.dtype)
            rows = np.flatnonzero(np.isin(ids, wanted) & ~claimed)
            if rows.size == 0:
                continue
            claimed[rows] = True
            surfaces.setdefault(surface.name, surface)
            corners_of.setdefault(surface.name, []).append(positions[triangles[rows]])
            faces_of[surface.name] = faces_of.get(surface.name, 0) + len(faces[surface.name])
            region_patches[names[index]].append(surface.name)
            if kinds.get(surface.name, "") not in CLOSED_KINDS:
                mouths.append(rows)
        tag_kinds.update({name: kind for name, kind in kinds.items() if name in surfaces})
        tag_members.update({name: sorted(found)[:64] for name, found in faces.items()})
        tag_digests.update(digests)
        layer_counts.update(effective_layers(project, part))
        untagged.append(np.flatnonzero(~claimed))
        openings.append(mouths)

    columns = columns_of(meshes, base_size)
    verdicts = classify(meshes, untagged, base_size, columns)
    kinds_of = [kind for _region, kind, _parts in regions]
    default = len(regions) - 1
    # An interface is a wall on both sides: it takes the wall rule (half the base size), or the
    # settings' surface size when one is set.
    from stokes.mesh.sizing import surface_target

    low, high = level_pair(
        base_size,
        surface_target(
            boundary_size=None,
            body_size=None,
            surface_size=float(own["surface_size"]),
            base_size=base_size,
            patch_type="wall",
        ),
    )
    zone_corners: dict[int, list[Any]] = {}
    interfaces: set[tuple[str, str]] = set()
    outer: list[Any] = []
    for index, (is_interface, neighbour) in enumerate(verdicts):
        positions, triangles = meshes[index]
        rows = untagged[index]
        # The outer skin takes a collar of the interface along every rim, so two bodies' skins
        # overlap where they meet (`collar`): without it the mesh leaked out through the seams.
        rim = collar(positions, triangles, rows, is_interface, base_size)
        skin = ~is_interface | rim
        outer.append(positions[triangles[rows[skin]]])
        touched, counts = np.unique(neighbour[is_interface], return_counts=True)
        for other, count in zip(touched.tolist(), counts.tolist(), strict=True):
            pair = (names[index], names[int(other)])
            # A few triangles at a rim can test as facing a body a wall away; a shared wall is
            # many of them.
            if count < max(MIN_SHARED, STRAY_SHARE * int(is_interface.sum())):
                continue
            if kinds_of[index] != "solid" and kinds_of[int(other)] != "solid":
                raise MeshInvalid(
                    f"the fluids {pair[0]!r} and {pair[1]!r} touch; two fluids need a solid "
                    "wall between them, or one region",
                    details={"code": "mesh.fluids_touch", "regions": list(pair)},
                )
            interfaces.add((min(pair), max(pair)))
        # A collar triangle is the mesh's boundary, so it is not also a zone face: one surface
        # holds each face (2026-11-24).
        coupled = is_interface & ~rim
        if index != default and coupled.any():
            zone_corners.setdefault(index, []).append(positions[triangles[rows[coupled]]])
    outer = _off_zones(outer, zone_corners, base_size)
    for index, chunk in enumerate(outer):
        if len(chunk):
            region_patches[names[index]].append("unassigned")
    outer = [chunk for chunk in outer if len(chunk)]
    if outer:
        o_low, o_high = level_pair(base_size, base_size)
        surfaces["unassigned"] = Surface(
            name="unassigned",
            file="unassigned.stl",
            region="unassigned",
            patch_type="wall",
            level_min=o_low,
            level_max=o_high,
            requested=float(base_size),
        )
        corners_of["unassigned"] = outer

    # ---- the background box around every body, and a point inside each
    lows = np.min(np.array([box[:3] for box in boxes]), axis=0)
    highs = np.max(np.array([box[3:] for box in boxes]), axis=0)
    union = (*lows.tolist(), *highs.tolist())
    extent = float(np.max(highs - lows))
    grown = base._aligned_box(union, max(0.05 * extent, 2.0 * base_size), base_size)
    zones: list[Any] = []
    stl_files: dict[str, str] = {}
    surface_triangles: dict[str, int] = {}
    surface_areas: dict[str, float] = {}

    def stage(name: str, file: str, chunks: list[Any]) -> None:
        corners = np.concatenate(chunks).astype("float32")
        size = ascii_stl_size(len(corners))
        if size > MAX_STAGED_BYTES:
            raise MeshInvalid(
                f"the surface for {name!r} would be {size / 1048576:.1f} MiB of ASCII STL, over "
                f"the {MAX_STAGED_BYTES / 1048576:.1f} MiB the agent can stage",
                details={"code": "mesh.too_large_to_stage", "surface": name, "bytes": size},
            )
        stl_files[f"{GEOMETRY_DIR}/{file}"] = ascii_stl_text(
            Solid(name=name, corners=corners, faces=len(corners))
        )
        surface_triangles[name] = len(corners)
        wide = corners.astype("float64")
        cross = np.cross(wide[:, 1] - wide[:, 0], wide[:, 2] - wide[:, 0])
        surface_areas[name] = float(0.5 * np.linalg.norm(cross, axis=1).sum())

    for name in sorted(surfaces):
        stage(name, surfaces[name].file, corners_of[name])

    inside_points: list[tuple[float, float, float]] = []
    for index, (region, kind, parts) in enumerate(regions):
        positions, triangles = meshes[index]
        seeds = _seeds(positions, triangles, openings[index], base_size, columns[index])
        point: tuple[float, float, float] | None = seeds[0] if seeds else None
        if point is None and index == default and inside_points:
            # The default region is the cells no zone claims: its own point is never written.
            point = inside_points[0]
        if point is None:
            found = inside_point_or_coarse(positions, triangles, grown)
            point = found.point
        if point is None:
            raise MeshInvalid(
                f"could not find a point inside {parts[0].name!r}; check that it is a closed solid",
                details={"code": "mesh.no_inside_point", "part": str(parts[0].id)},
            )
        inside_points.append(point)
        file = ""
        if index in zone_corners:
            file = f"{names[index]}_zone.stl"
            stage(f"{names[index]}_zone", file, zone_corners[index])
        elif index != default:
            raise MeshInvalid(
                f"the body {parts[0].name!r} (region {region.name!r}) shares no wall with "
                "another body; several regions are meshed together where they touch: leave it "
                "out of the simulation, or check that the bodies meet",
                details={"code": "mesh.region_apart", "part": str(parts[0].id)},
            )
        zones.append(
            Zone(
                name=names[index],
                kind="solid" if kind == "solid" else "fluid",
                part=str(parts[0].id),
                file=file,
                level_min=low,
                level_max=high,
                inside_point=point,
                # **No topoSet rebuild of the fluid from its openings** (ADR-0042 decision 3,
                # withdrawn 2026-11-20): `regionToCell` stops the mesh at any seed whose cell
                # snapping removed, and at a 1 mm cell in a gyroid mouth one always was (twice in
                # the walkthrough). The cells snappy keeps from the zone's inside point solve as
                # they are; the seeds still choose that point.
                seeds=(),
            )
        )

    ordered = tuple(surfaces[name] for name in sorted(surfaces))
    # No feature edges for the zone surfaces: they are open, and every edge of their rim would
    # be one (measured: the mesh refined along all of them and passed 2.8 M cells).
    files = {s.name: s.file for s in ordered}
    nx, ny, nz = base._snappy_cells(grown, base_size, int(own["max_cells"]))
    first = regions[0][2][0]
    return base_snapshot(
        own=own,
        settings=settings,
        surfaces=ordered,
        features=tuple(
            Feature(file=f"{name}.eMesh", level=level)
            for name, level in base._snappy_features(files)
        ),
        grown=grown,
        cells=(nx, ny, nz),
        inside_point=inside_points[0],
        stl_files=stl_files,
        domain_part=str(first.id),
        part_basis="+".join(bases),
        side_kinds={
            name: ("wall" if tag_kinds.get(name, "wall") in ("wall", "symmetry") else "patch")
            for name in surfaces
        },
        tag_members=tag_members,
        body_mesh=body_mesh,
        tag_digests={name: digest for name, digest in tag_digests.items() if name in surfaces},
        layer_counts={name: n for name, n in layer_counts.items() if name in surfaces},
        domain_bbox=union,
        surface_areas=surface_areas,
        surface_targets={s.name: (None, None, s.patch_type) for s in ordered},
        surface_triangles=surface_triangles,
        surface_faces={name: faces_of.get(name, 1) for name in surface_triangles},
        side_tags=base._tag_node_ids(project),
        zones=tuple(zones),
        default_region=names[default],
        interfaces=tuple(sorted(interfaces)),
        region_patches={name: tuple(dict.fromkeys(p)) for name, p in region_patches.items()},
        cores=core_count(own["cores"]),
        snap_controls=SNAP_CONTROLS,
    )


def _off_zones(outer: list[Any], zones: dict[int, list[Any]], base_size: float) -> list[Any]:
    """Each body's outer-skin triangles without those that lie on a zone surface.

    **Why.** snappyHexMesh snaps a face to the first surface it meets, and a boundary surface
    lying on a zone surface makes the shared wall a wall baffle, not an interface: adiabatic,
    and a second wall in the flow (measured on the mini plate exchanger, 2026-11-24: 241,774 of
    the 304,501 `unassigned` triangles lay on the zone surfaces, 74 % of the hot wall by area was
    left uncoupled, the duty read 39.8 W against 77.8 W with them removed and the hot pressure
    drop 37 % high). A collar triangle of the solid on a fluid's zone is dropped: the fluid's
    own collar closes the skin at that rim.

    **On the surface, not on its triangles** (2026-11-24, second pass): two bodies tessellated
    apart share a wall with no centre in common, so matching centres kept the solid's collar on
    the fluid's zone (measured on two channels through a block, the block's tunnels meshed finer:
    12 mm2 of 320 left as a baffle). A triangle lying in a zone triangle's plane, within
    `ON_SURFACE_FRACTION` of a cell, near parallel and over it, is dropped too
    (`stokes.mesh.on_surface`).

    **Cut where it is partly on a zone** (2026-11-24, third pass): with a second solid, the
    block a channel runs through is a zone too, and a channel's wall triangle that crosses the
    block's face at a mouth is half on the block's zone and half outer skin. Dropped whole (its
    centre on the zone) it left a hole of 0.3 to 1.1 mm2 in the outer skin outside each mouth;
    kept whole, it lay on the zone. It is now cut along the zone's triangles and the part off
    the zone kept (`stokes.mesh.on_surface.off_surface`).
    """
    import numpy as np

    from stokes.mesh.on_surface import off_surface

    staged = [np.concatenate(chunks) for chunks in zones.values() if chunks]
    if not staged:
        return outer
    surface = np.concatenate(staged)
    centres = surface.mean(axis=1)
    kept: list[Any] = []
    for chunk in outer:
        if len(chunk) == 0:
            kept.append(chunk)
            continue
        on = coincident(chunk.mean(axis=1), centres, TOUCH_FRACTION * base_size)
        kept.append(off_surface(chunk[~on], surface, ON_SURFACE_FRACTION * base_size))
    return kept


def base_snapshot(
    *, own: dict[str, Any], settings: Any, grown: Any, cells: Any, **rest: Any
) -> Any:
    """The `SnappySnapshot`, with the settings every snappy record carries read in one place."""
    from stokes.mesh import snapshot as base
    from stokes.mesh.drivers.snappy import SnappySnapshot

    return SnappySnapshot(
        background_min=(grown[0], grown[1], grown[2]),
        background_max=(grown[3], grown[4], grown[5]),
        background_cells=cells,
        base_size=float(own["base_size"]),
        max_cells=int(own["max_cells"]),
        max_non_ortho=float(own["max_non_ortho"]),
        max_skewness=float(own["max_skewness"]),
        settings_id=str(settings.id),
        domain="part",
        layer_settings=base._layer_settings(own),
        surface_size=float(own["surface_size"]),
        feature_angle=float(own["feature_angle"]),
        unmapped=(),
        **rest,
    )


#: How far into a body a seed is tried, in base cells, deepest first: a gyroid channel turns away
#: from its mouth within a cell or two, so a deeper point is often outside it.
SEED_DEPTHS = (1.5, 1.2, 1.0, 0.8)


def _clear_points(
    centre: Any, unit: Any, base_size: float, columns: Any, low: Any, high: Any
) -> Any:  # Any: numpy arrays
    """The points a little way in from a mouth's triangles that make good seeds, or none.

    A seed must be inside the body and clear of its box sides by most of a cell: snapping moves
    the mesh's skin by a fraction of a cell, and a seed it leaves outside stops topoSet ("Did not
    find ... in mesh", measured at 0.04 mm from a side). Clear of the body's own walls too, when
    the channel is wide enough for that.
    """
    import numpy as np

    loose: Any = None
    for depth in SEED_DEPTHS:
        for sign in (-1.0, 1.0):
            points = centre + sign * depth * base_size * unit
            inside = columns.inside(points)
            margin = 0.75 * base_size
            boxed = np.all((points > low + margin) & (points < high - margin), axis=1)
            clear = inside & boxed
            for axis in range(3):
                for step in (-0.5, 0.5):
                    moved = points.copy()
                    moved[:, axis] += step * base_size
                    clear &= columns.inside(moved)
            if clear.any():
                return points[clear]
            if loose is None and (inside & boxed).any():
                loose = points[inside & boxed]
    return loose if loose is not None else np.zeros((0, 3))


def _seeds(
    positions: Any, triangles: Any, mouths: list[Any], base_size: float, columns: Any
) -> list[tuple[float, float, float]]:
    """Points just inside a body's openings: the fluid is the cells connected to them.

    Each opening gives up to an equal share of `MAX_SEEDS`, taken from its largest triangles down
    and spread through them, each a cell or so in from the triangle's centre (`_clear_points`).
    """
    import numpy as np

    if not mouths:
        return []
    low, high = positions.min(axis=0), positions.max(axis=0)
    share = max(1, MAX_SEEDS // len(mouths))
    out: list[tuple[float, float, float]] = []
    for rows in mouths:
        corners = positions[triangles[rows]]
        normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        area = np.linalg.norm(normal, axis=1)
        keep = np.flatnonzero(area > 0.0)
        if keep.size == 0:
            continue
        order = keep[np.argsort(-area[keep], kind="stable")]
        picks = order[:: max(1, len(order) // (4 * share))][: 4 * share]
        unit = normal[picks] / area[picks][:, None]
        centre = corners[picks].mean(axis=1)
        found = _clear_points(centre, unit, base_size, columns, low, high)
        if len(found):
            out.extend(tuple(float(v) for v in p) for p in found[:share])  # type: ignore[misc]
    return out
