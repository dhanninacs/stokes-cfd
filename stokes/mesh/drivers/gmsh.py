"""The Gmsh driver (ADR-0007 decision 5): tetrahedra from the part's own B-rep, via gmshToFoam.

Gmsh is GPL and is never linked or imported (ADR-0001): it runs as a separate process on a
generated script, exactly as the OpenFOAM utilities do. The job is::

    gmsh constant/geometry/mesh.geo -3 -o constant/geometry/mesh.msh -nt <cores>
    gmshToFoam constant/geometry/mesh.msh
    foamDictionary ... -set wall        (one per wall or symmetry patch)
    checkMesh

## Why the part goes in as STEP, not as the staged STL

The other surface meshers read the display tessellation as STL. Gmsh would have to re-split and
re-parametrise those triangles to remesh them, and the surfaces it makes then carry no link back to
the tags. Read through Gmsh's OpenCASCADE kernel instead, the part keeps its true curved surfaces
**and its face numbering**: OpenCASCADE numbers the faces of a solid in the same explorer order on
both sides, so Stokes face ``i`` is Gmsh surface ``i + 1`` and a tag's faces become one physical
group, which `gmshToFoam` turns into one patch named after the tag.

**Measured, 2026-11-20**, on a pipe tee, a four-branch manifold, a 90 degree bend and a 20-pin
channel (5 to 26 faces), exported with the geometry worker's `export_step` and read back by Gmsh
4.15.2: every surface's bounding box matched its Stokes face within 0.04 mm. Two settings matter:
`Geometry.OCCBoundsUseStl` (OpenCASCADE's quick box of a torus section was 7.8 mm loose) and
`Geometry.OCCTargetUnit` (the export is in metres; Gmsh would otherwise read STEP in millimetres).

## The script checks the numbering itself

The order is a property of OpenCASCADE, not a promise, so the script **verifies it before it
meshes**: the surface and volume counts, then every surface's bounding box against the face Stokes
measured. A mismatch is an `Error(...)`, which makes Gmsh exit non-zero, so the job fails with the
reason instead of writing a mesh whose inlet is somewhere else. Gmsh also reads a file it cannot
parse as zero surfaces with exit status 0 (measured with a binary B-rep), which the count check
turns into a failure.

## What it does not do

No boundary layers (`stokes.mesh.catalogue`: the catalogue says so and the user is told), a `part`
domain only, and 3D only. Per-surface sizes are honoured (`MeshSize` on each surface's points),
the base size is the largest element and curvature refines curved faces.

The `.geo` is written in a closed form the agent re-checks line by line
(`stokes_agent.lint_gmsh`), because a Gmsh script can run shell commands.
"""

from __future__ import annotations

import math
from typing import Any

from stokes.compile.parallel import checked_cores
from stokes.mesh.errors import MeshInvalid

#: Bumped when any emitted text changes; it enters the mesh basis.
DRIVER_VERSION = "gmsh/1"

#: Where the case's geometry lives. The script names the STEP by its bare file name: Gmsh resolves
#: `ShapeFromFile` against the script's own folder.
GEOMETRY_DIR = "constant/geometry"
STEP_NAME = "domain.step"
STEP_FILE = f"{GEOMETRY_DIR}/{STEP_NAME}"
GEO_FILE = f"{GEOMETRY_DIR}/mesh.geo"
MSH_FILE = f"{GEOMETRY_DIR}/mesh.msh"
#: The joined multi-solid STL, staged for the fidelity measurement only (Gmsh does not read it).
SURFACE_FILE = f"{GEOMETRY_DIR}/domain.stl"
BOUNDARY_FILE = "constant/polyMesh/boundary"

#: The physical volume's name: `gmshToFoam` makes it a cellZone.
VOLUME_NAME = "fluid"

#: Elements per 2 pi of curvature: a pipe of any diameter gets at least this many around.
CURVATURE_ELEMENTS = 16
#: The smallest element, as a fraction of the base size: the floor curvature refinement may reach.
MIN_SIZE_RATIO = 0.1
#: Gmsh's 2D (Frontal-Delaunay) and 3D (HXT, parallel Delaunay) algorithms.
ALGORITHM_2D = 6
ALGORITHM_3D = 10
#: How far a Gmsh surface's box may sit from the face Stokes measured, as a fraction of the part's
#: diagonal. The display tessellation lies inside the true surface, so the two differ by the
#: tessellation's deflection (measured at most 0.04 mm on a 0.33 m part); a numbering mismatch
#: moves a box by a feature's size, orders of magnitude more.
BOX_TOLERANCE = 2e-3

#: Seconds for each step.
VOLUME_TIMEOUT_S = 3600
CONVERT_TIMEOUT_S = 1800

#: The boundary types `gmshToFoam` does not write itself (it writes every patch as `patch`).
RETYPED: frozenset[str] = frozenset({"wall", "symmetry"})


def write(snap: Any) -> dict[str, str]:
    """`constant/geometry/mesh.geo`, `system/controlDict` and the joined surface (for fidelity).

    The STEP file is not here: the driver is pure, and exporting a B-rep needs the geometry worker.
    `geometry_exports` names it and the mesh service stages it.
    """
    validate(snap)
    from stokes.mesh.drivers.blockmesh import _control_dict

    files = {GEO_FILE: geo(snap), "system/controlDict": _control_dict()}
    files.update(snap.stl_files)
    return files


def geometry_exports(snap: Any) -> list[tuple[str, dict[str, Any]]]:
    """The B-rep files the service must export into the case: ``[(path, part spec)]``."""
    return [(STEP_FILE, dict(snap.gmsh["part_spec"]))]


def steps(snap: Any) -> list[dict[str, Any]]:
    """Mesh, convert, set the wall and symmetry types, check."""
    cores = checked_cores(snap.cores)
    run: list[dict[str, Any]] = [
        {
            "name": "gmsh",
            "argv": ["gmsh", GEO_FILE, "-3", "-o", MSH_FILE, "-nt", str(cores)],
            "timeout_s": VOLUME_TIMEOUT_S,
        },
        {"name": "gmshToFoam", "argv": ["gmshToFoam", MSH_FILE], "timeout_s": CONVERT_TIMEOUT_S},
    ]
    for position, (patch, kind) in enumerate(sorted(_retyped(snap).items())):
        run.append(
            {
                "name": "patchType" if position == 0 else f"patchType{position + 1}",
                "argv": [
                    "foamDictionary",
                    BOUNDARY_FILE,
                    "-entry",
                    f"entry0/{patch}/type",
                    "-set",
                    kind,
                ],
                "timeout_s": 120,
            }
        )
    run.append({"name": "checkMesh", "argv": ["checkMesh"], "timeout_s": 600, "parse": "checkMesh"})
    return run


def cell_count(snap: Any) -> int:
    """The cells a uniform fill of the domain's box at the base size would hold (an order of
    magnitude, as for cfMesh; `checkMesh` gives the real count)."""
    box = getattr(snap, "domain_bbox", None) or snap.bbox
    size = float(snap.base_size)
    if size <= 0:
        return 0
    return int(math.prod(max(1, math.ceil((box[i + 3] - box[i]) / size)) for i in range(3)))


def patch_types(snap: Any) -> dict[str, str]:
    """Patch name -> boundary type: the tag names, as the physical groups name them."""
    return {str(s.region or s.name): str(s.patch_type) for s in snap.surfaces}


def validate(snap: Any) -> None:
    """Refuse a record this driver cannot mesh, in the user's words."""
    if not getattr(snap, "gmsh", None):
        raise MeshInvalid(
            "the Gmsh record is missing its face groups; build it with build_gmsh_snapshot",
            details={"code": "mesh.invalid", "mesher": "gmsh"},
        )
    if any(int(n) > 0 for n in (snap.layer_counts or {}).values()):
        raise MeshInvalid(
            "Gmsh cannot grow boundary layers in this version. Set layers to 0, or choose "
            "snappyHexMesh, hex-dominant, poly-hexcore or polyhedral for layers.",
            details={"code": "mesh.layers_unsupported", "mesher": "gmsh"},
        )


# ------------------------------------------------------------------------------- the script


def geo(snap: Any) -> str:
    """The Gmsh script. **Every line has one of the shapes `stokes_agent.lint_gmsh` accepts**;
    a change here that adds a new shape must add it there, with its reason."""
    record = snap.gmsh
    boxes: list[list[float]] = [list(map(float, b)) for b in record["boxes"]]
    groups = {str(k): [int(i) for i in v] for k, v in record["groups"].items()}
    tolerance = max(BOX_TOLERANCE * float(record["diagonal"]), 1e-9)
    base = float(snap.base_size)
    lines = [
        f"// Stokes Gmsh driver {DRIVER_VERSION}: tetrahedra from the part's B-rep",
        'SetFactory("OpenCASCADE");',
        "Geometry.OCCAutoFix = 0;",
        "Geometry.OCCImportLabels = 0;",
        "Geometry.OCCBoundsUseStl = 1;",
        'Geometry.OCCTargetUnit = "M";',
        f'v() = ShapeFromFile("{STEP_NAME}");',
        "s() = Surface{:};",
        f"If (#s() != {len(boxes)} || #v() != 1)",
        f'  Error("the part did not read back as one solid with {len(boxes)} faces");',
        "EndIf",
    ]
    for index, box in enumerate(boxes, start=1):
        checks = " || ".join(
            f"Fabs(bb({k}) - {_num(box[k])}) > {_num(tolerance)}" for k in range(6)
        )
        lines += [
            f"bb() = BoundingBox Surface{{{index}}};",
            f"If ({checks})",
            f'  Error("surface {index} is not where Stokes measured face {index - 1}");',
            "EndIf",
        ]
    for name, surfaces in sorted(groups.items()):
        lines.append(f'Physical Surface("{name}") = {{{", ".join(map(str, surfaces))}}};')
    lines.append(f'Physical Volume("{VOLUME_NAME}") = v();')
    for name, size in sorted(_surface_sizes(snap).items()):
        sized = groups.get(name)
        if sized:
            lines.append(
                f"MeshSize{{ PointsOf{{ Surface{{{', '.join(map(str, sized))}}}; }} }} = "
                f"{_num(size)};"
            )
    lines += [
        f"Mesh.MeshSizeMax = {_num(base)};",
        f"Mesh.MeshSizeMin = {_num(base * MIN_SIZE_RATIO)};",
        f"Mesh.MeshSizeFromCurvature = {CURVATURE_ELEMENTS};",
        f"Mesh.Algorithm = {ALGORITHM_2D};",
        f"Mesh.Algorithm3D = {ALGORITHM_3D};",
        "Mesh.Optimize = 1;",
        "Mesh.MshFileVersion = 2.2;",
        "Mesh.Binary = 0;",
    ]
    return "\n".join(lines) + "\n"


def _surface_sizes(snap: Any) -> dict[str, float]:
    """Patch name -> its requested size, where one is set and finer than the base size."""
    base = float(snap.base_size)
    out: dict[str, float] = {}
    for surface in snap.surfaces:
        size = float(getattr(surface, "requested", 0.0) or 0.0)
        if 0.0 < size < base:
            out[str(surface.region or surface.name)] = size
    return out


def _retyped(snap: Any) -> dict[str, str]:
    """The patches whose type `gmshToFoam` gets wrong: walls and symmetry planes."""
    return {name: kind for name, kind in patch_types(snap).items() if kind in RETYPED}


def _num(value: float) -> str:
    """A number as the script writes it: plain, round-trippable, never `inf` or `nan`."""
    if not math.isfinite(value):
        raise MeshInvalid(f"a non-finite number cannot go into the Gmsh script: {value!r}")
    return repr(float(value))
