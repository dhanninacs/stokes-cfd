"""The M1 test set: ten synthetic STEP files plus one IGES, built with OCP (ADR-0020 decision 9;
T-034).

CLAUDE.md §6 and ``docs/engineering/testing.md`` allow only public or synthetic fixtures, and
CLAUDE.md §8 forbids committing generated artifacts. The set is therefore *generated*: every file
is written from an OCCT shape this module builds at test time into the caller's folder (a
``tmp_path``), so nothing binary enters the repository and the licence question cannot arise. The
files are plain OCCT output and carry no third-party content.

.. list-table:: What the ten files cover
   :header-rows: 1

   * - file
     - covers
   * - ``assembly.step``
     - an assembly of 24 named solids (one group, one part per solid)
   * - ``bracket.step``
     - a filleted body with four mounting holes: fillets and holes
   * - ``plate.step``
     - a plate with a 3 x 3 grid of through holes: many holes
   * - ``hollow_block.step``
     - a body with one internal void: a cavity to extract
   * - ``chamber.step``
     - a body with two internal voids: several cavities
   * - ``thin_walled.step``
     - a thin-walled box with 0.01 mm step faces: slivers
   * - ``stepped_block.step``
     - a box with a 0.01 mm step on one edge: slivers
   * - ``open_surface.step``
     - an open surface model: a shell missing one face
   * - ``pipe_tee.step``
     - an open pipe with three free loops: cap, then extract fluid
   * - ``sensor.step``
     - a solid housing of two fused bodies with bores: enclosure
   * - ``open_surface.iges``
     - the same open surface as IGES

Every shape is in OCCT model units (millimetres), the unit the import reads the files in, and every
STEP file declares millimetres. Imported only by tests that already skipped themselves when OCP is
missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests import geometry_fixtures as fx

EPS = 0.01  # mm: the step width the sliver repairs are asked to drop
FILLET = 4.0  # mm: the bracket's corner fillet radius
HOLE = 3.0  # mm: the bracket's mounting hole radius


@dataclass(frozen=True)
class Fixture:
    """One file of the set, and what the gate does with it."""

    name: str
    """The part/group name the file is imported under, and the stem of its file name."""
    suffix: str
    """``.step`` or ``.iges``."""
    covers: str
    """What this file is in the set for, in one line."""
    domain: str
    """``enclosure`` or ``extract_fluid``: the fluid-domain step the gate runs."""
    repair: tuple[str, ...] = ()
    """Repair operations to run after ``geometry.check``, in order."""
    domain_params: dict[str, Any] = field(default_factory=dict)
    """Extra SI parameters for the domain operation."""
    caps: bool = False
    """True when the recipe takes the repair's ``<name>_caps`` tag as the extraction's caps."""
    cavities: int = 0
    """How many internal voids a ``cavity`` extraction has to find."""
    finds: tuple[str, ...] = ()
    """Finding codes ``geometry.check`` must report before the repair runs."""
    closed_before: bool = True
    """False for the files that are open surfaces until the repair closes them."""

    @property
    def file_name(self) -> str:
        return self.name + self.suffix


# --------------------------------------------------------------------- shapes
def edges_of(shape: Any) -> list[Any]:  # Any: OCP TopoDS_Shape
    """The edges of ``shape`` in TopExp order."""
    return fx.subshapes(shape, "EDGE")


def assembly() -> list[tuple[str, Any, tuple[float, float, float] | None]]:
    """A 24-solid assembly: a base plate and 23 blocks, each on its own named label.

    The blocks differ in size and colour so the reader has to keep them apart, and the plate
    spans all of them: one file, many solids, one group of parts after the import.
    """
    colours = (fx.RED, fx.BLUE, fx.GREEN)
    parts: list[tuple[str, Any, tuple[float, float, float] | None]] = [
        ("base_plate", fx.box(60.0, 60.0, 4.0, at=(-30.0, -30.0, 0.0)), fx.BLUE)
    ]
    for index in range(23):
        column, row = index % 6, index // 6
        size = 6.0 + 0.5 * (index % 4)
        x, y = -27.0 + 10.0 * column, -27.0 + 10.0 * row
        parts.append(
            (
                f"post_{index + 1:02d}",
                fx.box(size, size, 8.0 + float(index % 3), at=(x, y, 4.0)),
                colours[index % 3],
            )
        )
    return parts


def _fillet_vertical(solid: Any, radius: float) -> Any:  # Any: OCP shape
    """``solid`` with every vertical (z-parallel) edge rounded at ``radius``."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.GeomAbs import GeomAbs_CurveType
    from OCP.TopoDS import TopoDS

    fillet = BRepFilletAPI_MakeFillet(solid)
    added = 0
    for edge in edges_of(solid):
        curve = BRepAdaptor_Curve(TopoDS.Edge(edge))
        if curve.GetType() != GeomAbs_CurveType.GeomAbs_Line:
            continue
        if abs(curve.Line().Direction().Z()) > 0.99:
            fillet.Add(radius, TopoDS.Edge(edge))
            added += 1
    assert added >= 4, f"the bracket has only {added} vertical edges to round"
    return fillet.Shape()


def _cut(shape: Any, tools: list[Any]) -> Any:  # Any: OCP shapes
    """``shape`` with every tool cut out of it, one at a time."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    for tool in tools:
        shape = BRepAlgoAPI_Cut(shape, tool).Shape()
    return shape


def bracket() -> Any:  # Any: OCP shape
    """A filleted 60 x 40 x 10 mm bracket with four 3 mm mounting holes."""
    body = fx.box(60.0, 40.0, 10.0, at=(-30.0, -20.0, 0.0))
    holes = [fx.cylinder(HOLE, 12.0, at=(x, y, -1.0)) for x in (-22.0, 22.0) for y in (-12.0, 12.0)]
    return _fillet_vertical(_cut(body, holes), FILLET)


def plate() -> Any:  # Any: OCP shape
    """A 60 x 60 x 4 mm plate pierced by nine 4 mm holes on a 20 mm grid."""
    body = fx.box(60.0, 60.0, 4.0, at=(-30.0, -30.0, 0.0))
    holes = [
        fx.cylinder(4.0, 6.0, at=(-20.0 + 20.0 * column, -20.0 + 20.0 * row, -1.0))
        for column in range(3)
        for row in range(3)
    ]
    return _cut(body, holes)


def hollow_block() -> Any:  # Any: OCP shape
    """A 40 mm cube with one 24 mm cubic cavity: one internal void."""
    return fx.hollow_box((40.0, 40.0, 40.0), (24.0, 24.0, 24.0))


def chamber() -> Any:  # Any: OCP shape
    """A 60 x 40 x 30 mm block with two separate cavities: two internal voids."""
    body = fx.box(60.0, 40.0, 30.0)
    voids = [
        fx.box(16.0, 16.0, 12.0, at=(8.0, 12.0, 9.0)),
        fx.box(12.0, 20.0, 10.0, at=(36.0, 10.0, 10.0)),
    ]
    return _cut(body, voids)


def thin_walled() -> Any:  # Any: OCP shape
    """A 0.5 mm-walled box whose ``+x +z`` edge carries a pair of ``EPS``-wide step faces.

    The step is the size a CAD kernel leaves behind; ``slivers`` at 1e-4 mm drops it and leaves the
    12 faces of the thin-walled box, which is what the repair is for.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    shell = BRepAlgoAPI_Cut(
        fx.box(40.0, 40.0, 40.0), fx.box(39.0, 39.0, 39.0, at=(0.5, 0.5, 0.5))
    ).Shape()
    notch = fx.box(1.0 + EPS, 42.0, 1.0 + EPS, at=(40.0 - EPS, -1.0, 40.0 - EPS))
    return BRepAlgoAPI_Cut(shell, notch).Shape()


def stepped_block() -> Any:  # Any: OCP shape
    """A 20 mm box with a ``EPS``-wide step cut out of one top edge."""
    return fx.stepped_box((20.0, 20.0, 20.0), EPS)


def open_surface() -> Any:  # Any: OCP shape
    """A 30 mm box's shell missing its ``+z`` face: an open surface model."""
    return fx.open_box((30.0, 30.0, 30.0))


def pipe_tee() -> Any:  # Any: OCP shape
    """The open pipe tee: a shell with three free loops, closed by ``cap`` before extraction."""
    return fx.pipe_tee(6.0, 40.0)


def sensor() -> Any:  # Any: OCP shape
    """A housing: a bored block fused to a cross barrel and standing on two feet.

    Three bodies fused into one solid, a vertical bore, a cross bore and a washer face: enough
    planar and cylindrical faces that the enclosure's wall tagging has real work to do.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    body = fx.box(50.0, 30.0, 20.0, at=(-25.0, -15.0, 0.0))
    barrel = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0.0, 0.0, 12.0), gp_Dir(1.0, 0.0, 0.0)), 9.0, 30.0
    ).Shape()
    feet = [
        fx.box(8.0, 8.0, 6.0, at=(-25.0, -4.0, -6.0)),
        fx.box(8.0, 8.0, 6.0, at=(17.0, -4.0, -6.0)),
    ]
    fused = BRepAlgoAPI_Fuse(BRepAlgoAPI_Fuse(body, barrel).Shape(), feet[0]).Shape()
    fused = BRepAlgoAPI_Fuse(fused, feet[1]).Shape()
    bores = [
        fx.cylinder(4.0, 60.0, at=(0.0, 0.0, -10.0)),
        BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(0.0, -20.0, 12.0), gp_Dir(0.0, 1.0, 0.0)), 5.0, 40.0
        ).Shape(),
    ]
    return _cut(fused, bores)


# --------------------------------------------------------------------- the set
FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        "assembly",
        ".step",
        "an assembly of 24 solids in one file",
        "enclosure",
        domain_params={"factors": [0.5] * 6, "relative": True},
    ),
    Fixture(
        "bracket",
        ".step",
        "fillets and mounting holes",
        "enclosure",
        domain_params={"factors": [0.5] * 6},
    ),
    Fixture(
        "plate",
        ".step",
        "many through holes in a thin plate",
        "enclosure",
        domain_params={"factors": [1.0] * 6},
    ),
    Fixture(
        "hollow_block",
        ".step",
        "one internal void",
        "extract_fluid",
        domain_params={"mode": "cavity"},
        cavities=1,
    ),
    Fixture(
        "chamber",
        ".step",
        "two internal voids",
        "extract_fluid",
        domain_params={"mode": "cavity"},
        cavities=2,
    ),
    Fixture(
        "thin_walled",
        ".step",
        "a thin-walled part with sliver faces",
        "enclosure",
        repair=("slivers",),
        domain_params={"factors": [0.5] * 6},
        finds=("geometry.sliver_triangles",),
    ),
    Fixture(
        "stepped_block",
        ".step",
        "a sliver step on a solid box",
        "enclosure",
        repair=("slivers",),
        finds=("geometry.sliver_triangles",),
    ),
    Fixture(
        "open_surface",
        ".step",
        "an open surface model (also as IGES)",
        "enclosure",
        repair=("cap",),
        finds=("geometry.free_edges",),
        closed_before=False,
    ),
    Fixture(
        "pipe_tee",
        ".step",
        "an open pipe: cap, then extract the fluid",
        "extract_fluid",
        repair=("cap",),
        domain_params={"mode": "region"},
        caps=True,
        finds=("geometry.free_edges",),
        closed_before=False,
    ),
    Fixture(
        "sensor",
        ".step",
        "a solid housing to enclose",
        "enclosure",
        domain_params={"factors": [0.5] * 6},
    ),
    Fixture(
        "open_surface",
        ".iges",
        "the open surface model as IGES",
        "enclosure",
        repair=("sew", "cap"),
        finds=("geometry.free_edges",),
        closed_before=False,
    ),
)

STEP_FILES = tuple(f for f in FIXTURES if f.suffix == ".step")


def build_shape(fixture: Fixture) -> list[tuple[str, Any, tuple[float, float, float] | None]]:
    """The named OCCT shapes ``fixture`` is written from."""
    match fixture.name:
        case "assembly":
            return assembly()
        case "bracket":
            return [("bracket", bracket(), fx.RED)]
        case "plate":
            return [("plate", plate(), fx.BLUE)]
        case "hollow_block":
            return [("hollow_block", hollow_block(), None)]
        case "chamber":
            return [("chamber", chamber(), None)]
        case "thin_walled":
            return [("thin_walled", thin_walled(), None)]
        case "stepped_block":
            return [("stepped_block", stepped_block(), None)]
        case "open_surface":
            return [("open_surface", open_surface(), None)]
        case "pipe_tee":
            return [("pipe_tee", pipe_tee(), None)]
        case "sensor":
            return [("sensor", sensor(), None)]
    raise AssertionError(f"no shape for {fixture.name!r}")  # pragma: no cover


def write(fixture: Fixture, folder: Path) -> Path:
    """Write ``fixture`` into ``folder`` and return the file's path.

    STEP goes through the XCAF writer, so the solids keep their names; the IGES goes through the
    plain writer, which is how a surface model usually reaches us.
    """
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / fixture.file_name
    shapes = build_shape(fixture)
    if fixture.suffix == ".iges":
        return fx.write_iges_shapes([shape for _name, shape, _rgb in shapes], target)
    return fx.write_named(shapes, target)


def write_set(folder: Path) -> list[tuple[Fixture, Path]]:
    """Write the whole set (ten STEP files and the IGES) into ``folder``."""
    return [(fixture, write(fixture, folder)) for fixture in FIXTURES]
