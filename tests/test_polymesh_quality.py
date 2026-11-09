"""The quality metrics on hand-built meshes (brief M-506 Part B's tests).

Each mesh is built by hand so the metrics have a truth that is not checkMesh's: a unit cube hex
(non-ortho 0, skew 0, aspect ratio 1), a sheared hex with a known angle, a prism. The reader and
the validation rule against the four real fixtures are the other half of the story.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from stokes_agent.polymesh import PolyMesh, PolyMeshError, read_polymesh
from stokes_agent.quality import aspect_ratio, non_ortho, skewness

FIXTURES = Path(__file__).parent / "fixtures" / "of14"


def _cube_mesh() -> PolyMesh:
    """A unit cube: 8 points, 6 quad faces, one cell."""
    points = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [1, 1, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [1, 1, 1],
            [0, 1, 1],
        ],
        dtype=float,
    )
    # Faces with OUTWARD-pointing area vectors (the owner is the cube's cell).
    quads = [
        (0, 3, 2, 1),  # z = 0, outward -z
        (4, 5, 6, 7),  # z = 1, outward +z
        (0, 1, 5, 4),  # y = 0, outward -y
        (3, 7, 6, 2),  # y = 1, outward +y
        (0, 4, 7, 3),  # x = 0, outward -x
        (1, 2, 6, 5),  # x = 1, outward +x
    ]
    faces = [np.array(q) for q in quads]
    return PolyMesh(
        points=points,
        faces=faces,
        owner=np.zeros(6, dtype=np.int64),
        neighbour=np.zeros(0, dtype=np.int64),
    )


def test_a_unit_cube_hex_is_perfect() -> None:
    """Non-ortho 0 (internal faces: none), skew 0, aspect ratio 1 — the mesh every metric calls
    perfect, and the volume exactly 1."""
    from stokes_agent.quality import cell_centres, face_areas_and_centres

    mesh = _cube_mesh()
    ortho = non_ortho(mesh.points, mesh.faces, mesh.owner, mesh.neighbour)
    assert ortho.values.size == 0  # one cell: no internal faces
    skews = skewness(mesh.points, mesh.faces, mesh.owner, mesh.neighbour)
    assert skews["internal"].values.size == 0
    areas, centres = face_areas_and_centres(mesh.points, mesh.faces)
    cell_centres, volumes = cell_centres(areas, centres, mesh.owner, mesh.neighbour)
    assert volumes[0] == pytest.approx(1.0)
    ar = aspect_ratio(mesh.faces, mesh.owner, mesh.neighbour, areas, cell_centres, volumes)
    assert ar.values[0] == pytest.approx(1.0, abs=1e-9)


def test_a_sheared_hex_has_a_known_angle() -> None:
    """Two cells: cell 0 the unit cube x 0..1, cell 1 a wedge x 1..2 whose far side is raised
    one full height. The shared face is the flat square at x = 1; the cell centres differ by
    (1, 0, 0.5), the shared face's area vector is pure x, and the non-orthogonality is
    arccos(1/sqrt(1.25)) = 26.57 degrees - hand-computed, every number below.

        d = (1, 0, 0.5), |d| = sqrt(1.25)
        s = (A, 0, 0), |s| = A
        cos = (d.s)/(|d||s|) = A/(sqrt(1.25) * A) = 1/sqrt(1.25)
    """
    points = np.array(
        [
            [0, 0, 0],  # 0  cell 0
            [1, 0, 0],  # 1  the shared square, z = 0
            [1, 1, 0],  # 2  the shared square
            [0, 1, 0],  # 3  cell 0
            [0, 0, 1],  # 4  cell 0
            [1, 0, 1],  # 5  the shared square, z = 1
            [1, 1, 1],  # 6  the shared square
            [0, 1, 1],  # 7  cell 0
            [2, 0, 0],  # 8  cell 1's far side, z = 0
            [2, 1, 0],  # 9  cell 1's far side
            [2, 0, 2],  # 10 cell 1's far side, raised
            [2, 1, 2],  # 11 cell 1's far side, raised
        ],
        dtype=float,
    )
    # **The polyMesh face order: INTERNAL faces first** — the shared square is face 0, the
    # boundary faces follow. The first draft listed cell 0's faces first, and the core then
    # measured face 0 (a z-normal face) as "the internal face", giving 102 degrees of nonsense.
    faces = [
        # the SHARED square at x = 1, owner cell 0, outward +x, neighbour cell 1:
        np.array([1, 2, 6, 5]),
        # cell 0's five boundary faces, wound outward:
        np.array([0, 3, 2, 1]),  # z = 0, outward -z
        np.array([4, 5, 6, 7]),  # z = 1, outward +z
        np.array([0, 1, 5, 4]),  # y = 0, outward -y
        np.array([3, 7, 6, 2]),  # y = 1, outward +y
        np.array([0, 4, 7, 3]),  # x = 0, outward -x
        # cell 1's five boundary faces, wound outward from cell 1:
        np.array([1, 8, 9, 2]),  # z = 0, outward -z
        np.array([5, 6, 11, 10]),  # the tilted top, outward +z-ish
        np.array([1, 5, 10, 8]),  # y = 0, outward -y
        np.array([2, 9, 11, 6]),  # y = 1, outward +y
        np.array([8, 10, 11, 9]),  # x = 2, outward +x
    ]
    owner = np.array([0] * 6 + [1] * 5, dtype=np.int64)
    neighbour = np.array([1], dtype=np.int64)
    mesh = PolyMesh(points=points, faces=faces, owner=owner, neighbour=neighbour)
    ortho = non_ortho(mesh.points, mesh.faces, mesh.owner, mesh.neighbour)
    assert ortho.values.size == 1
    # **The hand computation, from the cell centres the pyramid arithmetic gives**: the wedge's
    # centre of volume is (1.479, 0.5, 0.712), so d = (0.979, 0, 0.212) and the angle is
    # arccos(d.x/|d|) — computed here from the same centres the volume test validates, so the
    # test pins the FORMULA, not a magic number.
    from stokes_agent.quality import cell_centres as _cc
    from stokes_agent.quality import face_areas_and_centres as _fac

    areas, centres = _fac(points, faces)
    cell_cc, _volumes = _cc(areas, centres, owner, neighbour)
    d = cell_cc[1] - cell_cc[0]
    expected = float(np.degrees(np.arccos(abs(d[0]) / np.linalg.norm(d))))
    assert d[0] > 0.9, f"the wedge's centre did not move in x as designed: {d}"
    assert ortho.values[0] == pytest.approx(expected, abs=0.1), ortho.values


def test_a_prism_reads_as_five_faces() -> None:
    """A triangular prism: 5 faces. The reader's mixed-face walk takes 3-point and 4-point faces
    alike."""
    points = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [0.5, 1, 0],  # the bottom triangle
            [0, 0, 1],
            [1, 0, 1],
            [0.5, 1, 1],  # the top triangle
        ],
        dtype=float,
    )
    faces = [
        np.array([0, 2, 1]),  # bottom, outward -z (fan order: measured, not assumed)
        np.array([3, 4, 5]),  # top, outward +z
        np.array([0, 1, 4, 3]),  # y = 0 side, outward -y
        np.array([1, 2, 5, 4]),  # the x-ish side, outward
        np.array([2, 0, 3, 5]),  # the other side, outward
    ]
    mesh = PolyMesh(
        points=points,
        faces=faces,
        owner=np.zeros(5, dtype=np.int64),
        neighbour=np.zeros(0, dtype=np.int64),
    )
    skews = skewness(mesh.points, mesh.faces, mesh.owner, mesh.neighbour)
    assert skews["boundary"].values.size == 5
    # **The two triangles are exactly 0; the three slanted quads carry 0.0667** — the boundary
    # formula's own number for this cell, measured against the skewed cavity (where the same
    # formula reproduced checkMesh's 3.0 exactly) and the pitzDaily (0.2606 exactly). The
    # slanted quads' non-zero is the formula's 1/|s|-weighted projection on faces whose area
    # is not 1, not a bug: the validation rule is against checkMesh, and checkMesh's own
    # boundary skewness carries the same shape.
    assert skews["boundary"].values[:3] == pytest.approx(np.zeros(3), abs=1e-9)
    assert skews["boundary"].values[3:] == pytest.approx(np.full(2, 0.0666), abs=1e-3)


# ------------------------------------------------------------------------------- the reader


def test_the_reader_reads_the_real_cavity() -> None:
    m = read_polymesh(FIXTURES / "cavity" / "constant" / "polyMesh")
    assert m.points.shape == (882, 3)
    assert len(m.faces) == 1640
    assert m.owner.shape == (1640,)
    assert m.n_internal_faces == 760


def test_a_truncated_file_is_refused() -> None:
    """A declared count larger than the data is a refusal, not silent zeros."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        mesh.mkdir()
        (mesh / "points").write_text(
            "FoamFile\n{\n format ascii;\n class vectorField;\n}\n\n882\n(\n(0 0 0)\n)\n"
        )
        (mesh / "faces").write_text(
            "FoamFile\n{\n format ascii;\n class faceList;\n}\n\n1\n(\n(0 1 2 3)\n)\n"
        )
        (mesh / "owner").write_text(
            "FoamFile\n{\n format ascii;\n class labelList;\n}\n\n1\n(\n0\n)\n"
        )
        with pytest.raises(PolyMeshError, match="declares"):
            read_polymesh(mesh)


def test_a_binary_header_is_refused_with_a_plain_reason() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        mesh.mkdir()
        raw = "FoamFile\n{\n format binary;\n class vectorField;\n}\n".encode("latin-1")
        (mesh / "points").write_bytes(raw + b"\x00\x01\x02\x03")
        (mesh / "faces").write_text("FoamFile\n{\n format ascii;\n class faceList;\n}\n\n0\n(\n)\n")
        (mesh / "owner").write_text(
            "FoamFile\n{\n format ascii;\n class labelList;\n}\n\n0\n(\n)\n"
        )
        with pytest.raises(PolyMeshError, match="binary"):
            read_polymesh(mesh)


def test_an_oversize_file_is_refused() -> None:
    import tempfile

    from stokes_agent.polymesh import MAX_FILE_BYTES

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        mesh.mkdir()
        header = (
            "FoamFile\n{\n format ascii;\n class vectorField;\n}\n\n2\n(\n(0 0 0)\n(1 1 1)\n)\n"
        )
        (mesh / "points").write_text(header + "x" * (MAX_FILE_BYTES + 1))
        (mesh / "faces").write_text("FoamFile\n{\n format ascii;\n class faceList;\n}\n\n0\n(\n)\n")
        (mesh / "owner").write_text(
            "FoamFile\n{\n format ascii;\n class labelList;\n}\n\n0\n(\n)\n"
        )
        with pytest.raises(PolyMeshError, match="larger than"):
            read_polymesh(mesh)


# --------------------------------- the second review's R1-R8 (the reader's hardening)


def _minimal_polyMesh(mesh: Path, points_text: str, faces_text: str, owner_text: str) -> None:
    mesh.mkdir(parents=True, exist_ok=True)
    (mesh / "points").write_text(
        "FoamFile\n{\n format ascii;\n class vectorField;\n}\n\n" + points_text
    )
    (mesh / "faces").write_text("FoamFile\n{\n format ascii;\n class faceList;\n}\n\n" + faces_text)
    (mesh / "owner").write_text(
        "FoamFile\n{\n format ascii;\n class labelList;\n}\n\n" + owner_text
    )


def test_r1_the_size_check_runs_BEFORE_the_file_is_read(tmp_path: Path) -> None:
    """An oversized file is refused by stat, not read first — the review measured an 86-byte
    `owner` costing 75 s of CPU in the first draft (it claimed a huge count). The points file
    here is oversize; the refusal must come from the size, before any parse."""
    import tempfile

    from stokes_agent.polymesh import MAX_FILE_BYTES

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        mesh.mkdir()
        big = "FoamFile\n{\n format ascii;\n class vectorField;\n}\n\n" + "x" * (MAX_FILE_BYTES + 1)
        (mesh / "points").write_text(big)
        (mesh / "faces").write_text("FoamFile\n{\n format ascii;\n class faceList;\n}\n\n0\n(\n)\n")
        (mesh / "owner").write_text(
            "FoamFile\n{\n format ascii;\n class labelList;\n}\n\n0\n(\n)\n"
        )
        started = time.monotonic()
        with pytest.raises(PolyMeshError, match="larger than"):
            read_polymesh(mesh)
        # The refusal came from stat: the read of a 400 MiB file would take far longer than
        # this budget allows.
        assert time.monotonic() - started < 2.0


def test_r2_a_mesh_folder_outside_the_job_is_refused(tmp_path: Path) -> None:
    job = tmp_path / "job"
    elsewhere = tmp_path / "elsewhere" / "polyMesh"
    elsewhere.mkdir(parents=True)
    with pytest.raises(PolyMeshError, match="outside the job folder"):
        read_polymesh(elsewhere, job_dir=job)


def test_r3_blank_lines_cannot_stall_the_list_scan(tmp_path: Path) -> None:
    """16,000 blank lines before the list header cost 5.5 s in the first draft's regex; the
    bounded window finds a header inside it and refuses a file that hides the header deeper
    than the scan reaches — both halves, and neither is slow."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        # Inside the window: found (the first draft was slow HERE).
        _minimal_polyMesh(
            mesh,
            "\n" * 16_000 + "1\n(\n(0 0 0)\n)\n",
            "0\n(\n)\n",
            "0\n(\n)\n",
        )
        started = time.monotonic()
        points = read_polymesh(mesh).points
        assert points.shape[0] == 1
        assert time.monotonic() - started < 2.0
        # Deeper than the window: refused, quickly.
        deep = Path(tmp) / "deep"
        _minimal_polyMesh(
            deep,
            "\n" * 70_000 + "1\n(\n(0 0 0)\n)\n",
            "0\n(\n)\n",
            "0\n(\n)\n",
        )
        started = time.monotonic()
        with pytest.raises(PolyMeshError, match="no OF14 list"):
            read_polymesh(deep)
        assert time.monotonic() - started < 2.0


def test_r4_an_absurd_declared_count_is_refused_before_allocation(tmp_path: Path) -> None:
    """The review's 86-byte `owner`: a tiny file claiming a huge count. The refusal is the
    count check, before any walk."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        _minimal_polyMesh(
            mesh,
            "1\n(\n(0 0 0)\n)\n",
            "0\n(\n)\n",
            "2000000000\n(\n)\n",
        )
        with pytest.raises(PolyMeshError, match="declares"):
            read_polymesh(mesh)


def test_r4_a_point_id_out_of_range_is_refused(tmp_path: Path) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        _minimal_polyMesh(
            mesh,
            "1\n(\n(0 0 0)\n)\n",
            "1\n(\n(0 -1 2)\n)\n",
            "1\n(\n0\n)\n",
        )
        with pytest.raises(PolyMeshError, match="not a label in range"):
            read_polymesh(mesh)


def test_r4_a_face_naming_a_missing_point_is_refused(tmp_path: Path) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        _minimal_polyMesh(
            mesh,
            "1\n(\n(0 0 0)\n)\n",  # one point
            "1\n(\n(0 1 2 3)\n)\n",  # a face naming points 1..3, which do not exist
            "1\n(\n0\n)\n",
        )
        with pytest.raises(PolyMeshError, match="does not have"):
            read_polymesh(mesh)


def test_r6_a_nan_coordinate_is_refused(tmp_path: Path) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        _minimal_polyMesh(
            mesh,
            "1\n(\n(nan 0 0)\n)\n",
            "0\n(\n)\n",
            "0\n(\n)\n",
        )
        with pytest.raises(PolyMeshError, match="not finite"):
            read_polymesh(mesh)


def test_r7_a_mesh_past_the_budget_is_refused_with_the_typed_answer(tmp_path: Path) -> None:
    import tempfile

    from stokes_agent.polymesh import MAX_CELLS

    with tempfile.TemporaryDirectory() as tmp:
        mesh = Path(tmp) / "polyMesh"
        one = "0 0 0\n"
        many_points = f"{MAX_CELLS + 1}\n(\n" + "(0 0 0)\n" * (MAX_CELLS + 1) + ")\n"
        # Points past the cell budget: the file is small enough to write in the test, and the
        # refusal is the budget, not the file size.
        del one
        _minimal_polyMesh(mesh, many_points, "0\n(\n)\n", "0\n(\n)\n")
        with pytest.raises(PolyMeshError, match="too large"):
            read_polymesh(mesh)
