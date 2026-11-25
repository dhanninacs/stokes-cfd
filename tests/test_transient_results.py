"""A transient run's results (ADR-0022 addendum 2026-11-24): its record, the openings' values at a
written time and their sign, the write interval and time rules of `solve.configure`, and the field
ranges over every written time."""

from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("STOKES_HOME", tempfile.mkdtemp())
os.environ.setdefault("STOKES_AGENT_HOME", tempfile.mkdtemp())

from stokes.api import Session
from stokes.api.commands import results_streams
from stokes.run import time_ranges, write_cadence
from stokes.run.errors import SolveInvalid
from stokes.run.records import RunRecord
from stokes.run.store import RunStore

FLOW_IN = "patchFlowRate(patch=inlet)/0/surfaceFieldValue.dat"
FLOW_OUT = "patchFlowRate(patch=outlet)/0/surfaceFieldValue.dat"
MEAN_IN = "patchAverage(patch=inlet,fields=(p))/0/surfaceFieldValue.dat"
MEAN_OUT_T = "fluid/patchAverage(patch=outlet,fields=(pT),region=fluid)/0/surfaceFieldValue.dat"


def _table(header: str, rows: list[tuple[float, ...]]) -> str:
    body = "\n".join("\t".join(f"{v:g}" for v in row) for row in rows)
    return f"# Surface field value\n# {header}\n{body}\n"


# ---------------------------------------------------------------- the sign of a flow


def test_an_inlet_s_flow_is_positive_into_the_domain_and_an_outlet_s_out_of_it() -> None:
    assert results_streams.directed_flow(-4e-4, "inlet") == pytest.approx(4e-4)
    assert results_streams.directed_flow(4e-4, "outlet") == pytest.approx(4e-4)
    # Backflow through an inlet shows as a negative flow in.
    assert results_streams.directed_flow(1e-5, "inlet") == pytest.approx(-1e-5)


def test_only_an_inlet_s_flow_table_is_drawn_with_its_sign_turned() -> None:
    openings = {"inlet": "inlet", "outlet": "outlet"}
    assert results_streams.monitor_sign(FLOW_IN, openings) == -1
    assert results_streams.monitor_sign(FLOW_OUT, openings) == 1
    assert results_streams.monitor_sign(MEAN_IN, openings) == 1
    assert results_streams.monitor_patch(MEAN_OUT_T) == "outlet"


def test_a_stream_s_flow_is_what_comes_in_and_backflow_is_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(results_streams, "_specific_heat", lambda ctx, material, t: None)
    stream = results_streams._stream
    forward = {"in": {"flow_rate": -4e-4, "p_mean": 2.0}, "out": {"flow_rate": 4e-4, "p_mean": 0}}
    found = stream(None, "fluid", "water", ["in"], ["out"], forward, False)  # type: ignore[arg-type]
    assert found["mass_flow"] == pytest.approx(4e-4)
    assert found["mass_flow_out"] == pytest.approx(4e-4)
    back = {"in": {"flow_rate": 1e-5, "p_mean": 0.0}, "out": {"flow_rate": -1e-5, "p_mean": 0}}
    found = stream(None, "fluid", "water", ["in"], ["out"], back, False)  # type: ignore[arg-type]
    assert found["mass_flow"] == pytest.approx(-1e-5)


# ---------------------------------------------------------------- the openings at a time


def test_the_monitor_tables_are_joined_by_patch_and_read_at_the_nearest_written_time() -> None:
    texts = {
        FLOW_IN: _table("Time sum(phi)", [(0.001, -1.0), (0.002, -2.0), (0.003, -3.0)]),
        MEAN_IN: _table("Time areaAverage(p)", [(0.001, 10.0), (0.002, 20.0), (0.003, 30.0)]),
        MEAN_OUT_T: _table("Time p T", [(0.001, 1.0, 300.0), (0.003, 3.0, 310.0)]),
        "probes/0/U": "ignored",
    }
    tables = results_streams.tables_from(texts)
    assert set(tables) == {"inlet", "outlet"}
    assert tables["outlet"]["T_area"] == [(0.001, 300.0), (0.003, 310.0)]
    sampled, values = results_streams.values_at(tables, 0.0021)
    assert values["inlet"] == {"flow_rate": -2.0, "p_mean": 20.0}
    # The outlet wrote at 0.001 and 0.003 only: its nearest row is 0.003, and that is reported.
    assert values["outlet"] == {"p_mean": 3.0, "T_area": 310.0}
    assert sampled == pytest.approx(0.003)


def test_a_resumed_run_s_tables_in_two_time_folders_are_one_history() -> None:
    texts = {
        FLOW_IN: _table("Time sum(phi)", [(0.001, -1.0), (0.002, -2.0)]),
        FLOW_IN.replace("/0/", "/0.002/"): _table("Time sum(phi)", [(0.002, -2.5), (0.003, -3.0)]),
    }
    rows = results_streams.tables_from(texts)["inlet"]["flow_rate"]
    assert rows == [(0.001, -1.0), (0.002, -2.5), (0.003, -3.0)]


class _Host:
    """A host whose job wrote the four monitor tables of a duct."""

    def __init__(self) -> None:
        self.reads = 0

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "job.outputs":
            return {
                "files": [{"path": f"postProcessing/{n}", "size": 10} for n in (FLOW_IN, MEAN_IN)]
            }
        if method == "job.read":
            self.reads += 1
            name = str(params["path"]).removeprefix("postProcessing/")
            rows = [(0.1, -1e-4), (0.2, -2e-4)] if name == FLOW_IN else [(0.1, 5.0), (0.2, 7.0)]
            return {"text": _table("Time v", rows)}
        return {}


def test_run_monitors_at_reads_the_tables_once_and_gives_the_flow_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stokes.api.commands import run as run_commands

    session = Session()
    session.execute("project.new", name="duct")
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), name="duct", state="done", job="j_" + "a" * 12))
    host = _Host()
    monkeypatch.setattr(run_commands, "_run_host", lambda ctx, run: (store.record(record.id), host))
    monkeypatch.setattr(results_streams, "opening_of", lambda project: {"inlet": "inlet"})
    found = session.execute("run.monitors_at", run=record.id, time=0.11)
    assert found["sampled"] == pytest.approx(0.1)
    assert found["monitors"]["inlet"]["flow"] == pytest.approx(1e-4)
    assert found["monitors"]["inlet"]["p_mean"] == pytest.approx(5.0)
    reads = host.reads
    session.execute("run.monitors_at", run=record.id, time=0.2)
    assert host.reads == reads  # a finished run's tables are kept


# ---------------------------------------------------------------- the record of a transient run


def test_a_transient_run_that_reached_its_end_time_is_done_with_the_time_it_simulated() -> None:
    session = Session()
    session.execute("project.new", name="duct")
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), state="running", transient=True, end_time=0.4))
    history = [{"iteration": n, "time": n * 0.001, "residuals": {"p": 1e-3}} for n in (399, 400)]
    store.finish(
        record.id,
        state="done",
        report={"iterations": 400},
        history=history,
        monitors={},
        status={},
    )
    finished = store.record(record.id)
    assert finished is not None
    assert finished.state == "done" and not finished.converged
    assert finished.sim_time == pytest.approx(0.4)


def test_a_transient_run_that_stopped_early_is_not_done() -> None:
    session = Session()
    session.execute("project.new", name="duct")
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), state="running", transient=True, end_time=0.4))
    history = [{"iteration": 100, "time": 0.1, "residuals": {"p": 1e-3}}]
    store.finish(record.id, state="done", report={}, history=history, monitors={}, status={})
    finished = store.record(record.id)
    assert finished is not None and finished.state == "done_unconverged"


# ---------------------------------------------------------------- how often it writes


def test_the_suggestion_aims_at_twenty_frames_a_period_and_fifty_a_run() -> None:
    # 0.4 s of a 5 Hz profile at 1 ms: 50 over the run (8 ms) beats 20 a period (10 ms).
    assert write_cadence.suggest_write_time(0.4, 0.001, [0.2]) == pytest.approx(0.008)
    # A fast profile: 20 a period wins.
    assert write_cadence.suggest_write_time(0.1, 0.0001, [0.01]) == pytest.approx(0.0005)
    # Never shorter than a step, and a whole number of steps.
    assert write_cadence.suggest_write_time(0.01, 0.001, []) == pytest.approx(0.001)
    assert write_cadence.suggest_write_time(1.0, 0.003, []) == pytest.approx(0.021)
    # At most MAX_FRAMES over a long run of a short period.
    assert write_cadence.suggest_write_time(100.0, 0.001, [0.01]) == pytest.approx(0.2)
    assert write_cadence.frames(0.4, 0.008) == 50


def _transient_session() -> Session:
    session = Session()
    session.execute("project.new", name="duct")
    session.execute("solve.configure", values={"end_time": 0.4, "delta_t": 0.001})
    return session


def _params(session: Session) -> Any:
    node = session.require_project().find("/solver/settings")
    assert node is not None
    return node.params


def test_switching_to_transient_writes_the_suggested_interval() -> None:
    session = _transient_session()
    session.execute("solve.configure", values={"transient": True})
    assert write_cadence.seconds(_params(session)["write_time"]) == pytest.approx(0.008)
    # Still the suggestion: a longer run moves it.
    session.execute("solve.configure", values={"end_time": 2.0})
    assert write_cadence.seconds(_params(session)["write_time"]) == pytest.approx(0.04)


def test_an_interval_the_user_typed_is_kept_when_the_run_changes() -> None:
    session = _transient_session()
    session.execute("solve.configure", values={"transient": True})
    session.execute("solve.configure", values={"write_time": "5 [ms]"})
    session.execute("solve.configure", values={"end_time": 2.0})
    assert write_cadence.seconds(_params(session)["write_time"]) == pytest.approx(0.005)


def test_a_time_step_longer_than_the_run_is_refused() -> None:
    session = _transient_session()
    session.execute("solve.configure", values={"transient": True})
    with pytest.raises(SolveInvalid, match="longer than the end time"):
        session.execute("solve.configure", values={"delta_t": 1.0})
    with pytest.raises(SolveInvalid, match="longer than the end time"):
        session.execute("solve.configure", values={"end_time": "0.5 [ms]"})
    assert write_cadence.seconds(_params(session)["delta_t"]) == pytest.approx(0.001)


def test_a_write_interval_shorter_than_a_step_or_longer_than_the_run_is_refused() -> None:
    session = _transient_session()
    session.execute("solve.configure", values={"transient": True})
    with pytest.raises(SolveInvalid, match="shorter than one time step"):
        session.execute("solve.configure", values={"write_time": 0.0001})
    with pytest.raises(SolveInvalid, match="longer than the run"):
        session.execute("solve.configure", values={"write_time": 1.0})


def test_a_steady_run_is_not_held_to_the_transient_rules() -> None:
    session = _transient_session()
    session.execute("solve.configure", values={"delta_t": 1.0})
    assert write_cadence.seconds(_params(session)["write_time"] or 0.0) in (0.0, None)


def test_the_write_suggestion_says_what_the_settings_write_now() -> None:
    session = _transient_session()
    found = session.execute("solve.write_suggestion")
    assert found["write_time"] == pytest.approx(0.008)
    assert found["period"] is None
    # Unset: 50 steps of 1 ms.
    assert found["current"] == {"write_time": pytest.approx(0.05), "frames": 8, "steps": True}


# ---------------------------------------------------------------- the ranges over every time


def test_the_times_read_leave_out_the_initial_condition_and_are_thinned() -> None:
    assert time_ranges.times_to_read([0.0, 0.1, 0.2]) == [0.1, 0.2]
    assert time_ranges.times_to_read([0.0]) == [0.0]
    many = time_ranges.times_to_read([i * 0.001 for i in range(1001)])
    assert len(many) == time_ranges.MAX_TIMES and many[0] == 0.001 and many[-1] == 1.0


def test_the_spans_of_every_time_are_widened_into_one() -> None:
    spans = time_ranges.union({}, {"p": (-1.0, 2.0)})
    spans = time_ranges.union(spans, {"p": (-3.0, 1.0), "U": (0.0, 1.0)})
    assert spans == {"p": (-3.0, 2.0), "U": (0.0, 1.0)}


def test_run_fields_reports_the_all_times_range_once_it_is_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    store: Any = SimpleNamespace(fields_path=lambda run_id: tmp_path / f"{run_id}.stkfld")
    run_id = "r_" + "b" * 12
    assert time_ranges.read_cached(store, run_id) is None
    (tmp_path / f"{run_id}.ranges.json").write_text(
        json.dumps({"times": [0.1, 0.2], "ranges": {"p": [-3, 2]}, "percentiles": {"p": [-2, 1]}}),
        encoding="utf-8",
    )
    cached = time_ranges.read_cached(store, run_id)
    assert cached is not None
    assert time_ranges.field_entry(cached, "p") == {
        "min": -3.0,
        "max": 2.0,
        "times": 2,
        "p01": -2.0,
        "p99": 1.0,
    }
    assert time_ranges.field_entry(cached, "U") is None


def test_working_out_the_ranges_extracts_every_time_and_writes_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.execute("project.new", name="duct")
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), state="done", job="j_" + "c" * 12))
    asked: list[float] = []
    headers = {
        0.1: {"patches": [{"fields": {"p": {"min": -1.0, "max": 1.0, "p01": -0.5, "p99": 0.5}}}]},
        0.2: {"patches": [{"fields": {"p": {"min": -4.0, "max": 0.5, "p01": -3.0, "p99": 0.2}}}]},
    }

    class Runner:
        def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
            asked.append(float(params["time"]))
            return {"artifact": "fields.stkfld", "sha256": "x", "size": 1}

    from stokes.run import artifacts, hosts

    monkeypatch.setattr(hosts.HostRegistry, "runner", lambda self, name: Runner())
    monkeypatch.setattr(
        artifacts, "fetch_artifact", lambda r, j, n, target, s, z: target.write_bytes(b"x")
    )
    monkeypatch.setattr(time_ranges, "_header_of", lambda path: headers[asked[-1]])
    events: list[str] = []
    monkeypatch.setattr(store, "emit_event", lambda name, data: events.append(name))
    time_ranges._work(store, record.id, [0.1, 0.2])
    assert asked == [0.1, 0.2]
    assert events == ["run.ranges"]
    cached = time_ranges.read_cached(store, record.id)
    assert cached is not None
    assert cached["ranges"] == {"p": [-4.0, 1.0]}
    assert cached["percentiles"] == {"p": [-3.0, 0.5]}
    assert time_ranges.status(store, record.id) == "ready"
    assert time_ranges.start(store, record.id, [0.1, 0.2]) == "ready"
    assert time_ranges.start(store, "r_" + "d" * 12, [0.1]) == "none"


def test_the_range_worker_uses_a_scratch_file_of_its_own_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two Stokes processes on one project both work the ranges out; a scratch name shared by the
    # run made one fail with WinError 32 while the other held the file (2026-11-25).
    session = Session()
    session.execute("project.new", name="duct")
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), state="done", job="j_" + "c" * 12))
    written: list[str] = []

    class Runner:
        def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
            return {"artifact": "fields.stkfld", "sha256": "x", "size": 1}

    from stokes.run import artifacts, hosts

    monkeypatch.setattr(hosts.HostRegistry, "runner", lambda self, name: Runner())

    def fetch(r: Any, j: Any, n: Any, target: Any, s: Any, z: Any) -> None:
        written.append(target.name)
        target.write_bytes(b"x")

    monkeypatch.setattr(artifacts, "fetch_artifact", fetch)
    monkeypatch.setattr(time_ranges, "_header_of", lambda path: {"patches": []})
    time_ranges._work(store, record.id, [0.1, 0.2])
    assert written and all(f".{os.getpid()}." in name for name in written)


def test_a_fields_swap_refused_by_another_handle_is_retried_then_explained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Windows refuses to replace a file another handle holds (a second Stokes process mapping it);
    # one refusal used to leave the time slider unable to move, with a bare WinError 5.
    from pathlib import Path

    from stokes.run import artifacts, hosts
    from stokes.run import store as store_module

    session = Session()
    session.execute("project.new", name="duct")
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), state="done", job="j_" + "c" * 12))

    class Runner:
        def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
            return {"artifact": "fields.stkfld", "sha256": "x", "size": 1}

    monkeypatch.setattr(hosts.HostRegistry, "runner", lambda self, name: Runner())

    def fetch(r: Any, j: Any, n: Any, target: Any, s: Any, z: Any) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")

    monkeypatch.setattr(artifacts, "fetch_artifact", fetch)
    tries: list[str] = []
    real_replace = Path.replace

    def refuse(self: Path, target: Any) -> Any:
        if self.name.endswith(".stkfld.next"):
            tries.append(self.name)
            raise PermissionError(5, "Access is denied")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", refuse)
    monkeypatch.setattr(store_module.time, "sleep", lambda seconds: None)
    error = store.fetch_fields(record.id, time=0.2)
    assert len(tries) == store_module.FIELDS_SWAP_TRIES
    assert "held open by another program" in error
    assert (
        not store.fields_path(record.id)
        .with_name(store.fields_path(record.id).name + ".next")
        .exists()
    )


def test_the_bulk_temperature_tables_are_read_at_a_time_too() -> None:
    texts = {
        "bulkT_outlet/0/surfaceFieldValue.dat": _table("Time weightedAverage(T)", [(0.001, 305.0)]),
        "hot/bulkT_hot_in/0/surfaceFieldValue.dat": _table("Time T", [(0.001, 350.0)]),
    }
    tables = results_streams.tables_from(texts)
    assert tables["outlet"]["T_bulk"] == [(0.001, 305.0)]
    assert tables["hot_in"]["T_bulk"] == [(0.001, 350.0)]
