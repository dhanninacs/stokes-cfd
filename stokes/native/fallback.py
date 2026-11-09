"""Reference implementations of the native kernels (numpy, single-threaded).

Each function here has the same signature as its C++ counterpart in
``native/src``. Tests run both and compare results; the fallback keeps Stokes
working where the extension is not built.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from numpy.typing import NDArray

Float = NDArray[np.float64]
Int = NDArray[np.int64]


def triangle_normals(points: Float, triangles: Int) -> Float:
    """Unit normals of each triangle (N, 3)."""
    p0 = points[triangles[:, 0]]
    p1 = points[triangles[:, 1]]
    p2 = points[triangles[:, 2]]
    n = np.cross(p1 - p0, p2 - p0)
    lengths = np.linalg.norm(n, axis=1)
    lengths[lengths == 0.0] = 1.0
    return n / lengths[:, None]


def triangle_adjacency(triangles: Int) -> tuple[Int, Int]:
    """Edge adjacency as (CSR offsets, neighbour indices), two triangles per shared edge."""
    count = triangles.shape[0]
    edges: dict[tuple[int, int], list[int]] = {}
    for index, (a, b, c) in enumerate(triangles.tolist()):
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            edges.setdefault(key, []).append(index)
    neighbours: list[list[int]] = [[] for _ in range(count)]
    for members in edges.values():
        for t in members:
            for other in members:
                if other != t:
                    neighbours[t].append(other)
    offsets = np.zeros(count + 1, dtype=np.int64)
    for i, n in enumerate(neighbours):
        offsets[i + 1] = offsets[i] + len(n)
    flat = np.fromiter((x for n in neighbours for x in n), dtype=np.int64, count=int(offsets[-1]))
    return offsets, flat


def grow_by_angle(
    normals: Float, offsets: Int, neighbours: Int, seeds: Int, angle_deg: float
) -> Int:
    """Triangles reachable from ``seeds`` across edges with a dihedral angle below ``angle_deg``."""
    cos_limit = float(np.cos(np.radians(angle_deg)))
    visited = np.zeros(normals.shape[0], dtype=bool)
    queue: deque[int] = deque(int(s) for s in seeds)
    for s in queue:
        visited[s] = True
    while queue:
        t = queue.popleft()
        for k in range(int(offsets[t]), int(offsets[t + 1])):
            other = int(neighbours[k])
            if visited[other]:
                continue
            if float(np.dot(normals[t], normals[other])) >= cos_limit:
                visited[other] = True
                queue.append(other)
    return np.flatnonzero(visited).astype(np.int64)


def connected_components(offsets: Int, neighbours: Int) -> Int:
    """Component label per triangle."""
    count = offsets.shape[0] - 1
    labels = np.full(count, -1, dtype=np.int64)
    current = 0
    for start in range(count):
        if labels[start] >= 0:
            continue
        labels[start] = current
        stack = [start]
        while stack:
            t = stack.pop()
            for k in range(int(offsets[t]), int(offsets[t + 1])):
                other = int(neighbours[k])
                if labels[other] < 0:
                    labels[other] = current
                    stack.append(other)
        current += 1
    return labels
