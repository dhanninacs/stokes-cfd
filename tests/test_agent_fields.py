"""The point-field extractor: `STKFLD1`, the pure packer, and the VTK reader (T-215).

**The one invariant this file exists to pin** is that a patch's point order *is* the surfaces
artifact's point order. The desktop (T-218) colours the patches it already draws by writing a
per-vertex tint, so a field array whose vertices do not line up with the drawn ones is not a
rendering bug — it is a wrong picture that looks plausible. Nothing but an assertion on the point
count, patch by patch and in order, catches that.

Three properties drop out of the brief and are tested here rather than assumed:

* **A patch with no field array stores an EMPTY `fields` dict**, never zeros. An `empty` patch has
  no `U`; writing `(0, 0, 0)` there would say the wall is at rest, which is a different claim.
* **`pack_fields` is pure numpy.** It has no VTK import at all, so most of this file runs on a host
  without VTK — and the layout can be tested without a mesh.
* **The header's offsets are multiples of four**, because the reader (`T-216`, pinned here) seeks
  to a byte offset and reads `float32`/`uint32` straight out of the buffer.

The layout, from the brief::

    bytes[8]  magic b"STKFLD1\\0"
    u32       header_length (JSON padded with spaces to a multiple of 4)
    bytes     header JSON
    data      arrays at header offsets, from the start of the data section
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.errors import StokesError
from stokes_agent.extract import (
    FIELDS,
    MAGIC_FIELDS,
    MAX_ARTIFACT_BYTES,
    AgentMismatch,
    AgentTooLarge,
    pack_fields,
    unpack_fields,
)

# ---------------------------------------------------------------- the pure packer


def patch(
    name: str,
    positions: list[list[float]],
    fields: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """One patch in `pack_fields`' input shape: points and the field arrays it has."""
    return {
        "name": name,
        "points": np.array(positions, dtype="float32"),
        "fields": fields or {},
    }


def quad(offset: float = 0.0) -> list[list[float]]:
    return [
        [offset, 0.0, 0.0],
        [offset + 1.0, 0.0, 0.0],
        [offset + 1.0, 1.0, 0.0],
        [offset, 1.0, 0.0],
    ]


def test_the_magic_and_the_header_layout() -> None:
    """`STKFLD1\\0`, then a u32 header length, then JSON padded to a multiple of four."""
    data, header = pack_fields(
        [patch("a", quad(), {"U": np.array([[1, 0, 0]] * 4, dtype="f4")})], None, 0.5
    )
    assert data[:8] == MAGIC_FIELDS
    length = int.from_bytes(data[8:12], "little")
    assert length % 4 == 0, "the header is padded to four bytes"
    parsed = json.loads(data[12 : 12 + length].decode("utf-8"))
    assert parsed["version"] == 1
    assert parsed["time"] == 0.5
    assert parsed["units"] == {"U": "m/s", "U_mag": "m/s", "p": "m2/s2"}
    assert header["slice"] is None
    assert len(data) == 12 + length + data[12 + length :].__len__()


def test_the_declared_fields_are_the_ones_the_brief_names() -> None:
    """`FIELDS` is what a model run must have: `U` and `p`. `U_mag` is computed, never read; the
    turbulence and time-averaged fields follow when the case has them (`OPTIONAL_FIELDS`)."""
    from stokes_agent.extract import OPTIONAL_FIELDS, fields_for

    assert FIELDS == ("U", "p")
    assert fields_for(None) == (*FIELDS, *OPTIONAL_FIELDS)


def test_a_reader_recovers_every_array_exactly() -> None:
    """**Byte for byte.** Offsets, counts and components in the header are the whole contract.

    Also the row-major part: `components == 3` means three components per point, so the stored
    buffer is `f32[3 * points]` and not three arrays of `points`.
    """
    u = np.array(
        [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [-1.0, 0.5, 0.25], [0.0, 0.0, 9.0]], dtype="float32"
    )
    p = np.array([10.0, 11.0, 12.0, 13.0], dtype="float32")
    mag = np.array([3.741657386, 8.774964387, 1.145643924, 9.0], dtype="float32")
    data, _ = pack_fields(
        [
            patch("a", quad(), {"U": u, "U_mag": mag, "p": p}),
            patch("b", quad(5.0), {"U": u, "U_mag": mag}),
        ],
        None,
        1.0,
    )
    got_header, got = unpack_fields(data)
    assert got_header["patches"][0]["name"] == "a"
    assert np.array_equal(got["a"]["U"], u), "U did not survive the round trip"
    # **`(points, components)`**, which is how a renderer consumes an array: the header's
    # `components` is what reshapes the flat buffer back, so this is the shape part of the contract.
    assert np.array_equal(got["a"]["p"], p.reshape(-1, 1))
    assert np.array_equal(got["a"]["U_mag"], mag.reshape(-1, 1))
    assert np.array_equal(got["b"]["U"], u), "the second patch's offset is wrong"
    assert "p" not in got["b"], "a patch without p must not gain one"


def test_every_offset_is_a_multiple_of_four() -> None:
    """A reader seeks to an offset and reads `f32`/`u32`, so an unaligned one is a corrupt read."""
    u = np.array([[1, 0, 0]] * 4, dtype="f4")
    p = np.array([1.0] * 4, dtype="f4")
    _, header = pack_fields(
        [patch("a", quad(), {"U": u, "U_mag": p, "p": p}), patch("b", quad(2.0), {"p": p})],
        {
            "origin": [0.0, 0.0, 0.5],
            "normal": [0.0, 0.0, 1.0],
            "positions": np.zeros((4, 3), dtype="f4"),
            "triangles": np.array([[0, 1, 2], [0, 2, 3]], dtype="u4"),
            "edges": np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="u4"),
            "fields": {"U": u, "U_mag": p, "p": p},
        },
        2.0,
    )
    offsets = [sub["offset"] for entry in header["patches"] for sub in entry["fields"].values()]
    offsets.append(header["slice"]["offset"])
    offsets += [sub["offset"] for sub in header["slice"]["fields"].values()]
    assert all(offset % 4 == 0 for offset in offsets), offsets


def test_the_offsets_are_contiguous_from_the_start_of_the_data_section() -> None:
    """**From the start of the data section**, not from the start of the file: the first patch's
    first field is at 0 and each block begins where the last one ended."""
    u = np.array([[1, 0, 0]] * 4, dtype="f4")
    p = np.array([1.0] * 4, dtype="f4")
    data, header = pack_fields([patch("a", quad(), {"U": u, "U_mag": p, "p": p})], None, 0.0)
    first = header["patches"][0]
    offsets = [entry["offset"] for entry in first["fields"].values()]
    assert offsets[0] == 0
    assert offsets == sorted(offsets)
    body = data[12 + int.from_bytes(data[8:12], "little") :]
    # 4 points x (3 + 1 + 1) float32.
    assert len(body) == 4 * 5 * 4


def test_min_and_max_span_the_stored_array() -> None:
    """`min`/`max` describe the bytes in the file, so a client can set its colour scale without
    reading them."""
    u = np.array(
        [[1.0, 0.0, 0.0], [3.0, 0.0, 0.0], [-2.0, 0.0, 0.0], [0.5, 0.0, 0.0]], dtype="float32"
    )
    mag = np.array([1.0, 3.0, 2.0, 0.5], dtype="float32")
    # |(1,0,0)| = 1, |(3,0,0)| = 3, |(-2,0,0)| = 2, |(0.5,0,0)| = 0.5 — so the magnitude's own
    # range is 0.5 .. 3 while the components' is -2 .. 3.
    p = np.array([-5.0, 2.0, 7.0, 0.0], dtype="float32")
    _, header = pack_fields([patch("a", quad(), {"U": u, "U_mag": mag, "p": p})], None, 0.0)
    entry = header["patches"][0]["fields"]
    assert (entry["p"]["min"], entry["p"]["max"]) == (-5.0, 7.0)
    # **`U` is measured over the magnitude**, not over every component separately. The components
    # range -2 .. 3, but what is reported is 0.5 .. 3: |u| at each point, which is the same range
    # `U_mag` has because here `U_mag` *is* that magnitude.
    assert (entry["U"]["min"], entry["U"]["max"]) == (0.5, 3.0)
    assert (entry["U_mag"]["min"], entry["U_mag"]["max"]) == (0.5, 3.0)
    assert entry["U"]["components"] == 3
    assert entry["U_mag"]["components"] == 1
    assert entry["p"]["components"] == 1


def test_a_patch_without_fields_packs_an_empty_dict() -> None:
    """**Never zeros.** An `empty` patch has no `U`; a zero there is a claim about the flow."""
    data, header = pack_fields(
        [patch("a", quad(), {"p": np.array([1.0] * 4, dtype="f4")}), patch("b", quad(3.0), {})],
        None,
        0.0,
    )
    assert header["patches"][1]["fields"] == {}
    _, got = unpack_fields(data)
    assert got["b"] == {}, "an empty patch gained arrays"


def test_the_patch_order_and_counts_are_the_surfaces_ones() -> None:
    """The header keeps the caller's order and each patch's own point count, which is what the
    mismatch check compares against."""
    _, header = pack_fields(
        [patch("walls", quad()), patch("inlet", quad(9.0)), patch("empty_", quad(2.0))],
        None,
        0.0,
    )
    assert [entry["name"] for entry in header["patches"]] == ["walls", "inlet", "empty_"]
    assert [entry["index"] for entry in header["patches"]] == [0, 1, 2]
    assert [entry["points"] for entry in header["patches"]] == [4, 4, 4]


def test_a_slice_with_no_polygons_is_empty_and_not_an_error() -> None:
    """A plane that misses the mesh gives a slice with zero of everything."""
    empty = np.zeros((0, 3), dtype="f4")
    data, header = pack_fields(
        [],
        {
            "origin": [0.0, 0.0, 0.0],
            "normal": [0.0, 0.0, 1.0],
            "positions": empty,
            "triangles": np.zeros((0, 3), dtype="u4"),
            "edges": np.zeros((0, 2), dtype="u4"),
            "fields": {},
        },
        0.0,
    )
    slice_ = header["slice"]
    assert (slice_["points"], slice_["triangles"], slice_["edges"]) == (0, 0, 0)
    assert slice_["fields"] == {}
    _, got = unpack_fields(data)
    assert got["__slice__"]["positions"].shape == (0, 3)


def test_the_slice_keeps_its_geometry_and_its_fields() -> None:
    """Positions, triangles and edges, then the point fields, all from the slice's own offset."""
    positions = np.array([[0, 0, 0.5], [1, 0, 0.5], [1, 1, 0.5], [0, 1, 0.5]], dtype="f4")
    triangles = np.array([[0, 1, 2], [0, 2, 3]], dtype="u4")
    edges = np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="u4")
    u = np.array([[1, 0, 0]] * 4, dtype="f4")
    data, header = pack_fields(
        [],
        {
            "origin": [0.0, 0.0, 0.5],
            "normal": [0.0, 0.0, 1.0],
            "positions": positions,
            "triangles": triangles,
            "edges": edges,
            "fields": {"U": u},
        },
        3.0,
    )
    slice_ = header["slice"]
    assert slice_["origin"] == [0.0, 0.0, 0.5]
    assert slice_["normal"] == [0.0, 0.0, 1.0]
    assert (slice_["points"], slice_["triangles"], slice_["edges"]) == (4, 2, 4)
    _, got = unpack_fields(data)
    assert np.allclose(got["__slice__"]["positions"], positions)
    assert np.array_equal(got["__slice__"]["triangles"], triangles)
    assert np.array_equal(got["__slice__"]["edges"], edges)
    assert np.array_equal(got["__slice__"]["U"], u)


# ---------------------------------------------------------------- the vertex order is the contract


def test_a_patch_whose_arrays_do_not_match_its_point_count_is_refused() -> None:
    """**The invariant the task exists to prove.** A field array with one value per *cell* rather
    than per *point* is the exact bug that would tint the wrong vertices; a wrong count is refused
    rather than packed."""
    with pytest.raises(ValueError, match="points"):
        pack_fields([patch("a", quad(), {"U": np.array([[1, 0, 0]] * 3, dtype="f4")})], None, 0.0)


def test_a_zero_area_triangle_is_dropped() -> None:
    """Correction 1 of the brief: a plane through a face plane gives zero-area polygons. They are
    dropped rather than stored, so the slice holds no degenerate geometry."""
    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype="f4")
    triangles = np.array([[0, 1, 2], [0, 2, 3], [0, 1, 1]], dtype="u4")
    _, header = pack_fields(
        [],
        {
            "origin": [0, 0, 0],
            "normal": [0, 0, 1],
            "positions": positions,
            "triangles": triangles,
            "edges": np.zeros((0, 2), dtype="u4"),
            "fields": {},
        },
        0.0,
    )
    assert header["slice"]["triangles"] == 2, "the degenerate triangle survived"


def test_an_artifact_over_the_limit_is_refused() -> None:
    """**`agent.too_large`, before the buffer is built.**

    The ceiling is the same 2 GiB `write_artifact` enforces, but it has to be raised *before* the
    data is packed: an artifact that is over the limit must not be measured by allocating it. The
    guard therefore works from the array's *shape*, and this reaches it with a view — a zero-stride
    array of `MAX_ARTIFACT_BYTES / 4 + 4` points holds one float32 of memory while *claiming* 2 GiB.
    That is exactly the case the pre-check exists for, and proving it here does not allocate: the
    view's `nbytes` is the size it would have if materialised, and `base.size` is what is really
    held.
    """
    points = MAX_ARTIFACT_BYTES // 4 + 4
    seed = np.zeros(1, dtype="f4")
    big = np.lib.stride_tricks.as_strided(seed, shape=(points, 3), strides=(0, 0))
    with pytest.raises(AgentTooLarge) as caught:
        pack_fields([{"name": "a", "points": big, "fields": {}}], None, 0.0)
    assert caught.value.code == "agent.too_large"
    assert "GiB" in caught.value.message
    # A zero-stride view's `nbytes` is what it would occupy if materialised; the seed it is a view
    # of is one float32. So no 2 GiB buffer exists at any point in this test.
    assert big.nbytes > MAX_ARTIFACT_BYTES
    assert np.shares_memory(big, seed)


# ---------------------------------------------------------------- the reader pins the layout


def test_a_wrong_magic_is_refused() -> None:
    """The test's own reader is the desktop's T-216 contract; a buffer that is not `STKFLD1` is not
    read as one."""
    data, _ = pack_fields([patch("a", quad())], None, 0.0)
    with pytest.raises(ValueError, match="STKFLD1"):
        unpack_fields(b"STKMSH1\0" + data[8:])


def test_a_truncated_buffer_is_refused() -> None:
    data, _ = pack_fields([patch("a", quad(), {"p": np.array([1.0] * 4, dtype="f4")})], None, 0.0)
    with pytest.raises(ValueError):
        unpack_fields(data[:-4])


def test_an_offset_past_the_end_is_refused() -> None:
    """A header that claims data which is not there must not read whatever follows in the file."""
    data, header = pack_fields(
        [patch("a", quad(), {"p": np.array([1.0] * 4, dtype="f4")})], None, 0.0
    )
    length = int.from_bytes(data[8:12], "little")
    header["patches"][0]["fields"]["p"]["offset"] = len(data) * 4
    text = json.dumps(header, separators=(",", ":")).encode("utf-8")
    text += b" " * ((-len(text)) % 4)
    body = data[12 + length :]
    broken = MAGIC_FIELDS + len(text).to_bytes(4, "little") + text + body
    with pytest.raises(ValueError, match="is past the end"):
        unpack_fields(broken)


# ---------------------------------------------------------------- the VTK reader


HEAD = """/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      / F ield          | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /  O peration      | Website:  https://openfoam.org                  |
|   \\\\  /   A nd            | Version:  14                                    |
|    \\\\/    M anipulation   |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
"""


def write_case(case: Path) -> None:
    """A one-hex OpenFOAM case with `0/U` and `0/p`, in the format blockMesh writes.

    The field format is `tests/fixtures/of14/pitzDaily/0/U`: `internalField   uniform (1 0 0);`
    for a vector and `internalField   uniform 5;` for a scalar.
    """
    mesh = case / "constant" / "polyMesh"
    mesh.mkdir(parents=True, exist_ok=True)
    (mesh / "points").write_text(
        HEAD
        + """{
    format      ascii;
    class       vectorField;
    location    "constant/polyMesh";
    object      points;
}
8
(
(0 0 0)
(1 0 0)
(1 1 0)
(0 1 0)
(0 0 1)
(1 0 1)
(1 1 1)
(0 1 1)
)
"""
    )
    (mesh / "faces").write_text(
        HEAD
        + """{
    format      ascii;
    class       faceList;
    location    "constant/polyMesh";
    object      faces;
}
6
(
4(0 3 2 1)
4(4 5 6 7)
4(0 1 5 4)
4(1 2 6 5)
4(2 3 7 6)
4(3 0 4 7)
)
"""
    )
    (mesh / "owner").write_text(
        HEAD
        + """{
    format      ascii;
    class       labelList;
    location    "constant/polyMesh";
    object      owner;
}
6
(
0
0
0
0
0
0
)
"""
    )
    (mesh / "neighbour").write_text(
        HEAD
        + """{
    format      ascii;
    class       labelList;
    location    "constant/polyMesh";
    object      neighbour;
}
0
(
)
"""
    )
    (mesh / "boundary").write_text(
        HEAD
        + """{
    format      ascii;
    class       polyBoundaryMesh;
    location    "constant/polyMesh";
    object      boundary;
}
2
(
    a
    {
        type            wall;
        inGroups        List<word> 1(wall);
        nFaces          1;
        startFace       0;
    }
    b
    {
        type            empty;
        inGroups        List<word> 1(empty);
        nFaces          5;
        startFace       1;
    }
)
"""
    )
    (case / "0").mkdir(parents=True, exist_ok=True)
    (case / "0" / "U").write_text(
        HEAD
        + """{
    format      ascii;
    class       volVectorField;
    location    "0";
    object      U;
}
dimensions      [0 1 -1 0 0 0 0];

internalField   uniform (1 0 0);

boundaryField
{
    a
    {
        type            fixedValue;
        value           uniform (1 0 0);
    }
    b
    {
        type            empty;
    }
}

// ************************************************************************* //
"""
    )
    (case / "0" / "p").write_text(
        HEAD
        + """{
    format      ascii;
    class       volScalarField;
    location    "0";
    object      p;
}
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 5;

boundaryField
{
    a
    {
        type            fixedValue;
        value           uniform 5;
    }
    b
    {
        type            empty;
    }
}

// ************************************************************************* //
"""
    )


def surfaces_header_for(case: Path) -> dict[str, Any]:
    """The surfaces artifact's header for the case, which is what `extract_fields` compares to."""
    from stokes_agent.extract import extract_surfaces

    _, header = extract_surfaces(case)
    return header


def test_a_hand_written_case_extracts_its_fields(tmp_path: Path) -> None:
    """**VTK reads the fields we wrote**, so the extractor is tested without OpenFOAM.

    `U` is `(1, 0, 0)` everywhere in the case, so `U_mag` is 1 on every patch point and `p` is 5 —
    numbers that cannot pass by accident. Patch `b` is `empty`, and VTK still reports its arrays;
    what the extractor must not do is invent values for a patch VTK gave none for.
    """
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_fields

    case = tmp_path / "hex"
    write_case(case)
    surfaces = surfaces_header_for(case)
    data, header = extract_fields(case, surfaces, None)
    assert data[:8] == MAGIC_FIELDS
    assert header["time"] == 0.0
    by_name = {entry["name"]: entry for entry in header["patches"]}
    assert set(by_name) == {entry["name"] for entry in surfaces["patches"]}
    _, got = unpack_fields(data)
    for name, entry in by_name.items():
        assert entry["points"] == {e["name"]: e["points"] for e in surfaces["patches"]}[name]
        assert any(np.allclose(got[name]["U"], [1.0, 0.0, 0.0]) for _ in [0]), got[name]["U"]
        assert np.allclose(got[name]["U_mag"], 1.0)
        assert np.allclose(got[name]["p"], 5.0)
    assert set(header["patches"][0]["fields"]) == {"U", "U_mag", "p"}


def test_the_patch_points_are_the_surfaces_points_in_the_same_order(tmp_path: Path) -> None:
    """**The invariant.** The positions the fields are attached to are the positions the surface
    extractor wrote — same patches, same order, same count. Nothing else makes the tint land on the
    vertex it belongs to."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_fields, extract_surfaces, unpack_artifact

    case = tmp_path / "hex"
    write_case(case)
    surfaces_data, surfaces_header = extract_surfaces(case)
    _, surfaces = unpack_artifact(surfaces_data)
    _, header = extract_fields(case, surfaces_header, None)
    assert [entry["name"] for entry in header["patches"]] == [
        entry["name"] for entry in surfaces_header["patches"]
    ]
    assert [entry["points"] for entry in header["patches"]] == [
        entry["points"] for entry in surfaces_header["patches"]
    ]
    assert surfaces, "the surfaces artifact has no patches, so this proves nothing"


def test_a_slice_through_the_cell_centre_gives_one_quad(tmp_path: Path) -> None:
    """Four points, two triangles, four edges — and the same values on the cut points."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_fields

    case = tmp_path / "hex"
    write_case(case)
    surfaces = surfaces_header_for(case)
    data, header = extract_fields(
        case, surfaces, {"origin": [0.5, 0.5, 0.5], "normal": [0.0, 0.0, 1.0]}
    )
    slice_ = header["slice"]
    assert slice_ is not None
    assert slice_["points"] == 4
    assert slice_["triangles"] == 2
    assert slice_["edges"] == 4
    _, got = unpack_fields(data)
    assert np.allclose(got["__slice__"]["U"], [1.0, 0.0, 0.0])
    assert np.allclose(got["__slice__"]["U_mag"], 1.0)
    assert np.allclose(got["__slice__"]["p"], 5.0)


def test_the_default_plane_does_not_lie_on_a_face(tmp_path: Path) -> None:
    """Correction 1 of the brief: with no plane the origin is the bbox centre moved along the
    normal by `1e-3` of the bbox diagonal, because the centre of a block-meshed duct is exactly a
    face plane and a cut through vertices gives duplicated or zero-area polygons.

    The offset is stated in the header, so a client can say which plane was cut.
    """
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import SLICE_OFFSET_FRACTION, extract_fields

    case = tmp_path / "hex"
    write_case(case)
    surfaces = surfaces_header_for(case)
    _, header = extract_fields(case, surfaces, None)
    slice_ = header["slice"]
    assert slice_ is not None
    diagonal = float(np.linalg.norm([1.0, 1.0, 1.0]))
    expected = 0.5 + SLICE_OFFSET_FRACTION * diagonal
    assert slice_["origin"][2] == pytest.approx(expected, rel=1e-5)
    assert slice_["normal"] == [0.0, 0.0, 1.0]
    assert slice_["points"] == 4, "the offset plane cuts the hex exactly once"


def test_the_default_plane_cuts_along_the_mesh_not_across_it(tmp_path: Path) -> None:
    """**The normal is the shortest axis of the mesh**, so the default cut holds the two longest.
    Measured in the GUI (2026-11-01): a pipe along x cut with the old fixed z-normal showed a disc,
    not the lengthwise profile anyone wants to see. A hex squashed to a tenth in y is cut by a
    y-normal plane through its middle, offset off the face as before; the cube (a tie) keeps z.
    """
    pytest.importorskip("vtkmodules")
    import re

    from stokes_agent.extract import SLICE_OFFSET_FRACTION, extract_fields

    case = tmp_path / "flat"
    write_case(case)
    points = case / "constant" / "polyMesh" / "points"
    squashed = re.sub(
        r"\((\d) (\d) (\d)\)",
        lambda m: f"({m.group(1)} {float(m.group(2)) * 0.1:g} {m.group(3)})",
        points.read_text(),
    )
    points.write_text(squashed)
    surfaces = surfaces_header_for(case)
    _, header = extract_fields(case, surfaces, None)
    slice_ = header["slice"]
    assert slice_["normal"] == [0.0, 1.0, 0.0]
    diagonal = float(np.linalg.norm([1.0, 0.1, 1.0]))
    assert slice_["origin"][1] == pytest.approx(0.05 + SLICE_OFFSET_FRACTION * diagonal, rel=1e-5)
    assert slice_["origin"][0] == pytest.approx(0.5) and slice_["origin"][2] == pytest.approx(0.5)
    assert slice_["points"] == 4, "the plane cuts the flat hex exactly once"


def test_a_slice_that_misses_the_mesh_is_empty(tmp_path: Path) -> None:
    """A plane outside the mesh gives a slice with zero of everything, not an error."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_fields

    case = tmp_path / "hex"
    write_case(case)
    surfaces = surfaces_header_for(case)
    _, header = extract_fields(
        case, surfaces, {"origin": [50.0, 50.0, 50.0], "normal": [0.0, 0.0, 1.0]}
    )
    assert header["slice"]["points"] == 0
    assert header["slice"]["triangles"] == 0
    assert header["slice"]["fields"] == {}


def test_a_surfaces_header_with_a_different_point_count_is_refused(tmp_path: Path) -> None:
    """**`agent.mismatch`, naming the patch and both counts.** A surfaces artifact of a different
    mesh would tint the wrong vertices; falling back silently is the one thing the brief forbids.

    The count is *larger* than the truth on purpose: a shorter one would fail inside VTK's own
    reader instead, and the error would be about the wrong thing.
    """
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_fields

    case = tmp_path / "hex"
    write_case(case)
    surfaces = surfaces_header_for(case)
    target = surfaces["patches"][1]
    target["points"] = int(target["points"]) + 4
    with pytest.raises(AgentMismatch) as caught:
        extract_fields(case, surfaces, None)
    assert caught.value.code == "agent.mismatch"
    assert target["name"] in caught.value.message
    assert str(target["points"]) in caught.value.message


def test_a_surfaces_header_in_a_different_patch_order_is_refused(tmp_path: Path) -> None:
    """Order as well as counts: the second patch renamed to the first's name is a mismatch even
    though both patches have their own counts."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_fields

    case = tmp_path / "hex"
    write_case(case)
    surfaces = surfaces_header_for(case)
    surfaces["patches"] = list(reversed(surfaces["patches"]))
    with pytest.raises(AgentMismatch) as caught:
        extract_fields(case, surfaces, None)
    assert caught.value.code == "agent.mismatch"


# ---------------------------------------------------------------- the protocol


@pytest.mark.skipif(sys.platform == "win32", reason="jobs are POSIX only")
def test_the_fields_method_refuses_a_job_that_is_not_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**`agent.not_ready` over the wire.** A job whose solver is still running has no fields to
    read, and the client is told which state it is in rather than handed an empty artifact.

    Reached through the real server and the real client, because the refusal belongs to the
    *method*: a handler-only test would not prove `extract.fields` is registered at all.

    The fake `blockMesh` is what makes the job creatable without OpenFOAM — the same trick
    `test_agent_job_protocol.py` uses — and the job is deliberately never started, so its state is
    `queued` and the fields must not be read.
    """
    import asyncio

    from stokes_agent import jobs as jobs_module
    from stokes_agent.client import AgentClient
    from stokes_agent.engine_env import EngineEnv
    from stokes_agent.jobs import JobStore
    from stokes_agent.server import AgentServer

    binary = tmp_path / "bin"
    binary.mkdir()
    utility = binary / "blockMesh"
    utility.write_text("#!/bin/sh\necho meshing\n")
    utility.chmod(0o755)
    made = EngineEnv(
        bashrc=str(tmp_path / "bashrc"),
        env={"PATH": f"{binary}:/usr/bin:/bin", "HOME": str(tmp_path)},
        project="OpenFOAM",
        version="14",
        flavour="org",
        seconds=0.0,
    )
    monkeypatch.setattr(jobs_module, "resolve_engine", lambda engine=None: made)

    async def scenario() -> None:
        server = AgentServer(jobs=JobStore(tmp_path / "jobs"))
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            reply = await client.call(
                "job.create",
                files={"system/controlDict": "x"},
                steps=[{"name": "run", "argv": ["blockMesh"], "timeout_s": 60}],
                label="t",
            )
            # Created, never started: the state is `queued`, so there are no fields.
            with pytest.raises(StokesError) as caught:
                await client.call("extract.fields", job=reply["job"])
            assert caught.value.code == "agent.not_ready"
            assert caught.value.details["state"] == "queued"
            # And the method is really registered, which `agent.info` reports.
            info = await client.call("agent.info")
            assert "extract.fields" in info["methods"]
        finally:
            await client.close()
            await server.stop()

    asyncio.run(scenario())


def test_the_slice_parameter_is_validated(tmp_path: Path) -> None:
    """`slice` comes from a client, so it is shapes-and-finiteness checked at the boundary."""
    from stokes_agent.server import AgentBadParams, _slice_plane

    assert _slice_plane({"origin": [0, 0, 0.5], "normal": [0, 0, 1]}) == {
        "origin": [0.0, 0.0, 0.5],
        "normal": [0.0, 0.0, 1.0],
    }
    for bad in (
        {"origin": [0, 0], "normal": [0, 0, 1]},
        {"origin": [0, 0, 0], "normal": [0, 0, 0]},
        {"origin": [0, 0, 0], "normal": [0, 0, float("nan")]},
        {"origin": [0, 0, 0], "normal": [0, 0, 1], "extra": 1},
        ["not", "an", "object"],
        {"origin": [0, 0, True], "normal": [0, 0, 1]},
    ):
        with pytest.raises(AgentBadParams):
            _slice_plane(bad)


# ---------------------------------------------------------------- the energy engine (ADR-0027)


def test_an_energy_job_reads_temperature_in_kelvin_and_pressure_in_pascal() -> None:
    """**Per job, not global**: `of14.fluid` reads `U`, absolute `p` and `T`; never `p_rgh`."""
    from stokes_agent.extract import fields_for, units_for

    assert fields_for("of14.fluid")[:3] == ("U", "p", "T")
    assert "TMean" in fields_for("of14.fluid")
    assert "p_rgh" not in fields_for("of14.fluid")
    units = units_for("of14.fluid")
    assert units["T"] == "K" and units["p"] == "Pa" and units["U"] == "m/s"
    assert units["pMean"] == "Pa" and units["TMean"] == "K"
    # The incompressible engine, and a job with no engine, read U and p first, then the optional.
    for engine in (None, "of14.incompressibleFluid"):
        assert fields_for(engine)[:2] == FIELDS
        assert "TMean" not in fields_for(engine)
        units = units_for(engine)
        assert {k: units[k] for k in ("U", "U_mag", "p")} == {
            "U": "m/s",
            "U_mag": "m/s",
            "p": "m2/s2",
        }
        assert units["pMean"] == "m2/s2" and units["UMean"] == "m/s"
        assert units["k"] == "m2/s2" and units["nut"] == "m2/s"
        assert units["omega"] == "1/s" and units["epsilon"] == "m2/s3"


def test_an_energy_artifact_carries_t_after_p_with_its_units() -> None:
    from stokes_agent.extract import _order_fields, fields_for, units_for

    energy = fields_for("of14.fluid")
    found = {
        "T": np.full(4, 300.0, dtype="f4"),
        "p": np.full(4, 101325.0, dtype="f4"),
        "U": np.array([[1, 0, 0]] * 4, dtype="f4"),
    }
    found["U_mag"] = np.linalg.norm(found["U"], axis=1)
    ordered = _order_fields(found, energy)
    assert list(ordered) == ["U", "U_mag", "p", "T"]
    data, header = pack_fields([patch("wall", quad(), ordered)], None, 1.0, units_for("of14.fluid"))
    assert header["units"]["T"] == "K" and header["units"]["p"] == "Pa"
    entry = header["patches"][0]["fields"]["T"]
    assert entry["components"] == 1 and entry["min"] == entry["max"] == 300.0
    _, arrays = unpack_fields(data)
    assert arrays["wall"]["T"].shape == (4, 1)


def test_the_engine_comes_from_the_job_records_solve_step() -> None:
    from stokes_agent.extract import engine_of_steps

    def record(solver: str) -> list[dict[str, Any]]:
        return [
            {"name": "decomposePar", "argv": ["decomposePar"]},
            {"name": "solve", "argv": ["foamRun", "-solver", solver]},
        ]

    assert engine_of_steps(record("fluid")) == "of14.fluid"
    assert engine_of_steps(record("incompressibleFluid")) == "of14.incompressibleFluid"
    assert engine_of_steps(record("simpleFoam")) is None
    for junk in (None, "x", [1, 2], [{"argv": "foamRun"}], [{"argv": ["foamRun", "-solver"]}]):
        assert engine_of_steps(junk) is None


def test_the_agent_accepts_the_fluid_solver_and_nothing_else_new() -> None:
    import re

    from stokes_agent.flags import ALLOWED_FLAGS

    pattern = ALLOWED_FLAGS["foamRun"]["-solver"].pattern
    assert pattern is not None
    assert re.fullmatch(pattern, "fluid") and re.fullmatch(pattern, "incompressibleFluid")
    for refused in ("fluid extra", "multiphaseEuler", "solid", ""):
        assert not re.fullmatch(pattern, refused)


def test_time_averaged_and_turbulence_fields_are_read_when_the_case_has_them(
    tmp_path: Path,
) -> None:
    """A transient run's `UMean` (with its derived `UMean_mag`) and a turbulence model's `nut` are
    read when present; a case without them (the test above) reads exactly `U`, `U_mag` and `p`."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_fields, units_for

    case = tmp_path / "hex"
    write_case(case)
    u_text = (case / "0" / "U").read_text()
    (case / "0" / "UMean").write_text(
        u_text.replace("object      U;", "object      UMean;").replace("(1 0 0)", "(0 2 0)")
    )
    p_text = (case / "0" / "p").read_text()
    (case / "0" / "nut").write_text(
        p_text.replace("object      p;", "object      nut;")
        .replace("[0 2 -2 0 0 0 0]", "[0 2 -1 0 0 0 0]")
        .replace("uniform 5", "uniform 3")
    )
    data, header = extract_fields(case, surfaces_header_for(case), None)
    names = list(header["patches"][0]["fields"])
    assert names[:5] == ["U", "U_mag", "p", "UMean", "UMean_mag"]
    assert "nut" in names
    _, got = unpack_fields(data)
    wall = got["a"]
    assert np.allclose(wall["UMean"], [0.0, 2.0, 0.0])
    assert np.allclose(wall["UMean_mag"], 2.0)
    assert np.allclose(wall["nut"], 3.0)
    assert header["units"]["UMean_mag"] == "m/s" and header["units"]["nut"] == "m2/s"
    assert units_for(None)["kMean"] == "m2/s2"
