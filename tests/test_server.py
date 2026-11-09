"""The HTTP/WebSocket server over the API: token checks, geometry transport, RPC and events."""

from __future__ import annotations

import gzip
import json
import struct
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware
from starlette.testclient import WebSocketTestSession

from stokes.api import Session
from stokes.app.demo import build_demo
from stokes.geometry import worker
from stokes.geometry.cache import write_cache
from stokes.geometry.errors import GeometryWorkerError
from stokes.geometry.mesh import PartMesh
from stokes.geometry.sample import tube
from stokes.geometry.store import GeometryStore
from stokes.geometry.transport import pack_part
from stokes.server.app import GZIP_LEVEL, MAX_BATCH_IDS, NO_MESH, create_app
from stokes.server.rpc import dumps
from stokes.server.runner import ServerThread
from tests.test_session_hooks import cached_project, join_threads

PARAMS = "/physics/parameters"


@pytest.fixture
def client() -> TestClient:
    session = Session()
    build_demo(session)
    app = create_app(
        session,
        token="t0k3n",
        dist_dir=None,
        extra_files={"/extra.js": (b"// x", "application/javascript")},
    )
    return TestClient(app)


def test_token_required(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health?token=wrong").status_code == 401
    assert client.get("/api/health", headers={"X-Stokes-Token": "t0k3n"}).json()["status"] == "ok"
    assert client.get("/extra.js").text == "// x"


def test_geometry_transport(client: TestClient) -> None:
    body = client.get("/api/geometry/demo?token=t0k3n").content
    (header_length,) = struct.unpack("<I", body[:4])
    header = json.loads(body[4 : 4 + header_length].rstrip(b"\0"))
    names = [g["name"] for g in header["groups"]]
    assert names == ["walls", "inlet_flange", "outlet_flange", "inlet", "outlet"]
    data = body[4 + header_length :]
    total = sum(g["vertices"] * 24 + g["triangles"] * 12 for g in header["groups"])
    assert len(data) == total
    assert all(g["offset"] % 4 == 0 for g in header["groups"])
    assert client.get("/api/geometry/nope?token=t0k3n").status_code == 404


def hears_the_open_project(ws: WebSocketTestSession) -> dict[str, Any]:
    """Take the `project.opened` a client is sent on connecting to a server that holds a project."""
    message = json.loads(ws.receive_text())
    assert message["event"] == "project.opened" and message["data"]["replay"] is True
    return cast(dict[str, Any], message["data"])


def test_dumps_writes_non_finite_numbers_as_null() -> None:
    """NaN and infinity are not JSON: a browser cannot parse a reply that holds them."""
    message = {"a": float("nan"), "b": [1.5, float("inf"), (float("-inf"),)], "c": {"d": "x"}}
    assert json.loads(dumps(message)) == {"a": None, "b": [1.5, None, [None]], "c": {"d": "x"}}
    assert dumps({"t": 293.15}) == json.dumps({"t": 293.15})


def test_a_reply_with_nan_is_still_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A diverged run's monitors are NaN; `run.list` holding them went unread and never settled."""
    session = Session()
    build_demo(session)
    real = session.execute

    def execute(method: str, **params: Any) -> Any:
        if method == "run.list":
            return {"runs": [{"monitors": {"outlet": {"flow_rate": float("nan")}}}]}
        return real(method, **params)

    monkeypatch.setattr(session, "execute", execute)
    client = TestClient(create_app(session, token="t0k3n", dist_dir=None))
    with client.websocket_connect("/ws?token=t0k3n") as ws:
        hears_the_open_project(ws)
        ws.send_text(json.dumps({"id": 9, "method": "run.list", "params": {}}))
        text = ws.receive_text()
    reply = json.loads(text, parse_constant=lambda name: pytest.fail(f"{name} in a reply"))
    assert reply == {"id": 9, "result": {"runs": [{"monitors": {"outlet": {"flow_rate": None}}}]}}


def test_rpc_roundtrip_and_events(client: TestClient) -> None:
    with client.websocket_connect("/ws?token=t0k3n") as ws:
        hears_the_open_project(ws)
        ws.send_text(json.dumps({"id": 1, "method": "rpc.ping", "params": {}}))
        assert json.loads(ws.receive_text()) == {"id": 1, "result": {"pong": True}}

        ws.send_text(
            json.dumps(
                {
                    "id": 2,
                    "method": "parameter.set",
                    "params": {"node": f"{PARAMS}/U_in", "name": "value", "value": "3 [m/s]"},
                }
            )
        )
        reply = json.loads(ws.receive_text())
        assert reply["id"] == 2 and reply["result"]["path"] == f"{PARAMS}/U_in"
        event = json.loads(ws.receive_text())
        assert event["event"] == "node.changed" and event["data"]["param"] == "value"

        ws.send_text(
            json.dumps({"id": 3, "method": "node.get", "params": {"node": f"{PARAMS}/dp_dyn"}})
        )
        got = json.loads(ws.receive_text())["result"]
        assert got["params"]["value"]["value"] == pytest.approx(0.5 * 998 * 9)
        assert got["displays"]["value"].endswith("Pa")

        ws.send_text(json.dumps({"id": 4, "method": "nope.nothing", "params": {}}))
        assert json.loads(ws.receive_text())["error"]["code"] == "api.unknown_command"
        ws.send_text("not json")
        assert json.loads(ws.receive_text())["error"]["code"] == "rpc.invalid"


def test_wrong_token_closes_socket(client: TestClient) -> None:
    with pytest.raises(Exception), client.websocket_connect("/ws?token=wrong") as ws:  # noqa: B017 - starlette raises on close
        ws.receive_text()


def test_events_from_other_threads_arrive_without_a_request(client: TestClient) -> None:
    session = client.app.state.hub.session  # type: ignore[attr-defined]
    with client.websocket_connect("/ws?token=t0k3n") as ws:
        hears_the_open_project(ws)
        ws.send_text(json.dumps({"id": 1, "method": "rpc.ping", "params": {}}))
        assert json.loads(ws.receive_text())["id"] == 1  # the client is registered
        for index in range(3):
            emitter = threading.Thread(
                target=session.emit, args=("geometry.import_progress",), kwargs={"index": index}
            )
            emitter.start()
            emitter.join()
        received = [json.loads(ws.receive_text()) for _ in range(3)]
        assert received == [
            {"event": "geometry.import_progress", "data": {"index": i}} for i in range(3)
        ]
        ws.send_text(
            json.dumps(
                {
                    "id": 2,
                    "method": "parameter.set",
                    "params": {"node": f"{PARAMS}/U_in", "name": "value", "value": "4 [m/s]"},
                }
            )
        )
        assert json.loads(ws.receive_text())["id"] == 2  # a command's own events follow its reply
        assert json.loads(ws.receive_text())["event"] == "node.changed"


def settled(ws: WebSocketTestSession, ping: int = 100) -> list[dict[str, Any]]:
    """The events the server sent this client before the reply to a ping sent now: a client's
    messages keep their order, so the reply closes the list."""
    ws.send_text(json.dumps({"id": ping, "method": "rpc.ping", "params": {}}))
    events: list[dict[str, Any]] = []
    while True:
        message = json.loads(ws.receive_text())
        if message.get("id") == ping:
            return events
        events.append(message)


def wait_until(condition: Callable[[], bool], seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert condition()


def test_a_client_connecting_to_an_open_project_is_sent_project_opened_first(
    tmp_path: Path,
) -> None:
    """The first client missed the `project.opened` of a `--project` start, and a second one the
    open that came before it: both showed the start screen until some later event arrived."""
    writer = Session()
    writer.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    folder = writer.project_path
    assert folder is not None
    session = Session()
    session.execute("project.open", path=str(folder))  # opened before any client connects
    client = TestClient(create_app(session, token="t0k3n", dist_dir=None))
    expected = session.project_opened_data()
    assert expected is not None
    for _ in range(2):  # a second client, after the first has come and gone
        with client.websocket_connect("/ws?token=t0k3n") as ws:
            assert hears_the_open_project(ws) == {**expected, "replay": True}
            assert settled(ws) == []  # nothing else is owed before its first reply


def test_a_client_connecting_with_no_project_is_sent_nothing() -> None:
    client = TestClient(create_app(Session(), token="t0k3n", dist_dir=None))
    with client.websocket_connect("/ws?token=t0k3n") as ws:
        assert settled(ws) == []


def test_project_opened_has_one_payload_for_the_emit_and_the_connect(tmp_path: Path) -> None:
    session = Session()
    seen: list[dict[str, Any]] = []
    session.events.subscribe("project.opened", lambda event, data: seen.append(dict(data)))
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    assert seen == [session.project_opened_data()]


def test_the_client_is_registered_before_the_store_is_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Events are not kept for a client that is not registered, and activation reports at once
    (cached meshes, the start of an import): the client has to be listening when it runs."""
    session = Session()
    session.execute("project.new", name="demo")

    def activate(self: GeometryStore) -> None:
        session.emit(
            "geometry.part_ready",
            node_id="n_0000abcd",
            path="/geometry/p",
            complete=True,
            **{"pass": "cache"},
        )

    monkeypatch.setattr(GeometryStore, "activate", activate)
    client = TestClient(create_app(session, token="t0k3n", dist_dir=None))
    with client.websocket_connect("/ws?token=t0k3n") as ws:
        hears_the_open_project(ws)
        events = settled(ws)
    assert [(e["event"], e["data"]["pass"]) for e in events] == [("geometry.part_ready", "cache")]


def test_the_first_client_hears_the_cached_parts_of_the_open_project(tmp_path: Path) -> None:
    folder, part_id = cached_project(tmp_path)
    session = Session()
    session.execute("project.open", path=str(folder))  # opened before any client connects
    app = create_app(session, token="t0k3n", dist_dir=None)
    store = app.state.geometry_store()
    with TestClient(app).websocket_connect("/ws?token=t0k3n") as ws:
        wait_until(lambda: store.status(part_id) == {"status": "ready"})
        join_threads("stokes-geometry-restore")
        events = settled(ws)
    ready = [e["data"] for e in events if e["event"] == "geometry.part_ready"]
    assert [(d["node_id"], d["pass"], d["complete"]) for d in ready] == [(part_id, "cache", True)]


def test_the_first_client_hears_an_import_start_for_a_part_without_a_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pipe.step"
    source.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
    writer = Session()
    writer.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    part = writer.require_project().add("/geometry", "part", "pipe", {"source": str(source)})
    writer.execute("project.save")

    def refuse(path: Path, **kwargs: Any) -> float:
        raise GeometryWorkerError("no worker in this test")

    monkeypatch.setattr(worker, "run_import", refuse)
    session = Session()
    session.execute("project.open", path=str(tmp_path / "demo"))
    app = create_app(session, token="t0k3n", dist_dir=None)
    store = app.state.geometry_store()
    with TestClient(app).websocket_connect("/ws?token=t0k3n") as ws:
        wait_until(lambda: store.status(part.id)["status"] == "failed")
        events = settled(ws)
    geometry = [e for e in events if e["event"].startswith("geometry.")]
    assert [e["event"] for e in geometry] == ["geometry.import_progress", "geometry.import_failed"]
    assert geometry[0]["data"] == {
        "node_id": part.id,
        "path": "/geometry/pipe",
        "pass": "read",
        "solid": None,
        "index": 0,
        "count": 0,
        "triangles": 0,
    }


# ---------------------------------------------------------------------------- /api/geometry/batch
TOKEN = {"X-Stokes-Token": "t0k3n"}


@pytest.fixture
def parts_client(tmp_path: Path) -> tuple[TestClient, list[str], str]:
    """A server on a saved project of three cached parts and one part without a mesh.

    Returns the client, the cached part ids and the id of the part that has no mesh.
    """
    writer = Session()
    writer.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    project = writer.require_project()
    cached = [project.add("/geometry", "part", f"pipe_{i}") for i in range(3)]
    bare = project.add("/geometry", "part", "bare")
    writer.execute("project.save")
    folder = writer.project_path
    assert folder is not None
    for i, part in enumerate(cached):
        mesh = tube("wall", "wall", (0.8, 0.8, 0.8), 0.03, 0.0, 0.3 + 0.1 * i, 8 + i)
        write_cache(folder, part.id, pack_part(PartMesh((mesh,))))
    session = Session()
    session.execute("project.open", path=str(folder))
    client = TestClient(create_app(session, token="t0k3n", dist_dir=None))
    return client, [p.id for p in cached], bare.id


def read_batch(body: bytes) -> list[tuple[str, bytes | None]]:
    """The parts of a batch reply in order: the other half of ``_batch_frame``, as the viewport
    reads it (``readBatch`` in web/src/api/client.ts), with every length checked."""
    (count,) = struct.unpack_from("<I", body, 0)
    at = 4
    parts: list[tuple[str, bytes | None]] = []
    for _ in range(count):
        (id_length,) = struct.unpack_from("<I", body, at)
        at += 4
        node_id = body[at : at + id_length].decode("utf-8")
        at += id_length
        (size,) = struct.unpack_from("<I", body, at)
        at += 4
        if size == NO_MESH:
            parts.append((node_id, None))
            continue
        assert at + size <= len(body)
        parts.append((node_id, body[at : at + size]))
        at += size
    assert at == len(body), "bytes after the last part"
    return parts


def test_geometry_batch_frames_meshes_and_marks_parts_without_one(
    parts_client: tuple[TestClient, list[str], str],
) -> None:
    client, ids, bare = parts_client
    unknown = "n_ffffffff"
    asked = [ids[0], unknown, ids[1], bare, "not-an-id", ids[2], ids[0]]
    response = client.get("/api/geometry/batch", params={"ids": ",".join(asked)}, headers=TOKEN)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["cache-control"] == "no-store"
    parts = read_batch(response.content)
    # In the order asked, a repeated id once, at its first place.
    assert [node_id for node_id, _ in parts] == [ids[0], unknown, ids[1], bare, "not-an-id", ids[2]]
    by_id = dict(parts)
    for node_id in ids:
        single = client.get(f"/api/geometry/{node_id}", headers=TOKEN)
        assert single.status_code == 200
        assert by_id[node_id] == single.content
    # Unknown, not an id, or a part with no mesh yet: the marker, not an empty payload.
    assert by_id[unknown] is None and by_id["not-an-id"] is None and by_id[bare] is None
    assert client.get(f"/api/geometry/{bare}", headers=TOKEN).status_code == 404


def test_geometry_batch_without_ids_is_an_empty_reply(
    parts_client: tuple[TestClient, list[str], str],
) -> None:
    client, _, _ = parts_client
    for query in ("", "?ids=", "?ids=,%20,"):
        response = client.get(f"/api/geometry/batch{query}", headers=TOKEN)
        assert response.status_code == 200
        assert response.content == struct.pack("<I", 0)


def test_geometry_batch_needs_the_token(parts_client: tuple[TestClient, list[str], str]) -> None:
    client, ids, _ = parts_client
    url = f"/api/geometry/batch?ids={ids[0]}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-Stokes-Token": "wrong"}).status_code == 401
    assert client.get(f"{url}&token=wrong").status_code == 401
    assert client.get(f"{url}&token=t0k3n").status_code == 200
    # The token is checked before the size, so a request without one learns nothing else.
    many = ",".join(f"n_{i:08x}" for i in range(MAX_BATCH_IDS + 1))
    assert client.get("/api/geometry/batch", params={"ids": many}).status_code == 401


def test_geometry_batch_refuses_more_than_the_cap(
    parts_client: tuple[TestClient, list[str], str],
) -> None:
    client, _, _ = parts_client
    ids = [f"n_{i:08x}" for i in range(MAX_BATCH_IDS + 1)]
    at_cap = client.get(
        "/api/geometry/batch", params={"ids": ",".join(ids[:MAX_BATCH_IDS])}, headers=TOKEN
    )
    assert at_cap.status_code == 200
    assert len(read_batch(at_cap.content)) == MAX_BATCH_IDS
    over = client.get("/api/geometry/batch", params={"ids": ",".join(ids)}, headers=TOKEN)
    assert over.status_code == 413


def test_geometry_batch_is_gzipped_only_when_asked(
    parts_client: tuple[TestClient, list[str], str],
) -> None:
    client, ids, _ = parts_client
    params = {"ids": ",".join(ids)}
    plain = client.get(
        "/api/geometry/batch", params=params, headers={**TOKEN, "Accept-Encoding": "identity"}
    )
    assert "content-encoding" not in plain.headers
    with client.stream(
        "GET", "/api/geometry/batch", params=params, headers={**TOKEN, "Accept-Encoding": "gzip"}
    ) as response:
        assert response.headers["content-encoding"] == "gzip"
        raw = b"".join(response.iter_raw())
    assert gzip.decompress(raw) == plain.content
    assert len(raw) < len(plain.content)
    # The gzip header's XFL byte names the level class: 2 for level 9, 4 for the fastest, 0 between.
    assert GZIP_LEVEL == 6 and raw[:2] == b"\x1f\x8b" and raw[8] == 0
    gzip_options = [m.kwargs for m in client.app.user_middleware if m.cls is GZipMiddleware]  # type: ignore[attr-defined]
    assert gzip_options == [{"minimum_size": 512, "compresslevel": GZIP_LEVEL}]


# ------------------------------------------------------------------ gzip for remote clients only
LOOPBACK_CLIENTS = ["127.0.0.1", "127.8.9.10", "::1", "::ffff:127.0.0.1"]
# Documentation addresses (RFC 5737, 3849), and the name the test client gives itself, which is not
# an address at all: a host the check cannot place counts as remote.
REMOTE_CLIENTS = ["203.0.113.7", "2001:db8::7", "::ffff:203.0.113.7", "testclient"]
BROWSER = "gzip, deflate, br"


def raw_batch(client: TestClient, ids: list[str], accept: str) -> tuple[dict[str, str], bytes]:
    """One batch request as the network carries it: the reply's headers and its body, undecoded."""
    with client.stream(
        "GET",
        "/api/geometry/batch",
        params={"ids": ",".join(ids)},
        headers={**TOKEN, "Accept-Encoding": accept},
    ) as response:
        assert response.status_code == 200
        return dict(response.headers), b"".join(response.iter_raw())


def from_client(client: TestClient, host: str) -> TestClient:
    """The same app, reached by a client that the server sees at ``host``."""
    return TestClient(client.app, client=(host, 50000))


@pytest.mark.parametrize("host", LOOPBACK_CLIENTS)
def test_geometry_batch_is_not_compressed_for_a_loopback_client(
    parts_client: tuple[TestClient, list[str], str], host: str
) -> None:
    client, ids, _ = parts_client
    headers, raw = raw_batch(from_client(client, host), ids, BROWSER)
    assert "content-encoding" not in headers
    # Large enough that the gzip middleware takes a reply this size from a client that is remote.
    assert len(raw) > 512
    # The frame itself, not a gzip stream: it reads as a batch with every id in it.
    assert [node_id for node_id, _ in read_batch(raw)] == ids


@pytest.mark.parametrize("host", REMOTE_CLIENTS)
def test_geometry_batch_is_gzipped_for_a_remote_client(
    parts_client: tuple[TestClient, list[str], str], host: str
) -> None:
    client, ids, _ = parts_client
    remote = from_client(client, host)
    _, plain = raw_batch(remote, ids, "identity")
    headers, raw = raw_batch(remote, ids, BROWSER)
    assert headers["content-encoding"] == "gzip"
    assert raw[:2] == b"\x1f\x8b" and len(raw) < len(plain)
    assert gzip.decompress(raw) == plain
    assert [node_id for node_id, _ in read_batch(plain)] == ids


def test_compress_loopback_gzips_for_a_loopback_client_too(
    parts_client: tuple[TestClient, list[str], str],
) -> None:
    client, ids, _ = parts_client
    session = client.app.state.hub.session  # type: ignore[attr-defined]
    app = create_app(session, token="t0k3n", dist_dir=None, compress_loopback=True)
    loopback = TestClient(app, client=("127.0.0.1", 50000))
    headers, raw = raw_batch(loopback, ids, BROWSER)
    assert headers["content-encoding"] == "gzip"
    assert [node_id for node_id, _ in read_batch(gzip.decompress(raw))] == ids


def test_the_running_server_does_not_compress_for_the_connection_it_is_on(
    parts_client: tuple[TestClient, list[str], str],
) -> None:
    """The test client names its own address; this asks uvicorn for the one it reports, over a
    real socket, as the desktop host's page does."""
    client, ids, _ = parts_client
    server = ServerThread(cast(FastAPI, client.app))
    url = server.start()
    try:
        request = urllib.request.Request(
            f"{url}/api/geometry/batch?ids={','.join(ids)}",
            headers={**TOKEN, "Accept-Encoding": BROWSER},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.headers.get("Content-Encoding") is None
            body = response.read()
    finally:
        server.stop()
    assert [node_id for node_id, _ in read_batch(body)] == ids


@pytest.fixture(autouse=True)
def _demo_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """**Keep the demo out of the real user folder** (Claude's defect 9).

    The `client` fixture calls `build_demo`, which writes its STEP under `STOKES_HOME`. With no
    redirect the tests read and wrote `~/.stokes/demo`, so a file left by an earlier run was reused
    and the suite depended on the machine's state.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path_factory.mktemp("stokes-home")))
