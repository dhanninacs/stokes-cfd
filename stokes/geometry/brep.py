"""B-rep checks and STEP export of parts (ADR-0020 decisions 6 and 7; ADR-0021 decision 5).

These run inside the geometry worker only: the B-rep lives in OCP and never reaches the API
process. :func:`check_brep` reports the validity and the geometry a mesher cares about (free
edges, unclosed shells, edges and faces below a size) and the face ids a UI can highlight, in the
part's own face numbering (:func:`stokes.geometry.operations.faces.ids_of`); :func:`write_step`
writes chosen parts, named by the caller, in metres.

The free-edge *findings* are counted by walking the shape's faces and asking how many of them each
edge belongs to, not through ``ShapeAnalysis_FreeBounds``: with ``splitclosed`` and ``splitopen``
set, that tool reports 0 free edges for an unsewn compound of loose faces (measured by T-027), so
an open surface model looked closed and no face id could be reported. The walk agrees with it on
shells and sees the loose faces; a degenerate edge (a sphere's pole, a cone's apex) is a point,
not a free edge. :func:`_free_edges` keeps the ``ShapeAnalysis_FreeBounds`` count for the
operations framework (:func:`stokes.geometry.operations.base.check_shape`), whose reports must not
change under a task about ``geometry.check``; the two deliberately differ on a loose compound.

Import rule: OCP inside the functions, as in :mod:`stokes.geometry.step`, so importing this
module never loads OCCT.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stokes.geometry.errors import GeometryFileError
from stokes.log import get_logger

log = get_logger(__name__)

SMALL_EDGE_M = 1e-6
"""Edges shorter than a micrometre usually mean wrong file units or a sliver."""
SMALL_FACE_M = 1e-6
"""Faces whose bounding box is under a micrometre across are degenerate for a mesher."""
MAX_FACE_IDS = 1000
"""Face ids one finding carries per list before it sets ``truncated``: a finding names the faces
a UI highlights, not a dataset."""
MM = 1e-3
"""Metres per millimetre: the unit :class:`~stokes.geometry.shapes.ShapeProvider` hands parts
back in, and so the unit :func:`write_step` takes its shapes in."""


@dataclass(frozen=True)
class BrepPart:
    """What a B-rep check found in one solid, in the part's own face numbering."""

    index: int
    name: str
    valid: bool
    free_edges: int
    open_shells: int
    small_edges: int
    small_faces: int
    free_edge_faces: tuple[int, ...] = ()
    """The faces a free edge belongs to (ADR-0021 decision 5), each at most once."""
    small_face_ids: tuple[int, ...] = ()
    """The faces whose bounding box is under a micrometre across."""
    small_edge_faces: tuple[int, ...] = ()
    """The faces that hold an edge under a micrometre long."""
    truncated: bool = False
    """True when a list above was cut at :data:`MAX_FACE_IDS`."""

    @property
    def ok(self) -> bool:
        return (
            self.valid
            and self.free_edges == 0
            and self.open_shells == 0
            and self.small_edges == 0
            and self.small_faces == 0
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "valid": self.valid,
            "free_edges": self.free_edges,
            "open_shells": self.open_shells,
            "small_edges": self.small_edges,
            "small_faces": self.small_faces,
            "free_edge_faces": list(self.free_edge_faces),
            "small_face_ids": list(self.small_face_ids),
            "small_edge_faces": list(self.small_edge_faces),
            "truncated": self.truncated,
            "ok": self.ok,
        }


def check_brep(shapes: Sequence[tuple[int, str, Any]], *, unit_scale: float) -> list[BrepPart]:
    """Check the given ``(index, name, shape)`` solids; ``unit_scale`` converts shape units to m."""
    return [_one(index, name, shape, unit_scale) for index, name, shape in shapes]


def _one(index: int, name: str, shape: Any, unit_scale: float) -> BrepPart:  # Any: TopoDS_Shape
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    from stokes.geometry.operations.faces import face_table, ids_of

    scale = unit_scale if unit_scale else 1.0
    edge_limit = SMALL_EDGE_M / scale
    face_limit = SMALL_FACE_M / scale

    valid = bool(BRepCheck_Analyzer(shape).IsValid())
    table = face_table(shape)
    edges = _walk_edges(shape)
    free = [(edge, owners) for edge, owners in edges if len(owners) < 2]
    small = [(edge, owners) for edge, owners in edges if _short(edge, edge_limit)]
    small_faces = _small_faces(shape, face_limit)
    open_shells = 0
    shells = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SHELL)
    while shells.More():
        if any(len(owners) < 2 for _edge, owners in _walk_edges(shells.Current())):
            open_shells += 1
        shells.Next()
    free_ids, cut = _capped(ids_of(shape, [f for _e, owners in free for f in owners], table=table))
    small_ids, cut = _capped(ids_of(shape, small_faces, table=table), cut)
    small_edge_ids, cut = _capped(
        ids_of(shape, [f for _e, owners in small for f in owners], table=table), cut
    )
    return BrepPart(
        index,
        name,
        valid,
        len(free),
        open_shells,
        len(small),
        len(small_faces),
        free_ids,
        small_ids,
        small_edge_ids,
        cut,
    )


def _walk_edges(shape: Any) -> list[Any]:  # Any: shape in, (TopoDS_Edge, [ancestor faces]) out
    """Each of ``shape``'s non-degenerate edges once, with the faces that use it (as many as its
    pcurves: the two sides of a seam are two entries, so a cylinder's seam is not a free edge).
    Walking the faces this way sees a loose face's edges, which ``ShapeAnalysis_FreeBounds`` does
    not (T-027)."""
    import OCP.collections as occ
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    if shape is None or shape.IsNull():
        return []
    ancestors = occ.IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(
        shape, TopAbs_ShapeEnum.TopAbs_EDGE, TopAbs_ShapeEnum.TopAbs_FACE, ancestors
    )
    found = []
    for i in range(1, ancestors.Extent() + 1):
        edge = TopoDS.Edge(ancestors.FindKey(i))
        if not _degenerate(edge):
            found.append((edge, list(ancestors.FindFromIndex(i))))
    return found


def _degenerate(edge: Any) -> bool:  # Any: TopoDS_Edge
    from OCP.BRep import BRep_Tool

    return bool(BRep_Tool.Degenerated_s(edge))


def _short(edge: Any, limit: float) -> bool:  # Any: TopoDS_Edge
    """Whether ``edge`` is shorter than ``limit`` (in shape units); a degenerate edge has no
    length and is not a small edge."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint

    try:
        return bool(GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge)) < limit)
    except Exception:  # OCCT's failures (Standard_NullObject ...) share no base class
        log.debug("edge length unavailable; not counted as small", exc_info=True)
        return False


def _small_faces(shape: Any, limit: float) -> list[Any]:  # Any: shape in, TopoDS_Face out
    """The faces whose bounding box is under ``limit`` (in shape units) across, in id order."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    found = []
    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
    while explorer.More():
        box = Bnd_Box()
        BRepBndLib.Add_s(explorer.Current(), box)
        if not box.IsVoid():
            lo, hi = box.CornerMin(), box.CornerMax()
            span = ((hi.X() - lo.X()) ** 2 + (hi.Y() - lo.Y()) ** 2 + (hi.Z() - lo.Z()) ** 2) ** 0.5
            if span < limit:
                found.append(explorer.Current())
        explorer.Next()
    return found


def _capped(ids: list[int], cut: bool = False) -> tuple[tuple[int, ...], bool]:
    """``ids`` as a tuple, cut at :data:`MAX_FACE_IDS`; the flag says a list was cut."""
    return tuple(ids[:MAX_FACE_IDS]), cut or len(ids) > MAX_FACE_IDS


def _free_edges(shape: Any, limit: float) -> int:  # Any: TopoDS_Shape
    """The ``ShapeAnalysis_FreeBounds`` count of free edges: kept for the operations framework
    (:func:`stokes.geometry.operations.base.check_shape`), whose reports do not change here. It
    returns 0 for an unsewn compound of loose faces (measured by T-027); :func:`_one` counts those
    through :func:`_walk_edges` instead."""
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopAbs import TopAbs_ShapeEnum

    try:
        bounds = ShapeAnalysis_FreeBounds(shape, limit, True, True)
        wires = (bounds.GetOpenWires(), bounds.GetClosedWires())
        return sum(_count(w, TopAbs_ShapeEnum.TopAbs_EDGE) for w in wires)
    except Exception:  # a shape OCCT cannot analyse is reported by `valid`
        return 0


def _count(compound: Any, kind: Any) -> int:  # Any: TopoDS_Shape, TopAbs_ShapeEnum
    """How many sub-shapes of ``kind`` a compound holds; zero for a null shape."""
    from OCP.TopExp import TopExp_Explorer

    if compound is None or compound.IsNull():
        return 0
    found = 0
    explorer = TopExp_Explorer(compound, kind)
    while explorer.More():
        found += 1
        explorer.Next()
    return found


@dataclass(frozen=True)
class StepSolidOut:
    """One solid to write: its name (the part node's), its shape in millimetres, and its colour.

    ``color`` is sRGB in 0..1, or None for the reader's default. It is written as the label's
    *surface* colour, which is the kind `step._label_color` reads first, so a file written here and
    read back keeps the colour it was given.
    """

    name: str
    shape: Any  # Any: TopoDS_Shape
    color: tuple[float, float, float] | None = None


def _in_metres(shape: Any) -> Any:  # Any: TopoDS_Shape in, TopoDS_Shape out
    """``shape`` (millimetres) scaled about the origin into metres."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    trsf.SetScaleFactor(MM)
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def write_step(solids: Sequence[StepSolidOut], path: Path) -> int:
    """Write ``solids`` (each in millimetres), named as the caller names them, to a STEP file in
    metres; returns the number written.

    OCCT's writer converts from its working unit (``xstep.cascade.unit``) to the unit the file
    declares (``write.step.unit``). Shapes are scaled to metres here and the working unit is set
    to metres to match, so the writer passes them through as they are: scaling them and letting
    OCCT convert a second time would write a file a thousand times too small.

    The working unit is process-wide, so the previous value is put back in a ``finally``: a
    worker runs one job per process and would not notice, but an in-process caller (a test, the
    CLI) would otherwise read every later STEP file a thousand times off.
    """
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.Interface import Interface_Static
    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_Controller
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    STEPControl_Controller.Init_s()  # registers the write.step.* and xstep.* parameters
    # **Both of these are process-wide, and BOTH are put back.**
    #
    # `write.step.unit` is the unit the written file DECLARES; `xstep.cascade.unit` is OCCT's
    # working unit while writing. `write_step` writes metres and sets both to M to pass the shapes
    # through.
    #
    # The `write.step.unit` line used to be set and never restored — only `xstep.cascade.unit` was
    # in the `finally` — so every STEP written later **in the same process** declared METRES. That
    # is not a theoretical hazard: a project whose geometry is written by `write_step` and then
    # re-imported in that process reads back a scale a thousand times too large. Claude's review saw
    # it as `params["units"] == "m"` where "mm" was expected, in a full parallel suite run and only
    # there, because whether it bites depends on which test wrote a STEP first.
    declared = Interface_Static.CVal_s("write.step.unit")
    previous = Interface_Static.CVal_s("xstep.cascade.unit")
    # **Both sets go inside the `try`, and BOTH are restored in the `finally`** (Claude's defect 9).
    #
    # The set of `write.step.unit` used to sit before the `try`, so if the SECOND set — the
    # `xstep.cascade.unit` one — raised, the first change was never undone and the process was left
    # with `write.step.unit = "M"` for the life of the process. That is the same thousand-fold
    # scale error the restore was added to prevent, reachable through the one path the restore did
    # not cover. Reading both first means the `finally` has something to put back either way.
    try:
        if not Interface_Static.SetCVal_s("write.step.unit", "M"):
            raise GeometryFileError("OCCT did not accept metres as the STEP unit")
        if not Interface_Static.SetCVal_s("xstep.cascade.unit", "M"):
            raise GeometryFileError("OCCT did not accept metres as its working unit")
        document = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
        XCAFApp_Application.GetApplication_s().InitDocument(document)
        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        color_tool = XCAFDoc_DocumentTool.ColorTool_s(document.Main())

        written = 0
        for solid in solids:
            label = shape_tool.AddShape(_in_metres(solid.shape), False)
            TDataStd_Name.Set_s(label, TCollection_ExtendedString(solid.name))
            if solid.color is not None:
                r, g, b = solid.color
                # `SetColor` is an INSTANCE method here (there is no `SetColor_s` in this OCP
                # build), and sRGB is what `_label_color` reads back. `ColorSurf` is the kind the
                # reader tries first — setting only the generic colour is read second and is the
                # fallback a viewer uses when no surface colour is present.
                color_tool.SetColor(
                    label,
                    Quantity_Color(
                        float(r), float(g), float(b), Quantity_TypeOfColor.Quantity_TOC_sRGB
                    ),
                    XCAFDoc_ColorType.XCAFDoc_ColorSurf,
                )
            written += 1

        writer = STEPCAFControl_Writer()
        writer.SetNameMode(True)
        writer.SetColorMode(True)
        writer.Transfer(document)
        status = writer.Write(str(path))
        # The status is an enum that is always truthy, so compare it with Done.
        if written and status != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise GeometryFileError(
                f"OCCT could not write {path.name}", details={"path": str(path)}
            )
        return written
    finally:
        Interface_Static.SetCVal_s("xstep.cascade.unit", previous)
        Interface_Static.SetCVal_s("write.step.unit", declared)
