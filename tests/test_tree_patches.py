"""The tree as a client keeps it current: node summaries, streamed trees and the patch events.

A node event carries the summary of the node it names, and of the nodes the change reached, so that
a client patches its tree instead of fetching all of it (ADR-0019 step 8). ``node.tree`` is built in
fragments so that the server's event loop is never held for a whole refresh (the same ADR).
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from stokes.api import Session
from stokes.api.summaries import ALSO_LIMIT, summarise, tree_dict, tree_stream
from stokes.app.demo import build_demo
from stokes.model import Node, Project, QuantityValue
from stokes.server import rpc
from stokes.server.app import create_app
from stokes.units import format_quantity

PARAMS = "/physics/parameters"


# ---------------------------------------------------------------- the old builders
def reference_summary(project: Project, node: Node) -> dict[str, Any]:
    """What ``node.get`` returned before the summary builder was rewritten, kept as the oracle."""
    data = node.summary()
    data["structural"] = project.is_structural(node)
    displays: dict[str, str] = {}
    for pspec in project.spec_of(node).params:
        if pspec.kind == "quantity" and isinstance(node.params.get(pspec.name), QuantityValue):
            dim = project.param_dimension(node, pspec)
            unit = dim.unit_for(project.display_units) or None
            displays[pspec.name] = format_quantity(project.value_of(node.id, pspec.name), unit)
    if displays:
        data["displays"] = displays
    return data


def reference_tree(project: Project, node: Node, depth: int | None) -> dict[str, Any]:
    data = reference_summary(project, node)
    if depth is None or depth > 0:
        data["children"] = [
            reference_tree(project, c, None if depth is None else depth - 1) for c in node.children
        ]
    return data


def many_parts(count: int) -> Project:
    """A project with ``count`` part nodes under /geometry/parts/asm, built without a CAD kernel."""
    project = Project.new("big")
    parts = project.add("/geometry", "group", "parts")
    group = project.add(parts.id, "group", "asm")
    for index in range(count):
        node = Node(
            f"n_{index:08x}",
            "part",
            f"solid_{index + 1}",
            {
                "source": "C:/models/assembly.step",
                "solid": index,
                "units": "mm",
                "triangles": 12,
                "faces": 6,
                "bbox": [0.0, 0.0, 0.0, 0.0025, 0.0025, 0.001],
                "color": "#c7ccd1",
                "visible": True,
            },
            parent=group,
        )
        group.children.append(node)
    return Project(project.root, name="big")


# ---------------------------------------------------------------- summaries
@pytest.fixture
def demo() -> Session:
    session = Session()
    build_demo(session)
    return session


def test_summaries_equal_the_old_ones(demo: Session) -> None:
    project = demo.require_project()
    for node in project.nodes():
        assert summarise(project, node) == reference_summary(project, node)
        assert list(summarise(project, node)) == list(reference_summary(project, node))
    for depth in (None, 0, 1, 2):
        assert tree_dict(project, project.root, depth) == reference_tree(
            project, project.root, depth
        )
        assert demo.execute("node.tree", depth=depth) == reference_tree(
            project, project.root, depth
        )
    sweep = "/studies/velocity_sweep"
    assert demo.execute("node.tree", root=sweep) == reference_tree(
        project, project.get(sweep), None
    )


def test_a_streamed_tree_is_the_same_document(demo: Session) -> None:
    project = demo.require_project()
    for depth in (None, 0, 1, 3):
        text = "".join(tree_stream(project, project.root, depth))
        tree = tree_dict(project, project.root, depth)
        assert json.loads(text) == json.loads(json.dumps(tree))
        assert text == json.dumps(json.loads(text), separators=(",", ":"))  # written compactly


def test_a_big_tree_streams_in_many_fragments() -> None:
    project = many_parts(1000)
    fragments = list(tree_stream(project, project.root))
    assert len(fragments) >= 1000 // 64  # the loop gets control back about every 64 nodes
    assert json.loads("".join(fragments)) == tree_dict(project, project.root)
    assert tree_dict(project, project.root) == reference_tree(project, project.root, None)


def test_a_leaf_at_the_depth_limit_has_no_children_key_and_a_leaf_inside_has_an_empty_one() -> None:
    project = many_parts(3)
    text = "".join(tree_stream(project, project.root, 4))
    tree = json.loads(text)
    group = tree["children"][0]["children"][0]["children"][0]  # geometry / parts / asm
    assert group["name"] == "asm" and group["children"][0].get("children") is None
    deeper = json.loads("".join(tree_stream(project, project.root, 5)))
    leaf = deeper["children"][0]["children"][0]["children"][0]["children"][0]
    assert leaf["children"] == []


def test_the_display_text_is_cached_per_value_and_unit(demo: Session) -> None:
    project = demo.require_project()
    node = project.get(f"{PARAMS}/U_in")
    assert summarise(project, node)["displays"] == {"value": "2 m/s"}
    demo.execute("project.set_units", system="imperial")
    assert summarise(project, node)["displays"]["value"].endswith("ft/s")
    demo.execute("history.undo")
    assert summarise(project, node)["displays"] == {"value": "2 m/s"}


# ---------------------------------------------------------------- events
def recorded(session: Session, *names: str) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []
    for name in names:
        session.events.subscribe(name, lambda e, d: seen.append((e, d)))
    return seen


def test_added_carries_the_node_its_parent_and_no_index_when_it_goes_last(demo: Session) -> None:
    seen = recorded(demo, "node.added")
    demo.execute("parameter.define", name="mu", dimension="dynamic_viscosity", value="1 [mPa*s]")
    (_, data), *rest = seen
    assert not rest
    parent = demo.require_project().get(PARAMS)
    assert data["parent"] == parent.id and "index" not in data
    assert data["node"]["path"] == f"{PARAMS}/mu" and data["node"]["children_count"] == 0
    assert data["node"] == demo.execute("node.get", node=f"{PARAMS}/mu")
    assert data["node_id"] == data["node"]["id"] and data["path"] == data["node"]["path"]


def test_added_in_the_middle_says_where(demo: Session) -> None:
    seen = recorded(demo, "node.added")
    demo.execute("node.add", parent="/geometry", type="group", name="first", index=0)
    ((_, data),) = seen
    assert data["index"] == 0
    assert demo.require_project().get("/geometry").children[0].id == data["node_id"]


def test_changed_carries_the_node_and_what_the_edit_reached(demo: Session) -> None:
    seen = recorded(demo, "node.changed")
    demo.execute("parameter.set", node=f"{PARAMS}/U_in", name="value", value="3 [m/s]")
    ((_, data),) = seen
    assert data["param"] == "value" and data["node"]["displays"] == {"value": "3 m/s"}
    reached = {n["name"]: n for n in data["also"]}
    # The two expressions that read `U_in`, **and the demo's inlet boundary**, whose velocity is
    # the expression `U_in` (the demo assigns the parameter, so a change to it reaches the condition
    # too). This assertion named only the two expressions before the demo gained real conditions;
    # a boundary is now genuinely downstream of the parameter, which is what the demo is for.
    assert set(reached) == {"dp_dyn", "Q", "inlet"}
    assert reached["dp_dyn"]["displays"]["value"].startswith("4491")  # 0.5 * 998 * 9
    assert all(n == demo.execute("node.get", node=n["id"]) for n in data["also"])
    assert "resync" not in data


def test_a_rename_reaches_the_expressions_that_read_the_name(demo: Session) -> None:
    seen = recorded(demo, "node.changed")
    demo.execute("node.rename", node=f"{PARAMS}/rho", name="density")
    ((_, data),) = seen
    assert data["node"]["name"] == "density" and data["node"]["path"] == f"{PARAMS}/density"
    (broken,) = data["also"]
    assert broken["name"] == "dp_dyn" and broken["status"] == "failed"
    assert "error" in broken["meta"]


def test_a_removal_names_the_parent_and_the_expressions_it_broke(demo: Session) -> None:
    seen = recorded(demo, "node.removed")
    parent = demo.require_project().get(PARAMS)
    demo.execute("node.remove", node=f"{PARAMS}/D")
    ((_, data),) = seen
    assert data["parent"] == parent.id and "node" not in data
    assert [n["name"] for n in data["also"]] == ["Q"] and data["also"][0]["status"] == "failed"


def test_a_new_parameter_repairs_the_expression_that_waited_for_it(demo: Session) -> None:
    demo.execute("node.remove", node=f"{PARAMS}/D")
    seen = recorded(demo, "node.added")
    demo.execute("parameter.define", name="D", dimension="length", value="60 [mm]")
    ((_, data),) = seen
    (repaired,) = data["also"]
    assert repaired["name"] == "Q" and repaired["status"] == "ok"
    assert "error" not in repaired.get("meta", {})


def test_an_edit_that_reached_too_many_nodes_asks_for_a_resync(demo: Session) -> None:
    for index in range(ALSO_LIMIT + 6):
        demo.execute(
            "parameter.define", name=f"copy_{index}", dimension="velocity", value="U_in * 1"
        )
    seen = recorded(demo, "node.changed")
    demo.execute("parameter.set", node=f"{PARAMS}/U_in", name="value", value="5 [m/s]")
    ((_, data),) = seen
    assert data["resync"] is True and "also" not in data and "node" in data


def test_events_that_name_no_known_node_pass_unchanged(demo: Session) -> None:
    seen = recorded(demo, "node.changed", "geometry.part_ready", "geometry.import_progress")
    demo.emit("node.changed", node_id="n_deadbeef", path="/gone")
    demo.emit("geometry.part_ready", node_id="n_deadbeef", path="/gone", complete=True)
    demo.emit("geometry.import_progress", index=1)
    assert [d for _, d in seen] == [
        {"node_id": "n_deadbeef", "path": "/gone"},
        {"node_id": "n_deadbeef", "path": "/gone", "complete": True},
        {"index": 1},
    ]


def test_a_finished_part_carries_its_node_and_a_coarse_one_does_not(demo: Session) -> None:
    project = demo.require_project()
    part = project.add("/geometry", "part", "bracket", {"source": "C:/models/bracket.step"})
    part.status = "ok"  # the geometry store sets it after the last node.changed, silently
    seen = recorded(demo, "geometry.part_ready", "geometry.import_failed")
    demo.emit("geometry.part_ready", node_id=part.id, path=part.path, complete=False)
    demo.emit("geometry.part_ready", node_id=part.id, path=part.path, complete=True)
    demo.emit("geometry.import_failed", node_id=part.id, path=part.path, code="x", message="m")
    coarse, fine, failed = (d for _, d in seen)
    assert "node" not in coarse
    assert fine["node"]["status"] == "ok" and failed["node"]["id"] == part.id


def test_what_one_thread_changed_is_not_sent_with_another_threads_event(demo: Session) -> None:
    project = demo.require_project()
    seen = recorded(demo, "node.changed")
    part = project.add("/geometry", "part", "bracket", {"source": "C:/models/bracket.step"})

    def import_thread() -> None:  # like the geometry store: set parameters, then say so
        project.set_param(part.id, "triangles", 12)
        demo.emit("node.changed", node_id=part.id, path=part.path)

    project.set_param(project.get(f"{PARAMS}/U_in").id, "value", "4 [m/s]")  # this thread's change
    worker = threading.Thread(target=import_thread)
    worker.start()
    worker.join()
    ((_, from_worker),) = seen
    assert "also" not in from_worker  # the main thread's edit is not the worker's to announce
    demo.emit("node.changed", node_id=project.get(f"{PARAMS}/U_in").id, path=f"{PARAMS}/U_in")
    assert {n["name"] for n in seen[-1][1]["also"]} == {"dp_dyn", "Q", "inlet"}  # see above


def test_loading_a_project_leaves_nothing_to_announce(tmp_path: Path) -> None:
    session = Session()
    build_demo(session)
    session.execute("project.save", path=str(tmp_path / "demo"))
    other = Session()
    other.execute("project.open", path=str(tmp_path / "demo"))
    assert other.require_project().drain_touched() == ([], False)


def test_emit_does_not_change_what_the_caller_passed(demo: Session) -> None:
    payload = {"node_id": demo.require_project().get(PARAMS).id, "path": PARAMS}
    before = dict(payload)
    demo.emit("node.changed", **payload)
    assert payload == before


# ---------------------------------------------------------------- the server
@pytest.fixture
def client(demo: Session) -> TestClient:
    return TestClient(create_app(demo, token="t0k3n", dist_dir=None))


def rpc_call(ws: Any, request_id: int, method: str, **params: Any) -> dict[str, Any]:
    ws.send_text(json.dumps({"id": request_id, "method": method, "params": params}))
    message: dict[str, Any] = json.loads(ws.receive_text())
    if message.get("event") == "project.opened" and message["data"].get("replay"):
        message = json.loads(ws.receive_text())  # what a client connecting to a project hears first
    return message


def test_node_tree_over_rpc_is_the_command_result(client: TestClient, demo: Session) -> None:
    with client.websocket_connect("/ws?token=t0k3n") as ws:
        whole = rpc_call(ws, 1, "node.tree")
        assert whole == {"id": 1, "result": demo.execute("node.tree")}
        shallow = rpc_call(ws, 2, "node.tree", depth=1)
        assert shallow["result"] == demo.execute("node.tree", depth=1)
        sub = rpc_call(ws, 3, "node.tree", root=f"{PARAMS}/dp_dyn")
        assert sub["result"] == demo.execute("node.tree", root=f"{PARAMS}/dp_dyn")


def test_node_tree_errors_have_the_command_shape(client: TestClient, demo: Session) -> None:
    with client.websocket_connect("/ws?token=t0k3n") as ws:
        missing = rpc_call(ws, 1, "node.tree", root="/nowhere")
        bad = rpc_call(ws, 2, "node.tree", depth="deep")
        unknown = rpc_call(ws, 3, "node.tree", extra=1)
        assert missing["error"]["code"] == "model.unknown_node"
        assert bad["error"]["code"] == "validation" and "depth" in bad["error"]["message"]
        assert unknown["error"]["code"] == "validation"
        assert rpc_call(ws, 4, "rpc.ping")["result"] == {"pong": True}  # the connection is fine
    closed = Session()
    with TestClient(create_app(closed, token="t0k3n", dist_dir=None)).websocket_connect(
        "/ws?token=t0k3n"
    ) as ws:
        assert rpc_call(ws, 5, "node.tree")["error"]["code"] == "api.no_project"


def test_the_node_event_reaches_the_client_with_its_summary(client: TestClient) -> None:
    with client.websocket_connect("/ws?token=t0k3n") as ws:
        reply = rpc_call(
            ws, 1, "parameter.define", name="mu", dimension="dynamic_viscosity", value="1 [mPa*s]"
        )
        assert reply["result"]["path"] == f"{PARAMS}/mu"
        event = json.loads(ws.receive_text())
        assert event["event"] == "node.added"
        assert event["data"]["node"]["name"] == "mu" and event["data"]["parent"]


def test_a_reply_to_a_big_tree_leaves_the_loop_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """While a big tree is built, other work on the loop is not held until it is done."""
    session = Session()
    session.project = many_parts(1500)
    hub = rpc.RpcHub(session)
    monkeypatch.setattr(rpc, "TREE_SLICE_SECONDS", 0.0)  # hand the loop back after every fragment

    async def main() -> tuple[str, int]:
        ticks = 0
        stop = False

        async def ticker() -> None:
            nonlocal ticks
            while not stop:
                ticks += 1
                await asyncio.sleep(0)

        task = asyncio.create_task(ticker())
        text = await hub._tree_reply({"id": 7, "method": "node.tree", "params": {}})
        stop = True
        await task
        return text, ticks

    text, ticks = asyncio.run(main())
    fragments = len(list(tree_stream(session.require_project(), session.require_project().root)))
    assert ticks >= fragments - 1 and fragments > 20
    reply = json.loads(text)
    assert reply["id"] == 7
    assert reply["result"] == tree_dict(session.require_project(), session.require_project().root)


def test_an_event_raised_on_the_loop_between_commands_is_sent_at_once() -> None:
    """Services finish on the loop between commands (the mesh service's `_finish_from_job`): their
    events must reach the client then, not with the reply of whatever the client asks next.
    Measured before the fix: the Mesh card said "Generating…" for two minutes after the mesh was
    done. An event raised INSIDE a command still waits for that command's reply.
    """
    session = Session()
    hub = rpc.RpcHub(session)

    async def main() -> tuple[int, int, int]:
        client = rpc._Client(ws=None, loop=asyncio.get_running_loop())  # type: ignore[arg-type]
        hub._clients.append(client)
        session.emit("mesh.done", node_id="n1")
        at_once = client.queue.qsize()
        hub._executing += 1  # what `dispatch` does around `session.execute`
        try:
            session.emit("mesh.progress", node_id="n1")
        finally:
            hub._executing -= 1
        held = client.queue.qsize()
        hub._flush_events()  # what `serve` does once the reply is queued
        return at_once, held, client.queue.qsize()

    at_once, held, after = asyncio.run(main())
    assert at_once == 1, "an event between commands waited for the next request"
    assert held == 1 and after == 2, "an event inside a command must follow its reply"


@pytest.fixture(autouse=True)
def _demo_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """**Keep the demo out of the real user folder** (Claude's defect 9).

    `build_demo` writes its STEP through `demo_folder()`, which is under `STOKES_HOME` (or the
    user's
    home). These tests called it with no redirect, so they read and wrote `~/.stokes/demo` — which
    is
    why a demo built by an EARLIER RUN, with different tags, was served to them: the content-hashed
    name matched a file left behind, so the current generator's tags never appeared and the tests
    failed on the previous run's data. Two failures in the full suite were that, not the code.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path_factory.mktemp("stokes-home")))
