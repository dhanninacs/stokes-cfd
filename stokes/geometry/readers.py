"""One reader entry point for geometry source files (ADR-0021 decision 1.4).

``read_source`` dispatches on the suffix to the STEP, IGES and BREP readers, and ``read_mesh`` to
the mesh ones (STL, OBJ, 3MF); between them they are the only place the worker's jobs (import,
``check_brep``, ``export_step``) and the shape provider read a file. OCP loads inside the B-rep
readers, so importing this module costs nothing beyond the standard library and
:mod:`stokes.geometry.formats`; the mesh readers bring numpy, never OCP.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from stokes.errors import ValidationError
from stokes.geometry import formats
from stokes.geometry.errors import MeshOnly

if TYPE_CHECKING:
    from stokes.geometry.meshfile import MeshModel
    from stokes.geometry.step import StepModel


def read_source(path: str | Path, *, units: str | None = None) -> StepModel:
    """The model of a B-rep source: STEP, IGES or BREP, through the readers that know it.

    ``units`` is the length unit for a format the file does not declare (BREP; default
    millimetres). Giving it for a format that declares its own (STEP, IGES, 3MF) is a
    :class:`~stokes.errors.ValidationError`; a mesh format has no B-rep to read and is refused
    with :class:`~stokes.geometry.errors.MeshOnly` (its reader is :func:`read_mesh`).
    """
    fmt = formats.format_for(Path(path))
    if units is not None and fmt.units == "file":
        raise ValidationError(
            f"{fmt.label} files declare their own units; drop the units parameter",
            details={"format": fmt.id},
        )
    if fmt.family == "mesh":
        raise MeshOnly(
            f"{Path(path).name} is a mesh-only file: it has no B-rep",
            details={"format": fmt.id, "path": str(path)},
        )
    if fmt.id == "step":
        from stokes.geometry.step import read_step

        return read_step(path)
    if fmt.id == "iges":
        from stokes.geometry.iges import read_iges

        return read_iges(path)
    from stokes.geometry.brepfile import read_brep

    return read_brep(path, units or "mm")


def read_mesh(path: str | Path, *, units: str | None = None) -> MeshModel:
    """The bodies of a mesh-only source: STL, OBJ or 3MF, through :mod:`stokes.geometry.meshfile`.

    ``units`` is the length unit for the formats that declare none (STL, OBJ; default
    millimetres) and must be absent for a 3MF, which declares its own. A B-rep format is a
    :class:`~stokes.errors.ValidationError` here: it is :func:`read_source`'s.
    """
    from stokes.geometry.meshfile import read_3mf, read_obj, read_stl

    fmt = formats.format_for(Path(path))
    if fmt.family != "mesh":
        raise ValidationError(
            f"{fmt.label} is a B-rep format, not a mesh one", details={"format": fmt.id}
        )
    if units is not None and fmt.units == "file":
        raise ValidationError(
            f"{fmt.label} files declare their own units; drop the units parameter",
            details={"format": fmt.id},
        )
    if fmt.id == "3mf":
        return read_3mf(path)
    if fmt.id == "stl":
        return read_stl(path, units)
    return read_obj(path, units)
