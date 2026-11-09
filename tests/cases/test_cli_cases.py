"""`stokes tutorial` and `stokes case` (T-316): the case verbs that need no engine.

Running a case needs OpenFOAM and is the engine test's
(`tests/engine/test_external_cavity_engine.py`); here the catalogue, the import and the edits go
through the CLI on a synthetic case (ADR-0029 decision 2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.cli.main import run

from .synthetic import HEADER, make_case

CONTROL = (
    "application foamRun;\nsolver incompressibleFluid;\nendTime 0.5;\n"
    "PISO\n{\n    nCorrectors 2;\n}\n"
)


def call(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any]:
    code = run(list(argv))
    out = capsys.readouterr().out.strip().splitlines()[-1]
    return code, json.loads(out)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(folder))
    return folder


def test_tutorials_cases_and_edits_from_the_cli(
    tmp_path: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "tutorials"
    case = make_case(
        root,
        "incompressibleFluid/cavity",
        CONTROL,
        files={"0/U": HEADER + "internalField uniform (0 0 0);\n"},
    )
    code, listed = call(capsys, "tutorial", "list", "--root", str(root))
    assert code == 0 and [c["id"] for c in listed["cases"]] == ["incompressibleFluid/cavity"]

    project = str(tmp_path / "demo.stokes")
    call(capsys, "new", project, "--name", "demo")
    code, imported = call(capsys, "case", "import", project, "--folder", str(case))
    assert code == 0 and imported["path"] == "/cases/cavity"

    code, edited = call(
        capsys, "case", "set", project, "/cases/cavity", "system/controlDict", "endTime", "2 [s]"
    )
    assert code == 0 and edited["value"] == {"quantity": "2 [s]", "si": 2.0}
    code, edited = call(
        capsys,
        "case",
        "set",
        project,
        "/cases/cavity",
        "system/controlDict",
        "PISO/nCorrectors",
        "3",
    )
    assert code == 0 and edited["value"] == 3 and edited["path"] == ["PISO", "nCorrectors"]

    code, shown = call(capsys, "case", "settings", project, "/cases/cavity", "system/controlDict")
    rows = {"/".join(r["path"]): r for r in shown["rows"] if r["kind"] == "entry"}
    assert rows["endTime"]["text"] == "2" and rows["PISO/nCorrectors"]["overridden"] is True

    code, diff = call(capsys, "case", "diff", project, "/cases/cavity")
    assert "+endTime 2;" in diff["files"][0]["diff"], "the edits were saved with the project"

    code, reset = call(
        capsys, "case", "reset", project, "/cases/cavity", "system/controlDict", "endTime"
    )
    assert code == 0 and reset["removed"] == 1
    code, refused = call(
        capsys, "case", "set", project, "/cases/cavity", "system/controlDict", "x", "#codeStream"
    )
    assert code == 1 and refused["error"]["code"] == "case.override_value"
