"""What stopped ballValve from loading: argument allow-lists and the case's own function files."""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes_agent.flags_external import ExternalFlagRefused, check_external_argv
from stokes_agent.lint_external import (
    ProbedInstall,
    case_functions,
    lint_file_external,
)

INSTALL = ProbedInstall.from_lists(
    utilities=["potentialFoam", "transformPoints", "createNonConformalCouples"],
    etc_functions=["patchFlowRate"],
)


@pytest.mark.parametrize(
    "argv",
    [
        ["potentialFoam", "-pName", "p_rgh"],
        ["transformPoints", "-pointZone", "ball", "Rz=-45"],
        ["createNonConformalCouples", "pipeNonCouple", "ballNonCouple"],
    ],
)
def test_the_ball_valve_steps_are_allowed(argv: list[str]) -> None:
    check_external_argv(argv, INSTALL)


@pytest.mark.parametrize(
    "argv",
    [
        ["potentialFoam", "-pName", "../p"],
        ["transformPoints", "-pointZone", "$(evil)", "Rz=-45"],
        ["createNonConformalCouples", "a", "b", "c"],
    ],
)
def test_their_values_are_still_plain_names(argv: list[str]) -> None:
    with pytest.raises(ExternalFlagRefused):
        check_external_argv(argv, INSTALL)


def write_case(root: Path, functions: str, own: bool) -> Path:
    system = root / "system"
    system.mkdir(parents=True)
    (system / "functions").write_text(functions)
    if own:
        (system / "wallsSurface").write_text("type surfaces;\n")
    return system / "functions"


def test_a_function_the_case_ships_itself_is_accepted(tmp_path: Path) -> None:
    path = write_case(tmp_path / "a", "#includeFunc wallsSurface\n", own=True)
    assert "wallsSurface" in case_functions(tmp_path / "a")
    assert lint_file_external("system/functions", path, INSTALL) == []


def test_a_function_neither_the_case_nor_the_install_has_is_refused(tmp_path: Path) -> None:
    path = write_case(tmp_path / "b", "#includeFunc wallsSurface\n", own=False)
    found = lint_file_external("system/functions", path, INSTALL)
    assert [f.why for f in found] == ["the function is not one this OpenFOAM install provides"]
