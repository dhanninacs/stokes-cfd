"""The job supervisor (T-144a/T-202a).

**A fake utility, not OpenFOAM.** Every test here writes a small shell script called `blockMesh`
into a temp directory and points a fake `EngineEnv` at it, so these run on any machine and in
CI. The one test that drives the real install is `tests/engine/test_agent_jobs_engine.py`,
marked `engine`.

The interesting cases are the hostile ones — an argv that names `/bin/sh`, a path that climbs
out of the job, a cancel that must reach a **grandchild** — because this is the code that turns
a client's request into a running program.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from stokes_agent import jobpaths, jobs
from stokes_agent.engine_env import EngineEnv
from stokes_agent.jobs import (
    JobBadParams,
    JobMissingUtility,
    JobNotFound,
    JobStore,
    Step,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="jobs are POSIX only")

# The fake utility's script, per test. `$1` is what it should do.
FAKE = """#!/bin/sh
{body}
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineEnv:
    """An `EngineEnv` whose PATH is a temp bin folder plus the real one.

    The temp folder comes first, so a test can shadow `blockMesh` — which is exactly what a real
    install does for its own utilities, and it means nothing here reaches the network's
    OpenFOAM.
    """
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


def fake_utility(env: EngineEnv, body: str, name: str = "blockMesh") -> Path:
    """Install a fake utility on the fake PATH and return its path."""
    path = Path(env.env["PATH"].split(":")[0]) / name
    path.write_text(FAKE.format(body=body))
    path.chmod(0o755)
    return path


def make_store(tmp_path: Path, events: list[tuple[str, dict[str, Any]]] | None = None) -> JobStore:
    """A store whose events land in ``events``.

    **The callback takes two arguments** — `(name, data)`, the brief's signature — so
    `events.append` cannot be used directly; my first version did and every event raised
    `TypeError`, which `_emit`
    swallowed by design. The test then failed on an empty list rather than on the real cause,
    which is why the store now wraps it in a two-argument lambda.
    """
    if events is None:
        return JobStore(tmp_path / "jobs")
    return JobStore(tmp_path / "jobs", on_event=lambda name, data: events.append((name, data)))


def settle(store: JobStore, job: str, timeout: float = 20.0) -> dict[str, Any]:
    """Wait for a job to reach a terminal state and return its state file."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = store.status(job)
        if state["state"] not in ("queued", "running", "cancelling"):
            return dict(state)
        time.sleep(0.05)
    raise AssertionError(f"job {job} did not finish in {timeout}s: {store.status(job)['state']}")


# ---------------------------------------------------------------- the happy path


def test_create_start_done(tmp_path: Path, env: EngineEnv) -> None:
    """A job runs, its log holds what the program printed, and the state file records it."""
    fake_utility(env, 'echo "first"; echo "second"; exit 0')
    events: list[tuple[str, dict[str, Any]]] = []
    store = make_store(tmp_path, events)
    job = store.create({"system/controlDict": "x"}, [Step("mesh", ("blockMesh",))])
    assert store.status(job)["state"] == "queued", "create leaves the job queued"
    store.start(job)
    state = settle(store, job)
    assert state["state"] == "done"
    assert state["steps"][0]["rc"] == 0, "the exit code must be recorded, not left null"
    assert state["steps"][0]["state"] == "done"
    assert state["steps"][0]["started"] and state["steps"][0]["ended"]
    assert state["ended"]
    assert store.tail(job, "mesh")["lines"] == ["first", "second"]
    states = [data["state"] for name, data in events if name == "job.state"]
    assert states[0] == "queued" and "running" in states and states[-1] == "done"
    # The staged file is on disk, in the job folder.
    assert (store.job_dir(job) / "system" / "controlDict").read_text() == "x"


def test_every_step_announces_its_start(tmp_path: Path, env: EngineEnv) -> None:
    """A later step's start is sent, not only its end: a client names the step that is running.

    Only the first step's start went out, so the Mesh card named decomposePar for the whole of a
    parallel snappyHexMesh run (the elbow, 2026-11-08).
    """
    fake_utility(env, "exit 0")
    events: list[tuple[str, dict[str, Any]]] = []
    store = make_store(tmp_path, events)
    job = store.create({}, [Step("first", ("blockMesh",)), Step("second", ("blockMesh",))])
    store.start(job)
    assert settle(store, job)["state"] == "done"
    seen = [
        (data["state"], data["step"], data["index"])
        for name, data in events
        if name == "job.state" and data.get("step")
    ]
    assert seen == [
        ("running", "first", 0),
        ("done", "first", 0),
        ("running", "second", 1),
        ("done", "second", 1),
    ]


def test_a_failed_step_skips_the_rest(tmp_path: Path, env: EngineEnv) -> None:
    """The first failure ends the job; the remaining steps are `skipped`, not left `queued`."""
    fake_utility(env, "exit 3")
    store = make_store(tmp_path)
    job = store.create({}, [Step("mesh", ("blockMesh",)), Step("check", ("blockMesh",))])
    store.start(job)
    state = settle(store, job)
    assert state["state"] == "failed"
    assert state["steps"][0]["rc"] == 3
    assert state["steps"][0]["state"] == "failed"
    assert state["steps"][1]["state"] == "skipped"
    assert state["error"]["code"] == "agent.step_failed"


def test_the_steps_run_in_order(tmp_path: Path, env: EngineEnv) -> None:
    """A stage that reads what the previous step wrote — how `copy_from` and checkMesh are used.

    The fake both **writes a file and prints**, because a step's evidence has two places to be:
    the file the next step reads, and the log the user watches. My first version redirected the
    only `echo` into the file and then asserted on the log, which was correctly empty.
    """
    fake_utility(env, 'echo "mesh made" > constant_polyMesh; echo "mesh made"; exit 0')
    store = make_store(tmp_path)
    job = store.create({}, [Step("one", ("blockMesh",)), Step("two", ("blockMesh",))])
    store.start(job)
    assert settle(store, job)["state"] == "done"
    assert store.tail(job, "one")["lines"] == ["mesh made"]
    assert store.tail(job, "two")["lines"] == ["mesh made"]


# ---------------------------------------------------------------- refusals


@pytest.mark.parametrize("argv0", ("/bin/sh", "rm", "blockMesh;rm", "foamToVTK", "cat"))
def test_argv0_must_be_an_allow_listed_bare_name(
    tmp_path: Path, env: EngineEnv, argv0: str
) -> None:
    """**The security boundary.** A path, a shell metacharacter or a program Stokes does not
    drive is refused before anything is written."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    with pytest.raises(JobBadParams) as caught:
        store.create({}, [Step("s", (argv0,))])
    assert caught.value.code == "agent.bad_params"
    assert list((tmp_path / "jobs").iterdir()) == [], "a refusal must leave nothing behind"


def test_a_missing_utility_is_its_own_code(tmp_path: Path, env: EngineEnv) -> None:
    """`agent.missing_utility` is what the host wizard branches on: the install is there and the
    program is not, which is a different answer from "no engine"."""
    store = make_store(tmp_path)
    with pytest.raises(JobMissingUtility) as caught:
        store.create({}, [Step("s", ("checkMesh",))])
    assert caught.value.code == "agent.missing_utility"


@pytest.mark.parametrize(
    "rel",
    ("../x", "/etc/x", "a/../../x", "a\\b", "..", "a/", "", "a//b"),
)
def test_a_file_path_must_stay_inside_the_job(tmp_path: Path, env: EngineEnv, rel: str) -> None:
    """Written *and* resolved, because a path can look contained and not be."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    with pytest.raises(JobBadParams):
        store.create({rel: "x"}, [Step("s", ("blockMesh",))])
    assert list((tmp_path / "jobs").iterdir()) == []


@pytest.mark.parametrize("job", ("../../etc", "j_zzz", "", "j_1234", "not-a-job"))
def test_a_bad_job_id_is_refused(tmp_path: Path, env: EngineEnv, job: str) -> None:
    store = make_store(tmp_path)
    with pytest.raises(JobBadParams):
        store.status(job)


def test_a_symlink_out_of_the_job_is_refused(tmp_path: Path, env: EngineEnv) -> None:
    """**The check no string test can replace.** A symlink inside the job folder passes every
    pattern and is caught only by resolving the path."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    # **The source must be DONE, or the done-check answers first** and the test proves nothing about
    # symlinks. My first version copied from a queued job and passed for that reason — the
    # assertion was on the message, which is the only thing that told the two refusals apart.
    store.start(job)
    settle(store, job)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("s")
    (store.job_dir(job) / "link").symlink_to(outside)
    with pytest.raises(JobBadParams) as caught:
        store.create({}, [Step("s", ("blockMesh",))], copy_from={"job": job, "paths": ["link"]})
    # **Either refusal is correct, and the containment check wins here** — which is defence in depth
    # working rather than a test that missed its target. The containment rule fires on the
    # RESOLVED path before the symlink walk is reached; the symlink walk is the second lock on
    # the same door. The assertion is therefore on the outcome (refused, and not copied) rather
    # than on which message.
    assert "resolves outside" in str(caught.value) or "symlink" in str(caught.value)
    # And a symlink pointing INSIDE the job is allowed: the rule is about leaving the job, not about
    # links. Here the target does not exist, so the refusal is the missing-file one — which
    # proves the containment check let it through to the existence check rather than refusing
    # the link itself.
    (store.job_dir(job) / "inside").symlink_to(store.job_dir(job) / "a")
    with pytest.raises(JobBadParams, match="has no inside"):
        store.create({}, [Step("s", ("blockMesh",))], copy_from={"job": job, "paths": ["inside"]})


def test_a_symlinked_directory_cannot_be_walked_through(tmp_path: Path, env: EngineEnv) -> None:
    """**The case that makes containment load-bearing, proved by mutation.**

    ``linkdir/secret`` passes `REL_PATH_RE`, contains no ``..``, and has no separators a string
    check could object to — and it reads a file outside the job, because `linkdir` is a symlink
    to a directory elsewhere. Only resolving the path catches it.

    I found this by disabling the containment check and watching the suite still pass: every
    symlink test I had used a link as the **final** component, which the string rules and the
    existence check already
    covered. A check that no test can fail is a check that will one day be "simplified" away.
    """
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    store.start(job)
    settle(store, job)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("classified")
    # A symlinked DIRECTORY inside the job, and a path through it.
    (store.job_dir(job) / "linkdir").symlink_to(outside)
    assert jobpaths.REL_PATH_RE.match("linkdir/secret"), "the string rules accept this path"
    with pytest.raises(JobBadParams, match="resolves outside"):
        store.create(
            {}, [Step("s", ("blockMesh",))], copy_from={"job": job, "paths": ["linkdir/secret"]}
        )
    # And the read path is guarded by the same rule.
    with pytest.raises(JobBadParams, match="resolves outside"):
        store._inside(store.job_dir(job), "linkdir/secret")


def test_step_names_must_be_unique_and_well_formed(tmp_path: Path, env: EngineEnv) -> None:
    """A step name becomes part of a log's file name, so it cannot carry a separator."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    with pytest.raises(JobBadParams, match="two steps"):
        store.create({}, [Step("s", ("blockMesh",)), Step("s", ("blockMesh",))])
    for bad in ("1mesh", "a/b", "", "x" * 40, "a b"):
        with pytest.raises(JobBadParams):
            store.create({}, [Step(bad, ("blockMesh",))])


def test_a_bad_timeout_and_an_unknown_parser_are_refused(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    with pytest.raises(JobBadParams, match="timeout_s"):
        store.create({}, [Step("s", ("blockMesh",), timeout_s=0)])
    with pytest.raises(JobBadParams, match="timeout_s"):
        store.create({}, [Step("s", ("blockMesh",), timeout_s=86401)])
    with pytest.raises(JobBadParams, match="log parser"):
        store.create({}, [Step("s", ("blockMesh",), parse="nope")])


# ---------------------------------------------------------------- cancel


def test_cancel_kills_the_process_group_not_just_the_parent(tmp_path: Path, env: EngineEnv) -> None:
    """**The assertion that tells the two implementations apart.**

    The fake starts a `sleep 300` child and prints its pid. Killing only the parent would leave that
    grandchild running — which is what an OpenFOAM utility does when it spawns `mpirun` — and
    the case directory would stay locked. So the grandchild's pid is checked, not the job's
    state.

    **KNOWN FLAKY under full-suite load** (recorded 2026-10-14, round 52). It and
    `test_a_timeout_fails_the_step_and_kills_the_group` fail on a loaded machine and pass 3/3 in
    isolation. That is the race FIXES-C item 5 describes — a cancel landing before
    `_record_pid`, and
    `_live_groups` skipping a group whose leader has died — so the fix belongs there rather
    than here.
    Do not "fix" this by loosening the assertion: it is the assertion that tells the two
    implementations apart.
    """
    fake_utility(env, 'sleep 300 & echo "CHILD $!"; sleep 300')
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",), timeout_s=60)])
    store.start(job)
    child = _wait_for_child(store, job)
    assert _alive(child), "the grandchild should be running before the cancel"
    assert store.cancel(job) is True
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and _alive(child):
        time.sleep(0.05)
    assert not _alive(child), "the grandchild survived the cancel: the group was not killed"
    state = store.status(job)
    assert state["state"] == "cancelled"
    assert state["steps"][0]["state"] == "cancelled", "a cancelled step is never `failed`"


def test_cancelling_is_observed_before_cancelled(tmp_path: Path, env: EngineEnv) -> None:
    """The intermediate state is not decoration: it is how a step that ends 143 knows the difference
    between a cancel and a crash."""
    fake_utility(env, "sleep 3")
    events: list[tuple[str, dict[str, Any]]] = []
    store = make_store(tmp_path, events)
    job = store.create({}, [Step("s", ("blockMesh",), timeout_s=60)])
    store.start(job)
    time.sleep(0.6)
    store.cancel(job)
    states = [data["state"] for name, data in events if name == "job.state"]
    assert "cancelling" in states, f"cancelling was never observed: {states}"
    assert states.index("cancelling") < states.index("cancelled")


def test_cancelling_a_finished_job_changes_nothing(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    store.start(job)
    assert settle(store, job)["state"] == "done"
    assert store.cancel(job) is False
    assert store.status(job)["state"] == "done"


def test_a_timeout_fails_the_step_and_kills_the_group(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, 'sleep 300 & echo "CHILD $!"; sleep 300')
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",), timeout_s=1)])
    store.start(job)
    child = _wait_for_child(store, job)
    state = settle(store, job, timeout=15)
    assert state["state"] == "failed"
    assert state["error"]["code"] == "agent.timeout"
    time.sleep(0.4)
    assert not _alive(child), "the timeout must kill the group too"


def test_the_log_cap_fails_the_step(
    tmp_path: Path, env: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runaway program must not fill the disk. The cap is patched small to keep it quick."""
    monkeypatch.setattr(jobs, "MAX_LOG_BYTES", 4096)
    fake_utility(
        env, "i=0; while [ $i -lt 20000 ]; do echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; i=$((i+1)); done"
    )
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",), timeout_s=60)])
    store.start(job)
    state = settle(store, job, timeout=30)
    assert state["state"] == "failed"
    assert state["error"]["code"] == "agent.log_limit"


# ---------------------------------------------------------------- files


def test_put_in_two_chunks_and_the_offset_rules(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    first = store.put(job, "constant/big.bin", b"hello ", 0, False)
    assert first == 6
    second = store.put(job, "constant/big.bin", b"world", first, True)
    assert second == 11
    assert (store.job_dir(job) / "constant" / "big.bin").read_bytes() == b"hello world"
    # A wrong offset is refused: this is what makes a retry after a dropped connection safe.
    with pytest.raises(JobBadParams, match="offset"):
        store.put(job, "constant/big.bin", b"x", 3, False)


def test_put_is_refused_once_the_job_has_started(tmp_path: Path, env: EngineEnv) -> None:
    """A file that appears while a solver is reading the case is a race, not a feature."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    store.start(job)
    settle(store, job)
    with pytest.raises(JobBadParams, match="queued"):
        store.put(job, "x", b"y", 0, True)


def test_a_file_over_the_limit_is_refused(
    tmp_path: Path, env: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "MAX_FILE_BYTES", 16)
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    with pytest.raises(Exception, match="over the 16 limit"):
        store.create({"constant/big": b"x" * 100}, [Step("s", ("blockMesh",))])
    assert list((tmp_path / "jobs").iterdir()) == []


def test_copy_from_a_done_job_and_the_files_argument_wins(tmp_path: Path, env: EngineEnv) -> None:
    """The snappy case: `constant/polyMesh` comes from an earlier meshing run. A file given in
    `files` **wins**, so a caller can override one artifact without copying the rest again."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    source = store.create(
        {"constant/polyMesh/points": "from the source"}, [Step("s", ("blockMesh",))]
    )
    store.start(source)
    settle(store, source)

    copied = store.create(
        {"constant/polyMesh/points": "overridden"},
        [Step("s", ("blockMesh",))],
        copy_from={"job": source, "paths": ["constant/polyMesh"]},
    )
    assert (store.job_dir(copied) / "constant" / "polyMesh" / "points").read_text() == "overridden"
    assert store.status(copied)["copy_from"]["job"] == source

    # And with no override, the source's content arrives.
    plain = store.create(
        {}, [Step("s", ("blockMesh",))], copy_from={"job": source, "paths": ["constant/polyMesh"]}
    )
    assert (store.job_dir(plain) / "constant" / "polyMesh" / "points").read_text() == (
        "from the source"
    )


def test_copy_from_refuses_a_source_that_is_not_done(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    source = store.create({"constant/a": "1"}, [Step("s", ("blockMesh",))])
    with pytest.raises(JobBadParams, match="not done"):
        store.create(
            {}, [Step("s", ("blockMesh",))], copy_from={"job": source, "paths": ["constant/a"]}
        )


def test_copy_from_refuses_a_climb_and_an_unknown_id(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    source = store.create({"constant/a": "1"}, [Step("s", ("blockMesh",))])
    store.start(source)
    settle(store, source)
    with pytest.raises(JobBadParams):
        store.create({}, [Step("s", ("blockMesh",))], copy_from={"job": source, "paths": ["../x"]})
    # **`agent.not_found`, not `agent.bad_params`:** the id is well-formed, so this is a job that
    # is not there rather than a request that is wrong. A client deleting a job that already
    # finished needs to tell those apart, which is why the two are separate codes.
    with pytest.raises(JobNotFound) as caught:
        store.create(
            {},
            [Step("s", ("blockMesh",))],
            copy_from={"job": "j_ffffffffffff", "paths": ["a"]},
        )
    assert caught.value.code == "agent.not_found"


def test_tail_reads_the_last_lines_and_reports_truncation(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, 'i=0; while [ $i -lt 300 ]; do echo "line $i"; i=$((i+1)); done')
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    store.start(job)
    settle(store, job)
    tail = store.tail(job, "s", lines=10)
    assert tail["lines"] == [f"line {i}" for i in range(290, 300)]
    assert tail["truncated"] is True
    with pytest.raises(JobBadParams):
        store.tail(job, "s", lines=0)
    with pytest.raises(JobBadParams):
        store.tail(job, "nope")


def test_log_lines_are_capped_per_event_and_all_arrive(tmp_path: Path, env: EngineEnv) -> None:
    """A program printing thousands of lines must not put thousands in one event, and must
    not lose any.
    Both halves matter: the first is the UI thread, the second is the user's log."""
    fake_utility(env, 'i=0; while [ $i -lt 10000 ]; do echo "line $i"; i=$((i+1)); done')
    events: list[tuple[str, dict[str, Any]]] = []
    store = make_store(tmp_path, events)
    job = store.create({}, [Step("s", ("blockMesh",))])
    store.start(job)
    settle(store, job, timeout=40)
    batches = [data["lines"] for name, data in events if name == "log.lines"]
    assert batches, "no log lines were streamed"
    assert max(len(batch) for batch in batches) <= jobs.LOG_EVENT_MAX_LINES
    seen = [line for batch in batches for line in batch]
    assert seen == [f"line {i}" for i in range(10000)], "the stream lost or reordered lines"


def test_a_flood_beyond_the_ring_is_counted_not_lost_silently(
    tmp_path: Path, env: EngineEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**FIXES-B B7, against a real process.** With a small ring, every line the step printed is
    either in an event or counted in `dropped`; no event is over the per-event cap. (The
    bounded-stream logic itself is tested without a process in `test_agent_jobs_limits.py`, which
    also runs on Windows.)"""
    monkeypatch.setattr(jobs, "LOG_RING_LINES", 1000)
    fake_utility(env, 'i=0; while [ $i -lt 30000 ]; do echo "line $i"; i=$((i+1)); done')
    events: list[tuple[str, dict[str, Any]]] = []
    store = make_store(tmp_path, events)
    job = store.create({}, [Step("s", ("blockMesh",))])
    store.start(job)
    settle(store, job, timeout=60)
    batches = [data for name, data in events if name == "log.lines"]
    assert max(len(data["lines"]) for data in batches) <= jobs.LOG_EVENT_MAX_LINES
    sent = sum(len(data["lines"]) for data in batches)
    assert sent + sum(data["dropped"] for data in batches) == 30000


def test_long_lines_are_cut(tmp_path: Path, env: EngineEnv) -> None:
    """A single enormous line — a dumped array — must not cross the wire whole."""
    fake_utility(env, "python3 -c \"print('x' * 100000)\"")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    store.start(job)
    settle(store, job)
    lines = store.tail(job, "s")["lines"]
    assert len(lines[0]) <= jobs.MAX_LINE_CHARS


# ---------------------------------------------------------------- lifecycle


def test_recover_kills_a_live_orphan(tmp_path: Path, env: EngineEnv) -> None:
    """**A job never outlives the agent.** A state file left `running` with a live pid is killed and
    marked `interrupted` — the work may have been fine and only the reporting stopped, so it is not
    `failed`."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        state = store.status(job)
        state["state"] = "running"
        state["steps"][0]["pid"] = child.pid
        state["steps"][0]["pgid"] = os.getpgid(child.pid)
        state["steps"][0]["start_ticks"] = jobs._proc_start_ticks(child.pid)
        jobs.write_state(store.job_dir(job), state)
        assert store.recover() == [job]
        time.sleep(0.3)
        assert child.poll() is not None, "the orphan was not killed"
        assert store.status(job)["state"] == "interrupted"
    finally:
        if child.poll() is None:
            child.kill()


def test_recover_does_not_kill_a_reused_pid(tmp_path: Path, env: EngineEnv) -> None:
    """**The check that stops us killing an innocent process.** A pid is reused; a pid WITH its
    start time is not. A mismatch means this is somebody else's process, so it is marked
    `interrupted` and
    left alone."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        state = store.status(job)
        state["state"] = "running"
        state["steps"][0]["pid"] = child.pid
        state["steps"][0]["start_ticks"] = 1  # deliberately wrong
        jobs.write_state(store.job_dir(job), state)
        store.recover()
        time.sleep(0.3)
        assert child.poll() is None, "a pid with the wrong start time must NOT be killed"
        assert store.status(job)["state"] == "interrupted"
    finally:
        child.kill()


def test_recover_marks_a_dead_pid_interrupted(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    state = store.status(job)
    state["state"] = "running"
    state["steps"][0]["pid"] = 999_999_999  # certainly not running
    jobs.write_state(store.job_dir(job), state)
    assert store.recover() == [job]
    assert store.status(job)["state"] == "interrupted"


def test_shutdown_interrupts_running_jobs(tmp_path: Path, env: EngineEnv) -> None:
    """T-144c calls this at desktop exit: the agent must not leave a solver behind."""
    fake_utility(env, "sleep 30")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",), timeout_s=60)])
    store.start(job)
    time.sleep(0.8)
    store.shutdown()
    assert store.status(job)["state"] == "interrupted"


def test_list_is_newest_first_and_survives_a_broken_state_file(
    tmp_path: Path, env: EngineEnv
) -> None:
    """**A job with an unreadable state file is listed, not hidden.** A job that vanished
    from the list
    while its folder is on disk is worse than one that says why."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    first = store.create({}, [Step("s", ("blockMesh",))])
    time.sleep(0.01)
    second = store.create({}, [Step("s", ("blockMesh",))])
    listed = [item["id"] for item in store.jobs()]
    assert set(listed) == {first, second}
    broken = store.record_path(first)
    broken.write_text("{not json")
    listed = store.jobs()
    assert any(item["id"] == first and item["state"] == "unknown" for item in listed)


def test_every_running_step_is_marked_running(tmp_path: Path, env: EngineEnv) -> None:
    """**Found on a real pitzDaily run.** `start` writes `running` for step 0 and nothing did
    for the later ones, so step 1 read `queued` from launch to completion — measured, the solve
    was `queued` for its whole 40 seconds. A client could not tell a begun step from an
    unstarted one, and a cancel that looked for a running step to signal would find none."""
    fake_utility(env, "sleep 0.4")
    store = make_store(tmp_path)
    job = store.create({}, [Step("one", ("blockMesh",)), Step("two", ("blockMesh",))])
    store.start(job)
    seen_running: set[str] = set()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        for step in store.status(job)["steps"]:
            if step["state"] == "running":
                seen_running.add(step["name"])
        if store.status(job)["state"] not in ("queued", "running"):
            break
        time.sleep(0.02)
    assert seen_running == {"one", "two"}, f"only these were seen running: {seen_running}"


def test_a_second_start_is_refused_while_running(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, "sleep 2")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",), timeout_s=60)])
    store.start(job)
    with pytest.raises(Exception, match="already running"):
        store.start(job)
    store.cancel(job)


def _alive(pid: int) -> bool:
    from stokes_agent.daemon import process_alive

    return process_alive(pid)


def _wait_for_child(store: JobStore, job: str, timeout: float = 10.0) -> int:
    """The grandchild's pid, from the `CHILD <pid>` line the fake prints."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # **`log.tail` raises while the log does not exist yet**, and the log is created when
        # the step
        # starts — so a poller that treats the refusal as fatal races the step. That is this
        # helper's
        # bug, found when the full suite ran it under load; the alternative (making `tail`
        # answer empty
        # for a missing log) would change a contract no brief requires and this test does not need.
        try:
            found = store.tail(job, "s")["lines"]
        except JobBadParams:
            time.sleep(0.05)
            continue
        for line in found:
            if line.startswith("CHILD "):
                return int(line.split()[1])
        time.sleep(0.05)
    raise AssertionError("the fake never printed its child's pid")


def test_a_signal_killed_step_is_not_recorded_as_a_crash(tmp_path: Path, env: EngineEnv) -> None:
    """**143 is SIGTERM**, measured on this host. A step that ends 143 while a cancel was asked
    for is `cancelled`; without the state check it would be recorded as a failure with rc
    143."""
    fake_utility(env, "sleep 30")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",), timeout_s=60)])
    store.start(job)
    time.sleep(0.8)
    # Kill the group directly, as a cancel would, but leave the state as `cancelling` ourselves
    # so the
    # assertion is about the recording rule and not about the cancel path.
    state = store.status(job)
    state["state"] = "cancelling"
    jobs.write_state(store.job_dir(job), state)
    os.killpg(state["steps"][0]["pgid"], signal.SIGTERM)
    state = settle(store, job, timeout=15)
    assert state["steps"][0]["state"] == "cancelled"
    assert state["state"] == "cancelled"


# ---------------------------------------------------------------- the flag allow-list (T-202b)


@pytest.mark.parametrize(
    "argv",
    (
        ("blockMesh",),
        ("checkMesh",),
        ("surfaceFeatures",),
        ("foamRun", "-solver", "incompressibleFluid"),
        ("snappyHexMesh", "-overwrite"),
    ),
)
def test_an_allowed_invocation_is_accepted(
    tmp_path: Path, env: EngineEnv, argv: tuple[str, ...]
) -> None:
    """The invocations the e2e skeleton actually makes, and nothing more.

    The list is short on purpose: the case directory is the interface, so a utility is passed
    nothing or
    one `-solver`. Each of these was checked against `tests/fixtures/of14/help/<utility>.txt`,
    which is
    the install's own `-help` output.
    """
    fake_utility(env, "exit 0")
    fake_utility(env, "exit 0", name="checkMesh")
    fake_utility(env, "exit 0", name="surfaceFeatures")
    fake_utility(env, "exit 0", name="foamRun")
    fake_utility(env, "exit 0", name="snappyHexMesh")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", argv)])
    assert store.status(job)["steps"][0]["argv"] == list(argv)


@pytest.mark.parametrize(
    "argv",
    (
        # Flags that are a way out of the job folder, refused for EVERY utility.
        ("foamRun", "-case", "/etc"),
        ("blockMesh", "-case", "../other"),
        ("blockMesh", "-parallel"),
        ("foamRun", "-libs", "evil.so"),
        ("blockMesh", "-roots", "/"),
        ("blockMesh", "-hostRoots", "/"),
        ("blockMesh", "-fileHandler", "uncollated"),
        # Flags that exist but are not needed: an allow-list is closed, not "anything harmless".
        ("blockMesh", "-noClean"),
        ("checkMesh", "-allGeometry"),
        ("snappyHexMesh", "-dict", "x"),
        ("surfaceFeatures", "-noFunctionObjects"),
    ),
)
def test_a_forbidden_or_unlisted_flag_is_refused(
    tmp_path: Path, env: EngineEnv, argv: tuple[str, ...]
) -> None:
    """**The second half of the boundary.** An allow-listed program with an unchecked
    argument list is not
    a sandbox: `-case` points the utility at a directory the job does not own, and `-libs` loads
    code into
    the process."""
    for name in ("blockMesh", "checkMesh", "snappyHexMesh", "surfaceFeatures", "foamRun"):
        fake_utility(env, "exit 0", name=name)
    store = make_store(tmp_path)
    with pytest.raises(JobBadParams) as caught:
        store.create({}, [Step("s", argv)])
    assert caught.value.code == "agent.bad_params"
    assert list((tmp_path / "jobs").iterdir()) == []


@pytest.mark.parametrize(
    "argv",
    (
        ("foamRun", "-solver", "simpleFoam"),
        ("foamRun", "-solver", "incompressibleFluid extra"),
        ("foamRun", "-solver"),
        ("foamRun", "-solver", ""),
    ),
)
def test_an_allowed_flag_with_a_wrong_value_is_refused(
    tmp_path: Path, env: EngineEnv, argv: tuple[str, ...]
) -> None:
    """**A value check, because an allow-listed flag with an unchecked value is a hole with a polite
    name.** `-solver` decides which equations are solved and therefore which dictionaries are
    read, so
    only the one solver the skeleton writes a case for is permitted."""
    fake_utility(env, "exit 0", name="foamRun")
    store = make_store(tmp_path)
    with pytest.raises(JobBadParams):
        store.create({}, [Step("s", argv)])


# ---------------------------------------------------------------- the dictionary lint (T-202b)


def test_create_refuses_an_unsafe_dictionary(tmp_path: Path, env: EngineEnv) -> None:
    """The lint runs at `create`, and a refusal leaves nothing on disk."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    with pytest.raises(Exception) as caught:
        store.create({"system/controlDict": "#codeStream\n{\n}\n"}, [Step("s", ("blockMesh",))])
    assert caught.value.code == "agent.unsafe_dict"
    assert caught.value.details["file"] == "system/controlDict"
    assert caught.value.details["line"] == 1
    assert list((tmp_path / "jobs").iterdir()) == []


def test_put_lints_only_the_completed_file(tmp_path: Path, env: EngineEnv) -> None:
    """**A file is not a dictionary until its last chunk lands.** Checking each chunk
    would refuse a file
    split mid-directive, which is a false refusal — so a partial chunk is accepted and the final
    one is
    checked."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    first = store.put(job, "system/controlDict", b"#code", 0, False)
    assert first == 5, "a partial chunk is accepted: the directive is not complete yet"
    with pytest.raises(Exception) as caught:
        store.put(job, "system/controlDict", b"Stream\n", first, True)
    assert caught.value.code == "agent.unsafe_dict"


def test_start_lints_what_is_on_disk(tmp_path: Path, env: EngineEnv) -> None:
    """**Not redundant.** `create` and `put` see what they wrote; a file that arrived
    some other way — an
    older agent's staging, a hand edit — is caught only here, before a utility can read it."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    job_dir = store.job_dir(job)
    (job_dir / "system").mkdir(exist_ok=True)
    (job_dir / "system" / "controlDict").write_text('libs ("evil.so");\n')
    with pytest.raises(Exception) as caught:
        store.start(job)
    assert caught.value.code == "agent.unsafe_dict"
    assert store.status(job)["state"] == "queued", "a refused start must not run the job"


def test_copy_from_lints_what_it_brings_in(tmp_path: Path, env: EngineEnv) -> None:
    """A file copied from an older job is linted like any other — the rules apply to
    what is in the new
    job, not to how it got there."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    # **The source is made `done` FIRST, then the unsafe file appears.** Writing it before the start
    # would be caught by that job's own `start` lint — which is the right behaviour, but it tests a
    # different rule than this one. The scenario being tested is a file that reached
    # a finished job's
    # folder some other way, which is exactly what an older agent's staging looks like.
    source = store.create({}, [Step("s", ("blockMesh",))])
    store.start(source)
    settle(store, source)
    (store.job_dir(source) / "system").mkdir(exist_ok=True)
    (store.job_dir(source) / "system" / "controlDict").write_text("#codeStream\n")
    with pytest.raises(Exception) as caught:
        store.create(
            store.create(
                {},
                [Step("s", ("blockMesh",))],
                copy_from={"job": source, "paths": ["system/controlDict"]},
            )
        )
    assert caught.value.code == "agent.unsafe_dict"


# ---------------------------------------------------------------- the parser hook (T-202b)


class _CountingParser:
    """A test parser: one event per line, and a report of the line count."""

    def __init__(self, event: str = "monitor.residual", blocks: bool = True) -> None:
        self.n = 0
        self.event = event
        self.blocks = blocks

    def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for _ in lines:
            self.n += 1
            data: dict[str, Any] = {"value": self.n}
            if self.blocks:
                data["blocks"] = [self.n]
            out.append((self.event, data))
        return out

    def report(self) -> dict[str, Any]:
        return {"lines": self.n}


class _ExplodingParser:
    """A parser that raises, to prove a parser cannot fail a job."""

    def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
        raise RuntimeError("the format changed")

    def report(self) -> dict[str, Any]:
        raise RuntimeError("and so did the summary")


def test_a_parser_sees_every_line_and_reports(tmp_path: Path, env: EngineEnv) -> None:
    """The report is written whatever the outcome, which is what makes it something a
    client can rely
    on."""
    fake_utility(env, 'i=0; while [ $i -lt 500 ]; do echo "line $i"; i=$((i+1)); done')
    jobs.PARSERS["counting"] = _CountingParser
    try:
        store = make_store(tmp_path)
        job = store.create({}, [Step("s", ("blockMesh",), parse="counting")])
        store.start(job)
        state = settle(store, job, timeout=30)
        assert state["state"] == "done"
        assert state["reports"]["s"] == {"lines": 500}
    finally:
        del jobs.PARSERS["counting"]


def test_parser_events_are_rate_limited_and_lose_nothing(tmp_path: Path, env: EngineEnv) -> None:
    """**The limiter protects the wire; it is not a licence to drop samples.** 1,000
    lines arrive in well
    under a second, so the events are few — but the `blocks` lists they carry add up to all
    1,000 values,
    because coalescing concatenates rather than replaces."""
    fake_utility(env, 'i=0; while [ $i -lt 1000 ]; do echo "line $i"; i=$((i+1)); done')
    jobs.PARSERS["counting"] = _CountingParser
    events: list[tuple[str, dict[str, Any]]] = []
    try:
        store = make_store(tmp_path, events)
        job = store.create({}, [Step("s", ("blockMesh",), parse="counting")])
        store.start(job)
        settle(store, job, timeout=30)
    finally:
        del jobs.PARSERS["counting"]
    residuals = [data for name, data in events if name == "monitor.residual"]
    assert residuals, "no parser events were emitted"
    assert len(residuals) < 100, (
        f"{len(residuals)} events for 1,000 lines: the limiter did not work"
    )
    carried = [value for data in residuals for value in data.get("blocks", [])]
    assert sorted(carried) == list(range(1, 1001)), "coalescing lost values"
    assert all("job" in data and "step" in data for data in residuals), "job/step must be added"


def test_a_parser_cannot_fail_a_job(tmp_path: Path, env: EngineEnv) -> None:
    """**A parser reads output we did not write.** A wording change in a point
    release must not turn a
    successful mesh into a failed job, so an exception becomes a report and nothing else."""
    fake_utility(env, 'echo "hello"; exit 0')
    jobs.PARSERS["exploding"] = _ExplodingParser
    try:
        store = make_store(tmp_path)
        job = store.create({}, [Step("s", ("blockMesh",), parse="exploding")])
        store.start(job)
        state = settle(store, job)
        assert state["state"] == "done", "the parser's failure failed the job"
        assert state["steps"][0]["rc"] == 0
        assert "error" in state["reports"]["s"]
    finally:
        del jobs.PARSERS["exploding"]


def test_a_parser_that_cannot_be_built_does_not_fail_the_job(
    tmp_path: Path, env: EngineEnv
) -> None:
    """`parse_params` comes from a client, so a bad parameter must not stop a mesh."""

    def needs_an_argument(required: str) -> _CountingParser:
        return _CountingParser()

    fake_utility(env, "exit 0")
    jobs.PARSERS["needs_arg"] = needs_an_argument
    try:
        store = make_store(tmp_path)
        job = store.create({}, [Step("s", ("blockMesh",), parse="needs_arg")])
        store.start(job)
        state = settle(store, job)
        assert state["state"] == "done"
        assert "error" in state["reports"]["s"]
    finally:
        del jobs.PARSERS["needs_arg"]


def test_an_unknown_parser_name_is_refused_at_create(tmp_path: Path, env: EngineEnv) -> None:
    """A clear message about a name, rather than an error in a job thread halfway through a mesh."""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    with pytest.raises(JobBadParams, match="log parser"):
        store.create({}, [Step("s", ("blockMesh",), parse="nope")])


# ---------------------------------------------------------------- read (T-202b)


def test_read_returns_text_and_truncates(tmp_path: Path, env: EngineEnv) -> None:
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    (store.job_dir(job) / "postProcessing" / "residuals" / "0").mkdir(parents=True)
    target = store.job_dir(job) / "postProcessing" / "residuals" / "0" / "residuals.dat"
    target.write_text("line one\nline two\n")
    whole = store.read(job, "postProcessing/residuals/0/residuals.dat")
    assert whole["text"] == "line one\nline two\n"
    assert whole["truncated"] is False
    part = store.read(job, "postProcessing/residuals/0/residuals.dat", max_bytes=8)
    assert part["text"] == "line one"
    assert part["truncated"] is True
    assert part["size"] == 18


def test_read_handles_the_real_postprocessing_directory_name(
    tmp_path: Path, env: EngineEnv
) -> None:
    """**The measured case.** An OpenFOAM function object names its output directory after its own
    invocation, and for an argument-taking function that name is
    `patchAverage(patch=inlet,fields=(pU))` — parentheses, `=` and commas, with the spaces of
    `fields=(p U)` removed. Verified from a real pitzDaily run in T-200.

    `REL_PATH_RE` refuses that name, which is why reading uses the relaxed rule. Without it,
    every result
    written by a function taking arguments would be unreadable.
    """
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    name = "patchAverage(patch=inlet,fields=(pU))"
    folder = store.job_dir(job) / "postProcessing" / name / "0"
    folder.mkdir(parents=True)
    (folder / "surfaceFieldValue.dat").write_text("# Time\tareaAverage(p)\n0\t1.5e+04\n")
    reply = store.read(job, f"postProcessing/{name}/0/surfaceFieldValue.dat")
    assert "areaAverage(p)" in reply["text"]


def test_read_refuses_a_climb_and_a_symlink(tmp_path: Path, env: EngineEnv) -> None:
    """**The relaxation widens which names are spellable, not where a path may reach.**"""
    fake_utility(env, "exit 0")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("s")
    (store.job_dir(job) / "link").symlink_to(outside)
    for bad in ("postProcessing/../../etc/passwd", "//etc/passwd", "link/secret"):
        with pytest.raises(JobBadParams):
            store.read(job, bad)
    with pytest.raises(JobBadParams, match="max_bytes"):
        store.read(job, "anything", max_bytes=99_999_999)


def test_a_group_whose_leader_died_is_still_signalled(tmp_path: Path) -> None:
    """**FIXES-C item 5, first mechanism: the leader's death does not empty the group.**

    `_live_groups` skipped any step whose pid was not alive, so a job whose mesher or `mpirun` exits
    while its children keep running had those children left alone - and they are what hold the case
    directory. Measured before the fix: the leader was dead, the child was alive, and the group was
    not returned.

    **The start-time guard still applies while the leader lives**; with it gone there is no time to
    compare, so the guard moves to the group itself.
    """
    store = make_store(tmp_path)
    leader = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 300 & echo $!"],
        stdout=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    assert leader.stdout is not None
    child = int(leader.stdout.readline().strip())
    leader.wait()
    time.sleep(0.3)
    pgid = os.getpgid(child)
    try:
        groups = store._live_groups("j_under_test", {"steps": [{"pid": leader.pid, "pgid": pgid}]})
        assert pgid in groups, (
            "a group with living members was skipped because its leader had exited; the survivors "
            "would never be killed"
        )
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)


def test_a_group_with_no_members_is_not_signalled(tmp_path: Path) -> None:
    """**The guard that keeps the first fix safe.** A leaderless group is signalled only
    while it has a member: once the group is empty its pgid may be reused, and signalling a
    recycled pgid is worse than leaving an orphan."""
    store = make_store(tmp_path)
    proc = subprocess.Popen(["/bin/sh", "-c", "true"], start_new_session=True)
    proc.wait()
    pgid = proc.pid
    time.sleep(0.5)
    groups = store._live_groups("j_under_test", {"steps": [{"pid": pgid, "pgid": pgid}]})
    assert groups == [], f"an empty group was returned for signalling: {groups}"


def test_shutdown_does_not_relabel_a_job_the_user_cancelled(tmp_path: Path, env: EngineEnv) -> None:
    """**FIXES-C item 6: `cancelled` is a terminal state, and the user's word for it.**

    `_mark_interrupted` guarded `done`, `failed` and `interrupted` but not `cancelled`, so a job the
    user had deliberately cancelled was relabelled `interrupted` at desktop exit — telling them the
    agent had stopped their work when in fact they had. Measured before the fix: a `cancelled` state
    file read back as `interrupted` after `shutdown`.
    """
    fake_utility(env, "sleep 30")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",), timeout_s=60)])
    store.start(job)
    time.sleep(0.8)
    # The user cancels, and only then does the app exit.
    store.cancel(job)
    time.sleep(0.5)
    store.shutdown()
    assert store.status(job)["state"] == "cancelled", (
        "a job the user cancelled was relabelled by the agent's exit"
    )


def test_shutdown_still_marks_a_job_it_stopped_itself_interrupted(
    tmp_path: Path, env: EngineEnv
) -> None:
    """**The other half, and the reason the fix is not just 'do not overwrite'.**

    A job still RUNNING when the agent exits was stopped by the agent, not the user, and
    `interrupted` is the honest word for it. `shutdown` writes `cancelling` for the jobs it is
    stopping, so `_stopping` is what tells the two apart - without it, fixing the first case would
    have made this one report `cancelled` and blamed the user.
    """
    fake_utility(env, "sleep 30")
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",), timeout_s=60)])
    store.start(job)
    time.sleep(0.8)
    store.shutdown()
    assert store.status(job)["state"] == "interrupted"


def test_shutdown_leaves_an_already_finished_job_alone(tmp_path: Path, env: EngineEnv) -> None:
    """**The guard's own case, which a running job cannot exercise.**

    Both tests above start a job and let `shutdown` stop it, so they never reach the guard — the job
    is still running and `_mark_interrupted` has real work to do. This one writes each
    terminal state
    first and then calls `shutdown`, which is what catches a terminal state missing from the guard.
    `cancelled` was missing from it, and that is the defect item 6 names.
    """
    fake_utility(env, "exit 0")  # `create` checks the utility exists on the engine's PATH
    store = make_store(tmp_path)
    job = store.create({}, [Step("s", ("blockMesh",))])
    job_dir = store.job_dir(job)
    for state in ("done", "failed", "cancelled", "interrupted"):
        with store._job_lock(job):
            store._save(job_dir, {"job": job, "state": state, "steps": [{"state": state}]})
        store._mark_interrupted(job)
        assert store.status(job)["state"] == state, f"{state} was relabelled by shutdown"
