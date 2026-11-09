"""The on-disk edge/vertex topology cache (ADR-0022 §15.2).

A part's topology record is regenerated from its B-rep in the worker and cached beside the B-rep,
keyed by the part's *basis* — the same version name the face ids and the mesh use. It is
deliberately independent of the mesh cache (its own suffix, format version and sidecar), so an old
mesh still reads and the two can be replaced separately.

Layout, beside the shape cache (``<project>/cache/``)::

    <part id>.topology          the packed record (topology_transport.pack_topology)
    <part id>.topology.json     the sidecar: {version, part, basis, bytes, crc32, edges, vertices}

Atoms. The sidecar is written last (the commit), so a half-written record reads as absent and is
regenerated — the discipline :mod:`stokes.geometry.shapes` uses for B-reps. A record whose bytes do
not match the sidecar's length and checksum is refused rather than parsed; an unknown format
version is refused and regenerated, never handed to another decoder.

Import rule: no OCP and no numpy here; the parser is :mod:`stokes.geometry.topology_transport`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stokes.geometry.errors import GeometryFileError, MeshFormatError
from stokes.geometry.topology_brep import TOPOLOGY_VERSION, PartTopology
from stokes.geometry.topology_transport import crc32, pack_topology, unpack_topology
from stokes.model.ids import is_id

SUFFIX = ".topology"
SIDECAR_SUFFIX = ".topology.json"
MAX_TOPOLOGY_BYTES = 256 * 1024 * 1024
MAX_SIDECAR_BYTES = 64 * 1024
_BASIS_RE = re.compile(r"^[0-9a-f]{1,32}$")


@dataclass(frozen=True)
class TopologyEntry:
    """One topology sidecar: what the record beside it is."""

    part: str
    basis: str
    bytes: int
    crc32: int
    edges: int
    vertices: int
    version: int = TOPOLOGY_VERSION
    # The sampling policy the record was made with (A11). §15.2 wants the record bounded, and the
    # bound is a *policy* — a deflection fraction and a point cap — so a policy change must
    # invalidate the cache. Without this the reader accepted a record sampled by the old policy and
    # served it, which silently changes what a hover or a pick can see.
    policy: str = ""

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "version": self.version,
            "part": self.part,
            "basis": self.basis,
            "bytes": self.bytes,
            "crc32": self.crc32,
            "edges": self.edges,
            "vertices": self.vertices,
            "policy": self.policy,
        }


def topology_path(folder: Path, part_id: str) -> Path:
    """``<folder>/<part id>.topology``; the id must be a node id, so the name stays in the
    folder, as :func:`stokes.geometry.shapes.brep_path` does for a B-rep."""
    if not is_id(part_id):
        raise GeometryFileError(f"not a node id: {part_id!r}")
    return folder / f"{part_id}{SUFFIX}"


def sidecar_path(topology: Path) -> Path:
    """``<folder>/<part id>.topology.json``: the commit marker beside the record."""
    return topology.with_name(topology.name + ".json")


def read_entry(topology: Path) -> TopologyEntry | None:
    """The sidecar of ``topology``, or None when it is absent, too large or malformed."""
    path = sidecar_path(topology)
    try:
        if path.stat().st_size > MAX_SIDECAR_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _entry(data)


def _entry(data: Any) -> TopologyEntry | None:  # Any: JSON
    if not isinstance(data, dict) or data.get("version") != TOPOLOGY_VERSION:
        return None
    part, basis = data.get("part"), data.get("basis")
    size, crc = data.get("bytes"), data.get("crc32")
    edges, vertices = data.get("edges"), data.get("vertices")
    counts = (size, crc, edges, vertices)
    if (
        not (isinstance(part, str) and is_id(part))
        or not (isinstance(basis, str) and _BASIS_RE.match(basis))
        or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts)
    ):
        return None
    assert isinstance(size, int) and isinstance(crc, int)
    assert isinstance(edges, int) and isinstance(vertices, int)
    policy = data.get("policy")
    return TopologyEntry(
        part,
        basis,
        size,
        crc,
        edges,
        vertices,
        policy=str(policy) if isinstance(policy, str) else "",
    )


def fresh(topology: Path, basis: str, policy: str | None = None) -> bool:
    """Whether ``topology`` holds a record at version ``basis`` (and ``policy``) by its sidecar."""
    entry = read_entry(topology)
    if entry is None or not basis or entry.basis != basis:
        return False
    return policy is None or entry.policy == policy


def write_topology(topology: Path, value: PartTopology, policy: str = "") -> TopologyEntry:
    """Write ``value`` as the cache entry ``topology``: old sidecar out, record in, sidecar in.

    The record must already carry the basis it was made at; the caller (the worker) sets it, as it
    does for a B-rep. Raises ``OSError`` when the disk refuses, and
    :class:`~stokes.geometry.errors.GeometryFileError` for a record over 256 MB.
    """
    if not isinstance(value.basis, str) or not _BASIS_RE.match(value.basis):
        raise GeometryFileError(f"not a basis: {value.basis!r}")
    if not is_id(value.part):
        raise GeometryFileError(f"not a node id: {value.part!r}")
    blob = pack_topology(value)
    if len(blob) > MAX_TOPOLOGY_BYTES:
        raise GeometryFileError(f"the topology of {value.part} is over 256 MB; not cached")
    entry = TopologyEntry(
        value.part,
        value.basis,
        len(blob),
        crc32(blob),
        len(value.edges),
        len(value.vertices),
        policy=str(policy),
    )
    side = sidecar_path(topology)
    topology.parent.mkdir(parents=True, exist_ok=True)
    side.unlink(missing_ok=True)  # no entry from here until the new sidecar is written
    topology.write_bytes(blob)
    side.write_text(json.dumps(entry.to_json(), separators=(",", ":")), encoding="utf-8")
    return entry


def _consistent(blob: bytes, entry: TopologyEntry, basis: str) -> PartTopology | None:
    """The parsed record when it agrees with its sidecar and ``basis``, else None."""
    if len(blob) != entry.bytes or crc32(blob) != entry.crc32:
        return None
    try:
        value = unpack_topology(blob)
    except MeshFormatError:
        # A record that does not parse is a miss: regenerate rather than fail the request. Narrowed
        # to `MeshFormatError` (A12): a bare `except Exception` also swallowed real bugs — a
        # `TypeError` in the decoder looked identical to a stale cache entry.
        return None
    if value.part != entry.part or value.basis != basis:
        return None
    if len(value.edges) != entry.edges or len(value.vertices) != entry.vertices:
        return None
    return value


def read_topology(topology: Path, basis: str, policy: str | None = None) -> PartTopology | None:
    """The cached topology at ``basis``, or None when it is absent, stale, damaged or off-policy.

    The sidecar gates the read (size and checksum) before the payload is parsed, and the parsed
    record must agree with the sidecar, the asked-for basis **and the sampling policy** (A11). Any
    failure is a cache miss, never an error: the worker regenerates.

    ``policy`` is compared only when the caller names one, so a caller that does not care still
    reads a record written before policies were recorded.
    """
    entry = read_entry(topology)
    if entry is None or not basis or entry.basis != basis:
        return None
    if policy is not None and entry.policy != policy:
        return None
    # A11: the sidecar already states the size, so an oversized or truncated record is rejected
    # BEFORE the whole file is read into memory. `read_bytes()` on a 256 MB record would otherwise
    # allocate it just to hand it to a check that refuses it.
    if entry.bytes > MAX_TOPOLOGY_BYTES:
        return None
    try:
        if topology.stat().st_size != entry.bytes:
            return None  # a different size on disk than the sidecar recorded: a miss
        blob = topology.read_bytes()
    except OSError:
        return None
    return _consistent(blob, entry, basis)


def remove_topology(topology: Path) -> None:
    """Drop a record and its sidecar (a re-import that changed the basis, an explicit purge)."""
    topology.unlink(missing_ok=True)
    sidecar_path(topology).unlink(missing_ok=True)
