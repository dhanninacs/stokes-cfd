"""`tutorial.open` (T-312): a tutorial in a new project of its own, as one command.

Every case is synthetic and written by the test (ADR-0029 decision 2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session, replay
from stokes.api.journal import Journal
from stokes.errors import StokesError

from .synthetic import GUARDS, HEADER, make_case
from .test_import import _probe
from .test_tutorial_commands import FakeRunner


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(folder))
    return folder


@pytest.fixture
def tutorials(tmp_path: Path) -> Path:
    root = tmp_path / "tutorials"
    make_case(
        root,
        "incompressibleFluid/cavity",
        allrun=GUARDS + "runApplication blockMesh\nrunApplication $(getApplication)\n",
        files={"constant/physicalProperties": HEADER + "nu 0.01;\n"},
    )
    make_case(
        root,
        "incompressibleFluid/coded-case",
        files={"system/fvSolution": HEADER + "x #codeStream { code #{ #}; };\n"},
    )
    return root


def _session(tutorials: Path, journal: Path | None = None) -> Session:
    from stokes.run.hosts import HostRegistry

    session = Session(journal_path=journal)
    HostRegistry.of(session)._runners["local"] = FakeRunner(_probe(tutorials))  # type: ignore[assignment]
    return session


def test_a_tutorial_opens_in_a_new_project_named_after_it(home: Path, tutorials: Path) -> None:
    session = _session(tutorials)
    reply = session.execute("tutorial.open", tutorial="incompressibleFluid/cavity")
    assert reply["project"]["name"] == "cavity"
    assert Path(reply["project"]["path"]).name == "cavity.stokes"
    assert reply["case"]["status"] == "ready" and reply["case"]["path"] == "/cases/cavity"
    assert session.execute("case.get", node="/cases/cavity")["status"] == "ready"
    recent = session.execute("project.recent")["projects"]
    assert recent[0]["path"] == reply["project"]["path"], "it is remembered like any project"

    again = session.execute("tutorial.open", tutorial="incompressibleFluid/cavity")
    assert again["project"]["name"] == "cavity_2", "a second open never writes over the first"
    assert again["project"]["path"] != reply["project"]["path"]


def test_an_opened_tutorial_is_saved_so_reopening_it_keeps_the_case(
    home: Path, tutorials: Path
) -> None:
    """Measured in the app: a tutorial reopened from Recent came back empty ("Import CAD file"),
    because the project was written by `project.new` before the case was imported."""
    session = _session(tutorials)
    reply = session.execute("tutorial.open", tutorial="incompressibleFluid/cavity")
    other = _session(tutorials)
    other.execute("project.open", path=reply["project"]["path"])
    assert other.execute("case.get", node="/cases/cavity")["status"] == "ready"


def test_a_refused_case_leaves_nothing_behind_and_says_why(home: Path, tutorials: Path) -> None:
    from stokes.model.browse import projects_folder
    from stokes.model.recent import read_recent

    session = _session(tutorials)
    reply = session.execute("tutorial.open", tutorial="incompressibleFluid/coded-case")
    assert reply["project"] is None
    assert reply["case"]["status"] == "refused" and reply["case"]["refusals"]
    # The project made for it is closed, its folder removed and its Recent row dropped.
    assert session.project is None
    assert not any(p.name.startswith("coded_case") for p in projects_folder().glob("*"))
    assert not any("coded_case" in str(entry) for entry in read_recent())


def test_an_unknown_tutorial_is_refused_before_a_project_exists(
    home: Path, tutorials: Path
) -> None:
    session = _session(tutorials)
    with pytest.raises(StokesError):
        session.execute("tutorial.open", tutorial="incompressibleFluid/absent")


def test_the_journal_replays_the_same_project_and_case(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    journal = tmp_path / "journal.jsonl"
    session = _session(tutorials, journal=journal)
    reply = session.execute("tutorial.open", tutorial="incompressibleFluid/cavity")
    (record,) = [r for r in Journal.read(journal) if r["command"] == "tutorial.open"]
    assert record["params"]["path"] == reply["project"]["path"]
    assert record["params"]["node_id"] == reply["case"]["node_id"]
    assert record["params"]["snapshot"] == reply["case"]["snapshot"]

    elsewhere = tmp_path / "elsewhere" / "cavity.stokes"
    moved = {**record, "params": {**record["params"], "path": str(elsewhere)}}
    other = _session(tutorials)
    replay(other, [moved])
    case = other.execute("case.get", node="/cases/cavity")
    assert case["id"] == reply["case"]["node_id"] and case["snapshot"] == reply["case"]["snapshot"]
