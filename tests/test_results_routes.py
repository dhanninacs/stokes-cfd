"""The results routes and `run.fields`: serving a run's fields to the desktop and the browser.

**No OpenFOAM, no agent.** A fake runner (the shape `tests/test_run_commands.py` uses) answers
`extract.fields` and `artifact.get` from an in-memory `STKFLD1` built by the agent's own
`pack_fields`, so the desktop is tested against the real format while the host half stays a fake.

What is proved here, and each of these fails without the work:

* after a fake run the record carries `fields` and `slice`, the cache holds the verified artifact,
  and the three routes serve from it;
* the routes refuse a bad token, an unknown field with a 400 and an unknown patch with a 404;
* **a reopen with no runner at all still serves from the cache** — that is the point of caching it;
* a sha256 mismatch leaves the record `done` with `error` starting `run.extract`, `fields == []`,
  emits `run.failed_extract`, and leaves no `.part` or final file;
* `run.clear` removes the cache file.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from stokes.api import Session
from stokes.geometry.transport import unpack
from stokes.mesh.surfaces import unpack_edges
from stokes.run.records import RunRecord
from stokes.run.store import RESULTS_FOLDER, RESULTS_SUFFIX, RunStore
from stokes.server.app import create_app
from stokes_agent.extract import pack_fields

TOKEN = "t0k3n"
PART_ID = "n_aaaaaaaa"
TAG_ID = "n_bbbbbbbb"
REGION_ID = "n_cccccccc"
MESH_ID = "n_dddddddd"

#: The two patches the fake path knows, in the order the surfaces artifact lists them.
PATCH_NAMES = ("inlet", "walls")

#: `U_mag` follows `U`, exactly as the agent's extractor emits them.
FIELDS = ("U", "U_mag", "p")

UNITS = {"U": "m/s", "U_mag": "m/s", "p": "m2/s2"}


def quad(offset: float = 0.0) -> list[list[float]]:
    return [
        [offset, 0.0, 0.0],
        [offset + 1.0, 0.0, 0.0],
        [offset + 1.0, 1.0, 0.0],
        [offset, 1.0, 0.0],
    ]


def patch_arrays(scale: float) -> dict[str, np.ndarray]:
    u = np.array([[scale, 0.0, 0.0]] * 4, dtype="float32")
    u[:, 1] = np.arange(4, dtype="float32")
    return {
        "U": u,
        "U_mag": np.linalg.norm(u, axis=1).astype("float32"),
        "p": np.arange(4, dtype="float32") * scale,
    }


def field_artifact(
    *, walls_has_fields: bool = True, slice_missed: bool = False
) -> tuple[bytes, dict[str, Any]]:
    """The `STKFLD1` the fake host hands out: `inlet` with all three fields, `walls` with `p`.

    `walls` carrying no array at all is what makes the "empty fields dict" case testable on the
    desktop: the surfaces artifact's patch order is what the field artifact's order is.

    `slice_missed` builds the artifact a plane outside the mesh produces: **the section is there
    with `triangles == 0`**, which is how this format says "the cut found nothing" (the agent always
    writes a slice). It must read as *no slice* on the record and 404 on the routes, so it is not
    the same as a valid slice with an empty field.
    """
    patches = [
        {
            "name": "inlet",
            "points": np.array(quad(0.0), dtype="float32"),
            "fields": patch_arrays(1.0),
        },
        {
            "name": "walls",
            "points": np.array(quad(1.0), dtype="float32"),
            "fields": patch_arrays(2.0) if walls_has_fields else {},
        },
    ]
    if slice_missed:
        plane: dict[str, Any] = {
            "origin": [99.0, 99.0, 99.0],
            "normal": [1.0, 0.0, 0.0],
            "positions": np.zeros((0, 3), dtype="float32"),
            "triangles": np.zeros((0, 3), dtype="uint32"),
            "edges": np.zeros((0, 2), dtype="uint32"),
            "fields": {"p": np.zeros((0,), dtype="float32")},
        }
    else:
        plane = {
            "origin": [0.0, 0.0, 0.0],
            "normal": [1.0, 0.0, 0.0],
            "positions": np.array(quad(0.0), dtype="float32"),
            "triangles": np.array([[0, 1, 2], [0, 2, 3]], dtype="uint32"),
            "edges": np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="uint32"),
            "fields": patch_arrays(3.0),
        }
    return pack_fields(patches, plane, 1.5)


class FakeRunner:
    """Answers the calls a run and its field fetch make, from an in-memory artifact."""

    name = "local"

    def __init__(self, *, corrupt: bool = False, extract_fails: Exception | None = None) -> None:
        self.available = True
        self.why = ""
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.listeners: list[Any] = []
        self.job = "j_" + "c" * 12
        self.report: dict[str, Any] = {
            "iterations": 3,
            "converged": True,
            "last_residuals": {"Ux": 1e-6},
        }
        self.history: list[str] = []
        self.files: dict[str, str] = {}
        self.error: dict[str, Any] | None = None
        self.data, self.header = field_artifact()
        self.corrupt = corrupt
        self.extract_fails = extract_fails
        #: Set by :meth:`finish_with`; the executor subscribes, and the test fires the event.
        self.state = "running"

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        self.calls.append((method, params))
        if method == "host.probe":
            return {
                "installs": [
                    {
                        "id": "e_000000000001",
                        "bashrc": "/x/env.sh",
                        "version": "14",
                        "flavour": "org",
                    }
                ],
                "default_install": "/x/env.sh",
                "default_install_id": "e_000000000001",
            }
        if method == "job.create":
            return {"job": self.job}
        if method == "job.status":
            return {
                "state": self.state,
                "error": self.error,
                "started": 100.0,
                "ended": 102.5,
                "reports": {"solve": self.report},
            }
        if method == "job.read":
            path = str(params.get("path") or "")
            if path.endswith("residuals.jsonl"):
                return {"text": "\n".join(self.history), "truncated": False}
            return {"text": self.files.get(path, ""), "truncated": False}
        if method == "extract.fields":
            if self.extract_fails is not None:
                raise self.extract_fails
            return {
                "artifact": "fields.stkfld",
                "size": len(self.data),
                "sha256": self._digest(),
                "time": float(self.header["time"]),
                "patches": [
                    {
                        "name": entry["name"],
                        "index": entry["index"],
                        "points": entry["points"],
                        "fields": list(entry["fields"]),
                    }
                    for entry in self.header["patches"]
                ],
                "slice": {
                    "points": self.header["slice"]["points"],
                    "triangles": self.header["slice"]["triangles"],
                    "edges": self.header["slice"]["edges"],
                },
            }
        if method == "artifact.get":
            offset, length = int(params["offset"]), int(params["length"])
            return {
                "size": len(self.data),
                "sha256": self._digest(),
                "offset": offset,
                "data": self.data[offset : offset + length],
            }
        if method == "log.tail":
            return {"lines": [], "truncated": False}
        return {}

    def _digest(self) -> str:
        return hashlib.sha256(b"corrupt" if self.corrupt else self.data).hexdigest()

    def subscribe(self, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener) if listener in self.listeners else None

    def close(self) -> None:
        return

    def fire(self, name: str, **data: Any) -> None:
        for listener in list(self.listeners):
            listener(name, {"job": self.job, **data})


def install_fake(session: Session, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the host registry at the fake, keeping `probe`'s real wrapper shape."""
    from stokes.run.hosts import HostRegistry

    class Registry:
        def runner(self, name: str) -> Any:
            return fake

        def probe(self, name: str, refresh: bool = False) -> dict[str, Any]:
            return {
                "available": fake.available,
                "host": name,
                "record": fake.call("host.probe"),
                "why": fake.why,
            }

    monkeypatch.setattr(HostRegistry, "of", classmethod(lambda cls, s: Registry()))


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    made = Session()
    made.execute("project.new", name="duct")
    return made


def a_mesh(session: Session) -> None:
    """A finished mesh result, so the snapshot has something to build on."""
    session.project.add("/mesh", "group", "results")
    session.project.add("/mesh/results", "mesh_result", "m", {"state": "done"}, node_id=MESH_ID)
    node = session.project.find(MESH_ID)
    node.meta["patch_types"] = dict.fromkeys(PATCH_NAMES, "patch")
    node.meta["patch_tags"] = {"inlet": TAG_ID}
    session.project.add("/geometry", "part", "duct", {"derived": ""}, node_id=PART_ID)
    session.project.add("/geometry", "group", "tags")
    tag = session.project.add(
        "/geometry/tags",
        "tag",
        "domain",
        {"members": json.dumps([{"part": PART_ID, "faces": "all"}])},
        node_id=TAG_ID,
    )
    material = session.execute("material.create", name="air", kind="fluid", template="gas")
    session.project.add(
        "/regions",
        "region",
        "fluid",
        {"tag": tag.id, "material": material["id"]},
        node_id=REGION_ID,
    )
    # **The `mesh_patch` nodes the field artifact's patches are looked up by.** `run.fields`
    # maps a node to a patch by NAME, so a node named after each patch is what the routes take.
    for index, name in enumerate(PATCH_NAMES):
        session.project.add(
            "/mesh/results/m",
            "mesh_patch",
            name,
            {"index": index, "triangles": 2, "faces": 1, "color": "#9aa3ad"},
        )


def patch_id(session: Session, name: str) -> str:
    node = session.project.find(f"/mesh/results/m/{name}")
    assert node is not None, f"no patch node {name}"
    return str(node.id)


def finished(session: Session, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Start a run against the fake and let its final event collect it. Returns `run.start`."""
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    return session.execute("run.start")


def collected(
    session: Session, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """A run whose fields have been fetched, waiting on **the record going final**.

    That is the contract and it is worth stating: `finish` fetches the fields *before* it writes
    the terminal state, precisely so `record.final` means "everything about this run has landed".
    `store.settle` — and so `run.start(wait=True)` — returns on exactly that condition. An earlier
    version fetched *after* the state write, which made `wait=True` return before the fields
    existed; `test_waiting_for_a_run_waits_for_its_fields` below is what pins it.
    """
    import time

    started = finished(session, fake, monkeypatch)
    fake.state = "done"
    fake.fire("job.state", state="done", step=None)
    store = RunStore.of(session)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        record = store.record(started["run"])
        if record is not None and record.final:
            return started
        time.sleep(0.02)
    raise AssertionError("the run never finished")


def client_of(session: Session) -> TestClient:
    return TestClient(create_app(session, token=TOKEN, dist_dir=None))


def cache_path(session: Session, run_id: str) -> Path:
    return RunStore.of(session).fields_path(run_id)


def release_views(store: RunStore) -> None:
    """Close the store's mapped views of its cache files, so a test can delete one on Windows.

    A mapped file cannot be unlinked there (`WinError 32`). The views are a cache: the next read
    maps the file again, or finds it gone.
    """
    with store._lock:
        views = list(store._field_views.values())
        store._field_views.clear()
    for _stamp, view, _header in views:
        store._close_view(view)


# ---------------------------------------------------------------- the fetch after a run


def test_a_finished_run_records_its_fields(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    store = RunStore.of(session)
    record = store.record(started["run"])
    assert record is not None
    assert record.state == "done"
    assert record.error == ""
    assert record.fields == ["U", "U_mag", "p"]
    assert record.slice is True
    # **`run.fields` is what the desktop reads**, and it carries the range a legend needs.
    listing = session.execute("run.fields", run=started["run"])
    assert listing["run"] == started["run"]
    assert listing["slice"] is True
    assert listing["surfaces"] == "ok"
    names = [entry["name"] for entry in listing["fields"]]
    assert names == ["U", "U_mag", "p"]
    by_name = {entry["name"]: entry for entry in listing["fields"]}
    assert by_name["p"]["units"] == "m2/s2"
    # **min/max is the union over every patch AND the slice.** The stored ranges are
    # `inlet p = [0, 1, 2, 3]`, `walls p = [0, 2, 4, 6]`, slice `p = [0, 3, 6, 9]`, so the run's
    # range is 0..9 — the slice's is the last entry read, and taking only that would pass here by
    # luck if the order changed. `U_mag`'s is the union of the two patches and is *narrower* than
    # any single one of its three entries, which is what makes the union real rather than a max of
    # maxima.
    assert by_name["p"]["min"] == 0.0
    assert by_name["p"]["max"] == pytest.approx(9.0)
    assert by_name["U_mag"]["units"] == "m/s"
    # The slice carries `U` too, at 3x the walls' scale, so the union's ends come from different
    # sections: the minimum from the inlet's first vertex and the maximum from the slice's last.
    assert by_name["U_mag"]["min"] == 1.0, "the inlet's first U is (1, 0, 0): magnitude 1"
    assert by_name["U_mag"]["max"] == pytest.approx(np.sqrt(9.0 + 9.0))
    # And both `U` and `U_mag` range over the MAGNITUDE, never over the components: the smallest
    # component stored anywhere is 0 (the y and z of a first vertex) and the smallest magnitude is
    # 1, so a component-wise range would report 0.0 here.
    assert by_name["U"]["min"] == 1.0, "U's range is the magnitude's, not the components'"
    assert by_name["U"]["max"] == by_name["U_mag"]["max"]


def test_waiting_for_a_run_waits_for_its_fields(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**`run.start(wait=True)` must not return before the fields exist.**

    `store.settle` stops the instant the record is final, and the fetch is what fills `fields` — so
    if the fetch ran *after* the terminal state write, a caller that waited would still be able to
    read `run.fields` and get `surfaces: "missing"` for a run that had just converged. The engine
    test found this as a SKIPPED check 4; this is the same race with no OpenFOAM needed.
    """
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})

    # Fire the final event from another thread while `wait=True` blocks, which is the real shape:
    # the collect worker is what calls `finish`.
    import threading
    import time

    started: dict[str, Any] = {}

    def wait_for_it() -> None:
        started.update(session.execute("run.start", wait=True))

    watcher = threading.Thread(target=wait_for_it, daemon=True)
    watcher.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not fake.listeners:
        time.sleep(0.01)
    fake.state = "done"
    fake.fire("job.state", state="done", step=None)
    watcher.join(20.0)
    assert not watcher.is_alive(), "run.start(wait=True) never returned"

    # **The moment `wait=True` returns, the fields are there** — no polling, no grace period.
    listing = session.execute("run.fields", run=started["run"])
    assert listing["surfaces"] == "ok", listing
    assert [entry["name"] for entry in listing["fields"]] == ["U", "U_mag", "p"]


def test_the_artifact_lands_in_the_cache(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    path = cache_path(session, started["run"])
    assert path.is_file()
    assert path.read_bytes() == fake.data
    assert not path.with_name(path.name + ".part").exists()
    assert path.name == f"{started['run']}{RESULTS_SUFFIX}"
    assert path.parent.name == RESULTS_FOLDER


def test_a_run_that_has_no_fields_yet_says_missing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record written before the fetch, or after a failure: the cache file is what decides."""
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), name="duct", state="done"))
    listing = session.execute("run.fields", run=record.id)
    assert listing["fields"] == []
    assert listing["slice"] is False
    assert listing["surfaces"] == "missing"


# ---------------------------------------------------------------- the routes


def test_the_field_route_serves_the_codec_with_the_token(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    client = client_of(session)
    node = patch_id(session, "walls")
    url = f"/api/results/field/{started['run']}/{node}?field=p"
    assert client.get(url).status_code == 401
    assert client.get(f"{url}&token=wrong").status_code == 401
    reply = client.get(f"{url}&token={TOKEN}")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    head, values = read_payload(reply.content)
    assert head["field"] == "p"
    assert head["units"] == "m2/s2"
    assert (head["components"], head["count"]) == (1, 4)
    assert np.array_equal(values.reshape(-1), np.arange(4, dtype="float32") * 2.0)
    # **The range is the RUN's, over every patch and the slice** — the walls' own 0..6 is not it:
    # a legend shared between patches is the only way the colours of two patches mean one thing.
    assert (head["min"], head["max"]) == (0.0, 9.0)


def test_the_field_route_slices_with_the_word_slice(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    client = client_of(session)
    reply = client.get(f"/api/results/field/{started['run']}/slice?field=p&token={TOKEN}")
    assert reply.status_code == 200
    _head, values = read_payload(reply.content)
    assert np.array_equal(values.reshape(-1), np.arange(4, dtype="float32") * 3.0)


def test_an_unknown_field_is_a_400_and_an_unknown_patch_is_a_404(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    client = client_of(session)
    node = patch_id(session, "walls")
    assert (
        client.get(
            f"/api/results/field/{started['run']}/{node}?field=nope&token={TOKEN}"
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"/api/results/field/{started['run']}/n_00000000?field=p&token={TOKEN}"
        ).status_code
        == 404
    )
    # **A patch the artifact does not have** is a 404, not a 400: the id is well formed.
    assert (
        client.get(
            f"/api/results/field/{started['run']}/n_deadbeef?field=p&token={TOKEN}"
        ).status_code
        == 404
    )


def test_a_path_parameter_that_is_not_an_id_is_refused_before_any_lookup(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Validated against `ID_RE` before the store is touched** — the shape that keeps a path
    parameter from reaching a filename."""
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    client = client_of(session)
    for bad in ("notanid", "n_ZZZZZZZZ", "n_0000000", "r_ABC12345", "slice/../x"):
        reply = client.get(f"/api/results/field/{started['run']}/{bad}?field=p&token={TOKEN}")
        assert reply.status_code == 400, f"{bad} -> {reply.status_code}"
    for bad in ("r_zz", "notanid", "R_12345678"):
        reply = client.get(f"/api/results/slice/{bad}?token={TOKEN}")
        assert reply.status_code == 400, f"{bad} -> {reply.status_code}"
    # **A traversal never even reaches the handler.** A literal slash splits the path (and a
    # `%2f` is resolved by the client before it is sent), so the router answers 404 and no
    # parameter is validated — which is the right outcome: the text never becomes a path
    # component. Pinned so a future route change cannot quietly make it reach the store.
    for url in (
        f"/api/results/slice/../../etc/passwd?token={TOKEN}",
        f"/api/results/slice/%2e%2e%2fetc?token={TOKEN}",
    ):
        assert client.get(url).status_code == 404, url


def test_the_slice_routes_serve_transport_and_edges(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    client = client_of(session)
    url = f"/api/results/slice/{started['run']}"
    assert client.get(url).status_code == 401
    reply = client.get(f"{url}?token={TOKEN}")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    part = unpack(reply.content)
    assert len(part.solids) == 1
    assert (part.solids[0].vertex_count, part.solids[0].triangle_count) == (4, 2)

    edges = client.get(f"/api/results/slice-edges/{started['run']}?token={TOKEN}")
    assert edges.status_code == 200
    assert edges.headers["cache-control"] == "no-store"
    assert unpack_edges(edges.content).shape == (4, 6)


def test_a_patch_is_matched_by_NAME_not_by_index(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The node's name decides**, with `index` only as a fallback.

    A node's `index` and the artifact's patch order can disagree — the tree can be rearranged, and
    a rename must not silently serve a different patch's array. Here the two `mesh_patch` nodes have
    their **indices swapped**: an index-first lookup would serve the inlet's array under the walls
    node, which is exactly the wrong-picture failure the brief's name rule exists to prevent.
    """
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    project = session.project
    walls = project.find("/mesh/results/m/walls")
    inlet = project.find("/mesh/results/m/inlet")
    assert walls is not None and inlet is not None
    walls.params["index"], inlet.params["index"] = 0, 1

    client = client_of(session)
    reply = client.get(f"/api/results/field/{started['run']}/{walls.id}?field=p&token={TOKEN}")
    assert reply.status_code == 200
    _head, values = read_payload(reply.content)
    # The walls patch stores `p = [0, 2, 4, 6]`; the inlet's is `[0, 1, 2, 3]`.
    assert np.array_equal(values.reshape(-1), np.arange(4, dtype="float32") * 2.0)


def test_a_slice_that_missed_the_mesh_is_no_slice(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**`triangles == 0` means the plane missed**, and the artifact still carries a slice section.

    The section being present is not the same as there being a slice: the agent always writes one,
    so a run whose cut found nothing must report `slice: false` and 404 on the slice routes rather
    than hand the browser an empty mesh it would draw as nothing.
    """
    fake = FakeRunner()
    fake.data, fake.header = field_artifact(slice_missed=True)
    started = collected(session, fake, monkeypatch)
    record = RunStore.of(session).record(started["run"])
    assert record is not None
    assert record.slice is False
    assert record.fields == ["U", "U_mag", "p"], "the patches are unaffected by the miss"
    listing = session.execute("run.fields", run=started["run"])
    assert listing["slice"] is False
    assert listing["surfaces"] == "ok"
    client = client_of(session)
    assert client.get(f"/api/results/slice/{started['run']}?token={TOKEN}").status_code == 404
    assert client.get(f"/api/results/slice-edges/{started['run']}?token={TOKEN}").status_code == 404


def test_a_run_with_no_slice_says_404(session: Session) -> None:
    """`slice: false` on the record is what the route reads; there is no file to try."""
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), name="duct", state="done"))
    client = client_of(session)
    assert client.get(f"/api/results/slice/{record.id}?token={TOKEN}").status_code == 404
    assert client.get(f"/api/results/slice-edges/{record.id}?token={TOKEN}").status_code == 404


# ---------------------------------------------------------------- a failed fetch


def test_a_sha256_mismatch_leaves_the_run_done_and_says_so(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The run's state is not the fetch's business.** The fields are still on the host, the
    solve worked, and a `failed` record here would tell the user their run failed for a reason
    that has nothing to do with the solve."""
    fake = FakeRunner(corrupt=True)
    events: list[tuple[str, dict[str, Any]]] = []
    session.events.subscribe("*", lambda name, data: events.append((name, data)))
    started = collected(session, fake, monkeypatch)
    store = RunStore.of(session)
    record = store.record(started["run"])
    assert record is not None
    assert record.state == "done", "a fetch failure must not fail the run"
    assert record.error.startswith("run.extract"), record.error
    assert record.fields == []
    assert record.slice is False
    names = [name for name, _ in events]
    assert "run.failed_extract" in names
    failure = next(data for name, data in events if name == "run.failed_extract")
    assert failure["run_id"] == started["run"]
    assert failure["code"].startswith("run.")
    assert isinstance(failure["message"], str) and failure["message"]
    # **Nothing left behind**: no part, no final file.
    path = cache_path(session, started["run"])
    assert not path.exists()
    assert not path.with_name(path.name + ".part").exists()


def test_a_file_that_cannot_be_read_back_is_removed_not_left(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A fetch that lands a file this build cannot read must not leave it there.**

    The digest can match and the bytes still be a `STKFLD1` whose header lies — the artifact is
    untrusted, and the read-back after the fetch is the first thing that proves it. Without the
    cleanup the cache keeps a file that every later route request re-reads and re-refuses, and the
    `.part` name from the transfer is not the only thing that has to be gone.
    """
    fake = FakeRunner()
    # **Valid enough to fetch, invalid to read**: the right magic, then a header length that runs
    # past the end of the buffer.
    broken = b"STKFLD1\x00" + struct.pack("<I", 4096) + b" " * 8
    fake.data, fake.header = broken, field_artifact()[1]
    events: list[str] = []
    session.events.subscribe("*", lambda name, data: events.append(name))
    started = collected(session, fake, monkeypatch)
    record = RunStore.of(session).record(started["run"])
    assert record is not None
    assert record.state == "done"
    assert record.error.startswith("run.extract")
    assert record.fields == []
    assert "run.failed_extract" in events
    path = cache_path(session, started["run"])
    assert not path.exists(), "an unreadable artifact was left in the cache"
    assert not path.with_name(path.name + ".part").exists()


def test_an_agent_that_cannot_extract_leaves_the_run_done(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host without VTK, or an older agent with no `extract.fields`: the run is still a run."""
    fake = FakeRunner(extract_fails=RuntimeError("no VTK on this host"))
    started = collected(session, fake, monkeypatch)
    record = RunStore.of(session).record(started["run"])
    assert record is not None
    assert record.state == "done"
    assert record.error.startswith("run.extract")
    assert record.fields == []
    assert not cache_path(session, started["run"]).exists()


# ---------------------------------------------------------------- reopen and clear


def test_a_reopened_project_serves_from_the_cache_with_no_runner(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The whole point of caching it.** The agent is gone, the host is gone, and the browser
    still gets its field."""
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    node = patch_id(session, "walls")
    # **A path under `tmp_path`, never a relative one**: this test used to fall back to `.` and
    # wrote a `saved.stokes/` into the repository root when it ran.
    session.execute("project.save", path=str(tmp_path / "saved"))

    from stokes.run.hosts import HostRegistry

    class NoHost:
        def runner(self, name: str) -> Any:
            raise AssertionError("a reopened project must not need a host")

        def probe(self, name: str, refresh: bool = False) -> dict[str, Any]:
            raise AssertionError("a reopened project must not need a host")

    monkeypatch.setattr(HostRegistry, "of", classmethod(lambda cls, s: NoHost()))

    again = Session()
    again.execute("project.open", path=str(session.project_path))
    listing = again.execute("run.fields", run=started["run"])
    assert listing["surfaces"] == "ok"
    assert [entry["name"] for entry in listing["fields"]] == ["U", "U_mag", "p"]
    client = client_of(again)
    reply = client.get(f"/api/results/field/{started['run']}/{node}?field=p&token={TOKEN}")
    assert reply.status_code == 200
    _head, values = read_payload(reply.content)
    assert np.array_equal(values.reshape(-1), np.arange(4, dtype="float32") * 2.0)


def test_a_deleted_cache_file_makes_the_routes_404(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    node = patch_id(session, "walls")
    # **Released first, or Windows refuses the delete** (`WinError 32`): the store keeps the
    # fetched file mapped, and a mapped file cannot be unlinked there. A user who deletes the cache
    # does so with the app closed, which is the state this restores; the routes are then asked
    # about a file that is gone, which is what this test is about.
    release_views(RunStore.of(session))
    cache_path(session, started["run"]).unlink()
    client = client_of(session)
    assert (
        client.get(f"/api/results/field/{started['run']}/{node}?field=p&token={TOKEN}").status_code
        == 404
    )
    assert client.get(f"/api/results/slice/{started['run']}?token={TOKEN}").status_code == 404
    assert session.execute("run.fields", run=started["run"])["surfaces"] == "missing"


def test_run_clear_removes_the_cache_file(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRunner()
    started = collected(session, fake, monkeypatch)
    path = cache_path(session, started["run"])
    assert path.is_file()
    session.execute("run.clear", run=started["run"])
    assert not path.exists()
    assert RunStore.of(session).record(started["run"]) is None


def test_the_cache_path_cannot_leave_the_results_folder(session: Session) -> None:
    """The id becomes part of a path, so it is validated first — the T-210 rule."""
    from stokes.mesh.errors import MeshInvalid

    store = RunStore.of(session)
    with pytest.raises((MeshInvalid, ValueError)):
        store.fields_path("../../etc/passwd")


# ---------------------------------------------------------------- the codec, read back


def read_payload(payload: bytes) -> tuple[dict[str, Any], np.ndarray]:
    """The client's own decoder — the same one `tests/test_run_fields.py` uses, written out so the
    route test does not lean on the code it is checking."""
    length = struct.unpack_from("<I", payload, 0)[0]
    head = json.loads(payload[4 : 4 + length].decode("utf-8"))
    count, components = int(head["count"]), int(head["components"])
    values = np.frombuffer(payload, dtype="<f4", count=count * components, offset=4 + length)
    return head, values.reshape(count, components)


# ---------------------------------------------------------------- the results report (item 5)


def _run_with_fields(session: Session, monkeypatch: pytest.MonkeyPatch) -> str:
    """A finished run, through the same `collected` path the other results tests use."""
    return str(collected(session, FakeRunner(), monkeypatch)["run"])


def test_the_report_downloads_as_an_attachment(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A download, not a page**: the report is generated content, and a project name becomes
    part of its DOM - rendering it inside Stokes own origin would put user text in its document."""
    client = client_of(session)
    run_id = _run_with_fields(session, monkeypatch)
    answer = client.get(f"/api/results/report/{run_id}", params={"token": TOKEN})
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("text/html")
    assert "attachment" in answer.headers["content-disposition"]
    assert answer.headers["cache-control"] == "no-store"
    assert b"<!doctype html>" in answer.content


def test_the_report_route_and_the_command_say_the_same_thing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**One builder**: the command writes a file for a client on the host, and this hands the same
    bytes to a browser. The iteration count is the line that had two answers before."""
    from stokes.post.report import report_from_run, report_html

    client = client_of(session)
    run_id = _run_with_fields(session, monkeypatch)
    answer = client.get(f"/api/results/report/{run_id}", params={"token": TOKEN})
    store = RunStore.of(session)
    record = store.record(run_id)
    assert record is not None
    expected = report_html(
        report_from_run(
            project=str(session.require_project().name),
            run_id=run_id,
            record=record,
            fields={"fields": store.fields_status(run_id).get("fields") or []},
            residuals=store.residuals(run_id),
        )
    ).encode("utf-8")
    assert answer.content == expected


def test_an_id_that_is_not_a_run_id_is_400_and_an_unknown_one_is_404(session: Session) -> None:
    """The same rule the results routes apply: a path parameter never reaches a filesystem path
    unvalidated, and "not a run id" is a different answer from "no such run"."""
    client = client_of(session)
    bad = client.get("/api/results/report/not%20an%20id", params={"token": TOKEN})
    assert bad.status_code == 400
    missing = client.get("/api/results/report/r_00000000", params={"token": TOKEN})
    assert missing.status_code == 404
