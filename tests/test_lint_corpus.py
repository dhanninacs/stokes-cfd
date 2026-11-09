"""The lint-corpus tool's diff logic (M-504 sub-step 0).

The corpus run itself needs `$FOAM_TUTORIALS`, so what is unit-tested here is the part a
regression review relies on: **the diff** between two finding sets. New and lost are computed
per (file, token, why); a wording change in `why` counts as one new and one lost, because a
message that moved is a message a reviewer must look at, and silently ignoring it would hide
a real change behind a stable-looking count.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.lint_corpus import case_files, diff, relative


@pytest.fixture
def tutorials(tmp_path: Path) -> Path:
    """A tiny tutorial tree: one case with a system folder, so the file-set rules are exercised."""
    case = tmp_path / "tutorials" / "incompressibleFluid" / "demo"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text("application foamRun;\n")
    (case / "constant").mkdir()
    (case / "constant" / "polyMesh").mkdir()
    (case / "constant" / "polyMesh" / "faces").write_text("binary\n")
    (case / "0").mkdir()
    (case / "0" / "U").write_text("internalField uniform (0 0 0);\n")
    (case / "Allrun").write_text("blockMesh\n")  # outside the three folders: NOT in the set
    return tmp_path / "tutorials"


def test_the_file_set_is_the_three_folders_and_nothing_else(tutorials: Path) -> None:
    """`system/`, `constant/` and `0*/` are the staged folders; the case's helper scripts are not
    part of the corpus, and the run's own logs are not either."""
    files = case_files(tutorials)
    assert {f.name for f in files} == {"controlDict", "faces", "U"}


def test_the_set_is_sorted_by_full_path(tutorials: Path) -> None:
    """A stable order makes two runs' outputs comparable file by file."""
    found = case_files(tutorials)
    assert [f.name for f in found] == ["U", "faces", "controlDict"]


def test_relative_paths_keep_the_case_structure(tutorials: Path) -> None:
    found = {f.name: relative(f, tutorials) for f in case_files(tutorials)}
    assert found == {
        "U": "incompressibleFluid/demo/0/U",
        "faces": "incompressibleFluid/demo/constant/polyMesh/faces",
        "controlDict": "incompressibleFluid/demo/system/controlDict",
    }


def test_no_change_is_no_new_and_no_lost() -> None:
    old = {"a": [{"token": "t", "why": "w"}]}
    new, lost = diff(old, dict(old))
    assert new == {} and lost == {}


def test_a_new_finding_is_reported_by_file() -> None:
    old: dict[str, list[dict[str, str]]] = {}
    new_set = {"f": [{"token": "t", "why": "w"}]}
    new, lost = diff(old, new_set)
    assert new == new_set and lost == {}


def test_a_lost_finding_is_reported_by_file() -> None:
    new, lost = diff({"f": [{"token": "t", "why": "w"}]}, {})
    assert new == {} and lost == {"f": [{"token": "t", "why": "w"}]}


def test_a_wording_change_is_one_new_and_one_lost() -> None:
    """The why is part of the finding's identity: a message the lint module words differently is
    a change a reviewer sees, not a stable count that hides it."""
    old = {"f": [{"token": "t", "why": "the old words"}]}
    new_text = {"f": [{"token": "t", "why": "the new words"}]}
    new, lost = diff(old, new_text)
    assert new == new_text and lost == old


def test_the_same_token_in_two_files_is_two_findings() -> None:
    old: dict[str, list[dict[str, str]]] = {}
    new_set = {
        "f1": [{"token": "t", "why": "w"}],
        "f2": [{"token": "t", "why": "w"}],
    }
    new, lost = diff(old, new_set)
    assert set(new) == {"f1", "f2"} and lost == {}
