"""Mesh-only import (T-017, ADR-0021 decisions 1.2 and 1.3): the STL, OBJ and 3MF readers, the
feature-angle face split, the worker's mesh path, and the refusals a mesh-only part earns.

The reader and split tests build their files in the test (numpy for STL, text for OBJ, ``zipfile``
for 3MF), so they need neither OCP nor a worker. The end-to-end imports need OCP and skip
themselves without it; they share the module's warm workers, as the other geometry tests do.
"""

from __future__ import annotations

import math
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.api import Session
from stokes.errors import ValidationError
from stokes.geometry import formats, meshfile, readers
from stokes.geometry.errors import GeometryFileError, MeshOnly
from stokes.geometry.meshfile import read_3mf, read_obj, read_stl, split_faces
from stokes.geometry.worker import prewarm, shutdown_pool
from tests.selection_support import add_part, cube, new_session, part

F32 = np.ndarray[Any, Any]
STL_RECORD = np.dtype([("normal", "<f4", (3,)), ("corners", "<f4", (3, 3)), ("attr", "<u2")])
NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


@pytest.fixture(scope="module")
def workers() -> Iterator[None]:
    pytest.importorskip("OCP")
    prewarm(4)  # the end-to-end imports below, one warm worker each
    yield
    shutdown_pool()


# ---------------------------------------------------------------- meshes built in the test


def box_mesh(size: float = 1.0) -> tuple[F32, F32]:
    """An axis-aligned box of 8 shared corners and 12 triangles."""
    corners = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        dtype=np.float32,
    ) * np.float32(size)
    faces = (
        (0, 1, 2),
        (0, 2, 3),
        (4, 6, 5),
        (4, 7, 6),
        (5, 6, 2),
        (5, 2, 1),
        (7, 4, 0),
        (7, 0, 3),
        (3, 2, 6),
        (3, 6, 7),
        (0, 4, 5),
        (0, 5, 1),
    )
    return corners, np.asarray(faces, dtype=np.uint32)


def cylinder_mesh(segments: int = 64, radius: float = 1.0, height: float = 2.0) -> tuple[F32, F32]:
    """A closed cylinder: a fine side ring (``segments`` quads) and two cap fans."""
    angles = [2.0 * math.pi * i / segments for i in range(segments)]
    points = [
        (radius * math.cos(a), radius * math.sin(a), z) for z in (0.0, height) for a in angles
    ]
    faces: list[tuple[int, int, int]] = []
    for i in range(segments):
        j = (i + 1) % segments
        faces.append((i, j, segments + i))
        faces.append((j, segments + j, segments + i))
    for i in range(1, segments - 1):
        faces.append((0, i, i + 1))
        faces.append((segments, segments + i + 1, segments + i))
    return np.asarray(points, dtype=np.float32), np.asarray(faces, dtype=np.uint32)


def binary_stl(positions: F32, triangles: F32, header: bytes = b"solid part") -> bytes:
    records = np.zeros(triangles.shape[0], dtype=STL_RECORD)
    records["corners"] = positions[triangles]
    count = np.uint32(triangles.shape[0]).astype("<u4").tobytes()
    return header[:80].ljust(80, b" ") + count + records.tobytes()


def ascii_stl(bodies: Sequence[tuple[str, F32, F32]]) -> str:
    """One ``solid <name>`` block per body, as an ASCII STL writer writes it."""
    blocks = []
    for name, positions, triangles in bodies:
        lines = [f"solid {name}"]
        for tri in triangles:
            lines.append("  facet normal 0.000000e+00 0.000000e+00 1.000000e+00")
            lines.append("    outer loop")
            for corner in positions[tri]:
                lines.append("      vertex " + " ".join(f"{v:.6e}" for v in corner))
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append(f"endsolid {name}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks) + "\n"


def model_xml(objects: str, build: str = "", unit: str = "millimeter") -> str:
    """A 3MF model part: ``objects`` are ``<object>`` elements, ``build`` the items."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="{unit}" xmlns="{NS}">'
        f"<resources>{objects}</resources>{build}</model>\n"
    )


def triangle_object(object_id: str, name: str = "", *, size: float = 1.0) -> str:
    return (
        f'<object id="{object_id}" name="{name}"><mesh><vertices>'
        f'<vertex x="0" y="0" z="0"/><vertex x="{size}" y="0" z="0"/>'
        f'<vertex x="0" y="{size}" z="0"/></vertices>'
        '<triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>'
    )


def write_3mf(
    path: Path,
    xml: str | None,
    *,
    member: str = "3D/3dmodel.model",
    rels: str | None = None,
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        if xml is not None:
            archive.writestr(member, xml)
        if rels is not None:
            archive.writestr("_rels/.rels", rels)
    return path


# ---------------------------------------------------------------- split_faces


def test_split_faces_gives_six_faces_for_a_box() -> None:
    positions, triangles = box_mesh()
    ids = split_faces(positions, triangles)
    assert ids.dtype == np.uint32 and ids.shape == (12,)
    assert sorted(set(ids.tolist())) == [0, 1, 2, 3, 4, 5]
    assert ids[0] == 0  # renumbered by first appearance


def test_split_faces_gives_three_faces_for_a_fine_cylinder() -> None:
    positions, triangles = cylinder_mesh(64)
    ids = split_faces(positions, triangles)
    assert set(ids.tolist()) == {0, 1, 2}
    # A coarse cylinder keeps its facets apart: 16 sides at 22.5 degrees stay one region.
    coarse, coarse_triangles = cylinder_mesh(16)
    assert set(split_faces(coarse, coarse_triangles).tolist()) == {0, 1, 2}


def test_split_faces_links_only_below_the_feature_angle() -> None:
    """Two triangles sharing an edge (the second wound the other way), folded by ten degrees."""
    fold = math.radians(10.0)
    positions = np.array(
        [[0, 0, 0], [1, 0, 0], [0.5, -0.5, 0.0], [0.5, 0.5 * math.cos(fold), 0.5 * math.sin(fold)]],
        dtype=np.float32,
    )
    triangles = np.array([(0, 1, 2), (1, 0, 3)], dtype=np.uint32)
    assert set(split_faces(positions, triangles, 30.0).tolist()) == {0}
    assert set(split_faces(positions, triangles, 5.0).tolist()) == {0, 1}
    assert set(split_faces(positions, triangles, 180.0).tolist()) == {0}


def test_split_faces_keeps_disconnected_bodies_apart() -> None:
    positions, triangles = box_mesh()
    apart = np.concatenate([positions, positions + np.float32(5.0)])
    both = np.concatenate([triangles, triangles + np.uint32(8)])
    assert set(split_faces(apart, both).tolist()) == set(range(12))


def test_split_faces_on_no_triangles_is_empty() -> None:
    ids = split_faces(np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.uint32))
    assert ids.shape == (0,) and ids.dtype == np.uint32


def test_split_faces_refuses_an_angle_outside_the_range() -> None:
    positions, triangles = box_mesh()
    for angle in (0.0, 180.5, -1.0):
        with pytest.raises(ValidationError):
            split_faces(positions, triangles, angle)


# ---------------------------------------------------------------- STL


def test_a_binary_stl_gives_its_triangles_and_the_given_units(tmp_path: Path) -> None:
    positions, triangles = box_mesh(10.0)
    (tmp_path / "box.stl").write_bytes(binary_stl(positions, triangles, b"solid box"))
    model = read_stl(tmp_path / "box.stl", "mm")
    assert model.units == "mm" and model.warnings == ()
    body = model.bodies[0]
    assert body.name == "box" and body.triangles.shape == (12, 3)
    assert body.positions[body.triangles].shape == (12, 3, 3)
    assert float(body.positions.max()) == pytest.approx(10.0)
    inches = read_stl(tmp_path / "box.stl")  # the default is millimetres
    assert inches.units == "mm"


def test_a_binary_stl_is_recognised_by_its_record_count(tmp_path: Path) -> None:
    """A file whose size is not 84 + 50 n is read as text, whatever its header says."""
    positions, triangles = box_mesh()
    text = ascii_stl([("box", positions, triangles)])
    assert len(text.encode()) != 84 + 50 * triangles.shape[0]
    (tmp_path / "text.stl").write_text(text, encoding="ascii")
    assert len(read_stl(tmp_path / "text.stl").bodies) == 1
    (tmp_path / "short.stl").write_bytes(binary_stl(positions, triangles)[:-1])
    with pytest.raises(GeometryFileError) as info:
        read_stl(tmp_path / "short.stl")
    assert info.value.code == "geometry.unreadable"


def test_an_ascii_stl_gives_one_body_per_solid_block(tmp_path: Path) -> None:
    first, triangles = box_mesh(10.0)
    second = first + np.float32(20.0)
    (tmp_path / "two.stl").write_text(
        ascii_stl([("left", first, triangles), ("right", second, triangles)]), encoding="ascii"
    )
    model = read_stl(tmp_path / "two.stl", "cm")
    assert model.units == "cm"
    assert [b.name for b in model.bodies] == ["left", "right"]
    assert [split_faces(b.positions, b.triangles).max() + 1 for b in model.bodies] == [6, 6]


def test_an_ascii_stl_without_endsolid_keeps_its_body(tmp_path: Path) -> None:
    positions, triangles = box_mesh()
    text = ascii_stl([("box", positions, triangles)]).replace("endsolid box\n", "")
    (tmp_path / "open.stl").write_text(text, encoding="ascii")
    assert [b.name for b in read_stl(tmp_path / "open.stl").bodies] == ["box"]


def test_an_stl_without_triangles_is_refused(tmp_path: Path) -> None:
    (tmp_path / "empty.stl").write_text("solid nothing\nendsolid nothing\n", encoding="ascii")
    with pytest.raises(GeometryFileError) as info:
        read_stl(tmp_path / "empty.stl")
    assert "no triangles" in info.value.message


def test_an_ascii_stl_with_a_partial_triangle_is_refused(tmp_path: Path) -> None:
    (tmp_path / "odd.stl").write_text(
        "solid odd\nfacet normal 0 0 1\nouter loop\n"
        "vertex 0 0 0\nvertex 1 0 0\nendloop\nendfacet\nendsolid odd\n",
        encoding="ascii",
    )
    with pytest.raises(GeometryFileError) as info:
        read_stl(tmp_path / "odd.stl")
    assert "whole triangles" in info.value.message


def test_a_nan_or_infinite_coordinate_is_refused(tmp_path: Path) -> None:
    positions, triangles = box_mesh()
    for bad in (np.nan, np.inf):
        spoiled = positions.copy()
        spoiled[0, 0] = bad
        (tmp_path / "bad.stl").write_bytes(binary_stl(spoiled, triangles))
        with pytest.raises(GeometryFileError) as info:
            read_stl(tmp_path / "bad.stl")
        assert "NaN or infinite" in info.value.message


def test_a_source_over_the_size_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    positions, triangles = box_mesh()
    (tmp_path / "box.stl").write_bytes(binary_stl(positions, triangles))
    monkeypatch.setattr(meshfile, "MAX_SOURCE_BYTES", 16)
    with pytest.raises(GeometryFileError) as info:
        read_stl(tmp_path / "box.stl")
    assert "the limit is 16" in info.value.message
    with pytest.raises(GeometryFileError):
        read_stl(tmp_path / "missing.stl")


# ---------------------------------------------------------------- OBJ


def test_an_obj_gives_a_body_per_object_and_group(tmp_path: Path) -> None:
    (tmp_path / "two.obj").write_text(
        "# a comment\n"
        "v 0 0 0\nv 10 0 0\nv 10 10 0\nv 0 10 0\n"
        "f 1 2 3\n"  # before any o/g: a body named after the file
        "o first\ng first\nf 1 2 3\nf 1 3 4\n"
        "o second\nf 2 4 3\n",
        encoding="utf-8",
    )
    model = read_obj(tmp_path / "two.obj", "mm")
    assert model.units == "mm" and model.warnings == ()
    assert [b.name for b in model.bodies] == ["two", "first", "second"]
    assert [b.triangles.shape[0] for b in model.bodies] == [1, 2, 1]


def test_an_obj_takes_the_first_index_and_fan_triangulates(tmp_path: Path) -> None:
    (tmp_path / "quad.obj").write_text(
        "v 0 0 0\nv 10 0 0\nv 10 10 0\nv 0 10 0\n"
        "vt 0 0\nvn 0 0 1\n"
        "s off\nusemtl steel\nmtllib steel.mtl\n"
        "o quad\nf 1/1/1 2/2/1 3/3/1 4/4/1\n",
        encoding="utf-8",
    )
    body = read_obj(tmp_path / "quad.obj").bodies[0]
    assert body.triangles.tolist() == [[0, 1, 2], [0, 2, 3]]  # four corners fan into two
    assert body.positions.shape == (4, 3)


def test_an_obj_negative_index_counts_back_from_the_vertices_read(tmp_path: Path) -> None:
    (tmp_path / "negative.obj").write_text(
        "v 0 0 0\nv 10 0 0\nv 0 10 0\no tri\nf -3 -2 -1\n", encoding="utf-8"
    )
    body = read_obj(tmp_path / "negative.obj").bodies[0]
    assert body.triangles.tolist() == [[0, 1, 2]]
    assert body.positions.tolist() == [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]]


def test_an_obj_drops_a_body_without_faces(tmp_path: Path) -> None:
    (tmp_path / "empty.obj").write_text(
        "v 0 0 0\nv 1 0 0\nv 0 1 0\no nothing\nv 5 5 5\no tri\nf 1 2 3\n", encoding="utf-8"
    )
    assert [b.name for b in read_obj(tmp_path / "empty.obj").bodies] == ["tri"]
    (tmp_path / "none.obj").write_text("v 0 0 0\n", encoding="utf-8")
    with pytest.raises(GeometryFileError) as info:
        read_obj(tmp_path / "none.obj")
    assert "no faces" in info.value.message


def test_an_obj_index_past_its_vertices_is_refused(tmp_path: Path) -> None:
    (tmp_path / "bad.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 4\n", encoding="utf-8")
    with pytest.raises(GeometryFileError) as info:
        read_obj(tmp_path / "bad.obj")
    assert "does not have" in info.value.message
    (tmp_path / "short.obj").write_text("v 0 0 0\nv 1 0 0\nf 1 2\n", encoding="utf-8")
    with pytest.raises(GeometryFileError):
        read_obj(tmp_path / "short.obj")


# ---------------------------------------------------------------- 3MF


def test_a_3mf_reads_its_build_item_and_the_model_unit(tmp_path: Path) -> None:
    write_3mf(
        tmp_path / "part.3mf",
        model_xml(
            triangle_object("1", "base", size=10.0),
            '<build><item objectid="1"/></build>',
            unit="inch",
        ),
    )
    model = read_3mf(tmp_path / "part.3mf")
    assert model.units == "in" and model.warnings == ()
    assert [b.name for b in model.bodies] == ["base"]
    assert model.bodies[0].positions.tolist() == [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [0.0, 10.0, 0.0],
    ]


def test_a_3mf_places_a_component_with_its_transform(tmp_path: Path) -> None:
    """Object 2 holds object 1 moved 20 mm in x and rotated; the build item places object 2."""
    objects = triangle_object("1", "base") + (
        '<object id="2" name="placed"><components>'
        '<component objectid="1" transform="0 1 0 -1 0 0 0 0 1 20 0 0"/>'
        "</components></object>"
    )
    write_3mf(
        tmp_path / "placed.3mf",
        model_xml(
            objects, '<build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 5"/></build>'
        ),
    )
    body = read_3mf(tmp_path / "placed.3mf").bodies[0]
    assert body.name == "placed"
    assert body.positions.tolist() == [[20.0, 0.0, 5.0], [20.0, 1.0, 5.0], [19.0, 0.0, 5.0]]


def test_a_3mf_without_build_items_takes_the_object_no_component_places(tmp_path: Path) -> None:
    objects = triangle_object("1", "base") + (
        '<object id="2" name="wrapper"><components><component objectid="1"/></components></object>'
    )
    write_3mf(tmp_path / "noBuild.3mf", model_xml(objects))
    model = read_3mf(tmp_path / "noBuild.3mf")
    assert [b.name for b in model.bodies] == ["wrapper"]  # the top object, not what it places


def test_a_3mf_model_path_comes_from_its_relationship(tmp_path: Path) -> None:
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"'
        ' Target="/3D/model.3mf"/></Relationships>'
    )
    write_3mf(
        tmp_path / "rels.3mf",
        model_xml(triangle_object("1", "rel")),
        member="3D/model.3mf",
        rels=rels,
    )
    assert read_3mf(tmp_path / "rels.3mf").bodies[0].name == "rel"
    write_3mf(tmp_path / "norel.3mf", model_xml(triangle_object("1", "x")), member="3D/other.model")
    with pytest.raises(GeometryFileError) as info:
        read_3mf(tmp_path / "norel.3mf")
    assert "no 3D model part" in info.value.message


def test_a_3mf_warns_about_a_unit_it_does_not_know(tmp_path: Path) -> None:
    write_3mf(tmp_path / "odd.3mf", model_xml(triangle_object("1", "x"), unit="furlong"))
    model = read_3mf(tmp_path / "odd.3mf")
    assert model.units == "mm" and len(model.warnings) == 1 and "furlong" in model.warnings[0]


def test_a_3mf_with_a_doctype_is_refused(tmp_path: Path) -> None:
    evil = '<!DOCTYPE model [<!ENTITY x "y">]>' + model_xml(triangle_object("1", "x"))
    write_3mf(tmp_path / "evil.3mf", evil)
    with pytest.raises(GeometryFileError) as info:
        read_3mf(tmp_path / "evil.3mf")
    assert "DTD" in info.value.message
    write_3mf(
        tmp_path / "evlrels.3mf",
        model_xml(triangle_object("1", "x")),
        member="3D/model.3mf",
        rels='<!DOCTYPE r [<!ENTITY x "y">]><Relationships/>',
    )
    with pytest.raises(GeometryFileError):
        read_3mf(tmp_path / "evlrels.3mf")


def test_a_3mf_member_over_the_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_3mf(tmp_path / "big.3mf", model_xml(triangle_object("1", "x")))
    monkeypatch.setattr(meshfile, "MAX_3MF_MEMBER_BYTES", 16)
    with pytest.raises(GeometryFileError) as info:
        read_3mf(tmp_path / "big.3mf")
    assert "the limit is 16" in info.value.message


def test_a_3mf_total_over_the_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two small members pass the per-member limit and still break the total."""
    write_3mf(tmp_path / "many.3mf", model_xml(triangle_object("1", "x")))
    with zipfile.ZipFile(tmp_path / "many.3mf", "a") as archive:
        archive.writestr("extra", "x" * 512)
    monkeypatch.setattr(meshfile, "MAX_3MF_MEMBER_BYTES", 1024)
    monkeypatch.setattr(meshfile, "MAX_3MF_TOTAL_BYTES", 300)
    with pytest.raises(GeometryFileError) as info:
        read_3mf(tmp_path / "many.3mf")
    assert "uncompressed" in info.value.message


def test_a_3mf_that_is_not_a_zip_or_has_bad_xml_is_refused(tmp_path: Path) -> None:
    (tmp_path / "not.3mf").write_bytes(b"this is not a zip archive")
    with pytest.raises(GeometryFileError) as info:
        read_3mf(tmp_path / "not.3mf")
    assert "not a zip archive" in info.value.message
    write_3mf(tmp_path / "bad.3mf", "<model><oops></model>")
    with pytest.raises(GeometryFileError) as info:
        read_3mf(tmp_path / "bad.3mf")
    assert "malformed XML" in info.value.message


def test_a_3mf_component_of_a_missing_object_is_refused(tmp_path: Path) -> None:
    write_3mf(
        tmp_path / "missing.3mf",
        model_xml(
            triangle_object("1", "x") + '<object id="2"><components>'
            '<component objectid="9"/></components></object>',
            '<build><item objectid="2"/></build>',
        ),
    )
    with pytest.raises(GeometryFileError) as info:
        read_3mf(tmp_path / "missing.3mf")
    assert "which the file lacks" in info.value.message


def test_a_3mf_triangle_past_its_vertices_is_refused(tmp_path: Path) -> None:
    bad = (
        '<object id="1"><mesh><vertices><vertex x="0" y="0" z="0"/></vertices>'
        '<triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>'
    )
    write_3mf(tmp_path / "past.3mf", model_xml(bad, '<build><item objectid="1"/></build>'))
    with pytest.raises(GeometryFileError) as info:
        read_3mf(tmp_path / "past.3mf")
    assert "past its vertices" in info.value.message


def test_a_3mf_cycle_of_components_is_refused(tmp_path: Path) -> None:
    objects = (
        '<object id="1"><components><component objectid="2"/></components></object>'
        '<object id="2"><components><component objectid="1"/></components></object>'
    )
    write_3mf(tmp_path / "cycle.3mf", model_xml(objects, '<build><item objectid="1"/></build>'))
    with pytest.raises(GeometryFileError) as info:
        read_3mf(tmp_path / "cycle.3mf")
    assert "nest deeper" in info.value.message


# ---------------------------------------------------------------- dispatch and probes


def test_read_mesh_dispatches_on_the_suffix_and_refuses_a_units_parameter_for_3mf(
    tmp_path: Path,
) -> None:
    positions, triangles = box_mesh()
    (tmp_path / "box.stl").write_bytes(binary_stl(positions, triangles))
    assert readers.read_mesh(tmp_path / "box.stl").units == "mm"
    write_3mf(tmp_path / "part.3mf", model_xml(triangle_object("1", "x")))
    assert readers.read_mesh(tmp_path / "part.3mf").bodies[0].name == "x"
    with pytest.raises(ValidationError) as info:
        readers.read_mesh(tmp_path / "part.3mf", units="mm")
    assert "declare their own units" in info.value.message
    with pytest.raises(ValidationError):
        readers.read_mesh(tmp_path / "box.stl", units="boing")


def test_a_brep_format_is_not_a_mesh_source(tmp_path: Path) -> None:
    (tmp_path / "box.brep").write_text("DBRep_DrawableShape\n", encoding="utf-8")
    with pytest.raises(ValidationError) as info:
        readers.read_mesh(tmp_path / "box.brep", units="mm")
    assert "B-rep format" in info.value.message


def test_read_source_refuses_a_mesh_file_as_mesh_only(tmp_path: Path) -> None:
    positions, triangles = box_mesh()
    (tmp_path / "box.stl").write_bytes(binary_stl(positions, triangles))
    with pytest.raises(MeshOnly) as info:
        readers.read_source(tmp_path / "box.stl")
    assert info.value.code == "geometry.mesh_only"


def test_the_format_table_and_probe_know_the_mesh_formats(tmp_path: Path) -> None:
    assert [f.units for f in formats.FORMATS[3:]] == ["param", "param", "file"]
    fake = tmp_path / "fake.3mf"
    fake.write_bytes(b"not a zip archive at all")
    with pytest.raises(GeometryFileError) as info:
        formats.probe(fake, formats.format_for(fake))
    assert "not a 3MF file" in info.value.message


# ---------------------------------------------------------------- the refusals


def mesh_only_session(tmp_path: Path) -> tuple[Session, str]:
    """A session with one cube part marked mesh-only, the way an STL import leaves it."""
    session = new_session(tmp_path)
    part_id = add_part(session, "mesh", part(cube(0.01)))
    session.execute("parameter.set", node=part_id, name="family", value="mesh")
    return session, part_id


def test_geometry_check_reports_a_mesh_only_part_without_a_brep(tmp_path: Path) -> None:
    session, part_id = mesh_only_session(tmp_path)
    result = session.execute("geometry.check", part=part_id)
    assert result["brep"] == {"checked": False, "reason": "mesh-only part"}
    assert result["stats"]["faces"] == 6  # the tessellation is still checked


def test_export_step_refuses_a_mesh_only_part(tmp_path: Path) -> None:
    session, part_id = mesh_only_session(tmp_path)
    with pytest.raises(MeshOnly) as info:
        session.execute("geometry.export_step", parts=[part_id], path="mesh.step")
    assert info.value.code == "geometry.mesh_only"
    assert "Export it to STL for the mesher" in info.value.message
    assert not (tmp_path / "demo" / "mesh.step").exists()


def test_export_stl_writes_a_mesh_only_part(tmp_path: Path) -> None:
    session, part_id = mesh_only_session(tmp_path)
    session.execute("tag.create", name="walls", members=[{"part": part_id, "faces": [[0, 1]]}])
    result = session.execute("geometry.export_stl", path="walls.stl", tags=["walls"])
    assert result["solids"] == 1 and result["units"] == "m"
    written = Path(result["files"][0]["path"])
    assert written.is_file() and written.parent == session.project_path


# ---------------------------------------------------------------- end to end (OCP)


def test_a_binary_stl_imports_as_a_mesh_part(tmp_path: Path, workers: None) -> None:
    positions, triangles = box_mesh(10.0)
    source = tmp_path / "box.stl"
    source.write_bytes(binary_stl(positions, triangles, b"solid box"))
    session = Session(journal_path=tmp_path / "journal.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    result = session.execute("geometry.import", path=str(source), wait=True)
    assert result["type"] == "part" and result["import"] == {"status": "ready"}
    params = result["params"]
    assert params["family"] == "mesh" and params["units"] == "mm"
    assert params["faces"] == 6 and params["triangles"] == 12
    assert params["bbox"] == pytest.approx([0, 0, 0, 0.01, 0.01, 0.01], abs=1e-7)
    info = session.execute("geometry.info", node=result["id"])
    assert [(s["name"], s["kind"], s["faces"]) for s in info["solids"]] == [("box", "mesh", 6)]
    checked = session.execute("geometry.check", part=result["id"])
    assert checked["brep"] == {"checked": False, "reason": "mesh-only part"}
    with pytest.raises(MeshOnly):
        session.execute("geometry.export_step", parts=[result["id"]], path="box.step")
    session.execute("tag.create", name="inlet", members=[{"part": result["id"], "faces": [1]}])
    written = session.execute("geometry.export_stl", path="inlet.stl", tags=["inlet"], binary=False)
    assert written["triangles"] == 2
    ascii_file = Path(written["files"][0]["path"])
    assert ascii_file.read_text(encoding="ascii").startswith("solid inlet")


def test_an_ascii_stl_of_two_solids_gives_two_parts(tmp_path: Path, workers: None) -> None:
    positions, triangles = box_mesh(10.0)
    source = tmp_path / "two.stl"
    source.write_text(
        ascii_stl(
            [("left", positions, triangles), ("right", positions + np.float32(20.0), triangles)]
        ),
        encoding="ascii",
    )
    session = Session(journal_path=tmp_path / "j.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    result = session.execute("geometry.import", path=str(source), units="mm", wait=True)
    assert result["type"] == "group"
    info = session.execute("geometry.info", node=result["id"])
    assert [p["name"] for p in info["parts"]] == ["left", "right"]
    assert all(p["family"] == "mesh" and p["faces"] == 6 for p in info["parts"])
    moved = session.execute("geometry.info", node=info["parts"][1]["node_id"])
    assert moved["params"]["bbox"] == pytest.approx([0.02, 0.02, 0.02, 0.03, 0.03, 0.03], abs=1e-7)


def test_an_obj_of_two_groups_gives_two_parts(tmp_path: Path, workers: None) -> None:
    source = tmp_path / "two.obj"
    source.write_text(
        "v 0 0 0\nv 10 0 0\nv 10 10 0\nv 0 10 0\n"
        "o first\nf 1 2 3\nf 1 3 4\n"
        "o second\nf 1 2 3\nf 1 3 4\n"
        "v 20 0 0\nv 30 0 0\nv 30 10 0\nv 20 10 0\n"
        "o moved\nf 5 6 7\nf 5 7 8\n",
        encoding="utf-8",
    )
    session = Session(journal_path=tmp_path / "j.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    result = session.execute("geometry.import", path=str(source), wait=True)
    info = session.execute("geometry.info", node=result["id"])
    assert [p["name"] for p in info["parts"]] == ["first", "second", "moved"]
    assert all(p["family"] == "mesh" and p["units"] == "mm" for p in info["parts"])
    first = session.execute("geometry.info", node=info["parts"][0]["node_id"])
    moved = session.execute("geometry.info", node=info["parts"][2]["node_id"])
    assert first["params"]["bbox"] == pytest.approx([0, 0, 0, 0.01, 0.01, 0], abs=1e-7)
    assert moved["params"]["bbox"] == pytest.approx([0.02, 0, 0, 0.03, 0.01, 0], abs=1e-7)


def test_a_3mf_with_a_transformed_component_imports_placed(tmp_path: Path, workers: None) -> None:
    objects = triangle_object("1", "base", size=10.0) + (
        '<object id="2" name="placed"><components>'
        '<component objectid="1" transform="1 0 0 0 1 0 0 0 1 20 0 0"/>'
        "</components></object>"
    )
    source = write_3mf(
        tmp_path / "placed.3mf",
        model_xml(objects, '<build><item objectid="2"/></build>', unit="millimeter"),
    )
    session = Session(journal_path=tmp_path / "j.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    result = session.execute("geometry.import", path=str(source), wait=True)
    params = result["params"]
    assert params["family"] == "mesh" and params["units"] == "mm"
    assert params["faces"] == 1 and params["triangles"] == 1
    assert params["bbox"] == pytest.approx([0.02, 0, 0, 0.03, 0.01, 0], abs=1e-7)
    with pytest.raises(ValidationError, match="declare their own units"):
        session.execute("geometry.import", path=str(source), units="mm")
