"""The run store: records, files, history and the session hooks (T-214b).

**A fake runner, no OpenFOAM.** The store is what turns an agent's events into a run a desktop
can show,
so the interesting cases are the ones where something goes wrong: a record that outlived its
session, a
corrupt file, a cancellation, a name that is already taken.

**What the store deliberately does NOT do** is debounce: `settle` accepts `coalesce` and ignores it,
because a solve is not an edit — a user who pressed Solve wants that solve, and replaying a run away
would be a replay that does not reproduce the project.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.run.errors import RunError, RunNotFound
from stokes.run.records import MAX_RECORD_BYTES, RUN_STATES, RunRecord
from stokes.run.store import (
    MAX_HISTORY,
    RESULTS_FOLDER,
    RESULTS_SUFFIX,
    RunStore,
    runs_dir,
)


class FakeRunner:
    """A runner that records calls and can play events back. The mesh tests' fake, for runs."""

    name = "local"

    def __init__(self, *, available: bool = True, state: str = "done") -> None:
        self.available = available
        self.why = "" if available else "No OpenFOAM host on this machine"
        self.state = state
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.job = "j_" + "b" * 12

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        self.calls.append((method, params))
        if method == "host.probe":
            return {
                "installs": [{"bashrc": "/x/env.sh", "version": "14", "flavour": "org"}],
                "default_install": "/x/env.sh",
            }
        if method == "job.create":
            return {"job": self.job}
        if method == "job.status":
            return {"state": self.state, "steps": [], "reports": {}}
        return {}


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    made = Session()
    made.execute("project.new", name="duct")
    return made


@pytest.fixture
def store(session: Session) -> RunStore:
    made = RunStore.of(session)
    made.bind(lambda name, data: None)
    return made


def make_run(store: RunStore, **changes: Any) -> RunRecord:
    """A run named the way `run.start` names one: through `name_for`, so uniqueness is exercised."""
    session = store._session
    record = RunRecord(id=store.new_id(), name=store.name_for(session.project), **changes)
    return store.add(record)


# ---------------------------------------------------------------- the record itself


def test_a_record_round_trips_through_json() -> None:
    record = RunRecord(id="r_1", state="done", last_residuals={"Ux": 1e-6}, monitors={"inlet": {}})
    again = RunRecord.from_dict(record.to_dict())
    assert again == record


def test_an_unknown_key_is_ignored_not_refused() -> None:
    """**A record written by a NEWER build is not corrupt, it is newer.** Refusing it would make a
    project un-openable after a downgrade, and the fields that remain are still true."""
    found = RunRecord.from_dict({"id": "r_1", "state": "done", "something_new": {"a": 1}})
    assert found.id == "r_1" and found.state == "done"


def test_every_documented_state_is_in_the_list() -> None:
    """The list is the complete set, and a state without a consumer is a run that shows nothing."""
    assert set(RUN_STATES) == {
        "queued",
        "running",
        "cancelling",
        "done",
        "done_unconverged",
        "failed",
        "cancelled",
        "interrupted",
        "skipped",
    }


def test_done_unconverged_is_not_a_failure() -> None:
    """**Many real runs stop at the iteration limit with residuals still falling.** Calling that
    `failed` would be a lie, and `successful` is what the reuse path branches on."""
    record = RunRecord(id="r_1", state="done_unconverged")
    assert record.final is True
    assert record.successful is True
    assert RunRecord(id="r_2", state="failed").successful is False


def test_a_lean_record_drops_the_history_rather_than_refusing() -> None:
    """A record over the cap is written WITHOUT its history, because the agent's own file is the
    authority for it — and refusing to write would lose the whole record."""
    # **Over the cap on purpose.** 20,000 floats encode to ~249 KiB — UNDER the 256 KiB cap — so the
    # first version of this test asserted a truncation that correctly did not happen.
    bulky = RunRecord(id="r_1", last_residuals={f"f{i}": 1.0 for i in range(60000)})
    assert len(json.dumps(bulky.to_dict()).encode("utf-8")) > MAX_RECORD_BYTES, (
        "the fixture must exceed the cap"
    )
    text = bulky.encode()
    assert len(text.encode("utf-8")) <= MAX_RECORD_BYTES
    assert json.loads(text)["truncated"] is True


# ---------------------------------------------------------------- the store


def test_a_new_record_is_written_to_disk(session: Session, store: RunStore) -> None:
    record = make_run(store)
    path = runs_dir(session) / record.id / "record.json"
    assert path.is_file()
    assert json.loads(path.read_text())["id"] == record.id


def test_records_are_listed_newest_first(store: RunStore) -> None:
    first = make_run(store)
    second = make_run(store)
    assert [item["id"] for item in store.all_records()] == [second.id, first.id]
    assert store.current().id == second.id, "the current run is the newest"


def test_the_brief_s_list_spelling_still_works(store: RunStore) -> None:
    """The method is `all_records` because a method named `list` shadows the builtin generic
    inside its
    own class; the brief's spelling is kept as an alias."""
    make_run(store)
    assert store.list() == store.all_records()


def test_status_and_record_raise_run_not_found(store: RunStore) -> None:
    with pytest.raises(RunNotFound):
        store.status("r_nope")


def test_a_name_is_unique_within_a_project(session: Session, store: RunStore) -> None:
    """ "duct", then "duct 2" — a list of runs all called `duct` tells a user nothing."""
    assert store.name_for(session.project) == "duct"
    make_run(store)
    assert store.name_for(session.project) == "duct 2"
    make_run(store)
    assert store.name_for(session.project) == "duct 3"


def test_update_ignores_an_unknown_field(store: RunStore) -> None:
    record = make_run(store)
    store.update(record.id, nonsense=1, state="done")
    assert store.record(record.id).state == "done"


# ---------------------------------------------------------------- the residual history


def test_residuals_are_appended_and_read(store: RunStore) -> None:
    record = make_run(store)
    store.append_residual(
        record.id,
        {
            "iteration": 1,
            "time": 0.1,
            "residuals": {"Ux": 0.5, "p": 0.3},
            "continuity": {"local": 1e-5},
        },
    )
    store.append_residual(
        record.id,
        {"iteration": 2, "time": 0.2, "residuals": {"Ux": 0.1}, "continuity": {"local": 2e-5}},
    )
    found = store.residuals(record.id)
    assert found["iterations"] == [1, 2]
    assert found["time"] == pytest.approx([0.1, 0.2])
    assert found["fields"]["Ux"] == pytest.approx([0.5, 0.1])
    assert found["continuity"]["local"] == pytest.approx([1e-5, 2e-5])


def test_since_returns_only_the_later_blocks(store: RunStore) -> None:
    """`since` is the iteration AFTER which to return, which is what a poller asks for."""
    record = make_run(store)
    for index in (1, 2, 3):
        store.append_residual(record.id, {"iteration": index, "residuals": {"Ux": index}})
    assert store.residuals(record.id, since=1)["iterations"] == [2, 3]
    assert store.residuals(record.id, since=3)["iterations"] == []
    assert store.residuals(record.id, since=0)["iterations"] == [1, 2, 3]


def test_a_field_absent_from_a_block_is_absent_from_the_lists(store: RunStore) -> None:
    """**Nothing is invented.** A field the solver did not report has no list, rather than a list of
    zeros that a chart would draw as a converged field."""
    record = make_run(store)
    store.append_residual(record.id, {"iteration": 1, "residuals": {"Ux": 1.0}})
    assert set(store.residuals(record.id)["fields"]) == {"Ux"}


def test_the_history_is_capped_and_the_cap_keeps_the_earliest(store: RunStore) -> None:
    """A run left going overnight must not grow without bound in a desktop process. Past the cap the
    block is dropped rather than the list truncated, so the chart keeps its beginning."""
    record = make_run(store)
    # **Iterations start at 1**, as a real run's do — numbering from 0 made the FIRST block
    # vanish from
    # `residuals(since=0)`, which filters `iteration > 0`. The store was right and the fixture
    # was wrong.
    store._history[record.id]["blocks"] = [
        {"iteration": i, "residuals": {"Ux": 1.0}} for i in range(1, MAX_HISTORY + 1)
    ]
    store.append_residual(record.id, {"iteration": 999_999, "residuals": {"Ux": 1.0}})
    assert len(store.residuals(record.id)["iterations"]) == MAX_HISTORY


def test_replace_history_reads_the_agent_file_back(store: RunStore) -> None:
    """**The event stream is rate-limited; the file is complete.** Reading it back is what makes a
    finished run's chart show the whole run rather than the part that survived the socket."""
    record = make_run(store)
    store.append_residual(record.id, {"iteration": 1, "residuals": {"Ux": 9.0}})
    store.replace_history(
        record.id,
        [{"iteration": 1, "residuals": {"Ux": 1.0}}, {"iteration": 2, "residuals": {"Ux": 2.0}}],
    )
    found = store.residuals(record.id)
    assert found["iterations"] == [1, 2]
    assert found["fields"]["Ux"] == pytest.approx([1.0, 2.0]), "the file's values win"


# ---------------------------------------------------------------- the events


def test_a_job_state_for_another_job_is_ignored(store: RunStore) -> None:
    """**Events arrive for every job on the runner.** A run must act only on its own, or one run's
    progress appears on another's record."""
    record = make_run(store, job="j_mine")
    store.runner_event(record.id, "job.state", {"job": "j_theirs", "state": "running"})
    assert store.record(record.id).state == "queued"


def test_a_running_job_moves_the_record(store: RunStore) -> None:
    record = make_run(store, job="j_mine")
    store.runner_event(record.id, "job.state", {"job": "j_mine", "state": "running"})
    assert store.record(record.id).state == "running"


def test_a_final_state_from_the_wire_does_NOT_end_the_run(store: RunStore) -> None:
    """**Only `finish` ends a run, and this test is the contract.**

    A final `job.state` used to set the record's state at once, which made it final *before*
    `finish`
    had made the three RPCs that set `converged` — and `settle`/`run.status` both stop at
    `record.final`. A caller then read a run with `iterations` right and `converged` still `False`.
    That is the whole of the `converged` race (Opus, 2026-10-15), and the fix is that the wire event
    leaves the record non-final with a visible reason.
    """
    record = make_run(store, job="j_mine")
    store.runner_event(record.id, "job.state", {"job": "j_mine", "state": "cancelled"})
    assert store.record(record.id).final is False, "the wire must not end a run; finish does"
    assert store.status(record.id).get("message") == "collecting"


def test_a_step_state_does_not_end_the_run(store: RunStore) -> None:
    """**`job.state` with a step is a step's state, not the job's.** The agent emits one per
    step, so
    treating it as the run's end would finish a solve when its first step finished."""
    record = make_run(store, job="j_mine")
    store.runner_event(
        record.id, "job.state", {"job": "j_mine", "state": "done", "step": "solve", "index": 0}
    )
    assert store.record(record.id).state == "queued", "a step finishing is not the run finishing"


def test_a_residual_event_appends_every_block_and_counts_iterations(store: RunStore) -> None:
    """**One event carries every completed block** (T-213 correction 2), so the store must
    walk the list
    rather than take the last — otherwise a fast run's history has gaps."""
    record = make_run(store, job="j_mine")
    store.runner_event(
        record.id,
        "monitor.residual",
        {
            "job": "j_mine",
            "blocks": [
                {"iteration": 1, "residuals": {"Ux": 1.0}},
                {"iteration": 2, "residuals": {"Ux": 0.5}},
            ],
        },
    )
    found = store.residuals(record.id)
    assert found["iterations"] == [1, 2]
    assert store.record(record.id).iterations == 2


def test_progress_sets_the_fraction_and_message(store: RunStore) -> None:
    record = make_run(store, job="j_mine")
    store.runner_event(
        record.id, "job.progress", {"job": "j_mine", "fraction": 0.5, "message": "iteration 3"}
    )
    status = store.status(record.id)
    assert status["fraction"] == pytest.approx(0.5)


def test_a_log_event_is_forwarded(store: RunStore) -> None:
    seen: list[tuple[str, dict]] = []
    store.bind(lambda name, data: seen.append((name, data)))
    record = make_run(store, job="j_mine")
    store.runner_event(
        record.id, "log.lines", {"job": "j_mine", "step": "solve", "lines": ["a", "b"]}
    )
    assert any(name == "run.log" for name, _ in seen)


# ---------------------------------------------------------------- persistence


def test_a_record_survives_a_new_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**The property that makes a run outlive the desktop.** A solver takes minutes; the project is
    reopened and the run must still be there."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj"
    first = Session()
    first.execute("project.new", path=str(folder), name="duct")
    store = RunStore.of(first)
    record = store.add(RunRecord(id=store.new_id(), name="duct", state="done", iterations=42))

    second = Session()
    second.execute("project.open", path=str(folder))
    reopened = RunStore.of(second)
    reopened.project_opened()
    found = reopened.list()
    assert len(found) == 1
    assert found[0]["id"] == record.id
    assert found[0]["iterations"] == 42


def test_a_corrupt_record_is_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**One bad file must not make a project un-openable.**"""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj"
    first = Session()
    first.execute("project.new", path=str(folder), name="duct")
    store = RunStore.of(first)
    good = store.add(RunRecord(id=store.new_id(), name="duct", state="done"))
    bad = runs_dir(first) / "r_broken"
    bad.mkdir(parents=True)
    (bad / "record.json").write_text("{not json")

    second = Session()
    second.execute("project.open", path=str(folder))
    reopened = RunStore.of(second)
    reopened.project_opened()
    assert [item["id"] for item in reopened.list()] == [good.id]


def test_the_session_hooks_have_the_protocol_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The regression that cost four rounds.** `Session.notify_extensions` calls
    `method(**kwargs)`: `project_opened(project=, path=)` and `project_saved(project=, path=,
    previous=)`. The hooks here used to take no arguments and `previous` alone, so **every** open
    and save raised `TypeError`, the session logged "session extension failed" and carried on, and
    the effect was silent: a reopened project had no runs at all, and Save-as copied nothing.

    The two tests below this one call `project_opened()` by hand, which is exactly why they passed
    throughout. This one deliberately does not: it lets the session make the call.
    """
    import inspect

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    store = RunStore(Session())
    for hook, expected in (
        ("project_opened", {"project", "path"}),
        ("project_saved", {"project", "path", "previous"}),
    ):
        signature = inspect.signature(getattr(store, hook))
        names = {name for name in signature.parameters if name != "self"}
        assert names == expected, f"{hook} takes {names}, the protocol passes {expected}"


def test_a_reopened_project_finds_the_records_the_session_did_not_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Through `project.open`, with no manual hook call.** A run saved by one session must be
    found by a new one — the records are on disk, and the store is created lazily *after* the open
    by whatever asks first (the results routes, or `run.list`), so construction itself has to load
    them.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj"
    first = Session()
    first.execute("project.new", path=str(folder), name="duct")
    written = RunStore.of(first).add(
        RunRecord(id=RunStore.of(first).new_id(), name="duct", state="done", iterations=7)
    )
    first.execute("project.save")

    second = Session()
    second.execute("project.open", path=str(folder))
    # **No `project_opened()` here.** The store did not exist when the session opened the project.
    found = RunStore.of(second).record(written.id)
    assert found is not None, "a reopened project lost its run records"
    assert found.iterations == 7
    assert [item["id"] for item in second.execute("run.list")["runs"]] == [written.id]


def test_saving_copies_the_field_cache_into_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The first save too**, out of the scratch folder: without it a run's fields are lost the
    moment the project gets a home, and the host they came from may be gone."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj"
    session = Session()
    session.execute("project.new", name="duct")
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), name="duct", state="done"))
    # **`_cache_base()` first**, which is what materialises the scratch folder: `shape_dir()`
    # creates it lazily, and a run's fields are written through the same call, so this is the order
    # production uses rather than a convenience for the test.
    scratch = store._cache_base() / RESULTS_FOLDER
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / f"{record.id}{RESULTS_SUFFIX}").write_bytes(b"STKFLD1\x00")
    # A file that is not ours: a name this store could not have written is not copied.
    (scratch / "not-a-run.stkfld").write_bytes(b"junk")

    session.execute("project.save", path=str(folder))
    from stokes.geometry.shapes import cache_dir

    # **`save_project` appends the `.stokes` extension**, so the project folder is not the path
    # that was passed: the cache lives beside the file it names.
    copied = cache_dir(Path(f"{folder}.stokes")) / RESULTS_FOLDER
    assert (copied / f"{record.id}{RESULTS_SUFFIX}").read_bytes() == b"STKFLD1\x00"
    assert not (copied / "not-a-run.stkfld").exists()
    # And a `.part` from the copy is never left behind.
    assert list(copied.glob("*.part")) == []


def test_opening_a_project_replaces_the_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store belongs to a session, and a session has one project at a time."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", path=str(tmp_path / "a"), name="a")
    store = RunStore.of(session)
    store.add(RunRecord(id=store.new_id(), name="a"))
    session.execute("project.new", path=str(tmp_path / "b"), name="b")
    store.project_opened()
    assert store.list() == []


def test_a_record_left_running_is_marked_interrupted_when_the_host_forgets_it(
    session: Session, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**`interrupted` is the honest answer.** A `running` record that will never change is
    worse than
    one that says what happened; the agent may have restarted."""
    from stokes.run.hosts import HostRegistry

    record = make_run(store, state="running", job="j_gone", host="local")
    runner = FakeRunner(state="running")
    runner.call = lambda method, timeout=60.0, **params: (  # type: ignore[method-assign]
        (_ for _ in ()).throw(RuntimeError("no such job"))
        if method == "job.status"
        else {"job": "j_gone"}
    )
    monkeypatch.setattr(
        HostRegistry,
        "of",
        classmethod(lambda cls, s: type("H", (), {"runner": lambda self, name: runner})()),
    )
    store._reattach()
    assert store.record(record.id).state == "interrupted"


def test_a_record_left_running_is_COLLECTED_when_the_job_says_done(
    session: Session, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus item 10: re-attach set the state and stopped, and this test used to lock that in.**

    It asserted `state == "done"` — which the incomplete version satisfied. What a re-opened project
    needs is what a LIVE run would have collected: the converged split, the history and the
    monitors. Setting only the state left `reusable()` handing the run to the next solve as
    though it had been collected, with `converged` False and no residuals to draw.

    A re-attach collects on a worker thread, so this waits for it: the assertions are about what the
    run ends up holding, not about the call returning.
    """
    from stokes.run.hosts import HostRegistry

    # **The engine is set, as a real run's is.** Without it `executor_for` returns None and the
    # re-attach falls back to setting the state alone - correct for a record that names no engine,
    # and the reason this test would otherwise pass while proving nothing.
    record = make_run(
        store, state="running", job="j_done", host="local", engine="of14.incompressibleFluid"
    )
    runner = FakeRunner(state="done")
    runner.call = _collecting_call(runner)
    monkeypatch.setattr(
        HostRegistry,
        "of",
        classmethod(lambda cls, s: type("H", (), {"runner": lambda self, name: runner})()),
    )
    store._reattach()
    _wait_for_final(store, record.id)
    # **The state AND the collection.** A `done` job carries a report; a re-attach that only set the
    # state would leave these empty.
    got = store.record(record.id)
    assert got.state == "done"
    assert got.converged is True, "the converged split was not read on re-attach"
    assert got.iterations == 12, "the report was not read on re-attach"


def test_a_finished_record_is_not_reattached(
    session: Session, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a run that was going needs an answer; a finished one already has it."""
    from stokes.run.hosts import HostRegistry

    called: list[str] = []
    monkeypatch.setattr(
        HostRegistry,
        "of",
        classmethod(
            lambda cls, s: type("H", (), {"runner": lambda self, name: called.append(name)})()
        ),
    )
    make_run(store, state="done", job="j_x", host="local")
    store._reattach()
    assert called == []


def test_clear_removes_the_record_and_its_folder(session: Session, store: RunStore) -> None:
    record = make_run(store)
    folder = runs_dir(session) / record.id
    assert folder.is_dir()
    assert store.clear(record.id) is True
    assert store.record(record.id) is None
    assert not folder.exists()


def test_clear_an_unknown_run_is_false(store: RunStore) -> None:
    """A well-formed id that is not there is False; **a malformed one is refused** (item 1).

    The id is `r_00000000`, not `r_nope`: `ID_RE` requires four hex digits, so `r_nope` is now a
    malformed id and raises rather than returning False. The distinction is the point —
    "unknown" and
    "not an id at all" are different answers, and the first is what this test is about.
    """
    assert store.clear("r_00000000") is False
    with pytest.raises(RunError):
        store.clear("r_nope")


def test_closing_forgets_everything(session: Session, store: RunStore) -> None:
    make_run(store)
    store.project_closed()
    assert store.list() == []
    assert store.current() is None


# ---------------------------------------------------------------- settling


def test_settle_returns_at_once_when_nothing_is_running(store: RunStore) -> None:
    """The ordinary case: a session hook is called after every record, and most records have
    no run."""
    started = time.monotonic()
    store.settle(None)
    assert time.monotonic() - started < 1.0


def test_settle_waits_for_a_running_run(store: RunStore) -> None:
    record = make_run(store, state="running")

    def finish() -> None:
        time.sleep(0.05)
        store.update(record.id, state="done")

    thread = threading.Thread(target=finish, daemon=True)
    thread.start()
    started = time.monotonic()
    store.settle(5.0)
    assert time.monotonic() - started >= 0.04, "settle returned before the run finished"
    assert store.record(record.id).state == "done"


def test_settle_gives_up_at_the_timeout(store: RunStore) -> None:
    make_run(store, state="running")
    started = time.monotonic()
    store.settle(0.1)
    assert time.monotonic() - started >= 0.09


def test_settle_ignores_coalesce_and_says_so(store: RunStore) -> None:
    """**A solve is not an edit.** `coalesce` is accepted because the session passes it to every
    extension, and ignored because there is nothing to coalesce — replaying a run away would
    be a replay
    that does not reproduce the project."""
    record = make_run(store, state="running")
    store.update(record.id, state="done")
    store.settle(1.0, coalesce=True)
    assert store.record(record.id).state == "done"


def test_hold_debounce_is_a_no_op(store: RunStore) -> None:
    with store.hold_debounce():
        pass


def test_a_run_id_that_is_not_one_cannot_write_or_delete_outside_runs(
    session: Session, store: RunStore, tmp_path: Path
) -> None:
    """**Opus review item 1: a run id becomes a path segment, so it is validated.**

    Measured before the fix: `store.add(RunRecord(id="../IMPORTANT"))` wrote `record.json` **into a
    sibling folder**, and `store.clear("../IMPORTANT")` then **deleted that whole directory** —
    including a file in it that had nothing to do with runs. Every caller of `_record_folder` writes
    a path, so the check is there and in `add`, not only in `run.start`.

    **`fullmatch`, not `match`**: `match` accepts a trailing newline, and `r_abc12345\\n` is not the
    id the rest of the system validates against.
    """
    # The session's project has no folder (it was created without a path), so a relative id escapes
    # from the SESSION SCRATCH folder - which is what makes the payload reach a real directory.
    # The victim is a SIBLING of the runs folder, wherever that is: for a project with no folder the
    # store uses a session scratch directory, which is not under `tmp_path` - so the path is derived
    # from the store rather than guessed.
    victim = store._record_folder("r_deadbeef").parent.parent / "IMPORTANT"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "keep.txt").write_text("do not delete me")

    for bad in ("../IMPORTANT", "/etc/passwd", "not-an-id", "r_abc12345\n", ""):
        with pytest.raises(RunError):
            store.add(RunRecord(id=bad, name="evil"))
    with pytest.raises(RunError):
        store.clear("../IMPORTANT")
    assert victim.is_dir(), "the clear deleted a folder outside runs/"
    assert (victim / "keep.txt").is_file(), "the victim's file was deleted"
    assert not (victim / "record.json").exists(), "a record was written outside runs/"


def test_a_record_whose_id_is_not_its_folder_is_skipped(
    session: Session, store: RunStore, tmp_path: Path
) -> None:
    """**The same hole on the LOAD path.** A crafted or shared project can put any id inside
    `record.json`, and `clear` deletes by that id — so a record claiming to be `"../IMPORTANT"`,
    sitting in `runs/r_deadbeef/`, would delete a sibling directory.

    The folder name is the fact; the field inside it is a claim.
    """
    folder = store._record_folder("r_deadbeef")
    folder.mkdir(parents=True)
    (folder / "record.json").write_text(
        json.dumps({"id": "../IMPORTANT", "name": "evil", "state": "done"}), encoding="utf-8"
    )
    victim = folder.parent.parent / "IMPORTANT"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "keep.txt").write_text("x")

    store.project_opened(session.require_project(), None)
    ids = [r["id"] for r in store.all_records()]
    assert "../IMPORTANT" not in ids, "a record claiming a foreign id was loaded"
    assert victim.is_dir() and (victim / "keep.txt").is_file()


def test_a_late_cancel_cannot_move_a_finished_run_back(store: RunStore) -> None:
    """**Opus review item 2: a final state is final.**

    `cancel_run` checks `found.final`, then makes a `job.cancel` call that can take **up to 30
    s**. A
    job that finishes inside that window had `done` overwritten with `cancelling`. After that the
    record never became final again — `running()` kept returning it, so **no new solve could
    start**,
    and `reusable()` ignored it. Measured before the fix.

    The invariant the module states is "only `finish` sets a final state"; this is the other half of
    it, enforced structurally rather than by every caller remembering.
    """
    # `make_run` builds its own id through `store.new_id()`, so the id is taken from the record it
    # returns rather than invented here.
    run_id = make_run(store, state="running", job="j_1").id
    store.finish(
        run_id,
        state="done",
        report={"converged": True, "iterations": 64},
        history=[],
        monitors={},
        status={"state": "done", "iterations": 64, "seconds": 3.0},
    )
    assert store.record(run_id).state == "done"

    # The late cancel lands, exactly as `cancel_run` does after its call returns.
    store.update(run_id, state="cancelling")
    record = store.record(run_id)
    assert record.state == "done", "a finished run was moved back to a non-final state"
    assert record.final is True
    assert store.running() is None, "a finished run still blocks a new solve"


def test_update_still_changes_other_fields_of_a_finished_run(store: RunStore) -> None:
    """**The guard blocks the STATE, not the record.** A finished run still gains a name, an error
    note or a monitor after the fact — refusing those would make `finish`'s own follow-up work
    impossible, and the bug this protects against is a state going backwards.
    """
    run_id = make_run(store, state="done").id
    store.update(run_id, name="renamed", error="a note")
    record = store.record(run_id)
    assert record.name == "renamed"
    assert record.error == "a note"
    assert record.state == "done"


def _collecting_call(runner: FakeRunner) -> Any:
    """A `call` that answers the three reads a re-attach collects, with recognisable numbers.

    `job.status` carries the report, `job.read` carries the residual history, and only a path built
    from the snapshot is asked for - so a re-attach that skips the collection asks none of them, and
    the assertions in the test can tell.
    """
    import json as _json

    def call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        runner.calls.append((method, params))
        if method == "job.status":
            return {
                "state": runner.state,
                "steps": [],
                "reports": {"solve": {"converged": True, "iterations": 12}},
            }
        if method == "job.read":
            return {"data": _json.dumps({"blocks": [{"time": 1.0, "Ux": 1e-6, "p": 1e-5}]})}
        if method == "job.log":
            return {"lines": []}
        return {}

    return call


def _wait_for_final(store: RunStore, run_id: str, timeout: float = 5.0) -> None:
    """Wait for a re-attached run to reach a final state. It collects on a worker thread."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = store.record(run_id)
        if record is not None and record.final:
            return
        time.sleep(0.02)


def test_opening_another_project_records_a_run_left_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus item 11: opening another project dropped an in-flight run without a trace.**

    `project_opened` cleared the records. `running()` then answered None, so **a second solve could
    start while the first job was still going on the host** - and nothing ever cancelled it.
    Measured: open a project holding a running run, open another, and `running()` is None with
    the job live.

    **`interrupted`, not `cancelled`.** The session is leaving the run, not stopping it: the
    host may be another machine, and a solve the user asked for is not ours to kill because
    they opened a different file. `interrupted` says what happened and leaves the job
    findable, and the re-attach path (item 10) picks it up when the project is opened again.

    **And it must land in the folder it belongs to.** `project_opened` runs AFTER the session's
    `project_path` has changed, so `runs_dir(session)` at that moment names the incoming project -
    measured: the record became `interrupted` in memory and was written into the OTHER project's
    folder, leaving the real one still saying `running`. That is what `RunRecord.origin` exists for.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(home))
    first, second = tmp_path / "a.stokes", tmp_path / "b.stokes"
    session = Session()
    session.execute("project.new", path=str(first), name="a")
    store = RunStore.of(session)
    store.bind(lambda name, data: None)
    record = store.add(
        RunRecord(id="r_00000011", name="duct", state="running", job="j_live", host="local")
    )
    assert store.running() is not None

    session.execute("project.new", path=str(second), name="b")

    # The new project can solve: the old run no longer blocks it.
    assert store.running() is None, "a run from the previous project still blocks a new solve"
    # **And the record says what happened, in ITS OWN folder.**
    written = json.loads((first / "runs" / record.id / "record.json").read_text())
    assert written["state"] == "interrupted", written
    assert "closed while this run was going" in str(written.get("error")), written
    assert not (second / "runs" / record.id).exists(), (
        "the abandoned run was written into the project the user just OPENED"
    )


def test_two_unsaved_projects_do_not_share_a_runs_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus item 12: the scratch folder is kept on the SESSION, so it was shared.**

    `runs_dir`'s own docstring promised *"the scratch folder is created per store, not
    shared, because two unsaved projects in one process must not see each other's runs"* -
    and it did not deliver it.
    Measured: a second unsaved project listed `['r_00000001']`, a run from the project the user had
    already left.

    Separate folders are the promise; the two projects must not see each other's runs, which is what
    is asserted rather than the path alone.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="first")
    store = RunStore.of(session)
    store.bind(lambda name, data: None)
    store.add(RunRecord(id="r_00000012", name="in-first", state="done"))
    first = runs_dir(session)

    session.execute("project.new", name="second")
    second = runs_dir(session)
    assert first != second, "two unsaved projects share one runs folder"
    assert list(second.iterdir()) == [], (
        f"the second project sees the first project's runs: {[p.name for p in second.iterdir()]}"
    )
    # And the first project's records are not lost by the move - they are still on disk where they
    # were, which is what a saved project's folder is for.
    assert (first / "r_00000012" / "record.json").is_file()


# **REMOVED: `test_saving_a_project_discards_its_scratch_runs_folder`.**
#
# It asserted that the per-session runs scratch folder was deleted after a save - a fix for the
# scratch LEAKING, which is a different problem from the one the scratch caused. **The scratch is
# gone** (claude-win2, reading 1, binding), so there is no folder to discard and nothing left to
# assert. Its coverage is not lost but replaced: the runs tests assert that records are written
# INSIDE the project, which is where the scratch used to be copied to.
def test_the_fields_cache_maps_the_file_and_maps_it_once(
    session: Session, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus item 14: the cache read the WHOLE artifact, and read it twice on concurrent misses.**

    `path.read_bytes()` materialised up to the 2 GiB download cap per artifact, and
    `FIELD_LRU` is 2 - a worst case of 4 GiB, which the process cannot afford. And the read
    happened OUTSIDE the lock (measured by indentation: the read at indent 8, the nearest
    `with self._lock` also at indent 8, so the read is not inside it), so two concurrent
    requests for one run both read the file.

    **What is asserted is the mechanism, not a memory figure**: the view is backed by an
    `mmap`, and a second load of the same file does not map it again. A test that measured
    RSS would be slow and flaky; the mapping and the loader count are the properties that
    make the memory bounded.
    """
    import stokes.run.store as store_module

    record = make_run(store, state="done", job="j_fields")
    path = store.fields_path(record.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A real artifact, built the way the agent writes one.
    sys.path.insert(0, str(Path(__file__).parent))
    from test_run_fields import buffer as field_buffer

    raw, _ = field_buffer()
    path.write_bytes(raw)

    maps: list[str] = []
    real_mmap = store_module.mmap.mmap
    # **The type is captured BEFORE the patch**: `isinstance(view.obj, mmap.mmap)` with the
    # attribute replaced by a function raises `TypeError: arg 2 must be a type`, which is what
    # my first version did.
    mmap_type = store_module.mmap.mmap

    def counting(*args: Any, **kwargs: Any) -> Any:
        maps.append("map")
        return real_mmap(*args, **kwargs)

    monkeypatch.setattr(store_module.mmap, "mmap", counting)
    view, header = store._load_fields(record.id)

    assert header["patches"], "the artifact did not read back"
    assert isinstance(view.obj, mmap_type), (
        "the file was read into memory rather than mapped; a 2 GiB artifact is 2 GiB of RSS"
    )
    assert len(maps) == 1

    # **A second load is served from the cache** - so concurrent asks do not each read the file.
    store._load_fields(record.id)
    assert len(maps) == 1, "the file was mapped again for a run already in the cache"


def test_a_reopened_project_lists_its_runs_NEWEST_FIRST(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**G-301 step 17**: `run.list` promises "newest first", and after a reopen it listed by ID.

    The Results panel takes `runs[0]` as the run to show, so the defect was visible: measured on a
    two-run project, the panel named `r_9e4f89ea` - the FIRST run - while the second sat beside it.

    **The cause was a field nobody wrote**: `RunRecord.created` existed, `project_opened`
    sorted on it, and every record's `created` was `""`, so the sort compared equal strings
    and the order fell back to the alphabetical folder walk. Ids are `r_<hex>`, so
    "alphabetical" is effectively random.
    The ids here are chosen so that alphabetical order is the WRONG answer: `r_0000` sorts before
    `r_ffff` but is created AFTER it.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj"
    first = Session()
    first.execute("project.new", path=str(folder), name="duct")
    store = RunStore.of(first)
    older = store.add(RunRecord(id="r_0000beef", name="duct", state="done"))
    newer = store.add(RunRecord(id="r_ffffffff", name="duct", state="done"))
    # **`created` is stamped by the store**, on the first write, so the two differ by a real time.
    assert older.created != "", "the store must stamp `created` on a new record"
    assert newer.created != ""
    first.execute("project.save")

    second = Session()
    second.execute("project.open", path=str(folder))
    ids = [item["id"] for item in second.execute("run.list")["runs"]]
    assert ids == ["r_ffffffff", "r_0000beef"], f"expected newest first, got {ids}"


def test_a_reopened_finished_run_reads_its_history_back_from_the_host(
    store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A reload lost the chart.** The history lived in memory only, so after `project_opened` a
    finished run answered no blocks and the panel said its history was unavailable. The first
    `run.residuals` now reads the agent's file on a worker thread and says `loading` meanwhile."""
    from stokes.run.hosts import HostRegistry

    record = make_run(store, state="done", job="j_" + "c" * 12, host="local")
    lines = "\n".join(
        json.dumps({"iteration": i, "time": float(i), "residuals": {"e": 0.1 / i}})
        for i in (1, 2, 3)
    )
    runner = FakeRunner()
    # The host answers only once the test has seen the first reply, so `loading` is observable.
    release = threading.Event()

    def call(method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method != "job.read":
            return {}
        release.wait(5.0)
        return {"text": lines}

    runner.call = call  # type: ignore[method-assign]
    monkeypatch.setattr(
        HostRegistry,
        "of",
        classmethod(lambda cls, s: type("H", (), {"runner": lambda self, name: runner})()),
    )
    first = store.residuals(record.id)
    assert first["iterations"] == [] and first["loading"] is True
    release.set()
    deadline = time.monotonic() + 5.0
    found = store.residuals(record.id)
    while found["loading"] and time.monotonic() < deadline:
        time.sleep(0.01)
        found = store.residuals(record.id)
    assert found["loading"] is False
    assert found["iterations"] == [1, 2, 3]
    assert found["fields"]["e"] == pytest.approx([0.1, 0.05, 0.1 / 3])


def test_a_run_without_a_job_never_says_loading(store: RunStore) -> None:
    record = make_run(store, state="done")
    assert store.residuals(record.id)["loading"] is False


def test_a_record_left_queued_without_a_job_is_not_starting_for_ever(store: RunStore) -> None:
    """A start refused before a job existed left the record `queued` with no job, and the project
    reopened on "Run · starting" with a Stop button. With no job there is no host to ask, so it is
    `interrupted`, keeping any error it carried; nothing is asked of a host."""
    record = make_run(store, state="queued", job="")
    store._reattach()
    found = store.record(record.id)
    assert found is not None and found.state == "interrupted"
    assert found.error == "the run never started on its host"
    assert store.running() is None


def test_a_running_step_moves_a_queued_run_to_running(store: RunStore) -> None:
    """Every job.state the agent sends while a step runs names that step; the elbow's record stayed
    `queued`, and the strip said "Run · starting", through 1,400 iterations. A finished step does
    not end the run."""
    record = make_run(store, state="queued", job="j_mine")
    store.runner_event(
        record.id, "job.state", {"job": "j_mine", "state": "running", "step": "solve"}
    )
    assert store.record(record.id).state == "running"
    store.runner_event(record.id, "job.state", {"job": "j_mine", "state": "done", "step": "solve"})
    assert store.record(record.id).state == "running"
