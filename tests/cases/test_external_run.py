"""Running an imported case (T-309): the plan, `run.start case=`, events and collection.

The host is a fake runner that records what `job.create` received, so these tests check what the
desktop SENDS; the agent's side of the `external` profile is `tests/test_agent_jobs_external.py`.
Every case is synthetic and written by the test (ADR-0029 decision 2).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.errors import StokesError

from .synthetic import GUARDS, HEADER, INVENTORY, make_case
from .test_import import _home, _probe

CONTROL = "application foamRun;\nsolver incompressibleFluid;\nendTime 0.5;\n"
ALLRUN = (
    GUARDS
    + "cp -r 0.orig 0\n"
    + "runApplication blockMesh\n"
    + "runApplication decomposePar\n"
    + "runParallel $(getApplication)\n"
    + "runApplication reconstructPar\n"
)
FILES = {
    "0.orig/U": HEADER + "internalField uniform (0 0 0);\nboundaryField\n{\n}\n",
    "README": "notes for people, not for a solver\n",
    "system/decomposeParDict": HEADER + "numberOfSubdomains 4;\nmethod simple;\n",
}


def test_staged_remove_uses_the_action_target() -> None:
    from stokes.run.external import _apply_action

    files = {"0/phi": b"old", "0/phiExtra": b"keep", "constant/polyMesh/points": b"mesh"}
    assert _apply_action(files, {"op": "remove", "target": "0/phi", "source": None})
    assert "0/phi" not in files and files["0/phiExtra"] == b"keep"
    assert _apply_action(files, {"op": "remove", "target": "constant/polyMesh"})
    assert "constant/polyMesh/points" not in files


class _ProbeOnly:
    """A runner that answers the probe and nothing else."""

    def __init__(self, record: Any) -> None:
        self.record = record

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        assert method == "host.probe"
        if isinstance(self.record, Exception):
            raise self.record
        return self.record


def test_a_windows_host_gets_the_constraint_and_model_libraries_named() -> None:
    """The tutorial sweep (2026-11-01): on FoamWin `squareBend` died with `Table of
    fvConstraints is empty` and ran once `libs ("libfvConstraints.dll")` was in the controlDict.
    Only a library the probe lists, only for a case that has the file needing it, only on Windows.
    """
    from stokes.run.external import CasePlan, add_windows_libs

    control = b"application foamRun;\nendTime 1;\n\n// ***** //\n"
    windows = {"system": "Windows", "libraries": ["fvConstraints"]}

    plan = CasePlan()
    plan.files = {
        "system/controlDict": control,
        "system/fvConstraints": b"limitp {}\n",
        "constant/fvModels": b"x {}\n",
    }
    add_windows_libs(plan, _ProbeOnly(windows))
    assert plan.files["system/controlDict"].endswith(b'libs ("libfvConstraints.dll");\n')
    assert b"fvModels" not in plan.files["system/controlDict"], "the install has no libfvModels"
    assert plan.adapted == ["libs added for the Windows build: libfvConstraints.dll"]

    merged = CasePlan()
    merged.files = {
        "system/controlDict": b'libs ("libother.dll");\nendTime 1;\n',
        "system/fvConstraints": b"x",
    }
    add_windows_libs(merged, _ProbeOnly(windows))
    assert merged.files["system/controlDict"].startswith(
        b'libs ("libother.dll" "libfvConstraints.dll");'
    )

    same = CasePlan()
    same.files = {"system/controlDict": control, "system/fvConstraints": b"x"}
    add_windows_libs(same, _ProbeOnly({"system": "Linux", "libraries": ["fvConstraints"]}))
    assert same.files["system/controlDict"] == control and same.adapted == []
    add_windows_libs(same, _ProbeOnly(RuntimeError("no agent")))
    assert same.files["system/controlDict"] == control
    none = CasePlan()
    none.files = {"system/controlDict": control}
    add_windows_libs(none, _ProbeOnly(windows))
    assert none.files["system/controlDict"] == control, "no fvConstraints file, nothing to load"


@pytest.mark.parametrize("utility", ["dsmcFoam", "mdFoam", "mdEquilibrationFoam"])
def test_legacy_particle_solvers_report_time_step_progress(utility: str) -> None:
    from stokes.run.external import CasePlan, _build_steps

    plan = CasePlan()
    _build_steps([{"utility": utility, "argv": [utility]}], [], plan)
    assert plan.steps[0]["parse"] == "foamRun"


class JobRunner:
    """A host that answers the probe and records the job it is asked to create."""

    name = "local"
    available = True
    why = ""

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = {**record, "default_install_id": "of14"}
        self.created: dict[str, Any] = {}
        self.started: list[str] = []
        self.listeners: list[Any] = []
        self.extracts: list[dict[str, Any]] = []

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "host.probe":
            return self.record
        if method == "job.create":
            self.created = params
            return {"job": "j_000000000001"}
        if method == "job.start":
            self.started.append(params["job"])
            return {"state": "running"}
        if method == "job.status":
            # The solver's report: it reached its `End` (every finished OpenFOAM run prints it),
            # and, as a transient case, never printed "converged".
            # Keyed by the STEP's name, as the agent writes it (`.records/<job>.json`), not by
            # the desktop's `solve` alias: reading `reports["solve"]` gave an empty report for
            # every imported case, which is why they were all `done_unconverged` in the sweep.
            # The steps are RECORDS, as the agent's status holds them (`jobs.py` state["steps"]):
            # sweep 2 measured that a name list here hid the bug of `str(record)` matching no
            # report, which left every finished case `done_unconverged` (finding 11).
            return {
                "state": "done",
                "steps": [
                    {"name": "s1_blockMesh", "state": "done", "rc": 0},
                    {"name": "s2_foamRun", "state": "done", "rc": 0},
                ],
                "reports": {"s2_foamRun": {"ended": True, "iterations": 5}},
            }
        if method == "job.read" and str(params.get("path", "")).startswith("postProcessing/"):
            return {"text": "# Time p\n0.1 1\n0.2 2\n", "truncated": False}
        if method == "job.read":
            return {"text": '{"iteration": 1, "time": 0.005, "residuals": {"Ux": 0.1}}\n'}
        if method == "job.outputs":
            return {
                "files": [
                    {"path": "postProcessing/probes/0/p", "size": 9},
                    {"path": "postProcessing/probes/0/p.dat", "size": 20},
                ]
            }
        if method == "extract.fields":
            # Recorded and refused: these tests check what is ASKED for; the agent's own tests
            # check what it reads.
            self.extracts.append(params)
            raise RuntimeError("no fields in this fake")
        raise AssertionError(f"unexpected call {method}")

    def subscribe(self, listener: Any) -> None:
        self.listeners.append(listener)

    def close(self) -> None:
        return


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(folder))
    return folder


@pytest.fixture
def tutorials(tmp_path: Path) -> Path:
    root = tmp_path / "tutorials"
    make_case(root, "incompressibleFluid/cavity", CONTROL, allrun=ALLRUN, files=FILES)
    return root


def _session(tmp_path: Path, tutorials: Path) -> tuple[Session, JobRunner]:
    from stokes.run.hosts import HostRegistry

    session = Session()
    record = _probe(tutorials)
    record["utilities"] = [*INVENTORY["utilities"], "decomposePar", "reconstructPar"]
    runner = JobRunner(record)
    HostRegistry.of(session)._runners["local"] = runner  # type: ignore[assignment]
    session.execute("project.new", name="demo", path=str(tmp_path / "proj"))
    session.execute("case.import", tutorial="incompressibleFluid/cavity")
    return session, runner


def test_the_plan_stages_the_case_and_says_what_it_changed(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    from stokes.run.external import plan_case

    session, _runner = _session(tmp_path, tutorials)
    session.execute(
        "case.set", node="/cases/cavity", file="system/controlDict", path=["endTime"], value=2
    )
    node = session.require_project().get("/cases/cavity")
    plan = plan_case(_home(session), node)
    assert b"endTime 2;" in plan.files["system/controlDict"], "the override is staged"
    assert plan.files["0/U"] == plan.files["0.orig/U"], "cp -r 0.orig 0 is applied at staging"
    assert "README" not in plan.files, "a file no solver reads is not staged"
    kinds = {
        f["path"]: (f["area"], f["kind"])
        for f in session.execute("case.files", node="/cases/cavity")
    }
    assert kinds["README"] == ("extras", "extra"), "it is kept in the copy, not linted"
    assert [step["name"] for step in plan.steps] == ["s1_blockMesh", "s2_foamRun"]
    assert plan.steps[1]["parse"] == "foamRun"
    assert plan.steps[1]["parse_params"] == {"end_time": 2.0}
    assert "parse" not in plan.steps[0]
    assert len(plan.adapted) == 3, plan.adapted  # decomposePar, the parallel solve, reconstructPar


def test_a_decomposed_windows_run_loads_the_generic_patch_fields() -> None:
    """Measured on FoamWin: decomposePar stopped at `Unknown patchField type v2WallFunction`
    (pitzDaily's unused `0/v2`) until `libgenericFvFields.dll` was named in the controlDict."""
    from stokes.run.external import CasePlan, add_windows_libs

    windows = {"system": "Windows", "libraries": ["genericFvFields"]}
    plan = CasePlan(files={"system/controlDict": b"endTime 1;\n"})
    plan.steps = [{"argv": ["decomposePar", "-force"]}, {"argv": ["foamRun"], "ranks": 2}]
    add_windows_libs(plan, _ProbeOnly(windows))
    assert b'"libgenericFvFields.dll"' in plan.files["system/controlDict"]
    serial = CasePlan(files={"system/controlDict": b"endTime 1;\n"})
    serial.steps = [{"argv": ["foamRun"]}]
    add_windows_libs(serial, _ProbeOnly(windows))
    assert b"generic" not in serial.files["system/controlDict"], "only a run that decomposes"


def test_a_parallel_allrun_runs_on_the_chosen_cores(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    from stokes.run.external import plan_case

    session, _runner = _session(tmp_path, tutorials)
    node = session.require_project().get("/cases/cavity")
    plan = plan_case(_home(session), node, cores=3)
    names = [step["argv"][0] for step in plan.steps]
    assert names == ["blockMesh", "decomposePar", "foamRun", "reconstructPar"]
    assert plan.steps[2]["ranks"] == 3
    assert "ranks" not in plan.steps[1] and "ranks" not in plan.steps[3]
    assert b"numberOfSubdomains 3;" in plan.files["system/decomposeParDict"]
    # A mesh-only run stays serial whatever the cores.
    mesh = plan_case(_home(session), node, cores=3, mesh_only=True)
    assert all("ranks" not in step for step in mesh.steps)


def test_a_serial_foamrun_solve_is_decomposed_around() -> None:
    from stokes.run.external import CasePlan, _build_steps

    steps = [
        {"utility": "blockMesh", "argv": ["blockMesh"], "line": 2},
        {"utility": "foamRun", "argv": ["foamRun"], "line": 3},
        {"utility": "postProcess", "argv": ["postProcess", "-func", "yPlus"], "line": 4},
    ]
    plan = CasePlan(files={"system/controlDict": b"endTime 1;\n"})
    _build_steps(steps, [], plan, cores=4)
    argv = [step["argv"] for step in plan.steps]
    assert argv[:4] == [["blockMesh"], ["decomposePar", "-force"], ["foamRun"], ["reconstructPar"]]
    assert argv[4][0] == "postProcess", "a later step reads the reconstructed case"
    assert plan.steps[2]["ranks"] == 4
    assert b"numberOfSubdomains 4;" in plan.files["system/decomposeParDict"]


@pytest.mark.parametrize(
    ("steps", "regions", "why"),
    [
        ([{"utility": "icoFoam", "argv": ["icoFoam"]}], False, "single foamRun"),
        ([{"utility": "foamMultiRun", "argv": ["foamMultiRun"]}], True, "multi-region"),
        (
            [
                {"utility": "decomposePar", "argv": ["decomposePar"]},
                {"utility": "potentialFoam", "argv": ["potentialFoam"], "parallel": True},
            ],
            False,
            "potentialFoam cannot run over MPI",
        ),
    ],
)
def test_what_cannot_be_decomposed_runs_on_one_core_and_says_why(
    steps: list[dict[str, Any]], regions: bool, why: str
) -> None:
    from stokes.run.external import CasePlan, _build_steps

    plan = CasePlan(files={"system/controlDict": b"endTime 1;\n"})
    _build_steps(steps, [], plan, cores=4, regions=regions)
    assert all("ranks" not in step for step in plan.steps)
    assert "system/decomposeParDict" not in plan.files
    assert any(why in note for note in plan.adapted), plan.adapted


def test_run_start_with_a_case_sends_an_external_job(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session, runner = _session(tmp_path, tutorials)
    reply = session.execute("run.start", case="/cases/cavity")
    assert reply["state"] in ("queued", "running") and reply["job"] == "j_000000000001"
    assert reply["steps"] == ["s1_blockMesh", "s2_foamRun"]
    assert runner.created["profile"] == "external" and runner.created["install"] == "of14"
    assert "system/controlDict" in runner.created["files"]
    assert runner.started == ["j_000000000001"]
    record = session.execute("run.status", run=reply["run"])
    assert record["engine"] == "of14.external"

    # The job's final event collects the run: history read back, the record finished.
    for listener in runner.listeners:
        listener("job.state", {"job": "j_000000000001", "state": "done", "step": None})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if session.execute("run.status", run=reply["run"])["state"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    # **`done`, not `done_unconverged`**: a case that reached its `End` finished as written. The
    # residual criterion is a CAD model's; a transient tutorial never prints "converged", and the
    # sweep of 2026-11-01 reported every such case as "Stopped at N iterations".
    assert session.execute("run.status", run=reply["run"])["state"] == "done"

    again = session.execute("run.start", case="/cases/cavity")
    assert again["reused"] is True and again["run"] == reply["run"], "same case, same basis"
    session.execute(
        "case.set", node="/cases/cavity", file="system/controlDict", path=["endTime"], value=1
    )
    changed = session.execute("run.start", case="/cases/cavity")
    assert changed["reused"] is False, "an edit is a different case"


def test_a_case_with_nothing_to_run_is_refused_and_recorded(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session, runner = _session(tmp_path, tutorials)
    node = session.require_project().get("/cases/cavity")
    recipe = dict(node.params["recipe"])
    recipe["steps"] = [{**step, "folder": "sub"} for step in recipe["steps"]]
    session.execute("parameter.set", node="/cases/cavity", name="recipe", value=recipe)
    with pytest.raises(StokesError) as caught:
        session.execute("run.start", case="/cases/cavity")
    assert caught.value.code == "run.external_subcase"
    assert runner.created == {}, "nothing was sent"
    runs = session.execute("run.list")["runs"]
    assert runs and runs[0]["state"] == "failed" and "run.external_subcase" in runs[0]["error"]


def test_a_host_without_openfoam_records_skipped(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session, runner = _session(tmp_path, tutorials)
    runner.record["default_install_id"] = None
    reply = session.execute("run.start", case="/cases/cavity")
    assert reply["state"] == "skipped" and runner.created == {}


def test_the_executor_is_found_by_engine() -> None:
    from stokes.run.executors import executor_for
    from stokes.run.external import ExternalCaseExecutor

    assert executor_for("of14.external") is ExternalCaseExecutor


def test_run_time_asks_the_host_for_that_time(tmp_path: Path, home: Path, tutorials: Path) -> None:
    session, runner = _session(tmp_path, tutorials)
    reply = session.execute("run.start", case="/cases/cavity")
    with pytest.raises(StokesError) as caught:
        session.execute("run.time", run=reply["run"], time=0.25)
    assert caught.value.code == "run.not_finished"
    for listener in runner.listeners:
        listener("job.state", {"job": "j_000000000001", "state": "done", "step": None})
    deadline = time.monotonic() + 5
    while session.execute("run.status", run=reply["run"])["state"] in ("queued", "running"):
        assert time.monotonic() < deadline, "the run was never collected"
        time.sleep(0.05)
    assert runner.extracts and "time" not in runner.extracts[0], "a finished run reads the latest"

    answer = session.execute("run.time", run=reply["run"], time=0.25)
    assert answer == {"run": reply["run"], "time": 0.25, "fetching": True}
    deadline = time.monotonic() + 5
    while len(runner.extracts) < 2:
        assert time.monotonic() < deadline, "run.time never asked the host"
        time.sleep(0.05)
    assert runner.extracts[1]["time"] == 0.25


def test_a_solver_the_install_lacks_is_refused_before_anything_runs(
    tmp_path: Path, home: Path
) -> None:
    """Found by the T-317 sweep: seventeen cases whose module FoamWin lacks started a run that
    died in foamRun ("Solvers table is empty"). The probe says so first now."""
    from stokes.run.hosts import HostRegistry

    root = tmp_path / "tutorials"
    make_case(root, "XiFluid/flame", "application foamRun;\nsolver XiFluid;\n")
    session = Session()
    runner = JobRunner(_probe(root))
    HostRegistry.of(session)._runners["local"] = runner  # type: ignore[assignment]
    session.execute("project.new", name="demo", path=str(tmp_path / "proj"))
    session.execute("case.import", tutorial="XiFluid/flame")
    with pytest.raises(StokesError) as caught:
        session.execute("run.start", case="/cases/flame")
    assert caught.value.code == "run.engine_lacks" and "XiFluid" in caught.value.message
    assert runner.created == {}, "nothing was sent to the host"


def test_a_run_offers_its_function_object_tables(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session, _runner = _session(tmp_path, tutorials)
    run = session.execute("run.start", case="/cases/cavity")["run"]
    listed = session.execute("run.monitors", run=run)
    assert listed["monitors"] == [{"name": "probes/0/p.dat", "size": 20}], "tables only"
    table = session.execute("run.monitor", run=run, name="probes/0/p.dat")
    assert table["columns"] == ["Time", "p"] and table["values"]["p"] == [1.0, 2.0]
    for bad in ("../state.json", "probes/0/p", "/etc/x.dat"):
        with pytest.raises(StokesError) as caught:
            session.execute("run.monitor", run=run, name=bad)
        assert caught.value.code == "run.no_monitor"
