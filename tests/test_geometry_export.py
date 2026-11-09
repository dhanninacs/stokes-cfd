"""STL export (ADR-0020 decision 7): one solid per tag or part, in metres, named for
OpenFOAM; binary per file and ASCII multi-solid round trips; names; where files may go; refusals
for stale, missing and empty tags; STEP export through the shape cache (T-030): the node names,
several source files in one call, derived parts, and the refusals. Synthetic cubes; the STEP
tests need OCP and skip themselves without it."""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.api import Session
from stokes.errors import ValidationError
from stokes.geometry.errors import GeometryFileError, MeshOnly, SelectionError, SelectionStale
from stokes.geometry.export import solid_name
from stokes.geometry.files import SourceStamp
from tests.selection_support import STAMP, add_part, cube, new_session, part, put_mesh

_SOLID_RE = re.compile(rb"^solid (\S+)$", re.MULTILINE)


def read_binary(path: Path) -> tuple[bytes, np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    """Header, normals (T, 3) and corners (T, 3, 3) of a binary STL file."""
    data = path.read_bytes()
    (count,) = struct.unpack_from("<I", data, 80)
    assert len(data) == 84 + 50 * count
    records = np.frombuffer(
        data,
        dtype=np.dtype([("n", "<f4", (3,)), ("c", "<f4", (3, 3)), ("a", "<u2")]),
        count=count,
        offset=84,
    )
    return data[:80], records["n"].copy(), records["c"].copy()


def read_ascii(path: Path) -> dict[str, np.ndarray[Any, Any]]:
    """Solid name → corners (T, 3, 3) of an ASCII STL file."""
    text = path.read_text(encoding="ascii")
    solids: dict[str, np.ndarray[Any, Any]] = {}
    for block in re.finditer(r"^solid (\S+)\n(.*?)^endsolid \1$", text, re.MULTILINE | re.DOTALL):
        vertices = [
            [float(v) for v in line.split()[1:]]
            for line in block.group(2).splitlines()
            if line.strip().startswith("vertex")
        ]
        solids[block.group(1)] = np.array(vertices).reshape(-1, 3, 3)
    return solids


@pytest.fixture
def parts(tmp_path: Path) -> tuple[Session, str, str]:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [0]}])
    session.execute(
        "tag.create",
        name="walls",
        members=[{"part": a, "faces": [[1, 5]]}, {"part": b, "faces": "all"}],
    )
    return session, a, b


def test_binary_files_per_selection_round_trip(
    parts: tuple[Session, str, str], tmp_path: Path
) -> None:
    session, _, _ = parts
    result = session.execute("geometry.export_stl", path="triSurface", tags=["inlet", "walls"])
    assert result["solids"] == 2 and result["triangles"] == 2 + 22 and result["units"] == "m"
    folder = tmp_path / "demo.stokes" / "triSurface"
    assert sorted(p.name for p in folder.iterdir()) == ["inlet.stl", "walls.stl"]
    files = {Path(f["path"]).name: f for f in result["files"]}
    assert files["walls.stl"]["solids"] == [{"name": "walls", "triangles": 22, "faces": 11}]
    header, normals, corners = read_binary(folder / "inlet.stl")
    assert not header.startswith(b"solid") and b"inlet" in header
    assert corners.shape == (2, 3, 3) and np.allclose(corners[..., 0], 0.0)  # face 0 is x = 0
    assert np.allclose(normals, [[-1.0, 0.0, 0.0]] * 2)
    _, _, walls = read_binary(folder / "walls.stl")
    assert walls.shape[0] == 22 and walls.max() == pytest.approx(0.03)  # metres
    again = session.execute("geometry.export_stl", path="again", tags=["inlet", "walls"])
    for first, second in zip(result["files"], again["files"], strict=True):
        assert Path(first["path"]).read_bytes() == Path(second["path"]).read_bytes()


def test_one_ascii_file_holds_named_solids(parts: tuple[Session, str, str], tmp_path: Path) -> None:
    session, _, _ = parts
    result = session.execute(
        "geometry.export_stl",
        path="case/all.stl",
        tags=["walls"],
        parts=["/geometry/parts"],
        binary=False,
    )
    (written,) = result["files"]
    path = Path(written["path"])
    assert path == (tmp_path / "demo.stokes" / "case" / "all.stl").resolve()
    solids = read_ascii(path)
    assert {name: c.shape[0] for name, c in solids.items()} == {"walls": 22, "a": 12, "b": 12}
    assert _SOLID_RE.findall(path.read_bytes()) == [b"walls", b"a", b"b"]
    assert np.allclose(solids["b"].min(axis=(0, 1)), [0.02, 0.0, 0.0])
    with pytest.raises(ValidationError, match="binary STL file holds one solid"):
        session.execute("geometry.export_stl", path="two.stl", tags=["inlet", "walls"])
    single = session.execute("geometry.export_stl", path="inlet.stl", tags=["inlet"])
    assert single["files"][0]["bytes"] == 84 + 50 * 2


def test_solid_names_are_openfoam_words() -> None:
    taken: set[str] = set()
    assert [solid_name(n, taken) for n in ("inlet", "Inlet", "inlet", "2nd wall", "")] == [
        "inlet",
        "Inlet_2",
        "inlet_3",
        "s_2nd_wall",
        "solid",
    ]
    taken = set()
    assert solid_name("x" * 100, taken) == "x" * 64


def test_parts_with_one_name_in_two_groups(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    for group in ("left", "right"):
        add_part(session, "body", part(cube(0.01)), parent=f"/geometry/parts/{group}")
    result = session.execute("geometry.export_stl", path="out", parts=["/geometry/parts"])
    assert sorted(Path(f["path"]).name for f in result["files"]) == ["body.stl", "body_2.stl"]


def test_files_stay_inside_the_project_or_the_chosen_folder(
    parts: tuple[Session, str, str], tmp_path: Path
) -> None:
    session, _, _ = parts
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for path in (str(outside / "x.stl"), "../elsewhere/x.stl"):
        with pytest.raises(ValidationError, match="outside"):
            session.execute("geometry.export_stl", path=path, tags=["inlet"])
    with pytest.raises(ValidationError, match="control characters"):
        session.execute("geometry.export_stl", path="x\0.stl", tags=["inlet"])
    chosen = session.execute(
        "geometry.export_stl", path="x.stl", tags=["inlet"], folder=str(outside)
    )
    assert Path(chosen["files"][0]["path"]) == (outside / "x.stl").resolve()
    with pytest.raises(ValidationError, match="outside"):
        session.execute(
            "geometry.export_stl",
            path=str(tmp_path / "y.stl"),
            tags=["inlet"],
            folder=str(outside),
        )
    with pytest.raises(ValidationError, match="does not exist"):
        session.execute(
            "geometry.export_stl", path="x.stl", tags=["inlet"], folder=str(tmp_path / "nope")
        )
    (outside / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(ValidationError, match=r"not an \.stl file"):
        session.execute(
            "geometry.export_stl", path="notes.txt", tags=["inlet"], folder=str(outside)
        )
    link = outside / "link"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # a symbolic link needs a privilege on Windows
    with pytest.raises(ValidationError, match="outside"):
        session.execute(
            "geometry.export_stl", path="link/z.stl", tags=["inlet"], folder=str(outside)
        )


def test_a_new_project_can_export_WITHOUT_being_asked_for_a_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Rewritten for the new contract** (claude-win2, reading 1). This test used to assert that an
    unsaved project was REFUSED with "no folder yet" and needed an explicit `folder=`. **There is no
    such thing as an unsaved project any more** - every project has a folder from `project.new` - so
    the refusal it was checking no longer exists.

    **The coverage it carried is kept**: an export still runs, still needs a project, and still
    produces the right triangle count. What changed is that the folder is the project's own rather
    than something the caller had to supply.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="scratch")
    add_part(session, "a", part(cube(0.01)))
    # **No `folder=` argument**: the project has one, which is the point of the change.
    done = session.execute("geometry.export_stl", path="a.stl", parts=["/geometry/parts/a"])
    assert done["triangles"] == 12
    # **And it landed inside the project**, not in a temporary directory.
    written = Path(session.project_path) / "a.stl"
    assert written.is_file(), sorted(p.name for p in Path(session.project_path).iterdir())


def test_stale_missing_and_empty_selections_are_refused(parts: tuple[Session, str, str]) -> None:
    session, a, b = parts
    session.execute("tag.create", name="nothing")
    with pytest.raises(SelectionError, match="no faces"):
        session.execute("geometry.export_stl", path="n.stl", tags=["nothing"])
    session.execute("parameter.set", node=a, name="faces", value=4)  # as if faces 4, 5 had gone
    with pytest.raises(SelectionError, match="no longer"):
        session.execute("geometry.export_stl", path="w.stl", tags=["walls"])
    session.execute("history.undo")
    newer = SourceStamp(STAMP.name, STAMP.size, STAMP.mtime_ns + 1)
    put_mesh(session, a, part(cube(0.01, name="a"), source=newer))
    with pytest.raises(SelectionStale):
        session.execute("geometry.export_stl", path="w.stl", tags=["walls"])
    assert session.execute("geometry.export_stl", path="b.stl", parts=[b])["triangles"] == 12
    with pytest.raises(ValidationError, match="give tags"):
        session.execute("geometry.export_stl", path="none.stl")


def test_step_export_needs_a_b_rep_and_a_step_name(parts: tuple[Session, str, str]) -> None:
    session, a, _ = parts
    # A synthetic part has a mesh but no source file and no cached B-rep: nothing to write.
    with pytest.raises(GeometryFileError) as info:
        session.execute("geometry.export_step", parts=[a], path="a.step")
    assert info.value.code == "geometry.unreadable" and "/geometry/parts/a" in info.value.message
    with pytest.raises(ValidationError, match=r"not a \.step"):
        session.execute("geometry.export_step", parts=[a], path="a.stl")
    with pytest.raises(ValidationError, match="outside"):
        session.execute("geometry.export_step", parts=[a], path="../a.step")


def test_step_export_refuses_a_mesh_only_part(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    part_id = add_part(session, "mesh", part(cube(0.01)))
    session.execute("parameter.set", node=part_id, name="family", value="mesh")
    with pytest.raises(MeshOnly) as info:
        session.execute("geometry.export_step", parts=[part_id], path="mesh.step")
    assert info.value.code == "geometry.mesh_only"
    assert not (tmp_path / "demo" / "mesh.step").exists()


# ---------------------------------------------------------------- STEP through the shape cache


@pytest.fixture(scope="module")
def cad(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Two single-solid STEP files (OCP needed; the calling tests skip without it)."""
    pytest.importorskip("OCP")
    from tests import geometry_fixtures as fx

    folder = tmp_path_factory.mktemp("cad")
    return {
        "left": fx.write_named([("left", fx.box(10.0, 20.0, 30.0), None)], folder / "left.step"),
        "right": fx.write_named([("right", fx.box(4.0, 5.0, 6.0), None)], folder / "right.step"),
    }


def imported(tmp_path: Path, name: str, sources: list[tuple[str, Path]]) -> Session:
    """A saved session with each ``(name, source)`` imported, waited for."""
    session = new_session(tmp_path, name=name)
    for part_name, source in sources:
        session.execute("geometry.import", path=str(source), name=part_name, wait=True)
    return session


def extents_m(path: Path) -> list[tuple[float, float, float]]:
    """The bounding boxes of a STEP file's solids, in metres, in file order."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    from stokes.geometry.step import read_step

    model = read_step(path)
    found: list[tuple[float, float, float]] = []
    for solid in model.solids:
        bounds = Bnd_Box()
        BRepBndLib.Add_s(solid.shape, bounds)
        lo, hi = bounds.CornerMin(), bounds.CornerMax()
        found.append(
            (
                (hi.X() - lo.X()) * model.unit_scale,
                (hi.Y() - lo.Y()) * model.unit_scale,
                (hi.Z() - lo.Z()) * model.unit_scale,
            )
        )
    return found


def test_export_writes_the_node_names_and_reads_no_source_file(
    cad: dict[str, Path], tmp_path: Path
) -> None:
    """A renamed part exports under its new name, and the cache spares the source read."""
    from stokes.geometry.shapes import part_spec
    from stokes.geometry.step import read_step
    from stokes.geometry.store import GeometryStore
    from stokes.geometry.worker import acquire

    session = imported(tmp_path, "named", [("left", cad["left"])])
    project = session.require_project()
    parts = project.find("/geometry/parts")
    assert parts is not None
    node_id = parts.children[0].id
    session.execute("node.rename", node=node_id, name="bracket")
    node = project.get(node_id)
    store = GeometryStore.of(session)
    spec = part_spec(node, store.shape_dir(), store.basis(node))
    target = tmp_path / "bracket.step"
    payload = acquire().export_step([spec], target, names=[node.name])
    assert payload["provider"] == {"source_reads": 0, "cache_hits": 1}
    assert payload["names"] == ["bracket"] and payload["solids"] == 1
    again = read_step(target)
    assert [s.name for s in again.solids] == ["bracket"] and again.units == "m"
    assert extents_m(target)[0] == pytest.approx((0.01, 0.02, 0.03), rel=1e-5)
    done = session.execute("geometry.export_step", parts=[node_id], path="cmd.step")
    assert done["names"] == ["bracket"] and done["solids"] == 1 and done["units"] == "m"
    assert [s.name for s in read_step(Path(done["file"])).solids] == ["bracket"]


def test_parts_of_two_source_files_export_in_one_call(cad: dict[str, Path], tmp_path: Path) -> None:
    from stokes.geometry.step import read_step

    session = imported(tmp_path, "two", [("left", cad["left"]), ("right", cad["right"])])
    done = session.execute("geometry.export_step", parts=["/geometry/parts"], path="both.step")
    assert done["solids"] == 2 and sorted(done["names"]) == ["left", "right"]
    assert sorted(s.name for s in read_step(Path(done["file"])).solids) == ["left", "right"]
    assert sorted(e[0] for e in extents_m(Path(done["file"]))) == pytest.approx(
        [0.004, 0.01], rel=1e-5
    )


def test_a_derived_part_exports(tmp_path: Path) -> None:
    """The gap T-030 closes: an operation's output part has no source file of its own."""
    from tests.operations_support import create_and_wait

    session = new_session(tmp_path, name="derived")
    info = create_and_wait(
        session,
        "primitive",
        params={"shape": "box", "size": [0.02, 0.02, 0.02], "position": [0.0, 0.0, 0.0]},
    )
    assert info["status"] == "ok", info["error"]
    (part_id,) = info["outputs"]
    node = session.require_project().get(part_id)
    done = session.execute("geometry.export_step", parts=[part_id], path="box.step")
    assert done["solids"] == 1 and done["names"] == [node.name] and done["units"] == "m"
    assert extents_m(Path(done["file"]))[0] == pytest.approx((0.02, 0.02, 0.02), rel=1e-5)
