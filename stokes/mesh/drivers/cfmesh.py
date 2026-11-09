"""The cfMesh family and the poly-hexcore dual: four volume-fill types from one surface (ADR-0032).

## What this driver offers

Fluent Meshing's volume fills, mapped to what the FoamWin build ships (measured on the Flanged pipe
demo, 2026-11-07; the numbers are in ADR-0032):

==============  =================================  ===========================================
mesher id       utilities                          cells it makes
==============  =================================  ===========================================
``cfmesh``      ``cartesianMesh``                  hex-dominant: a Cartesian core, trimmed polyhedra
                                                   at the wall
``cfmesh_tet``  ``tetMesh``                        tetrahedra
``cfmesh_poly`` ``pMesh``                          polyhedra
``polyhex``     ``cartesianMesh`` → ``polyDualMesh``  poly-hexcore: the dual of a Cartesian mesh is
                                                   hexahedral in the core and polyhedral where the
                                                   octree transitions and at the wall
==============  =================================  ===========================================

## Why it reuses the snappy snapshot

cfMesh needs exactly what snappy needs from the tree: one surface per boundary tag (so the patch
names are the user's tag names), the per-surface target sizes, and the wall layer counts. The
snapshot builder (`stokes.mesh.snapshot.build_cfmesh_snapshot`) builds the snappy record and joins
its per-tag STLs into **one multi-solid STL**, because cfMesh reads a single `surfaceFile` and names
a patch after each `solid`.

## Two things measured, not assumed

* **cfMesh writes every patch as `type empty`.** Measured: the boundary file of an untouched
  `cartesianMesh` run on the pipe listed `inlet`, `outlet` and `walls` all as `empty`, and
  `checkMesh` then failed the 1-D/2-D test. `renameBoundary.newPatchNames` sets each patch's type
  from its tag kind, which is why the dictionary always carries that block.
* **Layers before the dual, not after.** `generateBoundaryLayers` run on a dual mesh left 6,909
  unused points (checkMesh: 2 failed checks); `cartesianMesh` growing the layers and
  `polyDualMesh` converting the layered mesh passed every check (max non-orthogonality 27.8°).

## The polyhedral type flattens its symmetry planes

**Measured on the mixing elbow (2026-11-08):** `pMesh` left 49 points of the symmetry plane up to
3.3 mm off it (5 mm cells) along the edge where a pipe wall meets the plane, though the surface it
was given holds the plane exactly, and k-omega SST diverged from those cells. cfMesh's own
`improveSymmetryPlanes` moves the plane's points back onto one plane, after which the same SST case
converged (residuals 1.6e-5). It acts only on patches typed `symmetryPlane`, and OpenFOAM 14's
`symmetryPlane` refuses a plane that is flat to 1e-4 rather than 1e-15 ("Symmetry plane ... is not
planar"), so the patch is meshed as `symmetryPlane`, flattened, and set back to `symmetry` with
`foamDictionary` before `checkMesh`. Only a symmetry surface that is itself planar is treated so.
The tetrahedral and hex-dominant types are not: the same utility inverted cells on both (3 on the
elbow's tet mesh).

## The tetrahedral and hex-dominant types snap theirs, as far as the cells allow

**Measured on the mixing elbow (2026-11-09):** the app's own realizable k-epsilon case on the tet
mesh died at iteration 113 with a negative temperature, a runaway that starts in cells beside the
bent plane (with plain schemes it is NaN by iteration 68), and the polyhedral mesh, whose plane is
flat, converges. A full snap of the tet plane's points inverts a cell, so the agent's `flatten`
action (`stokes_agent.flatten`) moves each point by as much of its move as the cells around it
allow: 76 to 78 points off, all but 3 or 4 moved fully, worst remaining offset 0.27 to 0.58 mm
(5 to 12 % of a cell), every cell still valid. The same case on the flattened mesh runs its 500
iterations. It runs after the fill and before `checkMesh`, never fails the job, and the mesh
service reports whatever is still bent (`stokes.mesh.flatness`).

## Purity

Like every driver this runs nothing and touches no disk: a snapshot in, case files and steps out.
"""

from __future__ import annotations

import math
from typing import Any

from stokes.compile.foam import Dict, Entry, Quoted, Raw, Word, render_file
from stokes.compile.parallel import checked_cores
from stokes.mesh.errors import MeshInvalid

#: Bumped when any emitted text changes; it enters the mesh basis.
DRIVER_VERSION = "cfmesh/3"

#: The types whose planar symmetry patches are flattened after the fill (see the module docstring).
FLATTENS: frozenset[str] = frozenset({"cfmesh_poly"})

#: The types whose planar symmetry patches are SNAPPED onto their plane after the fill, by the
#: agent's `flatten` action (`stokes_agent.flatten`): `improveSymmetryPlanes` inverts cells here.
SNAPS: frozenset[str] = frozenset({"cfmesh", "cfmesh_tet"})
SNAP_STEP = "flattenSymmetry"

#: The step that flattens, and the stem of the steps that set each plane back to `symmetry`.
FLATTEN_STEP = "improveSymmetryPlanes"
RESTORE_STEP = "symmetryType"

#: The boundary file `foamDictionary` edits, and the one value it may set.
BOUNDARY_FILE = "constant/polyMesh/boundary"
SYMMETRY = "symmetry"

#: The one surface cfMesh reads: the per-tag STLs joined, a `solid` per tag.
SURFACE_FILE = "constant/geometry/domain.stl"

#: Mesher id -> the utilities that build the volume, in order.
VOLUME_STEPS: dict[str, tuple[str, ...]] = {
    "cfmesh": ("cartesianMesh",),
    "cfmesh_tet": ("tetMesh",),
    "cfmesh_poly": ("pMesh",),
    "polyhex": ("cartesianMesh", "polyDualMesh"),
}

#: The meshers that refuse boundary layers. **Measured, 2026-11-07**: `tetMesh` with a wall layer
#: request on the pipe made 159 k prisms and left 132,239 points used by no face, so checkMesh
#: failed; `improveMeshQuality` and `renumberMesh` kept them, and `generateBoundaryLayers` after a
#: plain `tetMesh` failed two checks. A refusal says so; a failing mesh would not.
NO_LAYERS: frozenset[str] = frozenset({"cfmesh_tet"})

#: The meshers this driver serves.
MESHERS: tuple[str, ...] = tuple(VOLUME_STEPS)

#: The `polyDualMesh` feature angle floor, in degrees. **The dual keeps an edge whose faces meet
#: sharper than this**, and too small an angle splits nearly every curved-wall cell into several
#: (the faceted CAD reads as features). 70 is what the measured run used; a user's sharper
#: `feature_angle` is honoured above it.
DUAL_MIN_FEATURE_ANGLE = 70.0

#: Seconds per volume step. cfMesh meshes the demo in 8 to 43 s; an hour leaves room for real
#: parts without letting a wedged process hold the job for ever.
VOLUME_TIMEOUT_S = 3600
#: Seconds for the flatten action: reading a mesh in Python is the cost (3 s for 566,000 faces).
SNAP_TIMEOUT_S = 1800


def write(snap: Any) -> dict[str, str]:
    """`system/meshDict`, `system/controlDict` and the joined surface."""
    _validate(snap)
    from stokes.mesh.drivers.blockmesh import _control_dict

    files = {
        "system/meshDict": _mesh_dict(snap),
        "system/controlDict": _control_dict(),
    }
    files.update(snap.stl_files)
    return files


def steps(snap: Any) -> list[dict[str, Any]]:
    """The volume utilities for the chosen type, then `checkMesh`.

    **`threads`, not `ranks`.** cfMesh parallelises with OpenMP inside one process, so the
    settings' `cores` becomes the step's thread count (the agent sets `OMP_NUM_THREADS`); MPI
    cfMesh needs a decomposed surface and is left for later (ADR-0032).
    """
    cores = checked_cores(snap.cores)
    run: list[dict[str, Any]] = []
    for utility in VOLUME_STEPS[str(snap.mesher)]:
        if utility == "polyDualMesh":
            run.append(
                {
                    "name": "polyDualMesh",
                    "argv": ["polyDualMesh", _angle(dual_feature_angle(snap))],
                    "timeout_s": VOLUME_TIMEOUT_S,
                }
            )
            continue
        step: dict[str, Any] = {
            "name": utility,
            "argv": [utility],
            "timeout_s": VOLUME_TIMEOUT_S,
        }
        if cores > 1:
            step["threads"] = cores
        run.append(step)
    planes = flattened_planes(snap)
    if planes:
        run.append({"name": FLATTEN_STEP, "argv": [FLATTEN_STEP], "timeout_s": VOLUME_TIMEOUT_S})
        for position, patch in enumerate(planes):
            run.append(
                {
                    "name": RESTORE_STEP if position == 0 else f"{RESTORE_STEP}{position + 1}",
                    "argv": [
                        "foamDictionary",
                        BOUNDARY_FILE,
                        "-entry",
                        f"entry0/{patch}/type",
                        "-set",
                        SYMMETRY,
                    ],
                    "timeout_s": 120,
                }
            )
    for position, patch in enumerate(snapped_planes(snap)):
        run.append(
            {
                "name": SNAP_STEP if position == 0 else f"{SNAP_STEP}{position + 1}",
                "argv": [],
                "timeout_s": SNAP_TIMEOUT_S,
                "action": {"op": "flatten", "patch": patch},
            }
        )
    run.append({"name": "checkMesh", "argv": ["checkMesh"], "timeout_s": 600, "parse": "checkMesh"})
    return run


def flattened_planes(snap: Any) -> tuple[str, ...]:
    """The planar `symmetry` patches of a type in `FLATTENS`, for `improveSymmetryPlanes`."""
    return _planar_symmetry(snap) if str(snap.mesher) in FLATTENS else ()


def snapped_planes(snap: Any) -> tuple[str, ...]:
    """The planar `symmetry` patches of a type in `SNAPS`, for the agent to snap."""
    return _planar_symmetry(snap) if str(snap.mesher) in SNAPS else ()


def _planar_symmetry(snap: Any) -> tuple[str, ...]:
    """The planar `symmetry` surfaces of the snapshot, by patch name.

    **Planar is judged on the surface the mesher is given**, not assumed from the boundary kind: a
    curved symmetry surface is legal (`symmetry`, not `symmetryPlane`), and flattening one would
    move its points onto a plane that is not there.
    """
    solids = _solid_points(str(snap.stl_files.get(SURFACE_FILE, "")))
    planes: list[str] = []
    for surface in snap.surfaces:
        if str(surface.patch_type) != SYMMETRY:
            continue
        points = solids.get(str(surface.name))
        if points and _planar(points):
            planes.append(str(surface.region or surface.name))
    return tuple(planes)


def _solid_points(text: str) -> dict[str, list[tuple[float, float, float]]]:
    """Each `solid`'s vertices in an ASCII multi-solid STL (the joined surface)."""
    solids: dict[str, list[tuple[float, float, float]]] = {}
    current: list[tuple[float, float, float]] | None = None
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "solid":
            current = solids.setdefault(words[1] if len(words) > 1 else "", [])
        elif words[0] == "endsolid":
            current = None
        elif words[0] == "vertex" and current is not None and len(words) == 4:
            try:
                current.append((float(words[1]), float(words[2]), float(words[3])))
            except ValueError:
                continue
    return solids


def _planar(points: list[tuple[float, float, float]]) -> bool:
    """Every vertex of the surface on one plane, to the flatness module's tolerance."""
    import numpy as np

    from stokes.mesh.flatness import PLANAR_TOLERANCE, offsets

    array = np.unique(np.asarray(points, dtype=np.float64), axis=0)
    if len(array) == 3:
        return True  # three points always lie on a plane; one triangle is a planar face
    distance = offsets(array)
    if distance is None:
        return False
    extent = float(np.linalg.norm(array.max(axis=0) - array.min(axis=0)))
    return float(distance.max()) <= PLANAR_TOLERANCE * extent


def dual_feature_angle(snap: Any) -> float:
    """The angle `polyDualMesh` is given: the settings' feature angle, never below the floor."""
    return max(float(getattr(snap, "feature_angle", 0.0) or 0.0), DUAL_MIN_FEATURE_ANGLE)


def cell_count(snap: Any) -> int:
    """The cells a uniform fill of the domain's box at the base size would hold.

    **An order of magnitude before the job, as snappy's background count is**: cfMesh refines at
    the surfaces and keeps only the inside, so the real count comes from `checkMesh` afterwards.
    """
    box = getattr(snap, "domain_bbox", None) or snap.bbox
    size = float(snap.base_size)
    if size <= 0:
        return 0
    return int(math.prod(max(1, math.ceil((box[i + 3] - box[i]) / size)) for i in range(3)))


def patch_types(snap: Any) -> dict[str, str]:
    """Patch name -> boundary type: the tag names, as `renameBoundary` writes them."""
    return {str(s.region or s.name): str(s.patch_type) for s in snap.surfaces}


# ---------------------------------------------------------------------------------- the dictionary


def _mesh_dict(snap: Any) -> str:
    """`system/meshDict`: the surface, the sizes, the patch names and types, and the layers."""
    base = float(snap.base_size)
    entries: list[Entry] = [
        Entry("surfaceFile", Quoted(SURFACE_FILE)),
        Entry("maxCellSize", Raw(_num(base))),
    ]
    refined = [
        Entry(str(s.name), Dict((Entry("cellSize", Raw(_num(float(s.requested)))),)))
        for s in snap.surfaces
        if 0.0 < float(s.requested) < base
    ]
    if refined:
        # **Per patch, from the same precedence snappy's levels come from** (boundary size >
        # body size > surface size > the wall rule), so switching mesher keeps the sizes asked
        # for. cfMesh takes a length directly; no level arithmetic is involved.
        entries.append(Entry("localRefinement", Dict(tuple(refined))))
    # **A plane to be flattened is meshed as `symmetryPlane`**: `improveSymmetryPlanes` acts on no
    # other type, and a later step sets it back to `symmetry` (the module docstring).
    flatten = set(flattened_planes(snap))

    def meshed_type(surface: Any) -> str:
        name = str(surface.region or surface.name)
        return "symmetryPlane" if name in flatten else str(surface.patch_type)

    entries.append(
        Entry(
            "renameBoundary",
            Dict(
                (
                    Entry(
                        "newPatchNames",
                        Dict(
                            tuple(
                                Entry(
                                    str(s.name),
                                    Dict(
                                        (
                                            Entry("newName", Word(str(s.region or s.name))),
                                            Entry("type", Word(meshed_type(s))),
                                        )
                                    ),
                                )
                                for s in snap.surfaces
                            )
                        ),
                    ),
                )
            ),
        )
    )
    layers = _layers(snap)
    if layers is not None:
        entries.append(Entry("boundaryLayers", layers))
    return render_file("meshDict", Dict(tuple(entries)), location="system")


def _layers(snap: Any) -> Dict | None:
    """`boundaryLayers`, walls only, or None when no wall asked for a layer.

    **Per patch with no global count**: a global `nLayers` would grow layers on the inlet and
    outlet too, and Stokes's layer rule is walls only. `thicknessRatio` is the expansion ratio;
    in first-height mode the first layer's height caps `maxFirstLayerThickness`. cfMesh has no
    final-ratio control - it sizes the layers from the local cell - so that mode writes the count
    and the ratio only (ADR-0032 records the gap).
    """
    counts = {name: int(n) for name, n in snap.layer_counts.items() if int(n) > 0}
    if not counts:
        return None
    settings = dict(snap.layer_settings or {})
    ratio = float(settings.get("expansion_ratio", 1.2))
    first = float(settings.get("first_layer_height", 0.0) or 0.0)
    first_height = settings.get("mode") == "first_height" and first > 0.0
    per_patch: list[Entry] = []
    for name in sorted(counts):
        body: list[Entry] = [
            Entry("nLayers", counts[name]),
            Entry("thicknessRatio", Raw(_num(ratio))),
        ]
        if first_height:
            body.append(Entry("maxFirstLayerThickness", Raw(_num(first))))
        body.append(Entry("allowDiscontinuity", 0))
        per_patch.append(Entry(name, Dict(tuple(body))))
    return Dict((Entry("patchBoundaryLayers", Dict(tuple(per_patch))), Entry("optimiseLayer", 1)))


# ------------------------------------------------------------------------------------- validation


def validate(snap: Any) -> None:
    """The writer's refusals without writing, for the precheck."""
    _validate(snap)


def _validate(snap: Any) -> None:
    """Refuse what cfMesh would mesh wrongly or not at all, naming the reason."""
    if str(snap.mesher) not in VOLUME_STEPS:
        raise MeshInvalid(
            f"the cfMesh driver does not build {snap.mesher!r}",
            details={"code": "mesh.invalid", "mesher": str(snap.mesher)},
        )
    try:
        checked_cores(snap.cores)
    except (TypeError, ValueError) as exc:
        raise MeshInvalid(str(exc), details={"code": "mesh.invalid", "cores": snap.cores}) from exc
    if not snap.surfaces or SURFACE_FILE not in snap.stl_files:
        raise MeshInvalid(
            "no surface to mesh: cfMesh needs the part's closed surface",
            details={"code": "mesh.invalid"},
        )
    base = float(snap.base_size)
    if not math.isfinite(base) or base <= 0.0:
        raise MeshInvalid(
            f"the base size must be a positive length; got {base!r}",
            details={"code": "mesh.invalid"},
        )
    if str(snap.mesher) in NO_LAYERS and any(int(n) > 0 for n in snap.layer_counts.values()):
        raise MeshInvalid(
            "the tetrahedral type cannot grow boundary layers on this build: cfMesh leaves "
            "unused points behind and checkMesh fails. Set layers to 0, or choose the "
            "poly-hexcore, polyhedral or hex-dominant type for a layered mesh",
            details={"code": "mesh.layers_unsupported", "mesher": str(snap.mesher)},
        )
    from stokes.mesh.drivers.snappy import _validate_layers

    # **The same range checks as snappy's layers** (the M-504 review's F2): a loaded project
    # skips the parameter hooks, so the writer re-checks every value it is about to emit.
    _validate_layers(snap)


def _num(value: float) -> str:
    """A length as a dictionary number: 12 significant digits, no binary noise."""
    return f"{value:.12g}"


def _angle(value: float) -> str:
    """The dual's feature angle as the agent's positional pattern accepts it (0-180, 3 decimals)."""
    clamped = min(max(value, 0.0), 180.0)
    text = f"{clamped:.3f}".rstrip("0").rstrip(".")
    return text or "0"
