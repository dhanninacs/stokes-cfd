"""The first-run launcher: its library list matches pyproject.toml, and it knows a ready venv."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

from stokes import launcher

ROOT = Path(__file__).resolve().parent.parent


def _name(requirement: str) -> str:
    """The distribution name of a requirement string, without extras or version."""
    return re.split(r"[\[<>=!~ ]", requirement, maxsplit=1)[0].lower()


def test_requirements_match_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extras = project["optional-dependencies"]
    declared = {_name(r) for r in project["dependencies"]}
    for extra in ("ui", "server", "agent", "cad"):
        declared |= {_name(r) for r in extras[extra]}
    installed = {_name(r) for r in launcher.REQUIREMENTS}
    # cadquery-ocp is the distribution behind the `cad` extra; the launcher names it directly.
    assert installed == declared, (
        f"missing from launcher: {declared - installed}; extra: {installed - declared}"
    )


def test_venv_dir_follows_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert launcher.venv_dir() == tmp_path / "Stokes" / "venv"


def test_a_venv_is_ready_only_with_its_own_marker(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    python = launcher.venv_python(venv)
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    assert not launcher.is_ready(venv)  # no marker yet

    (venv / launcher.MARKER).write_text(launcher.requirements_digest() + "\n", encoding="utf-8")
    assert launcher.is_ready(venv)

    # A changed requirement list is not ready: the venv must be updated.
    assert not launcher.is_ready(venv, requirements=("numpy>=2.1",))


def test_a_marker_without_an_interpreter_is_not_ready(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / launcher.MARKER).write_text(launcher.requirements_digest() + "\n", encoding="utf-8")
    assert not launcher.is_ready(venv)


@pytest.mark.parametrize(
    ("version", "supported"), [((3, 12), False), ((3, 13), True), ((3, 14), True), ((3, 15), False)]
)
def test_python_versions_follow_requires_python(version: tuple[int, int], supported: bool) -> None:
    assert launcher.python_supported(version) is supported


def test_the_current_interpreter_is_supported_by_the_test_suite() -> None:
    # The project declares 3.13 and 3.14; a test run on another version would miss wheels silently.
    assert sys.version_info[:2] in ((3, 13), (3, 14))
