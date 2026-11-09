"""BREP reading through OCP (ADR-0021 decision 1.2).

OCP loads inside the functions, as in :mod:`stokes.geometry.step`; call these from the worker
process (:mod:`stokes.geometry.worker`). A BREP file holds a shape and nothing else: no names,
no colours, no units. The units are the import's (default millimetres), and the shapes are in
them, so :class:`~stokes.geometry.step.StepModel.unit_scale` converts them to metres.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.errors import ValidationError
from stokes.geometry.errors import GeometryFileError

if TYPE_CHECKING:
    from stokes.geometry.step import StepModel

_BINARY_MARKER = b"Open CASCADE Topology"
"""Files starting with this header are OCCT's binary BREP (``BinTools``); the rest are the
``DBRep_DrawableShape`` text form, which ``BRepTools`` reads (its header line carries
``CASCADE Topology`` as well)."""


def read_brep(path: str | Path, units: str = "mm") -> StepModel:
    """The solids of a BREP file in the given units, named from the file.

    One part per solid the shape holds (``TopExp`` order), else one per shell, else the shape
    itself as one body. The parts are called ``<stem>``, ``<stem>_2`` ...; the file carries no
    colours, so none are set.
    """
    from OCP.BinTools import BinTools
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    from stokes.geometry.files import unique_name
    from stokes.geometry.step import StepModel, StepSolid, face_count

    start = time.perf_counter()
    source = Path(path)
    with source.open("rb") as handle:
        head = handle.read(128)
    shape = TopoDS_Shape()
    try:
        if _BINARY_MARKER in head:
            ok = BinTools.Read_s(shape, str(source))
        else:
            ok = BRepTools.Read_s(shape, str(source), BRep_Builder())
    except Exception as exc:  # OCCT's failures share no base class
        raise GeometryFileError(f"OCCT could not read {source.name}: {exc}") from exc
    if not ok or shape.IsNull():
        raise GeometryFileError(f"OCCT could not read {source.name}", details={"path": str(source)})
    found = _bodies(shape)
    model = StepModel(units=units, unit_scale=unit_scale(units))
    stem = source.stem or "part"
    taken: set[str] = set()
    for solid, kind in found:
        name = unique_name(stem, taken)
        taken.add(name)
        model.solids.append(StepSolid(name, None, solid, face_count(solid), kind))
    model.seconds = time.perf_counter() - start
    return model


def _bodies(shape: Any) -> list[tuple[Any, str]]:  # Any: OCP TopoDS_Shape
    """The part bodies of a BREP shape: its solids, else its shells, else itself as one body."""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    found: list[tuple[Any, str]] = []
    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    while explorer.More():
        found.append((explorer.Current(), "solid"))
        explorer.Next()
    if found:
        return found
    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SHELL)
    while explorer.More():
        found.append((explorer.Current(), "shell"))
        explorer.Next()
    if found:
        return found
    return [(shape, "faces")]


def unit_scale(units: str) -> float:
    """Metres per one of the units a BREP file is imported in; :class:`ValidationError` when
    the text names no length unit. The pint registry is the unit table (ADR-0012)."""
    from stokes.units.registry import ureg

    try:
        return float(ureg().Quantity(1.0, units).to("m").magnitude)
    except Exception as exc:
        raise ValidationError(
            f"units {units!r} is not a length unit", details={"units": units}
        ) from exc
