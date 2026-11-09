"""The snappyHexMesh dictionaries: the case that makes a part's own shape the mesh (T-232).

## Why a dictionary writer is the whole job

snappyHexMesh takes no geometry on its command line. Everything it does comes from
`system/snappyHexMeshDict`: which surfaces to follow, how finely, which point is "inside", and how
hard to snap. A wrong key is not a crash - OpenFOAM ignores keys it does not know - so a
misremembered name produces a mesh that silently does less than the user asked. Every key here was
therefore checked against the installed OpenFOAM 14 source before it was written, and the findings
are recorded with their evidence in `tests/fixtures/of14/inventory-thinkyboy.md`.

**The two that cost the most to get wrong:**

* **`curvatureLevel` does not exist in OF14.** "Refine by curvature" is expressed as
  `resolveFeatureAngle` plus the per-surface `level (min max)` pair, where a cell that sees
  intersections above the angle is refined to the MAX of the pair. An emitted `curvatureLevel` is
  silently ignored: the mesh simply looks unrefined, with no error to explain why.
* **The inside point is `insidePoint`.** `locationInMesh` is accepted as a fallback (precedence is
  `insidePoints` > `insidePoint` > `locationInMesh`), but every OF14 tutorial uses `insidePoint`.

## Surfaces, features and patches

One STL per boundary tag, so snappy's own patch names are the user's tag names - the `regions {}`
block in `geometry` maps each surface to the patch it becomes. Faces in no tag are gathered into a
surface of their own and become a `wall` named `unassigned`, exactly as the blockMesh driver does,
so the boundary file has an entry for them.

`surfaceFeatures` reads the same staged surfaces and writes an `.eMesh` into
`constant/geometry/`, which `features` then refines along.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from stokes.compile.foam import (
    Comment,
    Dict,
    Entry,
    List_,
    Quoted,
    Raw,
    Tuple_,
    Word,
    render_file,
)
from stokes.compile.parallel import (
    checked_cores,
    decompose_par_dict,
    decompose_step,
    reconstruct_mesh_step,
)
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.levels import level_for, level_pair
from stokes.mesh.two_d import BACK as TWO_D_BACK
from stokes.mesh.two_d import FRONT as TWO_D_FRONT

#: Bumped when any emitted text changes. It enters the mesh basis, so a cached mesh from an older
#: driver is not mistaken for one this driver would produce.
#:
#: **`snappy/2`** (brief M-501): the basis now hashes the parts the dictionary is built from in
#: full — every tag's face digest, the per-surface levels, the features, the snap and quality
#: controls — so a mesh made by `snappy/1` (whose basis saw none of those) must be regenerated
#: once rather than mistaken for current.
#:
#: **`snappy/3`** (brief M-504): the layer pass. The writer emits the resolved
#: `addLayersControls` and the relaxed mesh-quality rules when a wall got layers, and the
#: snappy step's log is parsed for the layer coverage table — a mesh made by `snappy/2` never
#: had either.
#:
#: **`snappy/4`**: optional MPI meshing, with a generated `decomposeParDict`, decomposed background
#: mesh, parallel snappy run and reconstructed serial polyMesh for the normal readers.
DRIVER_VERSION = "snappy/5"

#: Where OF14 reads surfaces and feature-edge meshes from. **Measured, not assumed**: the motorBike
#: case has `constant/geometry/` and no `constant/triSurface/`, and its `snappyHexMeshDict` says
#: feature edges are "read from constant/geometry for now" (`snappyHexMeshDict:90`). Foundation
#: releases before about v10 used `triSurface`, which is why the brief flagged this.
GEOMETRY_DIR = "constant/geometry"

#: The angle above which a surface intersection counts as a feature worth refining to the max
#: level. 30 degrees is the brief's value and matches the fidelity design: it catches a fillet or a
#: chamfer without chasing tessellation noise.
RESOLVE_FEATURE_ANGLE = 30.0

#: The `surfaceFeatures` included angle at the DEFAULT feature angle of 30. **An included angle
#: of 150 means a dihedral of 30** - the utility measures the angle between the two faces, so the
#: two numbers are complements and using 30 here would extract almost nothing. Measured against
#: the OF14 tutorial, which uses exactly 150
#: (`tutorials/incompressibleFluid/propeller/system/surfaceFeaturesDict`). **The dictionary value
#: follows the setting now** (brief M-515): `includedAngle = 180 - feature_angle`, written in
#: `_surface_features_dict`, so this constant is only the default's complement.
FEATURES_INCLUDED_ANGLE = 150.0

#: `minRefinementCells`: stop refining when fewer cells than this remain selected, which prevents
#: spending iterations on a handful of cells. 2, per the fidelity design.
MIN_REFINEMENT_CELLS = 2

#: Cells between refinement levels. 3 is the OF14 tutorials' value and gives a 3-cell buffer, which
#: keeps the cell-size jump gentle enough for the solver's interpolation.
CELLS_BETWEEN_LEVELS = 3

#: The patch a surface gets when no boundary tag claims it. Written explicitly rather than as a
#: `defaultPatch`, so it is visible in the boundary file the quality card reads.
UNASSIGNED_PATCH = "unassigned"

#: The `addLayersControls` keys nobody sets in the guided flow, at the motorBike tutorial's
#: values - **the [VERIFY-OF14] evidence for every one** (the whole block, with its comments, is
#: in `tests/fixtures/of14/inventory-thinkyboy.md`):
#: `nGrow 0` (points whose extrusion failed do not drag their neighbours with them),
#: `nRelaxIter 3` (snapping-relaxation iterations; read from the layer phase by
#: `medialAxisMeshMover.C:1966`, required), `nSmoothSurfaceNormals 1` and `nSmoothNormals 3`
#: (both required, `medialAxisMeshMover.C:360,388`), `nSmoothThickness 2`,
#: `maxFaceThicknessRatio 0.5`, `maxThicknessToMedialRatio 0.3`, `minMedianAxisAngle 90`
#: (the medial-axis angle key; the mover also accepts the spelling `minMedialAxisAngle`,
#: `medialAxisMeshMover.C:369`), `nBufferCellsNoExtrude 0` and `nLayerIter 50` (both required,
#: `layerParameters.C:145,147`). The propeller tutorial's differing values are recorded beside
#: them in the inventory; where the two disagree this driver follows motorBike.
LAYER_FIXED: tuple[tuple[str, Any], ...] = (
    ("nGrow", 0),
    ("nRelaxIter", 3),
    ("nSmoothSurfaceNormals", 1),
    ("nSmoothNormals", 3),
    ("nSmoothThickness", 2),
    ("maxFaceThicknessRatio", 0.5),
    ("maxThicknessToMedialRatio", 0.3),
    ("minMedianAxisAngle", 90),
    ("nBufferCellsNoExtrude", 0),
    ("nLayerIter", 50),
)

#: `slipFeatureAngle`: where extrusion may slip along a wall instead of stopping. Written as
#: **half the feature angle**, which is the source's own default
#: (`medialAxisMeshMover.C:378-387`, `lookupOrDefault("slipFeatureAngle", featureAngle/2)`) -
#: the tutorials carry hand-tuned values (motorBike 30 over a 100 angle, propeller 15 over 60)
#: that are not a rule, and an explicit half keeps the dictionary honest about what was chosen.

#: `nRelaxedIter`: the layer iteration after which snappy switches to the relaxed quality rules
#: (`snappyLayerDriver.C:3145`; optional in the dictionary, `readIfPresent` at
#: `layerParameters.C:258`, and the default `labelMax` means never). The tutorials write none -
#: no tutorial layers block carries it - so **the value is ours and recorded here**: 20 of the
#: 50 layer iterations under the ordinary rules, the relaxed ones only for the last stretch.
#: Measured with the [F] re-measure; the point is to let layers survive where they collapse,
#: not to hide a mesh that cannot meet the ordinary rules at all.
LAYER_N_RELAXED_ITER = 20

#: A layer block's patch key: a BARE word, `\\Z`-anchored (the M-504 security review's F1).
#: `KEY_RE` deliberately allows quoted regex keys — `fvSchemes` uses them — and a regex layer key
#: would give layers to every patch, defeating the walls-only rule. `$FOAM_CASE` and `a;b` fail
#: here too, measured by the review's probes.
LAYER_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

#: The two layer modes (brief M-504), from `stokes.mesh.model` — the validator's own vocabulary,
#: imported so the writer and the model cannot drift.
LAYER_MODES = ("final_ratio", "first_height")

#: The relaxed `meshQualityControls`' `maxNonOrtho`, in degrees - the propeller tutorial's 75,
#: above the ordinary 65..70 bar, so the last layer iterations accept a worse mesh instead of
#: dropping the layers. (The sub-dictionary carries only the keys that differ; the rest are
#: found by the quality check's recursive lookup in the parent dictionary.)
LAYER_RELAXED_NON_ORTHO = 75

#: `snapControls`. The first four are REQUIRED by OF14 (`lookup`, not `lookupOrDefault` in
#: `snapParameters.C:33-36`), so they are always emitted. The two feature switches are optional but
#: written explicitly so the dictionary records what was intended.
SNAP_CONTROLS: tuple[tuple[str, Any], ...] = (
    ("nSmoothPatch", 3),
    ("tolerance", 2.0),
    ("nSolveIter", 50),
    ("nRelaxIter", 5),
    ("nFeatureSnapIter", 10),
    # `implicitFeatureSnap` defaults to FALSE in OF14 and we want it on: it snaps to features the
    # explicit edge extraction missed. `explicitFeatureSnap` defaults to true; set explicitly so a
    # reader does not have to know the default.
    ("implicitFeatureSnap", True),
    ("explicitFeatureSnap", True),
)


@dataclass(frozen=True)
class Surface:
    """One staged STL and the patch it becomes.

    ``name`` is the surface's name inside `geometry {}` and in `refinementSurfaces`; ``file`` is
    relative to :data:`GEOMETRY_DIR`. ``region`` is the name snappy gives the resulting patch -
    the user's tag name - so a tag called `inlet` produces a patch called `inlet` without any
    renaming step.
    """

    name: str
    file: str
    region: str | None = None
    patch_type: str = "wall"
    level_min: int = 0
    level_max: int = 0
    #: The size the level pair answers to, in metres (brief M-501): the per-body size when the
    #: body set one, the wall/patch rule otherwise. **For the dry-run precheck**, which reports
    #: what was asked against what the levels achieve — only the snapshot builder knows the rule,
    #: so it is recorded here rather than re-derived.
    requested: float = 0.0


@dataclass(frozen=True)
class Feature:
    """One extracted feature-edge mesh, and the level cells along it are refined to."""

    file: str
    level: int


@dataclass(frozen=True)
class SnappySnapshot:
    """Everything one snappyHexMesh job needs, as a plain record (SI, frozen).

    ``inside_point`` is **computed and verified by the caller** (`stokes.geometry.inside`), never
    guessed here: keeping cells reachable from a wrong point silently meshes the wrong region.
    """

    # **`surfaces` and `features` default to empty so a BOX domain can be built**: that case has
    # no part and therefore no surface, and it is a valid snappy job (the background block is the
    # whole mesh). A box case is only rejected if the user asked for a shape and gave none.
    surfaces: tuple[Surface, ...] = ()
    #: MPI ranks for snappyHexMesh; one retains the serial case and steps.
    cores: int = 1
    features: tuple[Feature, ...] = ()
    background_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    background_max: tuple[float, float, float] = (1.0, 1.0, 1.0)
    background_cells: tuple[int, int, int] = (1, 1, 1)
    inside_point: tuple[float, float, float] = (0.5, 0.5, 0.5)
    base_size: float = 1.0
    max_cells: int = 5_000_000
    feature_angle: float = RESOLVE_FEATURE_ANGLE
    cells_between_levels: int = CELLS_BETWEEN_LEVELS
    snap: bool = True
    #: The quality limits. `max_non_ortho` is what `checkMesh` reports against; the value is the
    #: OF14 tutorials' 65 and is what the quality card's verdict is computed from.
    max_non_ortho: float = 65.0
    max_skewness: float = 4.0
    refinement_regions: tuple[dict[str, Any], ...] = field(default=())
    #: The resolved per-wall layer counts (brief M-504): surface name -> count, walls only, an
    #: override kept even at 0 (an explicit "none"), and `unassigned` present when it exists and
    #: inherits. `addLayers true` iff any count is above 0; the counts are the `layers {}` block.
    layer_counts: dict[str, int] = field(default_factory=dict)
    #: The settings that shape the counts: mode, expansion ratio, final ratio, first layer
    #: height (SI metres), minimum thickness ratio, feature angle. Written beside the counts
    #: only when a count is above 0, and hashed into the basis's `layers` part with them.
    layer_settings: dict[str, Any] = field(default_factory=dict)
    #: The per-body mesh controls that reached this mesh, as `{body name: {size, refinement}}`.
    #:
    #: **Present so the BASIS can see them** (brief M-501). `mesh_basis` hashes this record's
    #: fields, and without this one a per-body edit would leave the basis unchanged - Generate
    #: would reuse the old mesh and the new control would do nothing visible.
    body_mesh: dict[str, object] = field(default_factory=dict)
    #: relative path -> the ASCII STL to stage there, one per surface. **Filled by the snapshot
    #: builder**, which owns the tessellation; the writer only carries them into the case tree.
    stl_files: dict[str, str] = field(default_factory=dict)
    #: Which mesher built this case. **Read by the service off the snapshot**, exactly as it reads
    #: `MeshSnapshot.mesher`, so both records answer "who built you?" the same way and the driver
    #: registry can be asked without the caller carrying the setting separately.
    mesher: str = "snappy"
    #: The service reads these four off the snapshot to build the result node's `derives_from` and
    #: its warnings, so a driver's record has to carry them however it is shaped. They mean the same
    #: thing here as on `MeshSnapshot`: which settings and part the mesh derives from, which tag is
    #: which patch, and which tags could not be honoured.
    settings_id: str = ""
    domain_part: str | None = None
    side_tags: dict[str, str] = field(default_factory=dict)
    unmapped: tuple[str, ...] = ()
    #: The mesh BASIS reads these too (`stokes.mesh.basis.mesh_basis`), so a snappy record has to
    #: answer them or the hash cannot be computed and Generate dies before the job exists.
    #:
    #: **They are not blockMesh concepts borrowed for convenience** - each has a real meaning here:
    #: `bbox` is the background box (`background_min`/`background_max` in the form the basis
    #: expects), `sides` is empty because snappy has no box sides, `side_kinds` maps each surface's
    #: patch to its boundary type, and `part_basis` is the B-rep version the surfaces were made
    #: from - which DOES change the mesh, so it belongs in the hash.
    part_basis: str = ""
    side_kinds: dict[str, str] = field(default_factory=dict)
    tag_members: dict[str, Any] = field(default_factory=dict)
    #: The domain spelling (`part`, `box`, `enclosure`) - in the basis's `geometry` part, because
    #: the same part under `enclosure` is meshed from OUTSIDE with a grown box.
    domain: str = ""
    #: Tag name -> the sha1 of the tag's FULL sorted face-id list (brief M-501).
    #:
    #: **Computed by the snapshot builder in `_tag_faces`**, where the full list exists — never
    #: from `tag_members`, which is cut to 64 ids per tag for the result node and would miss a
    #: re-picked face beyond the 64th.
    tag_digests: dict[str, str] = field(default_factory=dict)
    #: The `snapControls` as written, in order — in the basis's `snap` part. **A field, not an
    #: import**: `mesh_basis` must stay importable without the dictionary machinery, so the
    #: builder copies the controls onto the record it hands the basis.
    snap_controls: tuple[tuple[str, Any], ...] = ()
    #: Surface name -> the triangles staged for it (brief M-501). The dry-run precheck reports
    #: each surface's size from this, so it never has to build STL text.
    surface_triangles: dict[str, int] = field(default_factory=dict)
    #: Surface name -> the CAD FACES it holds, not triangles (brief M-501). The precheck's patch
    #: list and the unassigned-face count are about faces — what a user tags and what a boundary
    #: condition attaches to.
    surface_faces: dict[str, int] = field(default_factory=dict)
    #: The settings' surface size the surfaces were resolved under (brief M-503), carried so the
    #: estimate's default matches the dictionary's inputs.
    surface_size: float = 0.0
    #: The DOMAIN's own box, un-grown (brief M-503): the part's bbox for a `part` domain, the
    #: grown box for `box`. The estimate's background term is the cells inside THIS box — snappy
    #: keeps only the cells around the part (the `insidePoint` region), not the whole background
    #: block, which is grown for castellation workspace and would over-count by about 100 times.
    domain_bbox: tuple[float, float, float, float, float, float] | None = None
    #: Surface name -> its tessellated area, in m² (brief M-503). The estimate's area term comes
    #: from the display tessellation, grouped by the same pass that stages the STL.
    surface_areas: dict[str, float] = field(default_factory=dict)
    #: Surface name -> the sizing inputs its target was resolved from (boundary size, body size,
    #: patch type), so the estimate re-resolves under tried sizes through the same precedence.
    surface_targets: dict[str, tuple[float | None, float | None, str]] = field(default_factory=dict)
    #: The 2D mode (ADR-0031) as `stokes.mesh.two_d.TwoD.record` gives it, with the extrusion's
    #: `thickness` resolved in metres; **empty in 3D**, so a 3D case is byte-identical. Planar
    #: only: the surfaces `front` and `back` are the slab's faces across the plane, and the case
    #: extrudes `front` to one layer, which makes `front` and `back` `empty`.
    two_d: dict[str, Any] = field(default_factory=dict)

    @property
    def bbox(self) -> tuple[float, float, float, float, float, float]:
        """The background box in `MeshSnapshot.bbox`'s order, for the shared mesh basis."""
        return (*self.background_min, *self.background_max)

    @property
    def sides(self) -> dict[str, str]:
        """**Empty, deliberately.** blockMesh maps a tag to one of six box sides; snappy follows
        each surface, so there is no side mapping - and reporting one would change the basis for a
        reason that does not exist.
        """
        return {}

    def surface_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.surfaces)


def write(snap: SnappySnapshot) -> dict[str, str]:
    """The case files for a snappyHexMesh job.

    Returns `system/blockMeshDict` (the background block), `system/surfaceFeaturesDict`,
    `system/snappyHexMeshDict` and `system/controlDict`.

    **No `fvSchemes` or `fvSolution`.** Measured: snappyHexMesh, blockMesh, surfaceFeatures and
    checkMesh read `controlDict` and their own dictionaries, and those two are in the tutorials only
    because those cases go on to solve. Emitting them would be two more files to keep valid for no
    reader.
    """
    _validate(snap)
    cores = checked_cores(snap.cores)
    from stokes.mesh.drivers.blockmesh import _control_dict

    files = {
        "system/blockMeshDict": _background_dict(snap),
        "system/surfaceFeaturesDict": _features_dict(snap),
        "system/snappyHexMeshDict": _snappy_dict(snap),
        "system/controlDict": _control_dict(),
    }
    if cores > 1:
        files["system/decomposeParDict"] = decompose_par_dict(cores)
    if snap.two_d:
        files["system/extrudeMeshDict"] = _extrude_mesh_dict(snap)
    # **The surfaces travel WITH the case.** snappyHexMesh reads them from `constant/geometry/`,
    # so a dictionary naming `inlet.stl` is useless unless the file is staged beside it; the two
    # come from one place so they cannot disagree.
    files.update(snap.stl_files)
    return files


def steps(snap: SnappySnapshot) -> list[dict[str, Any]]:
    """The job steps: background, features, castellated+snapped mesh, then quality.

    **`snappyHexMesh` runs with NO flag.** Measured against
    `tests/fixtures/of14/help/snappyHexMesh.txt`: `-overwrite` is listed as *"Deprecated option,
    this is now default behaviour"*, so passing it would use a deprecated spelling of the default.

    **`surfaceFeatures` is its own step** rather than a flag: it writes
    `constant/geometry/<name>.eMesh`, which `snappyHexMeshDict`'s `features` block then reads, so
    the ordering matters and the two are separate utilities.
    """
    cores = checked_cores(snap.cores)
    run: list[dict[str, Any]] = [
        {"name": "blockMesh", "argv": ["blockMesh"], "timeout_s": 600},
    ]
    run.append(
        {
            "name": "surfaceFeatures",
            "argv": ["surfaceFeatures"],
            "timeout_s": 900,
        }
    )
    if cores > 1:
        run.append(decompose_step(no_fields=True))
    run.append(
        {
            "name": "snappyHexMesh",
            "argv": ["snappyHexMesh"],
            **({"ranks": cores} if cores > 1 else {}),
            "timeout_s": 3600,
            # **The layer coverage table** (brief M-504): snappy's final `patch faces layers
            # thickness [%]` print, read by the snappy parser. With no layers requested the
            # parser still runs and reports an empty table, which is the same "nothing grew"
            # answer the service stores.
            "parse": "snappy",
        }
    )
    if cores > 1:
        run.append(reconstruct_mesh_step())
    if snap.two_d:
        # **One cell thick** (ADR-0031 decision 5, the OF14 tutorials' recipe): the slab's `front`
        # faces are extruded to one layer, and the `plane` model makes `front` and the exposed
        # `back` both `empty` itself (measured: checkMesh reports 2 solution directions), so no
        # createPatch is needed. Serial, on the reconstructed mesh, and **with no flag**:
        # overwriting `constant/polyMesh` is OF14's default (`setNoOverwrite.H`; the old
        # `-overwrite` is "Deprecated option, this is now default behaviour"), as for snappy.
        run.append({"name": "extrudeMesh", "argv": ["extrudeMesh"], "timeout_s": 600})
    run.append(
        {
            "name": "checkMesh",
            "argv": ["checkMesh"],
            "timeout_s": 600,
            "parse": "checkMesh",
        }
    )
    return run


def _is_first_height(settings: dict[str, Any]) -> bool:
    """**The one mode predicate** (the M-504 security review's F2): the validator and the writer
    must test the mode the same way, or an unknown mode skips the first-height guard while the
    writer treats it as absolute — the bypass the review measured.
    """
    return settings.get("mode") == "first_height"


def _finite(value: Any, what: str, low: float, high: float) -> float:
    """The value as a finite float in `low..high`, or a typed refusal naming the setting.

    **The writer does not trust values that skipped the hooks** (F2): a loaded `project.json`
    validates no parameters, so the ranges the `validate` hooks enforce on `parameter.set` are
    re-checked here, against the numbers that actually reach the dictionary. NaN fails both
    comparisons and is refused by the finiteness test first; 1e9 fails the range.
    """
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MeshInvalid(
            f"{what} must be a number; got {value!r}", details={"code": "mesh.invalid"}
        ) from exc
    if not math.isfinite(number) or not low <= number <= high:
        raise MeshInvalid(
            f"{what} must be between {low} and {high} (finite); got {number}",
            details={"code": "mesh.invalid"},
        )
    return number


def _validate_layers(snap: SnappySnapshot) -> None:
    """Refuse a layer request the dictionary could not carry (brief M-504, and the M-504
    security review's F2).

    `first_height` mode writes `firstLayerThickness` as a length; a height of 0 would ask
    snappy for layers with no thickness - it would add nothing, or thinner-than-possible
    slivers, and neither is what the user asked for. **Every value is re-checked against the
    range its hook enforces on `parameter.set`**: a loaded `project.json` skips the hooks, and
    the review measured `nSurfaceLayers 1000000000`, `expansionRatio 1e9` and
    `minThickness -1` reaching the dictionary from one.
    """
    if not any(count > 0 for count in snap.layer_counts.values()):
        return
    settings = snap.layer_settings
    for name, count in snap.layer_counts.items():
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 10:
            raise MeshInvalid(
                f"the layer count of {name!r} must be an integer between 0 and 10; got {count!r}",
                details={"code": "mesh.invalid"},
            )
    if settings.get("mode") not in LAYER_MODES:
        raise MeshInvalid(
            f"the layer mode {settings.get('mode')!r} is not one of {', '.join(LAYER_MODES)}",
            details={"code": "mesh.invalid"},
        )
    _finite(settings.get("expansion_ratio", 1.2), "the expansion ratio", 1.0, 2.0)
    _finite(settings.get("final_ratio", 0.3), "the final layer ratio", 0.05, 1.0)
    _finite(settings.get("min_thickness_ratio", 0.1), "the minimum thickness ratio", 0.01, 1.0)
    _finite(settings.get("feature_angle", 100.0), "the layer feature angle", 0.0, 180.0)
    if not _is_first_height(settings):
        return
    height = _finite(settings.get("first_layer_height", 0.0), "the first layer height", 0.0, 1.0)
    if height <= 0.0:
        raise MeshInvalid(
            "first-height layer mode needs a first layer height; "
            "set one or switch the layer mode back to final ratio",
            details={"code": "mesh.no_first_height"},
        )


def _validate(snap: SnappySnapshot) -> None:
    """Refuse what would produce a silently wrong mesh, naming the reason."""
    try:
        checked_cores(snap.cores)
    except (TypeError, ValueError) as exc:
        raise MeshInvalid(str(exc), details={"code": "mesh.invalid", "cores": snap.cores}) from exc
    _validate_layers(snap)
    if not snap.surfaces:
        raise MeshInvalid(
            "no surfaces to mesh: a snappyHexMesh case needs at least one",
            details={"code": "mesh.invalid"},
        )
    for surface in snap.surfaces:
        if surface.level_min < 0 or surface.level_max < surface.level_min:
            raise MeshInvalid(
                f"surface {surface.name!r} has level ({surface.level_min} {surface.level_max}); "
                "the max must be at least the min and neither may be negative",
                details={
                    "code": "mesh.invalid",
                    "surface": surface.name,
                    "level_min": surface.level_min,
                    "level_max": surface.level_max,
                },
            )
    lo, hi = snap.background_min, snap.background_max
    if any(high <= low for low, high in zip(lo, hi, strict=True)):
        raise MeshInvalid(
            f"the background box is empty or inverted: min {lo} max {hi}",
            details={"code": "mesh.invalid"},
        )
    point = snap.inside_point
    if any(not (low < p < high) for p, low, high in zip(point, lo, hi, strict=True)):
        # **The point must be inside the background box**, or snappy finds no cells to keep and
        # fails with a message about the mesh rather than about the point. Checked here so the
        # error names what is actually wrong.
        raise MeshInvalid(
            f"the inside point {point} is outside the background box {lo}..{hi}; "
            "snappy would keep no cells and fail with a less useful message",
            details={"code": "mesh.invalid", "point": list(point)},
        )
    cells = snap.background_cells
    if any(count < 1 for count in cells):
        raise MeshInvalid(
            f"the background block needs at least one cell per axis; got {cells}",
            details={"code": "mesh.invalid"},
        )
    # **Every declared surface must have a staged STL.** The dictionary names the files and the
    # agent stages them from `stl_files`; if the two disagree, `surfaceFeatures` dies on the host
    # with `Cannot read ".../unassigned.stl"` - measured, on the demo, from a face-id/triangle-count
    # mix-up that declared a surface whose file was never written. Catching it here names the
    # surface instead of making the user read an OpenFOAM log.
    staged = set(snap.stl_files)
    missing = [
        f"{GEOMETRY_DIR}/{s.file}"
        for s in snap.surfaces
        if f"{GEOMETRY_DIR}/{s.file}" not in staged
    ]
    if missing:
        raise MeshInvalid(
            "the case declares surfaces whose STL was not staged: " + ", ".join(sorted(missing)),
            details={"code": "mesh.invalid", "missing": missing},
        )
    total = cells[0] * cells[1] * cells[2]
    if total > snap.max_cells:
        raise MeshInvalid(
            f"the background block alone would have {total:,} cells, over the limit of "
            f"{snap.max_cells:,}; increase the base size or the limit",
            details={"code": "mesh.invalid", "cells": total, "max_cells": snap.max_cells},
        )


# ---------------------------------------------------------------------- the files


def _vec(values: tuple[float, float, float] | tuple[int, int, int]) -> Tuple_:
    return Tuple_(tuple(Raw(_num(v)) for v in values))


def _num(value: float | int) -> str:
    """A number as OpenFOAM writes it: no exponent where a plain decimal will do, and integers
    without a trailing `.0`, so a golden file diffs cleanly against a hand-written dictionary."""
    if isinstance(value, int):
        return str(value)
    if value == int(value) and abs(value) < 1e16:
        return f"{value:.1f}"
    # **A bounded number of significant digits.** `repr` writes the full binary expansion, so an
    # inside point computed from a float32 bbox came out as
    # `(0.0049999999999999975 0.010000000000000002 0.0149999995)` - correct, and unreadable in a
    # dictionary a user may open. 12 significant digits is far past what a mesh cell can resolve and
    # keeps the file legible.
    return f"{value:.12g}"


def _background_dict(snap: SnappySnapshot) -> str:
    """`system/blockMeshDict`: ONE hex block over the background box.

    **Every node goes through `stokes.compile.foam`** (the blockMesh driver's own pattern). My first
    version spliced the hex line and the boundary block into `render_file`'s output with
    `str.replace`, and it produced a dictionary with **no opening brace after `FoamFile`** and a
    `background` patch with **no `faces`** — measured: `blockMesh` refused it with
    `keyword faces is undefined in dictionary "background"`, and snappy then found no `polyMesh` at
    all. Building the nodes is what makes the model's own escaping, indentation and validation
    apply.

    The six faces are a single `patch` — measured: the motorBike case's background boundary is
    `type patch;` (`blockMeshDict:35-55`). **The faces must be listed**: snappy replaces the
    background patches with the ones it derives from the surfaces, but it reads the file first, so
    an entry without `faces` is a hard failure.

    Vertices are the standard OpenFOAM ordering (x fastest, then y, then z), which is what
    `hex (0 1 2 3 4 5 6 7)` refers to.
    """
    x0, y0, z0 = snap.background_min
    x1, y1, z1 = snap.background_max
    nx, ny, nz = snap.background_cells
    vertices = (
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    )
    # The six faces of a hex, as OpenFOAM numbers them: -x, +x, -y, +y, -z, +z.
    faces = (
        (0, 3, 7, 4),
        (1, 2, 6, 5),
        (0, 1, 5, 4),
        (3, 2, 6, 7),
        (0, 1, 2, 3),
        (4, 5, 6, 7),
    )
    body = Dict(
        (
            Entry("scale", Raw("1")),
            Entry("vertices", List_(tuple(_vec(v) for v in vertices))),
            Entry(
                "blocks",
                List_((Raw(f"hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)"),)),
            ),
            Entry("edges", List_(())),
            Entry("boundary", List_(_background_patches(snap, faces))),
        )
    )
    return render_file("blockMeshDict", body, location="system")


def _background_patches(
    snap: SnappySnapshot, faces: tuple[tuple[int, int, int, int], ...]
) -> tuple[Entry, ...]:
    """The background block's patches: one `background` of all six faces in 3D.

    **In 2D the two faces across the plane are `front` (high) and `back` (low)** (ADR-0031): the
    block spans exactly the slab there, so these faces ARE the 2D planes, and snappy keeps a
    background patch that still has faces after castellation - flat, because nothing snaps it.
    `extrudeMesh` then extrudes `front`.
    """

    def entry(name: str, chosen: tuple[tuple[int, int, int, int], ...]) -> Entry:
        return Entry(
            name,
            Dict(
                (
                    Entry("type", Word("patch")),
                    Entry(
                        "faces",
                        List_(tuple(Tuple_(tuple(Raw(str(i)) for i in f)) for f in chosen)),
                    ),
                )
            ),
        )

    if not snap.two_d:
        return (entry("background", faces),)
    from stokes.mesh.two_d import from_record

    two = from_record(snap.two_d)
    assert two is not None
    low, high = faces[2 * two.normal], faces[2 * two.normal + 1]
    rest = tuple(f for i, f in enumerate(faces) if i not in (2 * two.normal, 2 * two.normal + 1))
    return (
        entry("background", rest),
        entry(TWO_D_FRONT, (high,)),
        entry(TWO_D_BACK, (low,)),
    )


def _features_dict(snap: SnappySnapshot) -> str:
    """`system/surfaceFeaturesDict`: extract the sharp edges of every staged surface.

    Keys measured against `tutorials/incompressibleFluid/propeller/system/surfaceFeaturesDict`:
    `surfaces` (a list of file names) and `includedAngle`. **`includedAngle` is the angle between
    the two faces, so 150 degrees means a dihedral of 30** — the complement of the `feature_angle`
    used elsewhere in the case, and the value OF14's own tutorial uses. Using 30 here would extract
    almost nothing and the mesh would lose its sharp edges with no error.
    """
    files = List_(tuple(Quoted(foam_path(s.file)) for s in snap.surfaces))
    body = Dict(
        (
            Entry("surfaces", files),
            # **The complement of the sharp-edge threshold** (brief M-515): the utility
            # measures the angle BETWEEN the two faces, so a 30-degree threshold is an
            # included angle of 150. At the default the value is the constant above, and the
            # dictionary is byte-identical to today.
            Entry("includedAngle", Raw(_num(180.0 - snap.feature_angle))),
            Entry("trimFeatures", Dict((Entry("minElem", 10),))),
        )
    )
    return render_file("surfaceFeaturesDict", body, location="system")


def _add_layers_controls(snap: SnappySnapshot, layers_on: bool) -> Dict:
    """`addLayersControls`: the layer counts and how they are grown.

    **With no count the block is the empty one it always was** (`layers {}` alone): a project
    that never asked for layers gets the dictionary it always got, byte for byte. The keys
    people do not set in the guided flow are written only when layers are on - they are the
    tutorial defaults, recorded in the inventory, and writing them over an empty block would
    change every golden for no behaviour.

    **The thickness spelling follows the mode** (brief M-504): `final_ratio` keeps
    `relativeSizes true` (snappy's own default in every tutorial) and writes
    `finalLayerThickness` as the ratio of the final layer to the local cell size;
    `first_height` writes `relativeSizes false` and `firstLayerThickness` in metres. **One
    mode per dictionary**: `relativeSizes` is a single bool (`layerParameters.C:106`), so
    relative and absolute cannot be mixed per patch - which the settings' single `layer_mode`
    already enforces.

    **`minThickness` in `first_height` mode is the ratio of the first layer height**, not of
    the cell size: with `relativeSizes false` snappy reads `minThickness` as a length, and
    writing the raw ratio there would put a 0.1 m floor on a millimetre part and silently
    remove every layer. Scaling by the first layer height keeps the setting's meaning ("below
    a tenth of the thinnest layer, do not add") in both modes; the settings' doc says so.

    **An override of 0 writes NO entry**: snappy has no "zero layers" spelling - a patch
    missing from `layers {}` gets none - so the resolved 0 means "leave it out". The count
    table on the result still shows it as an explicit decision.
    """
    if not layers_on:
        return Dict((Entry("layers", Dict(())),))
    settings = snap.layer_settings
    mode = str(settings.get("mode", "final_ratio"))
    expansion = float(settings.get("expansion_ratio", 1.2))
    feature_angle = float(settings.get("feature_angle", 100.0))
    min_ratio = float(settings.get("min_thickness_ratio", 0.1))
    relative = mode == "final_ratio"
    thickness = (
        Entry("finalLayerThickness", Raw(_num(float(settings.get("final_ratio", 0.3)))))
        if relative
        else Entry(
            "firstLayerThickness",
            Raw(_num(float(settings.get("first_layer_height", 0.0)))),
        )
    )
    min_thickness = (
        Raw(_num(min_ratio))
        if relative
        else Raw(_num(min_ratio * float(settings.get("first_layer_height", 0.0))))
    )
    # **The angle keys keep the tutorial's integer spelling** (`featureAngle 100;`), so a golden
    # diff against a hand-written dictionary shows values, not formatting.
    half = feature_angle / 2.0
    # Whole values keep the tutorials' integer spelling (`featureAngle 100;`).
    whole_angle = int(feature_angle) if feature_angle == int(feature_angle) else feature_angle
    whole_slip = int(half) if half == int(half) else half
    # **The keys are bare words AND real surfaces of this mesh** (the M-504 security review's
    # F1): a tag whose faces another tag claimed has no surface, so snappy would warn "does not
    # match any patch" — and a name from a loaded `project.json` (which validates no names)
    # reached the dictionary unchecked, where `"(.*)"` (layers on EVERY patch) and `$FOAM_CASE`
    # passed the lint as keys, measured by the review. A name that is not a bare word is refused
    # with a typed error before any text is written; a well-formed name with no surface is
    # dropped — snappy cannot grow layers on a patch that does not exist.
    surfaces_known = {surface.name for surface in snap.surfaces}
    patch_entries: tuple[Entry, ...] = ()
    for name, count in sorted(snap.layer_counts.items()):
        if count <= 0:
            continue
        if not isinstance(name, str) or not LAYER_KEY_RE.fullmatch(name):
            raise MeshInvalid(
                f"the layer entry {name!r} is not a bare patch name; layers are written for "
                "named walls only",
                details={"code": "mesh.invalid", "patch": str(name)},
            )
        if name not in surfaces_known:
            continue
        patch_entries += (Entry(name, Dict((Entry("nSurfaceLayers", count),))),)
    return Dict(
        (
            Entry("relativeSizes", Word("true" if relative else "false")),
            Entry("layers", Dict(patch_entries)),
            Entry("expansionRatio", Raw(_num(expansion))),
            thickness,
            Entry("minThickness", min_thickness),
            *(Entry(key, value) for key, value in LAYER_FIXED),
            Entry("featureAngle", Raw(_num(whole_angle))),
            Entry("slipFeatureAngle", Raw(_num(whole_slip))),
            Entry("nRelaxedIter", LAYER_N_RELAXED_ITER),
        )
    )


def _snappy_dict(snap: SnappySnapshot) -> str:
    """`system/snappyHexMeshDict`: the whole of what snappyHexMesh does.

    **`addLayers true` iff a wall got a count** (brief M-504): with none the block is exactly
    what it always was - `addLayers false` and an empty `layers {}` - so the no-layer golden
    dictionary is byte-identical to the one before the layer controls existed. Emitting
    `addLayers true` with no `layers {}` block is accepted by the utility and does nothing,
    which would be a silent no-op.
    """
    geometry = Dict(tuple(_geometry_entry(s) for s in snap.surfaces))
    controls = _castellated_controls(snap)
    layers_on = any(count > 0 for count in snap.layer_counts.values())
    mesh_quality = _mesh_quality(snap, relaxed=layers_on)
    body = Dict(
        (
            Entry("castellatedMesh", Word("true")),
            Entry("snap", Word("true" if snap.snap else "false")),
            Entry("addLayers", Word("true" if layers_on else "false")),
            Entry("geometry", geometry),
            Entry("castellatedMeshControls", controls),
            Entry("snapControls", Dict(tuple(Entry(k, _value(v)) for k, v in SNAP_CONTROLS))),
            Entry("addLayersControls", _add_layers_controls(snap, layers_on)),
            Entry("meshQualityControls", mesh_quality),
            Entry("debug", 0),
            Entry("mergeTolerance", Raw("1e-06")),
        )
    )
    return render_file("snappyHexMeshDict", body, location="system")


def _value(value: Any) -> Any:
    """A Python value as a foam entry value."""
    if isinstance(value, bool):
        return Word("true" if value else "false")
    return value


def _geometry_entry(surface: Surface) -> Entry:
    """One `geometry {}` member: a triSurface, and the `regions {}` naming its patch.

    **The `regions` block is how the patch gets the user's name.** Without it the patch is named
    after the file stem, so a tag called `inlet` would appear as `inlet.stl` and the boundary
    conditions written for `inlet` would not match any patch.
    """
    inner: list[Entry] = [
        Entry("type", Word("triSurface")),
        Entry("file", Quoted(foam_path(surface.file))),
    ]
    if surface.region:
        # `regions { <region> { name <patch>; } }` - the patch is named after the user's tag.
        named = Dict((Entry("name", Word(surface.region)),))
        inner.append(Entry("regions", Dict((Entry(surface.region, named),))))
    return Entry(surface.name, Dict(tuple(inner)))


def _castellated_controls(snap: SnappySnapshot) -> Dict:
    """`castellatedMeshControls`: the part that decides what the mesh resolves.

    **`resolveFeatureAngle` plus the per-surface `level (min max)` pair IS the curvature
    refinement** — OF14 has no `curvatureLevel` key (verified; an emitted one is silently ignored).
    A cell that sees surface intersections at an angle above `feature_angle` is refined to the max
    of its surface's pair.
    """
    # **Each item is a bare `{ ... }` block**, which is what `features ( { file ...; level ...; } )`
    # is in OpenFOAM: an unnamed dictionary, not a keyed entry. A bare `Dict` renders with the
    # braces misaligned (the list shifts the body but the inner block was placed for a keyed
    # entry), so it is wrapped in `Raw`-free form by giving the list the dict as its value.
    features = List_(
        tuple(
            Dict((Entry("file", Quoted(foam_path(f.file))), Entry("level", f.level)))
            for f in snap.features
        )
    )
    surfaces = Dict(
        tuple(
            Entry(
                s.name,
                Dict(
                    (
                        Entry("level", Tuple_((Raw(str(s.level_min)), Raw(str(s.level_max))))),
                        Entry("patchInfo", Dict((Entry("type", Word(s.patch_type)),))),
                    )
                ),
            )
            for s in snap.surfaces
        )
    )
    regions = Dict(
        tuple(
            Entry(
                str(r.get("name", "region")),
                Dict(
                    (
                        Entry("mode", Word(str(r.get("mode", "inside")))),
                        Entry("levels", Tuple_(tuple(Raw(_num(v)) for v in r.get("levels", ())))),
                    )
                ),
            )
            for r in snap.refinement_regions
        )
    )
    return Dict(
        (
            Comment("Refinement: curvature via resolveFeatureAngle + level (min max)"),
            Entry("maxLocalCells", Raw("1000000")),
            Entry("maxGlobalCells", Raw(str(snap.max_cells))),
            Entry("minRefinementCells", MIN_REFINEMENT_CELLS),
            Entry("nCellsBetweenLevels", snap.cells_between_levels),
            Entry("features", features),
            Entry("refinementSurfaces", surfaces),
            Entry("resolveFeatureAngle", Raw(_num(snap.feature_angle))),
            Entry("refinementRegions", regions),
            # **`insidePoint`, not `locationInMesh`** (measured: both work, `insidePoint` is
            # preferred and is what every OF14 tutorial uses).
            Entry("insidePoint", _vec(snap.inside_point)),
            Entry("allowFreeStandingZoneFaces", Word("true")),
        )
    )


def _mesh_quality(snap: SnappySnapshot, *, relaxed: bool) -> Dict:
    """`meshQualityControls`, written **inline**.

    The `#includeEtc "caseDicts/mesh/generation/meshQualityDict"` form resolves (verified: the file
    exists at `etc/caseDicts/mesh/generation/` and is on the lint's allow-list), but the tutorials
    write the keys inline and so does this: a copied project is then self-contained and does not
    depend on an install's caseDicts layout.

    **`relaxed { maxNonOrtho 75; }` only when layers are on** (brief M-504, and the [F] flag's
    whole point). During the layer pass snappy reads the relaxed sub-dictionary once it has run
    past `addLayersControls`' `nRelaxedIter` iterations (verified in the install's source,
    `snappyLayerDriver.C:3145` reading `motionDict.subDict("relaxed")`, and
    `layerParameters.C:258` reading `nRelaxedIter` with `readIfPresent` - the default is
    `labelMax`, i.e. never relax), so with no layers the sub-dictionary would be dead text and
    is left out.

    **`relaxed` carries only the keys that differ** - here, just `maxNonOrtho` - and it works
    because the quality check reads with a recursive lookup (`checkMesh.C` calls
    `dict.lookup<scalar>(key, true)`, and a keyword missing in the sub-dictionary is found in
    its parent, which is this dictionary). That is exactly the propeller tutorial's shape: a
    `relaxed { maxNonOrtho 75; }` under a full `meshQualityControls`. The value is higher than
    the ordinary limit, so the last layer iterations accept a worse mesh rather than collapsing
    the layers.
    """
    entries = [
        Entry("maxNonOrtho", Raw(_num(snap.max_non_ortho))),
        Entry("maxBoundarySkewness", 20),
        Entry("maxInternalSkewness", Raw(_num(snap.max_skewness))),
        Entry("maxConcave", 80),
        Entry("minVol", Raw("-1e+30")),
        Entry("minTetQuality", Raw("1e-30")),
        Entry("minArea", Raw("-1")),
        Entry("minTwist", Raw("0.05")),
        Entry("minDeterminant", Raw("0.001")),
        Entry("minFaceWeight", Raw("0.05")),
        Entry("minVolRatio", Raw("0.01")),
        Entry("minTriangleTwist", Raw("-1")),
        Entry("nSmoothScale", 4),
        Entry("errorReduction", Raw("0.75")),
    ]
    if relaxed:
        entries.append(
            Entry(
                "relaxed",
                Dict((Entry("maxNonOrtho", Raw(_num(LAYER_RELAXED_NON_ORTHO))),)),
            )
        )
    return Dict(tuple(entries))


def foam_path(text: str) -> str:
    """The path as OpenFOAM writes it: **forward slashes on every platform**.

    A Windows path with backslashes inside a dictionary is read as an escape sequence, so this is
    the one place a platform difference could silently break a case that works on Linux.
    """
    return text.replace("\\", "/")


def snapped_levels(
    base: float, surfaces: tuple[tuple[str, float], ...], *, fine: float | None = None
) -> tuple[tuple[int, int], ...]:
    """`level (min max)` pairs for surfaces named by their target size, with the deviations.

    Returns the pairs in order. The caller pairs them with the surface names; a size whose level
    rounds by more than :data:`~stokes.mesh.levels.DEVIATION_WARNING` is the caller's warning to
    raise, which is why :func:`~stokes.mesh.levels.level_for` reports the deviation rather than
    only the level.
    """
    return tuple(level_pair(base, size, fine=fine) for _name, size in surfaces)


def level_warnings(base: float, surfaces: tuple[tuple[str, float], ...]) -> list[str]:
    """What to tell the user about levels that could not hit the requested size."""
    out: list[str] = []
    for name, size in surfaces:
        choice = level_for(base, size)
        if choice.warns:
            out.append(
                f"{name}: {size * 1000:.3f} mm resolves to {choice.achieved * 1000:.3f} mm "
                f"(level {choice.level}, {choice.deviation * 100:.0f} % off)"
            )
    return out


def cell_count(snap: SnappySnapshot) -> int:
    """The cell count the **background block alone** will produce.

    **The background block, not the final mesh.** Castellation refines cells where the surfaces
    pass, so the meshed count is larger and is only known after snappy runs; this is the number the
    result node can carry before the job starts, and it is the same meaning blockMesh's `cells` has
    (there, the two happen to be equal). The result's `cells` is corrected from the log once the
    job finishes.
    """
    nx, ny, nz = snap.background_cells
    return int(nx * ny * nz)


def patch_types(snap: SnappySnapshot) -> dict[str, str]:
    """Patch name -> `boundary` type word, for every patch the mesh will have.

    **The names are the user's tag names**, because each surface carries a `regions` entry naming
    its patch (`Surface.region`). A surface with no region falls back to its own name, which is
    what snappy does with the file stem.

    The quality card and the `0/` field writer both read this before the job runs, so a patch the
    user tagged must appear here even though the boundary file does not exist yet.
    """
    out: dict[str, str] = {}
    for surface in snap.surfaces:
        out[surface.region or surface.name] = surface.patch_type
    if snap.two_d:
        # The extrusion makes the slab's two faces `empty` (ADR-0031 decision 5).
        out[TWO_D_FRONT] = "empty"
        out[TWO_D_BACK] = "empty"
    return out


def _extrude_mesh_dict(snap: SnappySnapshot) -> str:
    """`system/extrudeMeshDict`: the slab's `front` patch, extruded one layer (ADR-0031).

    **Keys as the OF14 tutorials write them** (`incompressibleFluid/flowWithOpenBoundary`,
    `fluid/aerofoilNACA0012`): `constructFrom patch`, `extrudeModel plane` with a top-level
    `thickness`. `sourceCase` is **`"."`**, the case itself, because the agent's lint refuses a
    `$FOAM_CASE` expansion; `fileName(".")` resolves to the working directory the job runs in.
    **`flipNormals true`**: the layer grows from `front` back INTO the slab, so the 2D mesh
    occupies the slab's own thickness (measured: with `false` it lay one thickness outside it, z
    1..2 mm for a 0..1 mm slab). The `plane` model makes `front` and `back` both `empty`.
    """
    thickness = float(snap.two_d.get("thickness") or 0.0)
    if not thickness > 0 or not math.isfinite(thickness):
        raise MeshInvalid(
            f"the 2D extrusion needs a positive thickness; got {thickness}",
            details={"code": "mesh.invalid", "thickness": thickness},
        )
    body = Dict(
        (
            Entry("constructFrom", Word("patch")),
            Entry("sourceCase", Quoted(".")),
            Entry("sourcePatches", List_((Word(TWO_D_FRONT),))),
            Entry("exposedPatchName", Word(TWO_D_BACK)),
            Entry("extrudeModel", Word("plane")),
            Entry("thickness", Raw(_num(thickness))),
            Entry("flipNormals", Word("true")),
            Entry("mergeFaces", Word("false")),
        )
    )
    return render_file("extrudeMeshDict", body, location="system")


def diagonal(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> float:
    """The box diagonal, which a caller uses as the part's characteristic length."""
    return math.dist(lo, hi)
