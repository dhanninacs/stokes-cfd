"""Read a ``STKFLD1`` field artifact and turn it into what the browser draws (T-216).

The agent writes this artifact (``agent/stokes_agent/extract.py``, T-215): the 8-byte magic
``b"STKFLD1\\0"``, a ``u32`` header length, a JSON header padded with spaces to a multiple of 4
bytes, then the arrays, each at an offset **measured from the start of the data section**. Per
patch, per field in header order, ``f32[components x points]``. The slice's geometry follows at
``slice.offset`` — ``positions f32[3P]``, ``triangles u32[3T]``, ``edges u32[2E]`` — and then the
slice's own fields. **Patch order and ``points`` are the surfaces artifact's**, which is the one
invariant the point fields rest on: the desktop tints the vertices it already drew, so an array in a
different order is a wrong picture rather than a visible failure.

## The buffer is untrusted

It arrived over a connection and sits in a cache file, so :func:`read_header` checks the magic, the
version, the sizes and **every offset and count against ``len(data)``** before anything is sliced,
and refuses a header over 16 MiB with :class:`~stokes.geometry.errors.MeshFormatError`. A header is
the one thing in this format that can lie about the bytes beside it; reading whatever happened to
follow would be the failure this reader exists to prevent. It is deliberately the same style and the
same limit as :func:`stokes.mesh.surfaces.read_header`, so there is one set of rules to remember.

## What leaves here

* :func:`field_array`: one ``(patch, field)`` as a **view** — ``(points,)`` for one component,
  ``(points, 3)`` for three. A patch with no array for that field is a refusal, because the header
  says so by having no entry: the agent writes an **empty** fields dict for a patch with no field,
  never zeros.
* :func:`slice_arrays`: the slice's positions, triangles and edges, again as views.
* :func:`field_payload`: the client codec — ``u32`` header length, JSON ``{"version", "field",
  "units", "components", "count", "min", "max"}`` padded to a multiple of 4, then
  ``f32[components x count]``. T-218 validates it the way ``parseMeshEdges`` does.
* :func:`slice_transport`: the slice as the existing **transport version 2**, one group, ``kind:
  "mesh"``, every face id 0 — byte for byte the shape ``/api/mesh/{id}`` already sends, so the
  viewport has one transport reader.
* :func:`slice_edges`: the slice's cell edges in the T-210 **edge codec**, the same one
  ``/api/mesh/edges/{id}`` serves.

numpy is imported **inside the functions that need it**: ``stokes.server.app`` and
``stokes.api.commands.run`` both import this package, and neither is on a path that should pay for
numpy at start-up (``docs/engineering/performance.md``).
"""

from __future__ import annotations

import json
import math
import struct
from typing import TYPE_CHECKING, Any

from stokes.geometry.errors import MeshFormatError
from stokes.run.errors import RunError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np


class RunFieldsInvalid(RunError):
    """A field was asked for that the run's artifact does not have.

    **Not the same as "no such patch"**, which is a 404: the client named a patch the run has and a
    field that patch does not carry, so the request itself is wrong and a 400 says which. Defined
    here rather than in `stokes/run/errors.py` because it belongs to this format — the one place
    that knows what a field entry is — and the run layer only re-raises it.
    """

    code = "run.no_field"


MAGIC = b"STKFLD1\0"
"""The first eight bytes of every field artifact."""

ARTIFACT_VERSION = 1
"""The artifact header ``version`` this reader understands."""

FIELD_CODEC_VERSION = 1
"""The ``version`` the client field codec's header carries."""

EDGE_CODEC_VERSION = 1
"""The ``version`` the T-210 edge codec's header carries, kept the same on purpose."""

MAX_HEADER_BYTES = 16 * 1024 * 1024
"""The largest JSON header accepted. **The same limit as the surfaces reader.**"""

MAX_PATCHES = 100_000
"""The most patches a header may list: a bound on the work and the memory of reading one."""

SLICE_INDEX = -1
"""The patch index that names the slice in :func:`field_array`.

Negative so it can never collide with a real patch index, and a name rather than a magic tuple
because every caller has an index by the time it asks.
"""

SLICE = "slice"
"""The target a client names when it wants the slice rather than a patch
(``/api/results/field/<run>/slice``)."""

DEFAULT_SLICE_COLOR = (0.6, 0.6, 0.65)
"""The grey a slice is drawn in when the caller names no colour."""

_F4 = "<f4"
_U4 = "<u4"


def read_header(data: bytes | memoryview) -> dict[str, Any]:
    """The artifact's header, validated against ``len(data)``.

    The returned dict is the file's own header (``version``, ``time``, ``units``, ``patches``,
    ``slice``) plus ``data_start``: the byte offset where the data section begins. Raises
    :class:`MeshFormatError` for a wrong magic, a truncated buffer, a header over
    :data:`MAX_HEADER_BYTES`, an unknown version, a malformed patch or slice entry, or any array
    whose offset and count run past the end of the buffer.
    """
    size = len(data)
    if size < 12 or bytes(data[:8]) != MAGIC:
        raise MeshFormatError("not a STKFLD1 field artifact")
    length = struct.unpack_from("<I", data, 8)[0]
    if length > MAX_HEADER_BYTES:
        raise MeshFormatError(f"the field header is {length} bytes, over the limit")
    start = 12 + length
    if start > size:
        raise MeshFormatError("the field artifact is shorter than its header")
    try:
        header = json.loads(bytes(data[12:start]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MeshFormatError(f"the field header is not JSON: {exc}") from exc
    if not isinstance(header, dict):
        raise MeshFormatError("the field header is not an object")
    if header.get("version") != ARTIFACT_VERSION:
        raise MeshFormatError(f"unknown field artifact version {header.get('version')!r}")
    patches = header.get("patches")
    if not isinstance(patches, list) or len(patches) > MAX_PATCHES:
        raise MeshFormatError("the field header has no usable patch list")
    available = size - start
    for position, patch in enumerate(patches):
        _check_points(patch, position, available, what=f"patch {position}")
        _check_fields(patch.get("fields"), position, available, points=patch.get("points", 0))
    slice_entry = header.get("slice")
    if slice_entry is not None:
        _check_slice(slice_entry, available)
    header["data_start"] = start
    return header


def _check_points(entry: Any, position: int, available: int, *, what: str) -> None:
    """Refuse an entry that is not an object with a usable ``points`` count."""
    if not isinstance(entry, dict):
        raise MeshFormatError(f"{what} is not an object")
    points = entry.get("points")
    if type(points) is not int or points < 0:
        raise MeshFormatError(f"{what}: points is not a count")


def _check_fields(fields: Any, position: int, available: int, *, points: int) -> None:
    """Refuse a fields dict whose every array does not fit inside the data section."""
    if not isinstance(fields, dict):
        raise MeshFormatError(f"entry {position} has no fields object")
    for name, field in fields.items():
        if not isinstance(name, str) or not isinstance(field, dict):
            raise MeshFormatError(f"entry {position} has a malformed field")
        components = field.get("components")
        offset = field.get("offset")
        if type(components) is not int or components not in (1, 3):
            raise MeshFormatError(f"entry {position}: {name} has {components!r} components")
        if type(offset) is not int or offset < 0:
            raise MeshFormatError(f"entry {position}: {name} has no usable offset")
        for key in ("min", "max"):
            value = field.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise MeshFormatError(f"entry {position}: {name} has no {key}")
            # **NaN and the infinities are refused, not carried** (Opus item 13). `json.loads`
            # READS `NaN` - so a header could hold one - and the codec then wrote it out literally,
            # which **JavaScript's `JSON.parse` refuses**: measured, it throws
            # `Unexpected token 'N'`. The field's DATA was fine; the header alone stopped every
            # field of that patch from drawing. `finite()` existed for this and nothing called it.
            if not finite(value):
                raise MeshFormatError(
                    f"entry {position}: {name} has a non-finite {key} ({value!r}), "
                    "which no client can plot"
                )
        if offset + points * components * 4 > available:
            raise MeshFormatError(f"entry {position}: {name} runs past the end of the artifact")


def _check_slice(entry: Any, available: int) -> None:
    """Refuse a slice entry whose geometry or fields run past the end of the artifact.

    The geometry is at one offset and the index arrays follow it at widths the counts already give,
    so only ``points``, ``triangles``, ``edges`` and ``offset`` are needed to find the end of it.
    """
    counts = {}
    for key in ("points", "triangles", "edges", "offset"):
        value = entry.get(key) if isinstance(entry, dict) else None
        if type(value) is not int or value < 0:
            raise MeshFormatError(f"the slice has no usable {key}")
        counts[key] = value
    end = counts["offset"] + counts["points"] * 12 + counts["triangles"] * 12 + counts["edges"] * 8
    if end > available:
        raise MeshFormatError("the slice runs past the end of the artifact")
    _check_fields(entry.get("fields"), SLICE_INDEX, available, points=counts["points"])


def _patches(header: dict[str, Any]) -> list[dict[str, Any]]:
    patches = header.get("patches")
    if not isinstance(patches, list):
        raise MeshFormatError("the field header has no patch list")
    return patches


def _field(header: dict[str, Any], patch_index: int, field: str) -> dict[str, Any]:
    """One ``(patch, field)`` entry, or a refusal naming what is missing."""
    if patch_index == SLICE_INDEX:
        entry = header.get("slice")
        if not isinstance(entry, dict):
            raise MeshFormatError("the artifact has no slice")
        what = "the slice"
    else:
        patches = _patches(header)
        if not 0 <= patch_index < len(patches):
            raise MeshFormatError(f"the artifact has no patch {patch_index}")
        entry = patches[patch_index]
        what = f"patch {patch_index}"
    fields: Any = entry.get("fields")
    if not isinstance(fields, dict) or field not in fields:
        raise MeshFormatError(f"{what} has no field {field!r}")
    described: dict[str, Any] = fields[field]
    return described


def _view(data: bytes | memoryview, field: dict[str, Any], points: int) -> np.ndarray:
    """The float32 view a field entry describes, checked once more against the buffer.

    The header was validated on the way in; this is the same arithmetic against the same buffer, and
    it is here because :func:`field_array` is public and a caller may pass a header from anywhere.
    """
    import numpy as np

    offset, components = int(field["offset"]), int(field["components"])
    count = points * components
    start = int(field.get("_base", 0)) + offset
    if offset < 0 or start + count * 4 > len(data):
        raise MeshFormatError("a field array runs past the end of the artifact")
    return np.frombuffer(data, dtype=_F4, count=count, offset=start)


def field_array(
    data: bytes | memoryview, header: dict[str, Any], patch_index: int, field: str
) -> np.ndarray:
    """One field of one patch (or the slice at :data:`SLICE_INDEX`) as a **view** of ``data``.

    Shape ``(points,)`` for one component and ``(points, 3)`` for three, which is what a renderer
    consumes. ``header`` must be the result of :func:`read_header` of the same buffer. Raises
    :class:`MeshFormatError` for an unknown patch, a field the patch does not have (a patch with no
    array stores no entry, which is how "no field here" is said in this format), or an array that
    does not fit.
    """
    entry = _field(header, patch_index, field)
    base = int(header.get("data_start", 0))
    points = (
        int(header["slice"]["points"])
        if patch_index == SLICE_INDEX
        else int(_patches(header)[patch_index]["points"])
    )
    described = {**entry, "_base": base}
    values = _view(data, described, points)
    components = int(entry["components"])
    return values if components == 1 else values.reshape(points, components)


def slice_arrays(
    data: bytes | memoryview, header: dict[str, Any]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The slice's ``(positions (P, 3) f32, triangles (T, 3) u32, edges (E, 2) u32)``, as views.

    ``T == 0`` means the plane missed the mesh — the agent always writes a slice section, so a
    missing section is a refusal while an empty one is an answer.
    """
    import numpy as np

    entry = header.get("slice")
    if not isinstance(entry, dict):
        raise MeshFormatError("the artifact has no slice")
    points, triangles, edges = (
        int(entry["points"]),
        int(entry["triangles"]),
        int(entry["edges"]),
    )
    start = int(header.get("data_start", 0)) + int(entry["offset"])
    if start + points * 12 + triangles * 12 + edges * 8 > len(data):
        raise MeshFormatError("the slice runs past the end of the artifact")
    positions = np.frombuffer(data, dtype=_F4, count=points * 3, offset=start).reshape(-1, 3)
    triangle_at = start + points * 12
    tris = np.frombuffer(data, dtype=_U4, count=triangles * 3, offset=triangle_at).reshape(-1, 3)
    edges_at = triangle_at + triangles * 12
    segments = np.frombuffer(data, dtype=_U4, count=edges * 2, offset=edges_at).reshape(-1, 2)
    for label, indices in (("triangle", tris), ("edge", segments)):
        if indices.size and int(indices.max()) >= points:
            raise MeshFormatError(f"the slice's {label} names a point that does not exist")
    return positions, tris, segments


def field_payload(values: np.ndarray, field: str, units: str, vmin: float, vmax: float) -> bytes:
    """The client codec for one field: a JSON header, then ``f32[components x count]``.

    ``components`` is the array's own shape rather than a table, because the header a client reads
    has to agree with the bytes beside it. **Contiguity is forced**, so the payload is the array
    rather than the strides of whatever the caller happened to pass.
    """
    import numpy as np

    block = np.ascontiguousarray(values, dtype=_F4)
    components = 1 if block.ndim == 1 else int(block.shape[1])
    count = int(block.shape[0])
    head = json.dumps(
        {
            "version": FIELD_CODEC_VERSION,
            "field": field,
            "units": units,
            "components": components,
            "count": count,
            "min": float(vmin),
            "max": float(vmax),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    head += b" " * ((-len(head)) % 4)
    return struct.pack("<I", len(head)) + head + block.tobytes()


def slice_transport(
    data: bytes | memoryview,
    header: dict[str, Any],
    color: tuple[float, float, float] = DEFAULT_SLICE_COLOR,
) -> bytes:
    """The slice as transport version 2: one ``mesh`` group, every face id 0, units metres.

    Byte for byte the shape :func:`stokes.mesh.surfaces.patch_transport` produces, so the viewport
    decodes a slice with the reader it already has. The slice is one picking unit, like a patch, so
    ``faces`` is 1 and every id is 0.
    """
    import numpy as np

    from stokes.geometry.mesh import TriMesh, vertex_normals
    from stokes.geometry.transport import pack_meshes

    positions, triangles, _edges = slice_arrays(data, header)
    mesh = TriMesh(
        name=SLICE,
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


def slice_edges(data: bytes | memoryview, header: dict[str, Any]) -> bytes:
    """The slice's cell edges in the T-210 edge codec, described in
    :mod:`stokes.mesh.surfaces`.

    The same bytes ``/api/mesh/edges/{id}`` sends, so the "surface with cells" view has one edge
    reader for patches and slices alike, and the codec is not changed or duplicated.
    """
    import numpy as np

    positions, _triangles, edges = slice_arrays(data, header)
    segments = np.ascontiguousarray(positions[edges].reshape(-1, 6), dtype=_F4)
    head = json.dumps(
        {"version": EDGE_CODEC_VERSION, "units": "m", "segments": int(segments.shape[0])},
        separators=(",", ":"),
    ).encode("utf-8")
    head += b" " * ((-len(head)) % 4)
    return struct.pack("<I", len(head)) + head + segments.tobytes()


def field_range(
    data: bytes | memoryview,
    header: dict[str, Any],
    patch_index: int,
    field: str,
) -> tuple[float, float]:
    """``(min, max)`` of one stored array, **for ``U`` over the magnitude**.

    The artifact's own header already carries the range the agent measured, so this recomputes it
    only for a caller that wants the numbers of one patch; :func:`stokes.run.store` reads the
    header's own entries when it answers `run.fields`, because those are what the artifact says.

    Kept here because the rule — magnitude for a vector, the stored array otherwise — is a fact
    about the format, and two places computing it differently is how a legend comes to disagree
    with the colours under it.
    """
    import numpy as np

    values = field_array(data, header, patch_index, field)
    measured = np.linalg.norm(values, axis=1) if values.ndim == 2 else values
    if measured.size == 0:
        return (0.0, 0.0)
    return (float(np.min(measured)), float(np.max(measured)))


def finite(value: Any) -> bool:
    """Whether a header number is one a client can plot. A NaN range would blank a legend."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
