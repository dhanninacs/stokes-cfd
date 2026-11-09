"""Old names after a rename (ADR-0021 decisions 2.3 and 2.4).

``Session.execute`` accepts old parameter names and old node paths and journals current ones, old
journals replay, and a version-1 project is migrated on load. The command and node-type aliases
themselves are tested in ``test_api.py`` and ``test_model.py``. The renaming tasks fill the tables;
these tests inject throwaway entries, and the last one checks whatever entries the tables hold.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.api import CommandRegistry, Journal, Session, default_registry, legacy, replay
from stokes.errors import PersistenceError, ValidationError
from stokes.geometry.model import TAGS_PATH
from stokes.model import load_project, save_project
from stokes.model import registry as node_types
from stokes.model.persist import SCHEMA_VERSION, migrate
from stokes.model.schema import NODE_TYPE_ALIASES, NodeTypeRegistry, register_builtin_types
from tests.selection_support import add_part, cube, new_session, part

OLD = "/geometry/old_things"
NEW = "/geometry/new_things"

# ------------------------------------------------------------------ parameters and paths


def test_old_paths_in_parameters_move_to_the_current_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(legacy.PATHS, OLD, NEW)
    params = {
        "node": f"{OLD}/inlet",
        "parent": OLD,
        "parts": [f"{OLD}/a", "/geometry/parts/b", 3, f"{OLD}_2/c"],
        "name": "old_things",
        "sibling": f"{OLD}_2/inlet",
        "params": {"ref": f"{OLD}/a"},
        "depth": 2,
    }
    assert legacy.rewrite_params(params) == {
        "node": f"{NEW}/inlet",
        "parent": NEW,
        "parts": [f"{NEW}/a", "/geometry/parts/b", 3, f"{OLD}_2/c"],
        "name": "old_things",
        "sibling": f"{OLD}_2/inlet",
        "params": {"ref": f"{OLD}/a"},  # nested objects are not rewritten
        "depth": 2,
    }
    assert params["node"] == f"{OLD}/inlet"  # the caller's dict is left alone


def test_the_longest_old_prefix_wins_and_renames_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(legacy.PATHS, "/a", "/b")
    monkeypatch.setitem(legacy.PATHS, "/a/deep", "/c")
    monkeypatch.setitem(legacy.PATHS, "/b", "/d")  # the current prefix renamed again later
    assert legacy.rewrite_path("/a/deep/x") == "/c/x"
    assert legacy.rewrite_path("/a/x") == "/d/x"
    assert legacy.rewrite_path("/b") == "/d"
    assert legacy.rewrite_path("/ab") == "/ab"


def test_without_old_paths_the_parameters_pass_as_they_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(legacy, "PATHS", {})
    params = {"node": f"{OLD}/inlet"}
    assert legacy.rewrite_params(params) is params


def test_old_parameter_names_are_renamed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(legacy.PARAMETERS, "demo.fresh", {"caption": "label"})
    assert legacy.rename_parameters("demo.fresh", {"caption": "a", "n": 1}) == {
        "label": "a",
        "n": 1,
    }
    assert legacy.rename_parameters("demo.fresh", {"label": "a"}) == {"label": "a"}
    assert legacy.rename_parameters("demo.other", {"caption": "a"}) == {"caption": "a"}
    with pytest.raises(ValidationError):
        legacy.rename_parameters("demo.fresh", {"caption": "a", "label": "b"})


# ------------------------------------------------------------------ journals


@pytest.fixture
def renamed(monkeypatch: pytest.MonkeyPatch) -> CommandRegistry:
    """The built-in commands with throwaway renames: ``node.add`` was called ``tree.add``, its
    ``parent`` was called ``under``, and the path NEW was OLD."""
    reg = CommandRegistry()
    built_in = default_registry()
    for name in built_in.names():
        reg.register(built_in.get(name))
    reg.alias("tree.add", "node.add")
    monkeypatch.setitem(legacy.PARAMETERS, "node.add", {"under": "parent"})
    monkeypatch.setitem(legacy.PATHS, OLD, NEW)
    return reg


def test_a_journal_with_old_names_replays_under_the_current_ones(
    renamed: CommandRegistry, tmp_path: Path
) -> None:
    """An old journal replays into a project that has the renamed group, as a migrated project
    has it: the nodes land under the new path, the session journals current names, and that
    journal replays to the same tree."""
    journal = tmp_path / "journal.jsonl"
    session = Session(renamed, journal_path=journal)
    session.execute("project.new", name="demo")
    session.execute("node.add", parent="/geometry", type="group", name="new_things")
    old_records = [
        {"command": "tree.add", "params": {"under": OLD, "type": "group", "name": "a"}},
        {"command": "tree.add", "params": {"under": f"{OLD}/a", "type": "group", "name": "b"}},
        {"command": "node.rename", "params": {"node": f"{OLD}/a/b", "name": "c"}},
    ]
    replay(session, old_records)
    assert session.execute("node.get", node=f"{NEW}/a/c")["type"] == "group"
    records = Journal.read(journal)
    assert [r["command"] for r in records] == [
        "project.new",
        "node.add",
        "node.add",
        "node.add",
        "node.rename",
    ]
    assert [r["params"]["parent"] for r in records[2:4]] == [NEW, f"{NEW}/a"]
    assert records[4]["params"]["node"] == f"{NEW}/a/b"
    assert not any("under" in r["params"] for r in records)
    fresh = Session(renamed)
    fresh.execute("project.new", name="other")
    replay(fresh, records[1:])
    assert fresh.execute("node.tree")["children"] == session.execute("node.tree")["children"]


def test_node_add_with_an_old_type_stores_the_current_type(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(legacy.NODE_TYPES, "folder", "group")
    renamed_types = NodeTypeRegistry()
    register_builtin_types(renamed_types)  # takes the renamed types from the table
    monkeypatch.setattr(session.require_project(), "registry", renamed_types)
    added = session.execute("node.add", parent="/geometry", type="folder", name="things")
    assert added["type"] == "group"
    assert session.execute("node.get", node="/geometry/things")["type"] == "group"
    other = Session()
    other.execute("project.new", name="other")
    monkeypatch.setattr(other.require_project(), "registry", renamed_types)
    replay(other, session.journal.records[-1:])
    assert other.execute("node.get", node="/geometry/things")["type"] == "group"


# ------------------------------------------------------------------ project migration

MEMBERS = '[{"part":"n_00000001","faces":"all"}]'


def node(
    node_id: str, node_type: str, name: str, *children: dict[str, Any], **params: Any
) -> dict[str, Any]:
    """A node as ``project.json`` stores it."""
    data: dict[str, Any] = {
        "id": node_id,
        "type": node_type,
        "name": name,
        "status": "ok",
        "params": params,
    }
    if children:
        data["children"] = list(children)
    return data


def selection(node_id: str, name: str) -> dict[str, Any]:
    """A named selection as a version-1 project stored it."""
    return node(
        node_id,
        "named_selection",
        name,
        members=MEMBERS,
        kind="boundary",
        color="#3b82c4",
        visible=True,
    )


def project_data(version: int, *geometry: dict[str, Any]) -> dict[str, Any]:
    """A project file's content: the root and a ``/geometry`` group holding ``geometry``."""
    return {
        "schema_version": version,
        "stokes_version": "0.1.0",
        "name": "old",
        "display_units": "si",
        "created": "2026-09-01T00:00:00+00:00",
        "modified": "2026-09-01T00:00:00+00:00",
        "root": node(
            "p_00000000", "project", "old", node("g_00000001", "group", "geometry", *geometry)
        ),
    }


def write_project(folder: Path, version: int, *geometry: dict[str, Any]) -> Path:
    """``project.json`` written by hand, as a Stokes of that schema version wrote it."""
    project = folder / "old.stokes"
    project.mkdir()
    text = json.dumps(project_data(version, *geometry), indent=2)
    (project / "project.json").write_text(text, encoding="utf-8")
    return project


@pytest.fixture
def tags_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    """The node-type rename of T-015: ``named_selection`` became ``tag``."""
    monkeypatch.setitem(legacy.NODE_TYPES, "named_selection", "tag")


@pytest.mark.usefixtures("tags_rename")
def test_a_version_1_project_loads_with_its_selections_as_tags(tmp_path: Path) -> None:
    folder = write_project(
        tmp_path,
        1,
        node("g_00000002", "group", "parts"),
        node(
            "g_00000003",
            "group",
            "named_selections",
            selection("n_00000011", "inlet"),
            selection("n_00000012", "walls"),
            node("n_00000013", "group", "outlet"),  # a plain group: stays a legacy row
        ),
    )
    project = load_project(folder)
    assert project.find("/geometry/named_selections") is None
    tags = project.get("/geometry/tags")
    assert (tags.id, tags.type) == ("g_00000003", "group")
    assert [(c.id, c.type, c.name) for c in tags.children] == [
        ("n_00000011", "tag", "inlet"),
        ("n_00000012", "tag", "walls"),
        ("n_00000013", "group", "outlet"),
    ]
    assert project.get("/geometry/tags/inlet").params["members"] == MEMBERS
    assert project.get("/geometry/parts").id == "g_00000002"
    saved = save_project(project, folder)
    stored = json.loads((saved / "project.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == SCHEMA_VERSION == 3
    assert load_project(saved).root.to_json() == project.root.to_json()


@pytest.mark.usefixtures("tags_rename")
def test_named_selections_merge_into_an_existing_tags_group(tmp_path: Path) -> None:
    folder = write_project(
        tmp_path,
        1,
        node(
            "g_00000004",
            "group",
            "tags",
            node("n_00000021", "group", "inlet"),
            node("n_00000022", "group", "inlet_2"),
        ),
        node(
            "g_00000003",
            "group",
            "named_selections",
            selection("n_00000011", "inlet"),
            selection("n_00000012", "walls"),
        ),
    )
    project = load_project(folder)
    assert project.find("/geometry/named_selections") is None
    assert project.find("g_00000003") is None
    assert [(c.id, c.name) for c in project.get("/geometry/tags").children] == [
        ("n_00000021", "inlet"),
        ("n_00000022", "inlet_2"),
        ("n_00000011", "inlet_3"),
        ("n_00000012", "walls"),
    ]
    assert project.get("/geometry/tags/inlet_3").type == "tag"


@pytest.mark.usefixtures("tags_rename")
def test_a_version_2_project_is_not_migrated(tmp_path: Path) -> None:
    group = node("g_00000003", "group", "named_selections", selection("n_00000011", "inlet"))
    project = load_project(write_project(tmp_path, 2, group))
    assert project.get("/geometry/named_selections/inlet").type == "named_selection"
    assert project.find("/geometry/tags") is None


def test_a_project_newer_than_this_stokes_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PersistenceError, match="newer"):
        load_project(write_project(tmp_path, SCHEMA_VERSION + 1))


@pytest.mark.usefixtures("tags_rename")
def test_the_migration_renames_nothing_over_another_node() -> None:
    """Only a group named ``named_selections`` directly under ``/geometry`` moves, and never onto
    a ``tags`` node that is not a group; node types are renamed wherever they are."""
    not_a_group = migrate(project_data(1, node("n_00000031", "part", "named_selections")))
    assert not_a_group["schema_version"] == 3
    assert not_a_group["root"]["children"][0]["children"][0]["name"] == "named_selections"
    blocked = migrate(
        project_data(
            1,
            node("n_00000032", "part", "tags"),
            node("g_00000003", "group", "named_selections", selection("n_00000011", "inlet")),
        )
    )
    geometry = blocked["root"]["children"][0]["children"]
    assert [(c["name"], c["type"]) for c in geometry] == [
        ("tags", "part"),
        ("named_selections", "group"),
    ]
    assert geometry[1]["children"][0]["type"] == "tag"
    nested = node("g_00000006", "group", "named_selections", selection("n_00000041", "a"))
    deeper = migrate(project_data(1, node("g_00000002", "group", "parts", nested)))
    group = deeper["root"]["children"][0]["children"][0]["children"][0]
    assert group["name"] == "named_selections"
    assert group["children"][0]["type"] == "tag"


# ------------------------------------------------------------------ the tables themselves


def test_the_tables_name_current_commands_types_and_parameters() -> None:
    """Whatever the renaming tasks put in the tables resolves; with empty tables this checks that
    the default registries hold exactly their entries."""
    commands = default_registry()
    assert legacy.NODE_TYPES is NODE_TYPE_ALIASES, "one dict for the model and the API"
    assert commands.aliases() == {old: commands.canonical(old) for old in legacy.COMMANDS}
    for old in legacy.COMMANDS:
        assert old not in commands.names(), old
        commands.get(old)
    assert set(node_types.aliases()) == set(legacy.NODE_TYPES)
    for old in legacy.NODE_TYPES:
        assert old not in node_types.types(), old
        node_types.get(old)
    for old, new in legacy.PATHS.items():
        for path in (old, new):
            assert path.startswith("/") and len(path) > 1 and not path.endswith("/"), path
    for name, renames in legacy.PARAMETERS.items():
        definition = commands.get(name)
        assert definition.name == name, f"{name}: key the table by the current command name"
        known = {p.name for p in definition.params}
        for old, new in renames.items():
            assert new in known and old not in known, (name, old, new)


def test_old_and_new_command_names_give_the_same_result(session: Session) -> None:
    """The real ``selection.*`` entries: an old command, old parameter and old path reach the
    same tag as the current names do."""
    session.execute("project.new", name="demo")
    session.execute("tag.create", name="walls", members=[])
    assert session.execute("selection.list") == session.execute("tag.list")
    old = session.execute("selection.members", selection="/geometry/named_selections/walls")
    new = session.execute("tag.members", tag="/geometry/tags/walls")
    assert old == new
    assert old["members"] == []


def _without_ids(value: Any) -> Any:
    """A tree or list with the generated node ids removed: two sessions make their own ids, so
    only the shapes, names, types and parameters are comparable."""
    if isinstance(value, dict):
        return {k: _without_ids(v) for k, v in value.items() if k != "id"}
    if isinstance(value, list):
        return [_without_ids(v) for v in value]
    return value


def test_an_old_journal_of_selection_names_replays_to_the_same_tree(tmp_path: Path) -> None:
    """A journal written before the rename, with old command names, the old ``selection``
    parameter and the old path, replays into the same tree as one written with the new names."""
    old_records = [
        {"command": "project.new", "params": {"name": "demo"}},
        {
            "command": "selection.create",
            "params": {"name": "walls", "members": [], "kind": "plain"},
        },
        {
            "command": "selection.rename",
            "params": {"selection": "/geometry/named_selections/walls", "name": "inlet"},
        },
    ]
    old = Session(journal_path=tmp_path / "old.jsonl")
    replay(old, old_records)

    new = Session(journal_path=tmp_path / "new.jsonl")
    new.execute("project.new", name="demo")
    new.execute("tag.create", name="walls", members=[], kind="plain")
    new.execute("tag.rename", tag="/geometry/tags/walls", name="inlet")

    assert _without_ids(old.execute("node.tree")) == _without_ids(new.execute("node.tree"))
    assert _without_ids(old.execute("tag.list")) == _without_ids(new.execute("tag.list"))


def test_the_first_save_over_a_version_1_file_keeps_it(tmp_path: Path) -> None:
    folder = write_project(
        tmp_path,
        1,
        node("g_00000003", "group", "named_selections", selection("n_00000011", "inlet")),
    )
    original = (folder / "project.json").read_bytes()
    project = load_project(folder)
    assert not (folder / "project.v1.json").exists()  # loading alone changes nothing on disk
    save_project(project, folder)
    assert (folder / "project.v1.json").read_bytes() == original
    assert json.loads((folder / "project.json").read_text(encoding="utf-8"))["schema_version"] == 3
    (folder / "project.v1.json").write_bytes(b"the first copy")  # a later save keeps the first copy
    save_project(project, folder)
    assert (folder / "project.v1.json").read_bytes() == b"the first copy"
    assert not (folder / "project.v2.json").exists()  # a current file needs none


# ------------------------------------------------------------------ old names through a session


def test_every_old_selection_command_runs_through_the_session(tmp_path: Path) -> None:
    """All 13 old ``selection.*`` names run through ``Session.execute``, with the old
    ``selection`` parameter and the old path where they have them; the session journals the
    current names."""
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    b = add_part(session, "b", part(cube(0.01, (0.02, 0.0, 0.0), name="b")))
    session.execute("selection.create", name="inlet", members=[{"part": a, "faces": [0]}])
    session.execute("selection.add", selection="inlet", members=[{"part": b, "faces": [5]}])
    session.execute("selection.remove", selection="inlet", members=[{"part": b, "faces": [5]}])
    session.execute("selection.set", selection="inlet", members=[{"part": a, "faces": [[1, 2]]}])
    session.execute("selection.invert", selection="inlet", parts=[a, b])
    session.execute("selection.grow", selection="inlet", angle_deg=95.0)
    session.execute("selection.rematch", selection="inlet")
    renamed = session.execute(
        "selection.rename", selection="/geometry/named_selections/inlet", name="intake"
    )
    assert renamed["path"] == f"{TAGS_PATH}/intake"
    assert session.execute("selection.members", selection="intake")["faces"] == 12
    session.execute("parameter.set", node=b, name="color", value="#cc331a")
    assert session.execute("selection.by_color", color="#CC331A")["members"] == [
        {"part": b, "faces": "all"}
    ]
    assert session.execute("selection.by_name", pattern="b*")["count"] == 1
    session.execute("selection.delete", selection="intake")
    assert session.execute("selection.list") == []
    executed = [r["command"] for r in Journal.read(tmp_path / "demo.jsonl")]
    assert all(not c.startswith("selection.") for c in executed)
    assert {
        "tag.create",
        "tag.add",
        "tag.remove",
        "tag.set",
        "tag.invert",
        "tag.grow",
        "tag.rematch",
        "tag.rename",
        "tag.delete",
    } <= set(executed)


def test_export_stl_takes_the_old_selections_parameter(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    a = add_part(session, "a", part(cube(0.01, name="a")))
    session.execute("tag.create", name="inlet", members=[{"part": a, "faces": [0]}])
    result = session.execute("geometry.export_stl", path="case.stl", selections=["inlet"])
    assert result["solids"] == 1 and result["triangles"] == 2 and result["units"] == "m"
    written = tmp_path / "demo.stokes" / "case.stl"
    assert written.is_file() and written.stat().st_size == 84 + 50 * 2


def test_the_old_and_the_new_parameter_are_refused_together(session: Session) -> None:
    with pytest.raises(ValidationError, match=r"both 'selections' and 'tags'"):
        session.execute(
            "geometry.export_stl", path="case.stl", selections=["inlet"], tags=["inlet"]
        )
    with pytest.raises(ValidationError, match=r"both 'selection' and 'tag'"):
        session.execute("tag.add", selection="inlet", tag="inlet", members=[])
