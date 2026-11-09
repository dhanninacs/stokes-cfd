"""File actions between an external job's steps (the recipe's `rm -f 0/phi` after `potentialFoam`).

**What must hold.** A file action is an `external` job's step with no program. It acts only inside
the job folder, on staged folders, and refuses any symbolic link on either path. Its outcome is
recorded like a program's, and a failed action ends the job like a failed program does. No process
is launched, so these run on every platform; a symlink case is skipped where the host cannot make
one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.run.external import CasePlan, _build_steps
from stokes_agent import jobs
from stokes_agent.engine_env import EngineEnv
from stokes_agent.jobs import JobBadParams, JobStore, Step
from stokes_agent.lint_external import ProbedInstall

INSTALL = ProbedInstall.from_lists(
    solver_modules=["incompressibleFluid"],
    utilities=["blockMesh", "foamRun", "icoFoam", "potentialFoam"],
    libraries=["fieldFunctionObjects"],
    etc_functions=[],
    etc_files=[],
)
HEADER = "FoamFile\n{\n    format ascii;\n    class dictionary;\n}\n"
CASE: dict[str, bytes | str] = {
    "system/controlDict": HEADER + "application icoFoam;\nendTime 0.5;\n",
    "0.orig/U": HEADER + "internalField uniform (0 0 0);\nboundaryField\n{\n}\n",
    "0/phi": HEADER + "internalField uniform 0;\n",
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
    monkeypatch.setattr(jobs, "resolve_utility", lambda _engine, _name: "")
    made = JobStore(tmp_path / "jobs")
    monkeypatch.setattr(made, "_installed", lambda _engine: INSTALL)
    return made


def _action_step(name: str, action: dict[str, str]) -> Step:
    return Step(name=name, argv=(), timeout_s=60, action=action)


def test_a_file_action_is_taken_only_in_an_external_job(store: JobStore) -> None:
    step = _action_step("rm_phi", {"op": "remove", "target": "0/phi"})
    store.create(dict(CASE), [step], profile="external")
    with pytest.raises(JobBadParams, match="only an external job takes"):
        store.create(dict(CASE), [step])


@pytest.mark.parametrize(
    ("action", "match"),
    [
        ({"op": "chmod", "target": "0/phi"}, "not mkdir, remove or copy"),
        ({"op": "remove"}, "needs a target path"),
        ({"op": "remove", "target": "0/phi", "source": "0/U"}, "takes no source"),
        ({"op": "remove", "target": "../escape"}, "not allowed|outside|staged|relative"),
        ({"op": "remove", "target": "validation/notes"}, "not allowed|outside|staged|relative"),
        (
            {"op": "copy", "source": "0.orig", "target": "/etc"},
            "not allowed|outside|staged|relative",
        ),
        ({"op": "remove", "target": "0/phi", "extra": "x"}, "not mkdir, remove or copy"),
    ],
)
def test_a_file_action_that_is_not_one_is_refused(
    store: JobStore, action: dict[str, str], match: str
) -> None:
    with pytest.raises(JobBadParams, match=match):
        store.create(dict(CASE), [_action_step("act", action)], profile="external")


def test_a_file_action_with_a_program_is_refused(store: JobStore) -> None:
    step = Step(name="act", argv=("icoFoam",), action={"op": "remove", "target": "0/phi"})
    with pytest.raises(JobBadParams, match="runs no program"):
        store.create(dict(CASE), [step], profile="external")


def test_remove_deletes_the_staged_file_and_copy_restores_it(store: JobStore) -> None:
    job = store.create(
        dict(CASE),
        [
            _action_step("rm_phi", {"op": "remove", "target": "0/phi"}),
            _action_step("cp_u", {"op": "copy", "source": "0.orig/U", "target": "0/U"}),
        ],
        profile="external",
    )
    job_dir = store.job_dir(job)
    step_rm, step_cp = store._steps_of(job, store._load(job))
    assert store._run_action(job_dir, step_rm, "external") == ("done", None)
    assert not (job_dir / "0" / "phi").exists()
    assert store._run_action(job_dir, step_cp, "external") == ("done", None)
    assert (job_dir / "0" / "U").read_bytes() == CASE["0.orig/U"].encode()


def test_remove_of_a_missing_file_is_not_a_failure(store: JobStore) -> None:
    job = store.create(
        dict(CASE),
        [_action_step("rm_nope", {"op": "remove", "target": "0/nothere"})],
        profile="external",
    )
    (step,) = store._steps_of(job, store._load(job))
    assert store._run_action(store.job_dir(job), step, "external") == ("done", None)


def test_copy_of_a_missing_source_fails_with_its_code(store: JobStore) -> None:
    job = store.create(
        dict(CASE),
        [_action_step("cp_nope", {"op": "copy", "source": "0.orig/p", "target": "0/p"})],
        profile="external",
    )
    (step,) = store._steps_of(job, store._load(job))
    outcome, error = store._run_action(store.job_dir(job), step, "external")
    assert outcome == "failed"
    assert error is not None and error["code"] == "agent.file_action"
    assert "does not exist" in error["message"]


def test_an_action_refuses_a_symbolic_link_on_its_path(store: JobStore, tmp_path: Path) -> None:
    job = store.create(
        dict(CASE),
        [_action_step("rm_phi", {"op": "remove", "target": "0/phi"})],
        profile="external",
    )
    job_dir = store.job_dir(job)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep\n")
    link = job_dir / "0" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege on this host")
    step = _action_step("rm_link", {"op": "remove", "target": "0/linked/keep"})
    outcome, error = store._run_action(job_dir, step, "external")
    assert outcome == "failed"
    assert error is not None and "symbolic link" in error["message"]
    assert (outside / "keep").exists()


def test_the_recipe_order_puts_the_action_after_the_program() -> None:
    plan = CasePlan()
    steps = [{"utility": "potentialFoam", "argv": ["potentialFoam"], "line": 2}]
    later = [{"op": "remove", "target": "0/phi", "line": 3}]
    _build_steps(steps, later, plan)
    assert [step["name"] for step in plan.steps] == ["s1_potentialFoam", "a2_remove"]
    assert plan.steps[1]["action"] == {"op": "remove", "target": "0/phi"}
    assert plan.steps[1]["argv"] == []
    assert plan.skipped == []


def test_an_action_outside_the_solver_folders_is_listed_not_sent() -> None:
    plan = CasePlan()
    later = [{"op": "remove", "target": "postProcessing/old", "line": 4}]
    _build_steps([], later, plan)
    assert plan.steps == []
    assert len(plan.skipped) == 1
    assert "outside the folders a solver reads" in plan.skipped[0]
