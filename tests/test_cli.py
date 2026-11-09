from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.cli.main import run
from stokes.model.project import SKELETON

PARAMS = "/physics/parameters"
CUBE = "/geometry/parts/cube"


def call(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any]:
    code = run(list(argv))
    out = capsys.readouterr().out.strip().splitlines()[-1]
    return code, json.loads(out)


def why(capsys: pytest.CaptureFixture[str], code: int, payload: Any) -> str:
    """What the CLI said, for an assertion message.

    A failed verb exits non-zero and puts the reason in the payload's ``error``; stderr is where a
    crash or a warning lands. This test is the one entry on the board that has flaked (T-094): it
    passed alone and with its whole file, and failed once in a full run, and the bare
    ``assert code == 0`` threw away the only two things that would have placed it. A failure that
    cannot describe itself costs a whole investigation, so the reason goes in the message.
    """
    err = capsys.readouterr().err.strip()
    parts = [f"exit {code}", f"payload {json.dumps(payload)[:400]}"]
    if err:
        parts.append(f"stderr {err[-400:]}")
    return " | ".join(parts)


def test_cli_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = str(tmp_path / "demo")
    code, info = call(capsys, "new", project, "--name", "demo")
    assert code == 0 and info["name"] == "demo"
    code, _ = call(capsys, "param", project, "U_in", "velocity", "2 [m/s]")
    assert code == 0
    code, _ = call(capsys, "param", project, "dp", "pressure", "0.5 * 1.2 [kg/m**3] * U_in**2")
    assert code == 0
    code, got = call(capsys, "get", project, f"{PARAMS}/dp", "value")
    assert code == 0 and got["value"] == pytest.approx(2.4)
    code, _ = call(capsys, "set", project, f"{PARAMS}/U_in", "value", "3 [m/s]")
    assert code == 0
    code, got = call(capsys, "get", project, f"{PARAMS}/dp", "value", "--unit", "kPa")
    assert got["value"] == pytest.approx(5.4) and got["display"] == "0.0054 kPa"
    code, tree = call(capsys, "tree", project, "--depth", "1")
    assert code == 0 and len(tree["children"]) == len(SKELETON)
    code, added = call(capsys, "add", project, "/geometry", "group", "parts")
    assert code == 0 and added["path"] == "/geometry/parts"
    code, params = call(capsys, "params", project)
    assert [p["name"] for p in params] == ["U_in", "dp"]
    code, schema = call(capsys, "schema", "commands")
    assert code == 0 and any(c["name"] == "parameter.set" for c in schema)
    code, version = call(capsys, "schema", "version")
    assert code == 0 and "stokes" in version


def test_cli_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = str(tmp_path / "demo")
    call(capsys, "new", project)
    code, out = call(capsys, "get", project, "/nowhere")
    assert code == 1 and out["error"]["code"] == "model.unknown_node"
    code, out = call(capsys, "set", project, f"{PARAMS}", "value", "1")
    assert code == 1 and out["error"]["code"] == "model.unknown_parameter"
    code, out = call(capsys, "call", "node.get", "--project", project, "--json", '{"node": "/"}')
    assert code == 0 and out["type"] == "project"
    code, out = call(capsys, "call", "node.get", "--project", project, "--json", "[1]")
    assert code == 1


def test_cli_units_convert(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = call(capsys, "units", "convert", "25", "degC", "K")
    assert code == 0 and out["value"] == pytest.approx(298.15)
    assert out["factor"] == pytest.approx(1.0) and out["offset"] == pytest.approx(273.15)
    code, out = call(capsys, "units", "convert", "0", "m", "mm")
    assert code == 0 and out["value"] == 0.0 and out["factor"] == 1000.0 and out["offset"] == 0.0
    code, out = call(capsys, "units", "convert", "1", "m", "s")
    assert code == 1 and out["error"]["code"] == "units.convert"


def words(capsys: pytest.CaptureFixture[str], line: str, *more: str) -> tuple[int, Any]:
    """``call`` with the words of ``line``, then ``more`` as they are (a path, a quantity)."""
    return call(capsys, *line.split(), *more)


def project_with_a_cube(tmp_path: Path) -> str:
    """A saved project holding one meshed cube part, built without OCP: a synthetic cube (6
    faces, 12 triangles) written as the part's mesh cache, as a finished import leaves it."""
    from stokes.geometry.cache import write_cache
    from stokes.geometry.transport import pack_part
    from tests.selection_support import add_part, cube, new_session, part

    session = new_session(tmp_path, "demo")
    mesh = part(cube(0.01, name="cube"))
    part_id = add_part(session, "cube", mesh)
    folder = session.project_path
    assert folder is not None
    write_cache(folder, part_id, pack_part(mesh))
    session.execute("project.save")
    return str(folder)


def test_cli_selection_verbs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``tag create|list|add|grow|set|invert|members|by-name|rename|delete`` on a project
    with one part, one command per verb; the project is saved after the verbs that change it."""
    project = project_with_a_cube(tmp_path)
    members = json.dumps([{"part": CUBE, "faces": [3]}])
    code, created = words(capsys, "tag create walls --project", project, "--members", members)
    assert code == 0, created
    assert created["path"] == "/geometry/tags/walls"
    assert created["kind"] == "boundary" and created["faces"] == 1
    code, listed = words(capsys, "tag list --project", project)
    assert code == 0 and [s["name"] for s in listed] == ["walls"]
    code, added = words(
        capsys,
        "tag add walls --project",
        project,
        json.dumps([{"part": CUBE, "faces": [0]}]),
    )
    assert code == 0 and added["faces"] == 2 and added["changed"] is True
    code, grown = words(capsys, "tag grow walls --angle-deg 95 --project", project)
    assert code == 0 and grown["faces"] == 6 and grown["added"] == 4
    code, replaced = words(
        capsys,
        "tag set walls --project",
        project,
        json.dumps([{"part": CUBE, "faces": [1, 2]}]),
    )
    assert code == 0 and replaced["faces"] == 2
    code, inverted = words(capsys, "tag invert walls --project", project, f'["{CUBE}"]')
    assert code == 0 and inverted["faces"] == 4
    assert inverted["members"][0]["faces"] == [[0, 0], [3, 5]]
    code, expanded = words(capsys, "tag members walls --project", project)
    assert code == 0 and expanded["members"][0]["faces"] == [0, 3, 4, 5]
    code, found = words(capsys, "tag by-name cu* --project", project)
    assert code == 0 and found["count"] == 1 and found["members"][0]["faces"] == "all"
    code, renamed = words(capsys, "tag rename walls inlet --project", project)
    assert code == 0 and renamed["path"] == "/geometry/tags/inlet"
    code, listed = words(capsys, "tag list --project", project)  # rename saved the project
    assert code == 0 and [s["name"] for s in listed] == ["inlet"]
    code, deleted = words(capsys, "tag delete inlet --project", project)
    assert code == 0 and deleted["deleted"] is True
    code, listed = words(capsys, "tag list --project", project)
    assert code == 0 and listed == []


def test_cli_selection_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = str(tmp_path / "empty")
    call(capsys, "new", project)
    code, out = words(capsys, "tag list")
    assert code == 1 and "--project" in out["error"]["message"]
    code, out = words(capsys, "tag create walls --project", project, "--members", "not json")
    assert code == 1 and "--members" in out["error"]["message"]
    code, out = words(capsys, "tag set walls [1] --project", project)
    assert code == 1 and out["error"]["code"] == "model.unknown_node"


def test_cli_material_library_queries_need_no_project(capsys: pytest.CaptureFixture[str]) -> None:
    """The query is the first positional: it used to be taken for the project."""
    code, found = words(capsys, "material library-search nitrogen --providers builtin")
    assert code == 0 and found["total"] >= 1 and found["records"][0]["fluid"] == "nitrogen"
    code, solids = words(
        capsys, "material library-search --kind solid --limit 3 --providers builtin"
    )
    assert code == 0 and len(solids["records"]) == 3 and solids["total"] >= 3
    code, info = words(capsys, "material library-info nitrogen")
    assert code == 0 and "density" in info["methods"]


def test_cli_material_commands_that_need_a_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = str(tmp_path / "demo")
    call(capsys, "new", project)
    code, out = words(capsys, "material list")
    assert code == 1 and "--project" in out["error"]["message"]
    code, created = words(capsys, "material create mine --project", project)
    assert code == 0 and created["name"] == "mine"
    code, listed = words(capsys, "material list --project", project)
    assert code == 0 and [m["name"] for m in listed] == ["mine"]  # create saved the project
    add = '{"fluid": "air"}'
    code, _ = words(capsys, "call material.add_from_library --project", project, "--json", add)
    assert code == 0
    # The arguments reach the command as its properties (a list), T (a list) and p.
    code, result = words(
        capsys,
        "material evaluate air density --temperature 300 --project",
        project,
        "--pressure",
        "2 [bar]",
    )
    asked = '{"material": "air", "properties": ["density"], "T": [300], "p": "2 [bar]"}'
    code_api, expected = words(capsys, "call material.evaluate --project", project, "--json", asked)
    assert code == 0 and code_api == 0 and result == expected
    assert list(result["properties"]) == ["density"] and result["T"] == [300.0]
    assert result["p"] == pytest.approx(2.0e5)
    code, out = words(capsys, "material evaluate air density")
    assert code == 1 and "--project" in out["error"]["message"]


def test_cli_boundary_and_region_verbs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """ADR-0022 decision 3.5, the brief's "done means": one command makes the typed region, its
    boundaries group and the boundary and turns the tag red; the bodies go on water; the tag in
    use is not deleted without --force; each verb that changes the project saves it."""
    project = project_with_a_cube(tmp_path)
    inlet = json.dumps([{"part": CUBE, "faces": [0]}])
    code, made = words(capsys, "tag create inlet --project", project, "--members", inlet)
    assert code == 0, why(capsys, code, made)
    velocity = json.dumps({"velocity": "2 [m/s]"})
    code, done = words(
        capsys,
        "boundary assign",
        project,
        "/geometry/tags/inlet",
        "velocity_inlet",
        "--values",
        velocity,
    )
    assert code == 0, done
    assert done["boundary"]["path"] == "/regions/fluid/boundaries/inlet"
    assert done["tag"]["color"] == "#d64f4f" and done["recoloured"] is True
    code, tree = words(capsys, "tree", project, "--root", "/regions")
    assert code == 0 and [(c["name"], c["type"]) for c in tree["children"]] == [("fluid", "region")]
    assert [c["name"] for c in tree["children"][0]["children"]] == ["boundaries"]
    outlet = json.dumps([{"part": CUBE, "faces": [1]}])
    code, out = words(
        capsys, "boundary assign", project, outlet, "pressure_outlet", "--name", "exit"
    )
    assert code == 0 and out["boundary"]["path"] == "/regions/fluid/boundaries/exit"
    code, made_water = words(capsys, "material create water --project", project)
    assert code == 0, why(capsys, code, made_water)
    code, filled = words(capsys, "region assign", project, "water", f'["{CUBE}"]')
    assert code == 0 and filled["region"]["name"] == "fluid"
    assert filled["region"]["material"]["name"] == "water" and filled["moved"] == [
        filled["bodies"][0]
    ]
    code, out = words(capsys, "tag delete inlet --project", project)
    assert code == 1 and out["error"]["code"] == "model.referenced"
    code, out = words(capsys, "rm", project, "/geometry/tags/inlet")
    assert code == 1 and out["error"]["code"] == "model.referenced"
    code, listed = words(capsys, "boundary list", project)
    assert code == 0 and [(b["name"], b["primary"]["display"]) for b in listed] == [
        ("inlet", "2 m/s"),
        ("exit", "0 Pa"),
    ]
    code, regions = words(capsys, "region list", project)
    assert code == 0 and regions[0]["uncovered_faces"] == 4
    code, changed = words(
        capsys,
        "boundary set",
        project,
        "/regions/fluid/boundaries/inlet",
        "--values",
        '{"velocity": 3}',
    )
    assert code == 0 and changed["primary"]["display"] == "3 m/s"
    code, kinds = words(capsys, "boundary kinds")
    assert code == 0 and kinds[0]["id"] == "velocity_inlet"
    code, out = words(capsys, "boundary assign", project, "inlet", "fan")
    assert code == 1 and out["error"]["code"] == "boundary.invalid_kind"
    code, out = words(capsys, "boundary assign", project, "inlet", "wall", "--values", "[1]")
    assert code == 1 and "--values" in out["error"]["message"]
    code, forced = words(capsys, "tag delete inlet --force --project", project)
    assert code == 0 and forced["removed"] == [done["boundary"]["id"]]
    code, deleted = words(capsys, "boundary delete", project, "exit", "--delete-tag")
    assert code == 1  # a boundary is named by its id or path
    code, deleted = words(
        capsys, "boundary delete", project, "/regions/fluid/boundaries/exit", "--delete-tag"
    )
    assert code == 0 and deleted["tag_deleted"] is True
    code, made = words(capsys, "region create", project, "sink", "--kind", "solid")
    assert code == 0 and made["kind"] == "solid"
    code, renamed = words(capsys, "region set", project, "sink", "--name", "heat_sink")
    assert code == 0 and renamed["path"] == "/regions/heat_sink"
    code, gone = words(capsys, "region delete", project, "heat_sink", "--delete-tag")
    assert code == 0 and gone["tag_deleted"] is True
    code, regions = words(capsys, "region list", project)
    assert code == 0 and [r["name"] for r in regions] == ["fluid"]


def test_cli_setup_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``stokes setup check`` prints the state, the next step and the findings, and exits 1 only
    while the set-up is incomplete (ADR-0022 decision 5.1)."""
    project = project_with_a_cube(tmp_path)
    code, out = words(capsys, "setup check", project)
    assert code == 0 and out["state"] == "not_defined"
    assert out["next"] and [f["code"] for f in out["findings"]] == ["setup.no_region"]
    inlet = json.dumps([{"part": CUBE, "faces": [0]}])
    code, _ = words(capsys, "tag create inlet --project", project, "--members", inlet)
    assert code == 0
    code, _ = words(
        capsys,
        "boundary assign",
        project,
        "/geometry/tags/inlet",
        "velocity_inlet",
        "--values",
        json.dumps({"velocity": "2 [m/s]"}),
    )
    assert code == 0
    code, _ = words(capsys, "material create water --project", project)
    assert code == 0
    code, _ = words(capsys, "region assign", project, "water", f'["{CUBE}"]')
    assert code == 0
    code, out = words(capsys, "setup check", project)
    assert code == 1 and out["state"] == "incomplete" and out["next"] == "add an outlet boundary"
    assert [f["code"] for f in out["findings"] if f["severity"] == "error"] == ["setup.no_outlet"]
    outlet = json.dumps([{"part": CUBE, "faces": [1]}])
    code, _ = words(capsys, "boundary assign", project, outlet, "pressure_outlet", "--name", "exit")
    assert code == 0
    code, out = words(capsys, "setup check", project)
    assert code == 0 and out["state"] == "ready" and out["next"] == "Set-up complete"
    assert all(f["severity"] == "info" for f in out["findings"])
    assert out["counts"]["boundaries"] == 2 and out["counts"]["uncovered_faces"] == 4
