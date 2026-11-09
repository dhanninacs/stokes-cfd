"""The `converged` race, deterministically (Opus's mechanism, 2026-10-15).

**This test FAILS on the code before the fix**, which is what makes it worth having: the bug was
intermittent in a way that hid from every trace, and here it is on demand.

The mechanism, in one line: a final `job.state` from the wire **ended the run** (`state=done`)
before
`finish` had made the three RPCs that set `converged`. `settle` and `run.status` both stop at
`record.final`, so a caller read the record inside that window and found `iterations` right
(counted live
from `monitor.residual`) and `converged` still at its default `False`.

**Only `finish` may set a final state.** A final event from the wire leaves the record
non-final, with
`message="collecting"`, and the worker's `finish` writes the real verdict.
"""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Any

import pytest

os.environ.setdefault("STOKES_HOME", tempfile.mkdtemp())
os.environ.setdefault("STOKES_AGENT_HOME", tempfile.mkdtemp())

from stokes.api import Session
from stokes.compile.snapshot import SolveSnapshot
from stokes.run.executors import FoamRunExecutor
from stokes.run.hosts import HostRegistry
from stokes.run.records import RunRecord
from stokes.run.store import RunStore


class BlockingRunner:
    """`job.status` blocks until released, so the window is deterministic rather than timed."""

    name = "local"

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.release = threading.Event()
        self.job = "j_" + "d" * 12

    def call(self, method, timeout=60.0, **params):
        if method == "job.status":
            self.gate.set()  # tell the test we are inside the worker
            self.release.wait(5)  # and hold it there
            return {
                "state": "done",
                "started": 1.0,
                "ended": 2.0,
                "reports": {
                    "solve": {"converged": True, "iterations": 66, "last_residuals": {"Ux": 1e-6}}
                },
            }
        if method == "job.read":
            return {"text": "", "truncated": False}
        return {}

    def subscribe(self, listener):
        return lambda: None


def main() -> int:
    session = Session()
    session.execute("project.new", name="duct")
    store = RunStore.of(session)
    runner = BlockingRunner()
    executor = FoamRunExecutor(session, store)
    record = store.add(RunRecord(id=store.new_id(), name="duct", state="running", job=runner.job))
    store.bind_job(record.id, runner.job)

    # A minimal snapshot: no patches, so the monitor read builds no paths.
    from stokes.compile.snapshot import SolveSnapshot

    snap = SolveSnapshot(
        settings_id="n_s",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=66,
        write_interval=50,
        residual_target=1e-5,
        nu=1.5e-5,
        rho=1.2,
        patches=(),
    )

    # The worker resolves its runner through `HostRegistry`, so point that at the fake — the same
    # shape the mesh tests use.
    from stokes.run.hosts import HostRegistry

    class Registry:
        def runner(self, name):
            return runner

        def probe(self, name, refresh=False):
            return {"available": True, "host": name, "record": {}, "why": ""}

    import stokes.run.executors as ex

    HostRegistry.of = classmethod(lambda cls, s: Registry())
    ex.HostRegistry = HostRegistry

    # 1. The final event arrives.
    executor._on_event(
        record.id, runner.job, "job.state", {"job": runner.job, "state": "done", "step": None}, snap
    )
    # 2. THE ASSERTION: the record must NOT be final yet.
    if store.record(record.id).final:
        print("FAIL: the record went final on the event, before `finish` set converged")
        return 1
    print("ok: the record is still non-final while the worker reads")
    # 3. Release and wait for the worker.
    if not runner.gate.wait(2):
        print("FAIL: the worker never started")
        return 1
    runner.release.set()
    for _ in range(200):
        if store.record(record.id).final:
            break
        threading.Event().wait(0.01)
    # 4. The verdict.
    found = store.record(record.id)
    print(f"final state={found.state} converged={found.converged} iterations={found.iterations}")
    if found.state != "done" or found.converged is not True:
        print("FAIL: the record is not done+converged")
        return 1
    print("PASS")
    return 0


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[Session, RunStore, FoamRunExecutor, Any, Any]:
    """A store, an executor and a runner whose `job.status` blocks on a gate."""
    session = Session()
    session.execute("project.new", name="duct")
    store = RunStore.of(session)
    runner = BlockingRunner()
    executor = FoamRunExecutor(session, store)
    record = store.add(RunRecord(id=store.new_id(), name="duct", state="running", job=runner.job))
    store.bind_job(record.id, runner.job)

    class Registry:
        def runner(self, name: str) -> Any:
            return runner

        def probe(self, name: str, refresh: bool = False) -> dict[str, Any]:
            return {"available": True, "host": name, "record": {}, "why": ""}

    monkeypatch.setattr(HostRegistry, "of", classmethod(lambda cls, s: Registry()))
    return session, store, executor, runner, record


def snapshot() -> SolveSnapshot:
    """A minimal snapshot: no patches, so the monitor read builds no paths."""
    return SolveSnapshot(
        settings_id="n_s",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=66,
        write_interval=50,
        residual_target=1e-5,
        nu=1.5e-5,
        rho=1.2,
        patches=(),
    )


def test_a_final_event_does_not_end_the_run_before_the_report(
    wired: tuple[Session, RunStore, FoamRunExecutor, Any, Any],
) -> None:
    """**The assertion that fails on the old code.** A final event must leave the record NON-final,
    because `converged` is set by `finish`, which has three RPCs to make first."""
    _session, store, executor, runner, record = wired
    executor._on_event(
        record.id,
        runner.job,
        "job.state",
        {"job": runner.job, "state": "done", "step": None},
        snapshot(),
    )
    assert store.record(record.id).final is False, (
        "the record went final on the event, before `finish` set converged"
    )
    # And the reason is visible rather than a run that looks finished with no verdict.
    assert store.status(record.id).get("message") == "collecting"


def test_finish_writes_the_verdict(
    wired: tuple[Session, RunStore, FoamRunExecutor, Any, Any],
) -> None:
    """After the worker's RPCs, the record carries the report's verdict."""
    _session, store, executor, runner, record = wired
    executor._on_event(
        record.id,
        runner.job,
        "job.state",
        {"job": runner.job, "state": "done", "step": None},
        snapshot(),
    )
    assert runner.gate.wait(2), "the worker never started"
    runner.release.set()
    for _ in range(200):
        if store.record(record.id).final:
            break
        threading.Event().wait(0.01)
    found = store.record(record.id)
    assert found.state == "done"
    assert found.converged is True
    assert found.iterations == 66
    assert found.last_residuals == {"Ux": 1e-6}


def test_a_status_that_raises_still_ends_the_run(
    wired: tuple[Session, RunStore, FoamRunExecutor, Any, Any],
) -> None:
    """**The second ranked alternative Opus named.** A `job.status` that raises inside
    `_collect` must
    still end the run — as `done_unconverged`, which is honest, rather than leaving it running
    for ever
    or silently claiming convergence."""
    _session, store, executor, runner, record = wired

    def explode(method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "job.status":
            raise RuntimeError("the agent went away")
        return {}

    runner.call = explode  # type: ignore[method-assign]
    executor._on_event(
        record.id,
        runner.job,
        "job.state",
        {"job": runner.job, "state": "done", "step": None},
        snapshot(),
    )
    for _ in range(200):
        if store.record(record.id).final:
            break
        threading.Event().wait(0.01)
    found = store.record(record.id)
    assert found.final is True, "a failed read must not leave the run running for ever"
    assert found.converged is False
    assert found.state == "done_unconverged"
