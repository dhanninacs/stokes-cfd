"""The `run.*` commands and the executor (T-214b), against a fake runner.

**A fake runner, no OpenFOAM.** `tests/engine/test_run_engine.py` drives the real chain; this
covers the
paths a real solve takes only once — a host that cannot solve, a result that is reusable, a
cancel, a
report that says the run did not converge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.run.errors import RunNotFound, SolveInvalid
from stokes.run.records import RunRecord
from stokes.run.store import RunStore

#: Stable ids for the fixture, so a test can name them.
PART_ID = "n_aaaaaaaa"
TAG_ID = "n_bbbbbbbb"
REGION_ID = "n_cccccccc"


class FakeRunner:
    """Records its calls, and answers the four the run path makes."""

    name = "local"

    def __init__(
        self, *, available: bool = True, install: bool = True, state: str = "done"
    ) -> None:
        self.available = available
        self.why = "" if available else "No OpenFOAM host on this machine"
        self.install = install
        self.state = state
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.listeners: list[Any] = []
        self.job = "j_" + "c" * 12
        #: The parser's report, as `job.status` returns it under `reports["solve"]`.
        self.report: dict[str, Any] = {
            "iterations": 52,
            "converged": True,
            "last_residuals": {"Ux": 1e-6},
        }
        #: The residual history file, as `job.read` returns it.
        self.history: list[str] = []
        #: The monitor files, keyed by path.
        self.files: dict[str, str] = {}
        self.error: dict[str, Any] | None = None

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        self.calls.append((method, params))
        if method == "host.probe":
            return {
                "installs": (
                    [
                        {
                            "id": "e_000000000001",
                            "bashrc": "/x/env.sh",
                            "version": "14",
                            "flavour": "org",
                        }
                    ]
                    if self.install
                    else []
                ),
                "default_install": "/x/env.sh" if self.install else None,
                "default_install_id": "e_000000000001" if self.install else None,
            }
        if method == "job.create":
            return {"job": self.job}
        if method == "job.status":
            return {
                "state": self.state,
                "error": self.error,
                "started": 100.0,
                "ended": 102.5,
                "reports": {"solve": self.report},
            }
        if method == "job.read":
            path = str(params.get("path") or "")
            if path.endswith("residuals.jsonl"):
                return {"text": "\n".join(self.history), "truncated": False}
            return {"text": self.files.get(path, ""), "truncated": False}
        if method == "log.tail":
            return {"lines": ["Time = 1s"], "truncated": False}
        return {}

    def subscribe(self, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener) if listener in self.listeners else None

    def close(self) -> None:
        return


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    made = Session()
    made.execute("project.new", name="duct")
    return made


def install_fake(session: Session, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the host registry at the fake runner, keeping `probe`'s real wrapper shape."""
    from stokes.run.hosts import HostRegistry

    class Registry:
        def runner(self, name: str) -> Any:
            return fake

        def hosts(self) -> list[dict[str, Any]]:
            return []

        def probe(self, name: str, refresh: bool = False) -> dict[str, Any]:
            # **The wrapper, with the installs inside `record`** — that nesting is what a real probe
            # returns, and reading the top level instead made every run come out `skipped`.
            return {
                "available": fake.available,
                "host": name,
                "record": fake.call("host.probe"),
                "why": fake.why,
            }

    monkeypatch.setattr(HostRegistry, "of", classmethod(lambda cls, s: Registry()))


def a_mesh(session: Session) -> None:
    """A finished mesh result, so the snapshot has something to build on.

    **`project.new` already makes `/mesh`, `/solver`, `/monitors` and `/runs`** — adding them again
    raised `ModelError: / already has a child named 'mesh'`. Only the result and its meta are
    needed.
    """
    # **The result is a DIRECT child of `/mesh/results`.** `MeshService._result_node` takes
    # the first
    # child of that group, so an intermediate `blockmesh` group was itself returned as "the
    # result" and
    # its `state` param read as `None` — the mesh looked unfinished to the snapshot.
    session.project.add("/mesh", "group", "results")
    session.project.add(
        "/mesh/results",
        "mesh_result",
        "m",
        # **`state` on the NODE is what `MeshService.status()` reads** — it looks the result
        # up in the
        # tree and reports its params, so a node without this reports `None` and the snapshot
        # refuses
        # with `the mesh is 'None'`.
        {"state": "done"},
        node_id="n_mesh",
    )
    node = session.project.find("n_mesh")
    node.meta["patch_types"] = {"inlet": "patch", "outlet": "patch", "walls": "wall"}
    node.meta["patch_tags"] = {"inlet": "n_t1", "outlet": "n_t2", "walls": "n_t3"}
    # **A region whose tag holds a part, and a material on it.** The snapshot needs `nu`, and
    # it finds it
    # by asking which region's tag names the domain part (ADR-0022 decision 4.1) — so a bare
    # node is not
    # enough: the tag's `members` must name a real part id, which the tag schema validates.
    session.project.add("/geometry", "part", "duct", {"derived": ""}, node_id=PART_ID)
    session.project.add("/geometry", "group", "tags")
    tag = session.project.add(
        "/geometry/tags",
        "tag",
        "domain",
        {"members": json.dumps([{"part": PART_ID, "faces": "all"}])},
        node_id=TAG_ID,
    )
    material = session.execute("material.create", name="air", kind="fluid", template="gas")
    session.project.add(
        "/regions",
        "region",
        "fluid",
        {"tag": tag.id, "material": material["id"]},
        node_id=REGION_ID,
    )


# ---------------------------------------------------------------- the commands exist


def test_all_eighteen_run_commands_are_registered() -> None:
    """**The exact set**, so a new `run.*` command cannot land unregistered or unnoticed.

    Eleven since `run.report` added the results report for item 5: one self-contained HTML file
    whose facts are the API own answers, so it cannot disagree with the panel. Twelve since
    `run.iso_surface` (ADR-0026 decision 3) cuts an iso-surface of a finished run. Thirteen since
    `run.time` (T-310) shows a finished run's fields at another written time. Fifteen since
    `run.monitors` and `run.monitor` (T-311) offer the tables a run's function objects wrote.
    Sixteen since `run.vectors` samples the velocity through the solved volume (ADR-0030).
    Eighteen since `run.stop` (Stop writes the iteration it is on) and `run.resume` (continue it).
    **The count in the name is deliberate**: it is the thing a reader has to change when they add
    one, which is what makes this a second look rather than an edit to a set.
    """
    from stokes.api import default_registry

    names = {name for name in default_registry().names() if name.startswith("run.")}
    assert names == {
        "run.start",
        "run.cancel",
        "run.status",
        "run.list",
        "run.log",
        "run.residuals",
        "run.fields",
        "run.probe",
        "run.streamlines",
        "run.vectors",
        "run.report",
        "run.iso_surface",
        "run.time",
        "run.monitors",
        "run.monitor",
        "run.clear",
        "run.stop",
        "run.resume",
    }


def test_run_start_needs_settings_after_the_node_is_removed(session: Session) -> None:
    """**`solve.no_settings` still fires for a project whose node has gone.**

    G2 gives every project a `/solver/settings` from `project.new`, so this can no longer
    happen to a
    new one — but the refusal is still the right answer for a project that lost the node (an older
    file, or a user deleting it), and `_settings` is where it lives. Removed here rather than
    deleted
    from the test, so the path keeps its coverage.
    """
    from stokes.engines.model import SETTINGS_PATH

    node = session.require_project().find(SETTINGS_PATH)
    assert node is not None, "G2: the node exists from project.new"
    session.execute("node.remove", node=node.id)
    with pytest.raises(SolveInvalid) as caught:
        session.execute("run.start")
    assert caught.value.code == "solve.no_settings"


def test_run_list_is_empty_at_first(session: Session) -> None:
    assert session.execute("run.list") == {"runs": []}


def test_run_status_of_nothing_is_not_found(session: Session) -> None:
    with pytest.raises(RunNotFound):
        session.execute("run.status")


# ---------------------------------------------------------------- the host that cannot run


def test_a_host_without_openfoam_records_skipped(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**ADR-0024: recorded, not refused.** A replay on a machine without OpenFOAM must
    reproduce the
    same project instead of stopping at a step it cannot take."""
    fake = FakeRunner(available=False)
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    reply = session.execute("run.start")
    assert reply["state"] == "skipped"
    assert reply["job"] == ""
    found = session.project  # the record is in the store, not the tree
    del found
    run = RunStore.of(session).record(reply["run"])
    assert run is not None and run.state == "skipped"
    assert run.error.startswith("run.no_host")


def test_a_host_with_no_install_id_records_skipped(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The `record` nesting.** Reading `default_install_id` from the probe's top level
    returned None
    and made every run `skipped` on a host that meshes fine — found by running it on the real
    duct."""
    fake = FakeRunner(install=False)
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    assert session.execute("run.start")["state"] == "skipped"


# ---------------------------------------------------------------- finishing


def test_a_done_run_records_its_numbers(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRunner()
    fake.history = [
        json.dumps({"iteration": n, "time": n / 10, "residuals": {"Ux": 1.0 / n}})
        for n in (1, 2, 3)
    ]
    fake.report = {"iterations": 3, "converged": True, "last_residuals": {"Ux": 1e-6}}
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    reply = session.execute("run.start")
    store = RunStore.of(session)
    store.finish(
        reply["run"],
        state="done",
        report=fake.report,
        history=[json.loads(line) for line in fake.history],
        monitors={"inlet": {"flow_rate": -3e-05}},
        status={"started": 10.0, "ended": 12.5},
    )
    found = store.record(reply["run"])
    assert found.state == "done" and found.converged is True
    assert found.iterations == 3
    assert found.last_residuals == {"Ux": 1e-6}
    assert found.monitors["inlet"]["flow_rate"] == pytest.approx(-3e-05)
    assert found.timings["seconds"] == pytest.approx(2.5)


def test_a_run_that_did_not_converge_is_not_a_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**`done_unconverged`, not `done` and not `failed`.** Many real runs stop at the
    iteration limit
    with residuals still falling; reporting either extreme would be a lie."""
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    reply = session.execute("run.start")
    store = RunStore.of(session)
    store.finish(
        reply["run"],
        state="done",
        report={"iterations": 100, "converged": False, "last_residuals": {"Ux": 1e-3}},
        history=[],
        monitors={},
        status={},
    )
    found = store.record(reply["run"])
    assert found.state == "done_unconverged"
    assert found.final is True and found.successful is True


def test_a_failed_run_carries_the_error(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    reply = session.execute("run.start")
    store = RunStore.of(session)
    store.finish(
        reply["run"],
        state="failed",
        report={},
        history=[],
        monitors={},
        status={"error": {"code": "agent.step_failed", "message": "rc 1"}},
    )
    found = store.record(reply["run"])
    assert found.state == "failed"
    assert "agent.step_failed" in found.error


def test_the_history_from_the_file_replaces_the_events(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The file is complete, the event stream is a sample.** Reading it back is what makes a
    finished
    run's chart show the whole run."""
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    reply = session.execute("run.start")
    store = RunStore.of(session)
    store.append_residual(reply["run"], {"iteration": 1, "residuals": {"Ux": 9.0}})
    store.finish(
        reply["run"],
        state="done",
        report={"iterations": 3, "converged": True},
        history=[
            {"iteration": 1, "residuals": {"Ux": 1.0}},
            {"iteration": 2, "residuals": {"Ux": 0.5}},
            {"iteration": 3, "residuals": {"Ux": 0.1}},
        ],
        monitors={},
        status={},
    )
    found = store.residuals(reply["run"])
    assert found["iterations"] == [1, 2, 3]
    assert found["fields"]["Ux"] == pytest.approx([1.0, 0.5, 0.1]), "the file's values win"


# ---------------------------------------------------------------- reuse


def test_a_matching_finished_run_is_reused(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    first = session.execute("run.start")
    store = RunStore.of(session)
    store.finish(
        first["run"],
        state="done",
        report={"iterations": 3, "converged": True},
        history=[],
        monitors={},
        status={},
    )
    again = session.execute("run.start")
    assert again["reused"] is True
    assert again["run"] == first["run"], "the same case is the same run"


def test_a_new_rank_count_starts_a_new_run(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**ADR-0025.** A serial result must not satisfy a request for more ranks: the parallel case
    carries a decomposition dictionary, so changing only `cores` must start a new run rather than
    silently hand back the old one."""
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100, "cores": 1})
    first = session.execute("run.start")
    store = RunStore.of(session)
    store.finish(
        first["run"],
        state="done",
        report={"iterations": 3, "converged": True},
        history=[],
        monitors={},
        status={},
    )
    session.execute("solve.configure", values={"cores": 2})
    again = session.execute("run.start")
    assert again["reused"] is False
    assert again["run"] != first["run"]


def test_force_runs_again(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """`force=True` is how a user says "run it anyway"."""
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    first = session.execute("run.start")
    store = RunStore.of(session)
    store.finish(
        first["run"], state="done", report={"converged": True}, history=[], monitors={}, status={}
    )
    again = session.execute("run.start", force=True)
    assert again["reused"] is False
    assert again["run"] != first["run"]


def test_an_unconverged_run_is_reusable(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The case is the same case; a user who wants it again says `force`."""
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    first = session.execute("run.start")
    RunStore.of(session).finish(
        first["run"],
        state="done",
        report={"converged": False},
        history=[],
        monitors={},
        status={},
    )
    assert session.execute("run.start")["reused"] is True


def test_a_failed_run_is_not_reusable(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """**A failure must never be handed back as "already solved".**"""
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    first = session.execute("run.start")
    RunStore.of(session).finish(
        first["run"], state="failed", report={}, history=[], monitors={}, status={}
    )
    again = session.execute("run.start")
    assert again["reused"] is False and again["run"] != first["run"]


# ---------------------------------------------------------------- cancel, clear, log


def test_cancel_an_unknown_run_is_false(session: Session) -> None:
    assert session.execute("run.cancel", run="r_nope") == {"cancelled": False}


def test_cancel_marks_the_run(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    store = RunStore.of(session)
    store.add(RunRecord(id=store.new_id(), name="duct", state="running", job="j_x"))
    assert session.execute("run.cancel")["cancelled"] is True
    assert store.current().state == "cancelling"
    assert any(name == "job.cancel" for name, _ in fake.calls)


def test_clear_removes_the_run(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), name="duct", state="done"))
    assert session.execute("run.clear", run=record.id) == {"removed": record.id}
    assert store.record(record.id) is None


def test_clear_an_unknown_run_is_not_found(session: Session) -> None:
    with pytest.raises(RunNotFound):
        session.execute("run.clear", run="r_nope")


def test_log_is_empty_for_a_run_with_no_job(session: Session) -> None:
    store = RunStore.of(session)
    record = store.add(RunRecord(id=store.new_id(), name="duct"))
    assert session.execute("run.log", run=record.id) == {
        "step": "",
        "lines": [],
        "truncated": False,
    }


def test_log_refuses_a_silly_line_count(session: Session) -> None:
    with pytest.raises(SolveInvalid):
        session.execute("run.log", lines=99999)


def test_residuals_of_an_unknown_run_is_not_found(session: Session) -> None:
    with pytest.raises(RunNotFound):
        session.execute("run.residuals", run="r_nope")


def test_an_unconverged_run_is_asked_for_its_fields(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**An unconverged run must be asked for its fields, exactly as a converged one is.**

    `finish` asked only when the state was `done`, so a run that stopped at the iteration limit -
    which is what the demo pipe does by default - kept its solved case on the host and answered
    `run.fields` with `surfaces: "missing"`. Nothing could be plotted and nothing said why.

    Measured on the real demo pipe: 300 time steps, residuals down to 1e-4, artifacts on the
    host, and `record.fields == []`. Fetching by hand produced 114 kB containing `U`, `U_mag`,
    `p` and a slice - **the fields were there and nothing had asked for them.**

    The assertion is on the CALL, deliberately: what the fake returns for `extract.fields` does
    not matter, because the defect was never asking. A run that genuinely did not work must
    still not be asked, and that is the second half.
    """
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 10})
    reply = session.execute("run.start")
    store = RunStore.of(session)

    store.finish(
        reply["run"],
        state="done_unconverged",
        report={"iterations": 10, "converged": False},
        history=[],
        monitors={},
        status={},
    )
    assert "extract.fields" in [name for name, _ in fake.calls], (
        "an unconverged run was never asked for its fields, so it has nothing to plot"
    )


def test_a_failed_run_is_not_asked_for_its_fields(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The other side of it.** A failed run has no solved case, so asking would add a refused
    round trip to every failure - which is why the original condition existed at all."""
    fake = FakeRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 10})
    reply = session.execute("run.start")
    store = RunStore.of(session)

    store.finish(
        reply["run"],
        state="failed",
        report={},
        history=[],
        monitors={},
        status={},
    )
    assert "extract.fields" not in [name for name, _ in fake.calls], (
        "a failed run was asked for fields it cannot have"
    )


def test_run_order_key_falls_back_and_ties_deterministically() -> None:
    """The order a run's place in the list is decided by (Codex's 222732, item 4).

    **Measured on the audit's project**: its only run has `created = ""` and a real `updated`, and
    the store sorted on `created` alone - so an undated record sorted equal to nothing and came
    FIRST, in a list meant to be oldest-first.
    """
    from stokes.run.records import RunRecord
    from stokes.run.store import run_order_key

    def key(**kwargs: str) -> tuple[str, str]:
        return run_order_key(RunRecord(id=kwargs.pop("id", "r_1"), **kwargs))

    dated = key(id="r_a", created="2026-10-18T10:00:00+00:00")
    later = key(id="r_b", created="2026-10-18T11:00:00+00:00")
    assert dated < later, "a later run sorts after an earlier one"

    # **An unstamped `created` falls back to `updated`**, not to the front.
    updated_only = key(id="r_c", updated="2026-10-18T12:00:00+00:00")
    assert updated_only > later, "a run with only `updated` is placed by that time"

    # **The id breaks a tie**, so two runs in the same second keep a stable order.
    same = "2026-10-18T10:00:00+00:00"
    assert key(id="r_x", created=same) < key(id="r_y", created=same)

    # **Neither stamp sorts first, which is honest**: it cannot be placed later without inventing
    # a time.
    assert key(id="r_0") < dated


class RefusingRunner(FakeRunner):
    """An agent that refuses the staged case when the job is created (its dictionary lint)."""

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "job.create":
            from stokes.errors import StokesError

            raise StokesError(
                "system/controlDict:9: libs loads a shared library", code="agent.bad_params"
            )
        return super().call(method, timeout, **params)


def test_a_refused_start_records_a_failed_run_not_a_queued_one(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The elbow walkthrough reopened on "Run · starting" with a Stop button: a start the agent
    refused left its record `queued` for ever. It is `failed`, with the refusal, and a new run can
    start."""
    from stokes.errors import StokesError

    fake = RefusingRunner()
    install_fake(session, fake, monkeypatch)
    a_mesh(session)
    session.execute("solve.configure", values={"iterations": 100})
    with pytest.raises(StokesError):
        session.execute("run.start")
    store = RunStore.of(session)
    [row] = store.list()
    record = store.record(row["id"])
    assert record is not None
    assert record.state == "failed"
    assert "agent.bad_params" in record.error
    assert store.running() is None
