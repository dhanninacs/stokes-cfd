"""The shape cache and the shape provider (ADR-0021 decision 8.3): every part's B-rep on disk,
read back in the worker.

Layout: ``cache/geometry/<part id>.brep`` beside the mesh cache, OCCT's binary format
(``BinTools``, without triangulation) when this OCP has ``BinTools.Write_s``, else its text
format (``BRepTools``), and a sidecar ``<part id>.brep.json``::

    {"version": 1, "part", "basis", "units", "scale", "faces", "format", "bytes", "crc32"}

``basis`` is the version of the part the B-rep belongs to (:mod:`stokes.geometry.fingerprints`),
``scale`` the metres per unit of the stored shape (``units`` names it: ``mm`` for STEP and IGES,
which OCCT reads in millimetres, a BREP file's own unit, ``mm`` for an operation's output) and
``bytes`` and ``crc32`` the B-rep file's size and checksum. Writing removes the old sidecar,
writes the B-rep, then the sidecar: the sidecar is the commit, so a reader that finds one whose
size and checksum match the B-rep has a whole entry, and a write cut short leaves no entry at
all. That costs three file operations where a temporary file and a rename per file would cost
four, and renames are the slow part on Windows (about 8 ms each on the development laptop, which
an assembly of 4,500 parts pays twice over at import). Reads are capped at 2 GB.

Writers: the import worker, for every solid it read, just before tessellating it; the operation
job, for every output; and the provider, after a source read that the cache could not spare.

:class:`ShapeProvider` loads a part for a worker job from a spec the session builds
(:func:`part_spec`): ``{part, name, basis, source, solid, units, brep, derived}``. It uses the
cache when the sidecar's basis is the part's; otherwise it reads the source file (once per file
and job, whatever the number of parts), checks that the file is still the version the part was
imported from, and writes the cache. A derived part has no source: without its cache it is
:class:`~stokes.geometry.errors.OperationInvalid`, naming the operation to run again. Every
shape comes back in millimetres, the unit operations work in. ``source_reads`` and
``cache_hits`` count what a job did (T-030 asserts no source read when the cache is fresh).

Import rule: the standard library and the light geometry modules at module level; OCP, the
readers and numpy inside the functions that run in the worker, so the session can build specs
and read sidecars without them.
"""

from __future__ import annotations

import io
import json
import math
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.geometry.errors import GeometryFileError, OperationInvalid
from stokes.log import fields, get_logger
from stokes.model.ids import is_id, stable_id

if TYPE_CHECKING:
    from stokes.geometry.step import StepModel
    from stokes.model.nodes import Node

log = get_logger(__name__)

SUBDIR = ("cache", "geometry")
"""The shape cache shares the mesh cache's folder (:mod:`stokes.geometry.cache`)."""
SUFFIX = ".brep"
SIDECAR_SUFFIX = ".brep.json"
VERSION = 1
MAX_BREP_BYTES = 2 * 1024**3
MAX_SIDECAR_BYTES = 64 * 1024
MM = 1e-3
"""Metres per millimetre: the unit operations work in and store their outputs in."""
_BASIS_RE = re.compile(r"^[0-9a-f]{1,32}$")
_UNIT_NAMES = {1e-3: "mm", 1e-2: "cm", 1.0: "m", 1e-6: "um", 0.0254: "in", 0.3048: "ft"}


@dataclass(frozen=True)
class ShapeEntry:
    """One sidecar: what the B-rep beside it is."""

    part: str
    basis: str
    scale: float
    """Metres per unit of the stored shape."""
    faces: int
    format: str
    """``bin`` (BinTools) or ``text`` (BRepTools)."""
    bytes: int
    crc32: int

    @property
    def units(self) -> str:
        return unit_name(self.scale)

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "version": VERSION,
            "part": self.part,
            "basis": self.basis,
            "units": self.units,
            "scale": self.scale,
            "faces": self.faces,
            "format": self.format,
            "bytes": self.bytes,
            "crc32": self.crc32,
        }


def cache_dir(project_dir: Path) -> Path:
    """The folder of a project's shape (and mesh) cache."""
    return project_dir.joinpath(*SUBDIR)


def brep_path(folder: Path, part_id: str) -> Path:
    """``<folder>/<part id>.brep``; the id must be a node id, so the name stays in the folder."""
    if not is_id(part_id):
        raise GeometryFileError(f"not a node id: {part_id!r}")
    return folder / f"{part_id}{SUFFIX}"


def sidecar_path(brep: Path) -> Path:
    return brep.with_name(brep.name[: -len(SUFFIX)] + SIDECAR_SUFFIX)


def unit_name(scale: float) -> str:
    """A length unit's name for a scale in metres per unit (``mm`` for 0.001)."""
    for value, name in _UNIT_NAMES.items():
        if math.isclose(scale, value, rel_tol=1e-12):
            return name
    return f"{scale:g} m"


def solid_part_id(top: str, index: int) -> str:
    """The id of the part made from solid ``index`` when the import into ``top`` finds several
    solids; :func:`stokes.geometry.store.part_id_for` is this rule, and the import worker names
    the B-reps it caches with it before the session has built the group."""
    return stable_id("n", f"{top}/solid/{index}")


# ---------------------------------------------------------------- the sidecar (no OCP)


def read_entry(brep: Path) -> ShapeEntry | None:
    """The sidecar of ``brep``, or None when it is absent, too large or malformed."""
    path = sidecar_path(brep)
    try:
        if path.stat().st_size > MAX_SIDECAR_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _entry(data)


def _entry(data: Any) -> ShapeEntry | None:  # Any: JSON
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None
    part, basis, scale = data.get("part"), data.get("basis"), data.get("scale")
    faces, kind, size, crc = (data.get(k) for k in ("faces", "format", "bytes", "crc32"))
    ints = (faces, size, crc)
    if (
        not (isinstance(part, str) and is_id(part))
        or not (isinstance(basis, str) and _BASIS_RE.match(basis))
        or isinstance(scale, bool)
        or not isinstance(scale, int | float)
        or not (math.isfinite(scale) and scale > 0.0)
        or kind not in ("bin", "text")
        or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in ints)
    ):
        return None
    assert isinstance(faces, int) and isinstance(size, int) and isinstance(crc, int)
    return ShapeEntry(part, basis, float(scale), faces, str(kind), size, crc)


def fresh(brep: Path, basis: str) -> bool:
    """Whether ``brep`` holds the part's B-rep at version ``basis`` (by its sidecar alone: the
    session asks this without OCP, the provider checks the bytes when it reads)."""
    entry = read_entry(brep)
    return entry is not None and bool(basis) and entry.basis == basis


# ---------------------------------------------------------------- the B-rep (OCP, in the worker)


def write_shape(
    brep: Path,
    shape: Any,  # Any: OCP TopoDS_Shape
    *,
    part: str,
    basis: str,
    scale: float,
) -> ShapeEntry:
    """Write ``shape`` (in units of ``scale`` metres) as the cache entry ``brep`` at ``basis``:
    old sidecar out, B-rep in, sidecar in (the commit). Raises ``OSError`` when the disk refuses;
    a shape over 2 GB is not cached (:class:`GeometryFileError`)."""
    from stokes.geometry.step import face_count

    if not _BASIS_RE.match(basis):
        raise GeometryFileError(f"not a basis: {basis!r}")
    kind, data = _serialise(shape)
    if len(data) > MAX_BREP_BYTES:
        raise GeometryFileError(f"the B-rep of {part} is over 2 GB; not cached")
    entry = ShapeEntry(part, basis, scale, face_count(shape), kind, len(data), zlib.crc32(data))
    side = sidecar_path(brep)
    brep.parent.mkdir(parents=True, exist_ok=True)
    side.unlink(missing_ok=True)  # no entry from here until the new sidecar is written
    brep.write_bytes(data)
    side.write_text(json.dumps(entry.to_json(), separators=(",", ":")), encoding="utf-8")
    return entry


def _serialise(shape: Any) -> tuple[str, bytes]:  # Any: OCP TopoDS_Shape
    from OCP.BinTools import BinTools

    stream = io.BytesIO()
    if hasattr(BinTools, "Write_s"):
        from OCP.BinTools import BinTools_FormatVersion

        version = BinTools_FormatVersion.BinTools_FormatVersion_CURRENT
        BinTools.Write_s(shape, stream, False, False, version)
        return "bin", stream.getvalue()
    from OCP.BRepTools import BRepTools
    from OCP.TopTools import TopTools_FormatVersion

    text_version = TopTools_FormatVersion.TopTools_FormatVersion_CURRENT
    BRepTools.Write_s(shape, stream, False, False, text_version)
    return "text", stream.getvalue()


def read_shape(brep: Path, entry: ShapeEntry) -> Any | None:  # Any: OCP TopoDS_Shape
    """The B-rep ``entry`` describes, in its stored units; None when the file is missing, over
    2 GB, or not the bytes the sidecar committed (a write cut short, or another writer)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    try:
        size = brep.stat().st_size
        if size != entry.bytes or size > MAX_BREP_BYTES:
            return None
        data = brep.read_bytes()
    except OSError:
        return None
    if zlib.crc32(data) != entry.crc32:
        return None
    shape = TopoDS_Shape()
    try:
        if entry.format == "bin":
            from OCP.BinTools import BinTools

            BinTools.Read_s(shape, io.BytesIO(data))
        else:
            BRepTools.Read_s(shape, io.BytesIO(data), BRep_Builder())
    except Exception:  # OCCT's failures share no base class; the entry is unusable
        log.warning("shape cache entry unreadable; ignored", extra=fields(path=str(brep)))
        return None
    return None if shape.IsNull() else shape


def to_mm(shape: Any, scale: float) -> Any:  # Any: OCP TopoDS_Shape
    """``shape``, stored in units of ``scale`` metres, in millimetres; itself when it is already."""
    factor = scale / MM
    if math.isclose(factor, 1.0, rel_tol=1e-12):
        return shape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    trsf.SetScaleFactor(factor)
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


# ---------------------------------------------------------------- specs (the session's side)


def part_spec(node: Node, folder: Path | None, basis: str) -> dict[str, Any]:  # Any: JSON
    """What a worker job needs to load the part ``node`` through :class:`ShapeProvider`.

    ``basis`` is the part's current version (the node's ``basis``, or the legacy hash of an old
    project's part; empty when unknown, which reads the source and caches nothing). ``folder``
    is the session's shape cache, None when there is none to use."""
    from stokes.geometry.formats import read_units

    params = node.params
    source = str(params.get("source") or "")
    solid = params.get("solid", 0)
    return {
        "part": node.id,
        "name": node.name,
        "basis": basis,
        "source": source,
        "solid": solid if isinstance(solid, int) and not isinstance(solid, bool) else 0,
        "units": read_units(source, params.get("units")) if source else None,
        "brep": None if folder is None else str(brep_path(folder, node.id)),
        "derived": str(params.get("derived") or ""),
    }


# ---------------------------------------------------------------- the provider (in the worker)


class ShapeProvider:
    """Loads parts for one worker job: the cache when fresh, else the source (then the cache)."""

    def __init__(self) -> None:
        self.source_reads = 0
        """Source files read: what T-030 and T-031 assert stays 0 when the cache is fresh."""
        self.cache_hits = 0
        self.writes = 0
        self.bases: dict[str, str] = {}
        """The basis of each part this provider loaded, by part id: the value the shape was read or
        cached at. The topology job needs it (ADR-0022 §15.2 keys its record by basis), and a spec
        may leave ``basis`` empty while the provider derives it from the source stamp."""
        self._models: dict[tuple[str, str | None], StepModel] = {}

    def load(self, spec: dict[str, Any]) -> Any:  # Any: JSON spec in, OCP TopoDS_Shape (mm) out
        """The part ``spec`` names, in millimetres."""
        part = str(spec.get("part") or "")
        name = str(spec.get("name") or part)
        basis = str(spec.get("basis") or "")
        brep = Path(str(spec["brep"])) if spec.get("brep") else None
        if brep is not None and basis:
            entry = read_entry(brep)
            if entry is not None and entry.basis == basis:
                shape = read_shape(brep, entry)
                if shape is not None:
                    self.cache_hits += 1
                    self.bases[part] = entry.basis
                    return to_mm(shape, entry.scale)
        derived = str(spec.get("derived") or "")
        if derived:
            raise OperationInvalid(
                f"the B-rep of {name} is not in the shape cache; run operation {derived} again",
                details={"part": part, "op": derived},
            )
        source = str(spec.get("source") or "")
        if not source:
            raise GeometryFileError(
                f"{name} has no source file and no cached B-rep", details={"part": part}
            )
        model = self._model(source, spec.get("units"))
        index = int(spec.get("solid") or 0)
        if not 0 <= index < len(model.solids):
            raise GeometryFileError(
                f"{Path(source).name} no longer has solid {index + 1}", details={"part": part}
            )
        solid = model.solids[index]
        read = _source_basis(Path(source), index, solid.faces, spec.get("units"))
        if basis and read is not None and read != basis:
            raise GeometryFileError(
                f"{Path(source).name} changed since {name} was imported; import it again",
                details={"part": part, "path": source},
            )
        if brep is not None and (basis or read):
            self._write(brep, solid.shape, part, basis or str(read), model.unit_scale)
        self.bases[part] = basis or str(read or "")
        return to_mm(solid.shape, model.unit_scale)

    def _model(self, source: str, units: Any) -> StepModel:  # Any: JSON (text or null)
        """The source file read once per job, whatever the number of its parts asked for."""
        from stokes.errors import ValidationError
        from stokes.geometry.errors import GeometryError
        from stokes.geometry.readers import read_source

        key = (source, units if isinstance(units, str) and units else None)
        model = self._models.get(key)
        if model is None:
            try:
                model = read_source(source, units=key[1])
            except (GeometryError, ValidationError):
                raise
            except Exception as exc:  # OCCT's failures share no base class
                raise GeometryFileError(
                    f"OCCT could not read {Path(source).name}: {exc}", details={"path": source}
                ) from exc
            self.source_reads += 1
            self._models[key] = model
        return model

    def _write(self, brep: Path, shape: Any, part: str, basis: str, scale: float) -> None:
        """Cache what the source gave; a disk that refuses costs only the next job a read."""
        try:
            write_shape(brep, shape, part=part, basis=basis, scale=scale)
        except (OSError, GeometryFileError) as exc:
            log.warning("shape cache not written", extra=fields(part=part, error=str(exc)))
            return
        self.writes += 1


def _source_basis(source: Path, solid: int, faces: int, units: str | None = None) -> str | None:
    """The version of a solid of ``source`` as the file is now; None when it cannot be stated."""
    from stokes.geometry.files import SourceStamp
    from stokes.geometry.fingerprints import basis_for_source

    try:
        return basis_for_source(source, SourceStamp.of(source), solid, faces, units)
    except OSError:
        return None


def copy_shapes(source: Path, target: Path, parts: list[str]) -> int:
    """Copy the cache entries of ``parts`` from the folder ``source`` to ``target`` where the
    target has none or another one (Save as, or the first save of a project that had none):
    sidecar out, B-rep, sidecar in, as :func:`write_shape` writes. Returns how many it copied;
    one the disk refuses is logged and left to be rebuilt."""
    import shutil

    copied = 0
    for part in parts:
        src = brep_path(source, part)
        entry = read_entry(src)
        if entry is None:
            continue
        dst = brep_path(target, part)
        if read_entry(dst) == entry:
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            sidecar_path(dst).unlink(missing_ok=True)
            shutil.copyfile(src, dst)
            shutil.copyfile(sidecar_path(src), sidecar_path(dst))
        except OSError as exc:
            log.warning("shape cache not copied", extra=fields(part=part, error=str(exc)))
            continue
        copied += 1
    return copied
