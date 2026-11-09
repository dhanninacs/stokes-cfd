"""`foam_case` and the `case.*` commands (T-307): import, read, remove, undo, save and replay.

Every case is synthetic and written by the test (ADR-0029 decision 2).
"""

from __future__ import annotations

import gzip
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session, replay
from stokes.api.journal import Journal
from stokes.errors import StokesError

from .synthetic import GUARDS, HEADER, INVENTORY, make_case
from .test_tutorial_commands import FakeRunner, _snapshot

CAVITY_FILES = {
    "0/U": HEADER + "internalField uniform (0 0 0);\nboundaryField\n{\n    lid\n    {\n"
    "        type fixedValue;\n        value uniform (1 0 0);\n    }\n}\n",
    "constant/physicalProperties": HEADER + "viscosityModel constant;\nnu 0.01;\n",
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
        allrun=GUARDS + "runApplication blockMesh\nrunApplication $(getApplication)\n",
        files=CAVITY_FILES,
    )
    return root


def _session(
    tmp_path: Path,
    record: dict[str, Any] | None,
    *,
    journal: Path | None = None,
    project: str = "proj",
) -> Session:
    from stokes.run.hosts import HostRegistry

    session = Session(journal_path=journal)
    HostRegistry.of(session)._runners["local"] = FakeRunner(record)  # type: ignore[assignment]
    session.execute("project.new", name="demo", path=str(tmp_path / project))
    return session


def _probe(root: Path | None) -> dict[str, Any]:
    return {**INVENTORY, "tutorials_root": str(root) if root else None}


def _home(session: Session) -> Path:
    assert session.project_path is not None
    return Path(session.project_path)


def test_import_a_tutorial_copies_hashes_and_never_writes_the_install(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    before = _snapshot(tutorials)
    session = _session(tmp_path, _probe(tutorials))
    reply = session.execute("case.import", tutorial="incompressibleFluid/cavity")
    assert reply["status"] == "ready", reply
    assert reply["path"] == "/cases/cavity" and reply["refusals"] == []
    assert len(reply["snapshot"]) == 64
    assert [step["argv"] for step in reply["recipe"]["steps"]] == [["blockMesh"], ["foamRun"]]
    assert _snapshot(tutorials) == before, "the install was written"

    copy = _home(session) / "cases" / reply["node_id"]
    assert (copy / "source" / "0" / "U").read_bytes() == (
        tutorials / "incompressibleFluid/cavity/0/U"
    ).read_bytes()
    assert (copy / "scripts" / "Allrun").is_file() and not (copy / "source" / "Allrun").exists()
    assert not os.access(copy / "source" / "0" / "U", os.W_OK), "the copy is not read-only"

    got = session.execute("case.get", node=reply["node_id"])
    assert got["source"] == {"kind": "install", "tutorial": "incompressibleFluid/cavity"}
    assert got["solver"] == "incompressibleFluid" and got["dialect"] == "of14"
    assert got["snapshot"] == reply["snapshot"] and got["overrides"] == []
    assert got["status"] == "ready" and got["regions"] is None

    files = {row["path"]: row for row in session.execute("case.files", node="/cases/cavity")}
    assert files["0/U"]["kind"] == "field" and files["0/U"]["parsed"] is True
    assert files["system/controlDict"]["kind"] == "dict"
    assert files["Allrun"] == {
        "path": "Allrun",
        "area": "scripts",
        "kind": "script",
        "size": files["Allrun"]["size"],
        "parsed": False,
    }


def test_the_same_case_twice_has_the_same_snapshot_and_a_new_name(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session = _session(tmp_path, _probe(tutorials))
    first = session.execute("case.import", tutorial="incompressibleFluid/cavity")
    second = session.execute("case.import", tutorial="incompressibleFluid/cavity")
    assert second["path"] == "/cases/cavity_2" and first["snapshot"] == second["snapshot"]
    with pytest.raises(StokesError) as caught:
        session.execute("case.import", tutorial="incompressibleFluid/cavity", name="cavity")
    assert caught.value.code == "case.name_taken"


def test_resources_named_by_allrun_are_copied_in(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "tutorials"
    allrun = (
        GUARDS + "cp $FOAM_TUTORIALS/resources/geometry/body.obj constant/geometry/\n"
        "ln -s $FOAM_TUTORIALS/resources/blocks system/blocks\n"
        "runApplication blockMesh\nrunApplication foamRun\n"
    )
    make_case(root, "incompressibleFluid/body", allrun=allrun)
    (root / "resources" / "geometry").mkdir(parents=True)
    (root / "resources" / "geometry" / "body.obj").write_text("v 0 0 0\n", encoding="utf-8")
    (root / "resources" / "blocks").mkdir()
    (root / "resources" / "blocks" / "a").write_text(HEADER + "x 1;\n", encoding="utf-8")
    session = _session(tmp_path, _probe(root))
    reply = session.execute("case.import", tutorial="incompressibleFluid/body")
    assert reply["status"] == "ready", reply
    source = _home(session) / "cases" / reply["node_id"] / "source"
    assert (source / "constant/geometry/body.obj").read_text(encoding="utf-8") == "v 0 0 0\n"
    assert (source / "system/blocks/a").is_file()


def test_resource_glob_copies_matching_geometry(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "tutorials"
    allrun = (
        GUARDS + "cp $FOAM_TUTORIALS/resources/geometry/pipe*.obj.gz constant/geometry/\n"
        "runApplication blockMesh\n"
    )
    make_case(root, "mesh/glob", allrun=allrun)
    shared = root / "resources" / "geometry"
    shared.mkdir(parents=True)
    for name in ("pipe.obj.gz", "pipeWall.obj.gz", "other.obj.gz"):
        (shared / name).write_bytes(gzip.compress(b"v 0 0 0\n"))
    session = _session(tmp_path, _probe(root))
    reply = session.execute("case.import", tutorial="mesh/glob")
    assert reply["status"] == "ready", reply
    folder = _home(session) / "cases" / reply["node_id"] / "source" / "constant/geometry"
    assert {item.name for item in folder.iterdir()} == {"pipe.obj.gz", "pipeWall.obj.gz"}


def test_resource_glob_with_no_matches_is_refused(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "tutorials"
    allrun = GUARDS + "cp $FOAM_TUTORIALS/resources/geometry/pipe*.obj.gz constant/geometry/\n"
    make_case(root, "mesh/glob", allrun=allrun)
    (root / "resources" / "geometry").mkdir(parents=True)
    session = _session(tmp_path, _probe(root))
    reply = session.execute("case.import", tutorial="mesh/glob")
    assert reply["status"] == "refused"
    assert "matched no files" in str(reply)


def test_resource_root_link_outside_tutorials_is_refused(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "tutorials"
    make_case(root, "mesh/link", allrun=GUARDS + "cp $FOAM_TUTORIALS/resources/x system/x\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x").write_text("x 1;\n", encoding="utf-8")
    try:
        (root / "resources").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating a directory symlink requires platform privileges")
    session = _session(tmp_path, _probe(root))
    reply = session.execute("case.import", tutorial="mesh/link")
    assert reply["status"] == "refused"
    assert "leads out of tutorials" in str(reply)


def test_a_missing_or_escaping_resource_refuses(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "tutorials"
    make_case(root, "a/needs", allrun="cp $FOAM_TUTORIALS/resources/absent x\n")
    session = _session(tmp_path, _probe(root))
    reply = session.execute("case.import", tutorial="a/needs")
    assert reply["status"] == "refused" and "not in the tutorials folder" in str(reply)
    folder = make_case(tmp_path, "loose", allrun="cp $FOAM_TUTORIALS/resources/x x\n")
    reply = session.execute("case.import", folder=str(folder))
    assert reply["status"] == "refused" and "needs the tutorials folder" in str(reply)


def test_shared_mesh_dictionary_copies_nested_includes(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "tutorials"
    allrun = GUARDS + "runApplication blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/main\n"
    make_case(root, "incompressibleFluid/shared", allrun=allrun)
    shared = root / "resources" / "blockMesh"
    shared.mkdir(parents=True)
    (shared / "main").write_text(HEADER + '#include "nested"\n', encoding="utf-8")
    (shared / "nested").write_text('#include "last"\n', encoding="utf-8")
    (shared / "last").write_text("x 1;\n", encoding="utf-8")
    session = _session(tmp_path, _probe(root))
    reply = session.execute("case.import", tutorial="incompressibleFluid/shared")
    assert reply["status"] == "ready", reply
    source = _home(session) / "cases" / reply["node_id"] / "source" / "system"
    assert (source / "nested").is_file()
    assert (source / "last").read_text(encoding="utf-8") == "x 1;\n"


def test_shared_mesh_dictionary_refuses_escaping_include(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "tutorials"
    allrun = GUARDS + "runApplication blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/main\n"
    make_case(root, "incompressibleFluid/shared", allrun=allrun)
    shared = root / "resources" / "blockMesh"
    shared.mkdir(parents=True)
    (shared / "main").write_text(HEADER + '#include "../secret"\n', encoding="utf-8")
    session = _session(tmp_path, _probe(root))
    reply = session.execute("case.import", tutorial="incompressibleFluid/shared")
    assert reply["status"] == "refused"
    assert "non-local include" in str(reply)


def test_shared_mesh_dictionary_lints_included_content(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "tutorials"
    allrun = GUARDS + "runApplication blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/main\n"
    make_case(root, "incompressibleFluid/shared", allrun=allrun)
    shared = root / "resources" / "blockMesh"
    shared.mkdir(parents=True)
    (shared / "main").write_text(HEADER + '#include "nested"\n', encoding="utf-8")
    # A `#calc` that is NOT plain arithmetic (another dictionary's entry), so it is still refused:
    # `1 + 1` is resolved in the copy since ADR-0033, and the point here is the included file.
    (shared / "nested").write_text('x #calc "$other.y + 1";\n', encoding="utf-8")
    session = _session(tmp_path, _probe(root))
    reply = session.execute("case.import", tutorial="incompressibleFluid/shared")
    assert reply["status"] == "refused"
    assert "system/nested" in str(reply)


def test_import_a_folder_uses_the_default_recipe(tmp_path: Path, home: Path) -> None:
    folder = make_case(tmp_path, "mine/pipe-flow.v2", files=CAVITY_FILES)
    (folder / "log.blockMesh").write_text("old log", encoding="utf-8")
    (folder / "processor0").mkdir()
    (folder / "processor0" / "junk").write_text("\x00", encoding="utf-8")
    (folder / ".hidden").write_text("x", encoding="utf-8")
    session = _session(tmp_path, _probe(None))
    reply = session.execute("case.import", folder=str(folder))
    assert reply["status"] == "ready" and reply["path"] == "/cases/pipe_flow_v2"
    assert reply["recipe"]["origin"] == "default"
    got = session.execute("case.get", node=reply["path"])
    assert got["source"] == {"kind": "folder", "path": str(folder.resolve())}
    paths = {row["path"] for row in session.execute("case.files", node=reply["path"])}
    assert paths == {
        "0/U",
        "constant/physicalProperties",
        "system/controlDict",
        "system/blockMeshDict",
    }


def test_a_case_failing_the_external_lint_is_refused_and_stages_nothing(
    tmp_path: Path, home: Path
) -> None:
    coded = HEADER + "boundaryField\n{\n    inlet\n    {\n        type codedFixedValue;\n    }\n}\n"
    folder = make_case(tmp_path, "bad", files={"0/U": coded})
    session = _session(tmp_path, _probe(None))
    reply = session.execute("case.import", folder=str(folder))
    assert reply["status"] == "refused" and reply["node_id"] is None
    assert reply["refusals"] and reply["refusals"][0]["file"] == "0/U"
    project = session.require_project()
    assert project.find("/cases") is None, "a refused case added nodes"
    cases = _home(session) / "cases"
    assert not cases.exists() or list(cases.iterdir()) == [], "a refused case left files"


def test_an_unprobed_install_imports_unverified_and_refuses_install_files(
    tmp_path: Path, home: Path
) -> None:
    folder = make_case(tmp_path, "plain")
    session = _session(tmp_path, None)
    assert session.execute("case.import", folder=str(folder))["status"] == "unverified"
    etc = make_case(
        tmp_path, "etc", 'application foamRun;\nsolver fluid;\n#includeEtc "caseDicts/x"\n'
    )
    reply = session.execute("case.import", folder=str(etc))
    assert reply["status"] == "refused", "an unprobed install allows no etc file"


def test_an_allrun_the_agent_would_refuse_needs_a_recipe(tmp_path: Path, home: Path) -> None:
    folder = make_case(
        tmp_path,
        "partial",
        allrun=GUARDS + "runApplication setFields\nfoo bar\nrunApplication foamRun\n",
    )
    session = _session(tmp_path, _probe(None))
    reply = session.execute("case.import", folder=str(folder))
    assert reply["status"] == "needs_recipe"
    recipe = reply["recipe"]
    assert [s["argv"] for s in recipe["steps"]] == [["foamRun"]]
    assert recipe["refused"][0]["text"] == "setFields" and recipe["unparsed"][0]["line"] == 6


def test_paths_that_escape_or_overlap_are_refused(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    session = _session(tmp_path, _probe(tutorials))
    make_case(tmp_path, "outside")
    codes = {
        "../outside": "case.source_invalid",
        "incompressibleFluid/../../outside": "case.source_invalid",
        "/abs/path": "case.source_invalid",
        "C:/x": "case.source_invalid",
        "incompressibleFluid/absent": "case.source_missing",
        "incompressibleFluid": "case.source_missing",
    }
    for tutorial, code in codes.items():
        with pytest.raises(StokesError) as caught:
            session.execute("case.import", tutorial=tutorial)
        assert caught.value.code == code, tutorial
    inside = make_case(_home(session), "inner")
    folders = {
        "relative/case": "case.source_relative",
        str(tmp_path / "absent"): "case.source_missing",
        str(tmp_path): "case.source_missing",
        str(inside): "case.source_in_project",
        "": "case.source_invalid",
    }
    for folder, code in folders.items():
        with pytest.raises(StokesError) as caught:
            session.execute("case.import", folder=folder)
        assert caught.value.code == code, folder
    for params in ({}, {"tutorial": "a/b", "folder": str(inside)}):
        with pytest.raises(StokesError) as caught:
            session.execute("case.import", **params)
        assert caught.value.code == "case.source_invalid"
    bad = {"node_id": "nope", "snapshot": "abc"}
    for key, value in bad.items():
        with pytest.raises(StokesError) as caught:
            session.execute("case.import", tutorial="incompressibleFluid/cavity", **{key: value})
        assert caught.value.code == "case.source_invalid"


def test_a_link_out_of_the_install_is_refused(tmp_path: Path, home: Path, tutorials: Path) -> None:
    outside = make_case(tmp_path, "secret/case")
    links = (
        (outside, tutorials / "incompressibleFluid" / "linked"),
        (tmp_path / "secret", tutorials / "incompressibleFluid/cavity/system/leak"),
    )
    try:
        for target, link in links:
            link.symlink_to(target, target_is_directory=True)
    except OSError:
        # Windows without the symlink privilege: a junction is the link anyone can make there.
        _winapi = pytest.importorskip("_winapi")
        for target, link in links:
            if not link.exists():
                _winapi.CreateJunction(str(target), str(link))
    session = _session(tmp_path, _probe(tutorials))
    with pytest.raises(StokesError) as caught:
        session.execute("case.import", tutorial="incompressibleFluid/linked")
    assert caught.value.code == "case.source_escape"
    reply = session.execute("case.import", tutorial="incompressibleFluid/cavity")
    assert reply["status"] == "refused" and "leaves the case" in str(reply["refusals"])


def test_no_tutorial_folder(tmp_path: Path, home: Path) -> None:
    session = _session(tmp_path, _probe(None))
    with pytest.raises(StokesError) as caught:
        session.execute("case.import", tutorial="a/b")
    assert caught.value.code == "tutorial.no_root"


def test_undo_redo_and_remove(tmp_path: Path, home: Path, tutorials: Path) -> None:
    session = _session(tmp_path, _probe(tutorials))
    reply = session.execute("case.import", tutorial="incompressibleFluid/cavity")
    project = session.require_project()
    session.execute("history.undo")
    assert project.find("/cases") is None, "one undo step takes the case and its group"
    session.execute("history.redo")
    assert project.get("/cases/cavity").id == reply["node_id"]
    removed = session.execute("case.remove", node="/cases/cavity")
    assert removed["id"] == reply["node_id"] and project.find("/cases/cavity") is None
    session.execute("history.undo")
    assert session.execute("case.get", node="/cases/cavity")["status"] == "ready"
    with pytest.raises(StokesError) as caught:
        session.execute("case.get", node="/cases")
    assert caught.value.code == "case.not_a_case"


def test_save_and_reopen(tmp_path: Path, home: Path, tutorials: Path) -> None:
    session = _session(tmp_path, _probe(tutorials))
    reply = session.execute("case.import", tutorial="incompressibleFluid/cavity")
    session.execute("project.save")
    fresh = Session()
    fresh.execute("project.open", path=str(_home(session)))
    got = fresh.execute("case.get", node="/cases/cavity")
    assert got["snapshot"] == reply["snapshot"] and got["status"] == "ready"
    assert got["recipe"] == reply["recipe"]


def test_journal_replay_reimports_and_checks_the_snapshot(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    journal = tmp_path / "journal.jsonl"
    session = _session(tmp_path, _probe(tutorials), journal=journal)
    reply = session.execute("case.import", tutorial="incompressibleFluid/cavity")
    records = [r for r in Journal.read(journal) if r["command"].startswith("case.")]
    assert records[0]["params"]["snapshot"] == reply["snapshot"]
    assert records[0]["params"]["node_id"] == reply["node_id"]

    other = _session(tmp_path, _probe(tutorials), project="other")
    replay(other, records)
    assert other.execute("case.get", node="/cases/cavity")["snapshot"] == reply["snapshot"]
    assert other.require_project().get("/cases/cavity").id == reply["node_id"]

    changed = [{**records[0], "params": {**records[0]["params"], "snapshot": "0" * 64}}]
    third = _session(tmp_path, _probe(tutorials), project="third")
    with pytest.raises(StokesError) as caught:
        replay(third, changed)
    assert caught.value.code == "case.snapshot_mismatch"
    assert not (_home(third) / "cases" / reply["node_id"]).exists()


def test_replay_uses_the_copy_in_the_project_when_the_source_is_gone(
    tmp_path: Path, home: Path, tutorials: Path
) -> None:
    from stokes.cases.store import remove_tree

    journal = tmp_path / "journal.jsonl"
    session = _session(tmp_path, _probe(tutorials), journal=journal)
    reply = session.execute("case.import", tutorial="incompressibleFluid/cavity")
    records = [r for r in Journal.read(journal) if r["command"] == "case.import"]
    session.execute("history.undo")
    session.execute("project.save")
    remove_tree(tutorials)

    fresh = Session()
    fresh.execute("project.open", path=str(_home(session)))
    replay(fresh, records)
    got = fresh.execute("case.get", node="/cases/cavity")
    assert got["snapshot"] == reply["snapshot"] and got["status"] == "ready"

    elsewhere = _session(tmp_path, _probe(tutorials), project="elsewhere")
    with pytest.raises(StokesError) as caught:
        replay(elsewhere, records)
    assert caught.value.code in {"case.source_missing", "tutorial.no_root"}


def test_a_missing_copy_reads_as_missing(tmp_path: Path, home: Path, tutorials: Path) -> None:
    from stokes.cases.store import remove_tree

    session = _session(tmp_path, _probe(tutorials))
    reply = session.execute("case.import", tutorial="incompressibleFluid/cavity")
    remove_tree(_home(session) / "cases" / reply["node_id"])
    assert session.execute("case.get", node="/cases/cavity")["status"] == "missing"
    with pytest.raises(StokesError) as caught:
        session.execute("case.files", node="/cases/cavity")
    assert caught.value.code == "case.source_missing"


def test_the_case_commands_have_schemas() -> None:
    from stokes.api import default_registry

    schema = {entry["name"]: entry for entry in default_registry().json_schema()}
    assert schema["case.import"]["mutates"] is True
    assert schema["case.remove"]["mutates"] is True
    assert schema["case.get"]["mutates"] is False and schema["case.files"]["mutates"] is False
    assert set(schema["case.import"]["input_schema"]["properties"]) == {
        "tutorial",
        "folder",
        "name",
        "node_id",
        "snapshot",
    }
    from stokes.model import registry

    types = registry.json_schema()
    assert set(types["foam_case"]["properties"]) == {
        "source",
        "snapshot",
        "dialect",
        "solver",
        "recipe",
        "overrides",
        "file_edits",
    }


def test_node_parameter_validation(tmp_path: Path, home: Path, tutorials: Path) -> None:
    session = _session(tmp_path, _probe(tutorials))
    session.execute("case.import", tutorial="incompressibleFluid/cavity")
    good = {
        "source": {"kind": "folder", "path": "/x"},
        "solver": {"air": "fluid"},
        "overrides": [],
        "recipe": {"steps": []},
    }
    for name, value in good.items():
        session.execute("parameter.set", node="/cases/cavity", name=name, value=value)
    bad: dict[str, list[Any]] = {
        "source": [{"kind": "x"}, {"kind": "install", "path": "x"}, {"kind": "folder", "path": ""}],
        "solver": [3, {"a": 1}],
        "overrides": [{}],
        "recipe": [[], {"steps": 1}],
        "snapshot": ["xyz"],
    }
    for name, values in bad.items():
        for value in values:
            with pytest.raises(StokesError):
                session.execute("parameter.set", node="/cases/cavity", name=name, value=value)
    session.execute("parameter.set", node="/cases/cavity", name="source", value=None)


def test_solver_names_and_tree_names(tmp_path: Path) -> None:
    from stokes.cases.allrun import case_recipe, parse_allrun
    from stokes.cases.importer import node_name, solver_of

    multi = make_case(
        tmp_path,
        "m",
        "application foamMultiRun;\nregionSolvers\n{\n    b solid;\n    a fluid;\n}\n",
    )
    recipe, facts = case_recipe(multi)
    assert solver_of(facts, recipe) == {"a": "fluid", "b": "solid"}
    bare = make_case(tmp_path, "b", "application foamRun;\n")
    _, facts = case_recipe(bare)
    allrun = parse_allrun("runApplication foamRun -solver fluid\n", "foamRun")
    assert solver_of(facts, allrun) == "fluid"
    assert solver_of(facts, parse_allrun("", "foamRun")) is None
    legacy = make_case(tmp_path, "l", "application icoFoam;\n")
    _, facts = case_recipe(legacy)
    assert solver_of(facts, parse_allrun("", "icoFoam")) == "icoFoam"
    assert node_name("2D-case", set()) == "case_2D_case"
    assert node_name("---", {"case"}) == "case_2"
    assert node_name("x" * 80, {"x" * 64}) == "x" * 62 + "_2"


def test_copy_limits_and_file_kinds(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stokes.cases import store

    folder = make_case(
        tmp_path,
        "kinds",
        files={
            "constant/polyMesh/boundary": HEADER + "1\n(\n wall { type wall; }\n)\n",
            "system/broken": HEADER + "a {\n",
        },
    )
    session = _session(tmp_path, _probe(None))
    reply = session.execute("case.import", folder=str(folder))
    kinds = {row["path"]: row for row in session.execute("case.files", node=reply["path"])}
    assert kinds["constant/polyMesh/boundary"]["kind"] == "mesh"
    assert kinds["system/broken"]["parsed"] is False
    monkeypatch.setattr(store, "MAX_PARSE_BYTES", 4)
    rows = store.list_files(_home(session), reply["node_id"])
    assert rows is not None and not any(row["parsed"] for row in rows)

    monkeypatch.setattr(store, "MAX_CASE_BYTES", 10)
    with pytest.raises(StokesError) as caught:
        session.execute("case.import", folder=str(folder))
    assert caught.value.code == "case.too_large"
    monkeypatch.setattr(store, "MAX_CASE_FILES", 1)
    reply = session.execute("case.import", folder=str(folder))
    assert reply["status"] == "refused" and "more than 1 files" in str(reply["refusals"])


def test_store_helpers(tmp_path: Path) -> None:
    from stokes.cases import store

    with pytest.raises(StokesError) as caught:
        store.case_dir(tmp_path, "../x")
    assert caught.value.code == "case.store"
    assert store.read_manifest(tmp_path, "n_00000000") is None
    folder = tmp_path / store.CASES_FOLDER / "n_00000000"
    folder.mkdir(parents=True)
    (folder / store.MANIFEST).write_text("{bad", encoding="utf-8")
    assert store.read_manifest(tmp_path, "n_00000000") is None
    (folder / store.MANIFEST).write_text('{"version": 9}', encoding="utf-8")
    assert store.read_manifest(tmp_path, "n_00000000") is None
    assert store.has_snapshot(tmp_path, "n_00000000", "0" * 64) is False
    assert store.list_files(tmp_path, "n_00000000") is None
    assert store.is_script("Allrun") and store.is_script("Allrun.pre")
    assert not store.is_script("Allrunner_x/y")
    empty = tmp_path / "empty"
    empty.mkdir()
    staged = store.stage(tmp_path, "n_11111111", [], lambda rel, path: [])
    assert not staged.ok and "no files" in staged.refusals[0]["why"]
    blob = tmp_path / "blob"
    blob.write_bytes(b"\x00\x01")
    entry = store.Entry("constant/polyMesh/faces", store.SOURCE_AREA, blob)
    ok = store.stage(tmp_path, "n_22222222", [entry], lambda rel, path: [])
    assert ok.ok and store.has_snapshot(tmp_path, "n_22222222", ok.snapshot)
    rows = store.list_files(tmp_path, "n_22222222")
    assert rows is not None and rows[0]["kind"] == "binary"
    copied = tmp_path / "cases" / "n_22222222" / "source" / "constant/polyMesh/faces"
    copied.chmod(stat.S_IWRITE | stat.S_IREAD)
    copied.unlink()
    assert not store.has_snapshot(tmp_path, "n_22222222", ok.snapshot)
    store.remove_tree(tmp_path / "cases")
    assert not (tmp_path / "cases").exists()


def test_files_no_solver_reads_are_kept_but_not_linted(tmp_path: Path, home: Path) -> None:
    """A tutorial's `validation/` plot script or a top-level helper is shell, not a dictionary:
    it used to be linted as one and refused the whole case (found by the T-317 sweep)."""
    root = tmp_path / "t"
    make_case(
        root,
        "fluid/withExtras",
        files={
            "validation/createGraphs": "#!/bin/sh\nset -e\ngnuplot<<EOF\n#plot\nEOF\n",
            "makeNCC": "#!/bin/sh\n#comment\n",
            "README": "notes\n",
        },
    )
    session = _session(tmp_path, _probe(root))
    reply = session.execute("case.import", tutorial="fluid/withExtras")
    assert reply["status"] == "ready", reply["refusals"]
    areas = {f["path"]: f["area"] for f in session.execute("case.files", node="/cases/withExtras")}
    assert areas["validation/createGraphs"] == "extras"
    assert areas["makeNCC"] == "extras" and areas["README"] == "extras"
    assert areas["system/controlDict"] == "source"
