"""The mesher's output file, read with the checks of ADR-0035 decision 17.

Layout (little-endian): the magic ``STKIMSH1``, u32 vertex count, u32 triangle count, f32 x, y, z
per vertex, u32 a, b, c per triangle (counter-clockwise seen from outside), u32 label per triangle.
Labels are the face ids of decision 11; until slice 2 the mesher writes 0 for every triangle.

The file is untrusted: the magic, both counts against the file size, the triangle cap, every index
against the vertex count and every position for finiteness are checked before anything is used.
:func:`write` is the inverse, for tests and for the reference mesher.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.implicit.errors import MesherFailed

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

MAGIC = b"STKIMSH1"
HEADER = struct.Struct("<8sII")
MAX_TRIANGLES = 64 * 1024 * 1024
"""Triangles in one file (decision 17)."""
MAX_VERTICES = 3 * MAX_TRIANGLES


@dataclass(frozen=True)
class KernelMesh:
    """A mesh as the mesher wrote it: positions in kernel units and frame."""

    positions: npt.NDArray[np.float32]
    """(n, 3)"""
    triangles: npt.NDArray[np.uint32]
    """(m, 3)"""
    labels: npt.NDArray[np.uint32]
    """(m,)"""


def _bad(path: Path, why: str) -> MesherFailed:
    return MesherFailed(
        f"the mesher's output {path.name} is not valid: {why}", details={"why": why}
    )


def read(path: Path) -> KernelMesh:
    """The mesh in ``path``, checked; :class:`MesherFailed` when any check fails."""
    import numpy as np

    size = path.stat().st_size
    if size < HEADER.size:
        raise _bad(path, "shorter than its header")
    with path.open("rb") as handle:
        magic, vertices, triangles = HEADER.unpack(handle.read(HEADER.size))
        if magic != MAGIC:
            raise _bad(path, "wrong magic")
        if vertices > MAX_VERTICES or triangles > MAX_TRIANGLES:
            raise _bad(path, f"{triangles} triangles is over the limit of {MAX_TRIANGLES}")
        expected = HEADER.size + 12 * vertices + 12 * triangles + 4 * triangles
        if size != expected:
            raise _bad(path, f"{size} bytes where the counts say {expected}")
        positions = np.fromfile(handle, dtype="<f4", count=3 * vertices).reshape(-1, 3)
        indices = np.fromfile(handle, dtype="<u4", count=3 * triangles).reshape(-1, 3)
        labels = np.fromfile(handle, dtype="<u4", count=triangles)
    if not bool(np.isfinite(positions).all()):
        raise _bad(path, "a vertex position is not finite")
    if triangles and int(indices.max()) >= vertices:
        raise _bad(path, "a triangle names a vertex that does not exist")
    return KernelMesh(
        positions.astype(np.float32, copy=False),
        indices.astype(np.uint32, copy=False),
        labels.astype(np.uint32, copy=False),
    )


def write(path: Path, positions: Any, triangles: Any, labels: Any = None) -> None:  # Any: arrays
    """Write a mesh in the mesher's format (tests and the reference mesher)."""
    import numpy as np

    pos = np.ascontiguousarray(positions, dtype="<f4").reshape(-1, 3)
    tri = np.ascontiguousarray(triangles, dtype="<u4").reshape(-1, 3)
    lab = (
        np.zeros(len(tri), dtype="<u4")
        if labels is None
        else np.ascontiguousarray(labels, dtype="<u4").reshape(-1)
    )
    with path.open("wb") as handle:
        handle.write(HEADER.pack(MAGIC, len(pos), len(tri)))
        handle.write(pos.tobytes())
        handle.write(tri.tobytes())
        handle.write(lab.tobytes())
