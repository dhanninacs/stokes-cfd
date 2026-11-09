"""`case.settings`, `case.set`, `case.reset`, `case.diff`, `case.preview`, `case.boundary` (T-308).

Every case is synthetic and written by the test (ADR-0029 decision 2). The copy in the project is
never written: edits live in the node's `overrides`, and the staged files are computed from both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session, replay
from stokes.api.journal import Journal
from stokes.errors import StokesError

from .synthetic import GUARDS, HEADER, make_case
from .test_import import CAVITY_FILES, _home, _probe, _session

CONTROL = (
    "application foamRun;\nsolver incompressibleFluid;\n"
    "startTime 0;\nendTime 0.5;\ndeltaT 0.005;\nPISO\n{\n    nCorrectors 2;\n}\n"
)
FILES = {
    **CAVITY_FILES,
    "0/p": HEADER + "internalField uniform 0;\nboundaryField\n{\n    lid\n    {\n"
    "        type zeroGradient;\n    }\n    walls\n    {\n        type zeroGradient;\n    }\n}\n",
}


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
        CONTROL,
        allrun=GUARDS + "runApplication blockMesh\nrunApplication $(getApplication)\n",
        files=FILES,
    )
    return root


def _imported(tmp_path: Path, tutorials: Path, journal: Path | None = None) -> Session:
    session = _session(tmp_path, _probe(tutorials), journal=journal)
    session.execute("case.import", tutorial="incompressibleFluid/cavity")
    return session


def _rows(session: Session, file: str) -> dict[tuple[str, ...], dict[str, object]]:
    reply = session.execute("case.settings", node="/cases/cavity", file=file)
    return {tuple(row["path"]): row for row in reply["rows"] if row["kind"] == "entry"}


def test_settings_show_the_file_with_units_on_known_keys(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session = _imported(tmp_path, tutorials)
    found = _rows(session, "system/controlDict")
    assert found[("endTime",)]["text"] == "0.5"
    assert found[("endTime",)]["dimension"] == "time" and found[("endTime",)]["unit"] == "s"
    assert found[("PISO", "nCorrectors")]["text"] == "2"
    assert "dimension" not in found[("PISO", "nCorrectors")]
    assert not any(row["overridden"] for row in found.values())
    nu = _rows(session, "constant/physicalProperties")[("nu",)]
    assert nu["dimension"] == "kinematic_viscosity"


def test_set_a_quantity_stages_si_and_only_that_value(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session = _imported(tmp_path, tutorials)
    reply = session.execute(
        "case.set",
        node="/cases/cavity",
        file="system/controlDict",
        path=["endTime"],
        value="500 [ms]",
    )
    assert reply["value"] == {"quantity": "500 [ms]", "si": 0.5} and reply["text"] == "0.5"
    session.execute(
        "case.set", node="/cases/cavity", file="system/controlDict", path=["endTime"], value="2 [s]"
    )
    row = _rows(session, "system/controlDict")[("endTime",)]
    assert row["text"] == "2" and row["overridden"] is True and row["original"] == "0.5"
    case = session.execute("case.get", node="/cases/cavity")
    assert len(case["overrides"]) == 1, "setting a key again replaces its edit"

    diff = session.execute("case.diff", node="/cases/cavity")["files"]
    assert [item["file"] for item in diff] == ["system/controlDict"]
    changed = [line for line in diff[0]["diff"].splitlines() if line[:1] in "+-"]
    assert changed == [
        "--- a/system/controlDict",
        "+++ b/system/controlDict",
        "-endTime 0.5;",
        "+endTime 2;",
    ]

    copy = _home(session) / "cases" / case["id"] / "source" / "system" / "controlDict"
    assert b"endTime 0.5;" in copy.read_bytes(), "the copy itself is never written"


def test_typed_tokens_for_other_keys_and_refusals(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session = _imported(tmp_path, tutorials)
    session.execute(
        "case.set",
        node="/cases/cavity",
        file="0/U",
        path=["boundaryField", "lid", "value"],
        value={"tokens": ["uniform", [2, 0, 0]]},
    )
    grid = session.execute("case.boundary", node="/cases/cavity")
    assert grid["folder"] == "0" and grid["fields"] == ["U", "p"]
    assert grid["patches"] == ["lid", "walls"]
    assert grid["grid"]["U"]["lid"] == {"type": "fixedValue", "value": "uniform (2 0 0)"}
    assert grid["grid"]["p"]["walls"] == {"type": "zeroGradient"}

    refusals: list[tuple[dict[str, Any], str]] = [
        (
            {"file": "system/controlDict", "path": ["endTime"], "value": "3 [m]"},
            "case.override_unit",
        ),
        (
            {"file": "system/controlDict", "path": ["PISO", "nCorrectors"], "value": "2 [s]"},
            "case.override_value",
        ),
        ({"file": "system/controlDict", "path": ["PISO"], "value": 1}, "case.override_block"),
        (
            {"file": "system/controlDict", "path": ["x"], "value": "#codeStream"},
            "case.override_value",
        ),
        ({"file": "../outside", "path": ["x"], "value": 1}, "case.override_file"),
        ({"file": "system/absent", "path": ["x"], "value": 1}, "case.file_missing"),
        (
            {
                "file": "system/controlDict",
                "path": ["PISO", "x"],
                "value": {"quantity": "1 [s]", "si": 1},
            },
            "case.override_unit",
        ),
    ]
    for params, code in refusals:
        with pytest.raises(StokesError) as caught:
            session.execute("case.set", node="/cases/cavity", **params)
        assert caught.value.code == code, (params, caught.value.code)
    assert len(session.execute("case.get", node="/cases/cavity")["overrides"]) == 1


def test_undo_redo_reset_and_save(tmp_path: Path, home: Path, tutorials: Path) -> None:
    session = _imported(tmp_path, tutorials)
    set_ = {"node": "/cases/cavity", "file": "system/controlDict"}
    session.execute("case.set", **set_, path=["endTime"], value=1)
    session.execute("case.set", **set_, path=["deltaT"], value="1 [ms]")
    assert _rows(session, "system/controlDict")[("deltaT",)]["text"] == "0.001"
    session.execute("history.undo")
    assert _rows(session, "system/controlDict")[("deltaT",)]["text"] == "0.005"
    session.execute("history.redo")
    assert _rows(session, "system/controlDict")[("deltaT",)]["text"] == "0.001"

    assert session.execute("case.reset", **set_, path=["deltaT"])["removed"] == 1
    assert session.execute("case.reset", **set_, path=["deltaT"])["removed"] == 0
    assert _rows(session, "system/controlDict")[("endTime",)]["text"] == "1"

    session.execute("project.save")
    fresh = Session()
    fresh.execute("project.open", path=str(_home(session)))
    assert _rows(fresh, "system/controlDict")[("endTime",)]["text"] == "1"
    assert fresh.execute("case.reset", **set_)["removed"] == 1
    assert fresh.execute("case.diff", node="/cases/cavity") == {"files": []}


def test_preview_lists_and_shows_the_staged_case(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session = _imported(tmp_path, tutorials)
    session.execute(
        "case.set", node="/cases/cavity", file="system/controlDict", path=["endTime"], value=3
    )
    listing = session.execute("case.preview", node="/cases/cavity")
    assert listing["writer"] == "of14-external/1"
    flags = {row["path"]: row["overridden"] for row in listing["files"]}
    assert flags["system/controlDict"] is True and flags["0/U"] is False
    assert "Allrun" not in flags, "scripts are never staged"
    shown = session.execute("case.preview", node="/cases/cavity", file="system/controlDict")
    assert "endTime 3;" in shown["text"] and shown["overridden"] is True


def test_the_staged_files_for_the_executor(tmp_path: Path, home: Path, tutorials: Path) -> None:
    from stokes.cases.overrides import staged

    session = _imported(tmp_path, tutorials)
    session.execute(
        "case.set", node="/cases/cavity", file="system/controlDict", path=["endTime"], value=3
    )
    target = session.require_project().get("/cases/cavity")
    found = staged(_home(session), target.id, target.params["overrides"])
    assert list(found) == ["system/controlDict"] and b"endTime 3;" in found["system/controlDict"]


def test_journal_replay_gives_the_same_edits(tmp_path: Path, home: Path, tutorials: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    session = _imported(tmp_path, tutorials, journal=journal)
    session.execute(
        "case.set",
        node="/cases/cavity",
        file="system/controlDict",
        path=["endTime"],
        value="0.25 [s]",
    )
    session.execute(
        "case.set",
        node="/cases/cavity",
        file="0/U",
        path=["boundaryField", "lid", "type"],
        value="noSlip",
    )
    records = [r for r in Journal.read(journal) if r["command"].startswith("case.")]
    assert [r["command"] for r in records] == ["case.import", "case.set", "case.set"]
    other = _session(tmp_path, _probe(tutorials), project="other")
    replay(other, records)
    assert (
        other.execute("case.get", node="/cases/cavity")["overrides"]
        == session.execute("case.get", node="/cases/cavity")["overrides"]
    )


def test_a_hand_edited_copy_is_refused_not_staged(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    import stat

    session = _imported(tmp_path, tutorials)
    node_id = session.execute("case.get", node="/cases/cavity")["id"]
    copy = _home(session) / "cases" / node_id / "source" / "system" / "controlDict"
    copy.chmod(stat.S_IWRITE | stat.S_IREAD)
    copy.write_bytes(copy.read_bytes().replace(b"0.5", b"9.5"))
    with pytest.raises(StokesError) as caught:
        session.execute("case.settings", node="/cases/cavity", file="system/controlDict")
    assert caught.value.code == "case.store"
