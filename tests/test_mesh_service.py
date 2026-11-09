"""The mesh service: generate, follow, reuse, staleness (T-145b, T-207).

**A fake runner, no OpenFOAM.** Every test registers a runner in `HostRegistry` that records its
calls
and plays back `job.state` events from a thread — exactly what the agent does, without a socket
or an
install. The engine test that runs a real mesh is `tests/engine/test_mesh_slice_engine.py`.

The property worth naming: **a result is written by a job and is not an undo step.** Several
tests check
that, because "the mesh appears in the undo history" is the kind of mistake that only shows up
when a user
presses Ctrl-Z after a long run.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.errors import StokesError
from stokes.mesh.service import MeshService
from stokes.run.hosts import HostRegistry
from tests.mesh_surface_support import SurfaceServer


class FakeRunner:
    """A runner that records its calls and plays job events back.

    ``available=False`` makes it a host with no OpenFOAM, which is the `skipped` path.
    """

    name = "local"

    def __init__(
        self, *, available: bool = True, finish: str = "done", error: dict | None = None
    ) -> None:
        self.available = available
        self.why = "" if available else "No OpenFOAM host on this machine"
        self.finish = finish
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.listeners: list[Any] = []
        self.job = "j_" + "a" * 12
        #: The checkMesh report the job returns. **Empty by default**, so a test that does not set
        #: one is exercising the "nothing measured the mesh" path.
        self.report: dict[str, Any] = {}
        #: Answers `extract.surfaces` and `artifact.get` (T-210) from an in-memory artifact.
        self.surfaces = SurfaceServer()

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
        served = self.surfaces.handle(method, params)
        if served is not None:
            return served
        if method == "job.create":
            return {"job": self.job}
        if method == "job.status":
            return {
                "state": self.finish,
                "error": self.error,
                "steps": [
                    {"name": "blockMesh", "state": self.finish, "rc": 0},
                    {"name": "checkMesh", "state": self.finish, "rc": 0},
                ],
                "reports": {"checkMesh": self.report} if self.report else {},
            }
        return {}

    def subscribe(self, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener) if listener in self.listeners else None

    def close(self) -> None:
        return

    def finish_with(self, state: str | None = None) -> None:
        """Play the two events the agent sends for a completed job."""
        self._fire(
            {"job": self.job, "state": "running", "step": "blockMesh", "index": 0, "total": 1}
        )
        self._fire(
            {"job": self.job, "state": state or self.finish, "step": None, "index": 1, "total": 1}
        )

    def _fire(self, data: dict[str, Any]) -> None:
        for listener in list(self.listeners):
            listener("job.state", data)


def make(tmp_path: Path, **runner_kwargs: Any) -> tuple[Session, FakeRunner]:
    """A session with a box-domain mesh configured and a fake runner wired in."""
    import os

    os.environ["STOKES_HOME"] = str(tmp_path / "home")
    os.environ["STOKES_AGENT_HOME"] = str(tmp_path / "agent")
    session = Session()
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
    fake = FakeRunner(**runner_kwargs)
    HostRegistry.of(session).runner = lambda name: fake  # type: ignore[method-assign]
    return session, fake


def settle(session: Session, path: str, state: str = "done", timeout: float = 5.0) -> None:
    """Wait for the result node to reach a state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        node = session.project.find(path)
        if node is not None and node.params.get("state") == state:
            return
        time.sleep(0.02)
    node = session.project.find(path)
    raise AssertionError(
        f"{path} never reached {state}: {node.params.get('state') if node else None}"
    )


def test_generate_creates_a_running_result_and_finishes_on_the_event(tmp_path: Path) -> None:
    """The whole flow: the node appears `running`, the job gets the case, and `done` flips it."""
    session, fake = make(tmp_path)
    events: list[str] = []
    session.events.subscribe("*", lambda name, data: events.append(name))
    info = session.execute("mesh.generate")
    assert info["state"] == "running"
    assert info["path"] == "/mesh/results/blockmesh"
    assert info["reused"] is False

    # **The job got exactly the driver's files**, so the command and the driver cannot disagree.
    created = next(p for name, p in fake.calls if name == "job.create")
    assert set(created["files"]) == {"system/blockMeshDict", "system/controlDict"}
    assert "blocks" in created["files"]["system/blockMeshDict"]
    assert created["steps"][0]["argv"] == ["blockMesh"]
    # T-208 added the checkMesh step; a job is now two utilities.
    assert [step["name"] for step in created["steps"]] == ["blockMesh", "checkMesh"]
    # An engine ID, never the bashrc path (FIXES-A A2).
    assert created["install"] == "e_000000000001"

    fake.report = dict(GOOD_REPORT)
    fake.finish_with()
    settle(session, info["path"])
    # **`ok` comes from the checkMesh report** (T-208): `done` is the job finishing, `ok` is the
    # mesh
    # passing, and this fake reports a good one.
    assert session.project.find(info["path"]).params["ok"] is True
    assert "node.added" in events and "mesh.progress" in events and "mesh.done" in events
    # The order the brief asks for.
    assert events.index("node.added") < events.index("mesh.progress") < events.index("mesh.done")


def test_generate_is_not_an_undo_step(tmp_path: Path) -> None:
    """**The rule that matters for a user's Ctrl-Z.** The configure is undoable; the result is
    not, and
    undoing once after a generate must undo the *configure*, not the generation."""
    session, fake = make(tmp_path)
    # **G2 gives every project a `/mesh/settings` from `project.new`**, so this test can no
    # longer use
    # "the node disappeared" as its evidence — the node is not the configure's to create any
    # more. The
    # rule it exists for is unchanged and is asserted directly: **a generate pushes NO undo
    # step**, so
    # one undo after a generate reverses the configure and leaves the result alone.
    before = session.undo.can_undo
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    session.execute("history.undo")
    if before:
        # A configure ran before the generate, so the undo is available and must hit the configure.
        settings = session.project.find("/mesh/settings")
        assert settings is not None, "the node is not the configure's to remove"
        assert float(settings.params["base_size"].value) != pytest.approx(0.05), "not restored"
    assert session.project.find(info["path"]) is not None, "the result was undone; it must not be"


def test_a_second_generate_reuses_a_done_result(tmp_path: Path) -> None:
    """**A mesh costs minutes.** The same inputs must not start an identical job."""
    session, fake = make(tmp_path)
    first = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, first["path"])
    creates = sum(1 for name, _ in fake.calls if name == "job.create")
    second = session.execute("mesh.generate")
    assert second["reused"] is True
    assert second["path"] == first["path"], "a reuse must not replace the node"
    assert sum(1 for name, _ in fake.calls if name == "job.create") == creates


def test_force_meshes_again(tmp_path: Path) -> None:
    """`force=True` is how a user says "run it anyway"."""
    session, fake = make(tmp_path)
    first = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, first["path"])
    before = sum(1 for name, _ in fake.calls if name == "job.create")
    again = session.execute("mesh.generate", force=True)
    assert again["reused"] is False
    assert sum(1 for name, _ in fake.calls if name == "job.create") == before + 1


def test_a_host_that_cannot_mesh_is_skipped_not_failed(tmp_path: Path) -> None:
    """**ADR-0024.** A machine without OpenFOAM is a fact about the machine; a replay that
    stops there
    cannot be replayed. The result says `skipped` with the reason, and no job is created."""
    session, fake = make(tmp_path, available=False)
    info = session.execute("mesh.generate")
    assert info["state"] == "skipped"
    assert not any(name == "job.create" for name, _ in fake.calls)
    node = session.project.find(info["path"])
    assert node is not None and node.params["state"] == "skipped"
    assert str(node.params["error"]).startswith("mesh.no_host")


def test_a_failed_job_marks_the_result_failed(tmp_path: Path) -> None:
    session, fake = make(
        tmp_path, finish="failed", error={"code": "agent.step_failed", "message": "rc 1"}
    )
    info = session.execute("mesh.generate")
    fake.finish_with("failed")
    settle(session, info["path"], "failed")
    node = session.project.find(info["path"])
    assert node.params["ok"] is False
    assert "agent.step_failed" in str(node.params["error"])


def test_the_result_carries_the_patch_names_and_types(tmp_path: Path) -> None:
    """**T-210 and T-214 read these.** A box-domain snapshot has no tags, so `patch_tags` is empty
    and `patch_types` holds the one default patch — what this proves is that the keys exist and are
    dicts, so a reader does not have to guard."""
    session, _fake = make(tmp_path)
    info = session.execute("mesh.generate")
    node = session.project.find(info["path"])
    assert node.meta["patch_tags"] == {}
    # **A box domain has no tags, so all six sides are `unassigned`**, which is a wall patch the
    # mesh does have (FIXES-B B4): the list must carry it, not be empty.
    assert node.meta["patch_types"] == {"unassigned": "wall"}
    assert node.meta["engine"] == "org-14"


def test_staleness_follows_a_setting_and_its_undo(tmp_path: Path) -> None:
    """**A change makes the result stale; undoing the change makes it fresh again.** Nothing
    re-meshes by
    itself — a user who changed a size has not necessarily asked for a new mesh."""
    session, fake = make(tmp_path)
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    assert session.execute("mesh.status")["stale"] is False

    session.execute("mesh.configure", values={"base_size": "0.05 [m]"})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not session.execute("mesh.status")["stale"]:
        time.sleep(0.05)
    assert session.execute("mesh.status")["stale"] is True
    assert session.project.find(info["path"]).status == "stale"

    session.execute("history.undo")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and session.execute("mesh.status")["stale"]:
        time.sleep(0.05)
    assert session.execute("mesh.status")["stale"] is False


def test_cancel_calls_the_agent_and_the_event_sets_the_state(tmp_path: Path) -> None:
    session, fake = make(tmp_path, finish="cancelled")
    info = session.execute("mesh.generate")
    assert session.execute("mesh.cancel") == {"cancelled": True}
    assert any(name == "job.cancel" for name, _ in fake.calls)
    fake.finish_with("cancelled")
    settle(session, info["path"], "cancelled")
    assert session.project.find(info["path"]).params["state"] == "cancelled"


def test_cancelling_nothing_is_false(tmp_path: Path) -> None:
    session, fake = make(tmp_path)
    del fake
    assert session.execute("mesh.cancel") == {"cancelled": False}


def test_wait_is_refused_on_the_session_thread(tmp_path: Path) -> None:
    """**The server IS the session thread**,
    so waiting would deadlock it. The refusal names the events
    to follow instead."""
    session, fake = make(tmp_path)
    del fake
    service = MeshService.of(session)
    # Mark the calling thread as the session thread, which is what the server does.
    from stokes.geometry.store import GeometryStore

    GeometryStore.of(session)._session_thread = threading.get_ident()
    with pytest.raises(StokesError) as caught:
        session.execute("mesh.generate", wait=True)
    assert "wait=true would block" in str(caught.value)
    assert service is not None


def test_generate_needs_settings(tmp_path: Path) -> None:
    """A validation error, raised rather than written into a result: a replay would hit it too."""
    import os

    os.environ["STOKES_HOME"] = str(tmp_path / "home")
    session = Session()
    session.execute("project.new", name="t")
    HostRegistry.of(session).runner = lambda name: FakeRunner()  # type: ignore[method-assign]
    with pytest.raises(StokesError) as caught:
        session.execute("mesh.generate")
    assert caught.value.code == "mesh.invalid"


def test_a_second_generate_replaces_the_result_and_cancels_the_first(tmp_path: Path) -> None:
    """**One result per mesher** (ADR-0034). A new generate of the same mesher cancels what is
    running and swaps the node in."""
    session, fake = make(tmp_path)
    first = session.execute("mesh.generate")
    second = session.execute("mesh.generate", force=True)
    assert second["path"] == first["path"], "one result, so the path is the same"
    assert second["result"] != first["result"], "but a new node, so the old one's job is gone"
    assert any(name == "job.cancel" for name, _ in fake.calls), "the first job was not cancelled"


def kept_result(session: Session, mesher: str, cells: int) -> str:
    """A finished result of another mesher, as an earlier generate would have left it."""
    from stokes.model.ids import new_id, stable_id

    project = session.project
    if project.find("/mesh/results") is None:
        project.add("/mesh", "group", "results", node_id=stable_id("g", "/mesh/results"))
    node = project.add(
        "/mesh/results",
        "mesh_result",
        mesher,
        {"mesher": mesher, "state": "done", "cells": cells, "job": "j_" + "b" * 12},
        node_id=new_id("n"),
    )
    return str(node.id)


def test_each_mesher_keeps_its_own_result_and_the_new_one_is_in_use(tmp_path: Path) -> None:
    """ADR-0034: generating with one mesher no longer deletes another mesher's mesh."""
    session, fake = make(tmp_path)
    poly = kept_result(session, "cfmesh_poly", 79149)
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    listed = {row["mesher"]: row for row in session.execute("mesh.list")["results"]}
    assert set(listed) == {"cfmesh_poly", "blockmesh"}, "the polyhedral mesh is still there"
    assert listed["blockmesh"]["active"] is True and listed["cfmesh_poly"]["active"] is False
    assert session.execute("mesh.status")["result"] == info["result"]
    # The same mesher again replaces only its own result.
    again = session.execute("mesh.generate", force=True)
    fake.finish_with()
    settle(session, again["path"])
    meshers = [row["mesher"] for row in session.execute("mesh.list")["results"]]
    assert sorted(meshers) == ["blockmesh", "cfmesh_poly"]
    assert session.project.find(poly) is not None


def test_mesh_use_picks_the_mesh_shown_and_solved_on(tmp_path: Path) -> None:
    session, fake = make(tmp_path)
    poly = kept_result(session, "cfmesh_poly", 79149)
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    status = session.execute("mesh.use", result=poly)
    assert status["result"] == poly and status["cells"] == 79149
    assert session.execute("mesh.status")["result"] == poly, "a run reads mesh.status"
    listed = {row["id"]: row["active"] for row in session.execute("mesh.list")["results"]}
    assert listed == {poly: True, info["result"]: False}
    # Going back to blockMesh with nothing changed takes its kept mesh instead of meshing again.
    creates = sum(1 for name, _ in fake.calls if name == "job.create")
    back = session.execute("mesh.generate")
    assert back["reused"] is True and back["result"] == info["result"]
    assert sum(1 for name, _ in fake.calls if name == "job.create") == creates
    assert session.execute("mesh.status")["result"] == info["result"]
    with pytest.raises(StokesError) as caught:
        session.execute("mesh.use", result="n_00000000")
    assert caught.value.code == "mesh.no_result"


def test_mesh_use_is_refused_while_another_mesh_is_being_made(tmp_path: Path) -> None:
    session, _fake = make(tmp_path)
    poly = kept_result(session, "cfmesh_poly", 10)
    session.execute("mesh.generate")  # running: the fake has not finished it
    with pytest.raises(StokesError) as caught:
        session.execute("mesh.use", result=poly)
    assert caught.value.code == "mesh.busy"


def test_a_job_stopped_by_a_new_mesh_does_not_stay_running(tmp_path: Path) -> None:
    """The stopped job's result may now be another mesher's, which stays: it must not read
    "Meshing" for ever."""
    session, _fake = make(tmp_path)
    stuck = kept_result(session, "cfmesh_tet", 10)
    session.project.set_param(stuck, "state", "running")
    session.execute("mesh.generate")
    assert session.project.find(stuck).params["state"] == "cancelled"


def test_the_log_comes_from_the_agent(tmp_path: Path) -> None:
    """`mesh.log` reads the running step's log through the runner,
    and answers empty before there is a
    step rather than raising."""
    session, fake = make(tmp_path)
    assert session.execute("mesh.log") == {"step": "", "lines": [], "truncated": False}
    session.execute("mesh.generate")
    fake._fire({"job": fake.job, "state": "running", "step": "blockMesh", "index": 0, "total": 1})
    time.sleep(0.1)
    reply = session.execute("mesh.log", lines=10)
    assert reply["step"] == "blockMesh"
    assert any(name == "log.tail" for name, _ in fake.calls)


def test_the_result_node_is_written_through_meta_not_invented_parameters(tmp_path: Path) -> None:
    """**A `mesh_result` has no parameter for `engine` or `warnings`.** Inventing them would be
    a schema
    change for something one service reads; `meta` is free-form and persisted, and this is what
    stops the
    next person adding a parameter instead."""
    from stokes.model import registry

    names = {param.name for param in registry.get("mesh_result").params}
    assert "engine" not in names and "warnings" not in names
    session, fake = make(tmp_path)
    info = session.execute("mesh.generate")
    node = session.project.find(info["path"])
    assert "engine" in node.meta and "warnings" in node.meta
    assert "engine" not in node.params
    del fake


# ---------------------------------------------------------------- checkMesh quality (T-208)

GOOD_REPORT = {
    "cells": 1000,
    "points": 1331,
    "faces": 3300,
    "max_non_ortho": 5.95,
    "avg_non_ortho": 1.63,
    "max_skewness": 0.26,
    "mesh_ok": True,
    "failed_checks": 0,
}


def _with_report(tmp_path: Path, report: dict[str, Any], **values: Any) -> tuple[Session, Any]:
    """A session whose fake job returns a checkMesh report."""
    import os

    os.environ["STOKES_HOME"] = str(tmp_path / "home")
    os.environ["STOKES_AGENT_HOME"] = str(tmp_path / "agent")
    session = Session()
    session.execute("project.new", name="mesh")
    settings = {
        "domain": "box",
        "box": ["0 [m]", "0 [m]", "0 [m]", "1 [m]", "1 [m]", "1 [m]"],
        "base_size": "0.1 [m]",
    }
    settings.update(values)
    session.execute("mesh.configure", mesher="blockmesh", values=settings)
    fake = FakeRunner()
    real_call = fake.call

    def call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "job.status":
            reply = real_call(method, timeout, **params)
            reply["reports"] = {"checkMesh": report}
            return reply
        return real_call(method, timeout, **params)

    fake.call = call  # type: ignore[method-assign]
    HostRegistry.of(session).runner = lambda name: fake  # type: ignore[method-assign]
    return session, fake


def _finish(session: Session, fake: Any) -> dict[str, Any]:
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    return session.execute("mesh.status")


def test_a_good_mesh_writes_its_quality(tmp_path: Path) -> None:
    """The numbers reach the node and the status: `cells`, `points`, `faces`, and a `quality`
    dict with
    the settings' thresholds in it."""
    session, fake = _with_report(tmp_path, GOOD_REPORT)
    status = _finish(session, fake)
    assert status["cells"] == 1000
    assert status["ok"] is True
    assert status["nodes"] == {"points": 1331, "faces": 3300}
    node = session.project.find(status["path"])
    assert node.params["cells"] == 1000
    quality = node.params["quality"]
    assert quality["mesh_ok"] is True
    assert quality["max_non_ortho"] == pytest.approx(5.95)
    assert quality["thresholds"] == {"max_non_ortho": 70.0, "max_skewness": 4.0}
    assert "patches" not in quality, "the patch table is not a quality number"
    assert node.status == "ok"


def test_a_threshold_stricter_than_the_mesh_fails_it(tmp_path: Path) -> None:
    """**`ok` is the report's verdict AND the user's thresholds.** A mesh that satisfies
    OpenFOAM can still
    fail this project's bar, which is the entire reason the thresholds exist."""
    session, fake = _with_report(tmp_path, GOOD_REPORT, max_non_ortho=1.0)
    status = _finish(session, fake)
    assert status["ok"] is False
    assert session.project.find(status["path"]).status == "warning", (
        "a real mesh that misses the bar"
    )


def test_a_mesh_that_fails_its_own_checks_is_not_ok(tmp_path: Path) -> None:
    """OpenFOAM's own verdict. **And the step still succeeded**: checkMesh exits 0 while reporting
    `Failed 2 mesh checks.` (measured), so the job is `done` and `ok` is false — the two are
    different
    facts and a UI shows them differently."""
    report = {**GOOD_REPORT, "mesh_ok": False, "failed_checks": 2, "max_skewness": 21.4}
    session, fake = _with_report(tmp_path, report)
    status = _finish(session, fake)
    assert status["state"] == "done", "the job ran; it is the mesh that failed its checks"
    assert status["ok"] is False
    assert session.project.find(status["path"]).status == "warning"


def test_a_missing_figure_is_not_a_pass(tmp_path: Path) -> None:
    """**`None` means "not measured", and a mesh nobody measured is not one to call good.** A
    defaulted
    zero would report a mesh fine on the strength of a number nobody read."""
    session, fake = _with_report(tmp_path, {"cells": 1000, "mesh_ok": True})
    status = _finish(session, fake)
    assert status["ok"] is False


def test_a_parser_that_failed_is_not_a_pass(tmp_path: Path) -> None:
    """A parser exception becomes `{"error": …}` in the report and never fails the job — but
    the mesh is
    not `ok` either, because nothing read it."""
    session, fake = _with_report(tmp_path, {"error": "ValueError: the format changed"})
    status = _finish(session, fake)
    assert status["state"] == "done"
    assert status["ok"] is False
    assert session.project.find(status["path"]).status == "warning"


def test_no_report_at_all_is_not_a_pass(tmp_path: Path) -> None:
    """A job with no parser still finishes `done`, and `ok` says nothing measured it.

    The fake answers `host.probe`, `job.create` and `job.status` itself; this only removes the
    report, so
    the service sees a finished job that told it nothing about the mesh. **An earlier version of
    this test
    replaced `call` wholesale and forgot `host.probe`** — the service then saw a host with no
    install and
    wrote `skipped`, which the test read as a failure of the thing it was testing.
    """
    session, fake = make(tmp_path)
    real_call = fake.call

    def call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        reply = real_call(method, timeout, **params)
        if method == "job.status":
            reply.pop("reports", None)
        return reply

    fake.call = call  # type: ignore[method-assign]
    status = _finish(session, fake)
    assert status["state"] == "done"
    assert status["ok"] is False


# ---------------------------------------------------------------- FIXES-B


class SequencedRunner(FakeRunner):
    """A fake whose every `job.create` gets a NEW id, with hooks for the two failures below."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.created: list[str] = []
        self.on_create: Any = None
        self.start_error: Exception | None = None

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "job.create":
            self.calls.append((method, params))
            self.job = f"j_{len(self.created) + 1:012x}"
            self.created.append(self.job)
            if self.on_create is not None:
                self.on_create(self.job)
            return {"job": self.job}
        if method == "job.start" and self.start_error is not None:
            self.calls.append((method, params))
            raise self.start_error
        return super().call(method, timeout, **params)


def make_sequenced(tmp_path: Path, **kwargs: Any) -> tuple[Session, SequencedRunner]:
    session, _ = make(tmp_path)
    fake = SequencedRunner(**kwargs)
    HostRegistry.of(session).runner = lambda name: fake  # type: ignore[method-assign]
    return session, fake


def test_a_job_that_cannot_start_ends_the_result_and_releases_the_runner(tmp_path: Path) -> None:
    """**B3.** `job.start` raising used to leave the result `running` for ever and `_job` set."""
    session, fake = make_sequenced(tmp_path)
    fake.start_error = RuntimeError("the agent went away")
    info = session.execute("mesh.generate")
    assert info["state"] == "failed", info
    node = session.project.find(info["path"])
    assert node.params["state"] == "failed"
    assert "the agent went away" in str(node.params["error"])
    service = MeshService.of(session)
    assert service._job is None, "the runner state must be released"
    assert service.wait(0.5) is True, "nothing is running, so nothing to wait for"
    assert any(name == "job.delete" for name, _ in fake.calls), "the unstarted job is removed"


def test_the_old_jobs_final_event_cannot_end_the_new_result(tmp_path: Path) -> None:
    """**B8.** The old job's final event landing while the NEW generate is under way (here: during
    its `job.create`) must not mark the new result cancelled or clear `_job`."""
    session, fake = make_sequenced(tmp_path, finish="cancelled")
    session.execute("mesh.generate")
    old = fake.job

    def late_final_event(new_job: str) -> None:
        assert new_job != old
        fake._fire({"job": old, "state": "cancelled", "step": None, "index": 1, "total": 1})

    fake.on_create = late_final_event
    info = session.execute("mesh.generate", force=True)
    service = MeshService.of(session)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        time.sleep(0.05)
        node = session.project.find(info["path"])
        assert node.params["state"] == "running", "the old job's event ended the new result"
    assert service._job == fake.job != old


def test_the_seconds_come_from_a_record_the_agent_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**B2.** `mesh_result.seconds` was always 0: it read a top-level `started` the record never
    has. The record here is written by `JobStore` itself (create, start, finish), and its elapsed
    time must come out as a real number of seconds."""
    from stokes.mesh.service import _job_seconds
    from stokes_agent import jobs
    from stokes_agent.engine_env import EngineEnv
    from stokes_agent.jobs import JobStore, Step

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "blockMesh.exe").write_bytes(b"")
    (bin_dir / "blockMesh").write_bytes(b"")
    # **Executable, not merely present.** `shutil.which` checks the exec bit on POSIX; on Windows it
    # checks the extension instead, so a 0644 `blockMesh` resolves there and not here. Without
    # this the
    # test passes on Windows and fails on Linux with
    # `JobMissingUtility: blockMesh is not on the engine's PATH` — a platform difference in
    # the FIXTURE,
    # not in the code under test. (This file's T-210 revision dropped the chmod; re-applied.)
    (bin_dir / "blockMesh").chmod(0o755)
    made = EngineEnv(
        bashrc=str(tmp_path / "bashrc"),
        env={"PATH": str(bin_dir)},
        project="OpenFOAM",
        version="14",
        flavour="org",
        seconds=0.0,
    )
    monkeypatch.setattr(jobs, "resolve_engine", lambda engine=None: made)
    monkeypatch.setattr(jobs, "_posix_host", lambda: True)

    def fake_run(
        self: JobStore,
        job: str,
        job_dir: Path,
        steps: Any,
        engine: Any,
        profile: str | None = None,
    ) -> None:
        time.sleep(0.2)
        self._set_step(job_dir, 0, "done", 0)
        self._finish(job, job_dir, None)

    monkeypatch.setattr(JobStore, "_run", fake_run)
    store = JobStore(tmp_path / "jobs")
    job = store.create({"system/controlDict": "x"}, [Step("mesh", ("blockMesh",))])
    store.start(job)
    deadline = time.monotonic() + 10
    while store.status(job)["state"] != "done" and time.monotonic() < deadline:
        time.sleep(0.02)
    record = store.status(job)
    assert record["state"] == "done" and "started" not in record, "the shape under test"
    seconds = _job_seconds(record)
    assert 0.15 <= seconds < 10.0, seconds
    # A job still running has no `ended`, and a record with nothing to read counts 0.0.
    assert _job_seconds({**record, "ended": None}) == 0.0
    assert _job_seconds({}) == 0.0


def test_a_partly_tagged_domain_keeps_its_unassigned_wall_in_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**B4.** Only the inlet and the outlet are tagged; the other four sides are the patch
    `unassigned`, a wall, in the written dictionary AND in `mesh_result.meta.patch_types`."""
    from stokes.mesh.snapshot import MeshSnapshot

    session, fake = make(tmp_path)
    snap = MeshSnapshot(
        settings_id="n_s",
        mesher="blockmesh",
        base_size=0.1,
        bbox=(0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
        sides={"xmin": "inlet", "xmax": "outlet"},
        side_tags={"xmin": "t_in", "xmax": "t_out"},
        side_kinds={"xmin": "velocity_inlet", "xmax": "pressure_outlet"},
        unmapped=(),
        max_cells=5_000_000,
        cores=1,
        max_non_ortho=70.0,
        max_skewness=4.0,
        domain_part=None,
        part_basis="",
        tag_members={},
    )
    monkeypatch.setattr("stokes.mesh.snapshot.build_snapshot", lambda session, settings: snap)
    info = session.execute("mesh.generate")
    node = session.project.find(info["path"])
    assert node.meta["patch_types"] == {"inlet": "patch", "outlet": "patch", "unassigned": "wall"}
    created = next(p for name, p in fake.calls if name == "job.create")
    text = created["files"]["system/blockMeshDict"]
    assert "unassigned\n    {\n        type            wall;" in text


def test_wait_does_not_report_finished_before_a_job_exists(tmp_path: Path) -> None:
    """**The window between `cancel()` and `_reset` — and it needs no concurrency at all.**

    `self._finished` is created once in `__init__`, cleared only by `_reset`, and **`cancel()`
    does not
    clear it**. So on a second generate of a mesh that already finished, there is a window where
    `_finished` is *still set from the previous job* while `_replace` has already removed the
    old node —
    and `wait()` in that window returns `True` although nothing is running.

    **Journal replay reaches it**: `Session.settle` calls every extension's `settle` hook, and
    `MeshService.settle` calls `wait`. A replay would therefore advance past a record whose
    mesh has not
    started.

    The assertion is on the *effect* — "nothing is running, so do not say finished" — and it is
    deterministic: `Event.wait(0)` returns the flag's current value, so this is not a timing race.
    """
    session, fake = make(tmp_path)
    first = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, first["path"])  # `_finished` is now SET
    service = MeshService.of(session)

    # Block `job.create`, so the generate is caught inside the window. `fake.calls` is only
    # appended after the blocked call returns, so signal entry separately: the first generate's
    # `job.create` is already in that list, and waiting for a second entry there would deadlock.
    release = threading.Event()
    create_entered = threading.Event()
    real_call = fake.call

    def blocking_call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "job.create":
            create_entered.set()
            release.wait(timeout=10)
        return real_call(method, timeout, **params)

    fake.call = blocking_call  # type: ignore[method-assign]
    started = threading.Thread(
        target=lambda: session.execute("mesh.generate", force=True), daemon=True
    )
    started.start()
    try:
        # Wait until the generate is really inside the window: `job.create` has been entered.
        assert create_entered.wait(5.0), "the generate never reached create"

        # **Nothing is running yet**, so `wait` must not claim it is finished.
        assert service.wait(0.0) is False, (
            "wait() reported finished while the new job had not been created"
        )
    finally:
        release.set()
        started.join(timeout=10)


def test_a_replaced_result_is_not_finished_by_the_old_jobs_worker(tmp_path: Path) -> None:
    """**`_finish_with` must act only on the result it is finishing.**

    The worker reads `self._job` *after* being spawned, so a second generate can replace it — after
    which the worker fetches the wrong job's status and looks up a `result_id` that `_replace` has
    already removed. This pins the guard: a finish for a replaced result must not touch the
    service's
    current job.
    """
    session, _fake = make(tmp_path)
    first = session.execute("mesh.generate")
    service = MeshService.of(session)
    stale_id = first["result"]

    second = session.execute("mesh.generate", force=True)
    assert second["result"] != stale_id, "the second generate must replace the node"
    current = service._job  # the new job

    # The OLD job's finish arrives late. It must not clear the new job's id.
    with service._lock:
        assert service._job == current
    assert current is not None
    assert service.wait(0.0) is False, "the new job is still running"


# ------------------------------------------------------------------ the fidelity block


def test_a_measured_fidelity_reaches_the_quality_record(tmp_path: Path) -> None:
    """**The whole chain.** The agent answers `mesh.fidelity`, and the numbers must arrive on the
    result node inside `quality` - which is what the card reads.
    """
    session, fake = _with_report(tmp_path, GOOD_REPORT)
    real_call = fake.call
    measured = {
        "max_deviation_mm": 0.32,
        "max_deviation_pct": 0.16,
        "mean_deviation_mm": 0.041,
        "volume_error_pct": 0.14,
        "feature_capture_pct": 100.0,
        "features": 12,
        "sampled": 56170,
        "points": 56170,
        "verdict": "Good",
    }

    def call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "mesh.fidelity":
            # **Recorded here, because this wrapper replaces the real `call` for this method.** The
            # fake appends inside its own `call`, which this branch never reaches, so the assertion
            # below found no call and raised StopIteration - the block itself was already in the
            # record by then, so the chain was working.
            fake.calls.append((method, params))
            return measured
        return real_call(method, timeout, **params)

    fake.call = call  # type: ignore[method-assign]
    # **Set AFTER generate, not before.** `generate` calls `_reset`, which derives the case from the
    # snapshot - and this session meshes with blockMesh, which stages no STL, so it is legitimately
    # None. Setting it before `_finish` was overwritten and the test passed vacuously (measured: the
    # agent was never asked). Installing it here is what makes the chain actually run.
    from stokes.mesh.service import _FidelityCase

    service = MeshService.of(session)
    real_generate = service.generate

    def generate(*args: Any, **kwargs: Any) -> Any:
        info = real_generate(*args, **kwargs)
        service._fidelity_case = _FidelityCase(
            surfaces=("constant/geometry/walls.stl",),
            characteristic_length=0.5,
            cell_size=0.002,
        )
        return info

    service.generate = generate  # type: ignore[method-assign]
    status = _finish(session, fake)
    quality = session.project.find(status["path"]).params["quality"]
    assert "fidelity" in quality, "the fidelity block did not reach the record"
    assert quality["fidelity"]["verdict"] == "Good"
    assert quality["fidelity"]["max_deviation_mm"] == pytest.approx(0.32)
    # And the agent was asked with the case's own numbers, not invented ones.
    asked = next(params for method, params in fake.calls if method == "mesh.fidelity")
    assert asked["cad_surfaces"] == ["constant/geometry/walls.stl"]
    assert asked["characteristic_length"] == pytest.approx(0.5)
    assert asked["cell_size"] == pytest.approx(0.002)


def test_a_fidelity_that_FAILS_does_not_fail_the_mesh(tmp_path: Path) -> None:
    """**The measurement is a report ABOUT a mesh, not part of making one.** The mesh exists and its
    quality is measured; a comparison with its CAD that could not be made leaves the block absent -
    and the card then shows nothing rather than showing a pass.
    """
    session, fake = _with_report(tmp_path, GOOD_REPORT)
    real_call = fake.call

    def call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "mesh.fidelity":
            raise RuntimeError("the host refused the measurement")
        return real_call(method, timeout, **params)

    fake.call = call  # type: ignore[method-assign]
    status = _finish(session, fake)
    assert status["state"] == "done", "a failed measurement failed the mesh"
    assert status["ok"] is True, "the mesh's own checks passed"
    quality = session.project.find(status["path"]).params["quality"]
    assert "fidelity" not in quality, "an absent measurement must not appear as a block"


def test_a_mesh_with_no_staged_surfaces_is_not_measured_at_all(tmp_path: Path) -> None:
    """A blockMesh case stages no STL, so there is nothing to measure against. The agent must not
    even be asked, and the block must be absent rather than empty."""
    session, fake = _with_report(tmp_path, GOOD_REPORT)
    _finish(session, fake)
    assert not any(method == "mesh.fidelity" for method, _ in fake.calls), (
        "the agent was asked for a fidelity measurement with no CAD to measure against"
    )


def test_the_fidelity_case_is_None_without_staged_surfaces() -> None:
    """The record's own contract: a snapshot with no `stl_files` yields None, which is what makes
    the block absent rather than four blank rows."""
    from stokes.mesh.service import _FidelityCase

    class NoSurfaces:
        base_size = 0.01

    assert _FidelityCase.of(NoSurfaces()) is None

    class WithSurfaces:
        stl_files = {
            "constant/geometry/inlet.stl": "solid x\n",
            "constant/geometry/walls.stl": "s\n",
        }
        background_min = (0.0, 0.0, 0.0)
        background_max = (3.0, 4.0, 0.0)
        base_size = 0.01

    case = _FidelityCase.of(WithSurfaces())
    assert case is not None
    assert case.surfaces == ("constant/geometry/inlet.stl", "constant/geometry/walls.stl")
    assert case.characteristic_length == pytest.approx(5.0), "the box diagonal is 3-4-0"
    assert case.cell_size == pytest.approx(0.01)


# ---------------------------------------------------------------- layer coverage (M-504)


def _with_layers(
    tmp_path: Path, layer_report: dict[str, Any] | None, check: dict[str, Any] | None = GOOD_REPORT
) -> tuple[Session, Any]:
    """A session whose fake job returns the checkMesh report AND the snappy layer report, each
    under its own step name - the shape a real two-parser job produces."""
    import os

    os.environ["STOKES_HOME"] = str(tmp_path / "home")
    os.environ["STOKES_AGENT_HOME"] = str(tmp_path / "agent")
    session = Session()
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
    fake = FakeRunner()
    real_call = fake.call

    def call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "job.status":
            reply = real_call(method, timeout, **params)
            reports: dict[str, Any] = {}
            if check is not None:
                reports["checkMesh"] = check
            if layer_report is not None:
                reports["snappyHexMesh"] = layer_report
            reply["reports"] = reports
            return reply
        return real_call(method, timeout, **params)

    fake.call = call  # type: ignore[method-assign]
    HostRegistry.of(session).runner = lambda name: fake  # type: ignore[method-assign]
    return session, fake


LAYER_TABLE = [
    {
        "patch": "walls",
        "faces": 2454,
        "layers_mean": 3.0,
        "thickness_mean": 0.000319,
        "coverage_percent": 99.5,
    }
]


def test_the_layer_table_comes_from_the_snappy_report_not_the_checkMesh_one(tmp_path: Path) -> None:
    """**The step reports are kept apart.** `_report_of` returns the checkMesh report only, and
    that contract is what the quality write relies on; the coverage table is read beside it, by
    the snappy step's name. A checkMesh report carrying a `layers` key (a hostile or future
    shape) must NOT be the table."""
    session, fake = _with_layers(
        tmp_path,
        {"layers": LAYER_TABLE},
        check={**GOOD_REPORT, "layers": [{"patch": "planted", "coverage_percent": 1.0}]},
    )
    fake.finish_with()
    status = _finish(session, fake)
    node = session.project.find(status["path"])
    assert node.meta["layers"] == LAYER_TABLE


def test_a_wall_under_80_percent_earns_a_warning_and_stays_a_mesh(tmp_path: Path) -> None:
    """Layers that collapsed are an answer about the geometry, not a failed mesh: the result
    stays `done` and `ok` per its quality, and the warning names the wall and the percentage."""
    session, fake = _with_layers(
        tmp_path,
        {
            "layers": [
                {
                    "patch": "walls",
                    "faces": 2454,
                    "layers_mean": 1.2,
                    "thickness_mean": 0.0001,
                    "coverage_percent": 64.0,
                }
            ]
        },
    )
    fake.finish_with()
    status = _finish(session, fake)
    node = session.project.find(status["path"])
    assert status["ok"] is True, "the mesh itself still meets the bar"
    assert node.meta["warnings"] == ["walls: layers on 64 % of the faces of walls"]


def test_a_good_coverage_warns_nothing(tmp_path: Path) -> None:
    session, fake = _with_layers(tmp_path, {"layers": LAYER_TABLE})
    fake.finish_with()
    status = _finish(session, fake)
    assert session.project.find(status["path"]).meta["warnings"] == []


def test_no_layer_report_writes_no_layers_and_no_warning(tmp_path: Path) -> None:
    """A job whose snappy step carried no parser report (or a blockMesh job): no `meta.layers`
    and no coverage warning - absent, not zero."""
    session, fake = _with_layers(tmp_path, None)
    fake.finish_with()
    status = _finish(session, fake)
    node = session.project.find(status["path"])
    assert "layers" not in node.meta
    assert node.meta["warnings"] == []


def test_a_layer_report_that_failed_writes_nothing(tmp_path: Path) -> None:
    """A parser exception became `{"error": …}` in the step report: no table to store, so
    nothing is stored - the coverage card shows absent, never a fabricated pass."""
    session, fake = _with_layers(tmp_path, {"error": "ValueError: the format changed"})
    fake.finish_with()
    status = _finish(session, fake)
    assert "layers" not in session.project.find(status["path"]).meta


# ---------------------------------------------------------------- quality distributions (M-506)


def _with_quality_dist(
    tmp_path: Path, dist: dict[str, Any] | None, delay: float = 0.0
) -> tuple[Session, Any]:
    """A session whose fake job returns the distributions after an optional delay — the shape
    that proves the result is `done` BEFORE the fetch returns (R14c)."""
    import os

    os.environ["STOKES_HOME"] = str(tmp_path / "home")
    os.environ["STOKES_AGENT_HOME"] = str(tmp_path / "agent")
    session = Session()
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
    fake = FakeRunner()
    real_call = fake.call

    def call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "extract.quality":
            import time

            time.sleep(delay)
            return dist if dist is not None else {"error": "the reader refused"}
        if method == "job.status":
            reply = real_call(method, timeout, **params)
            reply["reports"] = {"checkMesh": GOOD_REPORT}
            return reply
        return real_call(method, timeout, **params)

    fake.call = call  # type: ignore[method-assign]
    HostRegistry.of(session).runner = lambda name: fake  # type: ignore[method-assign]
    return session, fake


def test_a_done_result_caries_the_pending_marker_then_the_distributions(tmp_path: Path) -> None:
    """**R14c's ordering**: the result is `done` and fresh BEFORE `extract.quality` returns
    (the fake delays 0.3 s), and the second `node.changed` carries `meta.quality_dist`."""
    session, fake = _with_quality_dist(
        tmp_path,
        {"metrics": {"non_ortho": {"max": 5.95}}, "cell_types": {"hex": 12225}},
        delay=0.3,
    )
    info = session.execute("mesh.generate")
    node = session.project.find(info["path"])
    fake.finish_with()
    settle(session, info["path"])
    # The result is done NOW, while the fetch is still running.
    assert node.params["state"] == "done"
    # And the distributions land when the fetch returns.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and node.meta.get("quality_dist") == {"pending": True}:
        time.sleep(0.05)
    assert node.meta["quality_dist"] == {
        "metrics": {"non_ortho": {"max": 5.95}},
        "cell_types": {"hex": 12225},
    }


def test_a_failed_distribution_leaves_the_result_done_with_the_error(tmp_path: Path) -> None:
    session, fake = _with_quality_dist(tmp_path, None)  # the fake answers with {"error": ...}
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    node = session.project.find(info["path"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "error" not in (node.meta.get("quality_dist") or {}):
        time.sleep(0.05)
    assert node.params["state"] == "done", "the mesh itself is finished and fine"
    assert node.params["ok"] is True
    assert "error" in node.meta["quality_dist"]


def test_a_blockMesh_result_GETS_the_pending_marker_and_distributions(tmp_path: Path) -> None:
    """**Every mesher's case writes an ASCII polyMesh** the reader can read, so the marker and
    the fetch are not snappy-only: blockMesh's cell count is exact, but the DISTRIBUTIONS are
    still worth the page. The first draft excluded blockMesh here and the R14c tests caught
    it."""
    session, fake = _with_quality_dist(tmp_path, {"metrics": {"non_ortho": {"max": 0.0}}})
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    node = session.project.find(info["path"])
    assert node.params["mesher"] == "blockmesh"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "pending" in (node.meta.get("quality_dist") or {}):
        time.sleep(0.05)
    assert node.meta["quality_dist"] == {"metrics": {"non_ortho": {"max": 0.0}}}


def test_mesh_quality_returns_the_record(tmp_path: Path) -> None:
    """`mesh.quality` reads the one record: checkMesh's numbers and the distributions."""
    session, fake = _with_quality_dist(tmp_path, {"metrics": {"non_ortho": {"max": 5.95}}})
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    reply = session.execute("mesh.quality")
    assert reply["ok"] is True
    assert reply["checkmesh"]["mesh_ok"] is True
    assert reply["distributions"]["metrics"]["non_ortho"]["max"] == 5.95


# --------------------------------------------- R9: the reply is validated before it is stored


def test_a_too_large_or_nonfinite_reply_is_refused_not_stored(tmp_path: Path) -> None:
    """**R9**: the reply is the agent's answer about a mesh, not a trusted document — the meta
    is persisted into `project.json`, so a huge reply would push the project past its load
    limit and a NaN would reach `json.dumps` as `NaN`."""
    session, fake = _with_quality_dist(
        tmp_path,
        {"metrics": {"non_ortho": {"edges": [float("nan")] * 10, "counts": [1] * 9}}},
    )
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    node = session.project.find(info["path"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and node.meta.get("quality_dist") == {"pending": True}:
        time.sleep(0.05)
    stored = node.meta["quality_dist"]
    assert "error" in stored, "a NaN reply must be refused, not stored"
    assert "non-finite" in stored["error"] or "too many" in stored["error"]


def test_a_oversized_reply_is_refused(tmp_path: Path) -> None:
    from stokes.mesh.service import QUALITY_DIST_MAX_BINS

    session, fake = _with_quality_dist(
        tmp_path,
        {"metrics": {"non_ortho": {"edges": [0.0] * (QUALITY_DIST_MAX_BINS + 1)}}},
    )
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    node = session.project.find(info["path"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and node.meta.get("quality_dist") == {"pending": True}:
        time.sleep(0.05)
    assert "too many" in node.meta["quality_dist"]["error"]


def test_a_missing_method_is_NOT_available_yet_not_an_error(tmp_path: Path) -> None:
    """**R9's live half**: until the agent ships `extract.quality`, every finished mesh would
    otherwise carry an error in its meta — a missing method means "not available yet", and the
    pending marker simply stays.

    **The wording is the agent's, not a paraphrase.** `AgentServer._dispatch` says ``unknown
    method``; the check used to look for ``unknown command`` only, which no real agent sends. A
    real WSL mesh proved it: the method was absent, the phrase missed, and the result carried a
    warning and an error rather than the pending marker this test is about.
    """
    import os

    os.environ["STOKES_HOME"] = str(tmp_path / "home")
    os.environ["STOKES_AGENT_HOME"] = str(tmp_path / "agent")
    session = Session()
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
    fake = FakeRunner()
    real_call = fake.call

    def call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "extract.quality":
            raise RuntimeError("unknown method 'extract.quality'")
        if method == "job.status":
            reply = real_call(method, timeout, **params)
            reply["reports"] = {"checkMesh": GOOD_REPORT}
            return reply
        return real_call(method, timeout, **params)

    fake.call = call  # type: ignore[method-assign]
    HostRegistry.of(session).runner = lambda name: fake  # type: ignore[method-assign]
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])
    node = session.project.find(info["path"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "quality_dist" not in node.meta:
        time.sleep(0.05)
    assert node.params["state"] == "done"
    assert node.params["ok"] is True
    assert node.meta["quality_dist"] == {"pending": True}, (
        "a missing method is not an error: the marker stays, the page shows checkMesh's numbers"
    )


def test_a_stored_none_in_the_layer_settings_reads_as_the_default() -> None:
    """**The demo project's crash, measured Oct 21**: a project saved before the null-clear
    convention carries `expansion_ratio: null`, and `own.get(key, default)` returns that None
    (the key IS present) — `float(None)` crashed `mesh.generate`. A stored None reads as the
    default."""
    from stokes.mesh.snapshot import _layer_settings

    settings = _layer_settings(
        {
            "layer_mode": None,
            "expansion_ratio": None,
            "final_ratio": None,
            "first_layer_height": None,
            "min_thickness_ratio": None,
            "layer_feature_angle": None,
        }
    )
    assert settings == {
        "mode": "final_ratio",
        "expansion_ratio": 1.2,
        "final_ratio": 0.3,
        "first_layer_height": 0.0,
        "min_thickness_ratio": 0.1,
        "feature_angle": 100.0,
    }
    # The honest values still come through:
    assert _layer_settings({"expansion_ratio": 1.5})["expansion_ratio"] == 1.5


def test_a_new_mesh_step_is_never_throttled_away() -> None:
    """A step's end and the next step's start arrive within milliseconds; the throttle dropped the
    start, and the Mesh card said "Meshing: surfaceFeatures" through the whole snappyHexMesh. A
    repeat of the same step inside the interval is still dropped."""
    sent: list[tuple[str, dict[str, Any]]] = []

    class Recorder:
        def emit(self, name: str, **data: Any) -> None:
            sent.append((name, data))

    service = MeshService(Recorder())  # type: ignore[arg-type]
    service._emit_throttled("mesh.progress", step="surfaceFeatures", stage="done")
    service._emit_throttled("mesh.progress", step="snappyHexMesh", stage="running")
    service._emit_throttled("mesh.progress", step="snappyHexMesh", stage="running")
    assert [d["step"] for _, d in sent] == ["surfaceFeatures", "snappyHexMesh"]
