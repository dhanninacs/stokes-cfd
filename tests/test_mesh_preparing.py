"""`mesh.generate` prepares off the session thread (ADR-0024 addendum 2026-11-14).

**The server is emulated by a one-thread executor**: commands run on it, the geometry store is
bound to it exactly as `stokes.server.app` binds the event loop, and the worker's tree changes come
back to it through `on_session_thread`. So "returns at once" is measured the way the server sees
it: the command returns while the worker is still blocked inside a step, and the loop is free to
answer `mesh.status` and `mesh.cancel` meanwhile.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session, replay
from stokes.api.journal import Journal
from stokes.geometry.store import GeometryStore
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.service import PREPARE_STEPS, MeshService
from stokes.run.hosts import HostRegistry
from tests.test_mesh_service import FakeRunner, settle


class Loop:
    """The server's event loop, as far as the mesh service can tell."""

    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="loop")
        self.ident = self.pool.submit(threading.get_ident).result()

    def dispatch(self, fn: Callable[[], None]) -> None:
        self.pool.submit(fn)

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return self.pool.submit(fn, *args, **kwargs).result(timeout=20)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


class GatedRunner(FakeRunner):
    """A fake runner that can hold a call until the test releases it, and finish jobs it starts."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.gates: dict[str, threading.Event] = {}
        self.entered: dict[str, threading.Event] = {}
        self.hooks: dict[str, Callable[[dict[str, Any]], None]] = {}
        self.auto_finish = False

    def gate(self, method: str) -> threading.Event:
        self.gates[method] = threading.Event()
        self.entered[method] = threading.Event()
        return self.gates[method]

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method in self.gates:
            self.entered[method].set()
            assert self.gates[method].wait(10), f"{method} was never released"
        hook = self.hooks.get(method)
        if hook is not None:
            hook(params)
        answer = super().call(method, timeout=timeout, **params)
        if method == "job.start" and self.auto_finish:
            threading.Thread(target=self.finish_with, daemon=True).start()
        return answer


def served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> tuple[Session, GatedRunner, Loop, list[tuple[str, dict[str, Any]]]]:
    """A box-domain blockMesh project whose commands run on an emulated server loop."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOKES_USER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    session = Session(journal_path=kwargs.pop("journal", None))
    session.execute("project.new", name="mesh")
    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={
            "domain": "box",
            "box": ["0 [m]", "0 [m]", "0 [m]", "1 [m]", "1 [m]", "1 [m]"],
            "base_size": "0.1 [m]",
        },
    )
    fake = GatedRunner(**kwargs)
    HostRegistry.of(session).runner = lambda name: fake  # type: ignore[method-assign]
    loop = Loop()
    GeometryStore.of(session).bind(loop.dispatch, loop.ident)
    events: list[tuple[str, dict[str, Any]]] = []
    session.events.subscribe("*", lambda name, data: events.append((name, dict(data))))
    return session, fake, loop, events


def wait_for(
    events: list[tuple[str, dict[str, Any]]], name: str, timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for seen, data in list(events):
            if seen == name:
                return data
        time.sleep(0.01)
    raise AssertionError(f"no {name} event; saw {[n for n, _ in events]}")


def steps_of(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    out: list[str] = []
    for name, data in events:
        if name == "mesh.preparing" and (not out or out[-1] != data["step"]):
            out.append(str(data["step"]))
    return out


def test_generate_returns_preparing_at_once_and_names_every_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, fake, loop, events = served(tmp_path, monkeypatch)
    release = fake.gate("host.probe")
    try:
        info = loop.run(session.execute, "mesh.generate")
        assert info["state"] == "preparing"
        assert info["result"] and info["steps"] == list(PREPARE_STEPS)
        assert fake.entered["host.probe"].wait(5), "the worker never reached the probe"
        # **The loop is free while the worker waits on the host**: a status call answers, and
        # says where the preparation is.
        status = loop.run(session.execute, "mesh.status")
        assert status["preparing"]["step"] == "probe"
        assert status["preparing"]["step_number"] == 2
        assert not any(name == "job.create" for name, _ in fake.calls)
        release.set()
        prepared = wait_for(events, "mesh.prepared")
        assert prepared["outcome"] == "started"
        assert prepared["node_id"] == info["result"]
        assert steps_of(events) == list(PREPARE_STEPS)
        timed = [d for n, d in events if n == "mesh.preparing"]
        assert all(d["steps"] == len(PREPARE_STEPS) for d in timed)
        assert [d["elapsed"] for d in timed] == sorted(d["elapsed"] for d in timed)
        staged = [d for d in timed if d["step"] == "stage"]
        assert staged[-1]["bytes_sent"] == staged[-1]["bytes_total"] > 0
        node = session.project.find(info["result"])
        assert node is not None and node.params["state"] == "running"
        assert loop.run(session.execute, "mesh.status")["preparing"] == {}
        fake.finish_with()
        settle(session, node.path)
    finally:
        release.set()
        loop.close()


def test_cancel_during_preparation_leaves_the_previous_mesh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, fake, loop, events = served(tmp_path, monkeypatch)
    try:
        # A finished mesh first, made off the loop (inline, as the CLI would).
        first = session.execute("mesh.generate")
        fake.finish_with()
        settle(session, first["path"])
        creates = sum(1 for name, _ in fake.calls if name == "job.create")

        release = fake.gate("host.probe")
        HostRegistry.of(session)._records.clear()  # the probe is cached; ask the host again
        info = loop.run(session.execute, "mesh.generate", force=True)
        assert info["state"] == "preparing"
        assert fake.entered["host.probe"].wait(5), "the worker never reached the probe"
        assert loop.run(session.execute, "mesh.cancel") == {"cancelled": True}
        assert loop.run(session.execute, "mesh.status")["preparing"]["cancelling"] is True
        release.set()
        prepared = wait_for(events, "mesh.prepared")
        assert prepared["outcome"] == "cancelled"
        cancelled = wait_for(events, "mesh.cancelled")
        assert cancelled["node_id"] == info["result"]
        # **Nothing was staged, and the mesh that was there is still there and still done.**
        assert sum(1 for name, _ in fake.calls if name == "job.create") == creates
        kept = session.project.find(first["path"])
        assert kept is not None and kept.id == first["result"]
        assert kept.params["state"] == "done"
        assert session.project.find(info["result"]) is None
        assert MeshService.of(session).wait(2)
        assert loop.run(session.execute, "mesh.cancel") == {"cancelled": False}
    finally:
        loop.close()


def test_cancel_between_staging_chunks_deletes_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stokes.run import staging

    # Small limits, so the blockMesh case goes up in several `job.put` chunks.
    monkeypatch.setattr(staging, "INLINE_LIMIT_BYTES", 64)
    monkeypatch.setattr(staging, "CHUNK_BYTES", 256)
    session, fake, loop, events = served(tmp_path, monkeypatch)
    puts: list[int] = []

    def on_put(params: dict[str, Any]) -> None:
        puts.append(int(params["offset"]))
        if len(puts) == 2:
            assert loop.run(session.execute, "mesh.cancel") == {"cancelled": True}

    fake.hooks["job.put"] = on_put
    try:
        info = loop.run(session.execute, "mesh.generate")
        prepared = wait_for(events, "mesh.prepared")
        assert prepared["outcome"] == "cancelled"
        names = [name for name, _ in fake.calls]
        assert names.count("job.put") == 2, "the upload went on after Cancel"
        assert "job.delete" in names and "job.start" not in names
        node = session.project.find(info["result"])
        assert node is not None and node.params["state"] == "cancelled"
        staged = [d for n, d in events if n == "mesh.preparing" and d["step"] == "stage"]
        assert 0 < staged[-1]["bytes_sent"] < staged[-1]["bytes_total"]
        assert MeshService.of(session).wait(2)
    finally:
        loop.close()


def test_a_refusal_on_the_worker_becomes_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(session: Any, settings: Any, **_: Any) -> Any:
        raise MeshInvalid("the box is inside out")

    session, _fake, loop, events = served(tmp_path, monkeypatch)
    monkeypatch.setattr("stokes.mesh.snapshot.build_snapshot", refuse)
    try:
        info = loop.run(session.execute, "mesh.generate")
        assert info["state"] == "preparing"
        prepared = wait_for(events, "mesh.prepared")
        assert prepared["outcome"] == "failed"
        assert prepared["code"] == "mesh.invalid"
        assert "inside out" in prepared["message"]
        failed = wait_for(events, "mesh.failed")
        assert failed["node_id"] == info["result"]
    finally:
        loop.close()


def test_wait_true_and_replay_run_the_same_steps_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "journal.jsonl"
    session, fake, loop, events = served(tmp_path, monkeypatch, journal=journal)
    fake.auto_finish = True
    try:
        info = loop.run(session.execute, "mesh.generate")
        wait_for(events, "mesh.prepared")
        assert MeshService.of(session).wait(10)
        settle(session, session.project.find(info["result"]).path)
        # `wait=True` off the loop: the steps run on this thread, the tree changes go through the
        # loop, and the answer is the finished mesh.
        done = session.execute("mesh.generate", wait=True, force=True)
        assert done["state"] == "done"
    finally:
        loop.close()

    records = Journal.read(journal)
    generates = [r for r in records if r["command"] == "mesh.generate"]
    assert generates[0]["params"]["result_id"] == info["result"]

    fresh = Session()
    fresh.execute("project.new", name="again")
    other = GatedRunner()
    other.auto_finish = True
    HostRegistry.of(fresh).runner = lambda name: other  # type: ignore[method-assign]
    start = next(i for i, r in enumerate(records) if r["command"] == "mesh.configure")
    replay(fresh, records[start:])
    # **The replay made the node the server's press journaled, and waited for it.**
    final = generates[-1]["params"]["result_id"]
    node = fresh.project.find(final)
    assert node is not None and node.params["state"] == "done"
