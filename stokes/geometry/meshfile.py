"""Mesh-only part readers: STL, OBJ and 3MF, and the feature-angle face split (ADR-0021 1.2, 1.3).

A mesh format carries no B-rep, so a part read from one has no faces to name: its faces are the
regions of the triangulation that meet below a feature angle, found by :func:`split_faces` (a box
STL gives six, a fine cylinder three). The three readers return a :class:`MeshModel` — one
:class:`MeshBody` per body the file names, positions in the file's own units — and the worker
(:mod:`stokes.geometry.worker_main`) splits, shades and scales them into metres.

Import rule: numpy and the standard library only, and OCP is never imported: a mesh file has no
B-rep to read, so this module costs nothing to import in the API process or in the worker.

Every input is untrusted (CLAUDE.md §4). A binary STL's record count is validated against the file
size (``84 + 50 n``), a 3MF archive may hold no member over 512 MB uncompressed and no more than
2 GB uncompressed in total, a 3MF part may not contain a ``<!DOCTYPE`` (a DTD brings external
entities), component nesting is bounded, indices are range-checked, and no reader accepts a NaN or
an infinite coordinate.
"""

from __future__ import annotations

import io
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
from numpy.typing import NDArray

from stokes.errors import ValidationError
from stokes.geometry.errors import GeometryFileError
from stokes.geometry.files import MAX_SOURCE_BYTES
from stokes.geometry.mesh import F32, U32, Vec3, weld

F64 = NDArray[np.float64]
I64 = NDArray[np.int64]
Float = NDArray[np.floating]  # Any: float32 or float64

DEFAULT_UNITS = "mm"
"""The length unit of a mesh format that does not declare one (STL, OBJ), as for BREP."""
FEATURE_ANGLE_DEG = 30.0
"""Two triangles meeting at a sharper angle than this belong to different faces (the region angle
``tag.grow`` and ``tag.region`` use)."""
MAX_3MF_MEMBER_BYTES = 512 * 1024**2
"""A single 3MF archive member may not claim more than this uncompressed (a zip-bomb bound)."""
MAX_3MF_TOTAL_BYTES = 2 * 1024**3
"""All members of one 3MF archive together may not claim more than this uncompressed."""
MAX_3MF_DEPTH = 8
"""How deep 3MF components may nest before the file is refused (a self-referencing cycle)."""

_STL_HEADER_BYTES = 84
_STL_RECORD = np.dtype([("normal", "<f4", (3,)), ("corners", "<f4", (3, 3)), ("attr", "<u2")])
_3MF_MODEL_PATH = "3D/3dmodel.model"
_3MF_RELS_PATH = "_rels/.rels"
_3MF_UNITS: dict[str, str] = {
    "micron": "um",
    "millimeter": "mm",
    "centimeter": "cm",
    "inch": "in",
    "foot": "ft",
    "meter": "m",
}
_OBJECT_FALLBACK = "object"


@dataclass(frozen=True)
class MeshBody:
    """One body of a mesh file: its name, its triangles and its vertices, in the file's units."""

    name: str
    positions: F32
    """(n, 3) in file units"""
    triangles: U32
    """(m, 3) indices into ``positions``"""
    color: Vec3 | None = None


@dataclass(frozen=True)
class MeshModel:
    """The bodies of a mesh file, the units they are in and the reader's notes."""

    bodies: tuple[MeshBody, ...]
    units: str = DEFAULT_UNITS
    warnings: tuple[str, ...] = ()


def unit_scale(units: str | None) -> float:
    """Metres per one of ``units`` (default millimetres); :class:`ValidationError` when the text
    names no length unit. The pint registry is the unit table (ADR-0012), as for BREP."""
    from stokes.geometry.brepfile import unit_scale as metres_per

    return metres_per(units or DEFAULT_UNITS)


# ------------------------------------------------------------------ STL


def read_stl(path: str | Path, units: str | None = DEFAULT_UNITS) -> MeshModel:
    """The bodies of a binary or ASCII STL file, in ``units`` (default millimetres).

    Binary STL is recognised by its size: an 80-byte header, a ``uint32`` triangle count and 50
    bytes per record, so a file whose size is not ``84 + 50 n`` is read as text. An ASCII file
    gives one body per ``solid <name>`` block; a body without triangles is dropped, and a file
    with none at all is refused.
    """
    source = Path(path)
    unit_scale(units)
    data = _read_bytes(source)
    if len(data) >= _STL_HEADER_BYTES:
        count = int(np.frombuffer(data[80:84], dtype="<u4")[0])
        if len(data) == _STL_HEADER_BYTES + _STL_RECORD.itemsize * count:
            records = np.frombuffer(data, dtype=_STL_RECORD, count=count, offset=_STL_HEADER_BYTES)
            positions = np.ascontiguousarray(records["corners"]).reshape(-1, 3)
            _finite(source, positions)
            triangles = np.arange(positions.shape[0], dtype=np.uint32).reshape(-1, 3)
            name = _header_name(data) or source.stem or "solid"
            return MeshModel((MeshBody(name, positions, triangles),), units or DEFAULT_UNITS)
    bodies = _ascii_stl(source, data)
    return MeshModel(tuple(bodies), units or DEFAULT_UNITS)


def _header_name(data: bytes) -> str:
    """The name a binary STL's free-text 80-byte header carries after ``solid``, or none."""
    text = data[:80].decode("utf-8", errors="replace")
    marker = text.lower().find("solid ")
    if marker < 0:
        return ""
    return text[marker + len("solid ") :].strip("\x00 \t\r\n")[:128]


def _ascii_stl(source: Path, data: bytes) -> list[MeshBody]:
    text = data.decode("utf-8", errors="replace")
    bodies: list[MeshBody] = []
    name = source.stem or "solid"
    points: list[tuple[float, float, float]] = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("solid") and (len(stripped) == 5 or stripped[5].isspace()):
            if inside:
                bodies.extend(_stl_body(source, name, points))
            name, points, inside = stripped[5:].strip() or name, [], True
        elif low.startswith("endsolid"):
            if inside:
                bodies.extend(_stl_body(source, name, points))
            name, points, inside = source.stem or "solid", [], False
        elif low.startswith("vertex") and inside:
            parts = stripped.split()
            if len(parts) < 4:
                raise GeometryFileError(
                    f"{source.name} has a vertex without three coordinates",
                    details={"path": str(source)},
                )
            try:
                points.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError as exc:
                raise GeometryFileError(
                    f"{source.name} has a vertex that is not a number",
                    details={"path": str(source)},
                ) from exc
    if inside:
        bodies.extend(_stl_body(source, name, points))
    if not bodies:
        raise GeometryFileError(f"{source.name} holds no triangles", details={"path": str(source)})
    return bodies


def _stl_body(source: Path, name: str, points: list[tuple[float, float, float]]) -> list[MeshBody]:
    """One ASCII solid as a body, or none when it holds no vertices."""
    if not points:
        return []
    if len(points) % 3:
        raise GeometryFileError(
            f"{source.name} holds {len(points)} corners, which is not whole triangles",
            details={"path": str(source)},
        )
    positions = np.asarray(points, dtype=np.float32)
    _finite(source, positions)
    triangles = np.arange(positions.shape[0], dtype=np.uint32).reshape(-1, 3)
    return [MeshBody(name, positions, triangles)]


# ------------------------------------------------------------------ OBJ


def read_obj(path: str | Path, units: str | None = DEFAULT_UNITS) -> MeshModel:
    """The bodies of a Wavefront OBJ file, in ``units`` (default millimetres).

    ``v`` gives vertices and ``f`` faces, fan-triangulated when they name more than three corners;
    ``a/b/c`` takes the vertex and a negative index counts back from the vertices read so far.
    ``o`` and ``g`` start a body, and the faces before any belong to one named after the file.
    Other keywords are ignored, and a body without faces is dropped.
    """
    source = Path(path)
    unit_scale(units)
    data = _read_bytes(source)
    text = data.decode("utf-8", errors="replace")
    vertices: list[tuple[float, float, float]] = []
    bodies: list[tuple[str, list[tuple[int, int, int]]]] = []
    current = -1
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0]
        if key == "v":
            vertices.append(_obj_vertex(source, parts))
        elif key in ("o", "g"):
            bodies.append((" ".join(parts[1:]).strip() or _OBJECT_FALLBACK, []))
            current = len(bodies) - 1
        elif key.startswith("f"):
            if current < 0:
                bodies.append((source.stem or _OBJECT_FALLBACK, []))
                current = len(bodies) - 1
            bodies[current][1].extend(_obj_face(source, parts[1:], len(vertices)))
    known = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    found = [_obj_body(source, name, faces, known) for name, faces in bodies if faces]
    if not found:
        raise GeometryFileError(f"{source.name} holds no faces", details={"path": str(source)})
    return MeshModel(tuple(found), units or DEFAULT_UNITS)


def _obj_vertex(source: Path, parts: list[str]) -> tuple[float, float, float]:
    if len(parts) < 4:
        raise GeometryFileError(
            f"{source.name} has a vertex without three coordinates", details={"path": str(source)}
        )
    try:
        return (float(parts[1]), float(parts[2]), float(parts[3]))
    except ValueError as exc:
        raise GeometryFileError(
            f"{source.name} has a vertex that is not a number", details={"path": str(source)}
        ) from exc


def _obj_face(source: Path, corners: list[str], vertices: int) -> list[tuple[int, int, int]]:
    """One ``f`` line's corners as triangles, in vertex-index space."""
    if len(corners) < 3:
        raise GeometryFileError(
            f"{source.name} has a face with {len(corners)} corners", details={"path": str(source)}
        )
    indices = []
    for corner in corners:
        text = corner.split("/", 1)[0]
        try:
            index = int(text)
        except ValueError as exc:
            raise GeometryFileError(
                f"{source.name} has a face corner that is not an index: {corner!r}",
                details={"path": str(source)},
            ) from exc
        index += vertices if index < 0 else -1
        if not 0 <= index < vertices:
            raise GeometryFileError(
                f"{source.name} refers to vertex {index + 1}, which it does not have",
                details={"path": str(source)},
            )
        indices.append(index)
    return [(indices[0], indices[k], indices[k + 1]) for k in range(1, len(indices) - 1)]


def _obj_body(
    source: Path, name: str, faces: list[tuple[int, int, int]], vertices: F64
) -> MeshBody:
    flat = np.asarray(faces, dtype=np.int64).reshape(-1)
    used, inverse = np.unique(flat, return_inverse=True)
    positions = np.ascontiguousarray(vertices[used], dtype=np.float32)
    _finite(source, positions)
    triangles = np.asarray(inverse, dtype=np.uint32).reshape(-1).reshape(-1, 3)
    return MeshBody(name, positions, triangles)


# ------------------------------------------------------------------ 3MF


def read_3mf(path: str | Path) -> MeshModel:
    """The build items of a 3MF file as bodies, in the units its model declares.

    The model part is ``3D/3dmodel.model``, or the path the ``_rels/.rels`` relationship names.
    Every build item becomes a body: its object's mesh, and the meshes of the components it
    nests, each placed by the item's and the components' 3x4 transforms. An object's name names
    the body. The archive and the XML are checked before anything is parsed (module docstring).
    """
    source = Path(path)
    data = _read_bytes(source)
    with _archive(source, data) as archive:
        part = _model_part(source, archive)
        xml = _member_bytes(source, archive, part)
    if b"<!DOCTYPE" in xml:
        raise GeometryFileError(
            f"{source.name} declares a DTD; 3MF files may not", details={"path": str(source)}
        )
    root = _parse(source, xml)
    units, warnings = _3mf_units(root.get("unit"))
    objects = _3mf_objects(source, root)
    items = [(item.get("objectid") or "", item.get("transform")) for item in _build_items(root)]
    if not items:
        items = [(_top_object(objects), None)]
    bodies: list[MeshBody] = []
    for index, (object_id, transform) in enumerate(items, start=1):
        positions, triangles = _collect(object_id, _matrix(source, transform), 0, objects)
        if triangles.shape[0]:
            name = objects[object_id].name or f"{_OBJECT_FALLBACK}_{index}"
            bodies.append(MeshBody(name, positions, triangles))
    if not bodies:
        raise GeometryFileError(f"{source.name} builds no triangles", details={"path": str(source)})
    return MeshModel(tuple(bodies), units, warnings)


@dataclass(frozen=True)
class _Object:
    """One 3MF object: its own mesh and the components it places."""

    name: str
    positions: F64
    triangles: I64
    components: tuple[tuple[str, F64], ...] = ()


def _archive(source: Path, data: bytes) -> zipfile.ZipFile:
    """The 3MF archive, with every member's uncompressed size bounded before it is read."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise GeometryFileError(f"{source.name} is not a zip archive") from exc
    total = 0
    for info in archive.infolist():
        size = info.file_size
        if size > MAX_3MF_MEMBER_BYTES:
            raise GeometryFileError(
                f"{source.name} holds the member {info.filename}, {size} bytes uncompressed; "
                f"the limit is {MAX_3MF_MEMBER_BYTES}",
                details={"path": str(source), "member": info.filename, "size": size},
            )
        total += size
        if total > MAX_3MF_TOTAL_BYTES:
            raise GeometryFileError(
                f"{source.name} claims more than {MAX_3MF_TOTAL_BYTES} bytes uncompressed",
                details={"path": str(source)},
            )
    return archive


def _model_part(source: Path, archive: zipfile.ZipFile) -> str:
    names = set(archive.namelist())
    if _3MF_MODEL_PATH in names:
        return _3MF_MODEL_PATH
    if _3MF_RELS_PATH in names:
        rels = _member_bytes(source, archive, _3MF_RELS_PATH)
        if b"<!DOCTYPE" in rels:
            raise GeometryFileError(
                f"{source.name} declares a DTD; 3MF files may not", details={"path": str(source)}
            )
        for relation in _parse(source, rels).iter():
            if _local(relation.tag) != "Relationship":
                continue
            kind = relation.get("Type") or ""
            target = (relation.get("Target") or "").lstrip("/")
            if kind.endswith("3dmodel") and target and target in names:
                return target
    raise GeometryFileError(f"{source.name} holds no 3D model part", details={"path": str(source)})


def _member_bytes(source: Path, archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except (KeyError, zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise GeometryFileError(
            f"cannot read {name} in {source.name}", details={"path": str(source)}
        ) from exc


def _parse(source: Path, xml: bytes) -> ElementTree.Element:
    """The parsed XML. Every caller has refused a ``<!DOCTYPE`` already (module docstring), so
    the DTD and its entities are gone before expat sees the bytes."""
    try:
        return ElementTree.fromstring(xml)  # noqa: S314 - a DTD is refused before this point
    except ElementTree.ParseError as exc:
        raise GeometryFileError(
            f"{source.name} holds malformed XML: {exc}", details={"path": str(source)}
        ) from exc


def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _3mf_units(unit: str | None) -> tuple[str, tuple[str, ...]]:
    found = (unit or "millimeter").strip().lower()
    symbol = _3MF_UNITS.get(found)
    if symbol is None:
        return "mm", (
            f"the 3MF unit {found!r} is not one of micron, millimeter, centimeter, inch, foot or "
            "meter; read as millimetres",
        )
    return symbol, ()


def _3mf_objects(source: Path, root: ElementTree.Element) -> dict[str, _Object]:
    objects: dict[str, _Object] = {}
    for element in root.iter():
        if _local(element.tag) != "object":
            continue
        oid = element.get("id") or ""
        if not oid:
            raise GeometryFileError(
                f"{source.name} has an object without an id", details={"path": str(source)}
            )
        positions, triangles = _object_mesh(source, element)
        components = tuple(
            (_component_id(source, component), _matrix(source, component.get("transform")))
            for group in element
            if _local(group.tag) == "components"
            for component in group
            if _local(component.tag) == "component"
        )
        objects[oid] = _Object(element.get("name") or "", positions, triangles, components)
    if not objects:
        raise GeometryFileError(f"{source.name} holds no objects", details={"path": str(source)})
    return objects


def _object_mesh(source: Path, element: ElementTree.Element) -> tuple[F64, I64]:
    """An object's own mesh as (positions, triangles); empty for a component-only object."""
    for child in element:
        if _local(child.tag) != "mesh":
            continue
        points: list[tuple[float, float, float]] = []
        corners: list[tuple[int, int, int]] = []
        for node in child.iter():  # <vertices>/<vertex> and <triangles>/<triangle>, any depth
            kind = _local(node.tag)
            if kind == "vertex":
                points.append(
                    (
                        _number(source, node, "x"),
                        _number(source, node, "y"),
                        _number(source, node, "z"),
                    )
                )
            elif kind == "triangle":
                corners.append(
                    (
                        _integer(source, node, "v1"),
                        _integer(source, node, "v2"),
                        _integer(source, node, "v3"),
                    )
                )
        positions = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        triangles = np.asarray(corners, dtype=np.int64).reshape(-1, 3)
        if triangles.size and (
            int(triangles.min()) < 0 or int(triangles.max()) >= positions.shape[0]
        ):
            raise GeometryFileError(
                f"{source.name} has a triangle past its vertices", details={"path": str(source)}
            )
        _finite(source, positions)
        return positions, triangles
    return np.zeros((0, 3), dtype=np.float64), np.zeros((0, 3), dtype=np.int64)


def _component_id(source: Path, element: ElementTree.Element) -> str:
    oid = element.get("objectid") or ""
    if not oid:
        raise GeometryFileError(
            f"{source.name} has a component without an object", details={"path": str(source)}
        )
    return oid


def _build_items(root: ElementTree.Element) -> list[ElementTree.Element]:
    for element in root:
        if _local(element.tag) == "build":
            return [child for child in element if _local(child.tag) == "item"]
    return []


def _top_object(objects: dict[str, _Object]) -> str:
    """The first object no component places, for a file without build items (the 3MF default)."""
    placed = {oid for obj in objects.values() for oid, _ in obj.components}
    return next((oid for oid in objects if oid not in placed), next(iter(objects)))


def _collect(
    object_id: str, transform: F64, depth: int, objects: dict[str, _Object]
) -> tuple[F32, U32]:
    """An object's triangles and its components' placed triangles, as (positions, triangles)."""
    obj = objects.get(object_id)
    if obj is None:
        raise GeometryFileError(f"a component names object {object_id}, which the file lacks")
    if depth > MAX_3MF_DEPTH:
        raise GeometryFileError(f"components nest deeper than {MAX_3MF_DEPTH}")
    chunks: list[tuple[F64, I64]] = []
    for oid, matrix in obj.components:
        placed, faces = _collect(oid, matrix @ transform, depth + 1, objects)
        chunks.append((placed.astype(np.float64), faces.astype(np.int64)))
    if obj.triangles.shape[0]:
        chunks.append((_placed(obj.positions, transform), obj.triangles))
    filled = [(section, tris) for section, tris in chunks if tris.shape[0]]
    if not filled:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.uint32)
    positions: list[F64] = []
    triangles: list[I64] = []
    base = 0
    for section, tris in filled:
        positions.append(section)
        triangles.append(tris + base)
        base += section.shape[0]
    return (
        np.concatenate(positions).astype(np.float32),
        np.concatenate(triangles).astype(np.uint32),
    )


def _placed(points: F64, transform: F64) -> F64:
    """``points`` placed by a 3MF transform (row vectors: ``[x y z 1] * M``)."""
    if points.shape[0] == 0:
        return points
    homo = np.concatenate([points, np.ones((points.shape[0], 1))], axis=1)
    return (homo @ transform)[:, :3]


def _matrix(source: Path, text: str | None) -> F64:
    """A 3MF transform (12 numbers, row-major) as a 4x4 matrix in row-vector convention."""
    if not text:
        return np.eye(4)
    try:
        values = [float(value) for value in text.split()]
    except ValueError as exc:
        raise GeometryFileError(
            f"{source.name} has a transform that is not a number", details={"path": str(source)}
        ) from exc
    if len(values) != 12 or not all(math.isfinite(value) for value in values):
        raise GeometryFileError(
            f"{source.name} has a malformed transform", details={"path": str(source)}
        )
    matrix = np.eye(4)
    matrix[0, :3] = values[0:3]
    matrix[1, :3] = values[3:6]
    matrix[2, :3] = values[6:9]
    matrix[3, :3] = values[9:12]
    return matrix


def _number(source: Path, element: ElementTree.Element, name: str) -> float:
    text = element.get(name)
    try:
        value = float(text)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise GeometryFileError(
            f"{source.name} has a {_local(element.tag)} without a {name} coordinate",
            details={"path": str(source)},
        ) from exc
    if not math.isfinite(value):
        raise GeometryFileError(
            f"{source.name} has a non-finite coordinate", details={"path": str(source)}
        )
    return value


def _integer(source: Path, element: ElementTree.Element, name: str) -> int:
    text = element.get(name)
    try:
        return int(text)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise GeometryFileError(
            f"{source.name} has a triangle without a vertex index",
            details={"path": str(source)},
        ) from exc


# ------------------------------------------------------------------ the face split


def split_faces(
    positions: F32 | F64, triangles: U32 | I64, angle_deg: float = FEATURE_ANGLE_DEG
) -> U32:
    """The face id of every triangle: the regions that meet below a feature angle.

    Corners are welded by position, the dihedral angle at every edge shared by exactly two
    triangles is taken from their facet normals, the two triangles are linked when it is below
    ``angle_deg``, and connected components are renumbered by first appearance. A box gives six
    faces and a fine cylinder three (the side, and each cap). Face ids run 0 to faces - 1.
    """
    if not 0.0 < angle_deg <= 180.0:
        raise ValidationError(
            f"a feature angle lies in (0, 180]; got {angle_deg!r}", details={"angle": angle_deg}
        )
    count = int(triangles.shape[0])
    if count == 0:
        return np.zeros(0, dtype=np.uint32)
    # stokes.model first: a process whose first import is this module (the worker) must load it
    # before stokes.geometry.model, which stokes.model's schema imports back. Callers in the API
    # process have it already; the import here is for the order alone.
    import stokes.model  # noqa: F401
    from stokes.geometry.selection import components
    from stokes.geometry.topology import Surface, edges_of, facets_of

    corners = np.ascontiguousarray(np.asarray(positions, dtype=np.float32)[triangles]).reshape(
        -1, 3
    )
    welded, indices = weld(corners, np.zeros(corners.shape[0], dtype=np.uint32))
    surface = Surface(
        welded,
        indices.astype(np.int64),
        np.arange(count, dtype=np.int64),
        np.zeros(count, dtype=np.int64),
        np.asarray([0, welded.shape[0]], dtype=np.int64),
        np.asarray([0, count], dtype=np.int64),
    )
    edges = edges_of(surface)
    facets = facets_of(surface)
    shared = np.flatnonzero(edges.count == 2)
    starts = edges.start[shared]
    left = edges.side[starts] // 3
    right = edges.side[starts + 1] // 3
    cos = np.einsum("ij,ij->i", facets.normal[left], facets.normal[right])
    keep = cos > math.cos(math.radians(angle_deg))
    links = np.stack([left[keep], right[keep]], axis=1).astype(np.int64)
    labels = components(links, count)
    return np.asarray(np.unique(labels, return_inverse=True)[1], dtype=np.uint32).reshape(-1)


# ------------------------------------------------------------------ shared


def _read_bytes(source: Path) -> bytes:
    """The whole file, refused over the source size limit (1 GiB, as for STEP)."""
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise GeometryFileError(
            f"cannot read {source}: no such file", details={"path": str(source)}
        ) from exc
    if size > MAX_SOURCE_BYTES:
        raise GeometryFileError(
            f"{source.name} is {size} bytes; the limit is {MAX_SOURCE_BYTES}",
            details={"path": str(source), "size": size},
        )
    try:
        return source.read_bytes()
    except OSError as exc:
        raise GeometryFileError(
            f"cannot read {source.name}: {exc.strerror or exc}", details={"path": str(source)}
        ) from exc


def _finite(source: Path, positions: Float) -> None:
    if positions.size and not np.isfinite(positions).all():
        raise GeometryFileError(
            f"{source.name} has a NaN or infinite vertex", details={"path": str(source)}
        )
