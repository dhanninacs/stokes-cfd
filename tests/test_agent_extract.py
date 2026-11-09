"""The surface extractor: pure numpy, the artifact format, and the chunk API (T-147a, T-209).

**The numbers here are measured from a real cavity mesh**, not invented: the VTK section below
builds a
one-hex case by hand and the engine test (`tests/engine/test_agent_extract_engine.py`) runs a real
`blockMesh` and checks the patches the brief names.

The three properties worth naming:

* **A fan, not a triangle filter.** `triangulate_fan` keeps the original polygon edges, which is
what lets
  `unique_edges` produce the mesh's real edges instead of a filter's diagonals.
* **The artifact is one buffer with per-patch offsets**, so a reader can take one patch without
the rest.
* **A chunk is bounded and a name is checked**, because both come from a client.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes_agent.extract import (
    MAGIC,
    MAX_CHUNK,
    NAME_RE,
    artifact_bytes,
    pack_artifact,
    read_chunk,
    triangulate_fan,
    unique_edges,
    unpack_artifact,
    write_artifact,
)

# ---------------------------------------------------------------- the pure geometry


def _can_symlink() -> bool:
    """Whether this account may create symlinks (Windows needs a privilege or developer mode)."""
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        try:
            (Path(folder) / "link").symlink_to(Path(folder) / "target")
        except (OSError, NotImplementedError):
            return False
    return True


needs_symlinks = pytest.mark.skipif(
    not _can_symlink(), reason="symlinks need a privilege on this host"
)


def test_a_quad_makes_two_triangles() -> None:
    """Polygon `v0..v3` gives `(v0,v1,v2)` and `(v0,v2,v3)` — a fan from the first vertex."""
    offsets = np.array([0, 4], dtype="uint32")
    connectivity = np.array([0, 1, 2, 3], dtype="uint32")
    assert triangulate_fan(offsets, connectivity).tolist() == [[0, 1, 2], [0, 2, 3]]


def test_a_pentagon_makes_three_triangles() -> None:
    offsets = np.array([0, 5], dtype="uint32")
    connectivity = np.array([0, 1, 2, 3, 4], dtype="uint32")
    assert triangulate_fan(offsets, connectivity).tolist() == [
        [0, 1, 2],
        [0, 2, 3],
        [0, 3, 4],
    ]


def test_a_quad_has_four_edges_and_the_ring_is_closed() -> None:
    """**The wrap from the last corner back to the first is a real edge** — a polygon is
    closed, and it is
    the one pair a naive `+1` index runs off the end for."""
    offsets = np.array([0, 4], dtype="uint32")
    connectivity = np.array([0, 1, 2, 3], dtype="uint32")
    edges = unique_edges(offsets, connectivity).tolist()
    assert edges == [[0, 1], [0, 3], [1, 2], [2, 3]]


def test_two_quads_sharing_an_edge_have_seven_edges() -> None:
    """The brief's number. Two quads share `(1,2)`, so the shared edge appears **once**: 8 - 1
    = 7."""
    offsets = np.array([0, 4, 8], dtype="uint32")
    connectivity = np.array([0, 1, 2, 3, 1, 4, 5, 2], dtype="uint32")
    edges = unique_edges(offsets, connectivity)
    assert len(edges) == 7
    assert [1, 2] in edges.tolist()


def test_edges_are_sorted_within_every_pair() -> None:
    """So `(3,1)` and `(1,3)` are the same edge and are counted once."""
    offsets = np.array([0, 3], dtype="uint32")
    connectivity = np.array([3, 1, 2], dtype="uint32")
    assert unique_edges(offsets, connectivity).tolist() == [[1, 2], [1, 3], [2, 3]]


def test_empty_input_is_an_empty_array_not_an_error() -> None:
    empty = np.zeros(0, dtype="uint32")
    assert triangulate_fan(empty, empty).shape == (0, 3)
    assert unique_edges(empty, empty).shape == (0, 2)


def test_the_functions_are_loops_free_over_faces() -> None:
    """**This is the property that makes a million-cell surface tractable.** A hundred
    thousand polygons go
    through in well under a second, which a per-face Python loop could not do."""
    import time

    count = 100_000
    offsets = (np.arange(count + 1, dtype="uint32")) * 4
    connectivity = np.repeat(np.arange(count, dtype="uint32") * 4, 4) + np.tile(
        np.arange(4, dtype="uint32"), count
    )
    start = time.monotonic()
    triangles = triangulate_fan(offsets, connectivity)
    elapsed = time.monotonic() - start
    assert len(triangles) == count * 2
    assert elapsed < 2.0, f"{count} polygons took {elapsed:.2f}s; that is a Python loop somewhere"


# ---------------------------------------------------------------- the artifact format


def patch(name: str, points: list[list[float]], faces: int = 1, kind: str = "patch") -> dict:
    """One patch with a quad, for the format tests."""
    positions = np.array(points, dtype="float32")
    return {
        "name": name,
        "patch_type": kind,
        "points": positions,
        "triangles": np.array([[0, 1, 2], [0, 2, 3]], dtype="uint32"),
        "edges": np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="uint32"),
        "faces": faces,
    }


def test_the_magic_and_the_header_layout() -> None:
    """`STKMSH1\\0`, then a u32 header length, then the JSON padded to a multiple of four."""
    data, header = pack_artifact([patch("a", [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])])
    assert data[:8] == MAGIC
    length = int.from_bytes(data[8:12], "little")
    assert length % 4 == 0, "the header is padded to four bytes"
    assert json.loads(data[12 : 12 + length].decode("utf-8"))["version"] == 1
    assert header["units"] == "m"


def test_the_header_counts_and_offsets() -> None:
    """Every patch's counts and the offset of its data, which is what a reader seeks to."""
    data, header = pack_artifact(
        [
            patch("a", [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], faces=1, kind="wall"),
            patch("b", [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], faces=5, kind="empty"),
        ]
    )
    first, second = header["patches"]
    assert (first["name"], first["points"], first["triangles"], first["edges"]) == ("a", 4, 2, 4)
    assert first["patch_type"] == "wall" and second["patch_type"] == "empty"
    assert first["index"] == 0 and second["index"] == 1
    assert first["offset"] == 0
    # a's block: 4*3 float32 + 2*3 uint32 + 4*2 uint32 = 48 + 24 + 32 = 104
    assert second["offset"] == 104
    assert len(data) == 12 + int.from_bytes(data[8:12], "little") + 208


def test_the_bbox_spans_every_patch() -> None:
    _, header = pack_artifact(
        [
            patch("a", [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]),
            patch("b", [[-2, 0, 1], [3, 0, 1], [3, 1, 1], [-2, 1, 1]]),
        ]
    )
    assert header["bbox"]["min"] == [-2.0, 0.0, 0.0]
    assert header["bbox"]["max"] == [3.0, 1.0, 1.0]


def test_a_reader_recovers_the_arrays_exactly() -> None:
    """**Byte for byte.** The offsets and counts in the header are the whole contract; a reader that
    follows them gets what the writer put there, with no interpretation."""
    positions = np.array([[0, 0, 0], [0.5, 0, 0], [0.5, 0.25, 0], [0, 0.25, 0]], dtype="float32")
    edges = np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="uint32")
    original = [
        {
            "name": "a",
            "patch_type": "wall",
            "points": positions,
            "triangles": np.array([[0, 1, 2], [0, 2, 3]], dtype="uint32"),
            "edges": edges,
            "faces": 1,
        }
    ]
    data, _ = pack_artifact(original)
    _, arrays = unpack_artifact(data)
    assert np.array_equal(arrays["a"]["points"], positions)
    assert np.array_equal(arrays["a"]["edges"], edges)
    assert np.array_equal(arrays["a"]["triangles"], original[0]["triangles"])


def test_a_bad_magic_is_refused() -> None:
    with pytest.raises(ValueError):
        unpack_artifact(b"NOPE1234" + b"\0" * 32)


# ---------------------------------------------------------------- writing and chunks


def test_an_artifact_round_trips_in_chunks(tmp_path: Path) -> None:
    """The brief's case: 20 MiB in three chunks, and the sha256 of what was assembled matches."""
    payload = bytes(range(256)) * (20 * 1024 * 1024 // 256)
    assert len(payload) == 20 * 1024 * 1024
    written = write_artifact(tmp_path, "big.stkmsh", payload)
    assert written["size"] == len(payload)
    assert written["sha256"] == hashlib.sha256(payload).hexdigest()

    chunk = MAX_CHUNK
    assembled = b""
    offsets = [0, chunk, 2 * chunk]
    for offset in offsets:
        reply = read_chunk(tmp_path, "big.stkmsh", offset, chunk)
        assert reply["size"] == len(payload)
        assert reply["offset"] == offset
        assembled += reply["data"]
        assert reply["sha256"] == written["sha256"], "the digest is of the whole file"
    assert assembled == payload


def test_the_index_records_every_artifact(tmp_path: Path) -> None:
    write_artifact(tmp_path, "one.stkmsh", b"a" * 10)
    write_artifact(tmp_path, "two.stkmsh", b"b" * 20)
    index = json.loads((Path(tmp_path) / "artifacts" / "index.json").read_text())
    assert set(index) == {"one.stkmsh", "two.stkmsh"}
    assert index["two.stkmsh"]["size"] == 20
    assert index["two.stkmsh"]["sha256"] == hashlib.sha256(b"b" * 20).hexdigest()


@pytest.mark.parametrize("name", ("../x", "a/b", "", ".", "A" * 5, "x" * 65, "with space"))
def test_a_bad_name_is_refused(tmp_path: Path, name: str) -> None:
    """**The name comes from a client**, so it is checked twice: the pattern, and that the
    resolved path
    is inside the artifacts folder."""
    assert not NAME_RE.match(name)
    with pytest.raises(ValueError):
        write_artifact(tmp_path, name, b"x")
    with pytest.raises(ValueError):
        read_chunk(tmp_path, name, 0, 10)


def test_a_chunk_over_the_limit_is_refused(tmp_path: Path) -> None:
    write_artifact(tmp_path, "a.stkmsh", b"x" * 100)
    with pytest.raises(ValueError):
        read_chunk(tmp_path, "a.stkmsh", 0, MAX_CHUNK + 1)


def test_an_offset_past_the_end_is_refused(tmp_path: Path) -> None:
    write_artifact(tmp_path, "a.stkmsh", b"x" * 100)
    with pytest.raises(ValueError):
        read_chunk(tmp_path, "a.stkmsh", 101, 10)


def test_a_negative_offset_or_length_is_refused(tmp_path: Path) -> None:
    write_artifact(tmp_path, "a.stkmsh", b"x" * 100)
    for offset, length in ((-1, 10), (0, -1)):
        with pytest.raises(ValueError):
            read_chunk(tmp_path, "a.stkmsh", offset, length)


def test_a_missing_artifact_is_a_file_error(tmp_path: Path) -> None:
    """`FileNotFoundError`, so the server maps it to `agent.not_found` rather than to bad
    parameters."""
    with pytest.raises(FileNotFoundError):
        read_chunk(tmp_path, "nope.stkmsh", 0, 10)


def test_reading_the_whole_artifact_matches_the_chunks(tmp_path: Path) -> None:
    payload = b"STKMSH1\0" + bytes(range(256)) * 40
    write_artifact(tmp_path, "a.stkmsh", payload)
    assert artifact_bytes(tmp_path, "a.stkmsh") == payload


# ---------------------------------------------------------------- the VTK reader


def write_hex_case(case: Path) -> None:
    """A one-hex OpenFOAM case, written by hand in the format blockMesh produces.

    Eight points, six quad faces, `owner`/`neighbour`, and a boundary with patch `a` (one face)
    and patch
    `b` (five faces). The header and layout were checked against a `polyMesh` that blockMesh
    wrote on
    thinkyboy (`/tmp/vtkprobe/case`).
    """
    mesh = case / "constant" / "polyMesh"
    mesh.mkdir(parents=True, exist_ok=True)
    (case / "system").mkdir(parents=True, exist_ok=True)
    head = """/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Website:  https://openfoam.org                  |
|   \\\\  /    A nd           | Version:  14                                    |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
"""
    (mesh / "points").write_text(
        head
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
    # Six quads. Face 0 is patch `a`; faces 1-5 are patch `b`.
    (mesh / "faces").write_text(
        head
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
        head
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
        head
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
        head
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


def test_a_hand_written_case_extracts(tmp_path: Path) -> None:
    """**VTK reads a case we wrote**, so the extractor is tested without a binary fixture and
    without
    OpenFOAM: one hex, two patches, and the counts the brief states."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_surfaces

    case = tmp_path / "hex"
    write_hex_case(case)
    data, header = extract_surfaces(case)
    assert data[:8] == MAGIC
    by_name = {entry["name"]: entry for entry in header["patches"]}
    assert set(by_name) == {"a", "b"}
    # Patch `a` is one quad: 1 face, 2 triangles, 4 edges.
    assert by_name["a"]["faces"] == 1
    assert by_name["a"]["triangles"] == 2
    assert by_name["a"]["edges"] == 4
    # Patch `b` is five quads: 5 faces, 10 triangles.
    assert by_name["b"]["faces"] == 5
    assert by_name["b"]["triangles"] == 10
    # **The types come from `constant/polyMesh/boundary`**: VTK does not expose them.
    assert by_name["a"]["patch_type"] == "wall"
    assert by_name["b"]["patch_type"] == "empty"


def test_two_reads_give_the_same_points_byte_for_byte(tmp_path: Path) -> None:
    """**T-215 relies on this.** A reader that reorders points between two reads of the same
    case would
    make cached field values line up with the wrong vertices, and nothing else would notice."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_surfaces

    case = tmp_path / "hex"
    write_hex_case(case)
    first, _ = extract_surfaces(case)
    second, _ = extract_surfaces(case)
    assert first == second, "the artifact differs between two reads of one case"


def test_the_case_marker_is_created_when_missing(tmp_path: Path) -> None:
    """`vtkOpenFOAMReader` needs a `case.foam` file; the extractor makes one rather than
    demanding it."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_surfaces

    case = tmp_path / "hex"
    write_hex_case(case)
    assert not (case / "case.foam").exists()
    extract_surfaces(case)
    assert (case / "case.foam").exists()


def test_open_case_is_the_shared_entry_point(tmp_path: Path) -> None:
    """**T-215 calls this too**, so its signature is a contract: patches, internal, fields."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import open_case

    case = tmp_path / "hex"
    write_hex_case(case)
    reader = open_case(case, patches=True, internal=False)
    assert reader.GetOutput().GetNumberOfBlocks() >= 1
    both = open_case(case, patches=True, internal=True)
    assert both.GetOutput().GetNumberOfBlocks() >= 1


# ---------------------------------------------------------------- what an adversarial review found
#
# These two test classes exist because a mutation-testing pass showed the suite did not cover
# them: a
# non-atomic `write_artifact` and an indentation-sensitive `_patch_types` both survived every test.


@pytest.mark.parametrize("indent", ["    ", "  ", "\t", ""])
def test_the_patch_types_survive_any_legal_indentation(tmp_path: Path, indent: str) -> None:
    """**OpenFOAM does not promise an indent.** The first version of `_patch_types` anchored
    on exactly four
    spaces, so a legal re-indent made every patch report `patch_type="patch"` — a
    wrong-but-plausible
    answer with no error anywhere. Real `blockMesh` output happens to use four, which is why nothing
    caught it.
    """
    from stokes_agent.extract import _patch_types

    mesh = tmp_path / "constant" / "polyMesh"
    mesh.mkdir(parents=True)
    body = (
        "FoamFile\n{\n    class polyBoundaryMesh;\n}\n\n2\n(\n"
        + indent
        + "walls\n"
        + indent
        + "{\n"
        + indent * 2
        + "type            wall;\n"
        + indent
        + "}\n"
        + indent
        + "front\n"
        + indent
        + "{\n"
        + indent * 2
        + "type            empty;\n"
        + indent
        + "}\n)\n"
    )
    (mesh / "boundary").write_text(body)
    assert _patch_types(tmp_path) == {"walls": "wall", "front": "empty"}


def test_a_type_word_in_a_comment_is_not_read_as_a_patch_type(tmp_path: Path) -> None:
    """The block header contains the word `type`-like prose; a scan that did not strip
    comments could pick a
    type out of it."""
    from stokes_agent.extract import _patch_types

    mesh = tmp_path / "constant" / "polyMesh"
    mesh.mkdir(parents=True)
    (mesh / "boundary").write_text(
        "/*--------------------------------*- C++ -*\n"
        "| this comment mentions type nonsense;\n"
        "\\*-----------------------------------------*/\n"
        "// and this line says type wrong;\n"
        "1\n(\n    a\n    {\n        type            symmetry;\n    }\n)\n"
    )
    assert _patch_types(tmp_path) == {"a": "symmetry"}


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="os.replace over an open file is refused on Windows; the agent is POSIX-only",
)
def test_the_write_is_atomic_so_a_reader_never_sees_a_partial_file(tmp_path: Path) -> None:
    """**A mutation that wrote straight to the target survived the whole suite**, so nothing
    covered this.

    A reader racing the writer must see either the previous file or the complete new one — never
    a prefix.
    The writer is checked by looking for the temp file's absence and the content's completeness
    after a
    burst of writes while a reader loop runs.
    """
    import threading

    payload = b"A" * 200_000
    replacement = b"B" * 200_000
    write_artifact(tmp_path, "race.stkmsh", payload)

    stop = threading.Event()
    seen: list[int] = []
    wrong: list[bytes] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                got = (Path(tmp_path) / "artifacts" / "race.stkmsh").read_bytes()
            except FileNotFoundError:
                continue
            seen.append(len(got))
            if got not in (payload, replacement):
                wrong.append(got[:64])

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        for _ in range(40):
            write_artifact(tmp_path, "race.stkmsh", replacement if _ % 2 else payload)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not wrong, f"a reader saw a partial file: {wrong[0][:32]!r}"
    assert seen, "the reader never ran, so this proves nothing"


def test_the_temp_file_does_not_survive_the_write(tmp_path: Path) -> None:
    """A leftover `.name.tmp` would be picked up by anything listing the folder, and the
    brief's index is
    the folder's own record."""
    write_artifact(tmp_path, "a.stkmsh", b"x" * 100)
    left = [p.name for p in (Path(tmp_path) / "artifacts").iterdir() if p.name.startswith(".")]
    assert left == [], f"the temp file was left behind: {left}"


@needs_symlinks
def test_a_symlinked_artifacts_folder_cannot_escape_the_job(tmp_path: Path) -> None:
    """**A real hole, found by a reviewer, now closed.**

    When `<job>/artifacts` is a symlink pointing outside the job, the first containment check
    resolved
    *both* sides — so it compared a path against its own resolution and always agreed, and the
    write landed
    outside the job entirely. The check that means something is whether the resolved artifacts
    folder is
    still under the resolved job.
    """
    job = tmp_path / "job"
    job.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (job / "artifacts").symlink_to(outside)

    # **The message is asserted, not just the type.** `read_chunk` on a name that does not exist
    # raises `FileNotFoundError`, which is not a `ValueError` — so a bare
    # `pytest.raises(ValueError)` would pass for the wrong reason here only if containment ran first
    # by accident. Pinning the message says which refusal happened.
    with pytest.raises(ValueError, match="not inside the job"):
        write_artifact(job, "escaped.stkmsh", b"x" * 10)
    with pytest.raises(ValueError, match="not inside the job"):
        read_chunk(job, "escaped.stkmsh", 0, 5)
    with pytest.raises(ValueError, match="not inside the job"):
        artifact_bytes(job, "escaped.stkmsh")
    assert list(outside.iterdir()) == [], "something was written outside the job"


@needs_symlinks
def test_a_symlinked_artifact_file_inside_the_folder_is_refused(tmp_path: Path) -> None:
    """A link *inside* the folder from a legal name to an outside file is a different shape of
    the same
    attack, and it is caught by the same rule."""
    job = tmp_path / "job"
    folder = job / "artifacts"
    folder.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("not yours")
    (folder / "innocent.stkmsh").symlink_to(secret)

    with pytest.raises(ValueError, match="symbolic link"):
        read_chunk(job, "innocent.stkmsh", 0, 5)


@needs_symlinks
def test_a_symlinked_index_is_refused(tmp_path: Path) -> None:
    """**The fifth shape of the same escape, and the one a hardcoded name invites.**

    `index.json` is the only path this module writes whose name is not a client's, so it is the one
    a symlink can be planted at by name. The first version built that path by hand and wrote it with
    no check, so the write escaped while the artifact beside it was correctly refused. Every path
    this module writes now comes from `_contained`.
    """
    job = tmp_path / "job"
    folder = job / "artifacts"
    folder.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text('{"something": "important"}')
    (folder / "index.json").symlink_to(victim)

    with pytest.raises(ValueError, match="symbolic link"):
        write_artifact(job, "a.stkmsh", b"x" * 10)
    assert victim.read_text() == '{"something": "important"}', "the victim file was overwritten"


def test_the_index_is_still_written_for_a_normal_job(tmp_path: Path) -> None:
    """The guard must not break the ordinary path: `index.json` is a legal name and is written."""
    write_artifact(tmp_path, "a.stkmsh", b"x" * 10)
    index = Path(tmp_path) / "artifacts" / "index.json"
    assert index.is_file()
    assert not index.is_symlink()
    assert "a.stkmsh" in json.loads(index.read_text())


# ---------------------------------------------------------------- the cache lookup


def test_a_cache_lookup_creates_nothing(tmp_path: Path) -> None:
    """**A read-only question must not make a directory.**

    `extract.surfaces` used `artifacts_dir` — which does `mkdir(parents=True, exist_ok=True)` — to
    ask whether an artifact already existed. Asking created the folder, and followed a symlinked
    `artifacts` while doing it.
    """
    from stokes_agent.extract import cached_artifact

    job = tmp_path / "job"
    job.mkdir()
    assert cached_artifact(job, "a.stkmsh") is None
    assert not (job / "artifacts").exists(), (
        "the lookup created the folder it was only asking about"
    )


@needs_symlinks
def test_a_containment_refusal_is_not_a_cache_miss(tmp_path: Path) -> None:
    """**An escape must not be swallowed as "no cache".**

    `cached_artifact` catches broad exceptions, and `_contained` refuses with `ValueError` — so an
    earlier version turned a symlinked index into a silent miss, after which the caller re-extracted
    and failed on the *write* with a message about the wrong file. The refusal is re-raised.
    """
    from stokes_agent.extract import cached_artifact

    job = tmp_path / "job"
    folder = job / "artifacts"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.json").write_text("{}")
    (folder / "index.json").symlink_to(outside / "index.json")

    with pytest.raises(ValueError, match="symbolic link"):
        cached_artifact(job, "a.stkmsh")


@needs_symlinks
def test_a_symlinked_ARTIFACT_is_not_swallowed_as_a_cache_miss(tmp_path: Path) -> None:
    """**The second containment call, which fixing only the first would have left hiding the same
    attack.**

    A valid index naming an entry whose file is a symlink is the same escape one level down. The
    index here is perfectly good — so the refusal can only come from the artifact's own check.
    """
    from stokes_agent.extract import cached_artifact

    job = tmp_path / "job"
    folder = job / "artifacts"
    folder.mkdir(parents=True)
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"not yours")
    (folder / "a.stkmsh").symlink_to(victim)
    (folder / "index.json").write_text(json.dumps({"a.stkmsh": {"size": 9, "sha256": "x"}}))

    with pytest.raises(ValueError, match="symbolic link"):
        cached_artifact(job, "a.stkmsh")


def test_a_client_supplied_climbing_name_is_refused_not_missed(tmp_path: Path) -> None:
    """The name comes from a client, so `../x` must be a refusal rather than a silent miss."""
    from stokes_agent.extract import cached_artifact

    with pytest.raises(ValueError, match="not a valid artifact name"):
        cached_artifact(tmp_path, "../../etc/passwd")


def test_a_corrupt_cache_is_a_miss_and_costs_only_a_reextract(tmp_path: Path) -> None:
    """A recorded entry whose file is gone, a truncated artifact and a non-JSON index all mean
    "no usable cache" — never an exception, because that would turn a corrupt cache into a failed
    command."""
    from stokes_agent.extract import cached_artifact

    job = tmp_path / "job"
    folder = job / "artifacts"
    folder.mkdir(parents=True)
    index = folder / "index.json"

    # An entry for a file that is not there.
    index.write_text(json.dumps({"ghost.stkmsh": {"size": 4, "sha256": "x"}}))
    assert cached_artifact(job, "ghost.stkmsh") is None

    # A file that is not an artifact.
    (folder / "trunc.stkmsh").write_bytes(b"not a stkmsh file")
    index.write_text(json.dumps({"trunc.stkmsh": {"size": 4, "sha256": "x"}}))
    assert cached_artifact(job, "trunc.stkmsh") is None

    # An index that is not JSON at all.
    index.write_text("{not json")
    assert cached_artifact(job, "trunc.stkmsh") is None


def test_the_cache_round_trips_what_the_writer_recorded(tmp_path: Path) -> None:
    """The positive case: what `write_artifact` records is what `cached_artifact` returns."""
    from stokes_agent.extract import cached_artifact

    data, _header = pack_artifact([patch("a", [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])])
    written = write_artifact(tmp_path, "a.stkmsh", data)
    found = cached_artifact(tmp_path, "a.stkmsh")
    assert found is not None
    assert found["size"] == written["size"] == len(data)
    assert found["sha256"] == written["sha256"]
    assert [entry["name"] for entry in found["patches"]] == ["a"]


@needs_symlinks
def test_the_refusal_happens_before_anything_is_created(tmp_path: Path) -> None:
    """**The ordering is the bug this pins.**

    `write_artifact` used to call `artifacts_dir`, which does `mkdir(parents=True, exist_ok=True)`,
    *before* checking containment — and it then used the directory `artifacts_dir` returned
    (unresolved) while the check had validated a different one (resolved). So the check passed and
    did not govern what was written. Reading back the two lines is what showed it; this test is what
    keeps it fixed.

    An artifact name is refused by `_contained` **before** any directory work, so a refusal leaves
    the outside directory exactly as it was — not even a stray `index.json`.
    """
    job = tmp_path / "job"
    job.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "witness.txt").write_text("untouched")
    (job / "artifacts").symlink_to(outside)

    # A **legal** name, so the only thing that can refuse it is the containment check — a bad name
    # would be refused earlier by the pattern and this test would prove nothing about ordering.
    with pytest.raises(ValueError, match="not inside the job"):
        write_artifact(job, "legal.stkmsh", b"x" * 10)
    assert sorted(p.name for p in outside.iterdir()) == ["witness.txt"], (
        "the refusal created something in the directory it refused to write to"
    )


# ---------------------------------------------------------------- FIXES-B B6: a chunk costs a chunk


class _CountingHandle:
    """Wraps a binary file handle and counts the bytes `read` hands out."""

    def __init__(self, handle: Any, counter: dict[str, int]) -> None:
        self._handle = handle
        self._counter = counter

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        self._counter["bytes"] += len(data)
        return data

    def __enter__(self) -> _CountingHandle:
        return self

    def __exit__(self, *exc: object) -> None:
        self._handle.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def test_read_chunk_reads_the_chunk_and_not_the_whole_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**B6.** It used to read and hash the whole artifact (up to 2 GiB) for every 8 MiB chunk. A
    counting wrapper on the artifact's file shows how much `read_chunk` really reads."""
    payload = bytes(range(256)) * (3 * 1024 * 1024 // 256)
    written = write_artifact(tmp_path, "big.stkmsh", payload)
    counter = {"bytes": 0}
    real_open = Path.open

    def counting_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_open(self, mode, *args, **kwargs)
        if self.name == "big.stkmsh" and "b" in mode and "r" in mode:
            return _CountingHandle(handle, counter)
        return handle

    monkeypatch.setattr(Path, "open", counting_open)
    reply = read_chunk(tmp_path, "big.stkmsh", 1_000_000, 4096)
    assert reply["data"] == payload[1_000_000 : 1_000_000 + 4096]
    assert reply["size"] == len(payload)
    assert reply["sha256"] == written["sha256"], "the digest is the one recorded at write time"
    assert counter["bytes"] <= 4096, f"read_chunk read {counter['bytes']} bytes for a 4096 chunk"


def test_a_digest_that_does_not_match_the_file_size_is_recomputed(tmp_path: Path) -> None:
    """The index entry is trusted only while its size is the file's: a replaced file must not be
    reported with the old file's digest."""
    write_artifact(tmp_path, "a.stkmsh", b"x" * 100)
    (Path(tmp_path) / "artifacts" / "a.stkmsh").write_bytes(b"y" * 50)
    reply = read_chunk(tmp_path, "a.stkmsh", 0, 10)
    assert reply["size"] == 50
    assert reply["sha256"] == hashlib.sha256(b"y" * 50).hexdigest()


@needs_symlinks
def test_open_case_refuses_a_symlinked_directory(tmp_path: Path) -> None:
    """**`touch()` follows a symlinked directory**, the same way `mkdir` followed a symlinked
    `artifacts`. Creating the marker through a link is a write at a path nobody validated."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import open_case

    real = tmp_path / "real"
    real.mkdir()
    (real / "constant" / "polyMesh").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)

    with pytest.raises(ValueError, match="symbolic link"):
        open_case(link, patches=True, internal=False)


@needs_symlinks
def test_open_case_refuses_a_symlinked_marker(tmp_path: Path) -> None:
    """**The dangling-link case, which `exists()` hides.**

    `Path.exists()` follows symlinks and returns `False` for a link whose target does not exist — so
    `if not marker.exists(): marker.touch()` would **create the link's target**, outside the case,
    with no error. This is escape shape #3 reached through `exists()` rather than a directory.
    """
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import open_case

    case = tmp_path / "hex"
    write_hex_case(case)
    victim = tmp_path / "somewhere-else.txt"
    (case / "case.foam").symlink_to(victim)
    assert not victim.exists(), "the link must dangle, or the test proves nothing"

    with pytest.raises(ValueError, match="symbolic link"):
        open_case(case, patches=True, internal=False)
    assert not victim.exists(), "the refusal created the link's target"


def test_open_case_still_creates_a_plain_marker(tmp_path: Path) -> None:
    """**The write is deliberate** — `test_the_case_marker_is_created_when_missing` asserts it —
    so the two checks must not have removed it."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import open_case

    case = tmp_path / "hex"
    write_hex_case(case)
    assert not (case / "case.foam").exists()
    open_case(case, patches=True, internal=False)
    assert (case / "case.foam").is_file()
    assert not (case / "case.foam").is_symlink()
