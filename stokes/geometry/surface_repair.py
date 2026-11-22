"""Find and smooth out the places where a staged surface crosses or folds onto itself (ADR-0043).

## Why a mesher that keeps the surface needs this

A boundary-conforming tetrahedral mesher (Gmsh) uses the staged triangles as the wall of the mesh,
so the wall must not cross itself. snappyHexMesh and cfMesh only sample the surface and never
notice. **Measured on the gyroid plate exchanger's hot stream (2026-11-20)**: the implicit body's
display tessellation (241,930 triangles) had 440 pairs of triangles crossing each other, nearly
parallel (11 degrees between their normals) and about 0.05 mm apart, plus folds where two
neighbours lie back to back; Gmsh stopped with "a segment and a facet intersect" or "overlapping
facets" on each.

## What the repair does

Finds every crossing - an edge of one triangle through another triangle that shares at most one
vertex with it - and every fold, two triangles sharing an edge whose normals point nearly opposite
ways. It then moves the vertices of those triangles and of their neighbours halfway towards the
mean of their neighbours (one Laplacian step), checks the neighbourhood again, and repeats. Vertices
on a tag boundary or an open edge never move, so the patches keep their outlines. On the exchanger
two rounds cleared every crossing (1,708 triangles; 8,470 vertices moved, the largest by 1.06 mm
on a coarse flat face where the shortest edge is long) in 145 s.

Pure: STL text in, STL text and a report out. numpy only, imported inside the functions: scipy is
reserved for the exploration numerics (`tests/test_explore_imports.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: The most smoothing rounds before giving up and leaving the surface as it was found.
MAX_ROUNDS = 12

#: Two neighbours whose normals are more opposite than this lie back to back: a dihedral under
#: about 0.6 degrees, where Gmsh's own test stops at 0.1 (`Mesh.AngleToleranceFacetOverlap`).
#: **Not looser**: the exchanger's surface has 8,152 knife folds near 10 degrees that are valid
#: (they cross nothing), and smoothing them all moved the surface without clearing it.
FOLD_COS = -0.99995

#: Parametric tolerance of the edge-through-triangle test.
EPS = 1e-7

_VERTEX = re.compile(r"^\s*vertex\s+(\S+)\s+(\S+)\s+(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Repair:
    """What the repair found and did."""

    #: Triangles that crossed or folded before the repair, and after it.
    before: int
    after: int
    #: Vertices moved, and the largest move in metres.
    moved: int
    largest_move: float
    rounds: int
    #: Closed pieces dropped because the fluid cannot reach them, and their triangles.
    pockets: int = 0
    pocket_triangles: int = 0

    def to_json(self) -> dict[str, object]:
        """For the result's record."""
        return {
            "before": self.before,
            "after": self.after,
            "moved": self.moved,
            "largest_move": self.largest_move,
            "rounds": self.rounds,
            "pockets": self.pockets,
            "pocket_triangles": self.pocket_triangles,
        }


def repair(texts: dict[str, str]) -> tuple[dict[str, str], Repair]:
    """Each named ASCII STL with its crossings and folds smoothed out, and the report.

    ``texts`` maps a tag to its STL text; the surfaces are treated as one, joined at shared
    vertices, so a crossing between two tags is found too. **A closed piece apart from the body
    whose normals face out of it is a pocket of fluid nothing reaches** (the exchanger's lattice
    left one, 2.6 mm across, after the cut): it is dropped, as snappyHexMesh keeps only the region
    around its inside point. A closed piece facing inwards is a solid island in the fluid and is
    kept. A surface with nothing to repair comes back as the same text.
    """
    import numpy as np

    names = list(texts)
    parts = [_triangles(texts[name]) for name in names]
    owner = np.concatenate([np.full(len(p), k) for k, p in enumerate(parts)]) if parts else None
    if owner is None or len(owner) == 0:
        return dict(texts), Repair(0, 0, 0, 0.0, 0)
    corners = np.concatenate(parts).reshape(-1, 3)
    points, inverse = np.unique(corners.round(12), axis=0, return_inverse=True)
    triangles = inverse.reshape(-1, 3)
    keep = _reachable(points, triangles)
    pockets = len(set(_components(triangles, len(points))[~keep].tolist()))
    dropped = int((~keep).sum())
    if dropped:
        triangles, owner = triangles[keep], owner[keep]
    edges, fixed = _edges_and_fixed(triangles, owner, len(points))
    bad = crossing_triangles(points, triangles)
    before = len(bad)
    if before == 0:
        if not dropped:
            return dict(texts), Repair(0, 0, 0, 0.0, 0)
        out = {name: _stl(name, points[triangles[owner == k]]) for k, name in enumerate(names)}
        return out, Repair(0, 0, 0, 0.0, 0, pockets, dropped)
    neighbours = _neighbours(edges, len(points))
    start = points.copy()
    rounds = 0
    while len(bad) and rounds < MAX_ROUNDS:
        rounds += 1
        touched = np.unique(triangles[bad])
        ring = np.unique(np.concatenate([touched, *(neighbours[i] for i in touched)]))
        free = ring[~fixed[ring]]
        if len(free) == 0:
            break
        means = np.array([points[neighbours[i]].mean(axis=0) for i in free])
        # **Each move is capped at half the vertex's shortest edge**: beside a long flat triangle
        # the mean of the neighbours is far away, and the uncapped step moved a vertex 1.2 mm.
        shortest = np.array(
            [np.linalg.norm(points[neighbours[i]] - points[i], axis=1).min() for i in free]
        )
        step = 0.5 * (means - points[free])
        length = np.linalg.norm(step, axis=1)
        scale = np.minimum(1.0, 0.5 * shortest / np.where(length > 0, length, 1.0))
        points[free] += step * scale[:, None]
        near = np.where(np.isin(triangles, ring).any(axis=1))[0]
        bad = crossing_triangles(points, triangles, near)
    after = len(crossing_triangles(points, triangles)) if rounds else before
    if after:
        # **Not repaired is not half repaired**: the surface goes to the mesher as it was staged,
        # and its error names the place.
        return dict(texts), Repair(before, after, 0, 0.0, rounds, pockets, dropped)
    moves = np.linalg.norm(points - start, axis=1)
    out = {name: _stl(name, points[triangles[owner == k]]) for k, name in enumerate(names)}
    moved = int((moves > 0).sum())
    return out, Repair(before, 0, moved, float(moves.max()), rounds, pockets, dropped)


#: Two neighbours whose normals are further apart than this make a knife fold: an inside angle
#: under about 25 degrees between them.
KNIFE_COS = -0.9


def knife_folds(texts: dict[str, str]) -> int:
    """How many edges of the joined surfaces fold back sharper than about 25 degrees.

    **Measured on the exchanger's lattice: 8,152**, from the implicit body's tessellation. Layers
    grown along the normals there cross at once (Gmsh: "overlapping facets").
    """
    import numpy as np

    parts = [_triangles(text) for text in texts.values()]
    parts = [p for p in parts if len(p)]
    if not parts:
        return 0
    corners = np.concatenate(parts).reshape(-1, 3)
    points, inverse = np.unique(corners.round(12), axis=0, return_inverse=True)
    triangles = inverse.reshape(-1, 3)
    normals = _normals(points, triangles)
    edges = np.sort(
        np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1
    )
    owner = np.tile(np.arange(len(triangles)), 3)
    key = edges[:, 0] * len(points) + edges[:, 1]
    order = np.argsort(key, kind="stable")
    key, owner = key[order], owner[order]
    pair = np.nonzero(key[1:] == key[:-1])[0]
    dots = (normals[owner[pair]] * normals[owner[pair + 1]]).sum(axis=1)
    return int((dots < KNIFE_COS).sum())


def _components(triangles: Any, count: int) -> Any:
    """Each triangle's connected piece (triangles joined by a shared vertex), as a label 0..n-1.

    Union-find over the triangle-vertex graph with hooking and pointer jumping, in numpy: a
    handful of rounds however long the surface (scipy is the exploration numerics' only).
    """
    import numpy as np

    # Nodes: the triangles first, then the vertices; an edge joins a triangle to each corner.
    tri = np.repeat(np.arange(len(triangles)), 3)
    ver = len(triangles) + triangles.ravel()
    parent = np.arange(len(triangles) + count)
    while True:
        a, b = parent[tri], parent[ver]
        changed = a != b
        if not changed.any():
            break
        high, low = np.maximum(a, b)[changed], np.minimum(a, b)[changed]
        np.minimum.at(parent, high, low)
        while True:
            jumped = parent[parent]
            if (jumped == parent).all():
                break
            parent = jumped
    _, labels = np.unique(parent[: len(triangles)], return_inverse=True)
    return labels.ravel()


def _reachable(points: Any, triangles: Any) -> Any:
    """Which triangles to keep: every piece but closed, outward-facing ones apart from the body.

    The body is the piece enclosing the most volume. A closed piece whose signed volume has the
    body's sign (its normals face out of it, as the body's do) is fluid on its own: nothing reaches
    it. One with the other sign is a hole in the body and stays.
    """
    import numpy as np

    labels = _components(triangles, len(points))
    keep = np.ones(len(triangles), dtype=bool)
    if labels.max() == 0:
        return keep
    v = points[triangles]
    signed = np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])) / 6.0
    volume = np.bincount(labels, weights=signed)
    body = int(np.argmax(np.abs(volume)))
    for piece in range(len(volume)):
        same_side = volume[piece] * volume[body] > 0.0
        if piece == body or not same_side or not _closed(triangles[labels == piece]):
            continue
        keep[labels == piece] = False
    return keep


def _closed(triangles: Any) -> bool:
    """Whether every edge of the triangles is used exactly twice."""
    import numpy as np

    edges = np.sort(
        np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1
    )
    _, uses = np.unique(edges, axis=0, return_counts=True)
    return bool((uses == 2).all())


def crossing_triangles(points: Any, triangles: Any, subset: Any = None) -> Any:
    """The triangles that cross another or fold onto a neighbour (indices, sorted, unique).

    ``subset`` limits the search to pairs with at least one triangle in it.
    """
    import numpy as np

    pairs = _candidate_pairs(points[triangles], subset)
    ti, tj = triangles[pairs[:, 0]], triangles[pairs[:, 1]]
    shared = (ti[:, :, None] == tj[:, None, :]).sum(axis=(1, 2))
    crossing = pairs[shared < 2]
    hit = np.zeros(len(crossing), dtype=bool)
    for a, b in ((0, 1), (1, 0)):
        mine, other = triangles[crossing[:, a]], triangles[crossing[:, b]]
        for k in range(3):
            p, q = mine[:, k], mine[:, (k + 1) % 3]
            # An edge that ends on the other triangle's vertex meets it there by construction.
            touches = (other == p[:, None]).any(axis=1) | (other == q[:, None]).any(axis=1)
            hit |= ~touches & _edge_through(
                points[p], points[q], points[other[:, 0]], points[other[:, 1]], points[other[:, 2]]
            )
    folds = pairs[shared == 2]
    normals = _normals(points, triangles)
    folded = (normals[folds[:, 0]] * normals[folds[:, 1]]).sum(axis=1) < FOLD_COS
    found = np.concatenate([crossing[hit].ravel(), folds[folded].ravel()])
    return np.unique(found)


# ------------------------------------------------------------------------------------------ helpers


def _triangles(text: str) -> Any:
    """An ASCII STL's triangles as an (N, 3, 3) array."""
    import numpy as np

    rows = _VERTEX.findall(text)
    if not rows:
        return np.zeros((0, 3, 3), dtype=np.float64)
    return np.asarray(rows, dtype=np.float64)[: len(rows) // 3 * 3].reshape(-1, 3, 3)


def _edges_and_fixed(triangles: Any, owner: Any, count: int) -> tuple[Any, Any]:
    """The unique edges, and the vertices that may not move: on an open edge or a tag boundary."""
    import numpy as np

    edges = np.sort(
        np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1
    )
    tags = np.tile(owner, 3)
    unique, index, uses = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    index = index.ravel()
    low = np.full(len(unique), np.iinfo(np.int64).max)
    high = np.full(len(unique), -1)
    np.minimum.at(low, index, tags)
    np.maximum.at(high, index, tags)
    fixed = np.zeros(count, dtype=bool)
    fixed[unique[(uses != 2) | (low != high)].ravel()] = True
    return unique, fixed


def _neighbours(edges: Any, count: int) -> list[Any]:
    """Each vertex's neighbours along the edges."""
    import numpy as np

    both = np.concatenate([edges, edges[:, ::-1]])
    order = np.argsort(both[:, 0], kind="stable")
    both = both[order]
    starts = np.searchsorted(both[:, 0], np.arange(count + 1))
    return [both[starts[i] : starts[i + 1], 1] for i in range(count)]


def _normals(points: Any, triangles: Any) -> Any:
    import numpy as np

    v = points[triangles]
    n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    length = np.linalg.norm(n, axis=1)
    return n / np.where(length > 0, length, 1.0)[:, None]


def _candidate_pairs(corners: Any, subset: Any) -> Any:
    """Every pair of triangles whose bounding spheres overlap, each pair once.

    The small triangles' centres are binned on a grid of cells twice the small radius, so two
    small triangles that can touch are in the same or neighbouring cells; the few large ones (the
    flat faces a tessellation leaves coarse) are each compared with every centre, so no pair is
    missed however unequal the sizes. numpy only: scipy is the exploration numerics' alone.
    """
    import numpy as np

    centres = corners.mean(axis=1)
    radii = np.linalg.norm(corners - centres[:, None], axis=2).max(axis=1)
    small_r = float(np.percentile(radii, 99.0))
    large = np.where(radii > small_r)[0]
    is_small = radii <= small_r
    # **Only the neighbourhood being repaired** when a subset is given: each round re-checks the
    # triangles it moved, which is a few thousand, not the whole surface.
    mine = np.arange(len(centres)) if subset is None else np.asarray(subset, dtype=np.int64)
    pairs = [_grid_pairs(centres, is_small, mine[is_small[mine]], 2.0 * small_r, subset is None)]
    if len(large):
        # A large triangle against everything, and everything searched against the large ones.
        # Sorted by x, each large triangle reads only the slab its reach can touch.
        by_x = np.argsort(centres[:, 0], kind="stable")
        xs = centres[by_x, 0]
        reach = float(radii.max())
        for i in large if subset is None else np.intersect1d(large, mine):
            lo, hi = np.searchsorted(
                xs, [centres[i, 0] - radii[i] - reach, centres[i, 0] + radii[i] + reach]
            )
            slab = by_x[lo:hi]
            near = slab[
                np.linalg.norm(centres[slab] - centres[i], axis=1) <= radii[slab] + radii[i]
            ]
            pairs.append(np.stack([np.full(len(near), i), near], axis=1))
        if subset is not None:
            for lo in range(0, len(mine), 512):
                block = mine[lo : lo + 512]
                gap = np.linalg.norm(centres[block][:, None] - centres[large][None], axis=2)
                a, b = np.nonzero(gap <= radii[block][:, None] + radii[large][None])
                pairs.append(np.stack([block[a], large[b]], axis=1))
    # **A pair may come twice** (a large triangle meets a searched one from both sides); testing it
    # twice costs less than sorting millions of pairs to drop it (measured: the sort was half the
    # run), and the crossing triangles are made unique at the end.
    found = np.concatenate([p.reshape(-1, 2) for p in pairs]).astype(np.int64)
    found = found[found[:, 0] != found[:, 1]]
    gap = np.linalg.norm(centres[found[:, 0]] - centres[found[:, 1]], axis=1)
    return found[gap <= radii[found[:, 0]] + radii[found[:, 1]]]


def _grid_pairs(centres: Any, is_small: Any, query: Any, cell: float, everything: bool) -> Any:
    """Pairs (query triangle, small triangle) whose centres are in the same or neighbouring cells.

    With every triangle as the query, each pair is produced once (half the neighbour offsets, and
    the larger index within a cell); with a subset, every neighbour of every queried triangle.
    Filtered by distance as it goes, so the arrays stay the size of the answer.
    """
    import itertools

    import numpy as np

    small = np.nonzero(is_small)[0]
    if len(small) == 0 or len(query) == 0:
        return np.zeros((0, 2), dtype=np.int64)
    radius = 0.5 * cell
    lowest = centres[small].min(axis=0)
    grid = np.floor((centres - lowest) / cell).astype(np.int64) + 1
    span = grid[small].max(axis=0) + 3

    def key(g: Any) -> Any:
        return (g[:, 0] * span[1] + g[:, 1]) * span[2] + g[:, 2]

    order = np.argsort(key(grid[small]), kind="stable")
    sorted_small = small[order]
    sorted_keys = key(grid[small])[order]
    offsets = [
        o for o in itertools.product((-1, 0, 1), repeat=3) if not everything or o >= (0, 0, 0)
    ]
    found = []
    for offset in offsets:
        target = key(grid[query] + np.asarray(offset))
        lo = np.searchsorted(sorted_keys, target, side="left")
        counts = np.searchsorted(sorted_keys, target, side="right") - lo
        total = int(counts.sum())
        if total == 0:
            continue
        starts = np.repeat(lo - (np.cumsum(counts) - counts), counts)
        other = sorted_small[np.arange(total) + starts]
        mine = np.repeat(query, counts)
        keep = np.linalg.norm(centres[mine] - centres[other], axis=1) <= 2.0 * radius
        if everything and offset == (0, 0, 0):
            keep &= other > mine
        found.append(np.stack([mine[keep], other[keep]], axis=1))
    return np.concatenate(found) if found else np.zeros((0, 2), dtype=np.int64)


def _edge_through(p: Any, q: Any, a: Any, b: Any, c: Any) -> Any:
    """Whether segment pq passes through triangle abc (Moller-Trumbore, vectorised)."""
    import numpy as np

    d = q - p
    e1 = b - a
    e2 = c - a
    h = np.cross(d, e2)
    det = (e1 * h).sum(axis=1)
    ok = np.abs(det) > 1e-30
    f = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = p - a
    u = f * (s * h).sum(axis=1)
    r = np.cross(s, e1)
    w = f * (d * r).sum(axis=1)
    t = f * (e2 * r).sum(axis=1)
    return ok & (u > -EPS) & (w > -EPS) & (u + w < 1 + EPS) & (t > EPS) & (t < 1 - EPS)


def _stl(name: str, corners: Any) -> str:
    """An ASCII STL of the triangles, as the snapshot builder writes one."""
    lines = [f"solid {name}"]
    for triangle in corners:
        lines += ["  facet normal 0 0 0", "    outer loop"]
        lines += [f"      vertex {x:.12g} {y:.12g} {z:.12g}" for x, y, z in triangle]
        lines += ["    endloop", "  endfacet"]
    lines.append(f"endsolid {name}")
    return "\n".join(lines) + "\n"
