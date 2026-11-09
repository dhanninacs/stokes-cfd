"""Binary transport for triangle meshes and part topology: JSON headers, then packed buffers.

Mesh layout (little-endian)::

    u32 header_length
    header JSON (UTF-8, zero-padded to a multiple of 4)
    per group, in header order: positions f32[3N], normals f32[3N], indices u32[3M]
                                (version 2, when the group says "faceIds": true) face ids u32[M]

The header lists ``groups`` with name, kind, colour, counts and the byte ``offset`` of each
group's buffers from the start of the data section. ``/api/geometry/demo`` serves version 1;
``/api/geometry/{node_id}`` serves a part as version 2, whose header adds ``units``, ``bbox``,
``faces`` and per-group ``faceIds``, ``faces``, ``faceBase`` and ``quality``. The full
description and the codec are in :mod:`stokes.geometry.transport`, shared with the mesh cache.

``/api/geometry/topology/{node_id}`` serves a part's B-rep edges and vertices through the separate,
versioned codec of :mod:`stokes.geometry.topology_transport` (ADR-0022 §15.2). It has the same
shape — a length, a JSON header, then one float32 frame — but an independent format and version,
so a mesh decoder is never asked to read it. A mesh-only part has no topology and is a 404.
"""

from __future__ import annotations

from stokes.geometry.topology_transport import (
    MAX_HEADER_BYTES as TOPOLOGY_MAX_HEADER_BYTES,
)
from stokes.geometry.topology_transport import (
    pack_topology,
    unpack_topology,
)
from stokes.geometry.transport import VERSIONS, pack_meshes, pack_part, unpack

MAGIC_VERSION = 1
"""The version :func:`pack_meshes` writes by default (the front end's original reader)."""

__all__ = [
    "MAGIC_VERSION",
    "TOPOLOGY_MAX_HEADER_BYTES",
    "VERSIONS",
    "pack_meshes",
    "pack_part",
    "pack_topology",
    "unpack",
    "unpack_topology",
]
