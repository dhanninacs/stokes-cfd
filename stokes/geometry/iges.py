"""IGES reading through OCP (ADR-0021 decision 1.2).

OCP loads inside the functions, as in :mod:`stokes.geometry.step`; call these from the worker
process (:mod:`stokes.geometry.worker`). ``IGESCAFControl_Reader`` transfers the file into a
transient XCAF document and the shapes go through the STEP walk (
:func:`stokes.geometry.step.walk_document`), so names, colours and the ``solid``/``shell``
bodies behave as for STEP. OCCT scales the shapes to its working unit the way the STEP reader
does; the unit the file declares comes from the global section's unit flag.

An IGES file holds solids, shells or loose faces. The walk takes the solids when the file has
them (OCCT's writer makes those with ``brep_mode`` 1), else one body per free shape. A shell or
a compound of faces stays one ``shell`` body; a face that stands alone is loose, and
:func:`read_iges` gathers the loose faces of a file without solids into one ``faces`` body, a
compound named from the file stem, so the six faces of a box written as surfaces are one part
and not six. The faces are not sewn: sewing changes the topology, so it is a repair operation
the user chooses (decision 5), and the repair is what turns the part into a solid.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.geometry.errors import GeometryFileError

if TYPE_CHECKING:
    from stokes.geometry.step import StepModel

# The global section's unit flag (the standard's order) to a length-unit symbol.
_FLAG_UNITS = {
    1: "in",
    2: "mm",
    4: "ft",
    5: "mi",
    6: "m",
    7: "km",
    8: "mil",
    9: "um",
    10: "cm",
    11: "uin",
}
_FLAG_CUSTOM = 3
"""Flag 3: the unit name in the file decides (``UnitName()``)."""


def read_iges(path: str | Path) -> StepModel:
    """The bodies of an IGES file with their names and colours, in file order.

    ``solid`` bodies when the file has solids; otherwise a ``shell`` body for each shell or
    compound, and one ``faces`` body, named from the file stem, for all the loose faces together
    (a compound of them, not sewn; see :func:`_gather_loose_faces`).

    The units come from the global section: the unit flag maps to a symbol (flag 3 leaves the
    name to the file); a flag the mapping does not know is read as millimetres and the model's
    ``warnings`` say so. The shapes are in the OCCT working unit, as for STEP.
    """
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.IGESCAFControl import IGESCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application

    from stokes.geometry import step

    start = time.perf_counter()
    source = Path(path)
    reader = IGESCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(False)
    status = reader.ReadFile(str(source))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise GeometryFileError(
            f"OCCT could not read {source.name} ({status.name})",
            details={"path": str(source), "status": status.name},
        )
    units, warnings = _file_units(reader)
    # A transient document, as the STEP reader makes: freed with its last handle once the
    # shapes have been taken out.
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    XCAFApp_Application.GetApplication_s().InitDocument(doc)
    if not reader.Transfer(doc):
        raise GeometryFileError(
            f"OCCT could not translate the shapes in {source.name}",
            details={"path": str(source)},
        )
    model = step.walk_document(doc, units=units, source=source)
    _gather_loose_faces(model, source)
    model.warnings = warnings
    model.seconds = time.perf_counter() - start
    return model


def _gather_loose_faces(model: StepModel, source: Path) -> None:
    """Make the loose faces of a file without solids one ``faces`` body, in place.

    The IGES reader returns each trimmed surface that belongs to no shell as a free shape of
    its own, so the six faces of a box written as surfaces would be six parts. They go into one
    ``BRep_Builder`` compound, named from the file stem and placed where the first of them was.
    Shells and compounds stay ``shell`` bodies, and a file that has any solid keeps what the
    walk found. Nothing is sewn: that is a repair the user asks for (decision 5).
    """
    from OCP.BRep import BRep_Builder
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopoDS import TopoDS_Compound

    from stokes.geometry.files import unique_name
    from stokes.geometry.step import StepSolid

    if any(body.kind == "solid" for body in model.solids):
        return
    others: list[StepSolid] = []
    loose: list[StepSolid] = []
    first = 0
    for body in model.solids:
        if body.shape.ShapeType() != TopAbs_ShapeEnum.TopAbs_FACE:
            others.append(body)
            continue
        if not loose:
            first = len(others)
        loose.append(body)
    if not loose:
        return
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for body in loose:
        builder.Add(compound, body.shape)
    name = unique_name(source.stem or "part", {body.name for body in others})
    faces = sum(body.faces for body in loose)
    others.insert(first, StepSolid(name, None, compound, faces, "faces"))
    model.solids = others


def _file_units(reader: Any) -> tuple[str, tuple[str, ...]]:  # Any: OCP IGESCAFControl_Reader
    """The length unit an IGES file declares, and the reader's notes on it.

    Flag 3 names the unit in the file and the name maps to a symbol like STEP's unit names do;
    an empty or unknown name, and every flag the mapping lacks, read as millimetres with a
    warning, because a misread unit is a misread size.
    """
    from stokes.geometry import step

    section = reader.Model().GlobalSection()
    flag = int(section.UnitFlag())
    if flag in _FLAG_UNITS:
        return _FLAG_UNITS[flag], ()
    if flag == _FLAG_CUSTOM:
        name = str(section.UnitName().ToCString()).strip().lower()
        symbol = step._UNIT_SYMBOLS.get(name, name[:32])
        if symbol:
            return symbol, ()
        return "mm", ("the file's unit name is empty; millimetres assumed",)
    return "mm", (f"the file's unit flag {flag} is not known; millimetres assumed",)
