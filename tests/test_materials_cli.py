"""``stokes material``: the library verbs (providers, add, set-state, refit, check) and the
built-in ones, JSON out, the project saved after the verbs that change it, typed errors with exit
code 1. The CoolProp flow is skipped without CoolProp."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.cli.main import run
from tests.test_materials_support import (  # noqa: F401 - fixtures
    needs_coolprop,
    private_home,
    warm_worker,
)


def call(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any]:
    code = run(list(argv))
    out = capsys.readouterr().out.strip().splitlines()[-1]
    return code, json.loads(out)


def test_cli_builtin_materials(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = str(tmp_path / "demo")
    call(capsys, "new", project)
    code, added = call(capsys, "material", "add", "air", "--project", project)
    assert code == 0 and added["record"] == "air"
    code, made = call(
        capsys, "material", "create", "block", "--kind", "solid", "--project", project
    )
    assert code == 0 and made["params"]["kind"] == "solid"
    code, listed = call(capsys, "material", "list", "--project", project)
    assert code == 0 and [m["name"] for m in listed] == ["air", "block"]  # saved after each
    code, check = call(capsys, "material", "check", "air", "--project", project)
    assert code == 0 and check["ok"] is True
    code, state = call(
        capsys, "material", "set-state", "block", "--T-max", "500 K", "--project", project
    )
    assert code == 0 and state["params"]["T_max"]["value"] == 500.0
    code, refused = call(capsys, "material", "refit", "air", "--project", project)
    assert code == 1 and refused["error"]["code"] == "validation"  # a built-in record
    code, error = call(capsys, "material", "check", "air")
    assert code == 1 and "needs a project" in error["error"]["message"]


@needs_coolprop
@pytest.mark.usefixtures("warm_worker", "private_home")
def test_cli_coolprop_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = str(tmp_path / "demo")
    call(capsys, "new", project)
    code, providers = call(capsys, "material", "providers")
    assert code == 0 and {p["name"] for p in providers} >= {"builtin", "coolprop", "cantera"}
    code, added = call(
        capsys,
        "material",
        "add",
        "Water",
        "--provider",
        "coolprop",
        "--T-min",
        "280",
        "--T-max",
        "97 degC",
        "--project",
        project,
    )
    assert code == 0, added
    assert added["fit"]["viscosity"]["method"] == "log_polynomial"
    assert added["params"]["T_max"]["value"] == pytest.approx(370.15)
    code, refit = call(
        capsys,
        "material",
        "refit",
        "Water",
        "--properties",
        "density",
        "--methods",
        '{"density": "constant"}',
        "--project",
        project,
    )
    assert code == 0 and refit["fit"]["density"]["method"] == "constant"
    code, state = call(
        capsys, "material", "set-state", "Water", "--T-max", "350", "--project", project
    )
    assert code == 0 and len(state["fit"]) == 4
    assert state["fit"]["density"] == {
        **state["fit"]["density"],
        "method": "constant",
        "forced": True,
    }
    code, listed = call(capsys, "material", "list", "--project", project)
    assert code == 0 and listed[0]["detached"] == [] and listed[0]["status"] == "ok"
    code, error = call(
        capsys, "material", "add", "Unobtainium", "--provider", "coolprop", "--project", project
    )
    assert code == 1 and error["error"]["code"] == "materials.fluid_unknown"
