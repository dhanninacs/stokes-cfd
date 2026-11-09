"""A mesh on a SECOND host must reach that host for everything that touches the agent.

**Why this file exists.** `claude-win2` asked for it after the six-site fix, and the reason is
uncomfortable: **every other mesh test wires the fake with `runner = lambda name: fake`, which
ignores the name entirely.** So a service that asked the LOCAL agent about a REMOTE job would pass
every one of them - which is exactly the defect the six-site fix repaired.

The four paths that reach an agent are: `job.status` when a result finishes, `extract.surfaces`,
`cancel`, and `log.tail`. Each is exercised here with two runners that record their own calls, so
a call that goes to the wrong one is visible rather than silent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.run.hosts import HostRegistry
from tests.mesh_surface_support import SurfaceServer
from tests.test_mesh_service import settle

#: The name the fake registry answers for, besides `local`.
REMOTE = "buildbox"


class RecordingRunner:
    """A runner for one named host that **records every call it is asked to make**.

    Deliberately not a subclass of the other fakes: what this file tests is which runner is
    asked, so the two must be distinguishable objects with separate call lists.
    """

    def __init__(self, name: str, *, finish: str = "done") -> None:
        self.name = name
        self.available = True
        self.why = ""
        self.finish = finish
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.listeners: list[Any] = []
        self.job = f"j_{name[:4]}" + "a" * 8
        self.surfaces = SurfaceServer()
        self.report: dict[str, Any] = {}

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
                "state": self.finish,
                "error": None,
                "started": 100.0,
                "ended": 102.5,
                "reports": {"solve": self.report},
            }
        if method == "job.read":
            return {"text": "", "truncated": False}
        if method == "log.tail":
            return {"lines": [f"a line from {self.name}"], "truncated": False}
        if method in ("extract.surfaces", "artifact.get"):
            return self.surfaces.answer(method, params)
        return {}

    def subscribe(self, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener) if listener in self.listeners else None

    def step_with(self, step: str = "blockMesh") -> None:
        """Play a running-step event, as the agent does while a job works.

        **`MeshService.log` needs this and `job.status` cannot supply it**: the step is remembered
        from the job's own `job.state` events, so a fake that only answers `job.status` has no step
        to tail and `log` returns empty before it calls anything. Measured - my first version did
        exactly that, and the runner saw only `job.create`.
        """
        for listener in list(self.listeners):
            listener("job.state", {"state": "running", "step": step, "job": self.job})

    def finish_with(self, state: str | None = None) -> None:
        """Play the final event the agent sends when a job is done."""
        chosen = state or self.finish
        data: dict[str, Any] = {"state": chosen, "step": None, "job": self.job}
        for listener in list(self.listeners):
            listener("job.state", data)

    def close(self) -> None:
        return None

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]


class TwoHostRegistry:
    """A registry that answers by NAME, so a call to the wrong host lands on a different runner."""

    def __init__(self, local: RecordingRunner, remote: RecordingRunner) -> None:
        self.runners = {"local": local, REMOTE: remote}

    def runner(self, name: str) -> Any:
        if name not in self.runners:
            from stokes.run.errors import UnknownHost

            raise UnknownHost(f"no host {name!r}", details={"code": "run.unknown_host"})
        return self.runners[name]

    def probe(self, name: str, refresh: bool = False) -> dict[str, Any]:
        """**The shape the mesh service reads**: `record.default_install_id` and an engine.

        My first version returned `{"installs": []}` with no `default_install_id`, so the service
        answered `skipped` - "a machine without OpenFOAM is a fact about the machine" - and every
        assertion failed for a reason that had nothing to do with which host was asked. That is the
        same trap the real probe's shape would set, so the fake has to carry it.
        """
        return {
            "host": name,
            "available": True,
            "why": "",
            "record": {
                "installs": [
                    {
                        "id": "e_000000000001",
                        "bashrc": "/x/env.sh",
                        "version": "14",
                        "flavour": "org",
                    }
                ],
                "default_install_id": "e_000000000001",
                "default_install": "/x/env.sh",
            },
        }


@pytest.fixture
def two_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Session, RecordingRunner, RecordingRunner]:
    """A session whose mesh is configured for the REMOTE host, with both runners recording."""
    import os

    os.environ["STOKES_HOME"] = str(tmp_path / "home")
    os.environ["STOKES_AGENT_HOME"] = str(tmp_path / "agent")
    session = Session()
    session.execute("project.new", name="two-hosts")
    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={
            "domain": "box",
            "box": ["0 [m]", "0 [m]", "0 [m]", "1 [m]", "1 [m]", "1 [m]"],
            "base_size": "0.1 [m]",
            "host": REMOTE,
        },
    )
    local, remote = RecordingRunner("local"), RecordingRunner(REMOTE)
    registry = TwoHostRegistry(local, remote)
    monkeypatch.setattr(HostRegistry, "of", classmethod(lambda cls, s: registry))
    return session, local, remote


def test_a_mesh_on_a_second_host_reaches_that_host_for_every_agent_call(
    two_hosts: tuple[Session, RecordingRunner, RecordingRunner],
) -> None:
    """**The whole point of the six-site fix.** All four agent-touching paths must go to the
    host the job runs on, and the LOCAL runner must not be asked anything at all.

    With the old code every one of these would have gone to `local` - and because the other tests'
    fakes ignore the name, nothing would have noticed.
    """
    session, local, remote = two_hosts
    info = session.execute("mesh.generate")
    remote.finish_with()
    settle(session, info["path"])

    # 1. `job.create` and the subscription happen where the job runs.
    assert "job.create" in remote.methods(), "the job was not created on the requested host"
    assert local.methods() == [], f"the local host was asked something: {local.methods()}"

    # 2. `job.status`, read when the result finishes - the path that had the bug.
    assert "job.status" in remote.methods()
    # 3. `extract.surfaces`, for the patches.
    assert "extract.surfaces" in remote.methods()
    assert local.methods() == [], f"the local host was asked something: {local.methods()}"


def test_cancel_reaches_the_host_the_mesh_runs_on(
    two_hosts: tuple[Session, RecordingRunner, RecordingRunner],
) -> None:
    """**`cancel` was one of the hardcoded sites.** A cancel that goes to the local agent
    signals a job id it has never heard of, and silently does nothing - the worst kind of
    failure, because the user believes the mesh stopped."""
    session, local, remote = two_hosts
    info = session.execute("mesh.generate")
    settle_cancel(session, info["path"])
    assert session.execute("mesh.cancel") == {"cancelled": True}
    assert "job.cancel" in remote.methods(), "the cancel did not reach the host running the job"
    assert "job.cancel" not in local.methods(), "the cancel went to the LOCAL agent"


def test_the_log_comes_from_the_host_the_mesh_runs_on(
    two_hosts: tuple[Session, RecordingRunner, RecordingRunner],
) -> None:
    """**`log.tail` was the other hardcoded site.** It would have read the local agent's log for a
    remote job - showing a user output that belongs to a different machine's job, or nothing."""
    session, local, remote = two_hosts
    session.execute("mesh.generate")
    remote.step_with()
    reply = session.execute("mesh.log")
    assert "log.tail" in remote.methods(), "the log was read from the wrong host"
    assert "log.tail" not in local.methods()
    assert any("buildbox" in str(line) for line in reply.get("lines") or []), reply


def settle_cancel(session: Session, path: str) -> None:
    """Wait until the result is running, so a cancel has something to cancel."""
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        node = session.project.find(path)
        if node is not None and node.params.get("state") == "running":
            return
        time.sleep(0.02)
