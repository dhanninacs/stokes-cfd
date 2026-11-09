"""The agent's security fixes (FIXES-A, Opus review 2026-10-14): A1, A2, A4 and the A5 items.

**These run on Windows too.** The supervisor's real work is POSIX (process groups, `/proc`), so
the tests stand in for exactly that and nothing else: `jobs._posix_host` says yes, the process
launch is a recorder, `_alive`/`_proc_start_ticks`/`_killpg` are fakes where a test is about
signalling. Everything the fixes changed — what is accepted from a client, where the record
lives, what `start` runs, whom `cancel` signals, who holds which lock — is the real code.

Each test is written to FAIL without its fix; the proof for each is in the change report.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from stokes_agent import engine_env, extract, jobs
from stokes_agent.engine_env import EngineEnv, UnknownEngine, engine_id, resolve_engine
from stokes_agent.jobpaths import JobPathError, check_result_path, check_staged_path
from stokes_agent.jobs import JobBadParams, JobStore, Step
from stokes_agent.server import AgentBadParams, AgentServer

UTILITIES = ("blockMesh", "checkMesh", "snappyHexMesh", "surfaceFeatures", "foamRun")


def test_result_path_accepts_windows_encoded_function_names() -> None:
    path = "postProcessing/volIntegrate(cloud%3Aalpha)/0/volFieldValue.dat"
    assert check_result_path(path) == path
    with pytest.raises(JobPathError):
        check_result_path("postProcessing/volIntegrate(cloud%oops)/0/result.dat")


def _can_symlink() -> bool:
    """Whether this account may create symlinks (Windows needs a privilege or developer mode)."""
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        try:
            (Path(folder) / "link").symlink_to(Path(folder) / "target")
        except (OSError, NotImplementedError):
            return False
    return True


needs_symlinks = pytest.mark.skipif(
    not _can_symlink(), reason="symlinks need a privilege on this host"
)


class FakeProc:
    """Stands in for `subprocess.Popen`: records the argv and exits 0 at once."""

    launched: list[list[str]] = []

    def __init__(self, argv: list[str], **_kwargs: Any) -> None:
        FakeProc.launched.append(list(argv))
        self.pid = 4242
        self.returncode = 0

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineEnv:
    """A POSIX-like host with one probed engine and a recorder for process launches.

    **The probe is the fake, `resolve_engine` is the real one**: the engine id the tests send is
    mapped through the same code a client's id goes through.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in UTILITIES:
        path = bin_dir / (f"{name}.exe" if os.name == "nt" else name)
        path.write_text("fake")
        path.chmod(0o755)
    bashrc = tmp_path / "openfoam14" / "env.sh"
    bashrc.parent.mkdir()
    bashrc.write_text("# fake")
    made = EngineEnv(
        bashrc=str(bashrc),
        env={"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""), "HOME": str(tmp_path)},
        project="OpenFOAM",
        version="14",
        flavour="org",
        seconds=0.0,
    )
    monkeypatch.setattr(engine_env, "bashrc_candidates", lambda: [bashrc])
    monkeypatch.setattr(engine_env, "cached_env", lambda _bashrc: made)
    monkeypatch.setattr(jobs, "_posix_host", lambda: True)
    monkeypatch.setattr(jobs, "NATIVE_WINDOWS", False)
    monkeypatch.setattr(jobs, "_getpgid", lambda pid: pid)
    FakeProc.launched = []
    monkeypatch.setattr(jobs.subprocess, "Popen", FakeProc)
    return made


def make_store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs")


def new_job(store: JobStore, files: dict[str, str] | None = None, **kwargs: Any) -> str:
    return store.create(files or {}, [Step("s", ("blockMesh",))], **kwargs)


def settle(store: JobStore, job: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = store.status(job)
        if state["state"] not in ("queued", "running", "cancelling"):
            return dict(state)
        time.sleep(0.02)
    raise AssertionError(f"job {job} did not finish: {store.status(job)['state']}")


def make_record(store: JobStore, job: str, **changes: Any) -> dict[str, Any]:
    """Edit a job's record the way the supervisor would have left it, and return it."""
    state = store.status(job)
    state.update(changes)
    jobs.write_state(store.job_dir(job), state)
    return state


def run_step_entry(pid: int, pgid: int, ticks: int | None) -> dict[str, Any]:
    return {
        "name": "s",
        "argv": ["blockMesh"],
        "state": "running",
        "rc": None,
        "pid": pid,
        "pgid": pgid,
        "start_ticks": ticks,
    }


class FakeGroups:
    """A stand-in for `os.killpg` over a set of live process groups.

    A group listed in ``survives_term`` ignores `SIGTERM` and dies only of `SIGKILL`; signal 0
    only tests. Every call is logged with its time.
    """

    def __init__(self, alive: set[int], survives_term: set[int] | None = None) -> None:
        self.alive = set(alive)
        self.survives_term = survives_term or set()
        self.log: list[tuple[float, int, int]] = []
        self.t0 = time.monotonic()

    def __call__(self, pgid: int, sig: int) -> None:
        self.log.append((time.monotonic() - self.t0, pgid, int(sig)))
        if pgid not in self.alive:
            raise ProcessLookupError(pgid)
        if sig == int(signal.SIGTERM) and pgid not in self.survives_term:
            self.alive.discard(pgid)
        if sig == jobs._SIGKILL:
            self.alive.discard(pgid)

    def signalled(self, sig: int) -> list[int]:
        return [pgid for _, pgid, got in self.log if got == sig]


# ============================================================ A1: the record is not a client's


@pytest.mark.parametrize(
    "path",
    (
        "state.json",
        "system/state.json",
        ".records/x",
        "artifacts/index.json",
        "constant/artifacts/index.json",
        "index.json",
        "log.s",
        # **`system/log.s` is NOT here any more** (FIXES-C item 6). It was refused by a
        # `name.startswith("log")` rule that could never protect anything: the agent's own logs are
        # `log.<step>` at the TOP of the job, and this function requires the first component to be
        # one of `system`, `constant`, `0` - so no staged path can name them at any depth. A case
        # file called `log.solve` inside `system/` is a user's own file, as is `logarithm`.
        "system/a.pid",
        "system/x.part",
        "system/.hidden",
        "other/file",
        "system",
        "../x",
        "system/../../x",
        "/etc/x",
        "C:/x",
        "system\\x",
    ),
)
def test_put_refuses_every_path_that_is_not_a_staged_file(
    tmp_path: Path, host: EngineEnv, path: str
) -> None:
    """**The job's own files are not a client's to write.** The first component must be a staged
    folder and the file must be inside it; the record, the artifact index and pid files are refused
    wherever they are. `state.json` was a legal path, and the old `start` rebuilt its argv from that
    file."""
    store = make_store(tmp_path)
    job = new_job(store)
    before = store.status(job)
    with pytest.raises(JobBadParams):
        store.put(job, path, b"{}", 0, True)
    assert store.status(job) == before, "a refused put must not change the record"


def test_create_refuses_the_same_paths(tmp_path: Path, host: EngineEnv) -> None:
    """`create`'s `files` is a client's path too, so it gets the same whitelist."""
    store = make_store(tmp_path)
    for path in ("state.json", "artifacts/index.json", "log.s", "other/file"):
        with pytest.raises(JobBadParams):
            store.create({path: "{}"}, [Step("s", ("blockMesh",))])
    assert list((tmp_path / "jobs").iterdir()) == [], "a refusal must leave nothing behind"


def test_a_case_file_whose_name_starts_with_log_is_writable(
    tmp_path: Path, host: EngineEnv
) -> None:
    """**The false refusal, as a test** (FIXES-C item 6, corrected).

    A rule `name.startswith("log")` refused `system/log.solve`, `system/logarithm` and
    `system/logistics` - ordinary names a case may hold, and the last two are not even related to
    logs. It protected nothing: the agent writes its own `log.<step>` at the **top of the job**, and
    a staged path must begin with `system`, `constant` or `0`, so the two can never be the
    same file.
    """
    store = make_store(tmp_path)
    job = new_job(store)
    for path in ("system/log.solve", "system/logarithm", "system/logistics"):
        assert check_staged_path(path) == path
    # And it can actually be written and read back, not merely named.
    store.put(job, "system/log.solve", b"// a user's own solver log name", 0, True)
    assert store.read(job, "system/log.solve")["text"] == "// a user's own solver log name"


def test_the_agents_own_log_is_still_unreachable(tmp_path: Path, host: EngineEnv) -> None:
    """**And the rule's real purpose still holds.** The agent's `log.<step>` sits at the job
    root, so it is refused as a staged path - not because of its name, but because it is not
    inside a staged folder at all."""
    for path in ("log.solve", "log.blockMesh", "artifacts/log.solve", "./log.solve"):
        with pytest.raises(JobPathError):
            check_staged_path(path)


def test_the_staged_folders_are_still_writable(tmp_path: Path, host: EngineEnv) -> None:
    """The whitelist must not break what the case writer produces."""
    store = make_store(tmp_path)
    files = {
        "system/controlDict": "a",
        "constant/polyMesh/points": "b",
        "constant/triSurface/part.stl": "c",
        "0/U": "d",
    }
    job = new_job(store, files)
    assert store.put(job, "system/fvSchemes", b"x", 0, True) == 1
    for rel in files:
        assert (store.job_dir(job) / rel).is_file()


def test_the_record_is_outside_the_job_folder(tmp_path: Path, host: EngineEnv) -> None:
    """The record lives in `<root>/.records/<job>.json`, where no client path can reach."""
    store = make_store(tmp_path)
    job = new_job(store, {"system/controlDict": "x"})
    record = store.record_path(job)
    assert record.is_file()
    assert record.parent == tmp_path / "jobs" / ".records"
    assert not (store.job_dir(job) / "state.json").exists()
    assert not list(store.job_dir(job).rglob("*.json")), "no json record inside the job folder"
    if os.name != "nt":
        assert record.stat().st_mode & 0o777 == 0o600


def test_a_planted_state_json_does_not_change_what_start_runs(
    tmp_path: Path, host: EngineEnv
) -> None:
    """**The remote-code-execution path.** Whatever a `state.json` in the job folder says — an
    argv naming `/bin/sh`, a pid — `start` runs only the step `create` validated, from the record
    the agent wrote, launched as the resolved utility on the engine PATH."""
    store = make_store(tmp_path)
    job = new_job(store, {"system/controlDict": "x"})
    planted = {
        "id": job,
        "state": "queued",
        "engine": None,
        "steps": [{"name": "s", "argv": ["/bin/sh", "-c", "evil"], "timeout_s": 5}],
    }
    (store.job_dir(job) / "state.json").write_text(json.dumps(planted))
    store.start(job)
    settle(store, job)
    assert len(FakeProc.launched) == 1
    launched = FakeProc.launched[0]
    assert Path(launched[0]).stem == "blockMesh", launched
    assert launched[1:] == [], "no argument from the planted file may reach the process"


@pytest.mark.parametrize(
    "argv",
    (
        ["rm", "-rf", "x"],
        ["/bin/sh", "-c", "evil"],
        ["blockMesh", "-case", "/etc"],
        ["foamRun", "-solver", "simpleFoam"],
        ["blockMesh;rm"],
    ),
)
def test_start_validates_the_argv_again_at_the_point_of_use(
    tmp_path: Path, host: EngineEnv, argv: list[str]
) -> None:
    """Defence in depth: even a record that has been edited on disk cannot make `start` launch
    what `create` would have refused."""
    store = make_store(tmp_path)
    job = new_job(store, {"system/controlDict": "x"})
    state = store.status(job)
    state["steps"][0]["argv"] = argv
    jobs.write_state(store.job_dir(job), state)
    with pytest.raises(JobBadParams):
        store.start(job)
    assert FakeProc.launched == []
    assert store.status(job)["state"] == "queued"


def test_a_step_is_checked_again_just_before_it_launches(tmp_path: Path, host: EngineEnv) -> None:
    """The check at the point of launch itself (`_run_step`), not only in `start`."""
    store = make_store(tmp_path)
    job = new_job(store)
    steps = [Step("s", ("rm", "-rf", "/"))]
    outcome, rc, error = store._run_step(job, store.job_dir(job), 0, steps[0], host, 1)
    assert outcome == "failed" and rc is None
    assert error is not None and error["code"] == "agent.bad_params"
    assert FakeProc.launched == []


def test_cancel_signals_only_what_the_record_names(
    tmp_path: Path, host: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `state.json` naming somebody else's group, planted in the job folder, is never read:
    `cancel` takes its pids from the record."""
    store = make_store(tmp_path)
    job = new_job(store)
    make_record(store, job, state="running")
    planted = {"state": "running", "steps": [run_step_entry(111, 9999, 5)]}
    (store.job_dir(job) / "state.json").write_text(json.dumps(planted))
    groups = FakeGroups({9999})
    monkeypatch.setattr(jobs, "_killpg", groups)
    monkeypatch.setattr(jobs, "_alive", lambda _pid: True)
    monkeypatch.setattr(jobs, "_proc_start_ticks", lambda _pid: 5)
    monkeypatch.setattr(jobs, "CANCEL_GRACE_S", 0.2)
    store.cancel(job)
    assert [entry for entry in groups.log if entry[1] == 9999] == []


def test_a_copy_from_never_brings_in_a_state_json(tmp_path: Path, host: EngineEnv) -> None:
    """`state.json` in a copy source is refused, whether named or inside a copied folder."""
    store = make_store(tmp_path)
    source = new_job(store, {"constant/polyMesh/points": "x"})
    store.start(source)
    assert settle(store, source)["state"] == "done"
    folder = store.job_dir(source)
    (folder / "state.json").write_text('{"steps": []}')
    (folder / "constant" / "state.json").write_text('{"steps": []}')
    (folder / "artifacts").mkdir()
    (folder / "artifacts" / "index.json").write_text("{}")
    (folder / "empty").mkdir()
    for paths in (
        ["state.json"],
        ["constant"],
        ["constant/state.json"],
        ["artifacts"],
        ["empty"],
    ):
        with pytest.raises(JobBadParams):
            new_job(store, copy_from={"job": source, "paths": paths})
    # The honest copy still works once the planted file is gone.
    (folder / "constant" / "state.json").unlink()
    copied = new_job(store, copy_from={"job": source, "paths": ["constant/polyMesh"]})
    assert (store.job_dir(copied) / "constant" / "polyMesh" / "points").read_text() == "x"
    assert not (store.job_dir(copied) / "state.json").exists()


# ================================================================== A2: an engine ID, never a path


def test_the_default_engine_resolves_to_the_probed_one(tmp_path: Path, host: EngineEnv) -> None:
    """With no id the agent uses the install its own probe found, and the id of that install
    names the same environment."""
    assert resolve_engine(None) is host
    assert resolve_engine(engine_id(host.bashrc)) is host
    store = make_store(tmp_path)
    job = new_job(store, install=engine_id(host.bashrc))
    assert store.status(job)["engine"] == engine_id(host.bashrc)
    default = new_job(store)
    assert store.status(default)["engine"] == engine_id(host.bashrc)


def test_a_path_is_refused_as_an_engine(tmp_path: Path, host: EngineEnv) -> None:
    """**Any path the client names used to be `source`d.** An absolute path (even the probed
    bashrc itself), a relative one and the path of a file staged in a job are all refused; so is
    an id the probe did not find."""
    staged = tmp_path / "jobs" / "j_000000000000" / "system"
    staged.mkdir(parents=True)
    evil = staged / "evil.sh"
    evil.write_text("touch /tmp/pwned")
    for bad in (
        host.bashrc,
        str(tmp_path / "evil.sh"),
        "env.sh",
        "../env.sh",
        str(evil),
        "e_000000000000",
        "e_0",
        "",
        "E_ABCDEF012345",
    ):
        with pytest.raises(UnknownEngine) as caught:
            resolve_engine(bad)
        assert caught.value.code == "agent.bad_params"
    with pytest.raises(UnknownEngine):
        resolve_engine(123)  # type: ignore[arg-type]


def test_create_refuses_a_path_as_install_and_leaves_nothing(
    tmp_path: Path, host: EngineEnv
) -> None:
    store = make_store(tmp_path)
    for bad in (host.bashrc, "env.sh", str(tmp_path / "evil.sh"), "e_000000000000"):
        with pytest.raises(JobBadParams) as caught:
            new_job(store, install=bad)
        assert caught.value.code == "agent.bad_params"
    assert list((tmp_path / "jobs").iterdir()) == []


def test_the_server_refuses_a_path_as_install(tmp_path: Path, host: EngineEnv) -> None:
    """Over the server's own handler, which is where a client's `install` arrives."""
    server = AgentServer(jobs=make_store(tmp_path))
    with pytest.raises(AgentBadParams):
        asyncio.run(
            server._job_create(
                files={}, steps=[{"name": "s", "argv": ["blockMesh"]}], install=host.bashrc
            )
        )


def test_the_host_record_carries_engine_ids(host: EngineEnv) -> None:
    """What a client sends comes from `host.probe`: each install has an `id`."""
    record = engine_env._install_record(Path(host.bashrc), refresh=False)
    assert record["id"] == engine_id(host.bashrc)
    assert record["supported"] is True


def test_the_host_record_names_a_default_engine_id() -> None:
    """`default_install_id` is what a client sends; it is in the record whatever the host."""
    assert "default_install_id" in engine_env.host_record()


# ================================================================== A4: cancel


def test_a_queued_job_is_cancelled_at_once_and_can_then_be_deleted(
    tmp_path: Path, host: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A queued job has nothing to wait for. It used to be left `cancelling` for ever, which
    `start` and `delete` both refuse."""
    monkeypatch.setattr(jobs, "CANCEL_GRACE_S", 0.2)
    store = make_store(tmp_path)
    job = new_job(store)
    started = time.monotonic()
    assert store.cancel(job) is True
    assert time.monotonic() - started < 0.15, "a queued cancel must not wait out the grace period"
    state = store.status(job)
    assert state["state"] == "cancelled"
    assert state["ended"]
    assert all(step["state"] == "skipped" for step in state["steps"])
    with pytest.raises(Exception, match="only a queued job can start"):
        store.start(job)
    assert store.delete(job) is True
    assert not store.job_dir(job).exists()
    assert not store.record_path(job).exists()
    assert (
        store.cancel(job.replace("j_", "j_")) is False if store.record_path(job).exists() else True
    )


def test_a_final_state_written_during_a_cancel_is_not_lost(
    tmp_path: Path, host: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Two writers, one record.** `cancel` loaded the record, and the job thread wrote its final
    state before `cancel` saved: the save then put `cancelling` over `done` and the job was stuck.
    Here the cancelling thread is held after its read until the finishing thread has had its
    chance to write; with the job's lock the finisher waits its turn and the result is a terminal
    state, never `cancelling`."""
    monkeypatch.setattr(jobs, "CANCEL_GRACE_S", 0.2)
    store = make_store(tmp_path)
    job = new_job(store)
    make_record(store, job, state="running")
    loaded = threading.Event()
    finished = threading.Event()
    real_load = store._load

    def slow_load(which: str) -> dict[str, Any]:
        data = real_load(which)
        if threading.current_thread().name == "canceller":
            loaded.set()
            finished.wait(0.5)
        return data

    monkeypatch.setattr(store, "_load", slow_load)
    barrier = threading.Barrier(2)

    def canceller() -> None:
        barrier.wait()
        store.cancel(job)

    def finisher() -> None:
        barrier.wait()
        loaded.wait(2)
        store._finish(job, store.job_dir(job), None)
        finished.set()

    threads = [
        threading.Thread(target=canceller, name="canceller"),
        threading.Thread(target=finisher, name="finisher"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert store.status(job)["state"] in ("done", "cancelled"), store.status(job)["state"]


@pytest.mark.parametrize(
    ("recorded", "actual"),
    ((5, 6), (None, 5), (5, None)),
    ids=("the pid was reused", "no start time was recorded", "the start time cannot be read"),
)
def test_cancel_does_not_signal_a_group_whose_start_time_does_not_match(
    tmp_path: Path,
    host: EngineEnv,
    monkeypatch: pytest.MonkeyPatch,
    recorded: int | None,
    actual: int | None,
) -> None:
    """The check `recover` makes, now made by `cancel` too: a recycled pid must not take a
    signal meant for the job."""
    monkeypatch.setattr(jobs, "CANCEL_GRACE_S", 0.2)
    store = make_store(tmp_path)
    job = new_job(store)
    state = store.status(job)
    state["state"] = "running"
    state["steps"][0] = run_step_entry(111, 9001, recorded)
    jobs.write_state(store.job_dir(job), state)
    groups = FakeGroups({9001})
    monkeypatch.setattr(jobs, "_killpg", groups)
    monkeypatch.setattr(jobs, "_alive", lambda _pid: True)
    monkeypatch.setattr(jobs, "_proc_start_ticks", lambda _pid: actual)
    store.cancel(job)
    assert groups.log == [], f"somebody else's group was signalled: {groups.log}"


def test_cancel_signals_a_group_whose_start_time_matches(
    tmp_path: Path, host: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control for the test above: with a matching start time the group is
    signalled — `SIGTERM`, then `SIGKILL` for one that is still there after the grace."""
    monkeypatch.setattr(jobs, "CANCEL_GRACE_S", 0.2)
    store = make_store(tmp_path)
    job = new_job(store)
    state = store.status(job)
    state["state"] = "running"
    state["steps"][0] = run_step_entry(111, 9001, 5)
    jobs.write_state(store.job_dir(job), state)
    groups = FakeGroups({9001}, survives_term={9001})
    monkeypatch.setattr(jobs, "_killpg", groups)
    monkeypatch.setattr(jobs, "_alive", lambda _pid: True)
    monkeypatch.setattr(jobs, "_proc_start_ticks", lambda _pid: 5)
    store.cancel(job)
    assert groups.signalled(int(signal.SIGTERM)) == [9001]
    assert groups.signalled(jobs._SIGKILL) == [9001]


def test_recover_interrupts_a_job_left_cancelling(tmp_path: Path, host: EngineEnv) -> None:
    """A job whose agent died mid-cancel was left `cancelling`, which `start` and `delete`
    refuse. Recovery now treats it like `running`."""
    store = make_store(tmp_path)
    job = new_job(store)
    make_record(store, job, state="cancelling")
    assert store.recover() == [job]
    assert store.status(job)["state"] == "interrupted"
    assert store.delete(job) is True


# ================================================================== A5


@needs_symlinks
def test_copy_from_refuses_a_link_inside_a_copied_folder(tmp_path: Path, host: EngineEnv) -> None:
    """`has_symlink` looked only at the named path's own components, and `copytree` followed a
    link found inside the folder — copying what it points at into the new job."""
    store = make_store(tmp_path)
    source = new_job(store, {"constant/polyMesh/points": "x"})
    store.start(source)
    assert settle(store, source)["state"] == "done"
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    (store.job_dir(source) / "constant" / "polyMesh" / "leak").symlink_to(secret)
    with pytest.raises(JobBadParams, match="symlink"):
        new_job(store, copy_from={"job": source, "paths": ["constant/polyMesh"]})
    # **Only the jobs OTHER than the source**, which is where the test itself planted the link. The
    # first version iterated every job folder including `source`, so it found its own fixture
    # and always
    # failed — a test that could not pass, which is as useless as one that cannot fail.
    others = [job for job in (tmp_path / "jobs").iterdir() if job.name != source]
    assert not any(path.name == "leak" for job in others for path in job.rglob("*")), (
        "the link target must not have been copied"
    )


def test_copy_from_walks_the_whole_tree_for_links(
    tmp_path: Path, host: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rule without needing the privilege to make a symlink: a file inside the copied
    folder that reports itself a link is refused, though no component of the named path is one."""
    store = make_store(tmp_path)
    source = new_job(store, {"constant/polyMesh/points": "x", "constant/polyMesh/leak": "y"})
    store.start(source)
    assert settle(store, source)["state"] == "done"
    real = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "leak" or real(self))
    with pytest.raises(JobBadParams, match="symlink"):
        new_job(store, copy_from={"job": source, "paths": ["constant/polyMesh"]})


def test_put_waits_for_start_and_is_then_refused(
    tmp_path: Path, host: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The race.** `put` and `start` shared no lock, so a final `put` could land after `start`
    had linted the case and before the job was marked running: the file was never linted. With
    one per-job lock `put` waits, then finds the job running and is refused."""
    store = make_store(tmp_path)
    job = new_job(store, {"system/controlDict": "x"})
    entered = threading.Event()
    real_lint = jobs.lint_job

    def slow_lint(job_dir: Path, **kwargs: Any) -> Any:
        found = real_lint(job_dir, **kwargs)
        entered.set()
        time.sleep(0.4)
        return found

    monkeypatch.setattr(jobs, "lint_job", slow_lint)
    starter = threading.Thread(target=store.start, args=(job,))
    starter.start()
    assert entered.wait(5)
    with pytest.raises(JobBadParams, match="queued"):
        store.put(job, "system/late", b"harmless\n", 0, True)
    starter.join(10)
    assert not (store.job_dir(job) / "system" / "late").exists()
    settle(store, job)


def test_shutdown_has_one_deadline_and_kills_every_survivor(
    tmp_path: Path, host: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**One deadline, not one per job.** `SIGTERM` goes to every group first, the wait happens
    once, and every group still alive is then `SIGKILL`led. Cancelling one job after another gave
    each its own grace period, so a second job that ignored `SIGTERM` was not even signalled until
    the first had used up the time the caller allowed."""
    grace = 0.5
    monkeypatch.setattr(jobs, "CANCEL_GRACE_S", grace)
    store = make_store(tmp_path)
    first, second = new_job(store), new_job(store)
    for job, pgid in ((first, 9001), (second, 9002)):
        state = store.status(job)
        state["state"] = "running"
        state["steps"][0] = run_step_entry(pgid, pgid, 5)
        jobs.write_state(store.job_dir(job), state)
    groups = FakeGroups({9001, 9002}, survives_term={9002})
    monkeypatch.setattr(jobs, "_killpg", groups)
    monkeypatch.setattr(jobs, "_alive", lambda _pid: True)
    monkeypatch.setattr(jobs, "_proc_start_ticks", lambda _pid: 5)
    started = time.monotonic()
    store.shutdown()
    elapsed = time.monotonic() - started

    terms = [item for item in groups.log if item[2] == int(signal.SIGTERM)]
    kills = [item for item in groups.log if item[2] == jobs._SIGKILL and item[1] in (9001, 9002)]
    assert {item[1] for item in terms} == {9001, 9002}
    assert max(item[0] for item in terms) - min(item[0] for item in terms) < grace / 2, (
        "every SIGTERM must go out before any wait: " + repr(terms)
    )
    assert [item[1] for item in kills if kills] == [9002], "only the survivor is SIGKILLed"
    assert elapsed < grace * 1.6 + 0.3, f"shutdown took {elapsed:.2f}s for one {grace}s deadline"
    assert store.status(first)["state"] == "interrupted"
    assert store.status(second)["state"] == "interrupted"


def test_a_cached_artifact_is_trusted_only_if_the_agents_record_says_so(
    tmp_path: Path, host: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A forged artifact is not a cache hit.** `artifacts/surfaces.stkmsh` and `index.json` can
    be planted on disk; only the agent's own record of having written the artifact makes it
    trusted. The planted pair here is perfectly well-formed and would pass every check on the
    files alone."""
    store = make_store(tmp_path)
    job = new_job(store, {"constant/polyMesh/points": "x"})
    store.start(job)
    assert settle(store, job)["state"] == "done"

    def artifact(patch: str) -> bytes:
        header = json.dumps({"patches": [patch]}).encode()
        return extract.MAGIC + len(header).to_bytes(4, "little") + header

    forged = artifact("forged")
    folder = store.job_dir(job) / "artifacts"
    folder.mkdir()
    (folder / "surfaces.stkmsh").write_bytes(forged)
    import hashlib

    (folder / "index.json").write_text(
        json.dumps(
            {
                "surfaces.stkmsh": {
                    "size": len(forged),
                    "sha256": hashlib.sha256(forged).hexdigest(),
                }
            }
        )
    )
    real = artifact("real")
    monkeypatch.setattr(extract, "extract_surfaces", lambda _dir: (real, {"patches": ["real"]}))
    server = AgentServer(jobs=store)

    first = asyncio.run(server._extract_surfaces(job))
    assert first["cached"] is False, "a planted artifact was served as a cache hit"
    assert first["patches"] == ["real"]
    assert (folder / "surfaces.stkmsh").read_bytes() == real

    second = asyncio.run(server._extract_surfaces(job))
    assert second["cached"] is True, "what the agent wrote itself is a cache hit"
    assert second["patches"] == ["real"]
    assert store.recorded_artifact(job, "surfaces.stkmsh") is not None
