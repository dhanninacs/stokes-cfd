"""Binary mesh transport, versions 1 and 2: the bytes the viewport fetches and the cache stores.

Layout (little-endian)::

    u32 header_length
    header JSON (UTF-8, zero-padded to a multiple of 4; header_length includes the padding)
    per group, in header order:
        positions f32[3N], normals f32[3N], indices u32[3M]
        version 2 only, when the group's "faceIds" is true: face ids u32[M]

Version 1 header: ``{"version": 1, "groups": [{"name", "kind", "color", "vertices",
"triangles", "offset"}]}``; ``offset`` is the byte offset of the group's first buffer from the
start of the data section (just after the header). Version 2 adds ``units`` (of the positions,
always ``"m"``), ``bbox`` (``{"min": [x, y, z], "max": [x, y, z]}`` or null), ``faces`` (B-rep
faces in the part) and optionally ``source`` (``{"name", "size", "mtime_ns"}`` of the file the
mesh came from); each group adds ``faceIds`` (bool), ``faces``, ``faceBase`` (its face ids lie
in ``[faceBase, faceBase + faces)``) and ``quality`` (``"coarse"`` or ``"fine"``).

The codec lives in :mod:`stokes.geometry` rather than the server because the geometry store
writes and reads the same bytes as its cache; :mod:`stokes.server.geometry` re-exports it.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Sequence
from typing import Any

import numpy as np

from stokes.geometry.errors import MeshFormatError
from stokes.geometry.files import SourceStamp
from stokes.geometry.mesh import Bbox, PartMesh, TriMesh, bbox_union

VERSIONS = (1, 2)
MAX_HEADER_BYTES = 1 << 24
_F4 = np.dtype("<f4")
_U4 = np.dtype("<u4")


def _bbox_json(box: Bbox | None) -> dict[str, list[float]] | None:
    if box is None:
        return None
    return {"min": [round(v, 9) for v in box[0]], "max": [round(v, 9) for v in box[1]]}


def _color_v2(color: Sequence[float]) -> list[float]:
    return [round(min(1.0, max(0.0, float(c))), 6) for c in color]


def pack_meshes(
    meshes: Sequence[TriMesh],
    version: int = 1,
    *,
    units: str = "m",
    source: SourceStamp | None = None,
    basis: str | None = None,
) -> bytes:
    """Pack meshes as one transport buffer. Version 1 is byte-for-byte the original format."""
    if version not in VERSIONS:
        raise MeshFormatError(f"unknown transport version {version}")
    groups: list[dict[str, Any]] = []
    chunks: list[bytes] = []
    offset = 0
    for mesh in meshes:
        pos = np.ascontiguousarray(mesh.positions, dtype="<f4").tobytes()
        nrm = np.ascontiguousarray(mesh.normals, dtype="<f4").tobytes()
        idx = np.ascontiguousarray(mesh.indices, dtype="<u4").tobytes()
        group: dict[str, Any] = {
            "name": mesh.name,
            "kind": mesh.kind,
            "color": list(mesh.color) if version == 1 else _color_v2(mesh.color),
            "vertices": mesh.vertex_count,
            "triangles": mesh.triangle_count,
            "offset": offset,
        }
        buffers = [pos, nrm, idx]
        if version == 2:
            has_ids = mesh.face_ids is not None
            group.update(
                {
                    "faceIds": has_ids,
                    "faces": mesh.faces,
                    "faceBase": mesh.face_base,
                    "quality": mesh.quality,
                }
            )
            if mesh.face_ids is not None:
                buffers.append(np.ascontiguousarray(mesh.face_ids, dtype="<u4").tobytes())
        groups.append(group)
        chunks.extend(buffers)
        offset += sum(len(b) for b in buffers)
    head: dict[str, Any]
    if version == 1:
        head = {"version": 1, "groups": groups}
    else:
        head = {
            "version": 2,
            "units": units,
            "bbox": _bbox_json(bbox_union(m.bbox for m in meshes)),
            "faces": sum(m.faces for m in meshes),
            "groups": groups,
        }
        if source is not None:
            head["source"] = source.to_json()
        # The B-rep version the triangles were made from, so a cache can tell "the same file" from
        # "the same geometry" (Claude's defect 1). Absent in records written before this existed,
        # which read as "cannot be vouched for" rather than as current.
        if basis:
            head["basis"] = basis
    header = json.dumps(head).encode("utf-8")
    header += b"\0" * ((-len(header)) % 4)
    return struct.pack("<I", len(header)) + header + b"".join(chunks)


def pack_part(part: PartMesh) -> bytes:
    """A part as transport version 2."""
    return pack_meshes(
        part.solids, version=2, units=part.units, source=part.source, basis=part.basis
    )


def read_header(data: bytes | memoryview) -> tuple[dict[str, Any], int]:
    """The parsed header and the byte offset where the data section starts."""
    if len(data) < 4:
        raise MeshFormatError("mesh buffer is too short")
    (length,) = struct.unpack_from("<I", data, 0)
    if length > MAX_HEADER_BYTES or 4 + length > len(data) or length % 4:
        raise MeshFormatError("mesh header is truncated or misaligned")
    try:
        header = json.loads(bytes(data[4 : 4 + length]).rstrip(b"\0").decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MeshFormatError(f"mesh header is not JSON: {exc}") from exc
    if not isinstance(header, dict) or header.get("version") not in VERSIONS:
        raise MeshFormatError(f"unsupported mesh version {header.get('version')!r}")
    if not isinstance(header.get("groups"), list):
        raise MeshFormatError("mesh header lists no groups")
    return header, 4 + length


def _count(group: dict[str, Any], key: str, default: int | None = None) -> int:
    value = group.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MeshFormatError(f"mesh group {key!r} must be a non-negative integer")
    return value


def _group(
    data: bytes | memoryview, start: int, group: Any, version: int
) -> TriMesh:  # Any: header JSON
    if not isinstance(group, dict):
        raise MeshFormatError("mesh group must be an object")
    name, kind, color = group.get("name"), group.get("kind"), group.get("color")
    if not isinstance(name, str) or not isinstance(kind, str):
        raise MeshFormatError("mesh group without a name or kind")
    if (
        not isinstance(color, list)
        or len(color) != 3
        or not all(isinstance(c, int | float) and 0.0 <= c <= 1.0 for c in color)
    ):
        raise MeshFormatError(f"mesh group {name!r}: colour must be three numbers from 0 to 1")
    vertices, triangles = _count(group, "vertices"), _count(group, "triangles")
    offset = _count(group, "offset")
    has_ids = version == 2 and group.get("faceIds") is True
    faces = _count(group, "faces", 0) if version == 2 else 0
    face_base = _count(group, "faceBase", 0) if version == 2 else 0
    quality = group.get("quality", "") if version == 2 else ""
    if not isinstance(quality, str):
        raise MeshFormatError(f"mesh group {name!r}: quality must be a string")
    base = start + offset
    end = base + vertices * 24 + triangles * (16 if has_ids else 12)
    if offset % 4 or end > len(data):
        raise MeshFormatError(f"mesh group {name!r}: buffers lie outside the data")
    positions = np.frombuffer(data, _F4, vertices * 3, base).reshape(vertices, 3)
    normals = np.frombuffer(data, _F4, vertices * 3, base + vertices * 12).reshape(vertices, 3)
    indices = np.frombuffer(data, _U4, triangles * 3, base + vertices * 24).reshape(triangles, 3)
    if triangles and (vertices == 0 or int(indices.max()) >= vertices):
        raise MeshFormatError(f"mesh group {name!r}: an index points past the vertices")
    face_ids = None
    if has_ids:
        face_ids = np.frombuffer(data, _U4, triangles, base + vertices * 24 + triangles * 12)
        if triangles and (
            int(face_ids.min()) < face_base or int(face_ids.max()) >= face_base + faces
        ):
            raise MeshFormatError(f"mesh group {name!r}: a face id lies outside its range")
    return TriMesh(
        name,
        kind,
        (float(color[0]), float(color[1]), float(color[2])),
        positions.astype(np.float32, copy=False),
        normals.astype(np.float32, copy=False),
        indices.astype(np.uint32, copy=False),
        None if face_ids is None else face_ids.astype(np.uint32, copy=False),
        faces,
        face_base,
        quality,
    )


def unpack(data: bytes | memoryview) -> PartMesh:
    """Parse and validate a version 1 or 2 buffer. The arrays are read-only views of ``data``."""
    header, start = read_header(data)
    version = int(header["version"])
    solids = tuple(_group(data, start, g, version) for g in header["groups"])
    units = header.get("units", "m") if version == 2 else "m"
    if not isinstance(units, str):
        raise MeshFormatError("mesh units must be a string")
    raw_basis = header.get("basis")
    basis = raw_basis if isinstance(raw_basis, str) and raw_basis else None
    return PartMesh(solids, units, SourceStamp.from_json(header.get("source")), basis)
