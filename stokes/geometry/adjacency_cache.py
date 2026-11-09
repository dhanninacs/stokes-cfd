"""The face-adjacency cache: ``<name>.stokes/cache/geometry/adjacency.bin`` (ADR-0020).

``tag.grow`` floods the face adjacency of a part's tessellation. Building it needs the weld
and the edge sort (about 2 s over 4,500 parts), so the import worker builds it while the meshes are
warm and :mod:`stokes.geometry.store` writes **one archive per project**, once when the import
finishes. :mod:`stokes.geometry.selection_cache` reads the archive once and uses an entry when its
mesh counts match the mesh in hand, building adjacency otherwise.

One file, like ``fingerprints.bin`` (per-part files cost more to write and read than they save for
assemblies of many small parts). Layout: the magic ``STKADJ1``, a ``uint32`` header length, a JSON
header ``{version, pairs, entries: [{part, faces, triangles, offset, count}]}``, then the ``int64``
face-id pairs (``pairs`` x 2) and the ``float64`` cosines (``pairs``), each entry a slice. The
project folder is untrusted, so the length, the ids and the slices are checked before use, and the
face and triangle counts identify the mesh an entry was built from.
"""

from __future__ import annotations

import json
import os
import struct
import tempfile
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from stokes.geometry.errors import MeshFormatError
from stokes.geometry.topology import Adjacency
from stokes.log import fields, get_logger
from stokes.model.ids import is_id

log = get_logger(__name__)

ARCHIVE = ("cache", "geometry", "adjacency.bin")
MAGIC = b"STKADJ01"
VERSION = 1
MAX_ARCHIVE_BYTES = 1024**3
MAX_HEADER_BYTES = 64 * 1024 * 1024
MAX_PAIRS = 200_000_000
_PAIR_BYTES = 16
_COS_BYTES = 8
_HEADER_FIELDS = ("part", "faces", "triangles", "offset", "count")

Entry = tuple[Adjacency, int, int]
"""A part's adjacency together with the face and triangle counts of the mesh it was built from."""


def archive_file(folder: Path) -> Path:
    return folder.joinpath(*ARCHIVE)


def write_archive(folder: Path, entries: Mapping[str, Entry]) -> Path:
    """Write every entry to the project's one archive, atomically (temporary file, then replace)."""
    head: list[dict[str, object]] = []
    pairs_list: list[np.ndarray] = []
    cos_list: list[np.ndarray] = []
    offset = 0
    for part, (adjacency, faces, triangles) in sorted(entries.items()):
        pairs = np.ascontiguousarray(adjacency.pairs, dtype="<i8").reshape(-1, 2)
        cos = np.ascontiguousarray(adjacency.cos, dtype="<f8")
        count = int(pairs.shape[0])
        head.append(
            {
                "part": part,
                "faces": int(faces),
                "triangles": int(triangles),
                "offset": offset,
                "count": count,
            }
        )
        pairs_list.append(pairs)
        cos_list.append(cos)
        offset += count
    header = json.dumps(
        {"version": VERSION, "pairs": offset, "entries": head}, separators=(",", ":")
    ).encode("utf-8")
    header += b" " * (-len(header) % 8)
    body_pairs = (
        np.concatenate(pairs_list).astype("<i8") if pairs_list else np.zeros((0, 2), dtype="<i8")
    )
    body_cos = np.concatenate(cos_list).astype("<f8") if cos_list else np.zeros(0, dtype="<f8")

    target = archive_file(folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    # `mkstemp` gives each writer its own name, so two writers in one process cannot share a
    # temporary file and fail with PermissionError; the replace below is still atomic.
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(handle)
    temporary = Path(name)
    try:
        with temporary.open("wb") as out:
            out.write(MAGIC + struct.pack("<I", len(header)) + header)
            out.write(body_pairs.tobytes())
            out.write(body_cos.tobytes())
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return target


def read_archive(folder: Path) -> dict[str, Entry]:
    """Every entry the project's archive holds; empty when it is absent or malformed (logged)."""
    path = archive_file(folder)
    try:
        size = path.stat().st_size
        if size > MAX_ARCHIVE_BYTES:
            raise MeshFormatError(f"{size} bytes")
        return _parse(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, MeshFormatError, ValueError, TypeError) as exc:
        log.warning("adjacency archive unreadable", extra=fields(path=str(path), error=str(exc)))
        return {}


def _parse(raw: bytes) -> dict[str, Entry]:
    if len(raw) < 12 or raw[:8] != MAGIC:
        raise MeshFormatError("not an adjacency archive")
    (length,) = struct.unpack_from("<I", raw, 8)
    if length > MAX_HEADER_BYTES or 12 + length > len(raw):
        raise MeshFormatError("bad header length")
    header = json.loads(raw[12 : 12 + length])
    if not isinstance(header, dict) or header.get("version") != VERSION:
        raise MeshFormatError("unknown layout")
    total, entries = header.get("pairs"), header.get("entries")
    if not isinstance(total, int) or not 0 <= total <= MAX_PAIRS or not isinstance(entries, list):
        raise MeshFormatError("bad pair count or entries")
    body = raw[12 + length :]
    if len(body) != total * (_PAIR_BYTES + _COS_BYTES):
        raise MeshFormatError("the arrays do not match the pair count")
    pairs = np.frombuffer(body, dtype="<i8", count=total * 2).reshape(total, 2).astype(np.int64)
    cos = np.frombuffer(body, dtype="<f8", count=total, offset=total * _PAIR_BYTES).astype(
        np.float64
    )
    if total and int(pairs.min()) < 0:
        raise MeshFormatError("a negative face id")
    # NaN is a valid cosine (every shared edge of the pair borders a triangle without area); only
    # a value outside [-1, 1] is corrupt.
    finite = cos[~np.isnan(cos)]
    if finite.size and bool(np.abs(finite).max() > 1.0 + 1e-6):
        raise MeshFormatError("a cosine out of range")
    found: dict[str, Entry] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise MeshFormatError("a malformed entry")
        part, faces, triangles, offset, count = (entry.get(k) for k in _HEADER_FIELDS)
        if not (
            isinstance(part, str)
            and is_id(part)
            and isinstance(faces, int)
            and isinstance(triangles, int)
            and faces >= 0
            and triangles >= 0
            and isinstance(offset, int)
            and isinstance(count, int)
            and 0 <= offset <= offset + count <= total
        ):
            raise MeshFormatError("a malformed entry")
        # A face id the mesh does not have (it reached `faces`) makes a grow raise or select
        # another part's faces; treat the entry as absent and build adjacency instead.
        if count and int(pairs[offset : offset + count].max()) >= faces:
            continue
        found[part] = (
            Adjacency(pairs[offset : offset + count].copy(), cos[offset : offset + count].copy()),
            faces,
            triangles,
        )
    return found
