"""Gmsh: tetrahedra from the tagged surfaces, with optional prism layers on the walls (ADR-0043).

## A separate program, through files only

Gmsh is GPL. Stokes never imports it, links it or carries its code: this driver writes a `.geo`
script (Gmsh's own input language) beside the per-tag STLs the snappy record already stages, and the
agent runs ``gmsh system/domain.geo -`` as a process, then OpenFOAM's ``gmshToFoam`` on the `.msh`
it saved. Like every driver this runs nothing and touches no disk: a snapshot in, case files and
steps out.

## The script, and what was measured to need it (Gmsh 4.15.2, FoamWin's bundle, 2026-11-20)

* **One STL per tag, merged, then `Coherence Mesh`.** The per-tag STLs share their edge vertices
  but Gmsh reads each file's vertices as its own nodes: without the merge the 3D mesher failed with
  "a segment and a facet intersect" along every edge two tags share (measured on the duct).
* **`ClassifySurfaces` + `CreateGeometry`** split each tag's triangles at edges sharper than the
  settings' feature angle into patches Gmsh can parametrise and remesh at the requested sizes. The
  physical groups cannot be set before it (measured: it drops them) and it renumbers every surface,
  so **each tag's share is counted first**: the tag alone is classified, its surfaces counted and
  the model deleted (`Delete Model`). Classification keeps the original surfaces apart (two coplanar
  tags stayed two) and numbers the new surfaces in the order the files were merged, so tag *k*
  owns the next ``n_k`` surfaces; the script stops with an error if the counts do not add up.
* **Sizes**: the base size is `Mesh.MeshSizeMax`; a surface asking for less gets a `Distance` +
  `Threshold` field (from its size at the wall to the base size three base cells away). Gmsh's
  `MeshSize{PointsOf{...}}` does nothing on these discrete surfaces (measured: the walls came out
  at the base size).
* **Layers** are Gmsh's boundary-layer extrusion of the wall surfaces along their normals
  (`Extrude{Surface{...}; Layers{...}; Recombine;}` with no vector): prisms, the same stack on every
  wall. The heights are signed by the staged surface's orientation (measured: the extrusion follows
  the STL normals, so a flipped surface grows its layers outside the part). An opening (any tag
  that is not a wall) cannot keep its own surface under the layers' edge, so it is **rebuilt as a
  plane** bounded by the layers' inner edge (`Plane Surface`), and its patch is that plane plus the
  layers' side faces on it. That is why a layered mesh needs every opening planar, and every wall
  layered with the same count; both are refused up front.
* **The measured limit of the layers**: on a sharp *inside* corner between two walls (the duct's
  90 degree edges) the layer fronts fold into each other once the stack is thicker than about
  0.6 of the wall cell size (passed at 0.6, failed at 0.7 at 1 mm cells), and Gmsh stops with
  "overlapping facets" or "a segment and a facet intersect". On a smooth wall (a pipe) 1.5 times the
  cell size passed. `ScaleLastLayer` avoided the fold but squashed the last layer to an aspect ratio
  of 3,000 (checkMesh failed), so it is not used. `explain_failure` turns that error into advice.
  Below the limit the duct's layered mesh passes checkMesh but its cells beside those corners reach
  66 to 86 degrees of non-orthogonality (several stacks measured), over Stokes's 65 degree bar, so
  the result is a warning; the pipe's layered mesh is at 55 degrees.
* **`Mesh.OptimizeNetgen`** lowered the worst non-orthogonality of the tetrahedra (duct 65 to 55
  degrees, layered pipe 65 to 55); on the layered duct it moved either way, so it stays on.
* `gmshToFoam` writes every patch as `type patch` (measured), so a `foamDictionary` step per wall
  or symmetry patch sets the type, exactly as the cfMesh driver sets its symmetry planes back.

The agent checks the script line by line against a closed grammar (`stokes_agent.geo_lint`) before
anything runs: a `.geo` file can run shell commands, and only the statements this module writes
are allowed.

**Over 500 lines on purpose**: the script is written one statement per line, as literal text the
grammar can be read against, and splitting the writer from the refusals would put the two halves
of one measured contract in different files.
"""

from __future__ import annotations

import math
import re
from typing import Any

from stokes.compile.parallel import checked_cores
from stokes.mesh.errors import MeshInvalid

#: Bumped when any emitted text changes; it enters the mesh basis.
DRIVER_VERSION = "gmsh/2"

#: The meshers this driver serves.
MESHERS: tuple[str, ...] = ("gmsh",)

#: The script, the mesh Gmsh saves and the folder the STLs are staged in. The script refers to the
#: other two relative to its own folder, which is how Gmsh resolves `Merge` and `Save` (measured).
GEO_FILE = "system/domain.geo"
MSH_FILE = "constant/domain.msh"
GEOMETRY_DIR = "constant/geometry"
_FROM_GEO = "../"

#: The cell zone `gmshToFoam` makes from the one physical volume.
VOLUME_NAME = "domain"

#: The programs the job runs, in order, beyond the ones every supported install has.
PROGRAMS: tuple[str, ...] = ("gmsh", "gmshToFoam")

#: What the script prints before growing layers, followed by the count; the agent's Gmsh parser
#: reports it as `layers`, so a failed job can be explained without the settings.
LAYER_MARK = "stokes layers"

#: The step that sets a patch's type, and the file and values it may touch.
TYPE_STEP = "patchType"
BOUNDARY_FILE = "constant/polyMesh/boundary"
SET_TYPES: frozenset[str] = frozenset({"wall", "symmetry"})

#: Seconds for Gmsh and for the conversion. The duct meshes in under a second; an hour leaves room
#: for real parts without letting a wedged process hold the job for ever.
GMSH_TIMEOUT_S = 3600
CONVERT_TIMEOUT_S = 1800

#: Tetrahedra per base-size cube of the part's box (measured on the duct at 1.5 mm: 3,191 tets
#: in 593 cubes), for the pre-job order of magnitude only.
TETS_PER_CUBE = 5.4

#: How far a refined surface's size grows back to the base size, in base cells.
GRADING_CELLS = 3.0

#: The `Distance` field's samples per surface direction: enough to resolve the requested size
#: across the tag's extent, within bounds that keep the field cheap.
MIN_SAMPLING = 20
MAX_SAMPLING = 200

#: A patch name as Gmsh's physical name and OpenFOAM's patch name both accept it.
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

#: The flatness an opening must have to be closed by a plane under layers, relative to its extent.
PLANAR_TOLERANCE = 1e-6

_VERTEX_RE = re.compile(r"^\s*vertex\s+(\S+)\s+(\S+)\s+(\S+)\s*$", re.MULTILINE)


def write(snap: Any) -> dict[str, str]:
    """`system/domain.geo`, `system/controlDict` and the per-tag STLs, repaired.

    **The STLs are repaired before they are staged** (`stokes.geometry.surface_repair`): Gmsh keeps
    the wall it is given, so a surface that crosses itself, or a sealed pocket beside the body,
    stops it where the other meshers never notice.
    """
    _validate(snap)
    from stokes.geometry.surface_repair import repair
    from stokes.mesh.drivers.blockmesh import _control_dict

    staged = {
        f"{GEOMETRY_DIR}/{s.file}": snap.stl_files[f"{GEOMETRY_DIR}/{s.file}"]
        for s in snap.surfaces
    }
    repaired, _report = repair(staged)
    files = {GEO_FILE: geo_script(snap), "system/controlDict": _control_dict()}
    files.update(snap.stl_files)
    files.update(repaired)
    return files


def uses_tessellation(snap: Any) -> bool:
    """Whether the staged triangles are kept as the wall mesh instead of remeshed.

    **When the tessellation is already as fine as the sizes asked for** - its median edge at most
    the smallest requested size - remeshing could only coarsen it, and the remesh is the step that
    cannot cope with a lattice: on the gyroid exchanger (241,930 triangles, median edge 0.2 mm
    against 0.4 mm asked) Gmsh split the surface into 10,932 patches, took 7 minutes to remesh
    them and stopped with "Wrong topology of boundary mesh for parametrization". A CAD
    tessellation of a box or a pipe (edges of tens of millimetres) is remeshed as before.
    """
    import numpy as np

    lengths = []
    for surface in snap.surfaces:
        corners = _points(snap.stl_files.get(f"{GEOMETRY_DIR}/{surface.file}", ""))
        if len(corners) < 3:
            continue
        tris = corners[: len(corners) // 3 * 3].reshape(-1, 3, 3)
        lengths.append(np.linalg.norm(tris[:, [1, 2, 0]] - tris, axis=2).ravel())
    if not lengths:
        return False
    base = float(snap.base_size)
    asked = [float(s.requested) for s in snap.surfaces if 0.0 < float(s.requested) < base]
    return float(np.median(np.concatenate(lengths))) <= min(asked, default=base)


def steps(snap: Any) -> list[dict[str, Any]]:
    """Gmsh, the conversion, one type edit per wall or symmetry patch, then `checkMesh`.

    **Serial.** Gmsh's default 3D mesher runs in one thread; the settings' `cores` is checked but
    not spent.
    """
    checked_cores(snap.cores)
    run: list[dict[str, Any]] = [
        {
            "name": "gmsh",
            "argv": ["gmsh", "-nopopup", GEO_FILE, "-"],
            "timeout_s": GMSH_TIMEOUT_S,
            "parse": "gmsh",
        },
        {"name": "gmshToFoam", "argv": ["gmshToFoam", MSH_FILE], "timeout_s": CONVERT_TIMEOUT_S},
    ]
    typed = [(patch, kind) for patch, kind in patch_types(snap).items() if kind in SET_TYPES]
    for position, (patch, kind) in enumerate(typed):
        run.append(
            {
                "name": TYPE_STEP if position == 0 else f"{TYPE_STEP}{position + 1}",
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
    """About the tetrahedra a fill of the domain's box at the base size holds (before the job)."""
    box = getattr(snap, "domain_bbox", None) or snap.bbox
    size = float(snap.base_size)
    if size <= 0:
        return 0
    cubes = math.prod(max(1, math.ceil((box[i + 3] - box[i]) / size)) for i in range(3))
    return int(cubes * TETS_PER_CUBE)


def patch_types(snap: Any) -> dict[str, str]:
    """Patch name -> boundary type: the tag names, as the type steps leave them."""
    return {_patch(s): str(s.patch_type) for s in snap.surfaces}


def layered(snap: Any) -> int:
    """The layer count every wall grows, or 0 for a mesh without layers."""
    counts = {int(n) for n in snap.layer_counts.values() if int(n) > 0}
    return max(counts) if counts else 0


def explain_failure(report: dict[str, Any]) -> str | None:
    """A sentence for a failed Gmsh step, from its parser's report; None when it has nothing.

    The first error Gmsh printed, and - when the script grew layers (`LAYER_MARK`) and Gmsh says
    the surfaces it was to fill cross - the measured cause: layer fronts folding into each other
    at a sharp inside corner (the module docstring).
    """
    errors = [str(e) for e in report.get("errors") or [] if str(e).strip()]
    if not errors:
        return None
    first = errors[0].rstrip(".")
    layers = bool(report.get("layers"))
    text = " ".join(errors).lower()
    collided = any(
        phrase in text
        for phrase in (
            "overlapping facets",
            "segment and a facet intersect",
            "invalid boundary mesh",
        )
    )
    if layers and collided:
        return (
            f"Gmsh could not fill the mesh: {first}. The boundary layers fold into each other at a "
            "sharp inside corner when the stack is thicker than about 0.6 of the wall cell size; "
            "use fewer layers, a smaller final ratio or first layer height, or a larger wall size"
        )
    return f"Gmsh failed: {first}"


# --------------------------------------------------------------------------------------- the script


def geo_script(snap: Any) -> str:
    """The `.geo` text. Every statement is one the agent's grammar (`geo_lint`) accepts."""
    surfaces = list(snap.surfaces)
    count = layered(snap)
    angle = _angle_radians(snap)
    kept = uses_tessellation(snap)
    # **Remeshed**: classify at the feature angle and give each patch a parametrisation.
    # **Kept**: only split each tag into its connected pieces and give them boundary curves, which
    # the layers' side faces need; the triangles stay as they are.
    split = ["CreateTopology;"] if kept else [f"ClassifySurfaces{{{_num(angle)}, 1, 1, Pi}};"]
    out: list[str] = [
        f"// Stokes {DRIVER_VERSION}: tetrahedra with optional prism layers on the walls.",
        "Mesh.MshFileVersion = 2.2;",
        "Mesh.Binary = 0;",
        "Mesh.Optimize = 1;",
        "Mesh.OptimizeNetgen = 1;",
        "// Each tag's share of the surfaces, counted alone.",
    ]
    for index, surface in enumerate(surfaces):
        out += [
            f'Merge "{_stl(surface)}";',
            *split,
            "t() = Surface{:};",
            f"n_{index} = #t();",
            "Delete Model;",
        ]
    out.append("// All the tags, joined at their shared edges.")
    out += [f'Merge "{_stl(surface)}";' for surface in surfaces]
    total = " + ".join(f"n_{index}" for index in range(len(surfaces)))
    # **`CreateTopology` before classifying**: it puts a curve on every tag boundary, which
    # classification then keeps. Without it two tags meeting on a smooth surface were classified
    # as one patch once `Coherence Mesh` had joined them (measured: two coplanar tags became one,
    # and the exchanger's counts came out 10,932 against 10,935).
    joined = ["CreateTopology;"] if kept else ["CreateTopology;", *split, "CreateGeometry;"]
    out += [
        "Coherence Mesh;",
        *joined,
        "s() = Surface{:};",
        f"If (#s() != {total})",
        f'  Error("splitting gave %g surfaces where the tags hold %g", #s(), {total});',
        "EndIf",
        "k = 0;",
    ]
    for index in range(len(surfaces)):
        out += [f"tag_{index}() = s({{k:k + n_{index} - 1}});", f"k += n_{index};"]
    out += _sizes(snap, surfaces, count, kept)
    walls = [i for i, s in enumerate(surfaces) if str(s.patch_type) == "wall"]
    caps = [i for i, s in enumerate(surfaces) if str(s.patch_type) != "wall"]
    if count > 0:
        out += _layers(snap, surfaces, walls, caps, count)
    else:
        out += [
            "sl = newsl;",
            "Surface Loop(sl) = {s()};",
            "core = newv;",
            "Volume(core) = {sl};",
        ]
    for index, surface in enumerate(surfaces):
        group = f"side_{index}" if count > 0 and index in caps else f"tag_{index}"
        out.append(f'Physical Surface("{_patch(surface)}") = {{{group}()}};')
    volumes = "core, cells()" if count > 0 else "core"
    out += [
        f'Physical Volume("{VOLUME_NAME}") = {{{volumes}}};',
        "Mesh 3;",
        f'Save "{_FROM_GEO}{MSH_FILE}";',
    ]
    return "\n".join(out) + "\n"


def _sizes(snap: Any, surfaces: list[Any], count: int, kept: bool = False) -> list[str]:
    """The base size, and a `Distance` + `Threshold` field per surface that asks for less.

    A kept tessellation has no fields: its triangles are the wall cells, and the tetrahedra grow
    from them to the base size as Gmsh grows a volume from its boundary.
    """
    base = float(snap.base_size)
    out = [
        f"Mesh.MeshSizeMax = {_num(base)};",
        "Mesh.MeshSizeMin = 0;",
        "Mesh.MeshSizeFromCurvature = 0;",
    ]
    finer = [(i, s) for i, s in enumerate(surfaces) if 0.0 < float(s.requested) < base]
    if not finer or kept:
        return out
    out += ["Mesh.MeshSizeFromPoints = 0;", "Mesh.MeshSizeExtendFromBoundary = 0;"]
    # **Above the layers**: the requested size starts where the tetrahedra start.
    inner = sum(_thicknesses(snap, count)) if count > 0 else 0.0
    thresholds: list[int] = []
    field = 0
    for index, surface in finer:
        size = float(surface.requested)
        extent = _extent(snap.stl_files.get(f"{GEOMETRY_DIR}/{surface.file}", ""))
        sampling = int(min(MAX_SAMPLING, max(MIN_SAMPLING, math.ceil(2.0 * extent / size))))
        distance, threshold = field + 1, field + 2
        field = threshold
        thresholds.append(threshold)
        out += [
            f"Field[{distance}] = Distance;",
            f"Field[{distance}].SurfacesList = {{tag_{index}()}};",
            f"Field[{distance}].Sampling = {sampling};",
            f"Field[{threshold}] = Threshold;",
            f"Field[{threshold}].InField = {distance};",
            f"Field[{threshold}].SizeMin = {_num(size)};",
            f"Field[{threshold}].SizeMax = {_num(base)};",
            f"Field[{threshold}].DistMin = {_num(inner)};",
            f"Field[{threshold}].DistMax = {_num(inner + GRADING_CELLS * base)};",
        ]
    minimum = field + 1
    out += [
        f"Field[{minimum}] = Min;",
        f"Field[{minimum}].FieldsList = {{{', '.join(str(t) for t in thresholds)}}};",
        f"Background Field = {minimum};",
    ]
    return out


def _layers(
    snap: Any, surfaces: list[Any], walls: list[int], caps: list[int], count: int
) -> list[str]:
    """Extrude the walls inwards, rebuild each opening as a plane inside the layers, fill the core.

    `out()` holds, per extruded wall, its top surface, its layer volume and one side surface per
    boundary curve of the wall, in that curve's order; a side surface's boundary is the wall's
    curve, a side line, the top curve and a side line (measured). The openings' planes are made
    from the top curves, chained into one closed loop.
    """
    sign = -1.0 if _outward(snap, surfaces) else 1.0
    heights = []
    total = 0.0
    for thickness in _thicknesses(snap, count):
        total += thickness
        heights.append(_num(sign * total))
    wall_lists = ", ".join(f"tag_{i}()" for i in walls)
    out = [
        "// The layers: one stack on every wall, grown into the part.",
        # The log line the agent's parser reads, so a failure can be explained (`LAYER_MARK`).
        f'Printf("{LAYER_MARK} %g", {count});',
        "Geometry.ExtrudeReturnLateralEntities = 1;",
        f"walls() = {{{wall_lists}}};",
        "nb() = {};",
        "For w In {0:#walls() - 1}",
        "  b() = Boundary{Surface{walls(w)};};",
        "  nb() += #b();",
        "EndFor",
        f"out() = Extrude{{Surface{{walls()}}; Layers{{{{{', '.join('1' for _ in heights)}}}, "
        f"{{{', '.join(heights)}}}}}; Recombine;}};",
        "tops() = {};",
        "cells() = {};",
        "lsrc() = {};",
        "ltop() = {};",
        "lside() = {};",
        "i = 0;",
        "For w In {0:#walls() - 1}",
        "  tops() += out(i);",
        "  cells() += out(i + 1);",
        "  For j In {0:nb(w) - 1}",
        "    b() = Boundary{Surface{out(i + 2 + j)};};",
        "    lsrc() += Abs(b(0));",
        "    ltop() += Abs(b(2));",
        "    lside() += out(i + 2 + j);",
        "  EndFor",
        "  i += 2 + nb(w);",
        "EndFor",
        "caps() = {};",
    ]
    for index in caps:
        out += _cap(index, _patch(surfaces[index]))
    out += [
        "sl = newsl;",
        "Surface Loop(sl) = {tops(), caps()};",
        "core = newv;",
        "Volume(core) = {sl};",
    ]
    return out


def _cap(index: int, name: str) -> list[str]:
    """The plane closing one opening inside the layers, and the opening's patch (`side_<index>`)."""
    tag = f"tag_{index}"
    side = f"side_{index}"
    return [
        f"{side}() = {{}};",
        f"For p In {{0:#{tag}() - 1}}",
        f"  b() = Boundary{{Surface{{{tag}(p)}};}};",
        "  loop() = {};",
        "  flag() = {};",
        "  For q In {0:#b() - 1}",
        "    c = Abs(b(q));",
        "    hit = -1;",
        "    For m In {0:#lsrc() - 1}",
        "      If (lsrc(m) == c && hit < 0)",
        "        hit = m;",
        "      EndIf",
        "    EndFor",
        "    If (hit < 0)",
        f'      Error("the opening {name} meets no layered wall along curve %g", c);',
        "    EndIf",
        "    loop() += ltop(hit);",
        f"    {side}() += lside(hit);",
        "    flag() += 0;",
        "  EndFor",
        "  e() = Boundary{Curve{loop(0)};};",
        "  first = Abs(e(0));",
        "  tip = Abs(e(1));",
        "  ring() = {loop(0)};",
        "  flag(0) = 1;",
        "  For step In {1:#loop() - 1}",
        "    found = 0;",
        "    For q In {0:#loop() - 1}",
        "      If (flag(q) == 0 && found == 0)",
        "        e() = Boundary{Curve{loop(q)};};",
        "        If (Abs(e(0)) == tip)",
        "          ring() += loop(q);",
        "          tip = Abs(e(1));",
        "          flag(q) = 1;",
        "          found = 1;",
        "        ElseIf (Abs(e(1)) == tip)",
        "          ring() += -loop(q);",
        "          tip = Abs(e(0));",
        "          flag(q) = 1;",
        "          found = 1;",
        "        EndIf",
        "      EndIf",
        "    EndFor",
        "    If (found == 0)",
        f'      Error("the layers around the opening {name} do not make one closed edge");',
        "    EndIf",
        "  EndFor",
        "  If (tip != first)",
        f'    Error("the layers around the opening {name} do not close");',
        "  EndIf",
        "  cl = newcl;",
        "  Curve Loop(cl) = {ring()};",
        "  ps = news;",
        "  Plane Surface(ps) = {cl};",
        "  caps() += ps;",
        f"  {side}() += ps;",
        "EndFor",
    ]


def _thicknesses(snap: Any, count: int) -> list[float]:
    """Each layer's thickness from the wall outwards, in metres, from the settings' layer mode.

    `first_height`: the first layer is the given height and each next one the expansion ratio
    times the last. `final_ratio`: the LAST layer is that fraction of the wall cell size (snappy's
    relative sizing), and the ones below shrink by the ratio. Gmsh grows one stack on every wall, so
    the wall cell size is the smallest any wall asks for.
    """
    settings = dict(snap.layer_settings or {})
    ratio = float(settings.get("expansion_ratio", 1.2))
    if settings.get("mode") == "first_height":
        first = float(settings.get("first_layer_height", 0.0) or 0.0)
        return [first * ratio**k for k in range(count)]
    final = float(settings.get("final_ratio", 0.3)) * _wall_size(snap)
    return [final / ratio ** (count - 1 - k) for k in range(count)]


def _wall_size(snap: Any) -> float:
    """The smallest cell size a wall asks for (the base size when none asks for less)."""
    base = float(snap.base_size)
    sizes = [
        float(s.requested)
        for s in snap.surfaces
        if str(s.patch_type) == "wall" and 0.0 < float(s.requested) < base
    ]
    return min(sizes, default=base)


# ------------------------------------------------------------------------------------- validation


def validate(snap: Any) -> None:
    """The writer's refusals without writing, for the precheck."""
    _validate(snap)


def _validate(snap: Any) -> None:
    """Refuse what Gmsh would mesh wrongly or not at all, naming the reason."""
    if str(snap.mesher) not in MESHERS:
        raise MeshInvalid(
            f"the Gmsh driver does not build {snap.mesher!r}",
            details={"code": "mesh.invalid", "mesher": str(snap.mesher)},
        )
    try:
        checked_cores(snap.cores)
    except (TypeError, ValueError) as exc:
        raise MeshInvalid(str(exc), details={"code": "mesh.invalid", "cores": snap.cores}) from exc
    if not snap.surfaces:
        raise MeshInvalid(
            "no surface to mesh: Gmsh needs the part's closed surface",
            details={"code": "mesh.invalid"},
        )
    for surface in snap.surfaces:
        name = _patch(surface)
        if not NAME_RE.match(name) or not NAME_RE.match(str(surface.file).removesuffix(".stl")):
            raise MeshInvalid(
                f"the boundary {name!r} cannot be a Gmsh patch name: use letters, digits and "
                "underscores, starting with a letter",
                details={"code": "mesh.invalid", "surface": name},
            )
        if f"{GEOMETRY_DIR}/{surface.file}" not in snap.stl_files:
            raise MeshInvalid(
                f"the surface {name!r} was not staged",
                details={"code": "mesh.invalid", "surface": name},
            )
        if str(surface.patch_type) not in ("wall", "patch", "symmetry"):
            raise MeshInvalid(
                f"the boundary {name!r} is a {surface.patch_type!r} patch, which Gmsh's "
                "conversion cannot type",
                details={"code": "mesh.invalid", "surface": name},
            )
    base = float(snap.base_size)
    if not math.isfinite(base) or base <= 0.0:
        raise MeshInvalid(
            f"the base size must be a positive length; got {base!r}",
            details={"code": "mesh.invalid"},
        )
    from stokes.mesh.drivers.snappy import _validate_layers

    # **The same range checks as snappy's layers** (the M-504 review's F2): a loaded project
    # skips the parameter hooks, so the writer re-checks every value it is about to emit.
    _validate_layers(snap)
    if layered(snap) > 0:
        _validate_layered(snap)


def _validate_layered(snap: Any) -> None:
    """The two shapes Gmsh's layers need: one count on every wall, and planar openings."""
    counts = {
        _patch(s): int(snap.layer_counts.get(str(s.name), 0) or 0)
        for s in snap.surfaces
        if str(s.patch_type) == "wall"
    }
    if len(set(counts.values())) > 1:
        listed = ", ".join(f"{name} {n}" for name, n in sorted(counts.items()))
        raise MeshInvalid(
            "Gmsh grows one stack of layers on every wall, so every wall needs the same layer "
            f"count (now: {listed}). Set the same count on all walls, set layers to 0, or "
            "choose snappyHexMesh or a cfMesh type for per-wall layers",
            details={"code": "mesh.layers_unsupported", "mesher": "gmsh", "counts": counts},
        )
    if uses_tessellation(snap):
        from stokes.geometry.surface_repair import knife_folds

        # **Layers on a kept tessellation follow its every fold** (measured on the exchanger:
        # 8,152 edges folding back sharper than 25 degrees, and the layers crossed at once with
        # "overlapping facets"). A smooth fine surface layers well (a pipe: 34,560 prisms,
        # checkMesh OK), so only a surface with such folds is refused.
        folds = knife_folds(dict(snap.stl_files))
        if folds:
            raise MeshInvalid(
                f"Gmsh cannot grow layers on this surface: it is meshed on the part's own "
                f"triangles, and {folds} of their edges fold back sharper than 25 degrees, where "
                "the layers would cross. Set layers to 0, or choose snappyHexMesh or a cfMesh "
                "type for a layered mesh",
                details={"code": "mesh.layers_unsupported", "mesher": "gmsh", "folds": folds},
            )
    for surface in snap.surfaces:
        if str(surface.patch_type) == "wall":
            continue
        points = _points(snap.stl_files.get(f"{GEOMETRY_DIR}/{surface.file}", ""))
        if not _planar(points):
            raise MeshInvalid(
                f"the opening {_patch(surface)!r} is not planar: under boundary layers Gmsh "
                "closes each opening with a plane. Set layers to 0, or choose snappyHexMesh or "
                "a cfMesh type for this part",
                details={
                    "code": "mesh.layers_unsupported",
                    "mesher": "gmsh",
                    "surface": _patch(surface),
                },
            )


# ---------------------------------------------------------------------------------------- helpers


def _patch(surface: Any) -> str:
    """The patch a surface becomes: its tag name."""
    return str(surface.region or surface.name)


def _stl(surface: Any) -> str:
    """A staged STL as the script names it, relative to the script's folder."""
    return f"{_FROM_GEO}{GEOMETRY_DIR}/{surface.file}"


def _angle_radians(snap: Any) -> float:
    """The classification angle: the settings' feature angle (edges sharper than it are kept)."""
    degrees = float(getattr(snap, "feature_angle", 30.0) or 30.0)
    return math.radians(min(max(degrees, 1.0), 180.0))


def _points(text: str) -> Any:
    """The vertices of an ASCII STL as an (N, 3) array, in file order (three per triangle)."""
    import numpy as np

    rows = _VERTEX_RE.findall(text)
    if not rows:
        return np.zeros((0, 3), dtype=np.float64)
    return np.asarray(rows, dtype=np.float64)


def _extent(text: str) -> float:
    """The diagonal of an STL's bounding box, in metres (0 for an empty one)."""
    import numpy as np

    points = _points(text)
    if len(points) == 0:
        return 0.0
    return float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))


def _planar(points: Any) -> bool:
    """Every vertex on one plane, to `PLANAR_TOLERANCE` of the surface's extent."""
    import numpy as np

    if len(points) < 3:
        return False
    unique = np.unique(points, axis=0)
    if len(unique) <= 3:
        return True
    centred = unique - unique.mean(axis=0)
    normal = np.linalg.svd(centred, full_matrices=False)[2][-1]
    extent = float(np.linalg.norm(unique.max(axis=0) - unique.min(axis=0)))
    return float(np.abs(centred @ normal).max()) <= PLANAR_TOLERANCE * max(extent, 1e-300)


def _outward(snap: Any, surfaces: list[Any]) -> bool:
    """Whether the staged surfaces face out of the part: their signed volume is positive."""
    import numpy as np

    volume = 0.0
    for surface in surfaces:
        points = _points(snap.stl_files.get(f"{GEOMETRY_DIR}/{surface.file}", ""))
        if len(points) < 3:
            continue
        triangles = points[: len(points) // 3 * 3].reshape(-1, 3, 3)
        volume += float(
            np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum()
        )
    return volume >= 0.0


def _num(value: float) -> str:
    """A number as the script writes it: 12 significant digits, no binary noise."""
    text = f"{value:.12g}"
    return "0" if text == "-0" else text
