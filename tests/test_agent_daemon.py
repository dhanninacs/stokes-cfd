"""The detached agent's state files and idle rule (T-159a).

**Two properties are the point**, and both are asserted rather than intended:

* **the secret never appears where it can be read by someone else** - not in `session.json`,
  not on stdout, and the file itself is mode 0600. It is the whole authentication of the
  channel, and `ps` shows every process's arguments to every user on the machine;
* **a stale session means "start fresh"**, not a crash and not a hang waiting for a process
  that died with the laptop lid.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from stokes_agent import daemon


@pytest.mark.skipif(os.name == "nt", reason="mode bits are not meaningful on Windows")
def test_the_secret_is_written_owner_only(tmp_path: Path) -> None:
    """**0600 by way of a 0600 temp file.** `write_text` + `chmod` creates the file readable and
    closes the window afterwards, and this file is the whole authentication."""
    path = daemon.write_secret(tmp_path, "abc123")
    assert path.read_text().strip() == "abc123"
    assert path.stat().st_mode & 0o077 == 0, oct(path.stat().st_mode)


def test_the_secret_is_not_in_the_session_record(tmp_path: Path) -> None:
    """`session.json` is what a client reads to find the agent; a secret in it would be a secret on
    disk in a second place, in a file that is not mode 0600."""
    secret = daemon.new_secret()
    daemon.write_secret(tmp_path, secret)
    daemon.write_session(tmp_path, daemon.Session(pid=1, port=2, started=3.0, protocol=2))
    text = (tmp_path / daemon.SESSION_FILE).read_text()
    assert secret not in text
    assert set(json.loads(text)) == {"pid", "port", "started", "protocol"}


def test_a_session_round_trips(tmp_path: Path) -> None:
    session = daemon.Session(pid=4242, port=51000, started=1234.5, protocol=2)
    daemon.write_session(tmp_path, session)
    assert daemon.read_session(tmp_path) == session


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        [],
        "not an object",
        {"pid": "4242", "port": 51000},  # a string pid
        {"pid": 4242, "port": 0},  # a port nothing can bind
        {"pid": 4242, "port": -1},
        {"pid": 4242},  # no port
    ],
)
def test_a_session_that_is_not_one_is_none(tmp_path: Path, data: object) -> None:
    """**Lenient, so a damaged file is the same answer as no file**: start fresh. Raising would turn
    a stale record into a failure a user cannot act on."""
    (tmp_path / daemon.SESSION_FILE).write_text(json.dumps(data))
    assert daemon.read_session(tmp_path) is None


def test_no_session_file_is_no_session(tmp_path: Path) -> None:
    assert daemon.read_session(tmp_path) is None
    assert daemon.read_secret(tmp_path) == ""


def test_the_secret_is_hex_and_long_enough() -> None:
    """32 bytes of entropy, hex - so 64 characters, and a value that survives being copied by hand
    (base64 does not, with `+` and `/`)."""
    secret = daemon.new_secret()
    assert len(secret) == daemon.SECRET_BYTES * 2 == 64
    assert all(character in "0123456789abcdef" for character in secret)
    # And it is not the same twice: a fixed secret would make every agent identical.
    assert daemon.new_secret() != secret


def test_this_process_is_alive_and_a_dead_one_is_not() -> None:
    """A live process survives the probe and a stale process is not reused."""
    assert daemon.process_alive(os.getpid()) is True
    assert daemon.process_alive(0) is False
    assert daemon.process_alive(-1) is False
    # A pid far above any real one.
    assert daemon.process_alive(2**31 - 1) is False


def test_process_probe_does_not_terminate_a_live_child() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert daemon.process_alive(child.pid)
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=10)
    assert not daemon.process_alive(child.pid)


def test_the_idle_rule_is_twelve_hours_of_no_use() -> None:
    """**Idle means no client AND no running job**; this answers the time half so both conditions
    stay visible at the call site."""
    assert daemon.IDLE_EXIT_S == 12 * 60 * 60
    assert daemon.should_exit_idle(now=0.0, last_active=0.0) is False
    assert daemon.should_exit_idle(now=daemon.IDLE_EXIT_S - 1, last_active=0.0) is False
    assert daemon.should_exit_idle(now=daemon.IDLE_EXIT_S, last_active=0.0) is True
    # Activity resets it, which is what "idle" has to mean.
    now = daemon.IDLE_EXIT_S * 2
    assert daemon.should_exit_idle(now=now, last_active=now - 60) is False


def test_the_state_directory_expands_a_literal_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**An `ssh ... -- cmd` argument list is not a shell**, so `--state-dir ~/.stokes/agent`
    arrives with the tilde intact. Not expanding it would create a directory called `~`."""
    assert daemon.state_dir("~/x").parts[0] != "~"
    assert daemon.state_dir("~/x") == Path("~/x").expanduser().resolve()
    # The environment variable, for an agent started without the flag.
    monkeypatch.setenv("STOKES_AGENT_STATE_DIR", str(tmp_path / "chosen"))
    assert daemon.state_dir(None) == tmp_path / "chosen"
    # **The default follows the agent's ROOT** (`$STOKES_AGENT_HOME`), not a hardcoded home.
    #
    # It was the literal `~/.stokes/agent`, which left the agent's SECRET and session file outside
    # a configured root: measured, pointing `STOKES_AGENT_HOME` at `~/stokes-work` still wrote the
    # state to `~/.stokes`. Both halves are asserted here - the root when one is set, and the
    # historical default when none is, so an existing install is unchanged.
    monkeypatch.delenv("STOKES_AGENT_STATE_DIR")
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "work"))
    assert daemon.state_dir(None) == tmp_path / "work" / "agent"
    monkeypatch.delenv("STOKES_AGENT_HOME")
    assert daemon.state_dir(None).parts[-2:] == (".stokes", "agent")


def test_the_serve_loop_EXITS_when_idle(tmp_path: Path) -> None:
    """**Opus M1.** `should_exit_idle` existed, was tested, and **had no caller** - so a detached
    agent started for one mesh ran for ever. The 12 h value is a constructor parameter for exactly
    this test, which would otherwise take twelve hours.

    Driven through `serve_forever`, because that is the loop the wiring was missing from. A test of
    `should_exit_idle` alone is what allowed it to go unnoticed.
    """
    import asyncio
    import time

    from stokes_agent.server import AgentServer

    async def run() -> float:
        server = AgentServer(port=0, token="t", mode="serve", idle_exit_s=1)
        await server.start()
        started = time.monotonic()
        try:
            await asyncio.wait_for(server.serve_forever(), timeout=15)
        finally:
            await server.stop()
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed < 10, f"an idle agent did not exit; it was still running after {elapsed:.1f}s"


def test_a_CONNECTED_client_keeps_the_agent_alive(tmp_path: Path) -> None:
    """**The other half of the spec's rule**, and the half that would make the fix dangerous:
    "idle" means no connected client AND no running job. A check on the clock alone would kill an
    agent that a client is using, so this asserts it stays up past its timeout while one is
    connected - and exits once that client goes.
    """
    import asyncio
    import contextlib

    from stokes_agent.server import AgentServer

    async def run() -> tuple[bool, bool]:
        server = AgentServer(port=0, token="t", mode="serve", idle_exit_s=1)
        host, port = await server.start()
        _reader, writer = await asyncio.open_connection(host, port)
        task = asyncio.create_task(server.serve_forever())
        # **Past the timeout**, so a clock-only check would have exited by now.
        await asyncio.sleep(2.5)
        alive_with_client = not task.done()
        writer.close()
        await asyncio.sleep(2.0)
        alive_after = not task.done()
        with contextlib.suppress(Exception):
            await server.stop()
        with contextlib.suppress(Exception):
            await task
        return alive_with_client, alive_after

    with_client, after = asyncio.run(run())
    assert with_client, "the agent exited while a client was connected"
    assert not after, "the agent stayed alive after its only client left"


def _run_detached(tmp_path: Path, idle: int) -> tuple[int, int, Path]:
    """Start the DETACHED agent through its own CLI. Returns `(pid, port, state folder)`.

    **Through the CLI, because that is where finding 3 was.** The serve loop's idle wiring existed
    and `_serve_detached` built its server without a value, so **the wiring never ran in
    production** while every unit test passed. Starting the real entry point is the only way to see
    that class of defect - the same lesson as H1's mode.
    """
    import subprocess
    import sys
    import time

    state = tmp_path / "agent"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "agent"),
        "STOKES_AGENT_STATE_DIR": str(state),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "stokes_agent",
            "--serve",
            "--state-dir",
            str(state),
            "--idle-exit",
            str(idle),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    del proc
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            record = json.loads((state / daemon.SESSION_FILE).read_text())
            return int(record["pid"]), int(record["port"]), state
        except (OSError, ValueError, KeyError):
            time.sleep(0.1)
    raise AssertionError("the detached agent never wrote its session record")


def _alive(pid: int) -> bool:
    """Whether the process is RUNNING - not merely present as a zombie.

    **`kill -0` is the wrong instrument, and it cost me two wrong conclusions.** It answers "may
    I signal this pid", which a **zombie** also satisfies: an exited process whose parent has not
    reaped it. The detached agent is not our child, so it sits as a zombie and `kill -0` keeps
    saying yes - measured directly. Two tests failed against correct code because of it.

    `/proc` gives the state, and `Z` is the one that must not count.
    """
    import subprocess

    if os.name == "nt":
        return daemon.process_alive(pid)
    if subprocess.run(["kill", "-0", str(pid)], check=False).returncode != 0:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        # **No `/proc`** (macOS): `kill -0` is all there is, and the zombie case does not
        # arise in the same way there.
        return True
    # The state is the field after the command, which may itself contain spaces inside parentheses.
    after = stat.rpartition(")")[2].strip()
    return not after.startswith("Z")


def test_the_REAL_CLI_exits_when_idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**Opus recheck round 3, finding 3.** The wiring existed and the production entry point never
    passed a value, so it never ran. `--idle-exit` is the lever, and it is configurable because the
    alternative is a test that waits twelve hours.
    """
    import time

    monkeypatch.setenv("STOKES_AGENT_STATE_DIR", str(tmp_path / "agent"))
    pid, _port, _state = _run_detached(tmp_path, idle=2)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    assert not _alive(pid), "the detached agent did not exit; the idle wiring is not in production"


def test_an_UNAUTHENTICATED_connection_does_not_keep_the_agent_alive(tmp_path: Path) -> None:
    """**Opus's rule**: if any TCP connection counted as a client, anyone who can reach the port
    could hold the agent open by connecting and doing nothing. `_readers` holds every accepted
    connection; `_writers` holds only the ones that completed the handshake.

    **THE ASSERTION IS THE LISTENER, NOT THE PROCESS, after three attempts.**

    * a 20 s deadline against a 10 s handshake timeout - the silent socket was dropped before the
      deadline, so the mutant passed;
    * an 8 s check against a 6 s limit - **failed on correct code**: ONE silent connection
      extends the agent by up to the handshake timeout (measured 12 s with a 6 s limit). Bounded,
      and not the threat;
    * a process-alive check after a loop that **broke on the first refused dial** - exactly what
      the correct code does, so with the MUTANT the loop ran to the end and the process died of
      natural causes before the assertion. Measured: the mutant passed.

    **What actually separates the two**: with bare connections counted, every dial SUCCEEDS and
    the agent is still listening after several idle limits; with only authenticated sessions
    counted, the server stops accepting and the dial is REFUSED. So that is what is asserted, and
    it is not a race - a refused dial is a definite answer, not a sampled state.
    """
    import socket
    import time

    pid, port, _state = _run_detached(tmp_path, idle=3)
    refused = False
    held: list[socket.socket] = []
    try:
        # **Six dials, four seconds apart** - twice the idle limit and well past the handshake
        # timeout, so a counted connection would keep the listener open throughout.
        for _ in range(6):
            try:
                held.append(socket.create_connection(("127.0.0.1", port), timeout=3))
            except OSError:
                refused = True
                break
            time.sleep(4)
        assert refused, (
            "the agent was still accepting connections after repeated silent dials across several "
            "idle limits - bare TCP connections are being counted as clients"
        )
    finally:
        for sock in held:
            sock.close()
        # **Left dead rather than lingering**, so the suite is not carrying a zombie per test.
        import contextlib

        with contextlib.suppress(OSError, ValueError):
            os.kill(pid, 9)


def test_an_agent_on_a_LONG_UPTIME_host_does_not_exit_at_once(tmp_path: Path) -> None:
    """**The latent bug in finding 3, and my first tests did not catch it.**

    `_last_active` started at `0.0` and is compared against `time.monotonic()`, which is **seconds
    since BOOT**. On a host up longer than its idle limit, a freshly started agent is therefore
    already "idle since boot" and exits on its **first poll** - about a minute after starting. This
    machine had been up 97 hours when I measured it, so a twelve-hour limit would have been no limit
    at all in reverse.

    **Why the other tests miss it**: they use a 2-second idle limit, which is smaller than any real
    uptime, so both the correct baseline and `0.0` expire at the same moment. The case that tells
    them apart is a limit **larger than the machine's uptime**.

    **The clock is injected, not read (claude-win2's 0930, round-4 item 7).** The first version
    read `time.monotonic()` and asserted `uptime > 60`, which made the test a tautology on any
    host that happened to be up: it proved nothing about the decision and skipped itself on a
    freshly booted machine. A clock we choose makes the case reproducible **and** lets us put the
    limit where the two baselines actually differ.
    """
    from stokes_agent import daemon

    # A host up 97 hours - the machine this was measured on - with a twelve-hour idle limit: the
    # limit is far below the uptime, so a `0.0` baseline expires instantly while a "just started"
    # baseline does not.
    uptime = 97 * 3600.0
    limit = daemon.IDLE_EXIT_S  # twelve hours
    assert limit < uptime, "this case needs an uptime longer than the idle limit"

    # **The bug**: `0.0` means "idle since boot", which is already past the deadline.
    assert daemon.should_exit_idle(now=uptime, last_active=0.0, idle_s=limit) is True, (
        "a 0.0 baseline must look expired - that is exactly what the bug did"
    )
    # **The fix**: the baseline is when THIS process started, so it is not idle yet.
    assert daemon.should_exit_idle(now=uptime, last_active=uptime, idle_s=limit) is False, (
        "a freshly started agent on a long-uptime host must not exit at once"
    )
    # And it does expire once the limit genuinely passes, so the fix does not disable the feature.
    assert daemon.should_exit_idle(now=uptime + limit, last_active=uptime, idle_s=limit) is True
    # The deadline helper agrees with the same arithmetic.
    assert daemon.idle_deadline(uptime, idle_s=limit) == uptime + limit

    # **The loop itself, with an injected clock.** The source-text check below is kept as a cheap
    # guard, but the real assertion is behavioural: drive `_exit_when_idle` with a clock we choose
    # and prove a freshly started agent on a 97-hour host does NOT exit, which is the bug.
    import asyncio

    from stokes_agent.server import AgentServer

    clock = {"now": 97 * 3600.0}  # a host up 97 hours, as measured
    server = AgentServer(idle_exit_s=daemon.IDLE_EXIT_S)
    server._now = lambda: clock["now"]  # the injected clock
    assert server._last_active is None, "a server that has not started has no baseline"

    async def poll_once() -> bool:
        """Run the idle loop until it either stops or passes one interval of virtual time."""
        task = asyncio.create_task(server._exit_when_idle())
        for _ in range(50):  # generous: the loop polls at idle_s/60, capped at 60 s
            await asyncio.sleep(0)
            if server._stop.is_set():
                break
            clock["now"] += daemon.IDLE_EXIT_S / 60.0
            await asyncio.sleep(0.01)
        stopped = server._stop.is_set()
        server._stop.set()  # let the task finish
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await task
        return stopped

    # With no client and no job, the agent must NOT exit at once: the baseline is "now", not boot.
    assert asyncio.run(poll_once()) is False, (
        "an agent on a long-uptime host exited at once - the 0.0 baseline bug is back"
    )

    # And the real server must not use 0.0: the bug was a literal in `__init__`.
    import inspect

    source = inspect.getsource(AgentServer.__init__)
    assert "self._last_active = 0.0" not in source, (
        "time.monotonic() is seconds since boot, so 0.0 is a baseline already passed"
    )


def test_the_state_folder_is_ABSOLUTE_and_frozen_at_start(tmp_path: Path) -> None:
    """**Opus residue 7.** A relative `--state-dir` was returned as given, so the agent's state
    folder depended on the working directory of whatever started it.

    **Why that matters more for this process than for most**: the agent DETACHES and then lives for
    hours, so anything that changed the process's working directory later - a test, an imported
    library, a future caller - would move its state out from under a client that had already been
    told where it is. Measured before the fix: `.stokes/agent` names a different folder before and
    after a `chdir`, and the returned `Path` kept the relative form.

    **Asserted on the SHAPE and on the resolution**, not on a particular folder: the value must be
    absolute, and `~` and `..` must already be settled.
    """
    from stokes_agent.daemon import state_dir

    # **A relative argument resolves against the cwd**, which is the honest reading of a relative
    # path - the defect was that it stayed relative, not which folder it named.
    relative = state_dir(".stokes/agent")
    assert relative.is_absolute(), "a relative state folder stayed relative"
    assert ".." not in relative.parts

    # **The forms a client actually sends**, each settled at the point it is read.
    assert state_dir("~/.stokes/agent").is_absolute()
    assert state_dir(None).is_absolute()
    dotted = state_dir(f"{tmp_path}/a/../b")
    assert dotted.is_absolute() and ".." not in dotted.parts, dotted
    assert dotted == (tmp_path / "b").resolve()

    # **The same argument in the same process gives the same folder**, which is what "frozen at
    # start" means: a `chdir` afterwards cannot move it.
    import os

    before = state_dir(".stokes/agent")
    here = str(Path.cwd())
    try:
        os.chdir(tmp_path)
        after = state_dir(".stokes/agent")
    finally:
        os.chdir(here)
    # **A relative argument is resolved against the cwd it was read in**, so these legitimately
    # differ - what must NOT differ is the value already returned, which is absolute and usable.
    assert before.is_absolute() and after.is_absolute()
    assert str(before).startswith(here.rstrip("/")), before
