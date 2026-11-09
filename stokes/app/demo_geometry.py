"""The demo project's geometry: a flanged pipe with a flow-through bore, generated at startup.

**Generated, never committed.** `CLAUDE.md` §6 and §8 forbid generated artifacts and binaries in the
tree, and the IP gate would have to check a STEP file, so the pipe is built from public OCP
primitives
at the moment it is needed and written to the session's scratch directory.

The demo used to be a hollow shell: `pipe`, `inlet_flange` and `outlet_flange` were empty *groups*
with
no geometry behind them, and the tags were empty groups, so picking, glyph placement,
`boundary.assign` and `setup.check` all had nothing to work with. The viewport showed a synthetic
sample mesh instead, which carries **no face ids** by design (`stokes.geometry.sample`) and
therefore cannot be tagged or given a condition either.

**The flow path is the point.** The first version of this module had solid discs for flanges, which
would **block the pipe**: a flange with no bore is not a flange, and a fluid that cannot enter and
leave
is not a flow case. So:

* both flanges are **bored** to the pipe's internal diameter, so the passage runs end to end;
* the **fluid** body is a straight cylinder of the bore radius that fills the pipe and **extends
past
  both flange faces** by :data:`EXTENSION`. That extension is standard CFD practice — a case with
  inlet
  and outlet faces flush against the geometry has its boundaries sitting right on the feature, where
  the flow is not developed and the solution is least trustworthy. Extending the domain moves the
  inlet and outlet into open space, so the boundary conditions are applied where the flow is
  uniform;
* the extension is what makes the fluid's end faces *free* and therefore natural
  `velocity_inlet`/`pressure_outlet` boundaries, rather than faces buried inside the solid.

Everything is in **millimetres**, as a CAD file is; the importer converts.

**What the demo can and cannot be meshed into, because the owner asked.** `blockMesh` — the only
mesher this build has — is the bounding-box mesher: it puts ONE hexahedral block around the part and
calls the six sides patches. So the demo pipe meshes to a **box the size of the pipe**, not to the
pipe, and that is by design rather than a fault:

* every quality check passes (non-orthogonality 0.00, skewness ~1e-13, aspect ratio 1.00), because a
  box meshes perfectly — a clean quality card here says nothing about the pipe;
* `inlet` and `outlet` become patches because the fluid's end faces lie ON the box's sides;
* **`walls` cannot**, because the bore's curved surface is INSIDE the box. It is reported as a
  warning on the result ("tag walls lies inside the domain; blockMesh cannot mesh it") and its faces
  fall to the `unassigned` wall — 4,704 of them, measured, against 144 each for inlet and outlet.

A solve on that box still converges and still plots, which is what makes the tutorial useful; what
it does NOT show is flow through a pipe. That needs `snappyHexMesh`, which meshes a body inside a
background box and is the next mesher to be built (T-150). Until then the tutorial is honest about
being a box case, and `stokes/mesh/catalogue.py` says so in the mesher's own description.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stokes import workspace_root

# The pipe's dimensions in millimetres.
BORE = 60.0
"""The internal diameter: the flow passage, and the fluid body's diameter."""
WALL = 4.0
"""Wall thickness, so the outer diameter is ``BORE + 2 * WALL``."""
LENGTH = 300.0
"""The pipe's length, flange face to flange face."""
FLANGE_RADIUS = 50.0
FLANGE_THICKNESS = 14.0
EXTENSION = 80.0
"""How far the fluid extends past each flange face, in millimetres.

About one and a third bore diameters. Long enough that the inlet and outlet boundaries sit clear of
the flanges rather than on them, short enough to keep the case small. Its size is a modelling
choice,
not a measurement: a user would size it from the Reynolds number of their flow.
"""

# The palette, sRGB 0..1. Chosen so the three kinds of body read apart at a glance and the FLOW PATH
# is the thing that stands out, which is what this demo is about:
#
#   * the pipe body a light warm steel, the flanges a darker grey so the joint between them reads as
#     a joint rather than a continuous smear;
#   * the fluid a saturated blue, and it is the only translucent body (see `build_demo`), so the
#     bore stays legible through the metal instead of being hidden inside it.
#
# These are written into the STEP file as each body's surface colour (`brep.write_step`), so they
# survive the round trip rather than being re-applied by the demo after import.
PIPE_COLOR = (0.62, 0.65, 0.70)
FLANGE_COLOR = (0.38, 0.41, 0.45)
FLUID_COLOR = (0.20, 0.52, 0.86)

__all__ = [
    "EXTENSION",
    "FLANGE_COLOR",
    "FLUID_COLOR",
    "PIPE_COLOR",
    "build_demo_step",
    "pipe_parts",
]

"""The name the generated file is written under, which the part names follow."""


@dataclass(frozen=True)
class DemoPart:
    """One named body of the demo, in millimetres."""

    name: str
    shape: Any  # Any: TopoDS_Shape
    color: tuple[float, float, float] | None = None


def _fuse(shapes: list[Any]) -> Any:  # Any: list of OCP shapes
    """One shape from several, so a part is a single solid rather than a compound.

    A compound would import as several parts (`geometry.import_step` names one part per solid),
    which would put `pipe_2`, `pipe_3`… in the tree and make the demo harder to read than the thing
    it replaces. A union keeps one body per name.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    out = shapes[0]
    for other in shapes[1:]:
        out = BRepAlgoAPI_Fuse(out, other).Shape()
    return out


def _cylinder(
    radius: float, length: float, at: tuple[float, float, float]
) -> Any:  # Any: OCP shape
    """A cylinder along +Z, based at ``at``, in millimetres."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*at), gp_Dir(0.0, 0.0, 1.0)), radius, length
    ).Shape()


def _bored(shape: Any, radius: float, z0: float, z1: float) -> Any:  # Any: OCP shapes
    """Cut a cylindrical bore of ``radius`` through ``shape`` between ``z0`` and ``z1``.

    The bore cylinder is made slightly longer than the span asked for, so the cut passes right
    through
    and leaves clean openings at both ends rather than a thin disc of material where the tool
    stopped.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    over = 1.0
    tool = _cylinder(radius, (z1 - z0) + 2.0 * over, (0.0, 0.0, z0 - over))
    return BRepAlgoAPI_Cut(shape, tool).Shape()


def pipe_parts() -> list[DemoPart]:
    """The demo's four bodies: ``pipe``, ``inlet_flange``, ``outlet_flange`` and ``fluid``.

    * the **pipe** is a tube — an outer cylinder with the bore cut out — so it has an inner wall as
      well as an outer one, and it is *open at both ends*;
    * each **flange** is a disc with the same bore through it, so the passage is continuous from one
      end of the assembly to the other. Without the bore a flange would seal the pipe;
    * the **fluid** is one straight cylinder of the bore radius, spanning the whole assembly **plus
      :data:`EXTENSION` at each end**. It is a separate body rather than an `extract_fluid` result
      for
      the demo's purposes: the extraction needs a fully closed cavity to find, and the pipe as built
      is
      open at both ends so its bore is not a cavity. Running the operation would need the assembly
      sealed first, which is a different demonstration — the T-114 fixtures cover the cavity case.
    """
    half = LENGTH / 2.0
    inlet_z = -half - FLANGE_THICKNESS
    outlet_z = half
    bore_radius = BORE / 2.0

    pipe_outer = _cylinder(bore_radius + WALL, LENGTH, (0.0, 0.0, -half))
    pipe = _bored(pipe_outer, bore_radius, -half, half)

    inlet_flange = _bored(
        _cylinder(FLANGE_RADIUS, FLANGE_THICKNESS, (0.0, 0.0, inlet_z)), bore_radius, inlet_z, -half
    )
    outlet_flange = _bored(
        _cylinder(FLANGE_RADIUS, FLANGE_THICKNESS, (0.0, 0.0, outlet_z)),
        bore_radius,
        outlet_z,
        outlet_z + FLANGE_THICKNESS,
    )

    # The fluid: the bore's full length, extended into open space at both ends.
    fluid_z0 = inlet_z - EXTENSION
    fluid_z1 = outlet_z + FLANGE_THICKNESS + EXTENSION
    fluid = _cylinder(bore_radius, fluid_z1 - fluid_z0, (0.0, 0.0, fluid_z0))

    return [
        DemoPart("pipe", pipe, PIPE_COLOR),
        DemoPart("inlet_flange", inlet_flange, FLANGE_COLOR),
        DemoPart("outlet_flange", outlet_flange, FLANGE_COLOR),
        DemoPart("fluid", fluid, FLUID_COLOR),
    ]


def demo_folder() -> Path:
    """The durable folder the generated STEP lives in: the per-user home, not the project.

    **Why not the session's scratch folder** (Claude's defect 5): that folder is deleted when the
    project closes (`GeometryStore.close`), so a SAVED demo project pointed at a source file that no
    longer existed — the parts were there, the file they came from was not, and a re-import, a
    `check.brep` or a units change would fail on a project the user had only opened and saved.

    **`STOKES_HOME`, the repository's own convention** (Claude's defect 3). The first version used
    `$XDG_CACHE_HOME` and then `~/.cache`, which is a Unix location: on Windows there is no
    `~/.cache` and the path is not where anything else in this app writes. `stokes/app/__main__.py`
    already resolves `<STOKES_HOME or ~/.stokes>` for its log directory, so the demo uses the same
    rule and the two cannot disagree about where this application keeps its per-user data.

    Falls back to the temporary directory when there is no writable home at all: a machine with no
    home should still run the demo, and a rebuilt STEP costs about a second.
    """
    import os

    base = os.environ.get("STOKES_HOME")
    root = Path(base) if base else workspace_root.home()
    try:
        folder = root / "demo"
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = workspace_root.scratch("stokes-demo-")
    return folder


def demo_source_name() -> str:
    """The STEP's file name, which carries a hash of the geometry it holds.

    **A version number in the name was not enough** (Claude's defect 3). `DEMO_SOURCE_NAME` was
    a fixed `demo_pipe.step`, and the docstring claimed the name carried the generator's version —
    it did not. So every launch rewrote the same file: the timestamp changed, a saved project's
    source looked modified, and its tags went stale on the next open. Hashing the geometry itself
    means the name changes **exactly when the bytes would**, so a rebuild is a no-op when nothing
    changed and a new file when something did.
    """
    import hashlib

    # The hash covers everything that decides the geometry: the dimensions, the palette and the
    # names. Hashing the written bytes would mean writing them first, which is the work to avoid.
    # The hash covers the dimensions and the palette. **The names come from `pipe_parts` itself**,
    # not a second list: a duplicated list of names is a thing that drifts, and the cost of
    # building the shapes to read them is paid anyway by the write that follows. A caller that only
    # wants the name pays about a second; `build_demo_step` would have built them regardless.
    recipe = "|".join(
        [
            f"{BORE}:{WALL}:{LENGTH}:{FLANGE_RADIUS}:{FLANGE_THICKNESS}:{EXTENSION}",
            repr(PIPE_COLOR),
            repr(FLANGE_COLOR),
            repr(FLUID_COLOR),
            ",".join(part.name for part in pipe_parts()),
        ]
    )
    digest = hashlib.sha1(recipe.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"demo_pipe_{digest}.step"


def build_demo_step(folder: Path | None = None) -> Path:
    """Write the demo's geometry and return its path.

    ``folder`` defaults to :func:`demo_folder`. The file name carries a hash of the geometry
    (:func:`demo_source_name`), and **an existing file is never overwritten**: the same geometry
    gives the same name, so a second call finds the file already correct and returns it without
    writing. That is what keeps a saved project's source stamp stable across launches, so its tags
    do not go stale (Claude's defect 3).

    Raises on failure rather than returning a half-built demo: one whose geometry is missing would
    look like a bug in everything downstream of it, which is exactly what the old empty-group demo
    did.
    """
    from stokes.geometry.brep import StepSolidOut, write_step

    target = folder if folder is not None else demo_folder()
    target.mkdir(parents=True, exist_ok=True)
    path = target / demo_source_name()
    if path.exists():
        # **The whole point of hashing the name.** The geometry is already on disk, so there is
        # nothing to write and — crucially — the file's mtime does not move, so a project that names
        # it is not told its source changed.
        return path

    # Written to a private temporary name, then **renamed into place without ever replacing an
    # existing file.**
    #
    # `Path.replace` over a file another process holds open raises `PermissionError [WinError 5]` on
    # Windows — Claude confirmed it — and the first version let that exception escape `build_demo`,
    # so the demo raised instead of falling back. Two writers can also race between the `exists()`
    # above and the write. Both are handled: the temporary name is unique per process, the rename is
    # attempted, and a rename that loses the race is not an error because the winner wrote the same
    # bytes for the same hash.
    import os

    temporary = target / f".{path.name}.{os.getpid()}.tmp"
    try:
        write_step(
            [StepSolidOut(part.name, part.shape, part.color) for part in pipe_parts()],
            temporary,
        )
        try:
            temporary.replace(path)
        except OSError:
            # Another process got there first, or the target is held open on Windows. If the file is
            # now present the job is done; if it is not, the caller gets the error.
            if not path.exists():
                raise
    finally:
        temporary.unlink(missing_ok=True)
    return path


def demo_available() -> bool:
    """Whether the demo's geometry can be built here, i.e. whether OCP is importable.

    The demo is built from OCP primitives, so a build without the optional `cad` extra cannot make
    it. This is the check its callers make so the app **starts** on such a build and degrades to the
    geometry-free demo instead of failing to open the window (defect 5). It imports the one module
    inside the function, so importing `demo_geometry` costs nothing when the answer is used late.
    """
    import importlib.util

    return importlib.util.find_spec("OCP") is not None
