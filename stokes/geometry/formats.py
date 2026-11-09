"""The geometry import formats and their file probes (ADR-0021 decision 1.2).

Standard library only, like :mod:`stokes.geometry.files`: the API layer checks a source file
before any worker starts and does not want OCP or numpy for it. A format names its file's
suffixes, its family (``brep`` or ``mesh``) and where its units come from: ``"file"`` when the
file declares them (STEP, IGES, 3MF) and ``"param"`` when the import's ``units`` parameter names
them (BREP, STL, OBJ). The mesh formats import as mesh-only parts through
:func:`stokes.geometry.readers.read_mesh`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stokes.geometry.errors import GeometryFileError, GeometryUnsupported

_PROBE_BYTES = 4096
"""How much of a file the header probes read."""
_BREP_MARKERS = (b"DBRep_DrawableShape", b"CASCADE Topology", b"Open CASCADE Topology")
_ZIP_MARKERS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
"""A 3MF is a zip archive; these are its local, empty and spanned-file signatures."""
_IGES_START_COLUMN = 72
"""Zero-based byte of column 73: the IGES start marker 'S' of the first 80-byte line."""


@dataclass(frozen=True)
class Format:
    """One import format: its id, picker label, suffixes, family and units source."""

    id: str  # "step" | "iges" | "brep" | "stl" | "obj" | "3mf"
    label: str  # "STEP", "IGES", ...
    suffixes: tuple[str, ...]
    family: str  # "brep" | "mesh"
    units: str  # "file" (declared in the file) | "param" (the units parameter)


FORMATS: tuple[Format, ...] = (
    Format("step", "STEP", (".step", ".stp"), "brep", "file"),
    Format("iges", "IGES", (".iges", ".igs"), "brep", "file"),
    Format("brep", "BREP", (".brep", ".brp"), "brep", "param"),
    Format("stl", "STL", (".stl",), "mesh", "param"),
    Format("obj", "OBJ", (".obj",), "mesh", "param"),
    Format("3mf", "3MF", (".3mf",), "mesh", "file"),
)
"""The import formats, in the order the picker lists them. The mesh formats import as mesh-only
parts (:mod:`stokes.geometry.readers`); 3MF declares its unit, STL and OBJ take the parameter."""


def format_for(path: str | Path) -> Format:
    """The format of a source file by its suffix, case-insensitive.

    :class:`GeometryUnsupported` names the known formats when the suffix is not one of them.
    """
    suffix = Path(path).suffix.lower()
    for fmt in FORMATS:
        if suffix in fmt.suffixes:
            return fmt
    raise GeometryUnsupported(
        f"no import format for the suffix {suffix or '(none)'}; "
        f"known: {', '.join(fmt.label for fmt in FORMATS)}",
        details={"path": str(path)},
    )


def family_for(path: str | Path) -> str:
    """The family a source file gives its parts: ``"brep"`` or ``"mesh"``."""
    return format_for(path).family


def read_units(path: str | Path, part_units: object) -> str | None:
    """The ``units`` to hand a reader for a part of ``path``: the part's own length unit when
    the format declares none (BREP), else None, since STEP and IGES files say theirs."""
    try:
        fmt = format_for(path)
    except GeometryUnsupported:
        return None
    return (
        part_units if fmt.units == "param" and isinstance(part_units, str) and part_units else None
    )


def probe(path: Path, fmt: Format) -> None:
    """Check the file's header against its format; :class:`GeometryFileError` when it does not
    look like one. STL and OBJ have no header to check: their readers validate the records."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_PROBE_BYTES)
    except OSError as exc:
        raise GeometryFileError(
            f"cannot read {path.name}: {exc.strerror or exc}", details={"path": str(path)}
        ) from exc
    if fmt.id == "step" and b"ISO-10303-21" not in head.upper():
        raise GeometryFileError(
            f"{path.name} is not a STEP file (no ISO-10303-21 header)",
            details={"path": str(path)},
        )
    if fmt.id == "iges" and (len(head) <= _IGES_START_COLUMN or head[_IGES_START_COLUMN] != 0x53):
        raise GeometryFileError(
            f"{path.name} is not an IGES file (no 'S' start marker in the first line)",
            details={"path": str(path)},
        )
    if fmt.id == "brep" and not any(marker in head for marker in _BREP_MARKERS):
        raise GeometryFileError(
            f"{path.name} is not a BREP file (no DBRep or Open CASCADE Topology header)",
            details={"path": str(path)},
        )
    if fmt.id == "3mf" and not any(head.startswith(marker) for marker in _ZIP_MARKERS):
        raise GeometryFileError(
            f"{path.name} is not a 3MF file (not a zip archive)", details={"path": str(path)}
        )


def filter_string() -> str:
    """The picker's file filter in the native dialog's name-filter syntax (globs: ``*.step``):
    every import format, then one entry per format, then all files."""

    def globs(suffixes: tuple[str, ...]) -> str:
        return " ".join(f"*{suffix}" for suffix in suffixes)

    entries = [f"Geometry files ({globs(tuple(s for fmt in FORMATS for s in fmt.suffixes))})"]
    entries += [f"{fmt.label} ({globs(fmt.suffixes)})" for fmt in FORMATS]
    entries.append("All files (*)")
    return ";;".join(entries)
