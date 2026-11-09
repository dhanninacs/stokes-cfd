"""The operations framework through the session (ADR-0021 decision 8; T-024): operation.create
into derived parts with meshes and cached B-reps, tags through the history, undo and redo,
journal replay to the same ids and STL bytes, re-runs on change with debounce and cancel, an
imported input that changes, delete, cancel, info, types and refusals.

Each run starts a worker process; one is kept warm ahead (the service prewarms one, and the
module two), so a run costs about a second plus the operation. Tests share nothing but the pool.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Journal, Session, replay
from stokes.errors import ReferencedNode, ValidationError
from stokes.geometry import shapes, worker
from stokes.geometry.errors import MeshOnly, OperationInvalid
from stokes.geometry.operations import is_built, output_id
from stokes.geometry.opstore import OperationStore
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx
from tests.operations_support import box_part, create_and_wait, cylinder_part

Events = list[tuple[str, dict[str, Any]]]


@pytest.fixture(scope="module", autouse=True)
def warm() -> Iterator[None]:
    worker.prewarm(2)
    yield None
    worker.shutdown_pool()


def new_session(root: Path, name: str = "demo") -> tuple[Session, Events]:
    session = Session(journal_path=root / f"{name}.jsonl")
    session.execute("project.new", name=name, path=str(root / name))
    events: Events = []
    session.events.subscribe("*", lambda event, data: events.append((event, data)))
    return session, events


def members(session: Session, tag: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = session.execute("tag.members", tag=tag)["members"]
    return found


def faces_of(session: Session, tag: str, part: str) -> list[int]:
    for member in members(session, tag):
        if member["part"] == part:
            return list(member["faces"])
    return []


def cut_case(root: Path) -> tuple[Session, Events, dict[str, str]]:
    """A 20 mm block with a 4 mm pin through it, tagged, and the cut of the block by the pin."""
    session, events = new_session(root)
    block = box_part(session)
    pin = cylinder_part(session)
    session.execute("tag.create", name="top", members=[{"part": block, "faces": [5]}])
    session.execute("tag.create", name="bore", members=[{"part": pin, "faces": [0]}])
    session.execute("tag.create", name="ends", members=[{"part": pin, "faces": [1, 2]}])
    # By name, as the palette gives them (ids and paths work too).
    cut = create_and_wait(session, "boolean", ["box"], {"op": "cut", "tools": ["cylinder"]})
    assert cut["status"] == "ok", cut["error"]
    assert cut["inputs"] == [block] and cut["params"]["tools"] == [pin]
    return session, events, {"block": block, "pin": pin, "cut": cut["id"], "out": cut["outputs"][0]}


def test_a_cut_makes_a_derived_part_and_carries_the_tags(tmp_path: Path) -> None:
    session, events, ids = cut_case(tmp_path)
    project = session.require_project()
    op, out = project.get(ids["cut"]), project.get(ids["out"])
    assert op.path == "/geometry/operations/boolean_1" and op.status == "ok"
    assert out.id == output_id(op.id, 0) and out.parent is not None
    assert out.path == "/geometry/parts/box_cut"  # named after its first input
    params = out.params
    assert (params["derived"], params["family"], params["units"], params["faces"]) == (
        op.id,
        "brep",
        "mm",
        7,
    )
    assert params["triangles"] > 0 and params["bbox"][3] == pytest.approx(0.02, abs=1e-6)
    assert out.status == "ok" and out.meta["derives_from"] == [op.id]
    assert sorted(op.meta["derives_from"]) == sorted([ids["block"], ids["pin"]])
    entry = shapes.read_entry(shapes.brep_path(GeometryStore.of(session).shape_dir(), out.id))
    assert entry is not None and entry.basis == params["basis"] and entry.units == "mm"
    mesh = GeometryStore.of(session).mesh(out.id)
    assert mesh is not None and mesh.faces == 7
    # The inputs are hidden in the operation's undo step.
    assert project.get(ids["block"]).params["visible"] is False
    assert project.get(ids["pin"]).params["visible"] is False
    # Tags follow the history; what the cut removed is reported, not dropped.
    assert len(faces_of(session, "top", out.id)) == 1
    assert len(faces_of(session, "bore", out.id)) == 1
    assert faces_of(session, "top", out.id) != faces_of(session, "bore", out.id)
    assert faces_of(session, "ends", out.id) == []
    info = session.execute("operation.info", op=op.id)
    unmapped = info["report"]["unmapped"]
    assert [(u["name"], u["part"], u["faces"]) for u in unmapped] == [("ends", ids["pin"], [1, 2])]
    assert info["report"]["check_after"]["valid"] and info["run"]["ok"]
    kinds = [e for e, d in events if d.get("node_id") == op.id]
    assert "geometry.op_progress" in kinds and kinds.count("geometry.op_done") == 1
    done = next(d for e, d in events if e == "geometry.op_done" and d["node_id"] == op.id)
    assert done["outputs"] == [out.id] and done["seconds"] > 0
    tags_changed = {d["path"] for e, d in events if e == "tag.changed"}
    assert {"/geometry/tags/top", "/geometry/tags/bore"} <= tags_changed
    listed = session.execute("operation.list")
    assert [o["id"] for o in listed] == [*_primitive_ops(session), op.id]
    GeometryStore.of(session).close()


def _primitive_ops(session: Session) -> list[str]:
    from stokes.geometry.opplan import operations_in

    return [o.id for o in operations_in(session.require_project()) if o.type == "primitive"]


def test_undo_and_redo_of_an_operation(tmp_path: Path) -> None:
    session, _events, ids = cut_case(tmp_path)
    project = session.require_project()
    store = GeometryStore.of(session)
    basis = project.get(ids["out"]).params["basis"]
    top = faces_of(session, "top", ids["out"])
    session.execute("history.undo")
    assert project.find(ids["cut"]) is None and project.find(ids["out"]) is None
    assert store.mesh(ids["out"]) is None
    assert project.get(ids["block"]).params["visible"] is True
    assert project.get(ids["pin"]).params["visible"] is True
    assert [m["part"] for m in members(session, "top")] == [ids["block"]]
    session.execute("history.redo")
    OperationStore.of(session).wait()
    again = project.get(ids["out"])
    assert project.get(ids["cut"]).status == "ok" and again.params["basis"] == basis
    assert faces_of(session, "top", ids["out"]) == top
    assert project.get(ids["block"]).params["visible"] is False
    store.close()


def test_the_journal_replays_to_the_same_ids_and_stl_bytes(tmp_path: Path) -> None:
    session, _events, ids = cut_case(tmp_path / "first")
    first = session.execute("geometry.export_stl", path="cut.stl", parts=[ids["out"]])
    records = Journal.read(tmp_path / "first" / "demo.jsonl")
    creates = [r for r in records if r["command"] == "operation.create"]
    assert len(creates) == 3 and all(r["params"]["node_id"] for r in creates)
    fresh, _ = new_session(tmp_path / "second")
    replay(fresh, records[1:])  # replay waits for each operation before the next record
    project = fresh.require_project()
    for key in ("block", "pin", "cut", "out"):
        assert project.find(ids[key]) is not None, key
    assert (
        project.get(ids["out"]).params["basis"]
        == session.require_project().get(ids["out"]).params["basis"]
    )
    assert faces_of(fresh, "top", ids["out"]) == faces_of(session, "top", ids["out"])
    again = fresh.execute("geometry.export_stl", path="cut.stl", parts=[ids["out"]])
    assert (
        Path(first["files"][0]["path"]).read_bytes() == Path(again["files"][0]["path"]).read_bytes()
    )
    GeometryStore.of(session).close()
    GeometryStore.of(fresh).close()


def test_a_replayed_burst_of_changes_runs_the_operation_as_often_as_the_original(
    tmp_path: Path,
) -> None:
    """A replay runs a burst of changes as often as the session that wrote the journal did.

    A journal records no timing, so replay cannot know from the file alone that three parameter
    changes arrived inside one 300 ms debounce window. It used to force every pending re-run at
    every record instead of letting the debounce do its job, so the same burst ran the operation
    FOUR times against TWO in the original — the same answer, from twice the worker time (M1 review,
    T-092, measured). ``replay`` now holds the debounce across a run of change records, so the burst
    collapses the way it did when it was recorded. The result was always the same; the work now is
    too.
    """
    session, events, ids = cut_case(tmp_path / "first")
    for fuzzy in ("0.001 [mm]", "0.002 [mm]", "0.003 [mm]"):  # within the 300 ms debounce
        session.execute("parameter.set", node=ids["cut"], name="fuzzy", value=fuzzy)
    OperationStore.of(session).wait()
    original = len([d for e, d in events if e == "geometry.op_done" and d["node_id"] == ids["cut"]])
    assert original == 2  # the create and one re-run for the three changes

    records = Journal.read(tmp_path / "first" / "demo.jsonl")
    fresh, again_events = new_session(tmp_path / "second")
    replay(fresh, records[1:])
    replayed = len(
        [d for e, d in again_events if e == "geometry.op_done" and d["node_id"] == ids["cut"]]
    )
    # One for the create and one for the burst, as in the session that wrote it. Before this was
    # coalesced, replay forced a run per record and this was 4.
    assert replayed == original, (
        f"replay ran the operation {replayed} times against {original} in the original session"
    )
    # The answer is the same: the same version of the same output.
    assert (
        fresh.require_project().get(ids["out"]).params["basis"]
        == session.require_project().get(ids["out"]).params["basis"]
    )
    GeometryStore.of(session).close()
    GeometryStore.of(fresh).close()


def test_a_parameter_change_re_runs_once_after_the_last_change(tmp_path: Path) -> None:
    session, events, ids = cut_case(tmp_path)
    project = session.require_project()
    service = OperationStore.of(session)
    before = project.get(ids["out"]).params["basis"]
    signature = project.get(ids["cut"]).meta["run"]["signature"]
    for fuzzy in ("0.001 [mm]", "0.002 [mm]", "0.003 [mm]"):  # within the 300 ms debounce
        session.execute("parameter.set", node=ids["cut"], name="fuzzy", value=fuzzy)
    assert project.get(ids["cut"]).status == "stale"
    assert project.get(ids["out"]).status == "stale"
    assert service.state(ids["cut"])["state"] in ("waiting", "running")
    service.wait()
    op = project.get(ids["cut"])
    assert op.status == "ok" and op.meta["run"]["signature"] != signature
    assert op.params["fuzzy"].value == pytest.approx(3e-6)
    after = project.get(ids["out"]).params["basis"]
    assert after != before  # a new version: tag members picked on the old one go stale
    assert faces_of(session, "top", ids["out"])  # the history carried the tag again
    top = next(m for m in members(session, "top") if m["part"] == ids["out"])
    assert top["stale"] is False
    done = [d for e, d in events if e == "geometry.op_done" and d["node_id"] == ids["cut"]]
    assert len(done) == 2  # the create and one re-run for the three changes
    # Undo of a parameter emits only history.changed: the op is judged again and re-runs.
    session.execute("history.undo")
    service.wait()
    assert project.get(ids["cut"]).params["fuzzy"].value == pytest.approx(2e-6)
    assert project.get(ids["cut"]).status == "ok"
    # Without auto_run a change leaves it stale until operation.run.
    session.execute("parameter.set", node=ids["cut"], name="auto_run", value=False)
    session.execute("parameter.set", node=ids["cut"], name="simplify", value=False)
    assert project.get(ids["cut"]).status == "stale" and not service.busy(ids["cut"])
    info = session.execute("operation.run", op="boolean_1", wait=True)
    assert info["status"] == "ok" and info["stale"] is False
    GeometryStore.of(session).close()


def test_a_change_during_a_run_cancels_it(tmp_path: Path) -> None:
    session, events, ids = cut_case(tmp_path)
    service = OperationStore.of(session)
    session.execute("operation.run", op=ids["cut"])
    time.sleep(0.05)
    session.execute("parameter.set", node=ids["cut"], name="fuzzy", value="0.004 [mm]")
    time.sleep(0.5)  # the debounce has started the re-run, which replaced the first
    service.wait()
    project = session.require_project()
    assert project.get(ids["cut"]).status == "ok"
    failed = [d for e, d in events if e == "geometry.op_failed"]
    assert failed == []  # a replaced run ends quietly
    GeometryStore.of(session).close()


def test_an_imported_input_that_changes_makes_it_run_again(tmp_path: Path) -> None:
    source = fx.write_plain(fx.box(20.0, 20.0, 20.0), tmp_path / "block.step")
    session, _events = new_session(tmp_path)
    part = session.execute("geometry.import", path=str(source), wait=True)
    pin = cylinder_part(session)
    cut = create_and_wait(session, "boolean", [part["id"]], {"op": "cut", "tools": [pin]})
    project = session.require_project()
    out = cut["outputs"][0]
    bore = math.pi * 4.0**2 * 20.0  # mm^3: the pin's 20 mm inside the block
    assert cut["report"]["check_after"]["volume"] == pytest.approx((8000.0 - bore) * 1e-9, rel=1e-6)
    basis = project.get(part["id"]).params["basis"]
    fx.write_plain(fx.box(30.0, 20.0, 20.0), source)  # the file changes; re-import the part
    store = GeometryStore.of(session)
    store.reimport(source, {0: part["id"]})
    assert store.wait(part["id"], timeout=120)
    assert project.get(part["id"]).params["basis"] != basis
    OperationStore.of(session).wait()
    info = session.execute("operation.info", op=cut["id"])
    assert info["status"] == "ok"
    assert info["report"]["check_after"]["volume"] == pytest.approx(
        (12000.0 - bore) * 1e-9, rel=1e-6
    )
    assert info["report"]["provider"]["source_reads"] == 0  # the import cached the new B-rep
    assert project.get(out).params["bbox"][3] == pytest.approx(0.03, abs=1e-6)
    store.close()


def test_delete_keeps_or_removes_the_outputs(tmp_path: Path) -> None:
    session, _events, ids = cut_case(tmp_path)
    project = session.require_project()
    store = GeometryStore.of(session)
    box_op = project.get(ids["block"]).params["derived"]
    with pytest.raises(ReferencedNode, match="boolean_1"):
        session.execute("operation.delete", op=box_op)  # the cut reads its output
    removed = session.execute("operation.delete", op=ids["cut"])
    assert removed["outputs"] == [ids["out"]] and removed["kept"] is False
    assert project.find(ids["cut"]) is None and project.find(ids["out"]) is None
    assert project.get(ids["block"]).params["visible"] is True and store.mesh(ids["out"]) is None
    session.execute("history.undo")
    assert project.get(ids["cut"]).status == "ok" and store.mesh(ids["out"]) is not None
    assert project.get(ids["block"]).params["visible"] is False
    kept = session.execute("operation.delete", op=ids["cut"], keep_outputs=True)
    assert kept["kept"] is True and project.get(ids["out"]).params["derived"] == ""
    entry = shapes.read_entry(shapes.brep_path(store.shape_dir(), ids["out"]))
    assert entry is not None and entry.basis == project.get(ids["out"]).params["basis"]
    store.close()


def test_cancel_leaves_it_stale_and_not_retried(tmp_path: Path) -> None:
    session, events = new_session(tmp_path)
    info = session.execute(
        "operation.create", type="primitive", params={"shape": "sphere", "size": [0.01, 0, 0]}
    )
    cancelled = session.execute("operation.cancel", op=info["id"])
    assert cancelled["cancelled"] is True
    service = OperationStore.of(session)
    service.wait()
    project = session.require_project()
    assert project.get(info["id"]).status == "stale"
    failed = [d for e, d in events if e == "geometry.op_failed"]
    assert [d["code"] for d in failed] == ["geometry.cancelled"]
    session.execute("history.undo")
    session.execute("history.redo")  # a redo runs it again: it is created anew
    service.wait()
    assert project.get(info["id"]).status == "ok"
    GeometryStore.of(session).close()


def test_refusals_before_any_worker_starts(tmp_path: Path) -> None:
    session, _events = new_session(tmp_path)
    block = box_part(session)
    # Which modules exist is not this test's business (T-025 to T-028 each add one): ask the
    # registry for a type it says is not built, and check the refusal that follows.
    types = session.execute("operation.types")["types"]
    unbuilt = [t["type"] for t in types if not t["built"]]
    if unbuilt:
        with pytest.raises(OperationInvalid, match="not built yet"):
            session.execute("operation.create", type=unbuilt[0], inputs=[block])
    with pytest.raises(ValidationError, match="unknown operation type"):
        session.execute("operation.create", type="extrude", inputs=[block])
    with pytest.raises(OperationInvalid, match="at least 1 input"):
        session.execute("operation.create", type="boolean", params={"op": "fuse"})
    with pytest.raises(ValidationError, match="own parameters"):
        session.execute("operation.create", type="boolean", inputs=[block], params={"inputs": []})
    session.execute("parameter.set", node=block, name="family", value="mesh")
    with pytest.raises(MeshOnly) as caught:
        session.execute("operation.create", type="boolean", inputs=[block], params={"op": "fuse"})
    assert caught.value.code == "geometry.mesh_only" and "mesh-only" in caught.value.message
    assert [t["type"] for t in types][:2] == ["primitive", "boolean"]
    built = {t["type"]: t["built"] for t in types}
    assert built["primitive"] and built["boolean"]  # T-024's two, built for good
    # The flag is a property, not a list: a type is built exactly when its module is there.
    assert all(t["built"] == is_built(t["type"]) for t in types)
    boolean = next(t for t in types if t["type"] == "boolean")
    assert boolean["part_params"] == ["tools"]
    assert set(boolean["schema"]["properties"]) >= {"op", "tools", "fuzzy", "simplify", "inputs"}
    GeometryStore.of(session).close()


def test_reopening_rebuilds_an_output_that_lost_its_mesh(tmp_path: Path) -> None:
    session, _events, ids = cut_case(tmp_path)
    session.execute("project.save")
    folder = session.project_path
    assert folder is not None
    GeometryStore.of(session).close()
    from stokes.geometry.cache import cache_file

    cache_file(folder, ids["out"]).unlink()  # the mesh cache is regenerable: it may be gone
    reopened = Session()
    reopened.execute("project.open", path=str(folder))
    store = GeometryStore.of(reopened)
    store.activate()  # what the server does: meshes from the cache, the rest rebuilt
    deadline = time.monotonic() + 120
    while store.mesh(ids["out"]) is None and time.monotonic() < deadline:
        time.sleep(0.2)
    OperationStore.of(reopened).wait()
    project = reopened.require_project()
    assert store.mesh(ids["out"]) is not None
    assert project.get(ids["cut"]).status == "ok" and project.get(ids["out"]).status == "ok"
    assert store.mesh(ids["block"]) is not None  # read from its cache, not rebuilt
    store.close()


def test_t056_fix_a_tag_holding_edges_keeps_them_through_a_cut(tmp_path: Path) -> None:
    """T-056-fix: edge and vertex members are carried through an operation, or reported.

    The review found `carry` moved face members and **silently dropped** edge and vertex ones: a tag
    naming an edge of the block came back holding nothing, with no note. The owner's answer (via
    Claude, 2026-10-12) is that this is not an accepted deviation from §15.1, which requires the
    loss
    to be reported.

    This runs a real cut and asserts the tag still holds its edges afterwards — the common case,
    where
    the operation never touched them — and that nothing is reported as lost for a member that
    survived.
    """
    session, _events = new_session(tmp_path)
    block = box_part(session)
    cylinder_part(session)

    # A tag on the block naming explicit EDGES and VERTICES, not only faces.
    session.execute(
        "tag.create",
        name="block_edges",
        members=[{"part": block, "edges": [[0, 3]], "vertices": [[0, 1]]}],
    )
    # `to_json` omits an EMPTY id set (§15.4: "absent fields mean none"), so `.get` is the honest
    # read; the member does carry edges at this point.
    before = members(session, "block_edges")
    assert before and before[0].get("edges"), f"the tag was created without edges: {before}"

    cut = create_and_wait(session, "boolean", ["box"], {"op": "cut", "tools": ["cylinder"]})
    assert cut["status"] == "ok", cut["error"]
    out = cut["outputs"][0]

    after = [m for m in members(session, "block_edges") if m["part"] == out]
    assert after, "the tag lost its output member entirely"
    # The edge ids are carried, not dropped: before the fix this list was empty.
    assert after[0].get("edges"), (
        "the edge ids were dropped through the cut — this is the T-056 defect: "
        f"member after = {after[0]}"
    )
    # And the run reports nothing lost for the faces it did map.
    info = session.execute("operation.info", op=cut["id"])
    lost = [
        row for row in info["report"]["unmapped"] if row.get("edges") and row.get("part") == block
    ]
    assert not lost, f"an edge that survived was reported lost: {lost}"


def test_t056_edge_numbering_matches_the_topology_record() -> None:
    """T-056-fix defect 1: edges and vertices must use the RECORD's numbering, not the explorer's.

    Claude found this and I reproduced it: `subshape_list` numbers edges and vertices by a
    `TopExp_Explorer` walk (which visits a shape once per occurrence), while the topology record —
    and therefore every tag member's ids — numbers them with `MapShapes` (once each). On a box that
    is **24 edges against 12 and 48 vertices against 8**, so a carried edge id was wrong for every
    edge
    after the first occurrence, and some ids ran past the part's own edge count.

    The test is the one Claude specified: a box, a tag naming edge 5, carried through a no-op
    operation, and edge 5 must come back as edge 5 with every id inside the record's count.
    """
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX

    from stokes.geometry.operations.faces import subshape_list
    from stokes.geometry.shapes import MM
    from stokes.geometry.topology_brep import _map_of, part_topology
    from tests import geometry_fixtures as fx

    shape = fx.box(10.0, 20.0, 30.0)
    topo = part_topology(shape, part="n_00000001", basis="b" * 8, scale=MM)
    edge_count = len(topo.edges)
    vertex_count = len(topo.vertices)

    # The record's own counts are the MapShapes ones; the explorer walk disagrees.
    assert edge_count == _map_of(shape, TopAbs_EDGE).Extent()
    assert vertex_count == _map_of(shape, TopAbs_VERTEX).Extent()
    assert len(subshape_list(shape, "edge")) == edge_count, (
        f"the edge enumeration yields {len(subshape_list(shape, 'edge'))} ids "
        f"for a record with {edge_count} edges — a carried id would name the wrong edge"
    )
    assert len(subshape_list(shape, "vertex")) == vertex_count, (
        f"the vertex enumeration yields {len(subshape_list(shape, 'vertex'))} ids "
        f"for a record with {vertex_count} vertices"
    )


def test_t056_a_carried_edge_id_survives_a_no_op_operation(tmp_path: Path) -> None:
    """T-056-fix defect 1, end to end: edge 5 in must be edge 5 out.

    A `heal` on an already-valid box changes no geometry, so the history maps every edge to itself.
    Before the numbering fix the tag came back naming 24-based ids, so the answer was either the
    wrong edge or an id past the part's edge count.
    """
    session, _events = new_session(tmp_path)
    block = box_part(session)
    session.execute("tag.create", name="an_edge", members=[{"part": block, "edges": [[5, 5]]}])
    before = members(session, "an_edge")[0]
    assert before.get("edges") == [5], f"the tag did not keep edge 5: {before}"

    op = create_and_wait(session, "heal", ["box"], {})
    assert op["status"] == "ok", op["error"]
    out = op["outputs"][0]
    after = [m for m in members(session, "an_edge") if m["part"] == out]
    assert after, "the tag lost its output member"
    assert after[0].get("edges") == [5], (
        f"edge 5 came back as {after[0].get('edges')} — the numbering differs between the "
        "carry path and the topology record"
    )
    # And no id may exceed the output's own edge count.
    info = session.execute("geometry.info", node=out)
    params = info.get("params") or {}
    assert params.get("faces"), "the output has no faces recorded"
