"""``stokes study create|generate|update|results|pareto``: JSON out, the project saved after the
commands that change it, typed errors with exit code 1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.cli.main import run
from tests.test_study_support import dp


def call(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any]:
    code = run(list(argv))
    out = capsys.readouterr().out.strip().splitlines()[-1]
    return code, json.loads(out)


def api(capsys: pytest.CaptureFixture[str], project: str, command: str, **params: Any) -> Any:
    code, out = call(capsys, "call", command, "--project", project, "--json", json.dumps(params))
    assert code == 0, out
    return out


def test_cli_study_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = str(tmp_path / "demo")
    call(capsys, "new", project)
    call(capsys, "param", project, "U_in", "velocity", "2 [m/s]")
    call(capsys, "param", project, "rho", "density", "998 [kg/m**3]")
    call(capsys, "param", project, "dp_dyn", "pressure", "0.5 * rho * U_in**2")
    code, created = call(capsys, "study", "create", project, "sweep", "--description", "speeds")
    assert code == 0 and created["path"] == "/studies/sweep"
    assert created["params"]["description"] == "speeds"
    api(capsys, project, "study.add_variable", study="sweep", parameter="U_in", values=[1, 2, 3])
    api(capsys, project, "study.add_response", study="sweep", expression="dp_dyn")
    code, generated = call(capsys, "study", "generate", project, "sweep")
    assert code == 0 and generated["points"] == 3
    code, partial = call(capsys, "study", "update", project, "/studies/sweep", "--points", "0,2")
    assert code == 0 and [p["index"] for p in partial["points"]] == [0, 2]
    code, rest = call(capsys, "study", "update", project, "sweep")
    assert code == 0 and rest["evaluated"] == 1 and rest["status"] == "done"
    code, results = call(capsys, "study", "results", project, "sweep")
    assert code == 0 and [p["outputs"]["dp_dyn"] for p in results["points"]] == pytest.approx(
        [dp(1.0), dp(2.0), dp(3.0)]
    )
    code, table = call(capsys, "study", "results", project, "sweep", "--format", "csv")
    assert code == 0 and table["text"].splitlines()[0].startswith("index,status,U_in [m/s]")
    code, info = call(capsys, "get", project, "/studies/sweep")
    assert info["params"]["status"] == "done"  # the CLI saved the project after the update
    api(capsys, project, "study.set_objective", study="sweep", response="dp_dyn")
    code, front = call(capsys, "study", "pareto", project, "sweep")
    assert code == 0 and front["front"] == [0]


def test_cli_study_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = str(tmp_path / "demo")
    call(capsys, "new", project)
    code, out = call(capsys, "study", "generate", project, "missing")
    assert code == 1 and out["error"]["code"] == "model.unknown_node"
    call(capsys, "study", "create", project, "sweep")
    code, out = call(capsys, "study", "generate", project, "sweep")
    assert code == 1 and out["error"]["code"] == "explore.study_definition"
    code, out = call(capsys, "study", "update", project, "sweep", "--points", "first")
    assert code == 1 and "--points" in out["error"]["message"]
    code, out = call(capsys, "study", "create", project, "doe", "--type", "random")
    assert code == 1 and out["error"]["code"] == "validation"
