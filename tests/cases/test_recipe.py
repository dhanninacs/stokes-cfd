"""The restricted Allrun parser and the default recipe (T-306), on synthetic Allrun texts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from stokes.cases.allrun import case_recipe, parse_allrun
from stokes.cases.recipe import Recipe, Step, check_recipe, default_recipe, stage_of

from .synthetic import GUARDS, make_case


def _argv(recipe: Recipe) -> list[list[str]]:
    return [list(step.argv) for step in recipe.steps]


def _unparsed(recipe: Recipe) -> dict[int, str]:
    return {item.line: item.why for item in recipe.unparsed}


def test_guards_comments_and_each_runner_form() -> None:
    text = (
        GUARDS
        + "\n# a comment line\n"
        + "runApplication blockMesh   # trailing comment\n"
        + "runApplication -s refine refineMesh -overwrite\n"
        + "runApplication -a checkMesh\n"
        + "runApplication decomposePar -force\n"
        + "runParallel foamRun\n"
        + "runParallel -np 4 -s second foamRun -solver fluid\n"
        + "runApplication $(getApplication)\n"
        + "runApplication $application\n"
        + 'runApplication "${application}"\n'
        + "runApplication foamPostProcess -func 'mag(U)' -latestTime\n"
        + "runApplication reconstructPar -latestTime\n"
        + "runApplication topoSet -region solid \\\n    -dict system/topoSetDict.1\n"
    )
    recipe = parse_allrun(text, "foamRun")
    assert recipe.unparsed == []
    assert recipe.kind == "complete" and recipe.origin == "allrun"
    assert _argv(recipe) == [
        ["blockMesh"],
        ["refineMesh", "-overwrite"],
        ["checkMesh"],
        ["decomposePar", "-force"],
        ["foamRun"],
        ["foamRun", "-solver", "fluid"],
        ["foamRun"],
        ["foamRun"],
        ["foamRun"],
        ["foamPostProcess", "-func", "mag(U)", "-latestTime"],
        ["reconstructPar", "-latestTime"],
        ["topoSet", "-region", "solid", "-dict", "system/topoSetDict.1"],
    ]
    steps = recipe.steps
    assert steps[1].name == "refineMesh.refine" and steps[0].name == "blockMesh"
    assert [s.parallel for s in steps[4:6]] == [True, True]
    assert steps[5].ranks == 4 and steps[4].ranks is None and steps[5].name == "foamRun.second"
    assert [s.stage for s in steps] == [
        "mesh",
        "mesh",
        "mesh",
        "init",
        "solve",
        "solve",
        "solve",
        "solve",
        "solve",
        "post",
        "post",
        "mesh",
    ]
    assert steps[-1].region == "solid" and steps[-1].line == 18
    assert steps[0].to_json()["utility"] == "blockMesh"


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("runApplication $(foamDictionary -entry x system/d)", "command substitution"),
        ("runApplication `echo blockMesh`", "command substitution"),
        ("runApplication blockMesh | tee log", "a pipe"),
        ("runApplication blockMesh > log.x", "a redirection"),
        ("runApplication blockMesh < input", "a redirection"),
        ("runApplication blockMesh; rm -rf /", "a command list"),
        ("runApplication blockMesh && runApplication foamRun", "a command list"),
        ("runApplication blockMesh &", "a command list"),
        ("runApplication $SOLVER", "a variable expansion"),
        ("runApplication blockMesh -dict $HOME/x", "a variable expansion"),
        ('runApplication blockMesh -dict "$HOME/x"', "a variable expansion"),
        ("eval runApplication blockMesh", "eval runs text"),
        ("source ./other.sh", "sourcing a script"),
        (". ./Allrun.pre", "sourcing a script"),
        ("exec foamRun", "exec replaces"),
        ("SOLVER=fluid", "a variable assignment"),
        ("foamRun", "not a command the recipe knows"),
        ("(cd fluid && ./Allrun)", "a subshell"),
        ("rm -rf *.orig", "a glob"),
        ("cp -r ~/x 0", "a glob"),
        ("runApplication 'unterminated", "an unterminated quote"),
        ("runApplication -x blockMesh", "-x is not a runApplication option"),
        ("runApplication -a", "names no application"),
        ("runApplication -s", "-s is not a runApplication option"),
        ("runApplication -s bad.name blockMesh", "the log suffix must be a plain name"),
        ("runParallel -np x foamRun", "-np must be a whole number"),
        ("runApplication ../bin/evil", "the application must be a plain name"),
        ("rm -rf /", "not a relative path inside the case"),
        ("rm -rf ..", "not a relative path inside the case"),
        ("rm", "rm names nothing"),
        ("rm -Z x", "-Z is not a rm option"),
        ("mkdir -p ../outside", "not a relative path inside the case"),
        ("mkdir", "mkdir names no folder"),
        ("cp -r 0.orig", "exactly one source and one target"),
        ("cp -r /etc/passwd 0", "the source is not a relative path"),
        ("cp -r 0.orig C:/x", "the target is not a relative path"),
        ("ln x y", "only a symbolic link"),
        ("cd /tmp", "not a relative path inside the case"),
        ("cd a b", "cd must name one folder"),
        ("export FOAM_SIGFPE=0", "a variable for later commands"),
    ],
)
def test_each_refused_form_is_listed_with_its_reason(line: str, why: str) -> None:
    recipe = parse_allrun(GUARDS + line + "\n", "foamRun")
    assert recipe.steps == [] and recipe.actions == []
    assert len(recipe.unparsed) == 1, recipe.unparsed
    item = recipe.unparsed[0]
    assert item.line == 5 and item.text == line and why in item.why
    assert recipe.kind == "partial"


def test_conditionals_loops_and_functions_are_listed_with_their_bodies() -> None:
    text = (
        "runApplication blockMesh\n"
        "if [ -f x ]\nthen\n    runApplication setFields\nfi\n"
        "for i in 1 2\ndo\n    runApplication foamRun\ndone\n"
        "if notTest; then runApplication foamRun; fi\n"
        "build()\n{\n    runApplication snappyHexMesh\n}\n"
        "case x in\n  a) runApplication foo ;;\nesac\n"
        "runApplication foamRun\n"
    )
    recipe = parse_allrun(text, "foamRun")
    assert _argv(recipe) == [["blockMesh"], ["foamRun"]]
    lines = _unparsed(recipe)
    assert set(lines) == {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17}
    assert "conditional" in lines[4] and "loop" in lines[8]
    assert "function" in lines[13] and "conditional" in lines[16]


def test_file_actions_resources_and_links() -> None:
    text = (
        "cp -r 0.orig 0\n"
        "cp -rf 0.orig/U 0/\n"
        "mkdir -p constant/geometry\n"
        "rm -rf 0 constant/polyMesh\n"
        "cp $FOAM_TUTORIALS/resources/geometry/body.stl.gz constant/geometry/\n"
        "cp ${FOAM_TUTORIALS}/resources/blockMesh/pitz system/blockMeshDict\n"
        "ln -s $FOAM_TUTORIALS/resources/geometry/x.obj constant/geometry/x.obj\n"
        "ln -sf ../0.orig/p constant/p\n"
        "cp $FOAM_TUTORIALS/resources/../etc/passwd x\n"
        "cp $FOAM_TUTORIALS/other/x x\n"
        "ln -s $FOAM_TUTORIALS/resources/x .\n"
    )
    recipe = parse_allrun(text, None)
    assert [a.to_json() for a in recipe.actions] == [
        {"op": "copy", "source": "0.orig", "target": "0", "line": 1},
        {"op": "copy", "source": "0.orig/U", "target": "0/U", "line": 2},
        {"op": "mkdir", "source": None, "target": "constant/geometry", "line": 3},
        {"op": "remove", "source": None, "target": "0", "line": 4},
        {"op": "remove", "source": None, "target": "constant/polyMesh", "line": 4},
        {"op": "copy", "source": "0.orig/p", "target": "constant/p", "line": 8},
    ]
    assert [r.to_json() for r in recipe.resources] == [
        {
            "source": "resources/geometry/body.stl.gz",
            "target": "constant/geometry/",
            "line": 5,
        },
        {"source": "resources/blockMesh/pitz", "target": "system/blockMeshDict", "line": 6},
        {"source": "resources/geometry/x.obj", "target": "constant/geometry/x.obj", "line": 7},
        {"source": "resources/x", "target": "", "line": 11},
    ]
    lines = _unparsed(recipe)
    assert set(lines) == {9, 10}
    assert "not a plain path under resources" in lines[9] and "variable" in lines[10]
    assert recipe.kind == "partial", "no steps at all is not a complete recipe"


def test_resource_glob_is_a_bounded_copy_not_shell_execution() -> None:
    recipe = parse_allrun(
        "cp $FOAM_TUTORIALS/resources/geometry/pipe*.obj.gz constant/geometry/\n"
        "cp $FOAM_TUTORIALS/resources/../secret* constant/geometry/\n"
        "cp $FOAM_TUTORIALS/resources/geometry/pipe*.obj.gz constant/geometry/; echo bad\n"
        "cp $FOAM_TUTORIALS/resources/geometry/*/*.obj.gz constant/geometry/\n",
        None,
    )
    assert [r.source for r in recipe.resources] == ["resources/geometry/pipe*.obj.gz"]
    assert set(_unparsed(recipe)) == {2, 3, 4}


def test_cd_into_sub_cases_sets_the_step_folder() -> None:
    text = (
        "cd fluid\nrunApplication blockMesh\ncd ..\n"
        "cd solid\nrunApplication blockMesh\ncd ../..\n"
        "cd ${0%/*} || exit 1\n"
    )
    recipe = parse_allrun(text, "foamMultiRun")
    assert [(s.folder, s.utility) for s in recipe.steps] == [
        ("fluid", "blockMesh"),
        ("solid", "blockMesh"),
    ]
    assert list(_unparsed(recipe)) == [6]


def test_application_lookup_needs_an_application() -> None:
    recipe = parse_allrun("runApplication $(getApplication)\n", None)
    assert "not known" in recipe.unparsed[0].why


def test_no_shell_feature_is_ever_executed(tmp_path: Path) -> None:
    """A hostile Allrun is text: parsing it creates nothing and runs nothing."""
    marker = tmp_path / "pwned"
    text = (
        f"touch {marker}\n"
        f"runApplication $(touch {marker})\n"
        f"runApplication `touch {marker}`\n"
        f"eval touch {marker}\n"
        f"runApplication blockMesh > {marker}\n"
    )
    recipe = parse_allrun(text, "foamRun")
    assert recipe.steps == [] and len(recipe.unparsed) == 5
    assert not marker.exists()


def test_binary_control_characters_and_limits() -> None:
    assert "binary" in parse_allrun("a\x00b", None).unparsed[0].why
    assert "control character" in parse_allrun("runApplication blo\x01ck\n", None).unparsed[0].why
    assert "control character" in parse_allrun("runApplication bl\x07ock\n", None).unparsed[0].why
    many = "".join("runApplication blockMesh\n" for _ in range(205))
    recipe = parse_allrun(many, None)
    assert len(recipe.steps) == 200 and "more than 200 steps" in recipe.unparsed[0].why
    tail = parse_allrun("runApplication blockMesh \\", None)
    assert _argv(tail) == [["blockMesh"]]


def test_the_default_recipe() -> None:
    assert _argv(default_recipe("foamRun", block_mesh=True, has_mesh=False)) == [
        ["blockMesh"],
        ["foamRun"],
    ]
    assert _argv(default_recipe("icoFoam", block_mesh=True, has_mesh=True)) == [["icoFoam"]]
    plain = default_recipe("foamMultiRun", block_mesh=False, has_mesh=False)
    assert _argv(plain) == [["foamMultiRun"]] and plain.kind == "complete"
    assert plain.origin == "default" and plain.steps[0].stage == "solve"
    none = default_recipe(None, block_mesh=True, has_mesh=False)
    assert _argv(none) == [["blockMesh"]] and none.kind == "partial"


def test_case_recipe_reads_allrun_or_falls_back(tmp_path: Path) -> None:
    case = make_case(tmp_path, "a/withAllrun", allrun=GUARDS + "runApplication blockMesh\n")
    recipe, facts = case_recipe(case)
    assert recipe.origin == "allrun" and _argv(recipe) == [["blockMesh"]]
    assert facts.solver == "incompressibleFluid"
    plain = make_case(tmp_path, "a/plain")
    recipe, _ = case_recipe(plain)
    assert recipe.origin == "default" and _argv(recipe) == [["blockMesh"], ["foamRun"]]
    multi = make_case(tmp_path, "m", "regionSolvers\n{\n    a fluid;\n}\n", block_mesh=False)
    assert _argv(case_recipe(multi)[0]) == [["foamMultiRun"]]
    legacy = make_case(tmp_path, "legacy/icoFoam/cavity", "endTime 1;\n")
    assert _argv(case_recipe(legacy, ("legacy", "icoFoam", "cavity"))[0])[-1] == ["icoFoam"]
    big = make_case(tmp_path, "big", allrun="#" * (300 * 1024))
    recipe, _ = case_recipe(big)
    assert "could not be read" in recipe.unparsed[0].why


def test_check_recipe_moves_refused_steps() -> None:
    recipe = parse_allrun("runApplication blockMesh\nrunApplication setFields\n", "foamRun")

    def check(argv: Sequence[str]) -> str | None:
        return None if argv[0] == "blockMesh" else f"{argv[0]!r} is not an allowed utility"

    check_recipe(recipe, check)
    assert recipe.checked and _argv(recipe) == [["blockMesh"]]
    assert recipe.refused[0].to_json() == {
        "line": 2,
        "text": "setFields",
        "why": "'setFields' is not an allowed utility",
    }
    assert recipe.kind == "partial" and recipe.to_json()["refused"][0]["line"] == 2


def test_check_recipe_with_the_agents_allow_list() -> None:
    """The argv words pass `stokes_agent.flags_external`, as `case.import` wires it."""
    from stokes_agent.flags_external import ExternalFlagRefused, check_external_argv
    from stokes_agent.lint_external import ProbedInstall

    install = ProbedInstall.from_lists(
        utilities=["blockMesh", "foamRun", "decomposePar"], solver_modules=["fluid"]
    )

    def check(argv: Sequence[str]) -> str | None:
        try:
            check_external_argv(argv, install)
        except ExternalFlagRefused as exc:
            return exc.message
        return None

    text = (
        "runApplication blockMesh\n"
        "runApplication blockMesh -dict ../../etc/x\n"
        "runApplication decomposePar -case other\n"
        "runApplication foamRun -solver nope\n"
        "runApplication foamRun -solver fluid\n"
    )
    recipe = check_recipe(parse_allrun(text, "foamRun"), check)
    assert _argv(recipe) == [["blockMesh"], ["foamRun", "-solver", "fluid"]]
    assert [item.line for item in recipe.refused] == [2, 3, 4]


def test_stage_names() -> None:
    assert stage_of("snappyHexMesh", None) == "mesh"
    assert stage_of("potentialFoam", "foamRun") == "solve"
    assert stage_of("setFields", "foamRun") == "init"
    assert stage_of("myApp", "myApp") == "solve"
    assert Step("x", ("x",)).utility == "x"


def test_a_dict_from_the_shared_resources_is_copied_into_the_case() -> None:
    """`blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/pitzDaily` (found by the T-317
    sweep in two dozen tutorials): the resource is copied into `system/` and the step names it."""
    text = GUARDS + "runApplication blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/pitzDaily\n"
    recipe = parse_allrun(text + "runApplication foamRun\n", "foamRun").to_json()
    assert [step["argv"] for step in recipe["steps"]] == [
        ["blockMesh", "-dict", "system/pitzDaily"],
        ["foamRun"],
    ]
    assert recipe["resources"][0]["source"] == "resources/blockMesh/pitzDaily"
    assert recipe["resources"][0]["target"] == "system/pitzDaily"
    assert recipe["unparsed"] == []
    bad = parse_allrun(
        GUARDS + "runApplication blockMesh -dict $FOAM_TUTORIALS/resources/../x\n", None
    )
    assert bad.to_json()["unparsed"], "a resource path must stay under resources/"
