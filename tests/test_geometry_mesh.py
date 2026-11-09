"""Meshes, the transport (versions 1 and 2), the mesh cache, source checks and the part schema.

No OCP needed: meshes are synthetic. The transport is read back with a reader written here,
independent of :func:`stokes.geometry.transport.unpack`.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.errors import ValidationError
from stokes.geometry import files
from stokes.geometry.cache import cache_file, read_cache, write_cache
from stokes.geometry.errors import (
    CadUnavailable,
    GeometryFileError,
    GeometryUnsupported,
    MeshFormatError,
)
from stokes.geometry.files import (
    SourceStamp,
    check_step_file,
    node_name_for,
    part_name_for,
    unique_name,
)
from stokes.geometry.mesh import PartMesh, TriMesh, bbox_list, bbox_union, vertex_normals, weld
from stokes.geometry.sample import sample_part
from stokes.geometry.transport import pack_meshes, pack_part, unpack
from stokes.model import Project, load_project, registry, save_project
from stokes.model.nodes import QuantityValue
from stokes.server.geometry import pack_meshes as server_pack_meshes


def read_transport(data: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A reader for both versions, written from the layout description alone."""
    (length,) = struct.unpack_from("<I", data, 0)
    assert length % 4 == 0
    header = json.loads(data[4 : 4 + length].rstrip(b"\0"))
    start = 4 + length
    groups = []
    end = start
    for g in header["groups"]:
        base = start + g["offset"]
        assert base % 4 == 0
        v, t = g["vertices"], g["triangles"]
        out: dict[str, Any] = {"meta": g}
        out["positions"] = np.frombuffer(data, "<f4", 3 * v, base).reshape(v, 3)
        base += 12 * v
        out["normals"] = np.frombuffer(data, "<f4", 3 * v, base).reshape(v, 3)
        base += 12 * v
        out["indices"] = np.frombuffer(data, "<u4", 3 * t, base).reshape(t, 3)
        base += 12 * t
        out["face_ids"] = None
        if g.get("faceIds"):
            out["face_ids"] = np.frombuffer(data, "<u4", t, base)
            base += 4 * t
        end = max(end, base)
        groups.append(out)
    assert end == len(data), "trailing or missing bytes after the last group"
    return header, groups


def original_v1(meshes: list[TriMesh]) -> bytes:
    """The version 1 writer as it was before version 2 existed (the golden reference)."""
    groups = []
    chunks: list[bytes] = []
    offset = 0
    for mesh in meshes:
        pos = np.ascontiguousarray(mesh.positions, dtype="<f4").tobytes()
        nrm = np.ascontiguousarray(mesh.normals, dtype="<f4").tobytes()
        idx = np.ascontiguousarray(mesh.indices, dtype="<u4").tobytes()
        groups.append(
            {
                "name": mesh.name,
                "kind": mesh.kind,
                "color": list(mesh.color),
                "vertices": mesh.vertex_count,
                "triangles": mesh.triangle_count,
                "offset": offset,
            }
        )
        chunks.extend((pos, nrm, idx))
        offset += len(pos) + len(nrm) + len(idx)
    header = json.dumps({"version": 1, "groups": groups}).encode("utf-8")
    header += b"\0" * ((-len(header)) % 4)
    return struct.pack("<I", len(header)) + header + b"".join(chunks)


def quad_solid(name: str = "plate", face_base: int = 0) -> TriMesh:
    """Two faces of two triangles each, the second face lifted to z = 1 m."""
    positions = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1]],
        dtype=np.float32,
    )
    indices = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 5]], dtype=np.uint32)
    normals = vertex_normals(positions, indices)
    face_ids = np.array([0, 0, 1, 1], dtype=np.uint32) + np.uint32(face_base)
    return TriMesh(
        name, "solid", (0.5, 0.25, 1.0), positions, normals, indices, face_ids, 2, face_base, "fine"
    )


# ---------------------------------------------------------------------- transport
def test_version_1_bytes_are_unchanged() -> None:
    meshes = sample_part()
    assert pack_meshes(meshes) == original_v1(meshes)
    assert server_pack_meshes(meshes) == original_v1(meshes)
    header, groups = read_transport(pack_meshes(meshes))
    assert header == {"version": 1, "groups": [g["meta"] for g in groups]}
    for mesh, group in zip(meshes, groups, strict=True):
        assert np.array_equal(group["positions"], mesh.positions)
        assert np.array_equal(group["indices"], mesh.indices)


def test_version_1_round_trip_through_unpack() -> None:
    part = unpack(pack_meshes(sample_part()))
    assert [s.name for s in part.solids] == [
        "walls",
        "inlet_flange",
        "outlet_flange",
        "inlet",
        "outlet",
    ]
    assert all(s.face_ids is None for s in part.solids) and part.units == "m"


def test_version_2_layout_and_round_trip() -> None:
    a, b = quad_solid("plate"), quad_solid("lid", face_base=2)
    stamp = SourceStamp("plate.step", 1234, 99)
    data = pack_part(PartMesh((a, b), "m", stamp))
    header, groups = read_transport(data)
    assert header["version"] == 2 and header["units"] == "m" and header["faces"] == 4
    assert header["bbox"] == {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}
    assert header["source"] == {"name": "plate.step", "size": 1234, "mtime_ns": 99}
    first = groups[0]["meta"]
    assert first["faceIds"] is True and first["faces"] == 2 and first["faceBase"] == 0
    assert groups[1]["meta"]["faceBase"] == 2 and first["quality"] == "fine"
    assert groups[1]["face_ids"].tolist() == [2, 2, 3, 3]
    assert np.array_equal(groups[0]["normals"], a.normals)
    back = unpack(data)
    assert back.source == stamp and back.faces == 4 and back.triangles == 8
    assert np.array_equal(back.solids[1].face_ids, b.face_ids)
    assert back.bbox == ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


def test_version_2_without_face_ids() -> None:
    data = pack_meshes(sample_part(), version=2)
    header, groups = read_transport(data)
    assert header["version"] == 2 and all(g["meta"]["faceIds"] is False for g in groups)
    assert all(g["face_ids"] is None for g in groups)


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda d: d[:3],
        lambda d: struct.pack("<I", 1 << 30) + d[4:],
        lambda d: d.replace(b'"version": 2', b'"version": 9'),
        lambda d: d[:-4],
    ],
)
def test_unpack_rejects_malformed_buffers(corrupt: Any) -> None:
    data = pack_part(PartMesh((quad_solid(),)))
    with pytest.raises(MeshFormatError) as info:
        unpack(corrupt(data))
    assert info.value.code == "geometry.bad_mesh"


def test_unpack_rejects_indices_and_face_ids_out_of_range() -> None:
    mesh = quad_solid()
    bad_index = TriMesh(**{**mesh.__dict__, "indices": mesh.indices + np.uint32(10)})
    with pytest.raises(MeshFormatError):
        unpack(pack_part(PartMesh((bad_index,))))
    bad_face = TriMesh(**{**mesh.__dict__, "face_ids": mesh.face_ids + np.uint32(5)})  # type: ignore[operator]
    with pytest.raises(MeshFormatError):
        unpack(pack_part(PartMesh((bad_face,))))


def test_unknown_version_is_refused() -> None:
    with pytest.raises(MeshFormatError):
        pack_meshes(sample_part(), version=3)


# ---------------------------------------------------------------------- mesh helpers
def test_weld_shares_corners_inside_a_face_only() -> None:
    square = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32
    )
    positions, indices = weld(square, np.zeros(6, dtype=np.uint32))
    assert positions.shape == (4, 3) and indices.shape == (2, 3)
    assert np.array_equal(positions[indices].reshape(-1, 3), square)
    _, apart = weld(square, np.array([0, 0, 0, 1, 1, 1], dtype=np.uint32))
    assert len(np.unique(apart)) == 6
    signed = square.copy()
    signed[0, 2] = -0.0
    assert weld(signed, np.zeros(6, dtype=np.uint32))[0].shape == (4, 3)


def test_vertex_normals_follow_the_winding() -> None:
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    up = vertex_normals(positions, np.array([[0, 1, 2]], dtype=np.uint32))
    down = vertex_normals(positions, np.array([[0, 2, 1]], dtype=np.uint32))
    assert np.allclose(up, [[0, 0, 1]] * 3) and np.allclose(down, [[0, 0, -1]] * 3)
    lonely = vertex_normals(np.zeros((2, 3), np.float32), np.zeros((0, 3), np.uint32))
    assert np.allclose(np.linalg.norm(lonely, axis=1), 1.0)


def test_bbox_helpers() -> None:
    assert bbox_union([None, ((0, 0, 0), (1, 1, 1)), ((-1, 2, 0), (0, 3, 0.5))]) == (
        (-1, 0, 0),
        (1, 3, 1),
    )
    assert bbox_union([]) is None
    assert bbox_list(None) == [] and bbox_list(((0, 0, 0), (1, 2, 3))) == [0, 0, 0, 1, 2, 3]
    assert quad_solid().bbox == ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


# ---------------------------------------------------------------------- cache
def test_cache_round_trip_and_freshness(tmp_path: Path) -> None:
    source = tmp_path / "plate.step"
    source.write_text("ISO-10303-21;\n", encoding="utf-8")
    project = tmp_path / "demo.stokes"
    node_id = "n_0000abcd"
    data = pack_part(PartMesh((quad_solid(),), "m", SourceStamp.of(source)))
    path = write_cache(project, node_id, data)
    assert path == project / "cache" / "geometry" / "n_0000abcd.mesh"
    part, raw = read_cache(project, node_id, source) or (None, b"")
    assert raw == data and part is not None and part.triangles == 4
    assert read_cache(project, node_id, tmp_path / "gone.step") is not None  # source missing
    source.write_text("ISO-10303-21; changed\n", encoding="utf-8")
    assert read_cache(project, node_id, source) is None  # the source changed since
    path.write_bytes(b"\x00\x01")
    assert read_cache(project, node_id, None) is None  # malformed: ignored, not raised
    assert read_cache(project, "n_ffffffff", None) is None  # absent
    with pytest.raises(MeshFormatError):
        cache_file(project, "../escape")


def test_cache_without_stamp_uses_modification_times(tmp_path: Path) -> None:
    source = tmp_path / "plate.step"
    source.write_text("ISO-10303-21;\n", encoding="utf-8")
    path = write_cache(tmp_path, "n_0000abcd", pack_part(PartMesh((quad_solid(),))))
    os.utime(source, ns=(path.stat().st_mtime_ns + 10**9,) * 2)
    assert read_cache(tmp_path, "n_0000abcd", source) is None
    os.utime(source, ns=(path.stat().st_mtime_ns - 10**9,) * 2)
    assert read_cache(tmp_path, "n_0000abcd", source) is not None


# ---------------------------------------------------------------------- source files
def test_check_step_file(tmp_path: Path) -> None:
    good = tmp_path / "Part 1.STP"
    good.write_bytes(b"\xef\xbb\xbfISO-10303-21;\nHEADER;\n")
    assert check_step_file(good) == good.resolve()
    cases = {
        "missing.step": None,
        "fake.step": b"solid ascii stl\n",
    }
    for name, content in cases.items():
        path = tmp_path / name
        if content is not None:
            path.write_bytes(content)
        with pytest.raises(GeometryFileError) as info:
            check_step_file(path)
        assert info.value.code == "geometry.unreadable"
    notes = tmp_path / "notes.txt"
    notes.write_bytes(b"ISO-10303-21;")
    with pytest.raises(GeometryUnsupported) as info:
        check_step_file(notes)  # the suffix names no import format
    assert info.value.code == "geometry.unsupported"
    with pytest.raises(GeometryFileError):
        check_step_file(tmp_path)


def test_names_from_files() -> None:
    assert part_name_for(Path("Part 1.step")) == "Part_1"
    assert part_name_for(Path("7-axis bracket (rev B).stp")) == "part_7_axis_bracket_rev_B"
    assert part_name_for(Path("---.step")) == "part"
    assert unique_name("pin", {"pin", "pin_2"}) == "pin_3" and unique_name("pin", set()) == "pin"
    # Solid names from a file become part names the same way (decision G1).
    assert node_name_for("bolt:1", "solid") == "bolt_1"
    assert node_name_for("Gehäuse 2", "solid") == "Geh_use_2"
    assert node_name_for("", "solid") == "solid" and node_name_for("3D", "solid") == "solid_3D"


def test_cad_missing_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(files, "cad_available", lambda: False)
    with pytest.raises(CadUnavailable) as info:
        files.require_cad()
    assert info.value.code == "geometry.cad_missing" and "cad" in info.value.message


# ---------------------------------------------------------------------- the part node
def test_part_node_schema_and_persistence(tmp_path: Path) -> None:
    spec = registry.get("part")
    schema = spec.json_schema()["properties"]
    assert schema["bbox"]["type"] == "array" and schema["bbox"]["x-dimension"] == "length"
    assert schema["color"]["pattern"] == "^#[0-9a-f]{6}$"
    assert schema["visible"]["default"] is True
    # **`simulate` is default TRUE and is not `visible`** (the workflow-v2 plan §2, T-221): the
    # eye is display-only and the mesher never reads it, while this decides what a mesh and a
    # solve use. A default of false would leave every body out of every existing project.
    assert schema["simulate"]["default"] is True
    # The label is `title` in the JSON schema - not `x-label`, which I assumed and the test
    # corrected.
    assert schema["simulate"]["title"] == "In simulation"
    assert schema["family"]["enum"] == ["brep", "mesh"] and schema["family"]["default"] == "brep"
    project = Project.new("demo")
    project.add("/geometry", "group", "parts")
    part = project.add("/geometry/parts", "part", "bracket", {"source": "C:/cad/bracket.step"})
    assert part.params == {
        "source": "C:/cad/bracket.step",
        "solid": 0,
        "units": "",
        "family": "brep",
        "triangles": 0,
        "faces": 0,
        "bbox": [],
        "color": "#c7ccd1",
        "visible": True,
        "opacity": 1.0,
        "simulate": True,
        # **The per-body mesh controls default to INHERIT** (T-232/T-234; the resolver is
        # M-504's): an empty size and zero levels leave a body meshing exactly as the global
        # settings say, so every existing project keeps its dictionary byte for byte. `layers`
        # materialises null - no default, because `0` is a meaning (none), not "not set".
        "mesh_size": QuantityValue(value=0.0, unit="m", expr=None),
        "refinement": 0,
        "layers": None,
        "basis": "",
        "derived": "",
    }
    project.set_param(part.id, "bbox", [0, 0, 0, 0.01, 0.02, 0.03])
    project.set_param(part.id, "color", "#cc331a")
    for name, bad in (("bbox", ["a"]), ("bbox", [float("nan")]), ("color", "red"), ("faces", 1.5)):
        with pytest.raises(ValidationError):
            project.set_param(part.id, name, bad)
    other = project.add("/geometry/parts", "part", "pin")
    assert other.params["bbox"] == [] and other.params["bbox"] is not part.params["bbox"]
    save_project(project, tmp_path / "demo")
    again = load_project(tmp_path / "demo")
    assert again.get("/geometry/parts/bracket").params["bbox"] == [0, 0, 0, 0.01, 0.02, 0.03]
