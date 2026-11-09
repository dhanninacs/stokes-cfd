"""A synthetic sample part as triangle meshes (numpy only): a flanged pipe with inlet and outlet.

Public, generated geometry (CLAUDE.md §1 rule 3). Real CAD comes from STEP import
(:mod:`stokes.geometry.step`, ADR-0014); both travel to the viewport through the binary
transport in :mod:`stokes.geometry.transport`. The sample carries no face ids.
"""

from __future__ import annotations

import numpy as np

from stokes.geometry.mesh import F32, U32, TriMesh

__all__ = ["F32", "U32", "TriMesh", "annulus", "disc", "merge", "sample_part", "tube"]


def _ring(x: float, radius: float, n: int) -> F32:
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False, dtype=np.float64)
    return np.column_stack([np.full(n, x), radius * np.cos(theta), radius * np.sin(theta)]).astype(
        np.float32
    )


def _strip(n: int, a0: int, b0: int) -> U32:
    """Triangles joining ring ``a`` (starting at index a0) and ring ``b`` (b0)."""
    i = np.arange(n, dtype=np.uint32)
    j = (i + 1) % n
    tri1 = np.column_stack([a0 + i, b0 + i, b0 + j])
    tri2 = np.column_stack([a0 + i, b0 + j, a0 + j])
    return np.vstack([tri1, tri2]).astype(np.uint32)


def tube(
    name: str,
    kind: str,
    color: tuple[float, float, float],
    radius: float,
    x0: float,
    x1: float,
    n: int = 96,
) -> TriMesh:
    """Open cylinder along x between ``x0`` and ``x1``, normals pointing outward."""
    a = _ring(x0, radius, n)
    b = _ring(x1, radius, n)
    positions = np.vstack([a, b])
    normals = positions.copy()
    normals[:, 0] = 0.0
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    return TriMesh(name, kind, color, positions, normals.astype(np.float32), _strip(n, 0, n))


def annulus(
    name: str,
    kind: str,
    color: tuple[float, float, float],
    x: float,
    r_inner: float,
    r_outer: float,
    facing: float,
    n: int = 96,
) -> TriMesh:
    """Flat ring at ``x`` facing +x (``facing=1``) or -x (``facing=-1``)."""
    inner = _ring(x, r_inner, n)
    outer = _ring(x, r_outer, n)
    positions = np.vstack([inner, outer])
    normals = np.zeros_like(positions)
    normals[:, 0] = facing
    indices = _strip(n, 0, n)
    if facing < 0:
        indices = indices[:, ::-1].copy()
    return TriMesh(name, kind, color, positions, normals, np.ascontiguousarray(indices))


def disc(
    name: str,
    kind: str,
    color: tuple[float, float, float],
    x: float,
    radius: float,
    facing: float,
    n: int = 96,
) -> TriMesh:
    ring = _ring(x, radius, n)
    centre = np.array([[x, 0.0, 0.0]], dtype=np.float32)
    positions = np.vstack([ring, centre])
    normals = np.zeros_like(positions)
    normals[:, 0] = facing
    i = np.arange(n, dtype=np.uint32)
    j = (i + 1) % n
    indices = np.column_stack([np.full(n, n, dtype=np.uint32), i, j]).astype(np.uint32)
    if facing < 0:
        indices = indices[:, ::-1].copy()
    return TriMesh(name, kind, color, positions, normals, np.ascontiguousarray(indices))


def merge(name: str, kind: str, color: tuple[float, float, float], parts: list[TriMesh]) -> TriMesh:
    positions = np.vstack([p.positions for p in parts])
    normals = np.vstack([p.normals for p in parts])
    offset = 0
    indices = []
    for p in parts:
        indices.append(p.indices + np.uint32(offset))
        offset += p.vertex_count
    return TriMesh(name, kind, color, positions, normals, np.vstack(indices).astype(np.uint32))


def sample_part() -> list[TriMesh]:
    """Walls, two flanges, inlet and outlet patches of a 300 mm pipe (metres)."""
    wall = (0.80, 0.82, 0.86)
    flange = (0.62, 0.65, 0.70)
    inlet = (0.93, 0.33, 0.28)
    outlet = (0.28, 0.56, 0.96)
    r, r_f, half, t = 0.03, 0.05, 0.15, 0.014
    parts: list[TriMesh] = [tube("walls", "wall", wall, r, -half, half)]
    for name, x0 in (("inlet_flange", -half - t), ("outlet_flange", half)):
        x1 = x0 + t
        parts.append(
            merge(
                name,
                "flange",
                flange,
                [
                    tube(name, "flange", flange, r_f, x0, x1),
                    annulus(name, "flange", flange, x0, r, r_f, -1.0),
                    annulus(name, "flange", flange, x1, r, r_f, +1.0),
                ],
            )
        )
    parts.append(disc("inlet", "inlet", inlet, -half - t, r, -1.0))
    parts.append(disc("outlet", "outlet", outlet, half + t, r, +1.0))
    return parts
