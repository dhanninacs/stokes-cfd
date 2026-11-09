"""STEP reading and tessellation through OCP (ADR-0003, ADR-0014).

OCP loads inside the functions, so importing this module costs only numpy; OCP objects are
typed ``Any`` because OCP ships no type stubs. Call these from the worker process
(:mod:`stokes.geometry.worker`), never from the thread that answers the API: importing OCP
takes seconds and an OCCT fault ends the process it runs in.

Units: OCCT's STEP reader converts every file to its working unit (``xstep.cascade.unit``,
millimetres by default); meshes come out in metres. The unit the file declares is reported as
text (``"mm"``, ``"in"`` ...) for the part node.

Tessellation runs BRepMesh with deflections relative to the shape's bounding-box diagonal
(:data:`COARSE` for first display, then :data:`FINE`), then takes the triangles in bulk through
OCCT's binary STL writer, which walks the faces in TopExp order, applies locations and reverses
the winding of reversed faces. Reading nodes one call at a time through the bindings costs about
7 µs per node and per triangle on the development laptop (8 s for 585 k triangles); the STL
route, with a numpy weld per face and area-weighted normals, takes 1.8 s for the same mesh.
Per-face triangle counts give each triangle its face id, numbered in TopExp order from
``face_base`` (0 for a part: every solid is a part of its own and numbers its faces from 0).
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.geometry.errors import GeometryError, GeometryFileError
from stokes.geometry.files import unique_name
from stokes.geometry.mesh import DEFAULT_COLOR, TriMesh, Vec3, vertex_normals, weld

MODEL_UNIT = 1e-3
"""Metres per OCCT model unit when ``xstep.cascade.unit`` is millimetres (the default)."""
MIN_DEFLECTION = 1e-6
MAX_ASSEMBLY_DEPTH = 64
MAX_NAME_LENGTH = 128
_STL_HEADER_BYTES = 84
_GROUP_LOG = math.log(1.5)
_STL_RECORD = np.dtype([("normal", "<f4", (3,)), ("corners", "<f4", (3, 3)), ("attr", "<u2")])
_CASCADE_UNITS = {
    "MM": 1e-3,
    "CM": 1e-2,
    "M": 1.0,
    "KM": 1e3,
    "UM": 1e-6,
    "IN": 0.0254,
    "INCH": 0.0254,
    "FT": 0.3048,
    "MI": 1609.344,
}
_UNIT_SYMBOLS = {
    "millimetre": "mm",
    "millimeter": "mm",
    "centimetre": "cm",
    "centimeter": "cm",
    "decimetre": "dm",
    "decimeter": "dm",
    "metre": "m",
    "meter": "m",
    "kilometre": "km",
    "kilometer": "km",
    "micrometre": "um",
    "micrometer": "um",
    "micron": "um",
    "inch": "in",
    "foot": "ft",
    "feet": "ft",
    "mile": "mi",
}
# Names exporters write when the user gave none: entity types, assembly-usage ids, OCAF entries.
_GENERIC_NAME_RE = re.compile(
    r"^(|solid|shell|compound|compsolid|face|body|nauo\d*|=>\[[0-9:]*\]"
    r"|open cascade step translator.*)$",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True)
class Quality:
    """A tessellation pass: linear deflection as a fraction of the bounding-box diagonal."""

    name: str
    linear: float
    angular: float


COARSE = Quality("coarse", 2e-3, 0.5)
FINE = Quality("fine", 5e-4, 0.2)
PASSES: tuple[Quality, ...] = (COARSE, FINE)


@dataclass
class StepSolid:
    """One body of a STEP file, placed in model coordinates."""

    name: str
    color: Vec3 | None
    shape: Any  # Any: OCP TopoDS_Shape (no stubs)
    faces: int
    kind: str = "solid"


@dataclass
class StepModel:
    solids: list[StepSolid] = field(default_factory=list)
    units: str = ""
    unit_scale: float = MODEL_UNIT
    seconds: float = 0.0
    warnings: tuple[str, ...] = ()
    """Reader notes the caller should surface (an unknown IGES unit flag, T-016)."""

    @property
    def faces(self) -> int:
        return sum(s.faces for s in self.solids)


def cascade_unit_scale() -> float:
    """Metres per model unit of the shapes the STEP reader produces in this process."""
    from OCP.Interface import Interface_Static

    unit = str(Interface_Static.CVal_s("xstep.cascade.unit") or "MM").strip().upper()
    scale = _CASCADE_UNITS.get(unit)
    if scale is None:
        raise GeometryError(f"unsupported OCCT working unit {unit!r}")
    return scale


def read_step(path: str | Path) -> StepModel:
    """The solids of a STEP file with their names and colours, in file order.

    Names come from the solid's own sub-shape label when the file gives it one, then the
    instance, the part and the nearest named assembly; bodies without one are called
    ``solid_1``, ``solid_2`` ... by position. Repeated names get ``_2``, ``_3`` suffixes.
    A colour is the solid's own, else the part's, else the one inherited from the instance or
    assembly above it. A part without solids contributes its faces as one ``shell`` body.
    """
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application

    start = time.perf_counter()
    source = Path(path)
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(False)
    reader.SetPropsMode(False)
    reader.SetGDTMode(False)
    reader.SetMatMode(False)
    reader.SetViewMode(False)
    status = reader.ReadFile(str(source))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise GeometryFileError(
            f"OCCT could not read {source.name} ({status.name})",
            details={"path": str(source), "status": status.name},
        )
    units = _file_units(reader)
    # A transient document: never registered with the application session, so it is freed
    # with its last handle once the shapes (which do not depend on it) have been taken out.
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    XCAFApp_Application.GetApplication_s().InitDocument(doc)
    if not reader.Transfer(doc):
        raise GeometryFileError(
            f"OCCT could not translate the shapes in {source.name}",
            details={"path": str(source)},
        )
    model = walk_document(doc, units=units, source=source)
    model.seconds = time.perf_counter() - start
    return model


def walk_document(doc: Any, *, units: str, source: Path) -> StepModel:  # Any: OCP TDocStd_Document
    """The StepModel of a read document: its free shapes walked, named and placed.

    Shared by the STEP and IGES readers (ADR-0021 decision 1.2): the same walk, the same
    naming and colouring, ``solid`` bodies and one ``shell`` body for a free shape without
    solids. ``units`` is what the file declared (or the parameter, for a reader that takes
    it); ``source`` names the file in the errors. The shapes are already in the working unit,
    so the scale is the process's.
    """
    import OCP.collections as occ
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free = occ.Sequence_TDF_Label()
    shape_tool.GetFreeShapes(free)
    found: list[_Body] = []
    for i in range(1, free.Length() + 1):
        _walk(free.Value(i), TopLoc_Location(), _Context(), found, 0)
    if not found:
        raise GeometryFileError(
            f"{source.name} contains no solids or faces", details={"path": str(source)}
        )
    model = StepModel(units=units, unit_scale=cascade_unit_scale())
    taken: set[str] = set()
    for index, body in enumerate(found, start=1):
        label = next((n for n in body.names if n and not _GENERIC_NAME_RE.match(n)), "")
        name = unique_name(label or f"solid_{index}", taken)
        taken.add(name)
        model.solids.append(
            StepSolid(name, body.color, body.shape, face_count(body.shape), body.kind)
        )
    return model


@dataclass(frozen=True)
class _Context:
    """What a label inherits from the assembly above it."""

    instance: str | None = None
    ancestor: str | None = None
    color: Vec3 | None = None


@dataclass
class _Body:
    shape: Any  # Any: OCP TopoDS_Shape, already placed
    names: tuple[str | None, ...]
    color: Vec3 | None
    kind: str


def _walk(
    label: Any, loc: Any, ctx: _Context, out: list[_Body], depth: int
) -> None:  # Any: OCP TDF_Label, TopLoc_Location
    import OCP.collections as occ
    from OCP.TDF import TDF_Label
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    if depth > MAX_ASSEMBLY_DEPTH:
        raise GeometryFileError("the assembly is nested too deeply")
    if XCAFDoc_ShapeTool.IsReference_s(label):
        referred = TDF_Label()
        XCAFDoc_ShapeTool.GetReferredShape_s(label, referred)
        placed = loc.Multiplied(XCAFDoc_ShapeTool.GetLocation_s(label))
        inner = _Context(_label_name(label), ctx.ancestor, _label_color(label) or ctx.color)
        _walk(referred, placed, inner, out, depth + 1)
        return
    own = _label_name(label)
    color = _label_color(label) or ctx.color
    if XCAFDoc_ShapeTool.IsAssembly_s(label):
        named = [n for n in (ctx.instance, own) if n and not _GENERIC_NAME_RE.match(n)]
        inner = _Context(None, named[0] if named else ctx.ancestor, color)
        components = occ.Sequence_TDF_Label()
        XCAFDoc_ShapeTool.GetComponents_s(label, components, False)
        for i in range(1, components.Length() + 1):
            _walk(components.Value(i), loc, inner, out, depth + 1)
        return
    shape = XCAFDoc_ShapeTool.GetShape_s(label)
    if shape.IsNull():
        return
    bodies: list[tuple[Any, str]] = []  # Any: OCP TopoDS_Shape
    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    while explorer.More():
        bodies.append((explorer.Current(), "solid"))
        explorer.Next()
    if not bodies and face_count(shape):
        bodies.append((shape, "shell"))
    solids = _solid_labels(label)
    for body, kind in bodies:
        # A solid inside a multi-body part has a sub-shape label of its own when the file
        # coloured or named it; that wins over what the part, the instance and the assembly say.
        mine = next((s for s in solids.get(hash(body), ()) if s.shape.IsSame(body)), None)
        out.append(
            _Body(
                body.Moved(loc),
                (mine.name if mine else None, ctx.instance, own, ctx.ancestor),
                (mine.color if mine else None) or color,
                kind,
            )
        )


@dataclass(frozen=True)
class _SolidLabel:
    """The name and colour a file gave to one solid inside a part."""

    shape: Any  # Any: OCP TopoDS_Shape
    name: str | None
    color: Vec3 | None


def _solid_labels(part: Any) -> dict[int, list[_SolidLabel]]:  # Any: OCP TDF_Label
    """The named or coloured solids of a part, by shape hash, from one pass over its sub-labels.

    One ``GetSubShapes`` call per part replaces a lookup per solid. The hash only narrows the
    candidates; ``IsSame`` decides, so a hash collision cannot attach one solid's colour to
    another. A part with thousands of solids stays linear.
    """
    import OCP.collections as occ
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    labels = occ.Sequence_TDF_Label()
    XCAFDoc_ShapeTool.GetSubShapes_s(part, labels)
    found: dict[int, list[_SolidLabel]] = {}
    for i in range(1, len(labels) + 1):  # len(): Length() costs about 1 ms a call on OCP 8.0.1
        label = labels.Value(i)
        shape = XCAFDoc_ShapeTool.GetShape_s(label)
        if shape.IsNull() or shape.ShapeType() != TopAbs_ShapeEnum.TopAbs_SOLID:
            continue  # face and edge labels carry colours the mesh transport does not keep
        name, color = _label_name(label), _label_color(label)
        if name or color:
            found.setdefault(hash(shape), []).append(_SolidLabel(shape, name, color))
    return found


def _label_name(label: Any) -> str | None:  # Any: OCP TDF_Label
    from OCP.TDataStd import TDataStd_Name

    guid = TDataStd_Name.GetID_s()
    # Ask IsAttribute before FindAttribute. FindAttribute with an out-parameter made in Python
    # faults the process on any label that has no TDataStd_Name: an access violation
    # (0xC0000005) that OCCT cannot trap, so there is no Python exception and the import just
    # dies. Nameless labels are normal; the solid sub-labels of a file read back have none.
    # Checked on OCP 8.0.1.
    if not label.IsAttribute(guid):
        return None
    attr = TDataStd_Name()
    if not label.FindAttribute(guid, attr):
        return None
    text = _CONTROL_RE.sub(" ", str(attr.Get().ToExtString())).strip()
    return text[:MAX_NAME_LENGTH] or None


def _label_color(label: Any) -> Vec3 | None:  # Any: OCP TDF_Label
    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
    from OCP.XCAFDoc import XCAFDoc, XCAFDoc_ColorTool, XCAFDoc_ColorType

    color = Quantity_Color()
    for kind in (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen):
        if not label.IsAttribute(XCAFDoc.ColorRefGUID_s(kind)):
            continue  # same rule as _label_name: ask for the attribute before reading it
        if XCAFDoc_ColorTool.GetColor_s(label, kind, color):
            r, g, b = color.Values(Quantity_TypeOfColor.Quantity_TOC_sRGB)
            return (_unit_interval(r), _unit_interval(g), _unit_interval(b))
    return None


def _unit_interval(value: float) -> float:
    return round(min(1.0, max(0.0, float(value))), 6)


def _file_units(reader: Any) -> str:  # Any: OCP STEPCAFControl_Reader
    import OCP.collections as occ

    lengths = occ.Sequence_TCollection_AsciiString()
    angles = occ.Sequence_TCollection_AsciiString()
    solid_angles = occ.Sequence_TCollection_AsciiString()
    reader.ChangeReader().FileUnits(lengths, angles, solid_angles)
    if lengths.Length() == 0:
        return ""
    name = str(lengths.Value(1).ToCString()).strip().lower()
    return _UNIT_SYMBOLS.get(name, name[:32])


def face_count(shape: Any) -> int:  # Any: OCP TopoDS_Shape
    """Faces in TopExp order, counting a face as often as the explorer visits it."""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def bbox_diagonal(shape: Any) -> float:  # Any: OCP TopoDS_Shape
    """Length of the bounding-box diagonal in model units; 0 for an empty shape."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    if box.IsVoid():
        return 0.0
    return float(box.CornerMin().Distance(box.CornerMax()))


def deflections(shape: Any, quality: Quality) -> tuple[float, float]:  # Any: OCP TopoDS_Shape
    """(linear deflection in model units, angular deflection in radians) for a pass."""
    return max(quality.linear * bbox_diagonal(shape), MIN_DEFLECTION), quality.angular


@dataclass(frozen=True)
class Body:
    """A shape to tessellate with its deflections and the metadata its mesh carries."""

    shape: Any  # Any: OCP TopoDS_Shape (no stubs)
    linear: float
    angular: float
    name: str = "solid"
    kind: str = "solid"
    color: Vec3 | None = None
    face_base: int = 0


def tessellate(
    shape: Any,  # Any: OCP TopoDS_Shape
    linear_deflection: float,
    angular_deflection: float,
    *,
    name: str = "solid",
    kind: str = "solid",
    color: Vec3 | None = None,
    face_base: int = 0,
    quality: str = "",
    unit: float = MODEL_UNIT,
    workdir: Path | None = None,
) -> TriMesh:
    """Mesh ``shape`` and return it with one face id per triangle; positions in metres.

    ``linear_deflection`` is in model units, ``angular_deflection`` in radians; ``unit`` is
    metres per model unit. Faces BRepMesh could not triangulate keep their id but no triangles.
    """
    body = Body(shape, linear_deflection, angular_deflection, name, kind, color, face_base)
    return tessellate_bodies([body], quality=quality, unit=unit, workdir=workdir)[0]


def tessellate_bodies(
    bodies: Sequence[Body],
    *,
    quality: str = "",
    unit: float = MODEL_UNIT,
    workdir: Path | None = None,
    parallel: bool = True,
) -> list[TriMesh]:
    """Mesh several bodies, each to at least its own deflections, and extract them in one go.

    ``parallel`` is OCCT's own threading inside one BRepMesh call. It buys only 1.27x on many small
    solids (12.5 s to 9.8 s on the 4,500-solid pin grid, 24 CPUs), so a process that is one of
    several helpers (:mod:`stokes.geometry.helpers`) turns it off rather than oversubscribe.

    Fixed costs dominate assemblies of many small solids on the development laptop: about
    4 ms per BRepMesh call and a few milliseconds of file-system time per STL file. So bodies
    whose linear deflections lie within a factor of 1.5 are meshed in one call at the finest
    of them, and one STL write serves the whole batch. ``workdir`` must exist and belong to
    one thread; without it a temporary folder is used.
    """
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    groups: dict[tuple[float, int], list[Body]] = {}
    for body in bodies:
        if not (body.linear > 0.0 and body.angular > 0.0):
            raise GeometryError("deflections must be positive")
        key = (body.angular, math.floor(math.log(body.linear) / _GROUP_LOG))
        groups.setdefault(key, []).append(body)
    for (angular, _), members in groups.items():
        shape = members[0].shape if len(members) == 1 else _compound([b.shape for b in members])
        BRepMesh_IncrementalMesh(shape, min(b.linear for b in members), False, angular, parallel)
    counts = [_triangle_counts(body.shape) for body in bodies]
    totals = [int(c.sum()) for c in counts]
    corners = np.zeros((0, 3, 3), dtype=np.float32)
    if sum(totals):
        shape = bodies[0].shape if len(bodies) == 1 else _compound([b.shape for b in bodies])
        corners = _stl_corners(shape, sum(totals), workdir)
    meshes = []
    offset = 0
    for body, face_counts, total in zip(bodies, counts, totals, strict=True):
        meshes.append(_mesh(body, face_counts, corners[offset : offset + total], quality, unit))
        offset += total
    return meshes


def _mesh(
    body: Body,
    counts: NDArray[np.int64],
    corners: NDArray[np.float32],
    quality: str,
    unit: float,
) -> TriMesh:
    faces = int(counts.size)
    local = np.repeat(np.arange(faces, dtype=np.uint32), counts)
    positions, indices = weld(corners.reshape(-1, 3), np.repeat(local, 3))
    normals = vertex_normals(positions, indices)
    metres = (positions.astype(np.float64) * unit).astype(np.float32)
    return TriMesh(
        body.name,
        body.kind,
        body.color or DEFAULT_COLOR,
        metres,
        normals,
        indices,
        local + np.uint32(body.face_base),
        faces,
        body.face_base,
        quality,
    )


def _stl_corners(shape: Any, total: int, workdir: Path | None) -> NDArray[np.float32]:  # Any: OCP
    """The triangle corners of a meshed shape, ``(total, 3, 3)``, in model units."""
    from OCP.StlAPI import StlAPI_Writer

    with _scratch(workdir) as folder:
        target = folder / f"mesh-{os.getpid()}-{threading.get_ident()}.stl"
        writer = StlAPI_Writer()
        writer.ASCIIMode = False
        if not writer.Write(shape, str(target)):
            raise GeometryError("OCCT could not write the triangulation")
        with target.open("rb") as handle:
            if (
                os.fstat(handle.fileno()).st_size
                != _STL_HEADER_BYTES + _STL_RECORD.itemsize * total
            ):
                raise GeometryError("the triangulation and its face counts disagree")
            handle.seek(_STL_HEADER_BYTES)
            records = np.fromfile(handle, dtype=_STL_RECORD, count=total)
    return np.ascontiguousarray(records["corners"])


def compound_of(shapes: Sequence[Any]) -> Any:  # Any: OCP TopoDS_Shape
    """The shapes, in order, as one compound (a batch to mesh, or to hand to a helper)."""
    return _compound(shapes)


def _compound(shapes: Sequence[Any]) -> Any:  # Any: OCP TopoDS_Shape
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def _triangle_counts(shape: Any) -> NDArray[np.int64]:  # Any: OCP TopoDS_Shape
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    counts: list[int] = []
    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
    triangulation = BRep_Tool.Triangulation_s
    as_face = TopoDS.Face
    while explorer.More():
        tri = triangulation(as_face(explorer.Current()), TopLoc_Location())
        counts.append(0 if tri is None else int(tri.NbTriangles()))
        explorer.Next()
    return np.asarray(counts, dtype=np.int64)


@contextmanager
def _scratch(workdir: Path | None) -> Iterator[Path]:
    if workdir is not None:
        yield workdir  # reused: the file in it is overwritten by the next batch
        return
    with tempfile.TemporaryDirectory(prefix="stokes-mesh-") as folder:
        yield Path(folder)
