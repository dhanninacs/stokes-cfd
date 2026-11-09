"""B-rep edge and vertex topology: enumeration, fingerprints and polyline sampling (ADR-0022 §15.1).

Edge and point selection needs identities for a part's B-rep *edges* and *vertices*, not its
triangles. This module is where those identities are made, and it is deliberately separate from
:mod:`stokes.geometry.topology`, which works on the *tessellation* (welded triangle edges and face
adjacency). The two must not be confused:

* :mod:`stokes.geometry.topology` merges mesh vertices by position to find shared triangle edges.
  Those "edges" belong to no B-rep face and have no stable id.
* here, an edge or vertex is an OCCT topological entity, numbered in ``TopExp.MapShapes`` order and
  addressed as ``(part node id, kind, zero-based id, basis)``.

**Numbering (ADR-0022 15.1).** Each part's shape is enumerated once with
``TopExp.MapShapes_s(shape, TopAbs_EDGE / TopAbs_VERTEX, map)``, edges first then vertices; the
first unique occurrence wins. Faces keep their existing ``TopExp_Explorer`` numbering (which counts
repeats) — :mod:`stokes.geometry.operations.faces` says why, and this module must not change it.

**Sampling.** An edge's polyline is sampled with ``GCPnts_QuasiUniformDeflection`` so a curve is
drawn and picked as the line it looks like. A closed edge may repeat its first point at the end. A
degenerate edge with no useful curve is reported but marked unpickable rather than given an
invented shape.

**Fingerprints.** For re-matching across a re-import, an edge records its true B-rep length
(``BRepGProp.LinearProperties``, never the polyline length), curve centre of mass, endpoints, a
tangent at mid-parameter and the curve type; a vertex records its position, its degree and the
lengths of its incident edges. This follows the face matcher's discipline: within one part by
normalised distance and local incidence, one to one, and an ambiguous match stays unmatched.

Import rule: OCP inside the functions, so importing this module costs nothing; numpy at module
level for the arrays it returns.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.geometry.errors import GeometryError, GeometryUnsupported

F32 = NDArray[np.float32]
F64 = NDArray[np.float64]
I64 = NDArray[np.int64]

TOPOLOGY_VERSION = 3
"""The record's wire version.

**2 added each edge's face ids** (T-124a). A version 1 payload therefore does not carry them, and
the
decoder refuses it as a miss rather than reading it as "an edge with no faces" — an edge with no
faces is not a thing the hidden-face rule or the edge -> face conversion can use, and silently
treating the old shape as the new one would make every edge look like a boundary edge.
"""
"""Version of the topology record and its cache. Bumped independently of the mesh transport
(ADR-0022 15.2), so an unknown version is regenerated and never fed to a mesh decoder."""

DEFAULT_DEFLECTION = 0.05
"""Deflection for polyline sampling, as a fraction of the part's bounding-box diagonal. The
display mesh uses much finer tolerances for shading; a picked edge only has to follow the curve, and
a coarser, smaller record keeps the lazy endpoint cheap (ADR-0022 15.2)."""

MAX_POLYLINE_POINTS = 512
"""Cap on the points sampled for one edge, so a pathological curve cannot grow the record
without limit (ADR-0022 15.2)."""

KINDS = ("edge", "vertex")
"""What a pick can name. A solid is measured as a whole rather than picked, so it is not a kind."""


@dataclass(frozen=True)
class TopoEdge:
    """One B-rep edge: its id, its sampled polyline and the figures re-matching compares."""

    edge_id: int
    """Zero-based id in ``TopExp.MapShapes`` order over the part's shape."""
    length: float
    """True B-rep length in metres (never the polyline length)."""
    centre: tuple[float, float, float]
    """Centre of mass of the curve, metres."""
    start: tuple[float, float, float]
    """First parameter point, metres."""
    end: tuple[float, float, float]
    """Last parameter point, metres."""
    tangent: tuple[float, float, float]
    """Unit tangent at mid-parameter; zero when the curve has none."""
    curve: str
    """OCCT curve type name (``GeomAbs_Line`` ...), or ``""`` when unavailable."""
    points: F32
    """(N, 3) the sampled polyline, metres, float32."""
    pickable: bool
    """False for a degenerate edge (no useful curve): reported, never picked (ADR-0022 15.2)."""
    faces: tuple[int, ...] = ()
    """The face ids this edge borders, in the mesh's numbering (T-124a).

    Added for §15.3's hidden-face rule and §15.4's edge -> face conversion. A boundary edge borders
    one face, an interior edge two. Empty for a record from before the codec version bump, which is
    why the reader treats an old payload as a miss rather than as an edge with no faces.
    """


@dataclass(frozen=True)
class TopoFace:
    """One B-rep face's measured size, in the mesh's face numbering.

    **Area is here rather than derived from the mesh on purpose.** Summing the display triangles
    would
    make the figure depend on the tessellation quality, so the same face would report a different
    area
    at a different setting — exactly what ADR-0022 §15.1 forbids for measurement. This is
    ``BRepGProp.SurfaceProperties_s`` on the real surface, converted to square metres, so it is a
    property of the model and not of how finely we drew it.
    """

    face_id: int
    """Zero-based id in mesh order (``TopExp_Explorer``, counting repeats), matching face ids."""
    area: float
    """True B-rep surface area, square metres."""
    centroid: tuple[float, float, float]
    """Area-weighted centre of the surface, metres."""


@dataclass(frozen=True)
class TopoSolid:
    """One solid body's measured size.

    A part may hold several solids (a compound import), so volumes are a list rather than one
    figure: reporting the sum alone would hide that the part is several bodies, and reporting the
    first alone would be wrong for the rest.
    """

    volume: float
    """Enclosed volume, cubic metres."""
    centroid: tuple[float, float, float]
    """Centre of mass, metres."""
    area: float
    """Total surface area of the solid's own faces, square metres."""


@dataclass(frozen=True)
class TopoVertex:
    """One B-rep vertex: its id, its position and its local incidence."""

    vertex_id: int
    position: tuple[float, float, float]
    """Position, metres."""
    degree: int
    """How many edges in this part meet here."""
    edge_ids: tuple[int, ...]
    """The incident edges, sorted."""
    edge_lengths: tuple[float, ...]
    """Their lengths, in the same order: the incidence figure re-matching compares."""


@dataclass
class PartTopology:
    """A part's edge and vertex topology at one basis, ready to pack for the lazy endpoint."""

    part: str
    basis: str
    scale: float
    """Metres per unit of the shape the topology was read from."""
    edges: list[TopoEdge] = field(default_factory=list)
    faces: int = 0
    """How many faces the part has, in the mesh's numbering (T-124a).

    Travels in the header so the decoder can range-check every edge's face ids against it: without a
    count, "face id 47" is unverifiable and a malformed record would resolve to nothing later.
    """
    vertices: list[TopoVertex] = field(default_factory=list)
    face_sizes: list[TopoFace] = field(default_factory=list)
    """Per-face area and centroid, in mesh face order (v3).

    Empty on a record from before the version bump, and the reader refuses a v2 payload rather than
    treating it as a part with no face sizes — see ``topology_transport.unpack_topology``.
    """
    solids: list[TopoSolid] = field(default_factory=list)
    """The part's solid bodies, measured (v3); empty for a shape with no solid (a shell, a wire)."""

    @property
    def storage_units(self) -> str:
        """The unit the *shape* is stored in (``scale`` metres per unit, e.g. ``mm``).

        This is not the unit of the record's figures: :meth:`edge_json`, :meth:`vertex_json` and the
        packed points are all metres, whatever the shape was stored in. The name is deliberately
        distinct from the payload's ``units`` field so the two can never be confused.
        """
        from stokes.geometry.shapes import unit_name

        return unit_name(self.scale)

    def edge_json(self) -> list[dict[str, Any]]:  # Any: JSON
        """The edge header entries, without their points (those travel as one binary frame)."""
        return [
            {
                "id": e.edge_id,
                "length": round(e.length, 9),
                "centre": [round(v, 9) for v in e.centre],
                "start": [round(v, 9) for v in e.start],
                "end": [round(v, 9) for v in e.end],
                "tangent": [round(v, 6) for v in e.tangent],
                "curve": e.curve,
                "points": int(e.points.shape[0]),
                "pickable": e.pickable,
                # T-124a: the faces this edge borders, in the mesh's numbering.
                "faces": list(e.faces),
            }
            for e in self.edges
        ]

    def face_size_json(self) -> list[dict[str, Any]]:  # Any: JSON
        return [
            {
                "id": f.face_id,
                "area": round(f.area, 12),
                "centroid": [round(v, 9) for v in f.centroid],
            }
            for f in self.face_sizes
        ]

    def solid_json(self) -> list[dict[str, Any]]:  # Any: JSON
        return [
            {
                "volume": round(s.volume, 12),
                "centroid": [round(v, 9) for v in s.centroid],
                "area": round(s.area, 12),
            }
            for s in self.solids
        ]

    def vertex_json(self) -> list[dict[str, Any]]:  # Any: JSON
        return [
            {
                "id": v.vertex_id,
                "position": [round(c, 9) for c in v.position],
                "degree": v.degree,
                "edges": list(v.edge_ids),
                "lengths": [round(x, 9) for x in v.edge_lengths],
            }
            for v in self.vertices
        ]


def _downcast_edge(shape: Any) -> Any:  # Any: OCP shape in, TopoDS_Edge out
    """``TopoDS.Edge`` of a map key. OCP 8 binds it as a plain static, like ``TopoDS.Face``."""
    from OCP.TopoDS import TopoDS

    return TopoDS.Edge(shape)


def _downcast_vertex(shape: Any) -> Any:  # Any: OCP shape in, TopoDS_Vertex out
    from OCP.TopoDS import TopoDS

    return TopoDS.Vertex(shape)


def _map_of(shape: Any, kind: Any) -> Any:  # Any: OCP TopoDS_Shape, TopAbs_ShapeEnum -> map
    """``TopExp.MapShapes_s`` over an ``IndexedMap`` of shapes (ADR-0022 15.1).

    The ADR names ``TopTools_IndexedMapOfShape``; OCP 8.0.1 does not bind that class. The
    equivalent is the templated ``IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher`` in
    :mod:`OCP.collections`, which is what ``MapShapes_s`` actually accepts (it refuses the
    non-hasher ``IndexedMap_TopoDS_Shape``). Verified on OCP 8.0.1.
    """
    import OCP.collections as occ
    from OCP.TopExp import TopExp

    found = occ.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, kind, found)
    return found


def _unit(vec: Any) -> tuple[float, float, float]:  # Any: OCP gp_Vec/gp_Dir
    values = (float(vec.X()), float(vec.Y()), float(vec.Z()))
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 0.0:
        return (0.0, 0.0, 0.0)
    return (values[0] / norm, values[1] / norm, values[2] / norm)


def sampling_policy() -> str:
    """A short name for how edges are sampled, for the cache key (A11, ADR-0022 §15.2).

    The record's *shape* depends on the sampling parameters, not only on the part and its basis: a
    different deflection or point cap produces different polylines from the same B-rep. Recording
    the policy in the sidecar means changing either constant invalidates the cache instead of
    serving geometry sampled the old way. Bump the name whenever `DEFAULT_DEFLECTION` or
    `MAX_POLYLINE_POINTS` changes.
    """
    return f"d{DEFAULT_DEFLECTION:g}p{MAX_POLYLINE_POINTS}"


def _deflection(shape: Any, scale: float) -> float:
    """The sampling deflection in the shape's own units: a fraction of its bbox diagonal.

    ADR-0022 15.2 wants a bounded record; a fixed fraction of the part's size keeps a small part and
    a large one sampled in the same proportion. Falls back to a small absolute value for a shape
    with no extent. Uses ``step.bbox_diagonal`` rather than ``Bnd_Box.Get``, which OCP 8.0.1 cannot
    convert to Python (see ``operations.base.check_shape``).
    """
    from stokes.geometry.step import bbox_diagonal

    diagonal = bbox_diagonal(shape)
    if diagonal <= 0.0:
        return max(DEFAULT_DEFLECTION, 1e-9)
    return max(diagonal * DEFAULT_DEFLECTION, 1e-9)


def _sample(edge: Any, deflection: float) -> F32:
    """The edge's polyline (N, 3) in the shape's own units, float32; empty when it cannot sample."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection

    curve = BRepAdaptor_Curve(edge)
    try:
        discretised = GCPnts_QuasiUniformDeflection(curve, deflection)
    except Exception:  # a curve OCCT cannot discretise: reported unpickable, not fatal
        return np.zeros((0, 3), dtype=np.float32)
    if not discretised.IsDone():
        return np.zeros((0, 3), dtype=np.float32)
    count = int(discretised.NbPoints())
    if count <= 0:
        return np.zeros((0, 3), dtype=np.float32)
    if count > MAX_POLYLINE_POINTS:  # resample coarser rather than truncate the curve
        return _resample(curve, discretised, count)
    points = np.empty((count, 3), dtype=np.float32)
    for index in range(count):
        point = discretised.Value(index + 1)  # 1-based
        points[index] = (point.X(), point.Y(), point.Z())
    return points


def _resample(curve: Any, discretised: Any, count: int) -> F32:
    """Evenly spaced samples when the natural discretisation exceeds :data:`MAX_POLYLINE_POINTS`."""
    first, last = curve.FirstParameter(), curve.LastParameter()
    points = np.empty((MAX_POLYLINE_POINTS, 3), dtype=np.float32)
    for index in range(MAX_POLYLINE_POINTS):
        parameter = first + (last - first) * index / (MAX_POLYLINE_POINTS - 1)
        point = curve.Value(parameter)
        points[index] = (point.X(), point.Y(), point.Z())
    return points


def _curve_type(curve: Any) -> str:
    try:
        return str(curve.GetType()).rsplit(".", 1)[-1]
    except Exception:  # no GetType on an exotic adaptor
        return ""


def _tangent(curve: Any) -> tuple[float, float, float]:
    """Unit tangent at mid-parameter; ``(0, 0, 0)`` when the curve has no derivative there."""
    from OCP.gp import gp_Vec

    mid = 0.5 * (curve.FirstParameter() + curve.LastParameter())
    point = curve.Value(mid)  # the adaptor's D1 needs a point out-parameter too
    vector = gp_Vec()
    try:
        curve.D1(mid, point, vector)
    except Exception:
        return (0.0, 0.0, 0.0)
    return _unit(vector)


def _edge_index_map(
    edge_map: Any,  # Any: the edge IndexedMap the caller already built
) -> Any:  # Any: IndexedMap[int, TopoDS_Shape] keyed by edge shape
    """An OCCT map from each edge shape to its zero-based id, for O(1) lookup (A9 review).

    The incidence builder has to name the edge a vertex meets. Scanning the edge list for each
    candidate is O(vertices x edges): **measured at 4.2 s of a 4.6 s job on a
    1,080-edge fused part**
    in the review. `TopTools_IndexedMapOfShape`-style hashing answers it in constant time.
    """
    import OCP.collections as occ

    index = occ.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    for position in range(1, edge_map.Extent() + 1):
        index.Add(edge_map.FindKey(position))
    return index


def face_count_in_mesh_order(shape: Any) -> int:  # Any: TopoDS_Shape
    """Faces in **the order the mesh numbers them**: `TopExp_Explorer`, counting repeats.

    `step.face_count` documents itself as "counting a face as often as the explorer visits it", and
    the mesh's per-triangle face ids come from exactly that walk (`step.tessellate_bodies`).
    ``TopExp.MapShapes_s``, which the edge and vertex records use, yields each face once instead —
    so
    using it here would mis-number every face after the first repeat. Measured equal for a box, the
    cold plate and the heat sink, but not equal in general, so the right walk is used rather than
    the
    convenient one.
    """
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def _face_ids_for_edges(
    shape: Any,  # Any: TopoDS_Shape
    edge_shapes: Sequence[Any],  # Any: TopoDS_Edge, in the record's edge order
    face_count: int,
) -> list[list[int]]:
    """For each edge, the face ids it borders, in **the mesh's face numbering** (T-124a).

    The numbering is the crux and it is subtle: the mesh assigns a triangle's face id from
    ``TopExp_Explorer(shape, TopAbs_FACE)`` order — ``step.face_count`` documents this as "counting
    a
    face as often as the explorer visits it" — while the edge and vertex records use
    ``TopExp.MapShapes_s``, which yields each shape **once**. The two agree whenever no face is
    visited twice (measured: equal for a box, the cold plate and the heat sink), but they are not
    the
    same enumeration in general, so this walks the explorer and calls a face **hidden by its
    position** in that walk. A face visited twice therefore contributes its id in both positions,
    and
    the transport's range check catches any disagreement with the mesh.

    Incidence comes from ``MapShapesAndAncestors_s`` on the **(EDGE, FACE)** axes, with duplicates
    removed by ``IsSame`` as A9 does. **Measured: (FACE, EDGE) returns nothing in this OCP build** —
    the same trap as A9's, where (EDGE, VERTEX) was empty and (VERTEX, EDGE) worked. A boundary edge
    borders one face; an interior edge two.
    """
    import OCP.collections as occ
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS

    found: list[list[int]] = [[] for _ in range(len(edge_shapes))]
    if not edge_shapes:
        return found

    # A face's id is its ordinal in the explorer walk, so the walk supplies both the id and the key.
    face_id_of: dict[int, int] = {}
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    ordinal = 0
    while explorer.More():
        face_id_of[hash(TopoDS.Face(explorer.Current()))] = ordinal
        ordinal += 1
        explorer.Next()
    if ordinal != face_count:
        raise GeometryError(
            f"the face walk found {ordinal} faces but the mesh counts {face_count}; "
            "the edge-to-face ids would not match the mesh"
        )

    ancestors = occ.IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, ancestors)
    edge_index = _edge_index_map_of(edge_shapes)
    for position in range(1, ancestors.Extent() + 1):
        edge = TopoDS.Edge(ancestors.FindKey(position))
        slot = edge_index.FindIndex(edge)
        if not 0 < slot <= len(edge_shapes):
            continue
        seen: list[Any] = []
        for candidate in ancestors.FindFromIndex(position):
            if any(candidate.IsSame(one) for one in seen):
                continue
            seen.append(candidate)
            face_id = face_id_of.get(hash(TopoDS.Face(candidate)))
            if face_id is not None:
                found[slot - 1].append(face_id)
    for ids in found:
        ids.sort()
    return found


def _edge_index_map_of(edge_shapes: Sequence[Any]) -> Any:  # Any: IndexedMap keyed by edge shape
    """A shape-hash map over an edge list, so a candidate can be named without a scan (A9)."""
    import OCP.collections as occ

    index = occ.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    for shape in edge_shapes:
        index.Add(shape)
    return index


def _incident_edges(
    shape: Any,  # Any: TopoDS_Shape
    vertex_map: Any,  # Any: the vertex IndexedMap, whose order defines the vertex ids
    edges: Sequence[TopoEdge],
    edge_map: Any,  # Any: the edge IndexedMap, so a shape can be named without a scan
    edge_index: Any,  # Any: shape -> id, built by `_edge_index_map`
) -> list[list[int]]:
    """The edge ids meeting each vertex, in ``vertex_map`` order (A9).

    Asked of the B-rep instead of guessed by proximity. The replaced version matched each edge end
    to the nearest sampled vertex within ``span * 1e-6`` — below typical STEP vertex tolerances, so
    imported geometry lost its incidence — and it was O(vertices x edges) Python work, which
    dominated this job on a large part.
    """
    import OCP.collections as occ
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    incident: list[list[int]] = [[] for _ in range(vertex_map.Extent())]
    if not edges:
        return incident

    ancestors = occ.IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_VERTEX, TopAbs_EDGE, ancestors)

    # A vertex's slot is its position in `vertex_map`, which is the id the record uses. Both maps
    # are built by `TopExp.MapShapes_s` over the same kind, so the same shape appears in both.
    slot_of: dict[int, int] = {}
    for index in range(1, vertex_map.Extent() + 1):
        vertex = TopoDS.Vertex(vertex_map.FindKey(index))
        slot_of[hash(vertex)] = index - 1

    for index in range(1, ancestors.Extent() + 1):
        vertex = TopoDS.Vertex(ancestors.FindKey(index))
        slot = slot_of.get(hash(vertex))
        if slot is None:
            continue
        seen: list[Any] = []
        for candidate in ancestors.FindFromIndex(index):
            if any(candidate.IsSame(one) for one in seen):
                continue
            seen.append(candidate)
            # A9: a hash lookup, not a scan of every edge.
            found = edge_index.FindIndex(candidate)
            if found > 0 and found <= len(edges):
                incident[slot].append(edges[found - 1].edge_id)
    for ids in incident:
        ids.sort()
    return incident


def part_topology(shape: Any, *, part: str, basis: str, scale: float) -> PartTopology:
    """The edge and vertex topology of one part shape (ADR-0022 15.1).

    ``shape`` is in units of ``scale`` metres (as the shape cache stores it); every figure returned
    is in metres. Faces are not enumerated here — they keep their own numbering.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX

    face_total = face_count_in_mesh_order(shape)
    result = PartTopology(part=part, basis=basis, scale=scale, faces=face_total)
    edge_map = _map_of(shape, TopAbs_EDGE)
    vertex_map = _map_of(shape, TopAbs_VERTEX)
    deflection = _deflection(shape, scale)

    edges: list[TopoEdge] = []
    edge_shapes: list[Any] = []  # Any: TopoDS_Edge, in the record's edge order (T-124a)
    for index in range(1, edge_map.Extent() + 1):
        edge = _downcast_edge(edge_map.FindKey(index))
        edge_shapes.append(edge)
        from OCP.BRepAdaptor import BRepAdaptor_Curve

        curve = BRepAdaptor_Curve(edge)
        props = GProp_GProps()
        BRepGProp.LinearProperties_s(edge, props)
        centre = props.CentreOfMass()
        points = _sample(edge, deflection)
        first, last = curve.FirstParameter(), curve.LastParameter()
        p_start, p_end = curve.Value(first), curve.Value(last)
        # BRepGProp and the adaptor answer in the shape's own units; every length is converted to
        # metres here with `scale`, the one place that knows the storage unit. Tangents are unit
        # vectors and stay unscaled.
        length = float(props.Mass()) * scale
        # `and`, not `or` (A8): a degenerate edge — a zero B-rep length — is not pickable even
        # when the sampler handed back two coincident points, which it does. §15.2 says such an
        # edge is "reported but not pickable", and with `or` a degenerate edge was pickable
        # whenever its polyline had two points, which is always.
        pickable = length > 0.0 and points.shape[0] >= 2
        edges.append(
            TopoEdge(
                edge_id=index - 1,
                length=length,
                centre=_scaled(centre, scale),
                start=_scaled(p_start, scale),
                end=_scaled(p_end, scale),
                tangent=_tangent(curve),
                curve=_curve_type(curve),
                points=(points * scale).astype(np.float32, copy=False),
                pickable=pickable,
            )
        )
    # T-124a: each edge's faces, in the mesh's face numbering. Computed after the loop because it
    # needs every edge shape at once, and it must use the SAME face walk the mesh does.
    faces_of = _face_ids_for_edges(shape, edge_shapes, face_total)
    # Rebuilt by index, not `list.index`: the latter is a linear scan per edge, which on a
    # 1,080-edge
    # part would be the same quadratic trap A9 was.
    result.edges = [
        replace(edge, faces=tuple(faces_of[position])) for position, edge in enumerate(edges)
    ]
    edges = result.edges
    edge_index = _edge_index_map(edge_map)
    result.vertices = _vertices(shape, vertex_map, edges, edge_map, edge_index, scale)
    # v3: the sizes of things, measured on the B-rep. Face areas use the SAME mesh-order walk the
    # face ids use (`subshape_list`), because a face area looked up by a face id must belong to that
    # face; the map-order walk would silently attribute areas to the wrong faces after the first
    # repeat, exactly the defect T-056-fix was about.
    result.face_sizes = _face_sizes(shape, scale)
    result.solids = _solids(shape, scale)
    return result


def _face_sizes(shape: Any, scale: float) -> list[TopoFace]:
    """Every face's area and centroid, in the mesh's face order (v3).

    ``scale`` is metres per shape unit, so an area scales by its **square**: getting that wrong
    would
    report a 100 mm square as 0.1 m² instead of 0.01 m², a hundredfold error that looks like a
    plausible number.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from stokes.geometry.operations.faces import subshape_list

    result: list[TopoFace] = []
    for index, face in enumerate(subshape_list(shape, "face")):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        centre = props.CentreOfMass()
        result.append(
            TopoFace(
                face_id=index,
                area=float(props.Mass()) * scale * scale,
                centroid=_scaled(centre, scale),
            )
        )
    return result


def _solids(shape: Any, scale: float) -> list[TopoSolid]:
    """Every solid's volume, centre of mass and own surface area (v3).

    A volume scales by the **cube** of the length scale. A shape with no solid — a shell, a face, a
    wire — gives an empty list rather than a zero, so "this part has no volume to report" is a fact
    the client can state instead of printing 0 m³ at the user.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from stokes.geometry.operations.faces import subshape_list

    solids: list[TopoSolid] = []
    for solid in subshape_list(shape, "solid"):
        volume_props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, volume_props)
        volume = float(volume_props.Mass())
        if volume < 0.0:
            # A shell OCCT could not orient: the magnitude is the volume, as `extract_fluid` does.
            volume = -volume
        centre = volume_props.CentreOfMass()
        area_props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(solid, area_props)
        solids.append(
            TopoSolid(
                volume=volume * scale * scale * scale,
                centroid=_scaled(centre, scale),
                area=float(area_props.Mass()) * scale * scale,
            )
        )
    return solids


def _scaled(point: Any, scale: float) -> tuple[float, float, float]:  # Any: OCP gp_Pnt
    """An OCP point in the shape's units as metres: the one conversion in this module."""
    return (float(point.X()) * scale, float(point.Y()) * scale, float(point.Z()) * scale)


def _vertices(
    shape: Any,  # Any: TopoDS_Shape
    vertex_map: Any,
    edges: list[TopoEdge],
    edge_map: Any,
    edge_index: Any,
    scale: float,
) -> list[TopoVertex]:
    """Vertices with their incident edges, from the B-rep (A9).

    ``scale`` converts the vertex positions to metres; incidence is ``_incident_edges``, which asks
    OCCT rather than comparing positions.
    """
    from OCP.TopoDS import TopoDS

    positions: list[tuple[float, float, float]] = []
    for index in range(1, vertex_map.Extent() + 1):
        vertex = TopoDS.Vertex(vertex_map.FindKey(index))
        raw = _vertex_point(vertex)  # already a plain triple, in the shape's own units
        positions.append((raw[0] * scale, raw[1] * scale, raw[2] * scale))

    # Vertex incidence from the B-rep itself (A9). The previous version matched each edge end to the
    # nearest sampled vertex within `span * 1e-6`, which is below typical STEP vertex tolerances, so
    # real incidence was lost on imported geometry — and it was an O(V x E) Python loop, which
    # dominated the job on a large part. `TopExp.MapShapesAndAncestors_s` answers it topologically:
    # for every vertex of the shape, the edges that meet it.
    #
    # The map lists an edge once per orientation (six entries for a box corner that has three
    # edges), so entries are deduplicated with `IsSame` before being recorded.
    incident = _incident_edges(shape, vertex_map, edges, edge_map, edge_index)

    result: list[TopoVertex] = []
    lengths = {e.edge_id: e.length for e in edges}
    for index, (position, attached) in enumerate(zip(positions, incident, strict=True)):
        attached.sort()
        result.append(
            TopoVertex(
                vertex_id=index,
                position=position,
                degree=len(attached),
                edge_ids=tuple(attached),
                edge_lengths=tuple(lengths[e] for e in attached),
            )
        )
    return result


def _vertex_point(vertex: Any) -> tuple[float, float, float]:  # Any: OCP TopoDS_Vertex
    point = None
    try:
        from OCP.BRep import BRep_Tool

        point = BRep_Tool.Pnt_s(vertex)
    except Exception:  # a vertex without a point
        return (0.0, 0.0, 0.0)
    return (float(point.X()), float(point.Y()), float(point.Z()))


def _span(positions: list[tuple[float, float, float]]) -> float:
    if not positions:
        return 0.0
    array = np.asarray(positions, dtype=np.float64)
    return float(np.linalg.norm(array.max(axis=0) - array.min(axis=0)))


def require_brep(shape: Any | None, part: str) -> Any:
    """The shape, or :class:`GeometryUnsupported` naming a mesh-only part (ADR-0022 15.1)."""
    if shape is None:
        raise GeometryUnsupported(
            f"part {part!r} has no B-rep, so it has no edges or vertices to pick",
            details={"part": part, "kind": "topology"},
        )
    return shape
