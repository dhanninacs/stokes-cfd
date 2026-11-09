"""The desktop reader of the surface artifact and its two payloads (T-210).

The artifact is built by the agent's own writer (`tests/mesh_surface_support.py`), so these tests
read the real format. The properties worth naming: a buffer is **validated before it is sliced**
(a short buffer, a wild offset, a wrong magic or an index outside the patch are refused with
`MeshFormatError`, never an `IndexError` or a read of someone else's memory), a patch leaves as the
**existing transport version 2** with every face id 0, and the cell edges round-trip through their
own codec.
"""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest

from stokes.geometry.errors import MeshFormatError
from stokes.geometry.transport import unpack
from stokes.mesh import surfaces
from tests.mesh_surface_support import QUAD_POINTS, TRIANGLE_POINTS, artifact


def _rebuild(header: dict, body: bytes) -> bytes:
    """An artifact with ``header`` in place of the real one, the data section unchanged."""
    text = json.dumps(header).encode()
    text += b" " * ((-len(text)) % 4)
    return surfaces.MAGIC + struct.pack("<I", len(text)) + text + body


def test_read_header_accepts_the_agents_artifact() -> None:
    data, written = artifact()
    header = surfaces.read_header(data)
    assert [p["name"] for p in header["patches"]] == ["inlet", "unassigned"]
    assert header["patches"] == written["patches"]
    assert header["data_start"] == 12 + int.from_bytes(data[8:12], "little")
    assert surfaces.read_header(memoryview(data))["version"] == 1


def test_a_buffer_truncated_by_one_byte_is_refused() -> None:
    data, _ = artifact()
    with pytest.raises(MeshFormatError):
        surfaces.read_header(data[:-1])


def test_an_offset_past_the_end_is_refused() -> None:
    data, written = artifact()
    start = 12 + int.from_bytes(data[8:12], "little")
    wild = json.loads(json.dumps(written))
    wild["patches"][1]["offset"] = len(data)
    with pytest.raises(MeshFormatError):
        surfaces.read_header(_rebuild(wild, data[start:]))


def test_a_wrong_magic_is_refused() -> None:
    data, _ = artifact()
    with pytest.raises(MeshFormatError):
        surfaces.read_header(b"STKMSH2\0" + data[8:])
    with pytest.raises(MeshFormatError):
        surfaces.read_header(b"")


def test_an_unknown_version_a_huge_header_and_a_bad_count_are_refused() -> None:
    data, written = artifact()
    start = 12 + int.from_bytes(data[8:12], "little")
    body = data[start:]
    with pytest.raises(MeshFormatError):
        surfaces.read_header(_rebuild({**written, "version": 2}, body))
    with pytest.raises(MeshFormatError):
        surfaces.read_header(surfaces.MAGIC + struct.pack("<I", 17 * 1024 * 1024) + b" " * 64)
    negative = json.loads(json.dumps(written))
    negative["patches"][0]["points"] = -1
    with pytest.raises(MeshFormatError):
        surfaces.read_header(_rebuild(negative, body))
    huge = json.loads(json.dumps(written))
    huge["patches"][0]["triangles"] = 2**40
    with pytest.raises(MeshFormatError):
        surfaces.read_header(_rebuild(huge, body))


def test_an_index_outside_the_patch_is_refused_when_it_is_read() -> None:
    data, _ = artifact()
    header = surfaces.read_header(data)
    start = header["data_start"]
    patch = header["patches"][0]
    first_triangle = start + patch["offset"] + patch["points"] * 12
    broken = bytearray(data)
    broken[first_triangle : first_triangle + 4] = struct.pack("<I", 99)
    bad = bytes(broken)
    with pytest.raises(MeshFormatError):
        surfaces.patch_arrays(bad, surfaces.read_header(bad), 0)
    with pytest.raises(MeshFormatError):
        surfaces.patch_arrays(data, header, 7)


def test_patch_arrays_are_the_agents_arrays_and_views() -> None:
    data, _ = artifact()
    header = surfaces.read_header(data)
    positions, triangles, edges = surfaces.patch_arrays(data, header, 1)
    assert np.array_equal(positions, TRIANGLE_POINTS)
    assert triangles.tolist() == [[0, 1, 2]]
    assert edges.tolist() == [[0, 1], [1, 2], [0, 2]]
    assert not positions.flags.owndata


def test_patch_transport_decodes_to_one_group_with_face_ids_zero() -> None:
    data, _ = artifact()
    header = surfaces.read_header(data)
    payload = surfaces.patch_transport(data, header, 0, (0.25, 0.5, 0.75))
    part = unpack(payload)
    assert len(part.solids) == 1
    group = part.solids[0]
    assert group.name == "inlet"
    assert group.kind == "mesh"
    assert group.vertex_count == 4
    assert group.triangle_count == 2
    assert group.face_ids is not None
    assert group.face_ids.tolist() == [0, 0]
    assert group.faces == 1
    assert group.color == pytest.approx((0.25, 0.5, 0.75))
    assert np.array_equal(group.positions, QUAD_POINTS)
    assert json.loads(payload[4 : 4 + struct.unpack_from("<I", payload)[0]])["version"] == 2


def test_patch_edges_round_trip_the_segment_coordinates() -> None:
    data, _ = artifact()
    header = surfaces.read_header(data)
    payload = surfaces.patch_edges(data, header, 0)
    length = struct.unpack_from("<I", payload)[0]
    assert length % 4 == 0
    head = json.loads(payload[4 : 4 + length])
    assert head == {"version": 1, "units": "m", "segments": 4}
    segments = surfaces.unpack_edges(payload)
    expected = np.concatenate(
        [QUAD_POINTS[[a, b]].reshape(1, 6) for a, b in [(0, 1), (1, 2), (2, 3), (0, 3)]]
    )
    assert np.array_equal(segments, expected)
    with pytest.raises(MeshFormatError):
        surfaces.unpack_edges(payload[:-1])
