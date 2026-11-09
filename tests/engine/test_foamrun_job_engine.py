"""The `foamRun` parser driven by a REAL solver through the REAL protocol (T-213, **checkpoint A**).

    STOKES_OPENFOAM_BASHRC=<the env script> STOKES_ENGINE_TESTS=1 PYTHONPATH=.:agent \\
        python3 -m pytest -q tests/engine/test_foamrun_job_engine.py -s

**What only this test can check.** `tests/test_foamrun_parser.py` proves the parser reads a
committed log
correctly. This proves the whole path: the agent starts `foamRun` on the tutorial case, the
parser is fed
the live output through the job supervisor, its events cross a socket while the solver runs, and the
history file on disk is what the report says it is.

**Why the parser matters here rather than in the tests above:** the duct will run at 10 to 40
iterations per
second, and the supervisor keeps only the last event per second per name. So this also checks
the property
that makes that safe — the events that arrive carry **every** block, and the history has them all.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from stokes_agent.client import AgentClient
from stokes_agent.jobs import JobStore
from stokes_agent.server import AgentServer

sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.engine

#: The tutorial case, and its own `endTime` — **[VERIFY-OF14]** read from
#: `tests/fixtures/of14/pitzDaily/system/controlDict`, not assumed: `endTime 0.3;`.
CASE = Path(__file__).parent.parent / "fixtures" / "of14" / "pitzDaily"
END_TIME = 0.3


def case_files() -> dict[str, str]:
    """The tutorial's own files, as the job's `files`.

    **`system/functions` is dropped from `controlDict`.** The tutorial defines a `surfaceFieldValue`
    functionObject, and the agent's dictionary lint refuses `functions` (T-202b correction 6)
    — so the
    case is the tutorial's minus that entry, and the test says so rather than weakening the lint.
    """
    files: dict[str, str] = {}
    for path in sorted(CASE.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(CASE).as_posix()
        text = path.read_text()
        if rel == "system/controlDict":
            text = "\n".join(line for line in text.splitlines() if not line.startswith("functions"))
            assert "functions" not in text, "the functions entry must be gone for the lint to pass"
        files[rel] = text
    return files


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_a_steady_run_streams_residuals_over_the_protocol(tmp_path: Path, monkeypatch) -> None:
    """**Checkpoint A.** A solver runs as an agent job and its residuals stream over the
    protocol, with no
    tree and no desktop code.

    The assertions the brief names, in its order:

    * `monitor.residual` events arrive **while the job runs** — at least 5 before `done`;
    * `job.progress.fraction` grows;
    * `job.status.reports.foamRun.iterations > 0`;
    * `job.read(job, "monitors/residuals.jsonl")` returns **as many lines as `iterations`** — the
      property that makes the rate limiter safe, because a coalesced event cannot lose a block.
    """
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))

    async def scenario() -> dict:
        server = AgentServer(jobs=JobStore(tmp_path / "agent" / "jobs"))
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        # **`install` is an ENGINE ID, not a path** (FIXES-C item 7): the agent maps an id onto an
        # install its own probe found, so a client can no longer name a bashrc to source. The
        # id comes
        # from `host.probe`, which is what a real client does.
        probed = await client.call("host.probe")
        install = probed.get("default_install_id")
        assert install, f"the probe found no usable OpenFOAM: {probed.get('why')}"
        seen: list[tuple[str, dict]] = []

        def drain() -> None:
            """**Drain the queue, do not subscribe to it.** `client.events` is an `asyncio.Queue`
            filled by the client's reader; the pattern is the same as
            `test_agent_job_protocol.py`."""
            while not client.events.empty():
                event = client.events.get_nowait()
                seen.append((event["event"], event["data"]))

        started = time.monotonic()
        try:
            reply = await client.call(
                "job.create",
                files=case_files(),
                steps=[
                    {"name": "blockMesh", "argv": ["blockMesh"], "timeout_s": 300},
                    {
                        "name": "solve",
                        "argv": ["foamRun", "-solver", "incompressibleFluid"],
                        "timeout_s": 600,
                        "parse": "foamRun",
                        "parse_params": {"end_time": END_TIME},
                    },
                ],
                install=install,
                label="t213",
            )
            job = reply["job"]
            await client.call("job.start", job=job)

            # **While it runs**: poll the state until it is final, collecting events meanwhile.
            deadline = time.monotonic() + 600
            residuals_before_done = 0
            fractions: list[float] = []
            while time.monotonic() < deadline:
                residuals_before_done = sum(1 for n, _ in seen if n == "monitor.residual")
                fractions = [
                    float(d.get("fraction") or 0.0) for n, d in seen if n == "job.progress"
                ]
                state = await client.call("job.status", job=job)
                drain()
                if state["state"] not in ("queued", "running", "cancelling"):
                    break
                await asyncio.sleep(0.05)
            seconds = time.monotonic() - started
            drain()
            state = await client.call("job.status", job=job)
            history = await client.call(
                "job.read", job=job, path="monitors/residuals.jsonl", max_bytes=1024 * 1024
            )
            return {
                "state": state,
                "seconds": seconds,
                "residuals_before_done": residuals_before_done,
                "fractions": fractions,
                "events": seen,
                "history": history,
            }

        finally:
            await client.close()
            await server.stop()

    result = run(scenario())
    state = result["state"]  # type: ignore[index]
    print(
        f"\nT-213 checkpoint A: {result['seconds']:.1f} s, "  # type: ignore[index]
        f"{state['state']}, {result['residuals_before_done']} residual events before done"  # type: ignore[index]
    )

    assert state["state"] == "done", state
    # At least 5 residual events arrived BEFORE the job finished — it streamed, rather than
    # reporting once at the end.
    assert result["residuals_before_done"] >= 5, (  # type: ignore[index]
        f"only {result['residuals_before_done']} residual events before done"  # type: ignore[index]
    )
    # Progress grew.
    fractions = result["fractions"]  # type: ignore[index]
    assert fractions, "no job.progress events"
    assert max(fractions) > min(fractions), f"fraction never grew: {sorted(set(fractions))[:5]}"

    report = state["reports"]["solve"]
    assert report["iterations"] > 0, report
    assert report["last_time"] == pytest.approx(END_TIME, rel=0.01)
    assert report["ended"] is True
    assert report["fatal"] is False
    # The final residuals are the tutorial's own last block, and they are tiny — the run settled.
    assert report["last_residuals"], "no residuals recorded"
    assert all(v < 1.0 for v in report["last_residuals"].values()), report["last_residuals"]

    # **Every block is on disk**, so a coalesced event cannot lose one.
    # `job.read` returns the file's `text`, not pre-split lines.
    lines = [line for line in result["history"]["text"].splitlines() if line.strip()]  # type: ignore[index]
    assert len(lines) == report["iterations"], (
        f"{len(lines)} lines on disk for {report['iterations']} iterations"
    )
