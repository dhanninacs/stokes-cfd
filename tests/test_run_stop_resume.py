"""Stop that saves, and Resume (`run.stop`, `run.resume`, the agent's `job.stop` and resume copy).

**Stop used to kill the solver**, so everything since the last scheduled write was lost and a run
stopped early was `cancelled`: no fields, nothing to continue. Now every case carries
`#includeFunc stopAtFile(action=writeNow)`; `job.stop` creates the file it watches, the solver
writes the iteration it is on and ends with exit 0, and the run is a result that `run.resume`
continues from its newest time folder (`startFrom latestTime`).

The agent job flows use a fake utility that is a shell script, as `test_agent_jobs.py` does, so
they run on POSIX only; the pieces that decide names and folders are tested everywhere.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

from stokes.compile.errors import CompileInvalid
from stokes.compile.foam import Dict, Entry, render_file
from stokes.compile.of14.control import STOP_FUNC, resume_control_text, stop_function
from stokes.run.records import RunRecord
from stokes_agent import jobs
from stokes_agent.engine_env import EngineEnv
from stokes_agent.jobs import STOP_FILE, JobBadParams, JobStore, Step

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="jobs use shell-script fakes")


# ---------------------------------------------------------------- the names agree


def test_the_case_and_the_agent_name_the_same_stop_file() -> None:
    """`stopAtFile` watches `$FOAM_CASE/<function name>`, and an `#includeFunc` with arguments is
    named by its call text, so the file the agent creates IS the call the case writes."""
    assert STOP_FUNC == STOP_FILE


def test_every_case_carries_the_stop_function() -> None:
    text = render_file("controlDict", Dict((Entry("functions", Dict((stop_function(),))),)))
    assert "#includeFunc" in text and STOP_FUNC in text


def test_the_stop_function_is_one_the_agent_lint_allows() -> None:
    from stokes_agent.lint import ALLOWED_INCLUDE_FUNC

    assert STOP_FUNC.split("(")[0] in ALLOWED_INCLUDE_FUNC


# ---------------------------------------------------------------- resume's controlDict


CONTROL = """FoamFile { object controlDict; }
solver          incompressibleFluid;
startFrom       startTime;
startTime       0;
endTime         500;
"""


def test_resume_starts_from_the_latest_time_and_changes_nothing_else() -> None:
    resumed = resume_control_text(CONTROL)
    assert "startFrom       latestTime;" in resumed
    assert "startTime       0;" in resumed and "endTime         500;" in resumed
    assert resumed.replace("latestTime", "startTime") == CONTROL


@pytest.mark.parametrize("text", ["endTime 500;\n", "startFrom a;\nstartFrom b;\n"])
def test_resume_refuses_a_controldict_it_does_not_know(text: str) -> None:
    with pytest.raises(CompileInvalid):
        resume_control_text(text)


# ---------------------------------------------------------------- which time folder


def test_the_time_folders_are_numeric_non_zero_folders_oldest_first(tmp_path: Path) -> None:
    for name in ("0", "0.orig", "50", "100", "1e-05", "constant", "postProcessing", "processor0"):
        (tmp_path / name).mkdir()
    (tmp_path / "200").write_text("a file, not a time")
    assert jobs._time_dirs(tmp_path) == ["1e-05", "50", "100"]
    assert jobs._latest_time_dir(tmp_path) == "100"


def test_a_case_that_wrote_nothing_has_no_latest_time(tmp_path: Path) -> None:
    (tmp_path / "0").mkdir()
    assert jobs._latest_time_dir(tmp_path) is None
    assert jobs._time_dirs(tmp_path / "missing") == []


# ---------------------------------------------------------------- the record


def test_a_record_carries_stopped_and_resumed_from_and_reads_old_records() -> None:
    old = RunRecord.from_dict({"id": "r_00000001", "state": "done_unconverged"})
    assert old.stopped is False and old.resumed_from == ""
    new = RunRecord(id="r_00000002", stopped=True, resumed_from="r_00000001")
    assert RunRecord.from_dict(new.to_dict()) == new


# ---------------------------------------------------------------- the store's stop_run


class StopRunner:
    """Answers `job.stop` and `job.cancel` as told, and records every call."""

    available = True
    why = ""

    def __init__(self, stopping: bool) -> None:
        self.stopping = stopping
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        self.calls.append((method, params))
        if method == "job.stop":
            return {"job": params["job"], "stopping": self.stopping}
        if method == "job.cancel":
            return {"job": params["job"], "cancelled": True}
        return {}

    def subscribe(self, listener: Any) -> Any:
        return lambda: None


def a_store(tmp_path: Path, runner: StopRunner) -> Any:
    from stokes.run.hosts import HostRegistry
    from stokes.run.store import RunStore
    from tests.selection_support import new_session

    session = new_session(tmp_path / "project")
    HostRegistry.of(session).runner = lambda name: runner  # type: ignore[method-assign]
    return RunStore.of(session)


def test_stop_asks_the_solver_to_save_and_marks_the_run_stopped(tmp_path: Path) -> None:
    runner = StopRunner(stopping=True)
    store = a_store(tmp_path, runner)
    store.add(RunRecord(id="r_00000001", state="running", job="j_aaaaaaaaaaaa", host="local"))
    assert store.stop_run("r_00000001") == "stopping"
    assert [method for method, _ in runner.calls] == ["job.stop"]
    found = store.record("r_00000001")
    # Still running: the job's own end finishes it, as a result rather than a cancel.
    assert found.stopped is True and found.state == "running"


def test_a_solver_that_cannot_save_is_cancelled_as_before(tmp_path: Path) -> None:
    runner = StopRunner(stopping=False)
    store = a_store(tmp_path, runner)
    store.add(RunRecord(id="r_00000001", state="running", job="j_aaaaaaaaaaaa", host="local"))
    assert store.stop_run("r_00000001") == "cancelled"
    assert [method for method, _ in runner.calls] == ["job.stop", "job.cancel"]
    assert (
        store.record("r_00000001").state == "cancelling"
        and store.record("r_00000001").stopped is False
    )


def test_a_queued_run_is_cancelled_and_a_finished_one_is_left_alone(tmp_path: Path) -> None:
    runner = StopRunner(stopping=True)
    store = a_store(tmp_path, runner)
    store.add(RunRecord(id="r_00000001", state="queued"))
    assert store.stop_run("r_00000001") == "cancelled"
    assert store.record("r_00000001").state == "cancelled"
    store.add(RunRecord(id="r_00000002", state="done_unconverged", job="j_bbbbbbbbbbbb"))
    assert store.stop_run("r_00000002") == ""
    assert runner.calls == []


def test_resume_continues_the_newest_result(tmp_path: Path) -> None:
    store = a_store(tmp_path, StopRunner(stopping=True))
    store.add(
        RunRecord(id="r_00000001", state="done_unconverged", job="j_aaaaaaaaaaaa", stopped=True)
    )
    store.add(RunRecord(id="r_00000002", state="failed", job="j_bbbbbbbbbbbb"))
    assert store.newest_result().id == "r_00000001"


# ---------------------------------------------------------------- the agent (POSIX fakes)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineEnv:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    made = EngineEnv(
        bashrc=str(tmp_path / "bashrc"),
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        project="OpenFOAM",
        version="14",
        flavour="org",
        seconds=0.0,
    )
    monkeypatch.setattr(jobs, "resolve_engine", lambda engine=None: made)
    return made


def fake_solver(env: EngineEnv) -> None:
    """A `blockMesh` that runs until the stop file appears, then writes time 120 and exits 0 -
    what `foamRun` does with `stopAtFile(action=writeNow)`."""
    path = Path(env.env["PATH"].split(":")[0]) / "blockMesh"
    path.write_text(
        "#!/bin/sh\n"
        f"while [ ! -f '{STOP_FILE}' ]; do sleep 0.05; done\n"
        "mkdir -p 120 && echo 'internalField uniform 1;' > 120/U\n"
        "exit 0\n"
    )
    path.chmod(0o755)


def settle(store: JobStore, job: str, state: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = store.status(job)
        if found["state"] == state:
            return dict(found)
        time.sleep(0.05)
    raise AssertionError(f"job {job} is {store.status(job)['state']}, wanted {state}")


@posix_only
def test_job_stop_lets_the_solver_save_and_finish_done(tmp_path: Path, env: EngineEnv) -> None:
    fake_solver(env)
    store = JobStore(tmp_path / "jobs")
    job = store.create({"system/controlDict": "x"}, [Step("solve", ("blockMesh",), timeout_s=60)])
    assert store.stop(job) is False, "a queued job has no solver to ask"
    store.start(job)
    settle(store, job, "running")
    assert store.stop(job) is True
    state = settle(store, job, "done")
    assert state.get("stop_requested")
    assert store.times(job) == ["120"]
    assert store.stop(job) is False, "a finished job has nothing to stop"


@posix_only
def test_resume_copies_the_newest_time_of_the_stopped_job(tmp_path: Path, env: EngineEnv) -> None:
    fake_solver(env)
    store = JobStore(tmp_path / "jobs")
    first = store.create({"system/controlDict": "x"}, [Step("solve", ("blockMesh",), timeout_s=60)])
    store.start(first)
    settle(store, first, "running")
    store.stop(first)
    settle(store, first, "done")
    second = store.create(
        {},
        [Step("solve", ("blockMesh",), timeout_s=60)],
        copy_from={"job": first, "paths": ["system/controlDict"], "latest_time_from": first},
    )
    assert (store.job_dir(second) / "120" / "U").read_text().startswith("internalField")
    assert store.status(second)["copy_from"]["paths"] == ["system/controlDict", "120"]


@posix_only
def test_resume_refuses_a_job_that_wrote_no_time(tmp_path: Path, env: EngineEnv) -> None:
    path = Path(env.env["PATH"].split(":")[0]) / "blockMesh"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    store = JobStore(tmp_path / "jobs")
    first = store.create({"system/controlDict": "x"}, [Step("solve", ("blockMesh",))])
    store.start(first)
    settle(store, first, "done")
    with pytest.raises(JobBadParams, match="no written time"):
        store.create(
            {},
            [Step("solve", ("blockMesh",))],
            copy_from={"job": first, "paths": ["system/controlDict"], "latest_time_from": first},
        )


def test_the_transient_controls_leave_an_existing_basis_alone() -> None:
    """Off, the new controls are not in the basis, so no finished run goes stale on upgrade."""
    from dataclasses import replace

    from stokes.compile.snapshot import PatchCondition, SolveSnapshot
    from stokes.run.snapshot import solve_basis

    snap = SolveSnapshot(
        settings_id="n",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=10,
        residual_target=1e-5,
        nu=1e-5,
        rho=1.0,
        patches=(PatchCondition("inlet", "patch", "velocity_inlet", velocity=(1.0, 0.0, 0.0)),),
    )
    base = solve_basis(snap, "m", "j", "of14.incompressibleFluid")
    import json
    from dataclasses import asdict

    fields = asdict(snap)
    for name in ("max_courant", "write_time", "average", "average_start"):
        assert name in fields
    assert solve_basis(replace(snap), "m", "j", "of14.incompressibleFluid") == base
    tuned = replace(snap, transient=True, max_courant=0.8)
    assert solve_basis(tuned, "m", "j", "of14.incompressibleFluid") != base
    del json
