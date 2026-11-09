"""The local runner: an agent in this process, behind a blocking facade (T-144c).

**Why the threading matters and is tested here.** The runner owns a thread with an asyncio loop
on it;
every other thread talks to it through a future. Two failures that design invites are a deadlock
(calling
into the loop FROM the loop) and a hang (waiting for a job that nobody is driving), so both have
a test
with a timeout rather than a comment saying "careful".

The fake utility is a shell script on a patched PATH, so no OpenFOAM is needed; the SIGTERM test
uses the
real install's environment only to resolve a program name.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from stokes.run.errors import RunnerUnavailable
from stokes.run.local import LocalRunner
from stokes_agent.engine_env import EngineEnv

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake utilities are POSIX shell scripts"
)

FAKE = "#!/bin/sh\n{body}\n"


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineEnv:
    """An `EngineEnv` whose PATH is a temp bin folder, wired into the agent."""
    from stokes_agent import engine_env, jobs

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
    monkeypatch.setattr(engine_env, "get_env", lambda install=None: made)
    monkeypatch.setattr(jobs, "resolve_engine", lambda engine=None: made)
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    return made


def fake_utility(env: EngineEnv, body: str) -> Path:
    path = Path(env.env["PATH"].split(":")[0]) / "blockMesh"
    path.write_text(FAKE.format(body=body))
    path.chmod(0o755)
    return path


def test_an_unsupported_platform_reports_itself_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux and Windows are supported; other platforms refuse before launching a thread."""
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    runner = LocalRunner()
    try:
        assert runner.available is False
        assert runner.why, "an unavailable runner must give a reason"
        assert "Linux" in runner.why
        with pytest.raises(RunnerUnavailable) as caught:
            runner.call("ping")
        assert caught.value.code == "run.unavailable"
    finally:
        runner.close()


def test_the_agent_answers_through_the_runner(fake_env: EngineEnv) -> None:
    """The lazy start, the loop, and a call that crosses the thread boundary."""
    runner = LocalRunner()
    try:
        assert runner.available is True
        assert runner.why == ""
        assert runner.call("ping") == {"pong": True}
        # The token never leaves the runner: not in the reply, not in the methods list.
        info = runner.call("agent.info")
        assert info["agent"].startswith("stokes-agent/")
        assert "token" not in str(info)
    finally:
        runner.close()


def test_a_job_runs_and_its_events_reach_a_listener(fake_env: EngineEnv) -> None:
    """The whole path: `job.create`, `job.start`, events on a listener, and the state read back."""
    fake_utility(fake_env, 'echo "from-the-fake"; exit 0')
    runner = LocalRunner()
    seen: list[tuple[str, dict[str, object]]] = []
    try:
        unsubscribe = runner.subscribe(lambda name, data: seen.append((name, data)))
        job = runner.call("job.create", files={}, steps=[{"name": "mesh", "argv": ["blockMesh"]}])[
            "job"
        ]
        runner.call("job.start", job=job)
        deadline = time.monotonic() + 10
        state: dict[str, object] = {}
        while time.monotonic() < deadline:
            state = runner.call("job.status", job=job)
            if state["state"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.05)
        assert state["state"] == "done", state
        # **The listener, on the loop thread, saw the transitions.**
        states = [data.get("state") for name, data in seen if name == "job.state"]
        assert "done" in states, f"the listener saw no done event: {states}"
        assert any(name == "log.lines" for name, _ in seen), "no log.lines event"
        # And unsubscribing stops them.
        unsubscribe()
        assert runner.call("log.tail", job=job, step="mesh")["lines"] == ["from-the-fake"]
    finally:
        runner.close()


def test_calling_from_a_listener_is_refused_rather_than_deadlocking(
    fake_env: EngineEnv,
) -> None:
    """**The deadlock this design invites.** A listener runs on the loop thread; `call` blocks
    that thread
    waiting for a coroutine that can only run on it. The runner refuses, which turns a hang into
    an error
    — and the test has a timeout so a regression fails instead of hanging the suite.
    """
    fake_utility(fake_env, "echo hi; exit 0")
    runner = LocalRunner()
    raised: list[BaseException] = []

    def listener(name: str, data: dict[str, object]) -> None:
        if name == "job.state" and data.get("state") == "done" and not raised:
            try:
                runner.call("ping")
            except BaseException as exc:
                raised.append(exc)

    try:
        runner.subscribe(listener)
        job = runner.call("job.create", files={}, steps=[{"name": "s", "argv": ["blockMesh"]}])[
            "job"
        ]
        runner.call("job.start", job=job)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not raised:
            time.sleep(0.05)
        assert raised, "a call from a listener neither raised nor was refused"
        assert isinstance(raised[0], RuntimeError)
    finally:
        runner.close()


def test_close_kills_a_running_job_and_marks_it_interrupted(fake_env: EngineEnv) -> None:
    """**A desktop that exits must not leave orphaned meshers** (ADR-0024). The grandchild
    check is the
    one that tells a real group kill from a parent-only one."""
    fake_utility(fake_env, 'sleep 300 & echo "CHILD $!"; sleep 300')
    runner = LocalRunner()
    child = None
    try:
        job = runner.call(
            "job.create",
            files={},
            steps=[{"name": "s", "argv": ["blockMesh"], "timeout_s": 600}],
        )["job"]
        runner.call("job.start", job=job)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and child is None:
            for line in runner.call("log.tail", job=job, step="s")["lines"]:
                if line.startswith("CHILD "):
                    child = int(line.split()[1])
            time.sleep(0.05)
        assert child is not None, "the fake never printed its child's pid"
        assert _alive(child), "the grandchild should be running before the close"

        # `close` sets `_closed`, so read the state from disk afterwards.
        from stokes.run.hosts import HostRegistry  # noqa: F401 - keeps the import honest

        job_dir = Path(os.environ["STOKES_AGENT_HOME"]) / "jobs" / job
        runner.close()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _alive(child):
            time.sleep(0.05)
        assert not _alive(child), "the grandchild survived close(): the group was not killed"
        import json

        # The record lives outside the job folder (FIXES-A A1).
        state = json.loads((job_dir.parent / ".records" / f"{job}.json").read_text())
        assert state["state"] == "interrupted", state["state"]
    finally:
        runner.close()


def test_close_is_idempotent(fake_env: EngineEnv) -> None:
    runner = LocalRunner()
    runner.call("ping")
    runner.close()
    runner.close()
    runner.close()


def test_a_call_after_close_is_refused(fake_env: EngineEnv) -> None:
    """A closed runner is a mistake, not a hang: it says so."""
    runner = LocalRunner()
    runner.call("ping")
    runner.close()
    with pytest.raises(RunnerUnavailable):
        runner.call("ping")


def test_sigterm_shuts_the_server_down_and_kills_the_job(tmp_path: Path) -> None:
    """**The review correction, as a test.** Python's default `SIGTERM` action ends the
    process at once,
    so `atexit` never runs — and a solver started with `start_new_session=True` is not in
    this process's
    group, so it survives as an orphan holding its case directory.

    A helper process installs the handlers, starts a long fake job, prints the job's pgid
    and sleeps in
    the same loop `main` uses. The test sends `SIGTERM` and asserts the process exits and **the
    pgid is
    gone** — which only happens if the shutdown path reached `shutdown_jobs`.
    """
    if platform.system() != "Linux":
        pytest.skip("the signal test is Linux-only")
    bashrc = os.environ.get("STOKES_OPENFOAM_BASHRC")
    if not bashrc or not Path(bashrc).is_file():
        pytest.skip("STOKES_OPENFOAM_BASHRC is not set to a real file")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "blockMesh").write_text(FAKE.format(body='sleep 300 & echo "CHILD $!"; sleep 300'))
    (bin_dir / "blockMesh").chmod(0o755)

    helper = tmp_path / "helper.py"
    helper.write_text(
        textwrap.dedent(
            f"""
            import os, sys, time
            sys.path.insert(0, {str(Path.cwd())!r})
            sys.path.insert(0, {str(Path.cwd() / "agent")!r})
            from pathlib import Path
            from stokes.run.local import LocalRunner
            from stokes.server.__main__ import install_signal_handlers
            from stokes_agent import engine_env, jobs

            real = engine_env.capture_env(Path({bashrc!r}))
            fake = engine_env.EngineEnv(
                bashrc=real.bashrc,
                env={{**real.env, "PATH": {str(bin_dir)!r} + ":/usr/bin:/bin"}},
                project=real.project, version=real.version, flavour=real.flavour, seconds=0.0)
            engine_env.get_env = lambda install=None: fake
            jobs.resolve_engine = lambda engine=None: fake

            runner = LocalRunner()
            job = runner.call("job.create", files={{}}, steps=[
                {{"name": "s", "argv": ["blockMesh"], "timeout_s": 600}}])["job"]
            runner.call("job.start", job=job)
            for _ in range(200):
                st = runner.call("job.status", job=job)
                pgid = st["steps"][0].get("pgid")
                if pgid:
                    break
                time.sleep(0.05)
            print(f"PGID {{pgid}}", flush=True)
            install_signal_handlers()
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                runner.close()
                print("SHUTDOWN DONE", flush=True)
            """
        )
    )
    env = {
        **os.environ,
        "PYTHONPATH": f"{Path.cwd()}:{Path.cwd() / 'agent'}",
        "STOKES_AGENT_HOME": str(tmp_path / "agent"),
    }
    proc = subprocess.Popen(
        [sys.executable, str(helper)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
    )
    try:
        pgid = _wait_for_pgid(proc, timeout=30)
        assert pgid is not None, "the helper never reported a pgid"
        proc.terminate()  # SIGTERM
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pytest.fail("the helper did not exit within 10 s of SIGTERM")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _group_alive(pgid):
            time.sleep(0.1)
        assert not _group_alive(pgid), (
            "the job's process group survived SIGTERM: the shutdown path did not reach the jobs"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        _kill_group(pgid if "pgid" in dir() else None)


def _alive(pid: int) -> bool:
    from stokes_agent.daemon import process_alive

    return process_alive(pid)


def _group_alive(pgid: int) -> bool:
    """Whether any process is left in a group. Negative pid signals the group."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_group(pgid: int | None) -> None:
    """Best-effort cleanup, so a failing test does not leave a `sleep 300` behind."""
    if not pgid:
        return
    import contextlib
    import signal

    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, signal.SIGKILL)


def _wait_for_pgid(proc: subprocess.Popen[str], timeout: float) -> int | None:
    """The pgid the helper prints, or None if it exits first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.stdout is None:
            return None
        line = proc.stdout.readline()
        if not line:
            return None
        if line.startswith("PGID "):
            return int(line.split()[1])
    return None


def test_main_installs_the_signal_handlers() -> None:
    """**That `main` actually calls them.** The SIGTERM test above proves the *function*
    works, but the
    helper script calls it directly — so removing the call from `main` left that test passing.
    Measured:
    the mutation passed. This is the assertion that covers the call site, which is where the review
    correction says the problem is.
    """
    import inspect

    from stokes.server import __main__ as server_main

    source = inspect.getsource(server_main.main)
    assert "install_signal_handlers()" in source, (
        "main() does not install the signal handlers, so SIGTERM will not run the shutdown path"
    )
    # And the handlers themselves raise KeyboardInterrupt, which is what the shutdown path catches.
    with pytest.raises(KeyboardInterrupt):
        server_main._raise_keyboard_interrupt(15, None)
