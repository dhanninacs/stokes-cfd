"""Synthetic geometry fixtures generated with OCP (public, generated geometry; CLAUDE.md §6).

STEP (``write_plain``, ``write_named``, ...), IGES (``write_iges``, ``write_iges_shapes``) and
BREP (``write_brep`` as text, ``write_brep_binary``). Imported only by tests that already skipped
themselves when OCP is missing. Every shape is in OCCT model units (millimetres) and written
with the shape's own placement.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

RED = (0.8, 0.2, 0.1)
BLUE = (0.1, 0.2, 0.9)
GREEN = (0.2, 0.7, 0.3)


def box(dx: float, dy: float, dz: float, at: tuple[float, float, float] = (0, 0, 0)) -> Any:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*at), dx, dy, dz).Shape()


def cylinder(radius: float, height: float, at: tuple[float, float, float] = (0, 0, 0)) -> Any:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), radius, height).Shape()


def moved(shape: Any, dx: float, dy: float, dz: float) -> Any:
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.TopLoc import TopLoc_Location

    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(dx, dy, dz))
    return shape.Moved(TopLoc_Location(trsf))


def compound(shapes: list[Any]) -> Any:
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    result = TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def write_plain(shape: Any, path: Path) -> Path:
    """A STEP file without names or colours (the reader falls back to solid_1 ...)."""
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone
    return path


def write_named(
    parts: list[tuple[str, Any, tuple[float, float, float] | None]], path: Path
) -> Path:
    """One free XCAF shape per (name, shape, colour); located shapes become small assemblies."""
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_StepModelType
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    for name, shape, rgb in parts:
        label = shapes.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        if rgb is not None:
            colors.SetColor(
                label,
                Quantity_Color(*rgb, Quantity_TypeOfColor.Quantity_TOC_sRGB),
                XCAFDoc_ColorType.XCAFDoc_ColorSurf,
            )
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone
    return path


def subsolid_document(named: bool = True) -> tuple[Any, Any]:
    """An XCAF document: one part ``bracket`` holding two solids, each with a sub-shape label.

    ``left_block`` is RED and ``right_block`` BLUE. With ``named=False`` no label gets a name
    (switch ``XCAFDoc_ShapeTool.SetAutoNaming_s`` off first, or OCCT names them itself).
    Returns ``(document, part label)``; keep the document alive while the label is in use.
    """
    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    left = box(10.0, 20.0, 30.0)
    right = box(4.0, 5.0, 6.0, at=(40.0, 0.0, 0.0))
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    XCAFApp_Application.GetApplication_s().InitDocument(doc)
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    part = shapes.AddShape(compound([left, right]), False)
    if named:
        TDataStd_Name.Set_s(part, TCollection_ExtendedString("bracket"))
    for name, solid, rgb in (("left_block", left, RED), ("right_block", right, BLUE)):
        sub = shapes.AddSubShape(part, solid)
        if named:
            TDataStd_Name.Set_s(sub, TCollection_ExtendedString(name))
        colors.SetColor(
            sub,
            Quantity_Color(*rgb, Quantity_TypeOfColor.Quantity_TOC_sRGB),
            XCAFDoc_ColorType.XCAFDoc_ColorSurf,
        )
    return doc, part


def write_subsolids(path: Path) -> Path:
    """The named :func:`subsolid_document` as STEP: two solids, each coloured on its own label.

    OCCT's writer keeps those colours (a styled item per solid) but writes the solids without
    names, so a file read back has coloured, nameless sub-shape labels under one named part.
    """
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_StepModelType

    doc, _part = subsolid_document()
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone
    return path


def write_iges(parts: list[tuple[str, Any, tuple[float, float, float] | None]], path: Path) -> Path:
    """One free XCAF shape per (name, shape, colour), as an IGES file of the named entities.

    OCCT's IGES exchange keeps entity names but drops the solid structure: each shape comes
    back as a compound of faces, so the reader's walk gives one ``shell`` body per shape.
    Colours set here do not round-trip through OCCT's own writer, so tests assert names, not
    colours.
    """
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.IGESCAFControl import IGESCAFControl_Writer
    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    for name, shape, rgb in parts:
        label = shapes.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        if rgb is not None:
            colors.SetColor(
                label,
                Quantity_Color(*rgb, Quantity_TypeOfColor.Quantity_TOC_sRGB),
                XCAFDoc_ColorType.XCAFDoc_ColorSurf,
            )
    writer = IGESCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    writer.Transfer(doc)
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone
    return path


def subshapes(shape: Any, kind: str) -> list[Any]:
    """The sub-shapes of ``shape`` of one kind (``"FACE"``, ``"SHELL"`` ...), in TopExp order."""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    found = []
    explorer = TopExp_Explorer(shape, getattr(TopAbs_ShapeEnum, f"TopAbs_{kind}"))
    while explorer.More():
        found.append(explorer.Current())
        explorer.Next()
    return found


def faces_of(shape: Any) -> list[Any]:
    """The faces of ``shape`` as separate shapes, in TopExp order."""
    return subshapes(shape, "FACE")


def write_iges_shapes(shapes: list[Any], path: Path, brep_mode: int = 0) -> Path:
    """The shapes as IGES through ``IGESControl_Writer.AddShape``: no names, no XCAF document.

    ``brep_mode`` 0 writes every shape as trimmed surfaces: a loose face comes back loose, and a
    solid comes back as a compound of faces. ``brep_mode`` 1 writes a solid as a manifold solid
    B-rep (entity 186), which comes back as a solid.
    """
    from OCP.IGESControl import IGESControl_Writer

    writer = IGESControl_Writer("MM", brep_mode)
    for shape in shapes:
        assert writer.AddShape(shape)
    writer.ComputeModel()
    assert writer.Write(str(path))
    return path


def write_brep(shape: Any, path: Path) -> Path:
    """The shape as a text BREP file (``BRepTools``): no names, no colours, no units."""
    from OCP.BRepTools import BRepTools

    assert BRepTools.Write_s(shape, str(path))
    return path


def write_brep_binary(shape: Any, path: Path) -> Path:
    """The shape as OCCT's binary BREP file (``BinTools``), which opens with ``Open CASCADE``."""
    from OCP.BinTools import BinTools

    assert BinTools.Write_s(shape, str(path))
    return path


def write_instanced(path: Path) -> Path:
    """An assembly ``fixture``: two instances ``bolt:1``/``bolt:2`` of one part and a plate."""
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_StepModelType
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    bolt = shapes.AddShape(cylinder(2.0, 10.0), False)
    TDataStd_Name.Set_s(bolt, TCollection_ExtendedString("bolt"))
    colors.SetColor(
        bolt,
        Quantity_Color(*GREEN, Quantity_TypeOfColor.Quantity_TOC_sRGB),
        XCAFDoc_ColorType.XCAFDoc_ColorGen,
    )
    plate = shapes.AddShape(box(40.0, 10.0, 2.0, (-10.0, -5.0, -2.0)), False)
    TDataStd_Name.Set_s(plate, TCollection_ExtendedString("plate"))
    assembly = shapes.NewShape()
    TDataStd_Name.Set_s(assembly, TCollection_ExtendedString("fixture"))
    for k in range(2):
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(20.0 * k, 0.0, 0.0))
        component = shapes.AddComponent(assembly, bolt, TopLoc_Location(trsf))
        TDataStd_Name.Set_s(component, TCollection_ExtendedString(f"bolt:{k + 1}"))
    shapes.AddComponent(assembly, plate, TopLoc_Location())
    shapes.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone
    return path


def relabel_units(source: Path, target: Path, si_unit: str) -> Path:
    """A copy of a millimetre STEP file that declares another unit, e.g. ``"$,.METRE."``."""
    text = source.read_text(encoding="utf-8")
    assert "SI_UNIT(.MILLI.,.METRE.)" in text
    target.write_text(text.replace("SI_UNIT(.MILLI.,.METRE.)", f"SI_UNIT({si_unit})"), "utf-8")
    return target


def hollow_box(
    outer: tuple[float, float, float] = (20.0, 20.0, 20.0),
    inner: tuple[float, float, float] = (10.0, 10.0, 10.0),
) -> Any:
    """A box with a smaller box cut out of its middle: one solid with one closed cavity.

    ``outer`` is the outer box's edge lengths (its lowest corner is the origin) and ``inner`` the
    cavity's, centred inside it. The cavity's volume is the inner box's, so a fluid extraction in
    ``cavity`` mode must find exactly that.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    middle = (
        (outer[0] - inner[0]) / 2.0,
        (outer[1] - inner[1]) / 2.0,
        (outer[2] - inner[2]) / 2.0,
    )
    return BRepAlgoAPI_Cut(box(*outer), box(*inner, at=middle)).Shape()


def pipe_tee(radius: float = 5.0, length: float = 30.0) -> Any:
    """A tee of three fused cylinders, open at all three ends: a shell with three free loops.

    The run pipe is two half-length cylinders along x (``-length/2`` to ``0`` and ``0`` to
    ``length/2``, through the origin) and the branch one half-length cylinder along z from the
    origin up. Fusing them gives a solid whose end faces are its only planar faces; the fixture
    removes them with ``BRepTools_ReShape``, leaving the shell a fluid extraction has to cap
    itself. Its three cylinders' union — what ``region`` mode has to fill — is
    ``pi r^2 length + pi r^2 length/2 - 8 r^3/3``: the overlap of the run and the branch is the
    half of Steinmetz's bicylinder (``16 r^3/3``) that the branch reaches.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.BRepTools import BRepTools_ReShape
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.TopoDS import TopoDS

    half = length / 2.0
    axis = gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(1.0, 0.0, 0.0))
    left = moved(BRepPrimAPI_MakeCylinder(axis, radius, half).Shape(), -half, 0.0, 0.0)
    right = BRepPrimAPI_MakeCylinder(axis, radius, half).Shape()
    branch = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)), radius, half
    ).Shape()
    tee = BRepAlgoAPI_Fuse(BRepAlgoAPI_Fuse(left, right).Shape(), branch).Shape()
    reshape = BRepTools_ReShape()
    for face in faces_of(tee):
        if BRepAdaptor_Surface(TopoDS.Face(face)).GetType() == GeomAbs_SurfaceType.GeomAbs_Plane:
            reshape.Remove(face)
    return TopoDS.Shell(subshapes(reshape.Apply(tee), "SHELL")[0])


def pin_grid(nx: int, ny: int) -> Any:
    """A compound of ``nx * ny`` pin fins (a box base and a cylinder each): 9 faces per pin."""
    pins = []
    for i in range(nx):
        for j in range(ny):
            x, y = i * 3.0, j * 3.0
            pins.append(box(2.5, 2.5, 1.0, (x, y, 0.0)))
            pins.append(cylinder(0.8, 6.0, (x + 1.25, y + 1.25, 1.0)))
    return compound(pins)


def open_box(size: tuple[float, float, float] = (20.0, 20.0, 20.0)) -> Any:
    """The box's shell with its ``+z`` face removed: an open shell with one free loop of 4 edges.

    ``size`` is the box's edge lengths and its lowest corner is the origin, so the removed face is
    the one whose plane sits at ``size[2]``. What an import of a surface model gives before the
    missing face is capped or the shell sewn shut.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools_ReShape
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.TopoDS import TopoDS

    shape = box(*size)
    reshape = BRepTools_ReShape()
    for face in faces_of(shape):
        surface = BRepAdaptor_Surface(TopoDS.Face(face))
        if (
            surface.GetType() == GeomAbs_SurfaceType.GeomAbs_Plane
            and abs(surface.Plane().Location().Z() - size[2]) < 1e-9
        ):
            reshape.Remove(face)
    return TopoDS.Shell(subshapes(reshape.Apply(shape), "SHELL")[0])


def loose_faces(size: tuple[float, float, float] = (20.0, 20.0, 20.0)) -> Any:
    """The six faces of a box as one compound, each a copy that owns its edges.

    What a surface model imported face by face looks like: no two faces share an edge, so the
    compound has 24 free edges (every edge belongs to one face) and is open even though its faces
    bound the box's volume. The count in :mod:`stokes.geometry.brep` reports none of them, which is
    why the repair operations count the free edges across the compound's faces themselves.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy

    return compound([BRepBuilderAPI_Copy(face).Shape() for face in faces_of(box(*size))])


def reversed_box(size: tuple[float, float, float] = (20.0, 20.0, 20.0)) -> Any:
    """The box built inside out: its volume is negative until the solid is oriented forward."""
    return box(*size).Reversed()


def tolerant_box(
    size: tuple[float, float, float] = (20.0, 20.0, 20.0), tolerance: float = 5.0
) -> Any:
    """The box with every vertex, edge and face tolerance raised to ``tolerance`` millimetres.

    A shape whose tolerances a reader left too large: ``BRep_Tool.MaxTolerance_s`` reports
    ``tolerance`` until a repair clamps it, and the geometry is the box all the same.
    """
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS

    shape = box(*size)
    builder = BRep_Builder()
    for kind in ("VERTEX", "EDGE", "FACE"):
        for sub in subshapes(shape, kind):
            if kind == "VERTEX":
                builder.UpdateVertex(TopoDS.Vertex(sub), tolerance)
            elif kind == "EDGE":
                builder.UpdateEdge(TopoDS.Edge(sub), tolerance)
            else:
                builder.UpdateFace(TopoDS.Face(sub), tolerance)
    return shape


def _shell_missing(shape: Any, drop: Callable[[Any], bool]) -> Any:
    """The first shell of ``shape`` with every face ``drop`` accepts removed (T-028).

    ``BRepTools_ReShape.Remove`` on each face, applied to the whole shape, then the shell that
    holds what is left: what an import of a surface model looks like when a face never came.
    """
    from OCP.BRepTools import BRepTools_ReShape
    from OCP.TopoDS import TopoDS

    reshape = BRepTools_ReShape()
    for face in faces_of(shape):
        if drop(face):
            reshape.Remove(face)
    return TopoDS.Shell(subshapes(reshape.Apply(shape), "SHELL")[0])


def _plane_at(face: Any, axis: int) -> float | None:
    """The coordinate of a planar face's plane along ``axis``, or None when it is not planar."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.TopoDS import TopoDS

    surface = BRepAdaptor_Surface(TopoDS.Face(face))
    if surface.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
        return None
    point = surface.Plane().Location()
    return float((point.X(), point.Y(), point.Z())[axis])


def open_cylinder(radius: float = 5.0, height: float = 20.0) -> Any:
    """A cylinder's shell with its ``+z`` end face removed: one free loop of one circular edge.

    The opening is planar, so a ``cap`` run closes it with ``BRepLib_FindSurface`` and a planar
    face, exactly as it closes a box missing a face; ``planar_only`` leaves a non-planar loop open,
    this one is capped either way.
    """
    return _shell_missing(cylinder(radius, height), lambda face: _plane_at(face, 2) == height)


def corner_open_box(size: tuple[float, float, float] = (20.0, 20.0, 20.0)) -> Any:
    """A box's shell missing its ``+z`` **and** ``+x`` faces: one **non-planar** free loop.

    The two openings share the edge at ``x = size[0]``, ``z = size[2]``, so their boundaries join
    into a single closed loop of six edges that spans ``z`` from 0 to ``size[2]``: no plane fits
    it, and ``cap`` can only close it with ``BRepFill_Filling`` unless ``planar_only`` leaves it
    open. What a smooth (non-planar) opening looks like.
    """
    return _shell_missing(
        box(*size),
        lambda face: _plane_at(face, 2) == size[2] or _plane_at(face, 0) == size[0],
    )


def split_cylinder(radius: float = 5.0, height: float = 20.0) -> Any:
    """A cylinder cut by the plane ``y = 0`` and put back together: its side is two faces.

    The two halves are cut from one cylinder and fused, so the result is the cylinder's volume
    again (six faces: two halves of the side on **one** cylindrical surface, the two end caps each
    split in two by the plane). ``unify`` merges the halves into one cylindrical face and gives
    three faces; keeping the two halves apart gives four. What ``unify`` is for.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

    solid = cylinder(radius, height)
    lower = BRepAlgoAPI_Cut(solid, box(60.0, 30.0, 90.0, at=(-30.0, 0.0, -30.0))).Shape()
    upper = BRepAlgoAPI_Cut(solid, box(60.0, 30.0, 90.0, at=(-30.0, -30.0, -30.0))).Shape()
    return BRepAlgoAPI_Fuse(lower, upper).Shape()


def stepped_box(size: tuple[float, float, float] = (20.0, 20.0, 20.0), step: float = 0.01) -> Any:
    """A box with a ``step``-wide slot cut out of one top edge: tiny step faces on its corner.

    The notch takes ``step`` off both ``+x`` and ``+z`` along the whole width, so the box gains a
    pair of faces ``step`` wide. ``slivers`` at a precision well above ``step`` drops them and
    gives the six faces of the box again; the repair of a step a CAD kernel left in.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    notch = box(1.0 + step, size[1] + 2.0, 1.0 + step, at=(size[0] - step, -1.0, size[2] - step))
    return BRepAlgoAPI_Cut(box(*size), notch).Shape()


def holed_box(size: tuple[float, float, float] = (20.0, 20.0, 20.0), radius: float = 3.0) -> Any:
    """A box with one cylindrical hole straight through it: seven faces, one of them the bore.

    The hole runs the full ``z`` extent, so removing the bore with ``defeature`` gives the box's
    six faces and its full ``size[0] * size[1] * size[2]`` volume back. What an unwanted through
    hole looks like before it is removed.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    bore = cylinder(radius, size[2] + 2.0, at=(size[0] / 2.0, size[1] / 2.0, -1.0))
    return BRepAlgoAPI_Cut(box(*size), bore).Shape()
