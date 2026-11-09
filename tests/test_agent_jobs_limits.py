"""FIXES-B B5 and B7: polyMesh provenance and the bounded log stream.

**These run on Windows too.** `tests/test_agent_jobs.py` is POSIX only because it launches real
processes. Here the process is a stand-in (`FakePopen`, `StubProcess`) and only the platform launch
boundary (`jobs._launch`) is stubbed, so what is tested is the supervisor's own logic: what it
records, what it lints and what it sends. The real native Job Object boundary has separate coverage
in `tests/test_native_windows_jobs.py`; this fixture must not disable `NATIVE_WINDOWS` globally.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from stokes_agent import jobs
from stokes_agent.engine_env import EngineEnv
from stokes_agent.jobs import JobStore, JobUnsafeDict, Step
from stokes_agent.lint import MAX_SCAN_BYTES

BIG = MAX_SCAN_BYTES + 1024 * 1024


class FakePopen:
    """Stands in for the mesher: writes a real-sized binary mesh into the job, then exits 0."""

    writes = True

    def __init__(self, argv: list[str], cwd: Path, **_kwargs: Any) -> None:
        self.pid = 4242
        self.returncode = 0
        if FakePopen.writes:
            folder = Path(cwd) / "constant" / "polyMesh"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "points").write_bytes(b"\0" * BIG)
            (folder / "boundary").write_text("1\n(\n    a\n    {\n        type wall;\n    }\n)\n")

    def poll(self) -> int:
        return 0


def fake_launch(argv: list[str], cwd: Path, _env: dict[str, str], _output: Any) -> FakePopen:
    """The platform launch boundary: no real process, but the same recorded argv and cwd."""
    return FakePopen(argv, cwd)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JobStore:
    """A `JobStore` whose platform launch boundary is `FakePopen`; native branches stay live."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "blockMesh").write_bytes(b"")
    (bin_dir / "blockMesh.exe").write_bytes(b"")
    # **Executable, not merely present** — `shutil.which` checks the exec bit on POSIX but the
    # extension
    # on Windows, so without this every test here fails on Linux with
    # `JobMissingUtility: blockMesh is not on the engine's PATH` while passing on Windows.
    # `test_agent_jobs.py`'s `fake_utility` already does this; this file was missing it.
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
    monkeypatch.setattr(jobs, "_getpgid", lambda pid: pid)
    monkeypatch.setattr(jobs, "_launch", fake_launch)
    monkeypatch.setattr(FakePopen, "writes", True)
    return JobStore(tmp_path / "jobs")


def run_to_done(store: JobStore, job: str) -> dict[str, Any]:
    store.start(job)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = store.status(job)
        if state["state"] not in ("queued", "running", "cancelling"):
            return dict(state)
        time.sleep(0.02)
    raise AssertionError(f"job {job} never finished: {store.status(job)['state']}")


def meshed_job(store: JobStore, files: dict[str, str] | None = None) -> str:
    """A finished job whose `blockMesh` step wrote a binary mesh over the size limit."""
    job = store.create(files or {"system/controlDict": "x"}, [Step("mesh", ("blockMesh",))])
    assert run_to_done(store, job)["state"] == "done"
    return job


COPY = {"paths": ["constant/polyMesh"]}


# ---------------------------------------------------------------- B5


def test_a_mesh_the_agent_wrote_is_recorded_and_may_be_copied_and_started(store: JobStore) -> None:
    """**B5.** A real mesh is over the 8 MiB scan limit and binary; a lint that refuses it refuses
    every snappy case. The mesher's files are recorded in the job's record, `copy_from` carries
    that provenance over, and `create` and `start` of the copy both pass."""
    source = meshed_job(store)
    record = store.status(source)
    assert record["polymesh"]["constant/polyMesh/points"] == BIG
    FakePopen.writes = False
    copied = store.create({}, [Step("again", ("blockMesh",))], copy_from={"job": source, **COPY})
    assert store.status(copied)["polymesh_trusted"]["constant/polyMesh/points"] == BIG
    assert (store.job_dir(copied) / "constant" / "polyMesh" / "points").stat().st_size == BIG
    assert run_to_done(store, copied)["state"] == "done"


def test_a_copied_mesh_with_no_provenance_is_linted(store: JobStore) -> None:
    """A source whose record never says its mesher wrote the file gets no exemption: here the file
    was swapped in on disk after the job finished."""
    FakePopen.writes = False
    source = meshed_job(store)
    folder = store.job_dir(source) / "constant" / "polyMesh"
    folder.mkdir(parents=True)
    (folder / "points").write_bytes(b"\0" * BIG)
    assert "polymesh" not in store.status(source)
    with pytest.raises(JobUnsafeDict):
        store.create({}, [Step("again", ("blockMesh",))], copy_from={"job": source, **COPY})


def test_a_file_the_client_staged_in_the_source_is_not_the_meshers(store: JobStore) -> None:
    """The record lists what the step wrote, not what was already there before it launched."""
    source = meshed_job(store, {"constant/polyMesh/extra": "staged by a client\n"})
    manifest = store.status(source)["polymesh"]
    assert "constant/polyMesh/points" in manifest
    assert "constant/polyMesh/extra" not in manifest
    FakePopen.writes = False
    copied = store.create({}, [Step("again", ("blockMesh",))], copy_from={"job": source, **COPY})
    trusted = store.status(copied)["polymesh_trusted"]
    assert "constant/polyMesh/extra" not in trusted


def test_a_client_put_or_file_into_polymesh_is_still_fully_linted(store: JobStore) -> None:
    """**A client's polyMesh file gets the size, line and binary rules**, through `create` and
    through a chunked `put`."""
    big_text = "(0 0 0)\n" * (BIG // 8)
    with pytest.raises(JobUnsafeDict):
        store.create({"constant/polyMesh/points": big_text}, [Step("s", ("blockMesh",))])
    with pytest.raises(JobUnsafeDict):
        store.create({"constant/polyMesh/points": b"\0\1\2"}, [Step("s", ("blockMesh",))])
    job = store.create({}, [Step("s", ("blockMesh",))])
    with pytest.raises(JobUnsafeDict):
        store.put(job, "constant/polyMesh/points", big_text.encode(), 0, True)
    other = store.create({}, [Step("s", ("blockMesh",))])
    with pytest.raises(JobUnsafeDict):
        store.put(other, "constant/polyMesh/points", b"\0\1\2", 0, True)


def test_a_put_over_a_trusted_mesh_file_ends_the_trust(store: JobStore) -> None:
    """A client that overwrites a mesher's file with a same-size binary must not keep the
    exemption: the put is refused, the file stays on disk, and `start` must refuse it too."""
    source = meshed_job(store)
    FakePopen.writes = False
    copied = store.create({}, [Step("again", ("blockMesh",))], copy_from={"job": source, **COPY})
    with pytest.raises(JobUnsafeDict):
        store.put(copied, "constant/polyMesh/points", b"\0" * BIG, 0, True)
    assert "constant/polyMesh/points" not in store.status(copied).get("polymesh_trusted", {})
    with pytest.raises(JobUnsafeDict):
        store.start(copied)


# ---------------------------------------------------------------- B7


class StubProcess:
    """A process that is still running for ``polls`` calls, then has exited with code 0."""

    def __init__(self, polls: int | None = 1) -> None:
        self.polls = polls
        self.pid = 1

    def poll(self) -> int | None:
        if self.polls is None:
            return None
        if self.polls > 0:
            self.polls -= 1
            return None
        return 0


class CountingParser:
    """A step parser that only counts the lines it was fed."""

    def __init__(self) -> None:
        self.seen = 0

    def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
        self.seen += len(lines)
        return []

    def report(self) -> dict[str, Any]:
        return {"seen": self.seen}


def drive_wait(
    tmp_path: Path, log_text: str, process: Any, parser: Any = None
) -> tuple[tuple[str, int | None, dict[str, str] | None], list[dict[str, Any]], Path]:
    """Run `_wait` over a prewritten log and return its outcome and the `log.lines` events."""
    events: list[dict[str, Any]] = []
    store = JobStore(
        tmp_path / "jobs",
        on_event=lambda name, data: events.append(data) if name == "log.lines" else None,
    )
    log_path = tmp_path / "log.s"
    log_path.write_text(log_text)
    job_dir = tmp_path / "jobs" / "j_000000000001"
    step = Step("s", ("blockMesh",))
    wrapped = jobs._StepParser("p", parser) if parser is not None else None
    outcome = store._wait(
        "j_000000000001", job_dir, 0, step, process, log_path, time.monotonic(), wrapped
    )
    return outcome, events, log_path


def test_a_flood_of_lines_is_capped_counted_and_reported_as_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**B7.** A step that prints far more than the ring holds: every line is either sent or
    counted in `dropped`, no event is over the per-event cap, the NEWEST lines are the ones kept,
    and the parser still saw every one."""
    monkeypatch.setattr(jobs, "LOG_RING_LINES", 1000)
    total = 6000
    parser = CountingParser()
    outcome, events, _ = drive_wait(
        tmp_path, "".join(f"line {i}\n" for i in range(total)), StubProcess(1), parser
    )
    assert outcome[0] == "done"
    sent = [line for event in events for line in event["lines"]]
    dropped = sum(event["dropped"] for event in events)
    assert dropped > 0, "nothing was reported dropped"
    assert len(sent) + dropped == total, "every line must be sent or counted"
    assert max(len(event["lines"]) for event in events) <= jobs.LOG_EVENT_MAX_LINES
    assert sent[-1] == f"line {total - 1}", "the newest lines are the ones kept"
    assert parser.seen == total, "the parser must see every line, dropped from the wire or not"


def test_the_default_caps_are_set() -> None:
    assert 0 < jobs.LOG_EVENT_MAX_LINES <= jobs.LOG_RING_LINES
    assert jobs.LOG_READ_CHUNK_BYTES >= jobs.MAX_LINE_CHARS


def test_a_log_over_the_limit_is_cut_and_ends_with_a_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**B7.** The step is killed and failed, and the file on disk is cut back to the limit and ends
    with a marker that says so."""
    monkeypatch.setattr(jobs, "MAX_LOG_BYTES", 4096)
    killed: list[bool] = []
    monkeypatch.setattr(JobStore, "_kill_group", lambda self, process: killed.append(True))
    outcome, events, log_path = drive_wait(tmp_path, "aaaaaaaaa\n" * 3000, StubProcess(None))
    assert outcome[0] == "failed" and outcome[2] is not None
    assert outcome[2]["code"] == "agent.log_limit"
    assert killed, "the step was not killed"
    raw = log_path.read_bytes()
    assert len(raw) <= 4096 + len(jobs.LOG_TRUNCATED_MARKER) + 2
    assert raw.rstrip(b"\n").endswith(jobs.LOG_TRUNCATED_MARKER.encode())
    assert any(jobs.LOG_TRUNCATED_MARKER in event["lines"] for event in events), (
        "the marker is part of the stream too"
    )


def test_a_log_line_longer_than_a_chunk_still_makes_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One "line" with no newline for more than a whole read chunk must not stall the reader."""
    monkeypatch.setattr(jobs, "LOG_READ_CHUNK_BYTES", 8192)
    store = JobStore(tmp_path / "jobs")
    log_path = tmp_path / "log.s"
    log_path.write_text("x" * 20000 + "\nend\n")
    offset = 0
    seen: list[str] = []
    for _ in range(10):
        offset, lines, _ = store._read_new_lines(log_path, offset)
        if not lines:
            break
        seen.extend(lines)
    assert offset == log_path.stat().st_size
    assert seen[-1] == "end"
    assert all(len(line) <= jobs.MAX_LINE_CHARS for line in seen)


# ------------------------------------------------------------------ Opus F6: space, and quotas


def test_a_case_over_the_JOB_QUOTA_is_refused_before_anything_is_written(
    store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus F6.** `MAX_FILE_BYTES` bounded ONE file and nothing bounded the folder, so a client
    could stage as many jobs as it liked until the disk filled - which is what happened twice.

    Refused from the request alone, so there is no folder to clean up.
    """
    monkeypatch.setattr(jobs, "MAX_JOB_BYTES", 1024)
    with pytest.raises(jobs.JobError) as caught:
        store.create({"system/x": "y" * 2048}, [Step("mesh", ("blockMesh",))])
    assert caught.value.code == "agent.too_large"
    assert "over the 1024-byte limit" in str(caught.value)
    # Nothing was created: a refusal must not leave a job folder behind.
    assert list(store.root.iterdir()) == []


def test_a_case_that_would_leave_the_disk_UNDER_THE_RESERVE_is_refused(
    store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The check neither disk incident had.** The agent wrote until the filesystem refused, which
    took the tooling down with it; this names the request and the room left instead.
    """
    monkeypatch.setattr(jobs, "DISK_RESERVE_BYTES", 2048)
    monkeypatch.setattr(jobs, "_free_bytes", lambda path: 1024)
    with pytest.raises(jobs.JobError) as caught:
        store.create({"system/x": "y" * 100}, [Step("mesh", ("blockMesh",))])
    assert caught.value.code == "agent.no_space"
    assert "under the" in str(caught.value) and "reserve" in str(caught.value)
    assert list(store.root.iterdir()) == []


def test_a_case_that_FITS_the_reserve_is_allowed(
    store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The false-refusal half**: the guard must not refuse an ordinary case on a healthy disk. On
    this machine `/home` has hundreds of GiB free, so a small case stages normally."""
    monkeypatch.setattr(jobs, "DISK_RESERVE_BYTES", 1024)
    monkeypatch.setattr(jobs, "_free_bytes", lambda path: 1024 * 1024)
    job = store.create({"system/x": "y"}, [Step("mesh", ("blockMesh",))])
    assert job.startswith("j_")
    assert store.job_dir(job).is_dir()


def test_an_UNREADABLE_root_reports_no_space_rather_than_plenty() -> None:
    """**The safe direction.** `_free_bytes` returning a large number for a path it cannot read
    would let the write proceed on precisely the check that exists to stop it. Zero refuses."""
    assert jobs._free_bytes(Path("/no/such/path/at/all")) == 0


def test_the_staged_size_counts_UTF8_and_bytes_the_way_the_write_does() -> None:
    assert jobs._total_bytes({"a": "abc"}) == 3
    assert jobs._total_bytes({"a": b"abcd"}) == 4
    assert jobs._total_bytes({"a": "é"}) == 2  # two bytes, one character
    assert jobs._total_bytes({}) == 0
