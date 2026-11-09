"""Read a ``STKMSH1`` surface artifact and turn one patch into viewport payloads (T-210).

The agent writes the artifact (``agent/stokes_agent/extract.py``): the 8-byte magic
``b"STKMSH1\\0"``, a ``u32`` header length, a JSON header padded to a multiple of 4 bytes, then for
each patch, at its ``offset`` from the start of the data section, ``f32[3P]`` positions,
``u32[3T]`` triangles and ``u32[2E]`` edges. The desktop never reads ``polyMesh``; this is the
whole of what it knows about a mesh.

**The buffer is untrusted.** It came over a connection from a host and sits in a cache file, so
:func:`read_header` checks every offset and count against the length of the buffer before anything
is sliced, and refuses a header over 16 MiB or a version it does not know with
:class:`~stokes.geometry.errors.MeshFormatError`. :func:`patch_arrays` checks that every triangle
and edge index names a point of the patch, because the browser would otherwise be handed an index
buffer that reads outside its vertex buffer.

Two payloads leave this module:

* :func:`patch_transport`: one patch as **transport version 2** (:mod:`stokes.geometry.transport`),
  a single group of ``kind: "mesh"`` whose face ids are all 0. **The patch is one PICKING UNIT**, so
  picking a patch picks the patch — which is not the same as it having one face: a `walls` patch of
  10,000 faces is one picking unit here, and the transport's ``faces`` is 1 for it. **The real face
  count is on the ``mesh_patch`` node** (``params["faces"]``, from the extraction), which is where a
  legend or a quality summary reads it. Do not use ``group.faces`` for that. The format is the
  existing
  one and is not changed.
* :func:`patch_edges`: the patch's cell edges in their own small codec, served on their own route::

      u32  header_length
      header JSON (UTF-8) {"version": 1, "units": "m", "segments": E}, padded with spaces to a
           multiple of 4; header_length includes the padding
      f32[6E]  the two end points of each segment, (x0 y0 z0 x1 y1 z1), little-endian

numpy is imported here and nowhere in the start-up path: import this module lazily.
"""

from __future__ import annotations

import json
import struct
from typing import Any

import numpy as np

from stokes.geometry.errors import MeshFormatError

MAGIC = b"STKMSH1\0"
"""The first eight bytes of every artifact."""

ARTIFACT_VERSION = 1
"""The artifact header ``version`` this reader understands."""

EDGE_MAGIC_VERSION = 1
"""The ``version`` the edge codec's header carries."""

MAX_HEADER_BYTES = 16 * 1024 * 1024
"""The largest JSON header accepted."""

MAX_PATCHES = 100_000
"""The most patches a header may list: a bound on the work and the memory of reading one."""

_F4 = np.dtype("<f4")
_U4 = np.dtype("<u4")
_COUNT_KEYS = ("points", "triangles", "edges", "faces", "offset")


def read_header(data: bytes | memoryview) -> dict[str, Any]:
    """The artifact's header, validated against ``len(data)``.

    The returned dict is the file's own header (``version``, ``units``, ``bbox``, ``patches``) plus
    ``data_start``: the byte offset where the data section begins. Raises :class:`MeshFormatError`
    for a wrong magic, a truncated buffer, a header over :data:`MAX_HEADER_BYTES`, an unknown
    version, a malformed patch entry, or any patch whose arrays run past the end of the buffer.
    """
    size = len(data)
    if size < 12 or bytes(data[:8]) != MAGIC:
        raise MeshFormatError("not a STKMSH1 surface artifact")
    length = struct.unpack_from("<I", data, 8)[0]
    if length > MAX_HEADER_BYTES:
        raise MeshFormatError(f"the artifact header is {length} bytes, over the limit")
    start = 12 + length
    if start > size:
        raise MeshFormatError("the artifact is shorter than its header")
    try:
        header = json.loads(bytes(data[12:start]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MeshFormatError(f"the artifact header is not JSON: {exc}") from exc
    if not isinstance(header, dict):
        raise MeshFormatError("the artifact header is not an object")
    if header.get("version") != ARTIFACT_VERSION:
        raise MeshFormatError(f"unknown artifact version {header.get('version')!r}")
    patches = header.get("patches")
    if not isinstance(patches, list) or len(patches) > MAX_PATCHES:
        raise MeshFormatError("the artifact header has no usable patch list")
    available = size - start
    for position, patch in enumerate(patches):
        _check_patch(patch, position, available)
    header["data_start"] = start
    return header


def _check_patch(patch: Any, position: int, available: int) -> None:
    """Refuse a patch entry that is malformed or whose arrays leave the data section."""
    if not isinstance(patch, dict) or not isinstance(patch.get("name"), str):
        raise MeshFormatError(f"patch {position} has no name")
    for key in _COUNT_KEYS:
        value = patch.get(key)
        if type(value) is not int or value < 0:
            raise MeshFormatError(f"patch {position}: {key} is not a count")
    end = patch["offset"] + 12 * patch["points"] + 12 * patch["triangles"] + 8 * patch["edges"]
    if end > available:
        raise MeshFormatError(f"patch {position} runs past the end of the artifact")
    if patch["triangles"] and not patch["points"]:
        raise MeshFormatError(f"patch {position} has triangles and no points")


def _patch(header: dict[str, Any], index: int) -> dict[str, Any]:
    patches = header["patches"]
    if type(index) is not int or not 0 <= index < len(patches):
        raise MeshFormatError(f"the artifact has no patch {index}")
    patch: dict[str, Any] = patches[index]
    return patch


def patch_arrays(
    data: bytes | memoryview, header: dict[str, Any], index: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(positions (P, 3) f32, triangles (T, 3) u32, edges (E, 2) u32)`` of patch ``index``.

    Views of ``data``, not copies. ``header`` must be the result of :func:`read_header` of the same
    buffer. Raises :class:`MeshFormatError` when an index points past the patch's points.
    """
    patch = _patch(header, index)
    base = header["data_start"] + patch["offset"]
    points, triangles, edges = patch["points"], patch["triangles"], patch["edges"]
    positions = np.frombuffer(data, dtype=_F4, count=points * 3, offset=base).reshape(-1, 3)
    triangle_at = base + points * 12
    tris = np.frombuffer(data, dtype=_U4, count=triangles * 3, offset=triangle_at).reshape(-1, 3)
    edges_at = triangle_at + triangles * 12
    segments = np.frombuffer(data, dtype=_U4, count=edges * 2, offset=edges_at).reshape(-1, 2)
    for label, indices in (("triangle", tris), ("edge", segments)):
        if indices.size and int(indices.max()) >= points:
            raise MeshFormatError(f"patch {index}: a {label} names a point that does not exist")
    return positions, tris, segments


def patch_transport(
    data: bytes | memoryview,
    header: dict[str, Any],
    index: int,
    color: tuple[float, float, float],
) -> bytes:
    """One patch as transport version 2: a single ``mesh`` group, every face id 0, units metres."""
    from stokes.geometry.mesh import TriMesh, vertex_normals
    from stokes.geometry.transport import pack_meshes

    positions, triangles, _ = patch_arrays(data, header, index)
    mesh = TriMesh(
        name=str(_patch(header, index)["name"]),
        kind="mesh",
        color=color,
        positions=positions,
        normals=vertex_normals(positions, triangles),
        indices=triangles,
        face_ids=np.zeros(triangles.shape[0], dtype=_U4),
        faces=1,
        face_base=0,
        quality="fine",
    )
    return pack_meshes([mesh], version=2, units="m")


def patch_edges(data: bytes | memoryview, header: dict[str, Any], index: int) -> bytes:
    """The patch's cell edges in the edge codec described in the module docstring."""
    positions, _, edges = patch_arrays(data, header, index)
    segments = np.ascontiguousarray(positions[edges].reshape(-1, 6), dtype=_F4)
    head = json.dumps(
        {"version": EDGE_MAGIC_VERSION, "units": "m", "segments": int(segments.shape[0])},
        separators=(",", ":"),
    ).encode("utf-8")
    head += b" " * ((-len(head)) % 4)
    return struct.pack("<I", len(head)) + head + segments.tobytes()


def unpack_edges(data: bytes | memoryview) -> np.ndarray:
    """Decode an edge payload to ``(E, 6)`` float32 (tests and tools; the viewport has its own)."""
    if len(data) < 4:
        raise MeshFormatError("the edge payload is shorter than its header")
    length = struct.unpack_from("<I", data, 0)[0]
    if length > MAX_HEADER_BYTES or 4 + length > len(data):
        raise MeshFormatError("the edge header runs past the payload")
    try:
        head = json.loads(bytes(data[4 : 4 + length]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MeshFormatError(f"the edge header is not JSON: {exc}") from exc
    if not isinstance(head, dict) or head.get("version") != EDGE_MAGIC_VERSION:
        raise MeshFormatError("unknown edge codec version")
    count = head.get("segments")
    if type(count) is not int or count < 0 or 4 + length + count * 24 != len(data):
        raise MeshFormatError("the edge payload does not match its segment count")
    return np.frombuffer(data, dtype=_F4, count=count * 6, offset=4 + length).reshape(-1, 6)
