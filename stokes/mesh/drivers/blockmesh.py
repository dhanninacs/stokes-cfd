"""The blockMesh driver: one hexahedral block over a box (T-145a, T-206).

## What blockMesh can and cannot do

blockMesh meshes **the bounding box**, in one block, with six sides. That is the whole of its
ability, and
it shapes everything here: a tag whose faces lie on a side of the box becomes a patch, and a tag
whose
faces lie *inside* the box cannot — the honest answer is `unmapped`, which T-207 turns into a
warning on
the result. The heat sink's `walls` is exactly that case, and pretending otherwise would produce
a mesh
whose boundary is not what the user tagged.

## The patch type is the boundary's kind, not this module's opinion

A side's patch type comes from `side_kinds`: a `wall` kind is `wall`, `symmetry` is
`symmetryPlane`, and everything else is `patch` — an inlet, an outlet, or a tag no boundary uses.

**A side no tag holds is a `wall` named `unassigned`** (the owner's rule, FIXES-B B4): an
explicit patch of type `wall`, so the boundary file has an entry for it and the `0/` fields carry
a `noSlip`/`zeroGradient` one. It is in `patch_types` like any other patch, never dropped.

**`symmetryPlane`, measured rather than guessed.** Both `symmetry` and `symmetryPlane` appear in
OF14's own
tutorials and both mesh and check clean. The decisive test is the solver: with a mesh using one
of each, it
reports `inconsistent patch and patchField types for patch type symmetryPlane and patchField
type symmetry`
— so the mesh and the `0/` field must agree. This driver writes `symmetryPlane` because the
condition
catalogue's own entry for symmetry is `ConditionSpec("symmetry", …, "plane")`: it means a flat
mirror plane, which is what a box side is. The word is `SYMMETRY_PATCH_TYPE`, shared with the
field writer (`stokes.compile.of14.boundaries`) so the two cannot drift apart.
"""

from __future__ import annotations

import math
from typing import Any

from stokes.compile.foam import (
    Dict,
    Entry,
    List_,
    Raw,
    Tuple_,
    Word,
    render_file,
)
from stokes.compile.snapshot import SYMMETRY_PATCH_TYPE
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.snapshot import SIDES, MeshSnapshot
from stokes.mesh.two_d import (
    AXIS,
    BACK,
    FRONT,
    FRONT_AND_BACK,
    TwoD,
    from_record,
    refuse_reserved,
    wedge_vertex,
)

#: Bumped when any emitted text changes. It enters the basis, so a cached mesh made by an older
#: driver is not mistaken for one this driver would produce.
DRIVER_VERSION = "blockmesh/1"

#: The patch a side gets when no tag holds it. Written explicitly — **not** as a `defaultPatch`
#: keyword, because an explicit entry is visible in the boundary file and T-147b reads that file.
DEFAULT_PATCH = "unassigned"

#: The type of that patch: a wall, so its `0/` condition is `noSlip`/`zeroGradient`.
DEFAULT_PATCH_TYPE = "wall"

#: The side faces, in the vertex order of the OpenFOAM `cavity` tutorial. Verified present in the
#: install's own dict; `checkMesh` is what proves the orientation is right.
SIDE_FACES: dict[str, tuple[int, int, int, int]] = {
    "xmin": (0, 4, 7, 3),
    "xmax": (2, 6, 5, 1),
    "ymin": (1, 5, 4, 0),
    "ymax": (3, 7, 6, 2),
    "zmin": (0, 3, 2, 1),
    "zmax": (4, 5, 6, 7),
}

#: A patch type per boundary kind. Anything not here is `patch` — an inlet, an outlet, or a tag no
#: boundary uses, which are all ordinary patches to blockMesh.
_PATCH_TYPE: dict[str, str] = {"wall": "wall", "symmetry": SYMMETRY_PATCH_TYPE}


def cells(snap: MeshSnapshot) -> tuple[int, int, int]:
    """The block's cell counts: `ceil(length / base_size)` per axis, never below 1.

    A length shorter than `base_size` still gets one cell: zero cells would be an empty block, which
    blockMesh refuses with a message about a face rather than about a size.
    """
    xmin, ymin, zmin, xmax, ymax, zmax = snap.bbox
    if snap.base_size <= 0:
        raise MeshInvalid(
            f"base_size must be positive; got {snap.base_size}",
            details={"code": "mesh.invalid", "base_size": snap.base_size},
        )
    counts = [
        max(1, math.ceil(length / snap.base_size))
        for length in (xmax - xmin, ymax - ymin, zmax - zmin)
    ]
    two = from_record(snap.two_d)
    if two is not None:
        # **One cell across the plane** (ADR-0031): what makes the mesh 2D for OpenFOAM.
        counts[two.normal] = 1
    return (counts[0], counts[1], counts[2])


def cell_count(snap: MeshSnapshot) -> int:
    """The total number of cells the mesh will have.

    **Separate from :func:`cells`, which returns the three per-axis counts.** The writer needs the
    trio for the `hex (…) (nx ny nz)` line; a caller reporting "how many cells" needs the product.
    The two drivers must agree on this function's return TYPE - a single int - and blockMesh
    returning a tuple here is what made `int(cells(snap))` raise `TypeError: int() argument must be
    a string … not 'tuple'` (measured, 23 tests).
    """
    nx, ny, nz = cells(snap)
    return int(nx * ny * nz)


def patch_type(kind: str) -> str:
    """The `boundary` type word for a boundary kind."""
    return _PATCH_TYPE.get(kind, "patch")


def write(snap: MeshSnapshot) -> dict[str, str]:
    """The case files: `system/blockMeshDict` and `system/controlDict`.

    **No `fvSchemes` or `fvSolution`.** Measured: `checkMesh` needs neither, and the driver runs
    `blockMesh` and `checkMesh` and nothing else. The solver's files are T-212's.
    """
    nx, ny, nz = cells(snap)
    total = nx * ny * nz
    if total > snap.max_cells:
        raise MeshInvalid(
            f"the mesh would have {total:,} cells, over the limit of {snap.max_cells:,}; "
            "increase the base size or the limit",
            details={"code": "mesh.invalid", "cells": total, "max_cells": snap.max_cells},
        )
    return {
        "system/blockMeshDict": _block_mesh_dict(snap, nx, ny, nz),
        "system/controlDict": _control_dict(),
    }


def steps(snap: MeshSnapshot) -> list[dict[str, Any]]:
    """The job steps: mesh, then check.

    **Serial**, because blockMesh has no parallel mode — which is why `cores` is not in the basis.

    `checkMesh` takes **no flags**: the captured log's own invocation is `Exec   : checkMesh`,
    and it reads `constant/polyMesh` from the working directory. It is a second step rather than
    something the service runs afterwards, so the quality report comes back through the job's
    `reports` the same way any other parser's output does.
    """
    del snap
    return [
        {"name": "blockMesh", "argv": ["blockMesh"], "timeout_s": 600},
        {
            "name": "checkMesh",
            "argv": ["checkMesh"],
            "timeout_s": 600,
            "parse": "checkMesh",
        },
    ]


# ---------------------------------------------------------------------- the files


def _block_mesh_dict(snap: MeshSnapshot, nx: int, ny: int, nz: int) -> str:
    """`system/blockMeshDict`, every node through `stokes.compile.foam`."""
    xmin, ymin, zmin, xmax, ymax, zmax = snap.bbox
    vertices = [
        (xmin, ymin, zmin),
        (xmax, ymin, zmin),
        (xmax, ymax, zmin),
        (xmin, ymax, zmin),
        (xmin, ymin, zmax),
        (xmax, ymin, zmax),
        (xmax, ymax, zmax),
        (xmin, ymax, zmax),
    ]
    two = from_record(snap.two_d)
    if two is not None and two.axisymmetric:
        # **The box becomes a wedge** about the plane's first axis (ADR-0031 decision 4). Which
        # corners are on the normal's high side is read off the box, so the same `hex` line and
        # `SIDE_FACES` still describe the block.
        high = snap.bbox[two.normal + 3]
        vertices = [wedge_vertex(v, v[two.normal] == high, two) for v in vertices]
    body = Dict(
        (
            # **`scale 1.0`**, because the vertices are already in metres. `convertToMeters` is the
            # pre-14 spelling and OF14 ignores it, which would give a mesh 1000x the intended size.
            Entry("scale", 1.0),
            Entry("vertices", List_(tuple(Tuple_(v) for v in vertices))),
            Entry(
                "blocks",
                List_((Raw(f"hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)"),)),
            ),
            Entry("boundary", List_(_boundary_entries(snap))),
        )
    )
    return render_file("blockMeshDict", body, location="system")


def patches(snap: MeshSnapshot) -> dict[str, tuple[str, list[str]]]:
    """Every patch the mesh will have: name -> (type word, its sides).

    **The one place the rule lives**, used by the dictionary below and by the service's result, so
    the `boundary` file, `mesh_result.meta.patch_types` and the `0/` fields cannot disagree. A side
    no tag holds is the `unassigned` patch of type `wall` (`DEFAULT_PATCH_TYPE`).

    Names in **order of first appearance** in `SIDES`, not alphabetical, so the boundary file reads
    the way the box is described and a diff between two runs is about the model rather than about
    sorting.
    """
    two = from_record(snap.two_d)
    owned = _two_d_patches(snap, two)
    taken = {side for _, sides in owned.values() for side in sides}
    grouped: dict[str, list[str]] = {}
    for side in SIDES:
        if side in taken:
            continue
        tagged = snap.sides.get(side)
        if tagged == DEFAULT_PATCH:
            raise MeshInvalid(
                f"a tag called {DEFAULT_PATCH!r} holds the {side} side; that name is the patch "
                "of the sides nobody tagged, so give the tag another name",
                details={"code": "mesh.invalid", "side": side, "name": DEFAULT_PATCH},
            )
        grouped.setdefault(tagged or DEFAULT_PATCH, []).append(side)
    out: dict[str, tuple[str, list[str]]] = {}
    for name, sides in grouped.items():
        if all(side not in snap.sides for side in sides):
            out[name] = (DEFAULT_PATCH_TYPE, sides)
            continue
        kinds = {snap.side_kinds.get(side, "") for side in sides}
        # One patch holding two sides must have ONE type; a kind that disagrees across its own
        # sides is a model this driver cannot express, and saying so beats picking one.
        if len({patch_type(k) for k in kinds}) != 1:
            raise MeshInvalid(
                f"the patch {name!r} holds sides with different conditions "
                f"({', '.join(sorted(kinds))}); give it one kind",
                details={"code": "mesh.invalid", "patch": name, "kinds": sorted(kinds)},
            )
        out[name] = (patch_type(next(iter(kinds))), sides)
    if two is not None:
        refuse_reserved(out)
    out.update(owned)
    return out


def _two_d_patches(snap: MeshSnapshot, two: TwoD | None) -> dict[str, tuple[str, list[str]]]:
    """The 2D mesher's own patches (ADR-0031): no user tag can hold these sides.

    Planar: one `frontAndBack` patch of type `empty` over the two sides normal to the plane.
    Axisymmetric: `front` and `back` of type `wedge` (each its own patch, because a wedge patch
    must be one plane), and the low radial side as `axis` of type `empty` when it lies at radius
    0, where the wedge collapses it to a line.
    """
    if two is None:
        return {}
    low, high = two.normal_sides()
    if two.planar:
        return {FRONT_AND_BACK: ("empty", [low, high])}
    out = {FRONT: ("wedge", [high]), BACK: ("wedge", [low])}
    tol = 1e-9 * max(1.0, *(abs(v) for v in snap.bbox))
    if abs(snap.bbox[two.radial]) <= tol:
        out[AXIS] = ("empty", [two.radial_low_side()])
    return out


def patch_types(snap: MeshSnapshot) -> dict[str, str]:
    """Patch name -> the `boundary` type word, for every patch including `unassigned`."""
    return {name: kind for name, (kind, _) in patches(snap).items()}


def _boundary_entries(snap: MeshSnapshot) -> tuple[Any, ...]:
    """One entry per patch, in the order `patches` gives them."""
    return tuple(
        Entry(
            name,
            Dict(
                (
                    Entry("type", Word(kind)),
                    Entry("faces", List_(tuple(Tuple_(SIDE_FACES[s]) for s in sides))),
                )
            ),
        )
        for name, (kind, sides) in patches(snap).items()
    )


def _control_dict() -> str:
    """`system/controlDict`: the few keys a utility needs to run.

    **`writeFormat ascii` and `writeCompression off` are explicit** because T-147b reads the mesh
    blockMesh writes; leaving them to a default would make the reader depend on a default it
    cannot see.
    """
    body = Dict(
        (
            # A geometry utility has no solver, but `controlDict` must name one it can parse.
            Entry("solver", Word("incompressibleFluid")),
            Entry("startFrom", Word("startTime")),
            Entry("startTime", 0),
            Entry("stopAt", Word("endTime")),
            Entry("endTime", 0),
            Entry("deltaT", 1),
            Entry("writeControl", Word("timeStep")),
            Entry("writeInterval", 1),
            Entry("writeFormat", Word("ascii")),
            Entry("writeCompression", Word("off")),
        )
    )
    return render_file("controlDict", body, location="system")
