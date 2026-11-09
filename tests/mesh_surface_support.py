"""A small ``STKMSH1`` artifact built by the agent's own writer, for the T-210 tests.

Two patches, like a duct with an inlet and a wall: ``inlet`` is a unit quad in the plane x = 0 (four
points, two triangles, four cell edges) and ``wall`` is one triangle. Built with
``stokes_agent.extract.pack_artifact`` so the desktop reader is tested against the real format,
not against a copy of it.

## Three things a reconstruction of this file got wrong, all caught by INDEX

This file was briefly missing from the T-210 bundle while four test modules imported it, so it was
rebuilt from the tests' expectations. Three facts about it are only discoverable by running:

* **Patch 0 is the quad and patch 1 the triangle.** The order is load-bearing:
`patch_transport(…, 0, …)`
  decodes one group with 4 vertices and 2 triangles, and the reversed order fails.
* **A quad's cell edges are its PERIMETER** — ``(0,1) (1,2) (2,3) (0,3)`` — not the fan's diagonal
  ``(3,0)``. `unique_edges` keeps the polygon's real edges and drops the diagonal the triangulation
  introduced, so the two are not the same segment set.
* **`artifact.get` must echo `offset`.** ``artifacts._receive`` *checks* it against the offset
it asked
  for, and omitting it made a perfectly good reply look like a protocol error ("asked for
  offset 0, got
  None") — so the surface fetch failed with a message about the wrong thing.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from stokes_agent.extract import pack_artifact

QUAD_POINTS = np.array(
    [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 1.0, 1.0], [0.0, 0.0, 1.0]], dtype="float32"
)
TRIANGLE_POINTS = np.array([[1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [1.0, 0.0, 1.0]], dtype="float32")


def two_patches() -> list[dict[str, Any]]:
    return [
        {
            "name": "inlet",
            "patch_type": "patch",
            "points": QUAD_POINTS,
            "triangles": np.array([[0, 1, 2], [0, 2, 3]], dtype="uint32"),
            "edges": np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="uint32"),
            "faces": 1,
        },
        {
            "name": "unassigned",
            "patch_type": "patch",
            "points": TRIANGLE_POINTS,
            "triangles": np.array([[0, 1, 2]], dtype="uint32"),
            "edges": np.array([[0, 1], [1, 2], [0, 2]], dtype="uint32"),
            "faces": 1,
        },
    ]


def artifact() -> tuple[bytes, dict[str, Any]]:
    """The artifact bytes and the agent's header for :func:`two_patches`."""
    return pack_artifact(two_patches())


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SurfaceServer:
    """Answers `extract.surfaces` and `artifact.get` for a fake runner from an in-memory artifact.

    ``corrupt`` makes the digest it reports wrong (the sha256 mismatch path); ``fail`` makes
    `extract.surfaces` raise, as a host without VTK does.
    """

    def __init__(self, data: bytes | None = None, *, corrupt: bool = False) -> None:
        built, self.header = artifact()
        self.data = data if data is not None else built
        self.corrupt = corrupt
        self.fail: Exception | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def handle(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """The reply for a method this serves, or None for any other."""
        if method == "extract.surfaces":
            self.calls.append((method, params))
            if self.fail is not None:
                raise self.fail
            return {
                "artifact": "surfaces.stkmsh",
                "size": len(self.data),
                "sha256": self._digest(),
                "patches": self.header["patches"],
                "cached": False,
            }
        if method == "artifact.get":
            self.calls.append((method, params))
            offset, length = int(params["offset"]), int(params["length"])
            return {
                "size": len(self.data),
                "sha256": self._digest(),
                "offset": offset,
                "data": self.data[offset : offset + length],
            }
        return None

    def _digest(self) -> str:
        return digest(b"corrupt" if self.corrupt else self.data)
