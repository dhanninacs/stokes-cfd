"""The job protocol over a real server and client (T-203).

**A real socket, a real server, a real client** — the point of this file is that the supervisor
is reachable
from outside the process, so an in-process stand-in would prove nothing. The utility is a fake shell
script, so no OpenFOAM is needed and these run anywhere.

The two hard properties are here: **a job survives the client that started it**, and **a stalled
client
cannot hold the event loop**. Both are the kind of thing that works by accident in a test and
fails in
production.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from stokes.errors import StokesError
from stokes_agent import jobs
from stokes_agent.client import AgentClient
from stokes_agent.jobs import JobStore
from stokes_agent.server import AgentServer

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="jobs are POSIX only")

FAKE = "#!/bin/sh\n{body}\n"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def fake_utility(folder: Path, body: str, name: str = "blockMesh") -> Path:
    """A fake utility on a PATH the test controls."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(FAKE.format(body=body))
    path.chmod(0o755)
    return path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A store bound to a temp home, with a fake PATH."""
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    from stokes_agent import engine_env
    from stokes_agent.engine_env import EngineEnv

    made = EngineEnv(
        bashrc=str(tmp_path / "bashrc"),
        env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path)},
        project="OpenFOAM",
        version="14",
        flavour="org",
        seconds=0.0,
    )
    monkeypatch.setattr(jobs, "resolve_engine", lambda engine=None: made)
    monkeypatch.setattr(engine_env, "get_env", lambda install=None: made)


def _server(tmp_path: Path) -> AgentServer:
    """A server whose job store writes under the test's home."""
    return AgentServer(jobs=JobStore(tmp_path / "agent" / "jobs"))


async def _settle(client: AgentClient, job: str, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = await client.call("job.status", job=job)
        if state["state"] not in ("queued", "running", "cancelling"):
            return dict(state)
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job} did not finish in {timeout}s")


def test_a_job_runs_over_the_wire_and_reports(
    tmp_path: Path, env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`job.create` → `job.start` → events → `job.status` → `log.tail`.

    The full path a desktop uses, over a real socket: the job returns at once, its state arrives as
    events, and the log is readable while and after it runs.
    """
    fake_utility(tmp_path / "bin", 'echo "first"; echo "second"; exit 0')
    monkeypatch.setattr("stokes_agent.server.HEARTBEAT_INTERVAL_S", 0.2)

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            reply = await client.call(
                "job.create",
                files={"system/controlDict": "x"},
                steps=[{"name": "mesh", "argv": ["blockMesh"]}],
            )
            job = reply["job"]
            assert job.startswith("j_")
            started = await client.call("job.start", job=job)
            assert started == {"job": job, "state": "running"}

            state = await _settle(client, job)
            assert state["state"] == "done"
            assert state["steps"][0]["rc"] == 0
            # **The events arrived on the client.** Drain the queue and look for the transitions.
            seen: list[tuple[str, dict[str, Any]]] = []
            while not client.events.empty():
                event = client.events.get_nowait()
                seen.append((event["event"], event["data"]))
            names = [name for name, _ in seen]
            assert "job.state" in names, f"no job.state events: {names}"
            states = [data["state"] for name, data in seen if name == "job.state"]
            assert "running" in states and "done" in states
            lines = [line for name, data in seen if name == "log.lines" for line in data["lines"]]
            assert lines == ["first", "second"], lines
            # And the log is readable through the protocol.
            tail = await client.call("log.tail", job=job, step="mesh", lines=10)
            assert tail["lines"] == ["first", "second"]
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_a_job_survives_the_client_that_started_it(tmp_path: Path, env: None) -> None:
    """**The property the whole design is for.** The server cancels a connection's request
    tasks when the
    client disconnects, so a job that lived inside a request would die with its creator. This
    starts a
    job, closes the client, and reads the result from a NEW connection.
    """
    fake_utility(tmp_path / "bin", "sleep 1; echo finished")

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        first = await AgentClient.connect(host, port, server.token)
        job = (
            await first.call(
                "job.create", files={}, steps=[{"name": "mesh", "argv": ["blockMesh"]}]
            )
        )["job"]
        await first.call("job.start", job=job)
        # Close while it is still running: this cancels the connection's request tasks.
        await first.close()

        second = await AgentClient.connect(host, port, server.token)
        try:
            state = await _settle(second, job)
            assert state["state"] == "done", "the job died with its client"
            tail = await second.call("log.tail", job=job, step="mesh")
            assert "finished" in "\n".join(tail["lines"])
        finally:
            await second.close()
            await server.stop()

    run(scenario())


def test_cancel_over_the_wire_kills_the_group(tmp_path: Path, env: None) -> None:
    """The grandchild check again, through the protocol: killing only the parent leaves a
    solver behind."""
    fake_utility(tmp_path / "bin", 'sleep 300 & echo "CHILD $!"; sleep 300')

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            job = (
                await client.call(
                    "job.create",
                    files={},
                    steps=[{"name": "mesh", "argv": ["blockMesh"], "timeout_s": 60}],
                )
            )["job"]
            await client.call("job.start", job=job)
            child = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and child is None:
                tail = await client.call("log.tail", job=job, step="mesh")
                for line in tail["lines"]:
                    if line.startswith("CHILD "):
                        child = int(line.split()[1])
                await asyncio.sleep(0.05)
            assert child is not None, "the fake never printed its child's pid"
            assert await _alive(child), "the child should be running before the cancel"

            reply = await client.call("job.cancel", job=job)
            assert reply == {"job": job, "cancelled": True}
            state = await _settle(client, job, timeout=15)
            assert state["state"] == "cancelled"
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and await _alive(child):
                await asyncio.sleep(0.05)
            assert not await _alive(child), "the grandchild survived a cancel over the wire"
        finally:
            await client.close()
            await server.stop()

    run(scenario())


async def _alive(pid: int) -> bool:
    from stokes_agent.daemon import process_alive

    return process_alive(pid)


def test_put_then_start_reads_the_file(tmp_path: Path, env: None) -> None:
    """A chunked upload, then a run that reads what was uploaded — the path a large case takes."""
    fake_utility(tmp_path / "bin", "wc -c < constant/big.bin")

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            job = (
                await client.call(
                    "job.create", files={}, steps=[{"name": "s", "argv": ["blockMesh"]}]
                )
            )["job"]
            first = await client.call(
                "job.put", job=job, path="constant/big.bin", data=b"a" * 100, offset=0, final=False
            )
            assert first == {"size": 100}
            second = await client.call(
                "job.put", job=job, path="constant/big.bin", data=b"b" * 50, offset=100, final=True
            )
            assert second == {"size": 150}
            await client.call("job.start", job=job)
            state = await _settle(client, job)
            assert state["state"] == "done"
            tail = await client.call("log.tail", job=job, step="s")
            assert tail["lines"] == ["150"], "the utility did not read the uploaded file"
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_refusals_carry_their_codes(tmp_path: Path, env: None) -> None:
    """Every refusal a client can act on has its own code, so a UI can say something specific."""
    # A fake is needed even here: two of these cases get as far as a successful `job.create`, so the
    # utility must resolve before the refusal being tested is reached.
    fake_utility(tmp_path / "bin", "exit 0")

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            # A disallowed utility: the store's own rule, surfaced through the protocol.
            with pytest.raises(Exception) as caught:
                await client.call(
                    "job.create", files={}, steps=[{"name": "s", "argv": ["/bin/sh"]}]
                )
            assert getattr(caught.value, "code", "") == "agent.bad_params", caught.value

            # Unknown step keys.
            with pytest.raises(Exception) as caught:
                await client.call(
                    "job.create",
                    files={},
                    steps=[{"name": "s", "argv": ["blockMesh"], "nope": 1}],
                )
            assert "unknown keys" in str(caught.value)

            # Too many steps.
            with pytest.raises(Exception) as caught:
                await client.call(
                    "job.create",
                    files={},
                    steps=[{"name": f"s{i}", "argv": ["blockMesh"]} for i in range(33)],
                )
            assert "at most 32 steps" in str(caught.value)

            # A files payload over the create limit.
            with pytest.raises(Exception) as caught:
                await client.call(
                    "job.create",
                    files={"big": b"x" * (33 * 1024 * 1024)},
                    steps=[{"name": "s", "argv": ["blockMesh"]}],
                )
            assert getattr(caught.value, "code", "") == "agent.too_large", caught.value

            # A put chunk over its own limit.
            job = (
                await client.call(
                    "job.create", files={}, steps=[{"name": "s", "argv": ["blockMesh"]}]
                )
            )["job"]
            with pytest.raises(Exception) as caught:
                await client.call(
                    "job.put",
                    job=job,
                    path="x",
                    data=b"x" * (9 * 1024 * 1024),
                    offset=0,
                    final=False,
                )
            assert getattr(caught.value, "code", "") == "agent.too_large", caught.value

            # An unknown job is `agent.not_found`, not a malformed request.
            with pytest.raises(Exception) as caught:
                await client.call("job.status", job="j_ffffffffffff")
            assert getattr(caught.value, "code", "") == "agent.not_found", caught.value
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_delete_refuses_a_running_job_and_removes_a_done_one(tmp_path: Path, env: None) -> None:
    """A delete must not pull the folder out from under a running utility."""
    fake_utility(tmp_path / "bin", "sleep 1")

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            job = (
                await client.call(
                    "job.create",
                    files={},
                    steps=[{"name": "s", "argv": ["blockMesh"], "timeout_s": 60}],
                )
            )["job"]
            await client.call("job.start", job=job)
            with pytest.raises(Exception) as caught:
                await client.call("job.delete", job=job)
            assert getattr(caught.value, "code", "") == "agent.busy", caught.value

            await _settle(client, job)
            assert await client.call("job.delete", job=job) == {"deleted": True}
            listed = await client.call("job.list")
            assert all(item["id"] != job for item in listed["jobs"])
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_job_read_returns_a_result_file_and_refuses_a_climb(tmp_path: Path, env: None) -> None:
    """`job.read` is how a `postProcessing` result reaches a client."""
    fake_utility(tmp_path / "bin", "exit 0")

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            job = (
                await client.call(
                    "job.create", files={}, steps=[{"name": "s", "argv": ["blockMesh"]}]
                )
            )["job"]
            # A file with the punctuation a real function object's directory carries.
            name = "patchAverage(patch=inlet,fields=(pU))"
            folder = tmp_path / "agent" / "jobs" / job / "postProcessing" / name / "0"
            folder.mkdir(parents=True)
            (folder / "surfaceFieldValue.dat").write_text("# Time\tareaAverage(p)\n0\t1.5e+04\n")
            reply = await client.call(
                "job.read", job=job, path=f"postProcessing/{name}/0/surfaceFieldValue.dat"
            )
            assert "areaAverage(p)" in reply["text"]
            assert reply["truncated"] is False

            with pytest.raises(Exception) as caught:
                await client.call("job.read", job=job, path="../x")
            assert getattr(caught.value, "code", "") == "agent.bad_params", caught.value
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_a_parser_event_reaches_the_client(tmp_path: Path, env: None) -> None:
    """**Every event the supervisor emits is forwarded, unchanged.** That is how T-213's
    residual stream
    and T-208's quality report reach a desktop without the protocol knowing what they are.
    """

    class Fake:
        def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
            return [("monitor.residual", {"value": len(lines)}) for _ in lines]

        def report(self) -> dict[str, Any]:
            return {"seen": True}

    fake_utility(tmp_path / "bin", 'echo "one"; echo "two"')
    jobs.PARSERS["fake"] = Fake

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            job = (
                await client.call(
                    "job.create",
                    files={},
                    steps=[{"name": "s", "argv": ["blockMesh"], "parse": "fake"}],
                )
            )["job"]
            await client.call("job.start", job=job)
            state = await _settle(client, job)
            assert state["reports"]["s"] == {"seen": True}

            seen: list[tuple[str, dict[str, Any]]] = []
            while not client.events.empty():
                event = client.events.get_nowait()
                seen.append((event["event"], event["data"]))
            residuals = [data for name, data in seen if name == "monitor.residual"]
            assert residuals, f"the parser event did not reach the client: {[n for n, _ in seen]}"
            # **`job` and `step` are added by the supervisor**, so a parser need not know either.
            assert all(data["job"] == job and data["step"] == "s" for data in residuals)
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_a_heartbeat_arrives(tmp_path: Path, env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A client that hears nothing cannot tell a quiet agent from a dead one, and a mesh is
    silent for
    minutes at a time."""
    monkeypatch.setattr("stokes_agent.server.HEARTBEAT_INTERVAL_S", 0.2)

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                while not client.events.empty():
                    event = client.events.get_nowait()
                    if event["event"] == "heartbeat":
                        assert "t" in event["data"] and "running" in event["data"]
                        return
                await asyncio.sleep(0.05)
            pytest.fail("no heartbeat within 6 s")
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_a_stalled_client_never_holds_the_loop(
    tmp_path: Path, env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The robustness property, and the one that is easy to get wrong.**

    A client that connects, subscribes and never reads fills its socket buffer; a `drain()` with no
    timeout then waits for ever — and it waits **on the mesh's log path**, so one rude client
    stalls the
    log stream for everyone. The test opens such a client, has a fake job print a lot, and times
    a `ping`
    from a second client.
    """
    fake_utility(
        tmp_path / "bin",
        'i=0; while [ $i -lt 20000 ]; do echo "a line of output number $i"; i=$((i+1)); done',
    )
    monkeypatch.setattr("stokes_agent.server.BROADCAST_TIMEOUT_S", 0.5)

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        # The stalled client: connected, subscribed, never reads. Its reader task is NOT started, so
        # nothing drains the socket.
        stalled_reader, stalled_writer = await asyncio.open_connection(host, port)
        from stokes_agent import protocol

        stalled_writer.write(
            protocol.encode(
                {
                    "kind": "hello",
                    "protocol": protocol.PROTOCOL_VERSION,
                    "token": server.token,
                }
            )
        )
        await stalled_writer.drain()
        # **Read the handshake reply and then stop reading.** That is the exact shape of a stalled
        # client: it is connected and counted, and its socket buffer fills from here on.
        await asyncio.wait_for(protocol.read_message(stalled_reader), timeout=2)
        await asyncio.sleep(0.1)

        healthy = await AgentClient.connect(host, port, server.token)
        try:
            job = (
                await healthy.call(
                    "job.create", files={}, steps=[{"name": "s", "argv": ["blockMesh"]}]
                )
            )["job"]
            await healthy.call("job.start", job=job)
            # **Wait until the job is actually producing output**, or the ping is timed against a
            # loop that has nothing to be blocked by — which is how my first version of this
            # test passed
            # with the broadcast timeout removed.
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                state = await healthy.call("job.status", job=job)
                if state["steps"][0]["state"] == "running":
                    break
                if state["state"] not in ("queued", "running"):
                    pytest.fail(
                        f"the noisy job finished before it could stall anyone: {state['state']}"
                    )
                await asyncio.sleep(0.02)
            # Give it a moment to fill the stalled client's socket buffer.
            await asyncio.sleep(0.3)
            worst = 0.0
            for _ in range(20):
                started = time.monotonic()
                await asyncio.wait_for(healthy.call("ping"), timeout=3.0)
                worst = max(worst, time.monotonic() - started)
                await asyncio.sleep(0.02)
            assert worst < 0.5, f"a ping waited {worst:.2f}s: the stalled client held the loop"
        finally:
            await healthy.close()
            stalled_writer.close()
            await server.stop()

    run(scenario())


def test_agent_info_lists_every_method(tmp_path: Path, env: None) -> None:
    """**Feature detection.** Protocol changes are additive only, so a client asks what this
    agent can do
    instead of guessing and getting an error."""

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            info = await client.call("agent.info")
            assert info["agent"].startswith("stokes-agent/")
            assert info["jobs_root"]
            methods = set(info["methods"])
            for name in (
                "job.create",
                "job.put",
                "job.start",
                "job.cancel",
                "job.status",
                "job.list",
                "job.delete",
                "job.read",
                "log.tail",
                "host.probe",
            ):
                assert name in methods, f"{name} is registered but not reported"
            assert methods == set(server._methods)
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_a_slow_create_does_not_block_a_ping(
    tmp_path: Path, env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**File work goes off the loop**, so a health check still answers while a case is written.

    **Why this test makes the work slow on purpose.** The obvious version stages a large payload
    and times
    a ping — and it cannot fail: writing 31 MiB to tmpfs takes about 0.16 s, so with the work
    moved onto
    the loop the delay is a few tens of milliseconds and every threshold passes. Measured, with the
    mutation in place: the callback fired 35 ms late.

    So the store call is wrapped in a deliberate sleep. That is a seam the test owns, and it
    makes the
    property measurable: with `to_thread` the loop is free and the callback fires on time; with
    the call
    inline the loop is blocked for the whole sleep and the callback fires late by about that much.
    """
    fake_utility(tmp_path / "bin", "exit 0")
    slow_for = 1.0
    real_create = JobStore.create

    def slow_create(self: JobStore, *args: Any, **kwargs: Any) -> str:
        time.sleep(slow_for)
        return real_create(self, *args, **kwargs)

    monkeypatch.setattr(JobStore, "create", slow_create)

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            started_at = time.monotonic()
            creating = asyncio.create_task(
                client.call(
                    "job.create",
                    # **A legal staged path.** The original used `{"small": b"x"}`, which the
                    # stricter
                    # `jobpaths.check_staged_path` (FIXES-C item 6) correctly refuses — a
                    # staged file
                    # must be inside `system`, `constant` or `0`. The fixture was wrong, not
                    # the rule.
                    files={"system/small": b"x"},
                    steps=[{"name": "s", "argv": ["blockMesh"]}],
                )
            )
            loop = asyncio.get_running_loop()
            due = loop.create_future()

            def fire() -> None:
                if not due.done():
                    due.set_result(time.monotonic())

            # Fired on the loop: a free loop fires it at +0.05, a blocked one only when the
            # block ends.
            loop.call_later(0.05, fire)
            fired = await asyncio.wait_for(due, timeout=slow_for + 5)
            # And a request still answers while the write is in flight.
            ping_started = time.monotonic()
            await asyncio.wait_for(client.call("ping"), timeout=slow_for + 5)
            ping_took = time.monotonic() - ping_started

            late = fired - started_at - 0.05
            assert late < 0.4, (
                f"the loop was blocked for about {late:.2f}s by job.create: the work is not off it"
            )
            assert ping_took < 0.4, f"the ping took {ping_took:.2f}s behind job.create"
            assert (await creating)["job"]
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_extract_refuses_a_job_that_is_not_done(tmp_path: Path, env: None) -> None:
    """**`agent.not_ready`, not a crash and not an empty artifact.** A job whose mesh is still
    being built
    has no surfaces, so a client that asked too early is told which state the job is in rather
    than handed
    an artifact of nothing."""
    fake_utility(tmp_path / "bin", "echo meshing")

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            reply = await client.call(
                "job.create",
                files={"system/blockMeshDict": "x"},
                steps=[{"name": "blockMesh", "argv": ["blockMesh"], "timeout_s": 60}],
                label="t",
            )
            # Created but never started: not `done`, so no surfaces exist yet.
            with pytest.raises(StokesError) as caught:
                await client.call("extract.surfaces", job=reply["job"])
            assert caught.value.code == "agent.not_ready"
        finally:
            await client.close()
            await server.stop()

    run(scenario())


def test_artifact_get_refuses_a_climb_a_missing_file_and_a_huge_chunk(
    tmp_path: Path, env: None
) -> None:
    """**The name and the length come from a client**, so the refusals are checked over the
    wire and not
    only inside the module."""
    fake_utility(tmp_path / "bin", "echo meshing")

    async def scenario() -> None:
        server = _server(tmp_path)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        try:
            reply = await client.call(
                "job.create",
                files={"system/blockMeshDict": "x"},
                steps=[{"name": "blockMesh", "argv": ["blockMesh"], "timeout_s": 60}],
                label="t",
            )
            job = reply["job"]
            with pytest.raises(StokesError) as climb:
                await client.call("artifact.get", job=job, name="../../etc/passwd")
            assert climb.value.code == "agent.bad_params"

            with pytest.raises(StokesError) as missing:
                await client.call("artifact.get", job=job, name="nope.stkmsh")
            assert missing.value.code == "agent.not_found"

            with pytest.raises(StokesError) as big:
                await client.call("artifact.get", job=job, name="a.stkmsh", length=9 * 1024 * 1024)
            assert big.value.code == "agent.bad_params"
        finally:
            await client.close()
            await server.stop()

    run(scenario())
