"""The job supervisor against the real OpenFOAM (T-202a).

Marked `engine`, so `tests/conftest.py` skips it unless `STOKES_ENGINE_TESTS=1`. Run on the mesh
host:
    STOKES_OPENFOAM_BASHRC=<the env script> STOKES_ENGINE_TESTS=1 STOKES_AGENT_HOME=$(mktemp -d) \\
        PYTHONPATH=.:agent python3 -m pytest -q tests/engine/test_agent_jobs_engine.py

**What only this test can check.** `tests/test_agent_jobs.py` drives a fake utility, so it
proves the staging, the argv rules, the cancel and the recovery — but nothing there proves a
real OpenFOAM utility, launched through the captured environment, with `cwd` at the job folder,
actually meshes. That is a fact about a machine and an install, and this is the test that
asserts it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from stokes_agent.jobs import JobStore, Step

pytestmark = pytest.mark.engine

FIXTURES = Path(__file__).parent.parent / "fixtures" / "of14"


def _tutorial(relative: str) -> Path:
    """A path under ``$FOAM_TUTORIALS``, resolved from the captured engine environment.

    Taken from `WM_PROJECT_DIR` rather than written out, so this test runs on any host with the
    install in any location — and so the repository never contains a machine-specific path (rule
    3).
    """
    from stokes_agent.engine_env import get_env

    return Path(get_env().env["WM_PROJECT_DIR"]) / "tutorials" / relative


def _case_files(dict_name: str = "blockMeshDict") -> dict[str, str]:
    """The cavity case as job files: its `blockMeshDict` and a `controlDict`.

    **The `blockMeshDict` is written as `system/blockMeshDict` on purpose.** The pitzDaily
    tutorial has no dict of its own — it is meshed with `-dict
    $FOAM_TUTORIALS/resources/blockMesh/pitzDaily` — so a job staging only the tutorial's `0/`,
    `constant/` and `system/` fails at its first step for a reason that has nothing to do with
    the supervisor. Measured while settling T-200's `[VERIFY-OF14]` items."""
    # **Resolved through the captured environment, not a literal path.** The IP gate refuses a home
    # folder in the repository, and a test that names one machine's install only runs on that
    # machine.
    tutorial = _tutorial("incompressibleFluid/cavity")
    files = {f"system/{dict_name}": (FIXTURES / "cavity" / "system" / "blockMeshDict").read_text()}
    control = tutorial / "system" / "controlDict"
    if control.is_file():
        files["system/controlDict"] = control.read_text()
    return files


def _settle(store: JobStore, job: str, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = store.status(job)
        if state["state"] not in ("queued", "running", "cancelling"):
            return dict(state)
        time.sleep(0.2)
    raise AssertionError(f"job {job} did not finish in {timeout}s")


def test_blockMesh_then_checkMesh_on_the_real_install(tmp_path: Path) -> None:
    """The cavity, meshed and checked by the real utilities through the supervisor.

    `blockMesh` exits 0, `checkMesh` says `Mesh OK.` — and the state file records both exit
    codes, which is the supervisor's own claim.
    """
    store = JobStore(tmp_path / "jobs")
    job = store.create(
        _case_files(),
        [Step("mesh", ("blockMesh",), timeout_s=120), Step("check", ("checkMesh",), timeout_s=120)],
        label="cavity",
    )
    store.start(job)
    state = _settle(store, job)
    assert state["state"] == "done", f"job ended {state['state']}: {state.get('error')}"
    assert state["steps"][0]["rc"] == 0
    assert state["steps"][1]["rc"] == 0
    log = "\n".join(store.tail(job, "check", lines=200)["lines"])
    assert "Mesh OK." in log, f"checkMesh did not pass:\n{log[-2000:]}"
    assert "cells:" in log
    # And the mesh is really on disk, in the job folder.
    assert (store.job_dir(job) / "constant" / "polyMesh" / "points").is_file()


def test_a_broken_dict_fails_the_job_with_the_utilitys_own_code(tmp_path: Path) -> None:
    """**The exit-code claim, end to end.** A deliberately invalid `blockMeshDict` makes
    `blockMesh` exit
    1, so the job is `failed` with `agent.step_failed` and the second step is `skipped`.

    Measured on this host: `blockMesh` exits 1 after `FOAM FATAL ERROR` — it does **not** exit 0
    — which is why the supervisor trusts the exit code and adds no log-string check.
    """
    files = _case_files()
    files["system/blockMeshDict"] = files["system/blockMeshDict"].replace(
        "hex (0 1 2 3 4 5 6 7)", "hex (0 1 2 3 4 5 6 7 8 9)"
    )
    store = JobStore(tmp_path / "jobs")
    job = store.create(
        files,
        [Step("mesh", ("blockMesh",), timeout_s=120), Step("check", ("checkMesh",), timeout_s=120)],
    )
    store.start(job)
    state = _settle(store, job)
    assert state["state"] == "failed"
    assert state["steps"][0]["rc"] == 1
    assert state["steps"][1]["state"] == "skipped"
    assert state["error"]["code"] == "agent.step_failed"


def test_cancel_kills_a_real_solver(tmp_path: Path) -> None:
    """**The group kill, on a real OpenFOAM process.**

    `foamRun` on pitzDaily is a long run, and the point of `start_new_session=True` is that
    killing the group reaches everything it spawns. On a single-core run there is no `mpirun`
    child to test, so what this asserts is the part that matters here: the utility dies, the job
    is `cancelled` rather than
    `failed`, and **nothing is left running**.
    """
    pitz = FIXTURES / "pitzDaily"
    files: dict[str, str] = {}
    for folder in ("0", "constant", "system"):
        for path in (pitz / folder).rglob("*"):
            if path.is_file():
                files[str(path.relative_to(pitz))] = path.read_text()
    # **The dict the tutorial is meshed with.** Note that pitzDaily's function object lives in a
    # separate `system/functions` file, not in `controlDict`, and its body is an ALLOWED
    # `#includeFunc patchAverage(patch=inlet, fields=(p U))` — so it stages verbatim and no
    # line has to
    # be removed. The brief expected to strip a `functions` block from `controlDict`;
    # measured, there is
    # none to strip (T-200's notes record this).
    # in T-202b.
    resources = _tutorial("resources/blockMesh/pitzDaily")
    if resources.is_file():
        files["system/blockMeshDict"] = resources.read_text()

    store = JobStore(tmp_path / "jobs")
    job = store.create(
        files,
        [
            Step("mesh", ("blockMesh",), timeout_s=120),
            Step("solve", ("foamRun", "-solver", "incompressibleFluid"), timeout_s=600),
        ],
    )
    store.start(job)
    # Wait for the solve to be under way, then cancel.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = store.status(job)
        if state["state"] == "failed":
            pytest.fail(f"the run failed before it could be cancelled: {state.get('error')}")
        if state["steps"][1]["state"] == "running":
            break
        time.sleep(0.2)
    else:
        pytest.fail("the solve never started")

    assert store.cancel(job) is True
    state = _settle(store, job, timeout=30)
    assert state["state"] in ("cancelled", "interrupted"), state
    # Nothing may be left running: this is the assertion the brief asks for after every engine run.
    import subprocess

    strays = subprocess.run(
        ["pgrep", "-u", str(__import__("os").getuid()), "-a", "foamRun"],
        capture_output=True,
        check=False,
    )
    assert strays.returncode != 0, f"foamRun was left running:\n{strays.stdout.decode()}"


def test_pitzdaily_meshes_and_solves_through_the_supervisor(tmp_path: Path) -> None:
    """**The full skeleton path for one case**: stage, `blockMesh`,
    `foamRun -solver incompressibleFluid`.

    This is the engine step the brief asks for, and it is the closest thing to the
    end-to-end goal that
    exists today: a case written as files, meshed and solved by real OpenFOAM through the
    supervisor,
    with the log on disk and both exit codes recorded.

    **pitzDaily's function objects stage verbatim.** Its `#includeFunc patchAverage(...)` is
    an allowed
    function, which is why the lint accepts the tutorial's own `system/functions`
    unchanged — measured,
    and worth knowing because the brief expected to strip a `functions` entry.
    """
    pitz = FIXTURES / "pitzDaily"
    files: dict[str, str] = {}
    for folder in ("0", "constant", "system"):
        for path in (pitz / folder).rglob("*"):
            if path.is_file():
                files[str(path.relative_to(pitz))] = path.read_text()
    resources = _tutorial("resources/blockMesh/pitzDaily")
    if resources.is_file():
        files["system/blockMeshDict"] = resources.read_text()

    store = JobStore(tmp_path / "jobs")
    job = store.create(
        files,
        [
            Step("mesh", ("blockMesh",), timeout_s=120),
            Step("solve", ("foamRun", "-solver", "incompressibleFluid"), timeout_s=600),
        ],
        label="pitzDaily",
    )
    store.start(job)
    state = _settle(store, job, timeout=600)
    assert state["state"] == "done", f"the run ended {state['state']}: {state.get('error')}"
    assert state["steps"][0]["rc"] == 0
    assert state["steps"][1]["rc"] == 0
    # The solver's own end marker, from its log.
    solve_log = store.tail(job, "solve", lines=50)["lines"]
    assert any(line.strip() == "End" for line in solve_log), (
        f"no End in the solver log: {solve_log[-5:]}"
    )
    # And it really wrote time directories beyond 0.
    written = sorted(
        item.name
        for item in store.job_dir(job).iterdir()
        if item.is_dir() and item.name[0].isdigit()
    )
    assert len(written) > 1, f"the solver wrote no time directories: {written}"
    # No parser was registered for this step, so `reports` stays empty.
    assert state.get("reports", {}) == {}
