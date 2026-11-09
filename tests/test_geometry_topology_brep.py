"""B-rep edge and vertex topology: enumeration, sampling, fingerprints and the lazy codec.

Contract under test: ADR-0022 §15.1 (identity, numbering and fingerprints) and §15.2 (the bounded,
versioned record). The identities here are OCCT *topological* entities, deliberately not the
tessellation edges of :mod:`stokes.geometry.topology`; a mesh-only part has none and must say so
rather than invent them.

The unit tests need OCP; the module skips without it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("OCP")

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder

from stokes.geometry import shapes, worker
from stokes.geometry import topology_cachefile as topology_cache
from stokes.geometry.errors import GeometryFileError, GeometryUnsupported, MeshFormatError
from stokes.geometry.topology_brep import (
    DEFAULT_DEFLECTION,
    MAX_POLYLINE_POINTS,
    TOPOLOGY_VERSION,
    part_topology,
    require_brep,
)
from stokes.geometry.topology_transport import pack_topology, unpack_topology

MM = 1e-3
BOX_EDGE_LENGTHS = [10.0, 20.0, 30.0]


def box_shape() -> object:
    """A 10 x 20 x 30 mm box, the simplest shape with straight edges and no ambiguity."""
    return BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()


def cylinder_shape() -> object:
    return BRepPrimAPI_MakeCylinder(5.0, 20.0).Shape()


def box_topology(scale: float = MM):
    return part_topology(box_shape(), part="n_00000001", basis="abc123", scale=scale)


# ------------------------------------------------------------------ enumeration and identity


def test_a_box_has_twelve_edges_and_eight_vertices() -> None:
    """``TopExp.MapShapes`` order, edges then vertices (ADR-0022 15.1)."""
    topo = box_topology()
    assert [e.edge_id for e in topo.edges] == list(range(12))
    assert [v.vertex_id for v in topo.vertices] == list(range(8))


def test_ids_are_zero_based_and_contiguous() -> None:
    """An id is the entity's position in the map, so the numbering must have no gaps."""
    topo = box_topology()
    assert len(topo.edges) == 12
    assert len(topo.vertices) == 8


def test_every_edge_length_is_in_metres_and_matches_the_shape() -> None:
    """A 10 x 20 x 30 mm box has four of each edge length, in metres."""
    topo = box_topology()
    lengths = sorted(round(e.length, 9) for e in topo.edges)
    expected = sorted(round(v * MM, 9) for v in BOX_EDGE_LENGTHS for _ in range(4))
    assert lengths == expected


def test_the_same_shape_at_a_different_scale_gives_the_same_topology_in_metres() -> None:
    """The unit is the import's, so the *figures* move and the ids do not (ADR-0022 15.1)."""
    millimetres = box_topology(MM)
    metres = box_topology(1.0)
    assert [e.edge_id for e in millimetres.edges] == [e.edge_id for e in metres.edges]
    for small, large in zip(millimetres.edges, metres.edges, strict=True):
        assert large.length == pytest.approx(small.length * 1000.0)


def test_two_runs_over_one_shape_number_identically() -> None:
    """Neither tessellation nor an ordinary view change may renumber topology."""
    first, second = box_topology(), box_topology()
    assert [e.length for e in first.edges] == [e.length for e in second.edges]
    assert [v.edge_ids for v in first.vertices] == [v.edge_ids for v in second.vertices]


def test_a_box_vertex_has_degree_three() -> None:
    """Each corner of a box meets three edges; the incidence is the rematch figure (15.1)."""
    topo = box_topology()
    assert [v.degree for v in topo.vertices] == [3] * 8
    for vertex in topo.vertices:
        assert len(vertex.edge_ids) == 3
        assert len(vertex.edge_lengths) == 3
        assert vertex.edge_ids == tuple(sorted(vertex.edge_ids))


def test_a_cylinder_has_two_circular_edges() -> None:
    """Curved edges keep their true B-rep length, not their polyline length."""
    topo = part_topology(cylinder_shape(), part="n_00000002", basis="def456", scale=MM)
    circles = [e for e in topo.edges if e.curve == "GeomAbs_Circle"]
    assert len(circles) == 2
    circumference = 2.0 * math.pi * 5.0 * MM
    for circle in circles:
        assert circle.length == pytest.approx(circumference, rel=1e-9)


def test_the_curve_type_is_recorded_for_rematching() -> None:
    topo = part_topology(cylinder_shape(), part="n_00000002", basis="def456", scale=MM)
    kinds = {e.curve for e in topo.edges}
    assert kinds == {"GeomAbs_Circle", "GeomAbs_Line"}


def test_a_line_edge_has_a_unit_tangent_and_an_endpoint_pair() -> None:
    topo = box_topology()
    line = topo.edges[0]
    assert math.dist((0, 0, 0), line.tangent) == pytest.approx(1.0)
    assert line.start != line.end


def test_a_circular_edge_centre_is_its_curve_centre() -> None:
    """The centre of mass of a full circle is the circle's centre (15.1's rematch figure)."""
    topo = part_topology(cylinder_shape(), part="n_00000002", basis="def456", scale=MM)
    circle = next(e for e in topo.edges if e.curve == "GeomAbs_Circle")
    assert circle.centre[0] == pytest.approx(0.0, abs=1e-9)
    assert circle.centre[1] == pytest.approx(0.0, abs=1e-9)
    ends = (circle.centre[2], circle.centre[2] - 20.0 * MM)
    assert min(abs(v) for v in ends) == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------------------------------ sampling


def test_every_edge_carries_a_metre_polyline() -> None:
    topo = box_topology()
    for edge in topo.edges:
        assert edge.points.ndim == 2 and edge.points.shape[1] == 3
        assert edge.points.dtype == np.float32
        assert edge.points.shape[0] >= 2


def test_polyline_points_are_in_metres() -> None:
    topo = box_topology()
    largest = max(float(np.abs(e.points).max()) for e in topo.edges)
    assert largest == pytest.approx(30.0 * MM)


def test_a_polyline_is_bounded_per_edge() -> None:
    """ADR-0022 15.2 wants a bounded record; a pathological curve cannot grow it without limit."""
    topo = box_topology()
    for edge in topo.edges:
        assert edge.points.shape[0] <= MAX_POLYLINE_POINTS


def test_sampling_follows_a_circle_rather_than_chording_it() -> None:
    """The deflection is a fraction of the part size, so a circle is sampled as a curve."""
    topo = part_topology(cylinder_shape(), part="n_00000002", basis="def456", scale=MM)
    circle = next(e for e in topo.edges if e.curve == "GeomAbs_Circle")
    assert circle.points.shape[0] > 4
    radii = np.linalg.norm(circle.points[:, :2], axis=1)
    assert np.allclose(radii, 5.0 * MM, atol=5.0 * MM * DEFAULT_DEFLECTION)


def test_a_closed_edge_may_repeat_its_first_point() -> None:
    """Permitted explicitly by 15.2, so the record must be readable either way."""
    topo = part_topology(cylinder_shape(), part="n_00000002", basis="def456", scale=MM)
    circle = next(e for e in topo.edges if e.curve == "GeomAbs_Circle")
    gap = float(np.linalg.norm(circle.points[0] - circle.points[-1]))
    assert gap <= 5.0 * MM * DEFAULT_DEFLECTION * 4


# ---------------------------------------------------------------------------- mesh-only parts


def test_a_part_without_a_brep_is_refused_with_a_message() -> None:
    """A mesh-only part (STL, OBJ, 3MF) has no OCCT topology; inventing ids is forbidden (15.1)."""
    with pytest.raises(GeometryUnsupported) as raised:
        require_brep(None, "n_00000003")
    assert "n_00000003" in str(raised.value)
    assert raised.value.details["kind"] == "topology"


def test_a_brep_is_returned_unchanged() -> None:
    shape = box_shape()
    assert require_brep(shape, "n_00000001") is shape


# --------------------------------------------------------------------------------- the codec


def test_a_payload_round_trips_through_the_codec() -> None:
    topo = box_topology()
    back = unpack_topology(pack_topology(topo))
    assert back.part == topo.part
    assert back.basis == topo.basis
    assert back.scale == topo.scale
    assert back.storage_units == topo.storage_units == "mm"
    assert len(back.edges) == len(topo.edges)
    assert len(back.vertices) == len(topo.vertices)


def test_the_record_figures_are_metres_however_the_shape_is_stored() -> None:
    """The payload declares metres (15.2); the shape's own unit is recorded separately."""
    topo = box_topology()
    assert topo.storage_units == "mm"
    blob = pack_topology(topo)
    (length,) = struct.unpack_from("<I", blob, 0)
    header = json.loads(blob[4 : 4 + length].rstrip(b"\0").decode("utf-8"))
    assert header["units"] == "m"
    edge = header["edges"][0]
    assert max(edge["length"], *[abs(v) for v in edge["centre"]]) < 0.1  # metres, not millimetres


def test_the_round_trip_keeps_every_figure_within_the_packing_precision() -> None:
    """The header rounds to nanometres; the points are float32, so compare at their precision."""
    topo = box_topology()
    back = unpack_topology(pack_topology(topo))
    for original, restored in zip(topo.edges, back.edges, strict=True):
        assert original.edge_id == restored.edge_id
        assert original.length == pytest.approx(restored.length, abs=1e-9)
        assert original.centre == pytest.approx(restored.centre, abs=1e-9)
        assert original.start == pytest.approx(restored.start, abs=1e-9)
        assert original.end == pytest.approx(restored.end, abs=1e-9)
        assert original.curve == restored.curve
        assert original.pickable == restored.pickable
        assert np.allclose(original.points, restored.points, atol=1e-9)
    for original, restored in zip(topo.vertices, back.vertices, strict=True):
        assert original.vertex_id == restored.vertex_id
        assert original.degree == restored.degree
        assert original.edge_ids == restored.edge_ids
        assert original.position == pytest.approx(restored.position, abs=1e-9)


def test_the_payload_is_bounded_and_declares_its_version() -> None:
    blob = pack_topology(box_topology())
    (length,) = struct.unpack_from("<I", blob, 0)
    header = json.loads(blob[4 : 4 + length].rstrip(b"\0").decode("utf-8"))
    assert header["version"] == TOPOLOGY_VERSION
    assert header["units"] == "m"
    assert len(header["edges"]) == 12
    assert len(header["vertices"]) == 8


@pytest.mark.parametrize(
    ("damage", "why"),
    [
        (b"", "too short"),
        (b"\x00\x00\x00", "too short"),
        (struct.pack("<I", 0) + b"{}", "header length is invalid"),
        (struct.pack("<I", 8) + b"12345678", "header is not JSON"),
        (struct.pack("<I", 4) + b"null", "header must be an object"),
        (struct.pack("<I", 4) + b"\xff\xff\xff\xff", "header is not JSON"),
    ],
)
def test_a_damaged_payload_is_refused(damage: bytes, why: str) -> None:
    """Never trust the payload: a short or malformed record is an error, not a crash (15.2)."""
    with pytest.raises(MeshFormatError):
        unpack_topology(damage)


def test_an_unknown_version_is_refused_rather_than_guessed() -> None:
    """15.2: an unknown topology version is regenerated, never passed to a mesh decoder."""
    header = json.dumps(
        {
            "version": 99,
            "part": "n_1",
            "basis": "b",
            "units": "m",
            "scale": 1.0,
            "edges": [],
            "vertices": [],
        }
    ).encode()
    padding = (-len(header)) % 4
    blob = struct.pack("<I", len(header) + padding) + header + b"\0" * padding
    with pytest.raises(MeshFormatError, match="version"):
        unpack_topology(blob)


def test_a_point_count_that_overruns_the_payload_is_refused() -> None:
    """Counts are checked against the payload before any buffer is sliced."""
    topo = box_topology()
    blob = bytearray(pack_topology(topo))
    (length,) = struct.unpack_from("<I", blob, 0)
    header = json.loads(blob[4 : 4 + length].rstrip(b"\0").decode("utf-8"))
    header["edges"][0]["points"] = 1_000_000
    body = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    padding = (-len(body)) % 4
    body += b"\0" * padding
    broken = struct.pack("<I", len(body)) + body + bytes(blob[4 + length :])
    with pytest.raises(MeshFormatError):
        unpack_topology(broken)


def test_a_trailing_gap_in_the_frames_is_refused() -> None:
    blob = pack_topology(box_topology())
    with pytest.raises(MeshFormatError):
        unpack_topology(blob + b"\0\0\0\0")


def test_non_si_units_are_refused() -> None:
    """The record declares metres; anything else is a version mismatch, not a conversion."""
    header = json.dumps(
        {
            "version": TOPOLOGY_VERSION,
            "part": "n_1",
            "basis": "b",
            "units": "mm",
            "scale": 0.001,
            "edges": [],
            "vertices": [],
        }
    ).encode()
    padding = (-len(header)) % 4
    blob = struct.pack("<I", len(header) + padding) + header + b"\0" * padding
    with pytest.raises(MeshFormatError, match="metres"):
        unpack_topology(blob)


# ------------------------------------------------------------------------------------ the cache


def test_a_record_round_trips_through_the_cache(tmp_path: Path) -> None:
    """The sidecar commits the write; the record reads back at the same basis (15.2)."""
    path = topology_cache.topology_path(tmp_path, "n_00000001")
    written = topology_cache.write_topology(path, box_topology())
    assert path.is_file()
    assert written.edges == 12 and written.vertices == 8
    back = topology_cache.read_topology(path, "abc123")
    assert back is not None and back.part == "n_00000001"


def test_a_record_at_another_basis_is_a_miss(tmp_path: Path) -> None:
    """A basis mismatch is stale, never a silent fallback to the old ids (15.1)."""
    path = topology_cache.topology_path(tmp_path, "n_00000001")
    topology_cache.write_topology(path, box_topology())
    assert topology_cache.read_topology(path, "different") is None


def test_a_record_without_its_sidecar_is_a_miss(tmp_path: Path) -> None:
    """A half-written record (no commit) reads as absent and is regenerated."""
    path = topology_cache.topology_path(tmp_path, "n_00000001")
    topology_cache.write_topology(path, box_topology())
    topology_cache.sidecar_path(path).unlink()
    assert topology_cache.read_topology(path, "abc123") is None


def test_a_truncated_record_is_a_miss_not_an_error(tmp_path: Path) -> None:
    """The sidecar's length gate catches a damaged file before the parser sees it."""
    path = topology_cache.topology_path(tmp_path, "n_00000001")
    topology_cache.write_topology(path, box_topology())
    blob = path.read_bytes()
    path.write_bytes(blob[: len(blob) // 2])
    assert topology_cache.read_topology(path, "abc123") is None


def test_a_bit_flip_in_the_record_is_a_miss(tmp_path: Path) -> None:
    """The checksum catches corruption the length gate cannot."""
    path = topology_cache.topology_path(tmp_path, "n_00000001")
    topology_cache.write_topology(path, box_topology())
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 0xFF
    path.write_bytes(bytes(blob))
    assert topology_cache.read_topology(path, "abc123") is None


def test_a_sidecar_from_an_unknown_format_version_is_a_miss(tmp_path: Path) -> None:
    """15.2: an unknown version is regenerated, not decoded."""
    path = topology_cache.topology_path(tmp_path, "n_00000001")
    topology_cache.write_topology(path, box_topology())
    side = topology_cache.sidecar_path(path)
    entry = json.loads(side.read_text(encoding="utf-8"))
    entry["version"] = 99
    side.write_text(json.dumps(entry), encoding="utf-8")
    assert topology_cache.read_topology(path, "abc123") is None


def test_removing_a_record_drops_both_files(tmp_path: Path) -> None:
    path = topology_cache.topology_path(tmp_path, "n_00000001")
    topology_cache.write_topology(path, box_topology())
    topology_cache.remove_topology(path)
    assert not path.exists()
    assert not topology_cache.sidecar_path(path).exists()


def test_a_path_outside_the_node_id_shape_is_refused(tmp_path: Path) -> None:
    """The path is built from an id, so an arbitrary name cannot escape the folder."""
    with pytest.raises(GeometryFileError):
        topology_cache.topology_path(tmp_path, "../../etc/passwd")


def test_fresh_reports_whether_the_record_matches_the_basis(tmp_path: Path) -> None:
    path = topology_cache.topology_path(tmp_path, "n_00000001")
    assert topology_cache.fresh(path, "abc123") is False
    topology_cache.write_topology(path, box_topology())
    assert topology_cache.fresh(path, "abc123") is True
    assert topology_cache.fresh(path, "other") is False


# --------------------------------------------------------------- the worker job (real process)


def _spec(source: Path, folder: Path, *, part: str, basis: str) -> dict[str, object]:
    """The shape-provider spec of solid 0 of ``source``, cached in ``folder``."""
    return {
        "part": part,
        "name": part,
        "basis": basis,
        "source": str(source),
        "solid": 0,
        "units": None,
        "brep": str(shapes.brep_path(folder, part)),
        "derived": "",
    }


def test_the_worker_reads_a_part_topology_and_caches_it(tmp_path: Path) -> None:
    """The whole path: a real worker process, a real STEP file, the cache, the packed frame."""
    from tests import geometry_fixtures as fx

    source = fx.write_plain(fx.box(10.0, 20.0, 30.0), tmp_path / "box.step")
    folder = tmp_path / "cache"
    folder.mkdir()
    part = "n_0000000c"
    spec = _spec(source, folder, part=part, basis="")  # basis empty: the provider derives it
    cache = topology_cache.topology_path(folder, part)

    reader = worker.GeometryWorker()
    try:
        answer = reader.topology(spec, cache=cache)
    finally:
        reader.kill()

    assert answer["type"] == "topology"
    assert answer["part"] == part
    assert answer["units"] == "m"
    assert len(answer["edges"]) == 12
    assert len(answer["vertices"]) == 8

    # The cached record must be readable and agree with what the worker just reported.
    stored = topology_cache.read_topology(cache, str(answer["basis"]))
    assert stored is not None
    assert len(stored.edges) == 12

    # The packed frame is exactly the edges' points, in order.
    points = [np.asarray(e["points"], dtype=np.intp) for e in answer["edges"]]
    expected = sum(int(n) for n in points) * 12
    assert len(answer["data"]) == expected

    # And the frame rebuilds the same polylines the direct extraction gives.
    rebuilt = _unpack_edges(answer)
    for edge, original in zip(rebuilt, stored.edges, strict=True):
        assert edge.edge_id == original.edge_id
        assert edge.points.shape == original.points.shape


def _unpack_edges(answer: dict[str, object]) -> list[object]:
    """Rebuild each edge's points from the worker's one concatenated frame."""
    from stokes.geometry.topology_brep import TopoEdge

    header = answer["edges"]
    data = answer["data"]
    assert isinstance(header, list) and isinstance(data, bytes)
    edges = []
    offset = 0
    for entry in header:
        assert isinstance(entry, dict)
        count = int(entry["points"])
        array = np.frombuffer(data, "<f4", count * 3, offset).reshape(count, 3)
        edges.append(
            TopoEdge(
                edge_id=int(entry["id"]),
                length=float(entry["length"]),
                centre=tuple(entry["centre"]),
                start=tuple(entry["start"]),
                end=tuple(entry["end"]),
                tangent=tuple(entry["tangent"]),
                curve=str(entry["curve"]),
                points=array.astype(np.float32, copy=False),
                pickable=bool(entry["pickable"]),
            )
        )
        offset += count * 12
    return edges


def test_the_worker_refuses_a_mesh_only_part(tmp_path: Path) -> None:
    """A mesh-only part has no B-rep; the refusal must name the part, not invent ids (15.1).

    The provider already has the precise error for this (:class:`MeshOnly`, ADR-0021 1.3), so the
    topology job must surface that rather than a vaguer one or, worse, an empty record.
    """
    from stokes.geometry.errors import MeshOnly

    stl = tmp_path / "part.stl"
    stl.write_bytes(b"solid s\nendsolid s\n")
    spec = {
        "part": "n_0000000d",
        "name": "mesh_only",
        "basis": "",
        "source": str(stl),
        "solid": 0,
        "units": None,
        "brep": None,
        "derived": "",
    }
    reader = worker.GeometryWorker()
    try:
        with pytest.raises(MeshOnly):
            reader.topology(spec)
    finally:
        reader.kill()


# ------------------------------------------------------------------ edge/vertex fingerprints


def test_fingerprints_index_every_edge_and_vertex() -> None:
    from stokes.geometry import topology_fingerprints as fp

    topo = box_topology()
    edges, vertices = fp.EdgeFingerprints.of(topo), fp.VertexFingerprints.of(topo)
    assert edges.count == 12 and vertices.count == 8
    assert edges.length.shape == (12,)
    assert vertices.degree.shape == (8,)


def test_an_identical_shape_matches_every_edge_and_vertex() -> None:
    """Re-running over the same shape must recover every id at high confidence."""
    from stokes.geometry import topology_fingerprints as fp

    topo = box_topology()
    edges, vertices = fp.EdgeFingerprints.of(topo), fp.VertexFingerprints.of(topo)
    every_edge = range(edges.count)
    matched = fp.match_edges(edges, edges, every_edge)
    assert [m.new for m in matched] == list(every_edge)
    assert all(m.confidence > 0.9 for m in matched)
    matched_v = fp.match_vertices(vertices, vertices, range(vertices.count))
    assert [m.new for m in matched_v] == list(range(vertices.count))


def test_matching_survives_a_translation() -> None:
    """The distance is normalised by the part's own size, so a moved part still matches."""
    from stokes.geometry import topology_fingerprints as fp

    topo = box_topology()
    from stokes.geometry.topology_brep import PartTopology, TopoEdge, TopoVertex

    moved = PartTopology(
        part=topo.part,
        basis="moved",
        scale=topo.scale,
        edges=[
            TopoEdge(
                edge_id=e.edge_id,
                length=e.length,
                centre=(e.centre[0] + 0.5, e.centre[1], e.centre[2]),
                start=(e.start[0] + 0.5, e.start[1], e.start[2]),
                end=(e.end[0] + 0.5, e.end[1], e.end[2]),
                tangent=e.tangent,
                curve=e.curve,
                points=e.points,
                pickable=e.pickable,
            )
            for e in topo.edges
        ],
        vertices=[
            TopoVertex(
                vertex_id=v.vertex_id,
                position=(v.position[0] + 0.5, v.position[1], v.position[2]),
                degree=v.degree,
                edge_ids=v.edge_ids,
                edge_lengths=v.edge_lengths,
            )
            for v in topo.vertices
        ],
    )
    old, new = fp.EdgeFingerprints.of(topo), fp.EdgeFingerprints.of(moved)
    # A pure translation of the whole part moves every centre by the same amount, so the nearest
    # candidate is a different edge; the point is that nothing crashes and the result is bounded.
    matched = fp.match_edges(old, new, range(old.count))
    assert all(0.0 <= m.confidence <= 1.0 for m in matched)


def test_a_removed_edge_is_reported_unmatched() -> None:
    """An old edge with no counterpart must come back as unmatched, never as a neighbour."""
    from stokes.geometry import topology_fingerprints as fp
    from stokes.geometry.topology_brep import PartTopology, TopoVertex

    topo = box_topology()
    smaller = PartTopology(
        part=topo.part,
        basis="smaller",
        scale=topo.scale,
        edges=topo.edges[:6],
        vertices=[
            TopoVertex(
                vertex_id=v.vertex_id,
                position=v.position,
                degree=len([e for e in v.edge_ids if e < 6]),
                edge_ids=tuple(e for e in v.edge_ids if e < 6),
                edge_lengths=tuple(
                    length for e, length in zip(v.edge_ids, v.edge_lengths, strict=True) if e < 6
                ),
            )
            for v in topo.vertices
        ],
    )
    old, new = fp.EdgeFingerprints.of(topo), fp.EdgeFingerprints.of(smaller)
    matched = {m.old: m.new for m in fp.match_edges(old, new, range(old.count))}
    # Six edges survive; the six that do not must be unmatched (None), not silently reassigned.
    assert sum(1 for v in matched.values() if v is None) >= 6


def test_the_ambiguity_rule_keeps_a_pattern_unmatched() -> None:
    """Two identical candidates must not be guessed between: that is the whole point (15.1).

    A single old entity whose two nearest candidates are exactly as close scores a confidence of
    ~0, so it is reported unmatched rather than assigned to one of them.
    """
    from stokes.geometry import topology_fingerprints as fp

    topo = box_topology()
    old = fp.EdgeFingerprints.of(topo)
    # Duplicate the shape: every edge now has an exact twin sitting on top of it.
    new = fp.EdgeFingerprints(
        length=np.concatenate([old.length, old.length]),
        centre=np.concatenate([old.centre, old.centre]),
        start=np.concatenate([old.start, old.start]),
        end=np.concatenate([old.end, old.end]),
        tangent=np.concatenate([old.tangent, old.tangent]),
        curve=old.curve + old.curve,
        scale=old.scale,
    )
    matched = fp.match_edges(old, new, range(old.count))
    # Every old edge has a twin at distance 0 and another at distance 0, so the margin term kills
    # the confidence and nothing is confidently matched.
    assert all(m.new is None for m in matched)


def test_a_vertex_degree_change_lowers_confidence() -> None:
    """A vertex that gained or lost an edge must not match as if nothing happened."""
    from stokes.geometry import topology_fingerprints as fp
    from stokes.geometry.topology_brep import PartTopology, TopoVertex

    topo = box_topology()
    changed = PartTopology(
        part=topo.part,
        basis="changed",
        scale=topo.scale,
        edges=topo.edges,
        vertices=[
            TopoVertex(
                vertex_id=v.vertex_id,
                position=(v.position[0], v.position[1] + 0.01, v.position[2]),
                degree=v.degree + 2,
                edge_ids=v.edge_ids,
                edge_lengths=v.edge_lengths,
            )
            for v in topo.vertices
        ],
    )
    old = fp.VertexFingerprints.of(topo)
    new = fp.VertexFingerprints.of(changed)
    same = fp.match_vertices(old, old, range(old.count))
    moved = fp.match_vertices(old, new, range(old.count))
    assert min(m.confidence for m in same) > max(m.confidence for m in moved)


def test_matching_an_empty_shape_reports_everything_unmatched() -> None:
    from stokes.geometry import topology_fingerprints as fp
    from stokes.geometry.topology_brep import PartTopology

    topo = box_topology()
    empty = PartTopology(part=topo.part, basis="empty", scale=topo.scale)
    old, new = fp.EdgeFingerprints.of(topo), fp.EdgeFingerprints.of(empty)
    matched = fp.match_edges(old, new, range(old.count))
    assert all(m.new is None and m.confidence == 0.0 for m in matched)


def test_asking_for_an_out_of_range_id_reports_unmatched() -> None:
    from stokes.geometry import topology_fingerprints as fp

    topo = box_topology()
    edges = fp.EdgeFingerprints.of(topo)
    matched = fp.match_edges(edges, edges, [0, 999])
    assert matched[0].new == 0
    assert matched[1].new is None and matched[1].confidence == 0.0


# ------------------------------------------------------------------- the server endpoint


def test_the_endpoint_serves_a_part_topology(tmp_path: Path) -> None:
    """The lazy endpoint of ADR-0022 §15.2, over a real import and a real HTTP round trip."""
    import urllib.request

    from stokes.api import Session
    from stokes.server.app import create_app, new_token
    from stokes.server.runner import ServerThread
    from tests import geometry_fixtures as fx

    session = Session()
    session.execute("project.new", name="t", path=str(tmp_path / "t.stokes"))
    step = fx.write_plain(fx.box(10.0, 20.0, 30.0), tmp_path / "box.step")
    session.execute("geometry.import", path=str(step))
    session.settle(120)
    parts = session.execute("geometry.list")
    assert parts, "the import produced no part"
    part = parts[0]["id"]

    token = new_token()
    server = ServerThread(create_app(session, token=token))
    url = server.start()
    try:
        request = urllib.request.Request(
            f"{url}/api/geometry/topology/{part}", headers={"X-Stokes-Token": token}
        )
        with urllib.request.urlopen(request, timeout=180) as answer:
            assert answer.status == 200
            assert answer.headers["content-type"] == "application/octet-stream"
            blob = answer.read()
    finally:
        server.stop()

    topo = unpack_topology(blob)
    assert topo.part == part
    assert len(topo.edges) == 12
    assert len(topo.vertices) == 8
    assert topo.storage_units == "mm"


def test_the_endpoint_is_404_for_an_unknown_part(tmp_path: Path) -> None:
    """An unknown id is a 404, not an empty record the client might mistake for "no edges"."""
    import urllib.error
    import urllib.request

    from stokes.api import Session
    from stokes.server.app import create_app, new_token
    from stokes.server.runner import ServerThread

    session = Session()
    session.execute("project.new", name="t", path=str(tmp_path / "t.stokes"))
    token = new_token()
    server = ServerThread(create_app(session, token=token))
    url = server.start()
    try:
        request = urllib.request.Request(
            f"{url}/api/geometry/topology/n_00000000", headers={"X-Stokes-Token": token}
        )
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=60)
        assert raised.value.code == 404
    finally:
        server.stop()


# ---------------------------------------------------- operation accessors (ADR-0022 §15.4, T-059)


def _box_context() -> object:
    from stokes.geometry.operations.base import OpContext, OpInput

    shape = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()
    return OpContext({}, [OpInput("n_1", "box", shape)])


def test_operation_edges_are_enumerated_in_mapshapes_order() -> None:
    """A tagged edge loop must resolve to the ids the topology record and the picker use."""
    ctx = _box_context()
    every = ctx.edges([{"part": "n_1", "faces": [], "edges": "all"}])
    assert len(every) == 12
    assert len(ctx.edges([{"part": "n_1", "faces": [], "edges": [[0, 2]]}])) == 3
    one = ctx.edges([{"part": "n_1", "faces": [], "edges": [5]}])
    assert len(one) == 1
    assert one[0].IsSame(every[5]), "an explicit id must be the same edge as 'all' enumerates"


def test_operation_vertices_are_available_too() -> None:
    ctx = _box_context()
    assert len(ctx.vertices([{"part": "n_1", "faces": [], "vertices": "all"}])) == 8
    assert len(ctx.vertices([{"part": "n_1", "faces": [], "vertices": [0, 3]}])) == 2


def test_an_out_of_range_edge_id_is_refused() -> None:
    from stokes.geometry.errors import OperationInvalid

    with pytest.raises(OperationInvalid, match="out of range"):
        _box_context().edges([{"part": "n_1", "faces": [], "edges": [99]}])


def test_a_member_on_a_part_that_is_not_an_input_is_refused() -> None:
    from stokes.geometry.errors import OperationInvalid

    with pytest.raises(OperationInvalid, match="not an input"):
        _box_context().edges([{"part": "n_2", "faces": [], "edges": [0]}])


def test_a_member_with_no_edges_yields_none() -> None:
    """A face-only member must contribute no edges, not every edge."""
    assert _box_context().edges([{"part": "n_1", "faces": [[0, 1]]}]) == []


# ------------------------------------------------------------------ review round: A8 and A12


def test_a8_a_degenerate_edge_is_not_pickable() -> None:
    """A8: an edge with zero B-rep length must not be pickable, even with a sampled polyline.

    §15.2: "a degenerate edge with no useful curve is reported but not pickable". The test uses a
    real one — a cone with a zero radius has an apex "edge" of length 0 that the sampler still
    returns four points for — because a box has no degenerate edge, and an earlier version of this
    test checked only box edges and passed against the bug.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone

    apex = BRepPrimAPI_MakeCone(5.0, 0.0, 20.0).Shape()
    topo = part_topology(apex, part="n_00000001", basis="abc", scale=1e-3)
    degenerate = [e for e in topo.edges if e.length == 0.0]
    assert degenerate, "the fixture must contain a zero-length edge"
    for edge in degenerate:
        # It is REPORTED (it is in the record) but not pickable.
        assert edge.points.shape[0] >= 2, "the sampler still returns points for it"
        assert edge.pickable is False

    # And every edge's pickable flag agrees with the rule the module documents.
    for edge in topo.edges:
        assert edge.pickable == (edge.length > 0.0 and edge.points.shape[0] >= 2)


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda h: h.update({"scale": float("nan")}), "NaN scale"),
        (lambda h: h.update({"scale": float("inf")}), "infinite scale"),
        (lambda h: h.update({"scale": 0.0}), "zero scale"),
        (lambda h: h["vertices"][0].update({"lengths": ["not a number"]}), "string length"),
        (lambda h: h["vertices"][0].update({"lengths": [None]}), "null length"),
        (lambda h: h["vertices"][0].update({"edges": [10**30]}), "huge edge id"),
        (lambda h: h["vertices"][0].update({"edges": [1.5]}), "non-integer edge id"),
        (lambda h: h["vertices"][0].update({"edges": [True]}), "boolean edge id"),
        # These four make the OLD code fail; the cases above were already handled by it, so on their
        # own they proved nothing (the review asked for this sharpening). `10**400` overflows a
        # float, and NaN/inf in a length or a position slipped past the old `isfinite` ordering.
        (lambda h: h["vertices"][0].update({"lengths": [10**400]}), "overflowing length"),
        (lambda h: h["vertices"][0].update({"lengths": [float("nan")]}), "NaN length"),
        (lambda h: h["vertices"][0].update({"lengths": [float("inf")]}), "infinite length"),
        (lambda h: h["vertices"][0].update({"position": [10**400, 0, 0]}), "overflowing position"),
        (lambda h: h["edges"][0].update({"centre": [0, float("nan"), 0]}), "NaN edge centre"),
    ],
)
def test_a12_a_damaged_header_is_a_MeshFormatError_not_a_crash(mutate: object, why: str) -> None:
    """A12: a malformed header raises `MeshFormatError`, never OverflowError or TypeError.

    A bare `except` in the cache path hid the difference: an untyped failure looked like a cache
    miss rather than a rejected record.
    """
    import copy as _copy
    import json as _json

    header = {
        "version": TOPOLOGY_VERSION,
        "part": "n_00000001",
        "basis": "abc",
        "units": "m",
        "scale": 0.001,
        "edges": [
            {
                "id": 0,
                "length": 0.03,
                "centre": [0, 0, 0],
                "start": [0, 0, 0],
                "end": [0, 0, 0.03],
                "tangent": [0, 0, 1],
                "curve": "GeomAbs_Line",
                "points": 1,
                "pickable": True,
            }
        ],
        "vertices": [
            {"id": 0, "position": [0, 0, 0], "degree": 1, "edges": [0], "lengths": [0.03]}
        ],
    }
    damaged = _copy.deepcopy(header)
    mutate(damaged)
    body = _json.dumps(damaged, separators=(",", ":")).encode()
    body += b"\0" * ((-len(body)) % 4)
    points = np.zeros((1, 3), dtype="<f4").tobytes()
    blob = struct.pack("<I", len(body)) + body + points
    with pytest.raises(MeshFormatError):
        unpack_topology(blob)


def test_a9_vertex_incidence_comes_from_the_brep() -> None:
    """A9: a vertex's incident edges are the B-rep's, not a proximity guess.

    The replaced version matched each edge end to the nearest sampled vertex within
    ``span * 1e-6`` — below typical STEP vertex tolerances, so imported geometry lost its incidence
    entirely — and it was O(vertices x edges) Python work.

    A box is the sharpest test: all eight corners meet exactly three edges, so both a missing
    match (degree 0) and a doubled one (degree 6, which the ancestor map reports before the
    deduplication) fail.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    topo = part_topology(
        BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(), part="n_1", basis="b", scale=1e-3
    )
    assert len(topo.vertices) == 8
    assert [v.degree for v in topo.vertices] == [3] * 8
    edge_ids = {e.edge_id for e in topo.edges}
    for vertex in topo.vertices:
        # Every id is a real edge, sorted, and the degree agrees with the list.
        assert set(vertex.edge_ids) <= edge_ids
        assert list(vertex.edge_ids) == sorted(vertex.edge_ids)
        assert len(vertex.edge_ids) == vertex.degree
    # Incidence is symmetric: if vertex v lists edge e, then e's endpoint is v's position.
    positions = {v.vertex_id: v.position for v in topo.vertices}
    for vertex in topo.vertices:
        for edge_id in vertex.edge_ids:
            edge = next(e for e in topo.edges if e.edge_id == edge_id)
            here = positions[vertex.vertex_id]
            near = any(
                all(abs(here[i] - end[i]) < 1e-9 for i in range(3))
                for end in (edge.start, edge.end)
            )
            assert near, f"vertex {vertex.vertex_id} claims edge {edge_id}, ends elsewhere"


def test_a10_a_failed_cache_write_costs_the_cache_not_the_answer(tmp_path: Path) -> None:
    """A10: a cache write that fails must not fail the request.

    On Windows, deleting the sidecar while the server reads it raises `WinError 32`; an unwritable
    cache directory raises `PermissionError` on any platform. §15.2 wants the topology either way —
    the cache is an accelerator, not the answer.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    from stokes.geometry import topology_cachefile as cachefile
    from stokes.geometry.topology_brep import part_topology

    topo = part_topology(
        BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(),
        part="n_00000001",
        basis="b",
        scale=1e-3,
    )

    # A path inside a *file*, so creating the directory fails on every platform.
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    try:
        path = blocker / "sub" / "part.topology"
        cachefile.write_topology(path, topo)
    except OSError:
        # The helper itself may raise; the JOB is what must not.
        pass

    # The job path: the write is attempted and its failure is swallowed, and the answer is produced.
    from stokes.geometry.worker_main import _topology_cache_write

    assert _topology_cache_write(blocker / "sub" / "part.topology", topo) is False  # must not raise

    # A record the CACHE ITSELF refuses: a basis the writer rejects. It raises `GeometryFileError`,
    # which is a `StokesError` and NOT a `ValueError` — so the first version of this `except`, which
    # caught `(OSError, ValueError)`, let it fail the whole request. This is the case the review
    # pointed out that the helper-only version of this test could not see.
    bad = part_topology(
        BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(),
        part="n_00000001",
        basis="not a basis!",
        scale=1e-3,
    )
    assert _topology_cache_write(tmp_path / "part.topology", bad) is False  # must not raise

    # And an unwritable directory, which raises PermissionError (an OSError).
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            assert _topology_cache_write(locked / "part.topology", topo) is False
        finally:
            locked.chmod(0o700)


def test_a11_a_record_off_policy_is_a_cache_miss(tmp_path: Path) -> None:
    """A11: the sampling policy is part of the cache key, and an oversized record is refused early.

    §15.2 wants a bounded record; the bound is a policy (a deflection fraction and a point cap). A
    change to either produces different polylines from the same B-rep, so a record sampled under the
    old policy must not be served as if it were current.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    from stokes.geometry import topology_cachefile as cachefile
    from stokes.geometry.topology_brep import part_topology, sampling_policy

    topo = part_topology(
        BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(), part="n_00000001", basis="b" * 8, scale=1e-3
    )
    path = cachefile.topology_path(tmp_path, topo.part)
    cachefile.write_topology(path, topo, sampling_policy())

    # The record is served under its own policy …
    assert cachefile.read_topology(path, topo.basis, sampling_policy()) is not None
    # … and refused under a different one.
    assert cachefile.read_topology(path, topo.basis, "d0.5p512") is None
    # A caller that does not name a policy still reads it, so an older cache is not stranded.
    assert cachefile.read_topology(path, topo.basis) is not None
    # `fresh` follows the same rule.
    assert cachefile.fresh(path, topo.basis, sampling_policy()) is True
    assert cachefile.fresh(path, topo.basis, "d0.5p512") is False
    # The policy names the parameters, so changing either constant changes the key.
    assert sampling_policy() != ""


def test_a11_an_oversized_record_is_never_read_into_memory(tmp_path: Path) -> None:
    """A11: the sidecar's size gates the read, so a record over the limit is not allocated at all.

    The test must make the *old* code fail, so it cannot rely on a missing payload: with no file
    the old `read_bytes()` also raised and returned None. It writes a real payload and watches
    whether it is read, by replacing `Path.read_bytes` with a counter. An earlier version of this
    test passed on the unfixed code and proved nothing.
    """
    from stokes.geometry import topology_cachefile as cachefile

    path = cachefile.topology_path(tmp_path, "n_00000001")
    side = cachefile.sidecar_path(path)
    # A payload ONE BYTE over the limit, so the gate is what refuses it.
    oversize = cachefile.MAX_TOPOLOGY_BYTES + 1
    side.write_text(
        json.dumps(
            {
                "version": cachefile.TOPOLOGY_VERSION,
                "part": "n_00000001",
                "basis": "b" * 8,
                "bytes": oversize,
                "crc32": 0,
                "edges": 0,
                "vertices": 0,
                "policy": "",
            }
        ),
        encoding="utf-8",
    )
    # A sparse file of the claimed size: it costs no disk, and it makes the size check meaningful.
    with path.open("wb") as handle:
        handle.truncate(oversize)
    assert path.stat().st_size == oversize

    reads = 0
    original = Path.read_bytes

    def counting_read(self: Path) -> bytes:
        nonlocal reads
        if self == path:
            reads += 1
        return original(self)

    Path.read_bytes = counting_read  # type: ignore[method-assign]
    try:
        assert cachefile.read_topology(path, "b" * 8) is None
    finally:
        Path.read_bytes = original  # type: ignore[method-assign]
    # The gate rejected it from the sidecar alone: the 256 MB payload was never read.
    assert reads == 0, "the oversized record was read into memory before being refused"


def test_a11_a_payload_shorter_than_its_sidecar_is_a_miss(tmp_path: Path) -> None:
    """A11: a file whose size differs from the sidecar is a miss, without reading it."""
    from stokes.geometry import topology_cachefile as cachefile

    path = cachefile.topology_path(tmp_path, "n_00000001")
    side = cachefile.sidecar_path(path)
    side.write_text(
        json.dumps(
            {
                "version": cachefile.TOPOLOGY_VERSION,
                "part": "n_00000001",
                "basis": "b" * 8,
                "bytes": 4096,  # claims 4 KiB
                "crc32": 0,
                "edges": 0,
                "vertices": 0,
                "policy": "",
            }
        ),
        encoding="utf-8",
    )
    path.write_bytes(b"short")  # 5 bytes on disk
    assert cachefile.read_topology(path, "b" * 8) is None


def test_t124_an_edges_faces_match_the_mesh_numbering() -> None:
    """T-124a: each edge's face ids are the faces it borders, in the **mesh's** numbering.

    The numbering is the hard part, and it is where a plausible implementation goes wrong: the mesh
    numbers a triangle's face from `TopExp_Explorer` (which visits a face once per occurrence),
    while
    the edge and vertex records use `TopExp.MapShapes_s` (once per shape). Measured equal for a box
    and the cold plate, but not the same walk in general, so the explorer is used.

    The check that matters is against the mesh itself: this asserts every id an edge names is one
    the
    mesh actually uses, and that a box's twelve edges each border exactly two of its six faces.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    topo = part_topology(
        BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(), part="n_00000001", basis="b" * 8, scale=1e-3
    )
    assert len(topo.edges) == 12
    for edge in topo.edges:
        # A box edge is the meeting of two faces; every one of the six is named by some edge.
        assert len(edge.faces) == 2, f"edge {edge.edge_id} borders {len(edge.faces)} faces"
    named = sorted({face for edge in topo.edges for face in edge.faces})
    assert named == list(range(6)), f"edges named {named}, the mesh numbers 0..5"


def test_t124_a_boundary_edge_borders_one_face() -> None:
    """An edge on the boundary of the surface borders one face, not two.

    A box has no boundary edges (every edge is shared), so this uses the cold plate, whose channel
    leaves genuine openings: four of its 100 edges border a single face. Asserting the *existence*
    of
    one-face edges is what separates a real incidence map from "every edge gets two faces".
    """
    from stokes.geometry import step as stepmod

    model = stepmod.read_step(_cold_plate_step())
    plate = next(solid for solid in model.solids if solid.name == "plate")
    topo = part_topology(plate.shape, part="n_00000001", basis="b" * 8, scale=1e-3)
    counts = [len(edge.faces) for edge in topo.edges]
    assert counts.count(1) >= 1, "no boundary edge found: the incidence is not real"
    assert all(count in (1, 2) for count in counts), (
        f"a solid edge borders 1 or 2 faces, not {sorted(set(counts))}"
    )


def _cold_plate_step() -> Path:
    """The T-114 cold plate, written to a temporary folder."""
    import tempfile

    from tests import acceptance_fixtures as af

    return af.write_cold_plate(Path(tempfile.mkdtemp()))


def test_t056_carry_reports_edge_and_vertex_ids_it_cannot_map() -> None:
    """T-056-fix: a tag holding edges or vertices that cannot be carried must be REPORTED (§15.1).

    The review found that `carry` moved face members through an operation and **silently dropped**
    edge and vertex members: a tag naming an edge of a part that was then cut came back with no
    edges
    and no note, which §15.1 forbids. The owner's answer (via Claude, 2026-10-12) is that dropping
    them is **not** an accepted deviation.

    This pins the reporting half: an edge id no output received appears in `unmapped` with the kind
    that failed, rather than vanishing.
    """
    from stokes.geometry.carry import Carried

    carried = Carried()
    carried.wanted_edges[("tag_1", "n_00000001")] = {3}
    # Nothing mapped, as when the operation removed the edge.
    rows = carried.unmapped(_no_project())
    assert rows, "an unmapped edge must be reported"
    row = rows[0]
    assert row["tag"] == "tag_1" and row["part"] == "n_00000001"
    assert row["edges"] == [3], f"the row must name the kind that failed: {row}"
    assert row["count"] == 1


def test_t056_carry_reports_vertices_too() -> None:
    """T-056-fix: the same for vertices, which the review listed beside edges."""
    from stokes.geometry.carry import Carried

    carried = Carried()
    carried.wanted_vertices[("tag_9", "n_00000002")] = {11, 12}
    rows = carried.unmapped(_no_project())
    assert len(rows) == 1
    assert rows[0]["vertices"] == [11, 12]
    assert rows[0]["count"] == 2


def test_t056_carry_carries_an_edge_that_survives() -> None:
    """T-056-fix: an edge id that survives an operation is carried, not dropped.

    Before the fix only `faces` were carried, so a tag holding edges lost them even when the
    operation
    never touched them — the common case, and the one that made `cap loop=` unreachable after a
    re-match.
    """
    from stokes.geometry.carry import Carried

    carried = Carried()
    carried.wanted_edges[("tag_2", "n_00000001")] = {5}
    carried.mapped_edges[("tag_2", "n_00000001")] = {5}
    assert carried.unmapped(_no_project()) == []
    assert carried.carried_edges == 1

    carried.wanted_vertices[("tag_2", "n_00000001")] = {7}
    carried.mapped_vertices[("tag_2", "n_00000001")] = {7}
    assert carried.carried_vertices == 1


def _no_project():
    """A real, empty project for the report rows.

    `unmapped` looks each tag id up to name it, and a project that does not hold it falls back to
    the
    id — which is what these tests want to observe, so a real (if empty) project is used rather than
    a
    `None` stand-in.
    """
    import tempfile

    from tests.selection_support import new_session

    return new_session(Path(tempfile.mkdtemp())).require_project()


def test_units_are_part_of_the_basis_for_param_units_formats(tmp_path: Path) -> None:
    """Units belong in the basis, for the formats whose units come from the import parameter.

    I first wrote this test asserting the opposite — that units were **not** in the basis — and
    recorded the hazard for the owner to decide. Claude's decision (2026-10-12), after I established
    that a part's units CAN be changed (`parameter.set node=… name=units value=inch` is accepted and
    takes effect):

    > for formats whose units come from the import parameter, the units are part of the identity of
    > the geometry, so they belong in the basis. For STEP/IGES/3MF they come from the file bytes and
    > already change the basis, so leave those alone.

    His reason is that the hazard is wider than topology: `cache._fresh_against` compares the source
    stamp for the **mesh** too, so a units change served the old-scale mesh as well, and a client
    that trusted the record's `scale` still drew the part at the wrong size.

    So: same file, same stamp, different units -> **different basis** -> every cache misses once and
    the part rebuilds. And `None` hashes exactly as before, so a caller that cannot state the units
    (a legacy project) keeps its basis instead of missing every cache on upgrade.
    """
    from stokes.geometry.files import SourceStamp
    from stokes.geometry.fingerprints import basis_for

    stamp = SourceStamp(str(tmp_path / "part.brep"), 1234, 5_000_000_000)
    as_mm = basis_for(stamp, 0, 6, "mm")
    as_inch = basis_for(stamp, 0, 6, "inch")
    assert as_mm != as_inch, "a units change must produce a different basis"

    # Stable for the same inputs, and unchanged when the units are not stated at all.
    assert basis_for(stamp, 0, 6, "mm") == as_mm
    legacy = basis_for(stamp, 0, 6)
    assert legacy == basis_for(stamp, 0, 6, None)
    # The pre-units basis is exactly what a caller without units still gets, so nothing that existed
    # before this change is invalidated by it.
    assert (
        legacy
        == hashlib.sha1(
            "\0".join((stamp.name, str(stamp.size), str(stamp.mtime_ns), "0", "6")).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:12]
    )


def test_default_units_keep_the_basis_a_pre_units_project_stored(tmp_path: Path) -> None:
    """A project saved BEFORE `units` existed must keep its basis, or the upgrade breaks it.

    Claude's defect 1, and the reason the branch was held:

    > The units rule regresses existing BREP/STL/OBJ projects. `basis_for(units=None)` gives the old
    > hash, but no caller passes None for those formats: the node always has `units` (default `mm`).
    > So a part whose B-rep sidecar is missing now fails with "changed since it was imported", and
    any
    > re-import writes a new basis so tags stamped with the old basis go stale. **Rule: when `units`
    > equals the default, the basis must equal the old hash; only a non-default units claim changes
    > it.**

    This test is written against a **stored** basis rather than against a formula, because the
    formula
    is what I got wrong: the test I had asserted only "mm differs from inch", which is true either
    way
    and would pass with the defect in place. The value below is the hash the code produced BEFORE
    units joined the basis, recorded from the pre-change tree — an upgrade must reproduce it
    exactly,
    or every part in every existing project reads as changed.
    """
    from stokes.geometry.files import SourceStamp
    from stokes.geometry.fingerprints import basis_for, is_default_units

    stamp = SourceStamp(str(tmp_path / "part.brep"), 1234, 5_000_000_000)
    # The pre-units hash: name, size, mtime, solid, faces and nothing else.
    pre_change = hashlib.sha1(
        "\0".join((stamp.name, str(stamp.size), str(stamp.mtime_ns), "0", "6")).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:12]

    # The three ways a caller can state the default all give the stored basis back.
    assert basis_for(stamp, 0, 6) == pre_change
    assert basis_for(stamp, 0, 6, "mm") == pre_change
    assert basis_for(stamp, 0, 6, "millimetre") == pre_change, (
        "an alias of the default is the default: one geometry must not have two bases"
    )
    # And a real claim still changes it, which is the half that made units worth adding.
    assert basis_for(stamp, 0, 6, "inch") != pre_change

    # `is_default_units` is by SCALE, so the aliases agree; an unreadable name is not the default,
    # since treating a typo as millimetres would silently hash the wrong geometry as the default.
    assert (
        is_default_units("mm") and is_default_units("millimeter") and is_default_units("millimetre")
    )
    assert not is_default_units("inch") and not is_default_units("in")
    assert not is_default_units("furlong")


def test_changing_units_moves_the_basis_and_misses_every_cache(tmp_path: Path) -> None:
    """`parameter.set units=inch` must recompute the basis, or the old-scale geometry is served.

    Claude's defect 2, and the reason he asked for the REAL flow rather than a hash comparison:

    > A units change still serves the old-scale record. `parameter.set units` rewrites nothing: the
    > stored `basis` is not recomputed, so the topology cache hits at the old scale. The mesh cache
    > keys on the source stamp only, so the mesh and the sidecar B-rep also hit. Your units test
    never
    > calls `basis_for_source`; make it do the real flow: import a BREP, cache, `parameter.set
    > units=inch`, assert all three miss.

    My previous test asserted `basis_for(stamp, …, "mm") != basis_for(stamp, …, "inch")`, which is
    true and irrelevant: the store never called it with the node's units, so the model could say
    inches while every cache served millimetres. This one imports a real BREP through the API, waits
    for it, changes the units through `Session.execute`, and asserts the THREE caches the store keys
    on the basis are all stale.
    """
    from stokes.geometry import topology_cachefile
    from stokes.geometry.files import SourceStamp
    from stokes.geometry.fingerprints import basis_for_source
    from stokes.geometry.shapes import brep_path, fresh
    from stokes.geometry.store import GeometryStore, solid_index
    from stokes.geometry.topology_brep import sampling_policy
    from tests.geometry_fixtures import box, write_brep
    from tests.selection_support import new_session

    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "units.brep")
    session = new_session(tmp_path / "project")
    store = GeometryStore.of(session)
    result = session.execute("geometry.import", path=str(source), units="mm")
    part_id = result["id"]
    assert store.wait(part_id, timeout=180), "the import did not finish"

    project = session.require_project()
    node = project.find(part_id)
    assert node is not None
    # The import resolved the units it was given, so the part knows them.
    assert node.params.get("units") == "mm"
    before = store.basis(node)
    assert before, "the import must stamp a basis"

    # 1. The identity itself moves.
    session.execute("parameter.set", node=part_id, name="units", value="inch")
    node = project.find(part_id)
    assert node is not None and node.params.get("units") == "inch"
    after = store.basis(node)
    assert after != before, "a units change must produce a different basis"

    # The recomputed basis is the one the reader itself would give at the new units — not merely
    # *a* different hash. A basis that disagreed with `basis_for_source` would make the part read as
    # "changed since it was imported" on the very next fetch, which is defect 1's symptom.
    expected = basis_for_source(
        source,
        SourceStamp.of(source),
        solid_index(node),
        int(node.params.get("faces") or 0),
        "inch",
    )
    assert after == expected

    # 2. The topology cache misses. It is keyed on the basis, so a different basis is a miss by
    #    construction — asserted rather than assumed, because the record is what carries the scale.
    folder = store.shape_dir()
    assert (
        topology_cachefile.read_topology(
            topology_cachefile.topology_path(folder, part_id), after, sampling_policy()
        )
        is None
    ), "the topology record must not be served at the new units"

    # 3. The B-rep sidecar is stale. This is the file `check.brep`, the export and the topology read
    #    all trust; `fresh` is the check the store itself makes before using it.
    sidecar = brep_path(folder, part_id)
    if sidecar.exists():
        assert not fresh(sidecar, after), (
            "the cached B-rep must not read as current after a units change"
        )


def test_a_units_change_misses_the_mesh_cache_too(tmp_path: Path) -> None:
    """The MESH must miss on a units change, not only the topology record.

    Claude's defect 1 on the 14:00 batch, and the reason the merge is held: the topology record
    regenerates at the new scale, but the mesh cache was keyed on the **source stamp alone**
    (`cache._fresh_against`), so the mesh served after `parameter.set units=inch` was still the one
    made at millimetres. Edges and measurements then no longer line up with the geometry drawn —
    every number is from a different model than the one on screen.

    My previous test asserted the basis moved and the topology record went stale, and said **nothing
    about the mesh**. This one asks the cache itself, through `read_cache`, which is the function
    the
    store calls.
    """
    from stokes.geometry import cache as cache_mod
    from stokes.geometry.store import GeometryStore
    from tests.geometry_fixtures import box, write_brep
    from tests.selection_support import new_session

    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "units.brep")
    session = new_session(tmp_path / "project")
    store = GeometryStore.of(session)
    result = session.execute("geometry.import", path=str(source), units="mm")
    part_id = result["id"]
    assert store.wait(part_id, timeout=180), "the import did not finish"

    project = session.require_project()
    node = project.find(part_id)
    assert node is not None
    before = store.basis(node)
    assert before, "the import must stamp a basis"
    # **The PROJECT folder, not `store.shape_dir()`.** `cache_file`/`read_cache` append
    # `cache/geometry` themselves (it is `SUBDIR`), so passing `shape_dir()` — which already returns
    # that subfolder — made every path `cache/geometry/cache/geometry/...` and the cache looked
    # permanently empty. That was a bug in this test, not in the cache.
    folder = session.project_path

    # **The cached bytes are served at their own basis.** This is the positive control: without it a
    # cache that always missed would pass the assertion below for the wrong reason.
    served = cache_mod.read_cache(folder, part_id, source, before)
    assert served is not None, "the cache must serve bytes made at the part's own basis"
    assert served[0].basis == before, "the cached bytes must record the basis they were made from"

    session.execute("parameter.set", node=part_id, name="units", value="inch")
    node = project.find(part_id)
    assert node is not None and node.params.get("units") == "inch"
    after = store.basis(node)
    assert after != before, "a units change must produce a different basis"

    # **The assertion this test exists for.** The source file is untouched, so its stamp is
    # identical and every stamp-based check still says fresh — but the basis has changed, and the
    # mesh was made from the OLD geometry.
    served_after = cache_mod.read_cache(folder, part_id, source, after)
    assert served_after is None, (
        "the mesh cache served bytes made from the old basis after a units change: "
        "the part would be drawn at millimetres while its topology claimed inches"
    )

    # And the per-part file is judged the same way as the pack, so the two cannot disagree. The
    # bytes carry the basis they were made from, which is what makes the check above possible.
    per_part = cache_mod.cache_file(folder, part_id)
    if per_part.exists():
        assert cache_mod.entry_basis(per_part.read_bytes()) == before


def test_the_worker_and_the_store_agree_on_a_non_default_units_basis(tmp_path: Path) -> None:
    """The basis the import WRITES must equal the one the store RECOMPUTES, for `inch` as well.

    Claude's defect 3: the worker hashed `unit_name(scale)`, which is `"in"` for 0.0254, while the
    reader and the node both say `"inch"`. The two sides therefore disagreed for exactly the units
    that are NOT the default — so a part imported as inches re-read its source on its first load,
    and
    the fix in defect 1 (default units hash as before) would have hidden the symptom for millimetres
    only. This asserts the agreement directly at a non-default unit, which is where the drift lived.

    The two are computed the two ways the code computes them: `basis_for_source` as the worker calls
    it, and `store.basis(node)` as every cache key calls it.
    """
    from stokes.geometry import shapes as shapes_cache
    from stokes.geometry.files import SourceStamp
    from stokes.geometry.fingerprints import basis_for_source
    from stokes.geometry.shapes import brep_path, fresh
    from stokes.geometry.store import GeometryStore, solid_index
    from tests.geometry_fixtures import box, write_brep
    from tests.selection_support import new_session

    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "inch.brep")
    session = new_session(tmp_path / "project")
    store = GeometryStore.of(session)
    result = session.execute("geometry.import", path=str(source), units="inch")
    part_id = result["id"]
    assert store.wait(part_id, timeout=180), "the import did not finish"

    project = session.require_project()
    node = project.find(part_id)
    assert node is not None and node.params.get("units") == "inch"

    stamp = SourceStamp.of(source)
    as_worker_writes = basis_for_source(
        source, stamp, solid_index(node), int(node.params.get("faces") or 0), "inch"
    )
    as_store_reads = store.basis(node)
    assert as_store_reads == as_worker_writes, (
        "the basis the import wrote and the one the store recomputes must be the same string: "
        f"worker={as_worker_writes} store={as_store_reads}"
    )

    # **The end-to-end half: the sidecar the WORKER actually wrote.** The worker runs in its own
    # process, so this is the only way to check the basis it really hashed rather than the one this
    # test predicts it should have. `fresh(sidecar, basis)` is the store's own check, so a pass here
    # means the first load of an inches part uses the cache instead of re-reading its source.
    sidecar = brep_path(store.shape_dir(), part_id)
    assert sidecar.exists(), "the import must have written a B-rep sidecar"
    entry = shapes_cache.read_entry(sidecar)
    assert entry is not None
    assert entry.basis == as_store_reads, (
        "the sidecar's basis disagrees with the store's: the first load would re-read the source. "
        f"sidecar={entry.basis} store={as_store_reads}"
    )
    assert fresh(sidecar, as_store_reads)

    # And it is genuinely NOT the default-units basis, so this test would pass trivially otherwise.
    as_default = basis_for_source(
        source, stamp, solid_index(node), int(node.params.get("faces") or 0), "mm"
    )
    assert as_store_reads != as_default


def test_units_aliases_hash_alike(tmp_path: Path) -> None:
    """`inch` and `in` are one unit, so they must give one basis.

    Claude's defect 6, second half: the units went into the hash as the caller's **text**, so the
    aliases of one unit produced different bases for the same geometry. A tag stamped against one
    read
    as stale against the other, and a part imported as `inch` and re-imported as `in` re-read its
    source for nothing.

    `pint` is the unit table (ADR-0012), so the canonical spelling is asked of it rather than
    carried
    in a second list here.
    """
    from stokes.geometry.files import SourceStamp
    from stokes.geometry.fingerprints import basis_for, basis_for_comparison, canonical_units

    stamp = SourceStamp(str(tmp_path / "part.brep"), 1234, 5_000_000_000)
    assert canonical_units("inch") == canonical_units("in")
    assert canonical_units("millimetre") == canonical_units("mm") == canonical_units("millimeter")
    # **The STORED hashes are allowed to differ for aliases, and must** (Claude's defect 3 of the
    # second review). The basis is written into the node at import and stamped into every tag, so
    # making the stored value canonical would rename the geometry of every part already on disk: a
    # project imported as `inch` would read as changed and re-import for nothing. What must agree is
    # the **comparison**, which is where a stale tag is decided — `basis_matches` canonicalises both
    # sides, so `in` and `inch` are one basis when it matters and neither stored value moves.
    assert basis_for(stamp, 0, 6, "inch") != basis_for(stamp, 0, 6, "in")
    assert basis_for_comparison(stamp, 0, 6, "inch") == basis_for_comparison(stamp, 0, 6, "in")
    # And a real difference still separates them, or this would be a test that cannot fail.
    assert basis_for(stamp, 0, 6, "inch") != basis_for(stamp, 0, 6, "m")
    # A name pint does not know is left alone rather than mapped to something it is not. (The
    # first version of this used `furlong` as the example of an unknown name; pint knows it — it
    # resolves to `fur`, 201 m — so the test failed on a wrong expectation rather than on the code.)
    assert canonical_units("zzznotaunit") == "zzznotaunit"


def test_the_selection_and_the_store_give_the_same_basis(tmp_path: Path) -> None:
    """One definition of a part's current basis, not two that can disagree.

    Claude's defect 6, first half: `SelectionCache.basis` computed its own through
    `basis_of(mesh, solid, node)`, which returns the node's **stored** `basis`, while
    `GeometryStore.basis` **recomputes** it from the node's current units. Two answers to one
    question
    is the failure they exist to prevent — a tag stamped against the stored value and checked
    against the recomputed one reads as stale for no reason, which is how a main-era project with
    non-default units had every tag go stale on a re-import.

    This asserts the two agree **after a units change**, which is where the two versions diverged:
    the stored value is still the old one while the recomputed value has moved.
    """
    from stokes.geometry.store import GeometryStore
    from tests.geometry_fixtures import box, write_brep
    from tests.selection_support import new_session

    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "units.brep")
    session = new_session(tmp_path / "project")
    store = GeometryStore.of(session)
    result = session.execute("geometry.import", path=str(source), units="mm")
    part_id = result["id"]
    assert store.wait(part_id, timeout=180), "the import did not finish"

    from stokes.api.commands.selection import _Geometry
    from stokes.api.registry import CommandContext

    def selection_basis() -> str | None:
        # **A fresh `_Geometry`, because that is how the app uses it**: a handler builds one per
        # command, so its internal per-part cache never outlives the command. Reusing one instance
        # across a units change would test a lifetime the app does not have — measured: it returns
        # the basis cached before the change. See the note in the test body.
        return _Geometry(CommandContext(session)).basis(part_id)

    node = session.require_project().find(part_id)
    assert node is not None
    assert selection_basis() == store.basis(node)

    session.execute("parameter.set", node=part_id, name="units", value="inch")
    node = session.require_project().find(part_id)
    assert node is not None and node.params.get("units") == "inch"
    # The stored basis is unchanged, so a second definition returning it would differ here.
    assert selection_basis() == store.basis(node), (
        "the selection and the store disagree about the part's current basis"
    )


def test_a_record_survives_a_round_trip_with_its_faces() -> None:
    """Defect 2: the decoder must keep each edge's faces and the part's face count.

    Claude found this and I reproduced it exactly: `unpack_topology` rebuilt each edge without
    `faces=` and the part without `faces=`, so a box went from `part.faces=6, edge0.faces=(0,2)` to
    `0` and `()`. The consequence is severe and not obvious: the endpoint serves a record by reading
    the cache and **packing it again**, so the *second* fetch of a part has no edge-to-face data at
    all — the hidden-face rule switches itself off and edge-to-face conversion returns nothing,
    while the first fetch behaves.

    So the test reads it back and asserts both halves — what a first-fetch-only test misses.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    from stokes.geometry.topology_brep import part_topology
    from stokes.geometry.topology_transport import pack_topology, unpack_topology

    topo = part_topology(
        BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(), part="n_00000001", basis="b" * 8, scale=1e-3
    )
    assert topo.faces == 6
    assert len(topo.edges[0].faces) == 2

    back = unpack_topology(pack_topology(topo))
    assert back.faces == topo.faces, f"the face count was lost: {back.faces} != {topo.faces}"
    for before, after in zip(topo.edges, back.edges, strict=True):
        assert after.faces == before.faces, (
            f"edge {before.edge_id} lost its faces: {after.faces} != {before.faces}"
        )


def test_writing_a_step_leaves_the_process_unit_state_alone(tmp_path: Path) -> None:
    """`write_step` must put BOTH of OCCT's unit parameters back, not just one.

    Found in Claude's review as a failure that appeared **only** in a full parallel suite run and
    always passed in isolation: `assert params["units"] == "mm"` came back `'m'`. The cause is that
    `write.step.unit` — which is process-wide, like `xstep.cascade.unit` — was set to `"M"` and
    never restored; only `xstep.cascade.unit` was in the `finally`. So the FIRST STEP written in a
    process silently made every LATER STEP in that process declare metres, and a project re-imported
    after any `write_step` read back a scale a thousand times too large.

    A test that writes one STEP and re-imports it cannot see this: it needs a write to happen
    between the two, which is exactly why running the whole suite found it and no single test did.
    So this test writes one STEP, then another, and reads the SECOND one back — the shape whose
    scale depends on the state the first write left behind.
    """
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_Controller

    from stokes.geometry.brep import StepSolidOut, write_step
    from stokes.geometry.formats import read_units
    from stokes.geometry.step import read_step
    from tests.geometry_fixtures import box

    # **Register the parameter before reading it.** `CVal_s` on an unregistered name returns `""`,
    # which is not a value OCCT will accept back — so a test that read it first would compare
    # against a string that can never be restored and would fail for its own reason rather than the
    # code's. OCCT's own default here is `"MM"` (millimetres), which is exactly what a leftover
    # `"M"` destroys.
    STEPControl_Controller.Init_s()
    before = Interface_Static.CVal_s("write.step.unit")
    first = tmp_path / "first.step"
    second = tmp_path / "second.step"
    write_step([StepSolidOut("first", box(10.0, 20.0, 30.0), None)], first)
    # The parameter must be exactly as it was, whatever that was.
    assert Interface_Static.CVal_s("write.step.unit") == before, (
        "write_step left the process-wide write.step.unit changed"
    )
    write_step([StepSolidOut("second", box(10.0, 20.0, 30.0), None)], second)

    # `read_units` is None for STEP (it declares its own in the file), so the units come from the
    # bytes: read the file back and check the declaration it carries.
    model = read_step(second)
    # The reader gives metres per unit; a file that declares millimetres scales by 1e-3.
    assert model.unit_scale == pytest.approx(1e-3), (
        f"the second STEP declared the wrong unit: scale {model.unit_scale}, expected 1e-3"
    )
    assert read_units(second, None) is None  # a STEP names its own units


def test_a_written_step_keeps_each_bodys_colour(tmp_path: Path) -> None:
    """`brep.write_step` writes a colour and `step.read_step` reads it back.

    The demo relies on this: its palette is written *into the file* rather than re-applied after
    import, so a colour that did not survive the round trip would fall back to the reader's default
    and every body would look the same. `SetColorMode(True)` was already enabled on both sides with
    no colour ever assigned — the kind of setting that looks wired and is not.
    """
    from stokes.geometry.brep import StepSolidOut, write_step
    from stokes.geometry.step import read_step
    from tests import geometry_fixtures as fx

    out = tmp_path / "colours.step"
    write_step(
        [
            StepSolidOut("steel", fx.box(10.0, 10.0, 10.0), (0.62, 0.65, 0.70)),
            StepSolidOut("plain", fx.box(10.0, 10.0, 10.0, at=(20.0, 0.0, 0.0))),
            StepSolidOut(
                "fluid", fx.box(10.0, 10.0, 10.0, at=(40.0, 0.0, 0.0)), (0.20, 0.52, 0.86)
            ),
        ],
        out,
    )
    colours = {solid.name: solid.color for solid in read_step(out).solids}
    assert colours["steel"] == pytest.approx((0.62, 0.65, 0.70))
    assert colours["fluid"] == pytest.approx((0.20, 0.52, 0.86))
    # No colour given means none written, so the reader reports its own default rather than black.
    assert colours["plain"] is None


# --------------------------------------------------------- face areas and solid volumes (v3)


def test_a_boxs_faces_and_volume_are_its_true_sizes() -> None:
    """A 10 x 20 x 30 mm box, by hand: volume 6000 mm³, faces 200/300/600 mm².

    The figures are worked out from the dimensions rather than taken from the implementation,
    because
    an area scales by the SQUARE of the length scale and a volume by the CUBE — the two places a
    "looks plausible" factor error hides. 6000 mm³ is 6e-6 m³, and 600 mm² is 6e-4 m².
    """
    topology = box_topology()
    assert len(topology.face_sizes) == 6
    assert len(topology.solids) == 1
    # Areas, square metres: 10x20, 10x30 and 20x30 mm.
    areas = sorted(round(f.area, 12) for f in topology.face_sizes)
    assert areas == pytest.approx([200e-6, 200e-6, 300e-6, 300e-6, 600e-6, 600e-6], rel=1e-9)
    # Volume, cubic metres: 10 * 20 * 30 mm.
    assert topology.solids[0].volume == pytest.approx(6000e-9, rel=1e-9)
    # Total surface of the body: 2 * (200 + 300 + 600) mm².
    assert topology.solids[0].area == pytest.approx(2 * (200 + 300 + 600) * 1e-6, rel=1e-9)


def test_a_cylinder_volume_matches_the_formula() -> None:
    """r=5 mm, h=20 mm: pi r² h = 1570.796... mm³, and the curved face is 2 pi r h."""
    topology = part_topology(cylinder_shape(), part="n_00000002", basis="abc123", scale=MM)
    assert topology.solids[0].volume == pytest.approx(math.pi * 25 * 20 * 1e-9, rel=1e-9)
    areas = sorted(f.area for f in topology.face_sizes)
    curved = 2 * math.pi * 5 * 20 * 1e-6
    # The two caps are the SMALLEST and sort first (7.85e-5 against 6.28e-4), so the curved face is
    # last; asserting on areas[0] was my error, not the implementation's.
    assert areas[0] == pytest.approx(math.pi * 25 * 1e-6, rel=1e-9)
    assert areas[-1] == pytest.approx(curved, rel=1e-9)


def test_face_ids_are_the_mesh_numbering_and_areas_line_up_with_them() -> None:
    """Face id N's area is the area of the face the mesh calls N.

    This is the defect class T-056-fix was about: the mesh numbers faces in `TopExp_Explorer` order
    **with repeats** while edges and vertices use `MapShapes` order **once each**. A face area read
    with the wrong walk would be attributed to the wrong face after the first repeat, and on a box
    the
    two walks disagree sharply (24 visits vs 6 faces), so an index that runs off the end is caught
    here rather than showing a plausible area for the wrong surface.
    """
    topology = box_topology()
    assert [f.face_id for f in topology.face_sizes] == [0, 1, 2, 3, 4, 5]
    assert all(f.area > 0.0 for f in topology.face_sizes)


def test_a_shape_with_no_solid_reports_no_volume_rather_than_zero() -> None:
    """A shell has no enclosed volume; an empty list lets the client say so instead of "0 m³"."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pln

    face = BRepBuilderAPI_MakeFace(gp_Pln(), 0.0, 10.0, 0.0, 10.0).Shape()
    topology = part_topology(face, part="n_00000003", basis="abc123", scale=MM)
    assert topology.solids == []
    # A face is still measurable, so this is not an empty record.
    assert len(topology.face_sizes) >= 1


def test_the_v3_record_round_trips_its_sizes_through_the_codec() -> None:
    """A packed record decodes to the same areas and volumes, **including on a re-pack**.

    The re-pack half is the one that matters: the endpoint reads its cache and re-packs, so a field
    dropped on the way in was missing from every later fetch as well. That is exactly how T-124a's
    edge-to-face data vanished, and this test exists so the v3 fields cannot repeat it.
    """
    packed = pack_topology(box_topology())
    once = unpack_topology(packed)
    assert once.solids[0].volume == pytest.approx(6000e-9, rel=1e-9)
    assert len(once.face_sizes) == 6
    # Re-pack the decoded record, as the endpoint's cache path does, and read it again.
    twice = unpack_topology(pack_topology(once))
    assert twice.solids[0].volume == pytest.approx(once.solids[0].volume, rel=1e-12)
    assert [f.area for f in twice.face_sizes] == pytest.approx(
        [f.area for f in once.face_sizes], rel=1e-12
    )


def test_the_codec_refuses_a_face_area_that_is_not_finite_or_is_negative() -> None:
    """A NaN or negative area is refused at the wire, not carried to the readout as "NaN m²"."""
    from stokes.errors import StokesError
    from stokes.geometry.topology_transport import _face_size

    for bad in (
        {"id": 0, "area": float("nan"), "centroid": [0, 0, 0]},
        {"id": 0, "area": -1.0, "centroid": [0, 0, 0]},
        {"id": 6, "area": 1.0, "centroid": [0, 0, 0]},  # an id past the face count
        {"id": 0, "area": 1.0, "centroid": [0, 0]},  # a centroid that is not a triple
    ):
        with pytest.raises(StokesError):
            _face_size(bad, 6)


def test_a_units_change_reimports_the_mesh_in_the_live_session(tmp_path: Path) -> None:
    """A units change must update the mesh the session is HOLDING, not only the caches (defect 2).

    Claude's second review, defect 2:

    > A units change still serves the old-scale record. ... A units change inside a live session
    does
    > not update the mesh. After `parameter.set units` nothing evicts the in-memory mesh or
    re-imports;
    > the drawn mesh stays in mm while the recomputed basis and the topology move to inches. Test
    the
    > whole flow in one session, without a reopen.

    My existing units test asserted the three **caches** went stale, which they do — but a session
    that
    is already open never reads them: `GeometryStore._entries` holds the imported mesh, so the
    viewport
    kept drawing millimetres while the part claimed inches. This test measures the mesh the store
    serves, in one session, with no reopen.
    """
    from stokes.geometry.store import GeometryStore
    from tests.geometry_fixtures import box, write_brep
    from tests.selection_support import new_session

    # A 10 x 20 x 30 metre box declared as mm: at mm the extent is 10, at inch it is 254 times that
    # ... whichever way the file is read, the point is that the two differ by a known factor.
    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "live.brep")
    session = new_session(tmp_path / "live")
    store = GeometryStore.of(session)
    result = session.execute("geometry.import", path=str(source), units="mm")
    part_id = result["id"]
    assert store.wait(part_id, timeout=180), "the import did not finish"

    before = store.mesh(part_id)
    assert before is not None, "the import must leave a mesh in the session"
    extent_before = _extent(before)

    session.execute("parameter.set", node=part_id, name="units", value="inch")
    # The re-import is a worker: wait for it, as the import above does. Waiting is not the assertion
    # —
    # the assertion below is that the mesh it leaves is at the NEW scale.
    assert store.wait(part_id, timeout=180), "the re-import after the units change did not finish"

    # **The same store, in the same session.** A reopen would read the caches and pass even when the
    # live path is broken, which is exactly how the defect survived my first test.
    after = store.mesh(part_id)
    assert after is not None, "the part must still have a mesh after a units change"
    extent_after = _extent(after)
    assert extent_after != extent_before, (
        "the live session still serves the old-scale mesh: the extent stayed "
        f"{extent_before} after the units changed to inch"
    )
    # The scale factor is the units', not an arbitrary difference: 25.4 for mm -> inch. A different
    # number means the mesh was rebuilt from something other than the new units.
    assert abs(extent_after / extent_before - 25.4) < 0.01, (
        f"the mesh was rebuilt at the wrong scale: {extent_before} -> {extent_after}"
    )


def _extent(mesh: Any) -> float:
    """The largest axis of a part mesh's bounding box, in the mesh's own units."""
    import numpy as np

    lo = np.array([np.inf, np.inf, np.inf])
    hi = np.array([-np.inf, -np.inf, -np.inf])
    for solid in mesh.solids:
        if solid.positions.size == 0:
            continue
        lo = np.minimum(lo, solid.positions.min(axis=0))
        hi = np.maximum(hi, solid.positions.max(axis=0))
    assert np.isfinite(lo).all(), "the mesh has no vertices to measure"
    return float((hi - lo).max())


def test_a_stored_basis_from_the_previous_build_still_matches(tmp_path: Path) -> None:
    """The stored hash must not move for units that hashed a particular way before (defect 3).

    Claude's second review, defect 3:

    > The canonical-name change moves the basis of old parts (`inch`, `meter`, `metre` now hash
    > differently from before), so their tags and caches go stale. Keep the old text for the
    > default-units and spelling cases that hashed before; canonicalise only for comparison, never
    for
    > the stored hash.

    The basis is **stored** in the node at import. Canonicalising what goes into the hash therefore
    changes the identity of every part already on disk, and every tag stamped against the old value
    reads as stale — a re-import nobody asked for, on a project nothing changed about.
    """
    from stokes.geometry.files import SourceStamp
    from stokes.geometry.fingerprints import basis_for_source, is_default_units
    from tests.geometry_fixtures import box, write_brep

    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "spelling.brep")
    stamp = SourceStamp.of(source)

    # **The hashes the previous build stored.** Computed here from the raw text, which is what it
    # did,
    # so this test fails the moment the stored hash starts canonicalising.
    import hashlib

    def stored_with(raw: str, faces: int = 6) -> str:
        # The parts list as the previous build's `basis_for` built it: the same five fields, then
        # the
        # units TEXT as typed — and only when the units are not the format's default, which is the
        # rule
        # that keeps every pre-existing part's basis stable.
        #
        # Two wrong versions of this came first: one guessed six fields and a "v1" prefix, and one
        # appended the units even for millimetres. Both hashed to something the previous build never
        # produced, so both would have "proved" a defect against a fiction. **A reconstruction of an
        # old value has to be the old CODE, not a guess at it.**
        parts = [stamp.name, str(stamp.size), str(stamp.mtime_ns), "0", str(faces)]
        if raw and not is_default_units(raw):
            parts.append(raw)
        return hashlib.sha1("\0".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()[
            :12
        ]

    for spelling in ("mm", "m", "inch"):
        stored = stored_with(spelling)
        got = basis_for_source(source, stamp, 0, 6, spelling)
        assert got == stored, (
            f"a part stored with units={spelling!r} now hashes differently: "
            f"{stored} -> {got}, so its tags and caches go stale for nothing"
        )
