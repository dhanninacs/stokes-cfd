"""Edge and vertex fingerprints and re-matching (ADR-0022 §15.1).

:mod:`stokes.geometry.fingerprints` re-matches *faces* across a re-import; this is its counterpart
for edges and vertices, and it follows the same discipline deliberately:

* A match is one-to-one inside one part.
* A candidate is scored by a normalised distance that mixes the figures that survive a rebuild.
* The confidence falls off with distance and, crucially, drops when a second candidate is nearly
  as close (:data:`DEFAULT_CONFIDENCE`): an ambiguous match is reported *unmatched* rather than
  guessed. Two identical bolt holes have identical edges, and picking the wrong one silently would
  corrupt a tag.

**Edge figures** (all SI): true B-rep length, curve centre of mass, both endpoints, the unit tangent
at mid-parameter and the OCCT curve type. The length is the *B-rep* length, never the polyline
length — the two differ by the sampling deflection and 15.1 calls that out explicitly.

**Vertex figures**: position, degree and the lengths of the incident edges, in a canonical order so
two runs over one shape compare equal.

Import rule: numpy at module level; scipy inside :func:`match_edges` / :func:`match_vertices`; no
OCP (the fingerprints are plain arrays the worker filled in).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.geometry.topology_brep import PartTopology, TopoEdge, TopoVertex

F64 = NDArray[np.float64]
I64 = NDArray[np.int64]

CANDIDATES = 8
"""Nearest candidates considered per old entity, as :data:`fingerprints.CANDIDATES` does."""
DEFAULT_CONFIDENCE = 0.5
"""Below this a match is reported as unmatched; see the module docstring."""
TOLERANCES = {"centre": 0.03, "length": 0.1, "turn": 0.2}
"""Centre shift as a fraction of the part's size, relative length change, and half the tangent
difference, each counting as distance 1."""
_TINY = 1e-30
# Curve type is a discrete hint, not a distance: a different type costs this much, which is enough
# to lose to a same-type candidate but not enough to forbid a match when it is the only one.
CURVE_MISMATCH = 2.0
_MAX_INCIDENT = 8
"""Incident edges compared per vertex; a higher-degree vertex is compared on its shortest edges."""


def _scale_of(edges: Sequence[TopoEdge], vertices: Sequence[TopoVertex]) -> float:
    """A size for the part from its own figures: the extent of the edge endpoints and vertices.

    The part's bounding-box diagonal would be better, but it is not in the record, and the point of
    a fingerprint is to be comparable to itself after a rebuild — any stable size measure does that.
    """
    points: list[tuple[float, float, float]] = []
    for edge in edges:
        points.append(edge.start)
        points.append(edge.end)
    points.extend(v.position for v in vertices)
    if not points:
        return 1.0
    array = np.asarray(points, dtype=np.float64)
    span = float(np.linalg.norm(array.max(axis=0) - array.min(axis=0)))
    return span if span > _TINY else 1.0


@dataclass(frozen=True)
class EdgeFingerprints:
    """One part's edges, indexed by edge id."""

    length: F64
    centre: F64
    start: F64
    end: F64
    tangent: F64
    curve: tuple[str, ...]
    scale: float

    @property
    def count(self) -> int:
        return int(self.length.shape[0])

    @classmethod
    def of(cls, topology: PartTopology) -> EdgeFingerprints:
        edges = topology.edges
        scale = _scale_of(edges, topology.vertices)
        if not edges:
            return cls(
                np.zeros(0),
                np.zeros((0, 3)),
                np.zeros((0, 3)),
                np.zeros((0, 3)),
                np.zeros((0, 3)),
                (),
                scale,
            )
        return cls(
            np.asarray([e.length for e in edges], dtype=np.float64),
            np.asarray([e.centre for e in edges], dtype=np.float64),
            np.asarray([e.start for e in edges], dtype=np.float64),
            np.asarray([e.end for e in edges], dtype=np.float64),
            np.asarray([e.tangent for e in edges], dtype=np.float64),
            tuple(e.curve for e in edges),
            scale,
        )


@dataclass(frozen=True)
class VertexFingerprints:
    """One part's vertices, indexed by vertex id."""

    position: F64
    degree: I64
    incident: F64
    """(V, _MAX_INCIDENT) the shortest incident edge lengths, zero-padded."""
    scale: float

    @property
    def count(self) -> int:
        return int(self.position.shape[0])

    @classmethod
    def of(cls, topology: PartTopology) -> VertexFingerprints:
        vertices = topology.vertices
        scale = _scale_of(topology.edges, vertices)
        if not vertices:
            return cls(
                np.zeros((0, 3)), np.zeros(0, dtype=np.int64), np.zeros((0, _MAX_INCIDENT)), scale
            )
        incident = np.zeros((len(vertices), _MAX_INCIDENT), dtype=np.float64)
        for index, vertex in enumerate(vertices):
            shortest = sorted(vertex.edge_lengths)[:_MAX_INCIDENT]
            incident[index, : len(shortest)] = shortest
        return cls(
            np.asarray([v.position for v in vertices], dtype=np.float64),
            np.asarray([v.degree for v in vertices], dtype=np.int64),
            incident,
            scale,
        )


@dataclass(frozen=True)
class EntityMatch:
    """Where an old edge or vertex went: ``new`` is None when it has no confident counterpart."""

    old: int
    new: int | None
    confidence: float

    def to_json(self) -> dict[str, object]:
        return {"from": self.old, "to": self.new, "confidence": round(self.confidence, 4)}


def _distance_edges(old: EdgeFingerprints, new: EdgeFingerprints, rows: I64, near: I64) -> F64:
    scale = max(old.scale, new.scale, _TINY)
    shift = np.linalg.norm(new.centre[near] - old.centre[rows][:, None, :], axis=2) / scale
    length = np.abs(new.length[near] - old.length[rows][:, None]) / np.maximum(
        np.maximum(old.length[rows][:, None], new.length[near]), _TINY
    )
    turn = np.linalg.norm(new.tangent[near] - old.tangent[rows][:, None, :], axis=2) / 2.0
    curve = np.asarray(
        [
            [CURVE_MISMATCH if new.curve[c] != old.curve[r] else 0.0 for c in row]
            for r, row in zip(rows, near, strict=True)
        ]
    )
    return np.asarray(
        np.sqrt(
            (shift / TOLERANCES["centre"]) ** 2
            + (length / TOLERANCES["length"]) ** 2
            + (turn / TOLERANCES["turn"]) ** 2
            + curve**2
        ),
        dtype=np.float64,
    )


def _nearest(tree: Any, points: F64, count: int) -> I64:  # Any: scipy cKDTree (no stubs)
    """The ``CANDIDATES`` nearest new entities of every old one, as a (old, k) index array."""
    k = min(CANDIDATES, max(count, 1))
    _, near = tree.query(points, k=k)
    array = np.asarray(near, dtype=np.int64)
    return array.reshape(-1, 1) if array.ndim == 1 else array


def match_edges(
    old: EdgeFingerprints,
    new: EdgeFingerprints,
    wanted: Iterable[int],
    min_confidence: float = DEFAULT_CONFIDENCE,
) -> list[EntityMatch]:
    """Map the ``wanted`` edges of ``old`` onto ``new``; ambiguous ones stay unmatched."""
    asked = [int(e) for e in wanted]
    chosen, confidence = _match(
        old.centre / max(old.scale, _TINY),
        new.centre / max(new.scale, _TINY),
        lambda rows, near: _distance_edges(old, new, rows, near),
        old.count,
        new.count,
    )
    return _reported(asked, chosen, confidence, old.count, min_confidence)


def match_vertices(
    old: VertexFingerprints,
    new: VertexFingerprints,
    wanted: Iterable[int],
    min_confidence: float = DEFAULT_CONFIDENCE,
) -> list[EntityMatch]:
    """Map the ``wanted`` vertices of ``old`` onto ``new``; ambiguous ones stay unmatched."""
    asked = [int(v) for v in wanted]
    chosen, confidence = _match(
        old.position / max(old.scale, _TINY),
        new.position / max(new.scale, _TINY),
        lambda rows, near: _distance_vertices(old, new, rows, near),
        old.count,
        new.count,
    )
    return _reported(asked, chosen, confidence, old.count, min_confidence)


def _distance_vertices(
    old: VertexFingerprints, new: VertexFingerprints, rows: I64, near: I64
) -> F64:
    scale = max(old.scale, new.scale, _TINY)
    shift = np.linalg.norm(new.position[near] - old.position[rows][:, None, :], axis=2) / scale
    # Incident lengths are in metres and compared as a fraction of the part size, so a small part
    # and a large one score the same shape equally.
    a, b = old.incident[rows][:, None, :], new.incident[near]
    spread = np.abs(a - b).sum(axis=2) / scale
    degree = np.abs(old.degree[rows][:, None] - new.degree[near]).astype(np.float64)
    return np.asarray(
        np.sqrt((shift / TOLERANCES["centre"]) ** 2 + spread**2 + degree**2), dtype=np.float64
    )


def _match(
    old_points: F64,
    new_points: F64,
    distance_of: Callable[[I64, I64], F64],
    old_count: int,
    new_count: int,
) -> tuple[I64, F64]:
    """One-to-one greedy assignment with the ambiguity-aware confidence."""
    chosen = np.full(old_count, -1, dtype=np.int64)
    confidence = np.zeros(old_count)
    if old_count == 0 or new_count == 0:
        return chosen, confidence
    tree = import_module("scipy.spatial").cKDTree(new_points)
    near = _nearest(tree, old_points, new_count)
    distance = distance_of(np.arange(old_count, dtype=np.int64), near)
    order = np.argsort(distance, axis=1)
    near = np.take_along_axis(near, order, axis=1)
    distance = np.take_along_axis(distance, order, axis=1)

    taken = np.zeros(new_count, dtype=bool)
    slot = np.full(old_count, -1, dtype=np.int64)
    for row in np.argsort(distance[:, 0], kind="stable").tolist():
        for column in range(distance.shape[1]):
            candidate = int(near[row, column])
            if not taken[candidate]:
                taken[candidate] = True
                slot[row] = column
                break
    rows = np.flatnonzero(slot >= 0)
    best = distance[rows, slot[rows]]
    others = distance[rows].copy()
    others[np.arange(rows.size), slot[rows]] = np.inf
    alternative = others.min(axis=1) if distance.shape[1] > 1 else np.full(rows.size, np.inf)
    with np.errstate(divide="ignore", invalid="ignore"):
        margin = np.where(np.isfinite(alternative), 1.0 - best / alternative, 1.0)
    score = np.exp(-0.5 * best**2) * np.clip(np.nan_to_num(margin, nan=0.0), 0.0, 1.0)
    chosen[rows] = near[rows, slot[rows]]
    confidence[rows] = score
    return chosen, confidence


def _reported(
    asked: Sequence[int],
    chosen: I64,
    confidence: F64,
    count: int,
    min_confidence: float,
) -> list[EntityMatch]:
    """The asked-for entities as matches: a confident counterpart, else unmatched with its score."""
    result: list[EntityMatch] = []
    for entity in asked:
        inside = 0 <= entity < count
        score = float(confidence[entity]) if inside else 0.0
        target = int(chosen[entity]) if inside else -1
        if inside and target >= 0 and score >= min_confidence:
            result.append(EntityMatch(entity, target, score))
        else:
            result.append(EntityMatch(entity, None, score))
    return result
