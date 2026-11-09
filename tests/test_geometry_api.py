"""geometry.* commands end to end: import through Session.execute, one part per solid, events,
undo and redo, journal replay, the mesh cache and restore, the HTTP endpoint, the event-loop
binding, cancellation, errors and the CLI.

One import (a file of two named solids, so a group of two parts) is shared by most tests;
workers are started ahead, because each spends seconds importing OCP. Tests run in file order.
"""

from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("OCP")

from fastapi.testclient import TestClient

from stokes.api import Journal, Session, replay
from stokes.cli.main import run
from stokes.errors import ModelError, UnknownNode, ValidationError
from stokes.geometry import files, worker
from stokes.geometry.cache import forget_packs, pack_file, write_cache
from stokes.geometry.errors import CadUnavailable, GeometryFileError, GeometryUnsupported
from stokes.geometry.mesh import PartMesh, TriMesh
from stokes.geometry.store import GeometryStore, part_id_for
from stokes.geometry.transport import pack_part
from stokes.model import Project
from stokes.server.app import create_app
from tests import geometry_fixtures as fx
from tests.test_tree_patches import many_parts

TOKEN = "t0k3n"
GROUP = "/geometry/parts/two"


@dataclass
class Imported:
    session: Session
    store: GeometryStore
    group_id: str
    root: Path
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def part(self, name: str) -> Any:  # Any: a model Node
        return self.session.require_project().get(f"{GROUP}/{name}")


@pytest.fixture(scope="module")
def cad(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    worker.prewarm(4)  # the shared import, the replay, the restore and the event-loop test
    root = tmp_path_factory.mktemp("cad")
    garbage = root / "garbage.step"
    garbage.write_text("ISO-10303-21;\nHEADER;\nthis is not STEP\n", encoding="utf-8")
    yield {
        "two": fx.write_named(
            [
                ("bracket", fx.box(10.0, 20.0, 30.0), fx.RED),
                ("pin", fx.moved(fx.cylinder(5.0, 40.0), 50.0, 0.0, 0.0), fx.BLUE),
            ],
            root / "two.step",
        ),
        "box": fx.write_plain(fx.box(10.0, 20.0, 30.0), root / "box.step"),
        "garbage": garbage,
    }
    worker.shutdown_pool()


@pytest.fixture(scope="module")
def imported(cad: dict[str, Path], tmp_path_factory: pytest.TempPathFactory) -> Iterator[Imported]:
    root = tmp_path_factory.mktemp("api")
    session = Session(journal_path=root / "journal.jsonl")
    session.execute("project.new", name="demo", path=str(root / "demo"))
    events: list[tuple[str, dict[str, Any]]] = []
    session.events.subscribe("*", lambda e, d: events.append((e, d)))
    result = session.execute("geometry.import_step", path=str(cad["two"]))
    store = GeometryStore.of(session)
    assert store.wait(result["id"], timeout=180)
    yield Imported(session, store, result["id"], root, events)
    store.close()


def header_and_ids(data: bytes) -> tuple[dict[str, Any], list[np.ndarray[Any, Any]]]:
    """Header and per-group face ids of a version 2 buffer."""
    (length,) = struct.unpack_from("<I", data, 0)
    header = json.loads(data[4 : 4 + length].rstrip(b"\0"))
    ids = []
    for g in header["groups"]:
        base = 4 + length + g["offset"] + g["vertices"] * 24 + g["triangles"] * 12
        ids.append(np.frombuffer(data, "<u4", g["triangles"], base))
    return header, ids


def forbid_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> worker.GeometryWorker:
        raise AssertionError("no worker may start here")

    monkeypatch.setattr(worker, "acquire", refuse)


def wait_for(seen: list[Any], count: int, seconds: float = 60.0) -> None:
    deadline = time.monotonic() + seconds
    while len(seen) < count and time.monotonic() < deadline:
        time.sleep(0.05)


def test_a_file_of_two_solids_becomes_a_group_of_two_parts(imported: Imported) -> None:
    project = imported.session.require_project()
    group = project.get(imported.group_id)
    assert group.type == "group" and group.path == GROUP
    assert [c.name for c in group.children] == ["bracket", "pin"]
    bracket, pin = imported.part("bracket"), imported.part("pin")
    assert bracket.id == part_id_for(group.id, 0) and pin.id == part_id_for(group.id, 1)
    for part, solid, faces, color in ((bracket, 0, 6, "#cc331a"), (pin, 1, 3, "#1a33e6")):
        assert part.type == "part" and part.status == "ok"
        params = part.params
        assert params["solid"] == solid and params["faces"] == faces and params["units"] == "mm"
        assert params["color"] == color and params["triangles"] > 0 and params["visible"]
        assert params["source"] == str(Path(params["source"]).resolve())
        assert imported.store.status(part.id) == {"status": "ready"}
    assert bracket.params["bbox"] == pytest.approx([0, 0, 0, 0.01, 0.02, 0.03], abs=1e-6)
    assert pin.params["bbox"] == pytest.approx([0.045, -0.005, 0, 0.055, 0.005, 0.04], abs=1e-4)


def test_events_name_each_part(imported: Imported) -> None:
    bracket, pin = imported.part("bracket"), imported.part("pin")
    tree = [(e, d["path"]) for e, d in imported.events if e in ("node.added", "node.removed")]
    assert tree == [
        ("node.added", "/geometry/parts"),
        ("node.added", GROUP),  # the placeholder part
        ("node.removed", GROUP),
        ("node.added", GROUP),  # the group, same id
        ("node.added", f"{GROUP}/bracket"),
        ("node.added", f"{GROUP}/pin"),
    ]
    progress = [
        (d["node_id"], d["pass"], d["solid"], d["index"], d["count"])
        for e, d in imported.events
        if e == "geometry.import_progress"
    ]
    assert progress == [
        (imported.group_id, "read", None, 0, 0),  # the job started: the file is not read yet
        (imported.group_id, "read", None, 0, 2),  # the file is read: two solids to tessellate
        (bracket.id, "coarse", "bracket", 0, 2),
        (pin.id, "coarse", "pin", 1, 2),
        (bracket.id, "fine", "bracket", 0, 2),
        (pin.id, "fine", "pin", 1, 2),
    ]
    ready = [
        (d["node_id"], d["path"], d["pass"], d["complete"])
        for e, d in imported.events
        if e == "geometry.part_ready"
    ]
    assert ready == [
        (bracket.id, f"{GROUP}/bracket", "coarse", False),
        (pin.id, f"{GROUP}/pin", "coarse", False),
        (bracket.id, f"{GROUP}/bracket", "fine", True),
        (pin.id, f"{GROUP}/pin", "fine", True),
    ]
    changed = {d["node_id"] for e, d in imported.events if e == "node.changed"}
    assert changed == {bracket.id, pin.id}


def test_list_and_info_per_part_and_for_the_group(imported: Imported) -> None:
    session = imported.session
    listed = session.execute("geometry.list")
    assert [p["path"] for p in listed] == [f"{GROUP}/bracket", f"{GROUP}/pin"]
    assert all(p["import"] == {"status": "ready"} for p in listed)
    info = session.execute("geometry.info", node=f"{GROUP}/pin")
    assert info["type"] == "part" and info["cache"]["present"]
    assert [(s["name"], s["faces"], s["quality"]) for s in info["solids"]] == [("pin", 3, "fine")]
    group = session.execute("geometry.info", node=GROUP)
    assert group["type"] == "group" and group["import"] == {"status": "ready"}
    assert [p["name"] for p in group["parts"]] == ["bracket", "pin"] and group["faces"] == 9
    assert group["triangles"] == sum(p["params"]["triangles"] for p in listed)
    assert group["bbox"] == pytest.approx([0, -0.005, 0, 0.055, 0.02, 0.04], abs=1e-4)
    with pytest.raises(ValidationError):
        session.execute("geometry.info", node="/")


def test_http_endpoint_serves_one_part(imported: Imported) -> None:
    client = TestClient(create_app(imported.session, token=TOKEN, dist_dir=None))
    pin = imported.part("pin")
    url = f"/api/geometry/{pin.id}"
    assert client.get(url).status_code == 401
    body = client.get(f"{url}?token={TOKEN}").content
    assert body == imported.store.transport(pin.id)
    header, face_ids = header_and_ids(body)
    assert header["version"] == 2 and header["units"] == "m" and header["faces"] == 3
    assert [(g["name"], g["faceIds"], g["faces"], g["faceBase"]) for g in header["groups"]] == [
        ("pin", True, 3, 0)
    ]
    assert sorted(set(face_ids[0].tolist())) == [0, 1, 2]
    assert header["bbox"]["max"] == pytest.approx([0.055, 0.005, 0.04], abs=1e-4)
    for missing in (imported.group_id, "n_00000000", "not-an-id"):
        assert client.get(f"/api/geometry/{missing}?token={TOKEN}").status_code == 404
    demo = client.get(f"/api/geometry/demo?token={TOKEN}").content
    assert json.loads(demo[4 : 4 + struct.unpack_from("<I", demo)[0]].rstrip(b"\0"))["version"] == 1


def test_journal_replay_rebuilds_the_same_parts(imported: Imported, tmp_path: Path) -> None:
    records = Journal.read(imported.root / "journal.jsonl")
    # The import went in under its old name; the journal holds the current one (T-016).
    assert [r["command"] for r in records] == ["project.new", "geometry.import"]
    assert records[1]["params"]["node_id"] == imported.group_id
    fresh = Session()
    fresh.execute("project.new", name="again")
    replay(fresh, records[1:])
    store = GeometryStore.of(fresh)
    assert store.wait(imported.group_id, timeout=180)
    for name in ("bracket", "pin"):
        again = fresh.require_project().get(f"{GROUP}/{name}")
        original = imported.part(name)
        assert again.id == original.id and again.params == original.params
    # Imported before the project had a folder: saving it writes the caches it lacks.
    fresh.execute("project.save", path=str(tmp_path / "again"))
    for thread in threading.enumerate():
        if thread.name == "stokes-geometry-cache":
            thread.join(30)
    folder = tmp_path / "again.stokes" / "cache" / "geometry"
    for name in ("bracket", "pin"):
        part = imported.part(name)
        assert (folder / f"{part.id}.mesh").read_bytes() == store.transport(part.id)
    store.close()


def test_restore_reimports_only_the_part_without_a_cache(imported: Imported) -> None:
    imported.session.execute("project.save")
    folder = imported.root / "demo.stokes" / "cache" / "geometry"
    bracket, pin = imported.part("bracket"), imported.part("pin")
    # A part has no cache when neither its own file nor the pack holds it. An import now writes the
    # pack (the pin is in it), so the pack goes too; the bracket still has its own file.
    forget_packs(imported.root / "demo.stokes")
    pack_file(imported.root / "demo.stokes").unlink()
    (folder / f"{pin.id}.mesh").unlink()
    reopened = Session()
    reopened.execute("project.open", path=str(imported.root / "demo"))
    seen: list[tuple[str, str, bool]] = []
    reopened.events.subscribe(
        "geometry.part_ready", lambda e, d: seen.append((d["node_id"], d["pass"], d["complete"]))
    )
    store = GeometryStore.of(reopened)
    store.activate()  # restores on a background thread
    wait_for(seen, 3)
    assert store.wait(timeout=180)
    assert (bracket.id, "cache", True) in seen and (pin.id, "fine", True) in seen
    assert all(node != bracket.id or kind == "cache" for node, kind, _ in seen)
    again = reopened.require_project().get(pin.id)
    assert again.status == "ok" and again.params["triangles"] == pin.params["triangles"]
    assert (folder / f"{pin.id}.mesh").is_file()  # written again
    store.close()


def test_the_group_is_built_on_the_bound_event_loop(
    cad: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    loop_thread: list[int] = []

    def serve() -> None:
        loop_thread.append(threading.get_ident())
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    runner = threading.Thread(target=serve, daemon=True)
    runner.start()
    assert ready.wait(10)
    built_on: list[int] = []
    original = GeometryStore._build_group

    def spy(self: GeometryStore, *args: Any) -> Any:
        built_on.append(threading.get_ident())
        return original(self, *args)

    monkeypatch.setattr(GeometryStore, "_build_group", spy)
    session = Session()
    session.execute("project.new", name="looped", path=str(tmp_path / "looped"))
    store = GeometryStore.of(session)
    store.bind(loop.call_soon_threadsafe, loop_thread[0])
    part = session.execute("geometry.import_step", path=str(cad["two"]), name="asm")
    assert store.wait(part["id"], timeout=180)
    assert built_on == loop_thread
    tree = session.execute("node.tree", root="/geometry/parts/asm")
    assert [c["name"] for c in tree["children"]] == ["bracket", "pin"]

    async def blocked() -> None:
        store.wait(part["id"])

    with pytest.raises(ValidationError):
        asyncio.run_coroutine_threadsafe(blocked(), loop).result(10)
    loop.call_soon_threadsafe(loop.stop)
    runner.join(10)
    loop.close()
    store.close()


def test_cache_is_used_when_the_project_reopens(
    imported: Imported, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker.prewarm(2)  # for the failed import and the CLI test further down
    imported.session.execute("project.save")
    folder = imported.root / "demo.stokes" / "cache" / "geometry"
    parts = [imported.part("bracket"), imported.part("pin")]
    cached = {p.id: (folder / f"{p.id}.mesh").read_bytes() for p in parts}
    # The bracket's cache is the one the import wrote. The pin's was rewritten by the restore
    # above, which meshed the pin alone at its own deflection; in the full import it shared a
    # BRepMesh call with the bracket at the finer of the two, so those bytes differ.
    assert cached[parts[0].id] == imported.store.transport(parts[0].id)
    forbid_workers(monkeypatch)
    reopened = Session()
    reopened.execute("project.open", path=str(imported.root / "demo"))
    store = GeometryStore.of(reopened)
    seen: list[dict[str, Any]] = []
    reopened.events.subscribe("geometry.part_ready", lambda e, d: seen.append(d))
    store.activate()
    wait_for(seen, 2, 30)
    assert sorted(d["node_id"] for d in seen) == sorted(p.id for p in parts)
    assert all(d["pass"] == "cache" and d["complete"] for d in seen)
    for part in parts:
        assert store.transport(part.id) == cached[part.id]
    store.close()


def test_undo_and_redo_restore_everything_without_importing_again(
    imported: Imported, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, store = imported.session, imported.store
    parts = [imported.part("bracket"), imported.part("pin")]
    before = {p.id: store.transport(p.id) for p in parts}
    removed: list[str] = []
    session.events.subscribe("geometry.part_removed", lambda e, d: removed.append(d["node_id"]))
    assert session.execute("history.state")["undo"] == "import two"
    session.execute("history.undo")
    project = session.require_project()
    assert project.find(imported.group_id) is None and project.find("/geometry/parts") is None
    assert all(store.mesh(p.id) is None and project.find(p.id) is None for p in parts)
    assert sorted(removed) == sorted(p.id for p in parts)
    forbid_workers(monkeypatch)
    session.execute("history.redo")
    assert project.get(imported.group_id).type == "group"
    for part in parts:
        assert project.get(part.id).params["triangles"] > 0
        assert store.transport(part.id) == before[part.id]
        assert store.status(part.id) == {"status": "ready"}


def test_errors_are_typed_and_leave_no_node(
    imported: Imported, cad: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = imported.session
    count = len(session.execute("geometry.list"))
    text = tmp_path / "notes.txt"
    text.write_text("not a geometry source\n", encoding="utf-8")
    stl = tmp_path / "mesh.step"
    stl.write_text("solid nothing\nendsolid\n", encoding="utf-8")
    with pytest.raises(GeometryUnsupported) as info:
        session.execute("geometry.import_step", path=str(text))  # no format claims .txt
    assert info.value.code == "geometry.unsupported"
    for bad in (tmp_path / "missing.step", stl):
        with pytest.raises(GeometryFileError) as info:
            session.execute("geometry.import_step", path=str(bad))
        assert info.value.code == "geometry.unreadable"
    with pytest.raises(ModelError):
        session.execute("geometry.import_step", path=str(cad["two"]), name="two")
    with pytest.raises(ValidationError):
        session.execute("geometry.import_step", path=str(cad["two"]), name="2 bad")
    monkeypatch.setattr(files, "cad_available", lambda: False)
    with pytest.raises(CadUnavailable):
        session.execute("geometry.import_step", path=str(cad["two"]))
    with pytest.raises(CadUnavailable):
        session.execute("geometry.prepare")
    assert len(session.execute("geometry.list")) == count


def test_a_failed_import_marks_the_placeholder(imported: Imported, cad: dict[str, Path]) -> None:
    session, store = imported.session, imported.store
    failures: list[dict[str, Any]] = []
    session.events.subscribe("geometry.import_failed", lambda e, d: failures.append(d))
    part = session.execute("geometry.import_step", path=str(cad["garbage"]), wait=True)
    assert part["import"]["status"] == "failed"
    assert part["import"]["error"]["code"] == "geometry.unreadable"
    node = session.require_project().get(part["id"])
    assert node.type == "part" and node.status == "failed" and "garbage.step" in node.meta["error"]
    assert failures[-1]["node_id"] == part["id"] and failures[-1]["code"] == "geometry.unreadable"
    assert store.transport(part["id"]) is None
    session.execute("history.undo")
    assert session.require_project().find(part["id"]) is None


def test_cli_imports_a_single_solid_as_one_part(
    cad: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "cli"
    assert run(["new", str(project), "--name", "cli"]) == 0
    capsys.readouterr()
    code = run(["geometry", "import", str(cad["box"]), "--project", str(project), "--name", "blk"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, out
    assert out["type"] == "part" and out["path"] == "/geometry/parts/blk"
    assert out["import"] == {"status": "ready"} and out["params"]["solid"] == 0
    assert [(s["name"], s["faces"]) for s in out["solids"]] == [("solid_1", 6)]
    assert run(["get", str(project), "/geometry/parts/blk", "triangles"]) == 0
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert got["value"] == out["params"]["triangles"] == 12
    assert Path(out["cache"]["path"]).is_file()
    code = run(["geometry", "import", str(tmp_path / "missing.step")])
    err = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1 and err["error"]["code"] == "geometry.unreadable"


def test_cancel_import(imported: Imported, cad: dict[str, Path]) -> None:
    session, store = imported.session, imported.store
    part = session.execute("geometry.import_step", path=str(cad["box"]), name="boxed")
    assert part["import"]["status"] == "importing"
    assert session.execute("geometry.cancel_import", node=part["id"]) == {"cancelled": [part["id"]]}
    assert store.wait(part["id"], timeout=60)
    status = store.status(part["id"])
    assert status["status"] == "cancelled" and status["error"]["code"] == "geometry.cancelled"
    assert session.require_project().get(part["id"]).status == "failed"
    assert session.execute("geometry.cancel_import") == {"cancelled": []}


# ---------------------------------------------------------------- styling (ADR-0021, 4.1 to 4.2)
# No CAD kernel involved: the trees are plain part nodes, as tests/test_tree_patches.py builds
# them. Undo cases use parts made through project.add, which holds every default parameter
# (including opacity); the old-parts case below keeps a part without one on purpose.


def three_part_project() -> Project:
    project = Project.new("style")
    parts = project.add("/geometry", "group", "parts")
    group = project.add(parts.id, "group", "asm")
    for index in (1, 2, 3):
        project.add(group.id, "part", f"solid_{index}")
    return project


def test_style_sets_three_parts_in_one_undo_step(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "journal.jsonl")
    session.project = three_part_project()
    project = session.require_project()
    parts = [project.get(f"/geometry/parts/asm/solid_{i}") for i in (1, 2, 3)]
    seen: list[dict[str, Any]] = []
    session.events.subscribe("node.changed", lambda e, d: seen.append(d))
    result = session.execute("geometry.style", nodes=[p.id for p in parts], visible=False)
    assert result == {"changed": [p.id for p in parts], "unchanged": [], "resolved": 3}
    assert all(p.params["visible"] is False for p in parts)
    assert [d["node_id"] for d in seen] == [p.id for p in parts]
    assert all(d["param"] == "visible" for d in seen)
    assert session.execute("history.state")["undo"] == "style 3 nodes"
    assert [r["command"] for r in session.journal.records] == ["geometry.style"]
    assert session.execute("history.undo")["kind"] == "composite"
    assert all(p.params["visible"] is True for p in parts)  # one step undid all three
    assert not session.execute("history.state")["can_undo"]


def test_style_a_group_reaches_its_parts_and_a_tag_keeps_its_style(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "journal.jsonl")
    session.project = three_part_project()
    project = session.require_project()
    session.execute("tag.create", name="inlet", color="#336699")
    tag = project.get("/geometry/tags/inlet")
    refs = ["/geometry/parts", tag.id, "/geometry/parts/asm/solid_1"]  # solid_1 twice
    result = session.execute("geometry.style", nodes=refs, color="#FF8800", opacity=0.25)
    parts = [project.get(f"/geometry/parts/asm/solid_{i}") for i in (1, 2, 3)]
    assert result["resolved"] == 4 and sorted(result["changed"]) == sorted(
        [p.id for p in parts] + [tag.id]
    )
    for node in [*parts, tag]:
        assert node.params["color"] == "#ff8800" and node.params["opacity"] == 0.25
    again = session.execute("geometry.style", nodes=refs, color="#FF8800", opacity=0.25)
    assert again == {"changed": [], "unchanged": result["changed"], "resolved": 4}
    assert session.execute("history.state")["undo"] == "style 4 nodes"  # nothing new pushed
    session.execute("history.undo")
    assert all(p.params["color"] == "#c7ccd1" and p.params["opacity"] == 1.0 for p in parts)
    assert tag.params["color"] == "#336699" and tag.params["opacity"] == 1.0


def test_style_an_old_part_treats_an_absent_opacity_as_one(tmp_path: Path) -> None:
    """A part from a project saved before part.opacity has no such parameter; the command
    compares against the default, so setting one to 1.0 changes nothing."""
    session = Session(journal_path=tmp_path / "journal.jsonl")
    session.project = many_parts(1)  # its parts carry no opacity parameter
    part = session.require_project().get("/geometry/parts/asm/solid_1")
    assert "opacity" not in part.params
    same = session.execute("geometry.style", nodes=[part.id], opacity=1.0)
    assert same == {"changed": [], "unchanged": [part.id], "resolved": 1}
    assert "opacity" not in part.params  # the absent value was not materialised
    assert not session.execute("history.state")["can_undo"]
    lower = session.execute("geometry.style", nodes=[part.id], opacity=0.5)
    assert lower == {"changed": [part.id], "unchanged": [], "resolved": 1}
    assert part.params["opacity"] == 0.5
    session.execute("history.undo")  # the part never held it: undo takes it off again
    assert "opacity" not in part.params
    session.execute("history.redo")
    assert part.params["opacity"] == 0.5


def test_style_refuses_bad_input_and_leaves_the_tree_alone(tmp_path: Path) -> None:
    session = Session()
    session.project = three_part_project()
    solid = "/geometry/parts/asm/solid_1"
    for params in (
        {"nodes": [solid], "color": "red"},
        {"nodes": [solid], "color": "#GG0000"},
        {"nodes": [solid], "color": "#ff88"},
        {"nodes": [solid], "opacity": 1.5},
        {"nodes": [solid], "opacity": -0.1},
        {"nodes": [solid]},
        {"nodes": []},
        {"nodes": [1], "visible": False},
    ):
        with pytest.raises(ValidationError) as info:
            session.execute("geometry.style", **params)
        assert info.value.code == "validation"
    with pytest.raises(UnknownNode) as info:
        session.execute("geometry.style", nodes=["/geometry/parts/asm/missing"], visible=False)
    assert info.value.code == "model.unknown_node"
    with pytest.raises(ValidationError):
        session.execute("geometry.style", nodes=["/"], visible=False)  # the root is no target
    assert session.require_project().get(solid).params["visible"] is True
    assert not session.execute("history.state")["can_undo"]


def test_style_journal_replay_gives_the_same_tree(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    session = Session(journal_path=journal)
    session.project = three_part_project()
    session.execute("tag.create", name="inlet", color="#336699")
    session.execute(
        "geometry.style",
        nodes=["/geometry/parts/asm", "/geometry/tags/inlet"],
        visible=False,
        color="#aa5500",
        opacity=0.5,
    )
    records = Journal.read(journal)
    assert [r["command"] for r in records] == ["tag.create", "geometry.style"]
    fresh = Session()
    fresh.project = three_part_project()
    fresh.execute("tag.create", name="inlet", color="#336699")
    replay(fresh, [records[1]])
    original, again = session.require_project(), fresh.require_project()
    for path in ("solid_1", "solid_2", "solid_3"):
        assert (
            again.get(f"/geometry/parts/asm/{path}").params
            == original.get(f"/geometry/parts/asm/{path}").params
        )
    assert again.get("/geometry/tags/inlet").params == original.get("/geometry/tags/inlet").params


def test_style_over_4500_parts_is_one_step(tmp_path: Path) -> None:
    """The ADR-0021 case: hiding every part of a big assembly in one undo step. Measured,
    not budgeted yet: the time is reported with the task (T-021)."""
    session = Session(journal_path=tmp_path / "journal.jsonl")
    session.project = many_parts(4500)
    seen: list[dict[str, Any]] = []
    session.events.subscribe("node.changed", lambda e, d: seen.append(d))
    start = time.perf_counter()
    result = session.execute("geometry.style", nodes=["/geometry/parts"], visible=False)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"geometry.style over 4500 parts: {elapsed_ms:.1f} ms")
    assert result["resolved"] == 4500
    assert len(result["changed"]) == 4500 and result["unchanged"] == []
    assert len(seen) == 4500
    assert session.execute("history.state")["undo"] == "style 4500 nodes"
    assert [r["command"] for r in session.journal.records] == ["geometry.style"]


# ---------------------------------------------------------------- one read per part (T-096/T-101)
#
# `_load_cache` claims a part under the store lock so a second caller waits for the first reader
# instead of opening the same cache file again. The batch route answering the viewport and the
# background restore that runs when the first client connects race for exactly the same parts on a
# cold project, so this is a guarantee, not an optimisation: it is asserted here rather than only
# in `tools/check_cache_claim.py`, where it could regress unnoticed.


class _CacheNode:
    """The part node `_load_cache` touches: an id, a type and its params."""

    def __init__(self, node_id: str) -> None:
        self.id = node_id
        self.type = "part"
        self.status = ""
        self.params: dict[str, object] = {}


class _CacheProject:
    def __init__(self, nodes: dict[str, _CacheNode]) -> None:
        self._nodes = nodes

    def find(self, ref: str) -> _CacheNode | None:
        return self._nodes.get(ref)


class _CacheSession:
    def __init__(self, project: _CacheProject, path: Path) -> None:
        self.project = project
        self.project_path = path


def _claim_store(folder: Path, nodes: dict[str, _CacheNode]) -> GeometryStore:
    """A `GeometryStore` with only what `_load_cache` uses, so the claim is tested directly."""
    store = GeometryStore.__new__(GeometryStore)
    store._session = _CacheSession(_CacheProject(nodes), folder)  # type: ignore[assignment]
    store._lock = threading.RLock()
    store._entries = {}
    store._reading = {}
    store._errors = {}
    store._cached = set()
    # **`_jobs` is not optional any more.** `_load_cache` asks `self.basis(node)` whether the cached
    # bytes are the part's current version (Claude's defect 1), and `basis` reaches `self.mesh` and
    # then `_entry`, which reads `_jobs` to tell "still importing" from "finished". A stub with a
    # fixed attribute list has to grow the attribute, not the store fall back to a default: the real
    # `__init__` always sets it.
    store._jobs = {}
    store._scratch = None
    return store


def _claim_payload() -> bytes:
    positions = np.zeros((3, 3), np.float32)
    return pack_part(
        PartMesh(
            (
                TriMesh(
                    "m",
                    "solid",
                    (0.5, 0.5, 0.5),
                    positions,
                    np.zeros((3, 3), np.float32),
                    np.zeros((1, 3), np.uint32),
                    None,
                    0,
                    0,
                    "fine",
                ),
            ),
            "m",
            None,
        )
    )


def test_two_threads_wanting_one_part_read_its_cache_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight callers, one part: one cache read, one shared entry, every caller served."""
    from stokes.geometry import cache as cache_mod

    node_id = "n_0000c1a1"
    node = _CacheNode(node_id)
    store = _claim_store(tmp_path, {node_id: node})
    write_cache(tmp_path, node_id, _claim_payload())

    reads: list[str] = []
    real_read = cache_mod.read_cache

    def counted(
        project_dir: Path, wanted: str, source: Path | None, basis: str | None = None
    ) -> Any:
        reads.append(wanted)
        # Hold the claim open so the other callers must find it and wait, rather than win the race
        # and read the file themselves; without the hold this test would prove nothing.
        threading.Event().wait(0.05)
        return real_read(project_dir, wanted, source, basis)

    monkeypatch.setattr(cache_mod, "read_cache", counted)
    results: list[Any] = []
    gate = threading.Barrier(8)

    def ask() -> None:
        gate.wait()
        results.append(store._load_cache(node))  # type: ignore[arg-type]

    threads = [threading.Thread(target=ask) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert reads == [node_id], f"the part's cache was read {len(reads)} times, expected 1"
    assert len(results) == 8 and all(r is not None for r in results)
    assert len({id(r) for r in results}) == 1, "the callers did not share one entry"
    assert node.status == "ok"


def test_a_claim_on_one_part_does_not_block_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A part being read must not serialise the read of a different part behind it."""
    from stokes.geometry import cache as cache_mod

    held, other = "n_0000c1a2", "n_0000c1a3"
    held_node, other_node = _CacheNode(held), _CacheNode(other)
    store = _claim_store(tmp_path, {held: held_node, other: other_node})
    payload = _claim_payload()
    write_cache(tmp_path, held, payload)
    write_cache(tmp_path, other, payload)

    real_read = cache_mod.read_cache
    reading = threading.Event()
    release = threading.Event()

    def slow(project_dir: Path, wanted: str, source: Path | None, basis: str | None = None) -> Any:
        if wanted == held:
            reading.set()
            release.wait(10.0)
        return real_read(project_dir, wanted, source, basis)

    monkeypatch.setattr(cache_mod, "read_cache", slow)
    first = threading.Thread(target=lambda: store._load_cache(held_node))  # type: ignore[arg-type]
    first.start()
    assert reading.wait(10.0), "the held read never started"

    done = threading.Event()

    def load_other() -> None:
        store._load_cache(other_node)  # type: ignore[arg-type]
        done.set()

    second = threading.Thread(target=load_other)
    second.start()
    blocked = not done.wait(2.0)
    release.set()
    first.join(30)
    second.join(30)
    assert not blocked, "reading one part blocked the read of a different part"


def test_a_missing_cache_is_not_a_cached_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A part with no cache file gets None, leaves no claim behind, and is retried next time."""
    from stokes.geometry import cache as cache_mod

    node_id = "n_0000c1a4"
    node = _CacheNode(node_id)
    store = _claim_store(tmp_path, {node_id: node})

    reads: list[str] = []
    real_read = cache_mod.read_cache

    # The signature tracks `read_cache`'s own: it gained the part's current `basis` so a cache made
    # from another geometry version can be rejected (Claude's defect 1). A stub with the old three
    # arguments made the real call fail with a TypeError rather than testing the miss it describes.
    def counted(
        project_dir: Path, wanted: str, source: Path | None, basis: str | None = None
    ) -> Any:
        reads.append(wanted)
        return real_read(project_dir, wanted, source, basis)

    monkeypatch.setattr(cache_mod, "read_cache", counted)
    assert store._load_cache(node) is None  # type: ignore[arg-type]
    assert node_id not in store._reading, "the claim was left behind after a miss"
    assert reads == [node_id]
    assert store._load_cache(node) is None  # type: ignore[arg-type]
    assert reads == [node_id, node_id], "a missing cache was not retried on the next call"
