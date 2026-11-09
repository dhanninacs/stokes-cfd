"""Tags on imported parts, end to end through the import worker (ADR-0020 slice 1):
select, grow, check and export the parts of a two-solid STEP file; replay the journal into another
project and get the same STL bytes (the M1 gate's replay property); change the file, re-import,
and carry the tag over to the new face numbering with tag.rematch."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Journal, Session, replay
from stokes.geometry import worker
from stokes.geometry.errors import SelectionStale
from stokes.geometry.selection_cache import SelectionCache
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx

GROUP = "/geometry/parts/two"


def rotated_box() -> Any:  # Any: OCP shape
    """The bracket's box (0..10, 0..20, 0..30 mm) built along other axes: same shape, faces in
    another order."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    axes = gp_Ax2(gp_Pnt(10.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0), gp_Dir(0.0, 1.0, 0.0))
    return BRepPrimAPI_MakeBox(axes, 20.0, 10.0, 30.0).Shape()


def write_two(path: Path, box: Any) -> Path:  # Any: OCP shape
    pin = fx.moved(fx.cylinder(5.0, 40.0), 50.0, 0.0, 0.0)
    return fx.write_named([("bracket", box, fx.RED), ("pin", pin, fx.BLUE)], path)


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    worker.prewarm(3)
    yield write_two(tmp_path_factory.mktemp("cad") / "two.step", fx.box(10.0, 20.0, 30.0))
    worker.shutdown_pool()


def imported(folder: Path, source: Path) -> Session:
    session = Session(journal_path=folder / "journal.jsonl")
    session.execute("project.new", name="demo", path=str(folder / "demo"))
    session.execute("geometry.import_step", path=str(source), wait=True)
    return session


def test_select_grow_check_export_and_replay(source: Path, tmp_path: Path) -> None:
    first = imported(tmp_path / "first", source)
    bracket = first.execute("node.get", node=f"{GROUP}/bracket")
    assert bracket["params"]["faces"] == 6
    first.execute("tag.create", name="walls", members=[{"part": f"{GROUP}/bracket", "faces": [2]}])
    grown = first.execute("tag.grow", tag="walls", angle_deg=95.0)
    assert grown["added"] == 5 and grown["faces"] == 6
    pins = first.execute("tag.by_name", pattern="pin*")
    first.execute("tag.create", name="pin", kind="region", members=pins["members"])
    checked = first.execute("geometry.check", part=GROUP)
    assert checked["worst"] == "ok" and checked["checked"] == 2
    exported = first.execute("geometry.export_stl", path="tri", tags=["walls", "pin"])
    triangles = {s["name"]: s["triangles"] for f in exported["files"] for s in f["solids"]}
    assert triangles["walls"] == 12 and triangles["pin"] > 100
    ascii_file = first.execute(
        "geometry.export_stl", path="all.stl", tags=["walls", "pin"], binary=False
    )["files"][0]["path"]

    records = Journal.read(tmp_path / "first" / "journal.jsonl")
    second = Session()
    second.execute("project.new", name="again", path=str(tmp_path / "second" / "again"))
    replay(second, records[1:])
    again = second.execute("geometry.export_stl", path="tri", tags=["walls", "pin"])
    for one, other in zip(exported["files"], again["files"], strict=True):
        assert Path(one["path"]).read_bytes() == Path(other["path"]).read_bytes()
    other_ascii = second.execute(
        "geometry.export_stl", path="all.stl", tags=["walls", "pin"], binary=False
    )["files"][0]["path"]
    assert Path(ascii_file).read_bytes() == Path(other_ascii).read_bytes()
    GeometryStore.of(first).close()
    GeometryStore.of(second).close()


def test_a_changed_file_is_rematched(source: Path, tmp_path: Path) -> None:
    changed = write_two(tmp_path / "changed.step", rotated_box())
    original = tmp_path / "two.step"
    original.write_bytes(source.read_bytes())
    session = imported(tmp_path, original)
    bracket = session.execute("node.get", node=f"{GROUP}/bracket")["id"]
    store = GeometryStore.of(session)
    mesh = store.mesh(bracket)
    assert mesh is not None
    # Pick the two faces at x = 0 and z = 30 mm by their fingerprints.
    prints = SelectionCache.of(session).fingerprints({bracket: mesh})[bracket]
    x_min = int(next(f for f in range(6) if prints.normal[f][0] < -0.9))
    z_max = int(next(f for f in range(6) if prints.normal[f][2] > 0.9))
    session.execute(
        "tag.create", name="walls", members=[{"part": bracket, "faces": [x_min, z_max]}]
    )
    original.write_bytes(changed.read_bytes())  # the CAD file changed
    store.reimport(original, {0: bracket})
    assert store.wait(bracket, timeout=180)
    after = store.mesh(bracket)
    assert after is not None and after is not mesh
    with pytest.raises(SelectionStale):
        session.execute("tag.add", tag="walls", members=[{"part": bracket, "faces": [0]}])
    result = session.execute("tag.rematch", tag="walls")
    assert result["unmatched"] == 0 and result["stale"] == []
    new_prints = SelectionCache.of(session).fingerprints({bracket: after})[bracket]
    expected = sorted(
        (
            int(next(f for f in range(6) if new_prints.normal[f][0] < -0.9)),
            int(next(f for f in range(6) if new_prints.normal[f][2] > 0.9)),
        )
    )
    found = session.execute("tag.members", tag="walls")["members"][0]["faces"]
    assert found == expected and expected != sorted((x_min, z_max))
    store.close()
