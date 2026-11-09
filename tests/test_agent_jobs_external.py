"""The agent's `external` job profile (ADR-0029 decision 5, plan T-309).

**What must hold.** An external job is judged by `flags_external` and `lint_external` against the
agent's OWN read of the install, never by a list the client sends; the default profile is
unchanged; a record's profile is what `start` and every step re-check. No process is launched
here, so the tests run on every platform: every case is synthetic (ADR-0029 decision 2).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stokes_agent import jobs
from stokes_agent.engine_env import EngineEnv
from stokes_agent.jobs import JobBadParams, JobError, JobStore, JobUnsafeDict, Step
from stokes_agent.lint_external import ProbedInstall
from stokes_agent.server import AgentServer

INSTALL = ProbedInstall.from_lists(
    solver_modules=["incompressibleFluid"],
    utilities=["blockMesh", "foamRun", "icoFoam", "foamPostProcess"],
    libraries=["fieldFunctionObjects"],
    etc_functions=["streamlines"],
    etc_files=["caseDicts/setConstraintTypes"],
)
HEADER = "FoamFile\n{\n    format ascii;\n    class dictionary;\n}\n"
CASE: dict[str, bytes | str] = {
    "system/controlDict": HEADER + "application icoFoam;\nendTime 0.5;\n",
    "0.orig/U": HEADER + "internalField uniform (0 0 0);\nboundaryField\n{\n"
    '    #includeEtc "caseDicts/setConstraintTypes"\n}\n',
    "0/U": HEADER + "internalField uniform (0 0 0);\nboundaryField\n{\n}\n",
}


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JobStore:
    engine = EngineEnv(
        bashrc=str(tmp_path / "bashrc"),
        env={"PATH": str(tmp_path)},
        project="OpenFOAM",
        version="14",
        flavour="org",
        seconds=0.0,
    )
    monkeypatch.setattr(jobs, "_posix_host", lambda: True)
    monkeypatch.setattr(jobs, "resolve_engine", lambda engine_id=None: engine)
    monkeypatch.setattr(jobs, "resolve_utility", lambda _engine, _name: sys.executable)
    made = JobStore(tmp_path / "jobs")
    monkeypatch.setattr(made, "_installed", lambda _engine: INSTALL)
    return made


def test_an_external_job_runs_what_the_install_has(store: JobStore) -> None:
    steps = [Step("mesh", ("blockMesh",)), Step("solve", ("icoFoam",))]
    job = store.create(dict(CASE), steps, profile="external")
    state = store._load(job)
    assert state["profile"] == "external"
    assert (store.job_dir(job) / "0.orig" / "U").is_file(), "0.orig is an external staged folder"


def test_the_default_profile_is_unchanged(store: JobStore) -> None:
    with pytest.raises(JobBadParams, match="not an allowed utility"):
        store.create({"system/controlDict": "x"}, [Step("solve", ("icoFoam",))])
    with pytest.raises(JobBadParams, match="not a staged path"):
        store.create({"0.orig/U": "x", "system/controlDict": "x"}, [Step("mesh", ("blockMesh",))])


@pytest.mark.parametrize(
    ("argv", "why"),
    [
        (("foamRun", "-solver", "XiFluid"), "not allowed"),
        (("foamRun", "-libs", "(evil)"), "not allowed for any utility"),
        (("dsmcFoam",), "not in this OpenFOAM install"),
        (("bash", "-c", "x"), "not an allowed utility"),
        (("foamPostProcess", "-func", "absent"), "not allowed"),
    ],
)
def test_an_external_step_is_judged_against_the_probe(
    store: JobStore, argv: tuple[str, ...], why: str
) -> None:
    with pytest.raises(JobBadParams, match=why):
        store.create(dict(CASE), [Step("step", argv)], profile="external")
    assert not any(store.root.glob("j_*")), "a refusal leaves nothing on disk"


def test_an_external_file_is_linted_by_the_external_profile(store: JobStore) -> None:
    coded: dict[str, bytes | str] = {
        **CASE,
        "system/fvSolution": HEADER + "x #codeStream { code #{ #}; };\n",
    }
    with pytest.raises(JobUnsafeDict):
        store.create(coded, [Step("solve", ("icoFoam",))], profile="external")
    absent: dict[str, bytes | str] = {**CASE, "0/p": HEADER + '#includeEtc "caseDicts/notThere"\n'}
    with pytest.raises(JobUnsafeDict):
        store.create(absent, [Step("solve", ("icoFoam",))], profile="external")


def test_profile_values_and_copy_from(store: JobStore) -> None:
    with pytest.raises(JobBadParams, match="profile"):
        store.create(dict(CASE), [Step("solve", ("icoFoam",))], profile="anything")
    with pytest.raises(JobBadParams, match="copy_from"):
        store.create(
            dict(CASE),
            [Step("solve", ("icoFoam",))],
            profile="external",
            copy_from={"job": "j_000000000000", "paths": ["constant/polyMesh"]},
        )


def test_mpi_is_only_for_the_listed_utilities(store: JobStore) -> None:
    with pytest.raises(JobBadParams, match="does not support an MPI step"):
        store.create(dict(CASE), [Step("mesh", ("blockMesh",), ranks=2)], profile="external")


def test_start_rechecks_under_the_record_s_profile(store: JobStore) -> None:
    job = store.create(dict(CASE), [Step("solve", ("icoFoam",))], profile="external")
    state = store._load(job)
    state["profile"] = "something-else"
    store._save(store.job_dir(job), state)
    with pytest.raises(JobError, match="unknown profile"):
        store.start(job)
    # A record that lost its profile is checked under the default rules, which refuse icoFoam.
    del state["profile"]
    store._save(store.job_dir(job), state)
    with pytest.raises(JobBadParams, match="not an allowed utility"):
        store.start(job)


def test_a_put_into_an_external_job_uses_its_rules(store: JobStore) -> None:
    job = store.create(dict(CASE), [Step("solve", ("icoFoam",))], profile="external")
    store.put(job, "0.orig/p", b"internalField uniform 0;\n", 0, True)
    with pytest.raises(JobUnsafeDict):
        store.put(job, "1/p", b'#includeEtc "caseDicts/notThere"\n', 0, True)


def test_the_wire_takes_a_profile() -> None:
    import asyncio

    server = AgentServer()
    with pytest.raises(Exception, match="profile must be a string"):
        asyncio.run(
            server._job_create(
                files={},
                steps=[{"name": "s", "argv": ["icoFoam"]}],
                profile=3,  # type: ignore[arg-type]
            )
        )
