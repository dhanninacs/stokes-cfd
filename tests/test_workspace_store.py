"""Workspace layers, merge and the store, on real files in a private STOKES_HOME."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.workspace import WorkspaceStore
from stokes.workspace.defaults import PANELS, TOPBAR_ITEMS, TREE_GROUPS, default_document
from stokes.workspace.errors import (
    ProjectFolderRequired,
    UnknownPreset,
    WorkspaceInvalid,
    WorkspacePathError,
)
from stokes.workspace.files import MAX_DOCUMENT_BYTES, read_file
from stokes.workspace.merge import stokes_home


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every workspace test runs against a private Stokes home, never the real one."""
    path = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(path))
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "demo.stokes"
    folder.mkdir()
    return folder


@pytest.fixture
def events() -> list[tuple[str, dict[str, Any]]]:
    return []


@pytest.fixture
def store(events: list[tuple[str, dict[str, Any]]]) -> WorkspaceStore:
    return WorkspaceStore(notify=lambda event, data: events.append((event, data)))


def write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, **document}), encoding="utf-8")


def read(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def user_file(home: Path) -> Path:
    return home / "workspace.json"


def project_file(project: Path) -> Path:
    return project / "workspace.json"


def codes(findings: Any) -> set[tuple[str, str]]:
    return {(f["code"], f["path"]) for f in findings}


# ---------------------------------------------------------------------- merging
def test_without_files_the_effective_workspace_is_the_default(store: WorkspaceStore) -> None:
    result = store.effective(None)
    assert result.document == default_document()
    assert result.findings == ()
    assert set(result.sources.values()) == {"default"}
    assert result.sources["layout.left.panels"] == "default"
    assert result.sources["theme"] == "default"


def test_project_overrides_user_overrides_defaults(
    store: WorkspaceStore, home: Path, project: Path
) -> None:
    write(
        user_file(home),
        {
            "theme": {"name": "light"},
            "viewport": {"grid": False},
            "layout": {"left": {"size": 280}},
        },
    )
    write(
        project_file(project),
        {
            "theme": {"name": "cupertino"},
            "viewport": {"viewMode": "wireframe"},
            "layout": {"left": {"size": 360}},
        },
    )
    result = store.effective(project)
    document, sources = result.document, result.sources
    assert document["theme"] == {"name": "cupertino"}
    assert document["layout"]["left"]["size"] == 360
    # **Every display switch the layer does not mention keeps its default** (the owner's display
    # toggles): the user turned the grid off, the project changed the view mode, and the six
    # switches nobody mentioned stay as they were - the point of a layered document.
    assert document["viewport"] == {
        "projection": "perspective",
        "viewMode": "wireframe",
        "grid": False,
        "axisLines": True,
        "shadow": True,
        "reflections": True,
        "edges": True,
        "gizmo": True,
        "scaleBar": True,
        "lowDetail": False,
        "background": "theme",
    }
    assert sources["theme"] == "project"
    assert sources["layout.left.size"] == "project"
    assert sources["viewport.grid"] == "user"
    assert sources["viewport.projection"] == "default"
    assert store.effective(None).document["theme"] == {"name": "light"}


def test_lists_and_the_theme_replace_while_objects_merge(
    store: WorkspaceStore, home: Path, project: Path
) -> None:
    write(
        user_file(home),
        {
            "theme": {"base": "dark", "tokens": {"--accent": "#ff0000"}},
            "tree": {"hidden": ["runs"]},
            "shortcuts": {"view.fit": "G"},
        },
    )
    write(
        project_file(project),
        {
            "theme": {"name": "light"},
            "tree": {"hidden": ["scenes"]},
            "shortcuts": {"view.projection": "P"},
        },
    )
    document = store.effective(project).document
    assert document["theme"] == {"name": "light"}
    assert document["tree"]["hidden"] == ["scenes"]
    assert document["shortcuts"]["view.fit"] == "G"
    assert document["shortcuts"]["view.projection"] == "P"


def test_an_invalid_project_value_falls_back_to_the_user_value(
    store: WorkspaceStore, home: Path, project: Path
) -> None:
    write(user_file(home), {"theme": {"name": "dark"}, "layout": {"left": {"mode": "split"}}})
    write(project_file(project), {"theme": {"name": "neon"}, "layout": {"left": {"mode": "grid"}}})
    result = store.effective(project)
    assert result.document["theme"] == {"name": "dark"}
    assert result.document["layout"]["left"]["mode"] == "split"
    assert result.sources["theme"] == "user"
    bad = {(f.scope, f.code, f.kind) for f in result.findings}
    assert bad == {("project", "workspace.invalid_value", "invalid")}


def test_unknown_keys_survive_merging_and_writing(store: WorkspaceStore, home: Path) -> None:
    write(user_file(home), {"plugins": {"probe": 1}, "layout": {"left": {"glow": True}}})
    document = store.effective(None).document
    assert document["plugins"] == {"probe": 1}
    assert document["layout"]["left"]["glow"] is True
    store.set("viewport.grid", False, "user", None)
    on_disk = read(user_file(home))
    assert on_disk["plugins"] == {"probe": 1}
    assert on_disk["layout"]["left"] == {"glow": True}
    assert on_disk["viewport"] == {"grid": False}
    assert next(iter(on_disk)) == "version"


def test_a_panel_in_two_places_keeps_the_place_of_the_higher_layer(
    store: WorkspaceStore, home: Path, project: Path
) -> None:
    write(user_file(home), {"layout": {"left": {"panels": [{"id": "tree"}, {"id": "properties"}]}}})
    write(project_file(project), {"layout": {"right": {"panels": [{"id": "properties"}]}}})
    result = store.effective(project)
    layout = result.document["layout"]
    # **`details` is the default left slot's second panel** (the layout decision 2026-10-18): the
    # override above places `properties`, which this document does not hold in `left`, so the
    # default arrangement stands.
    assert [p["id"] for p in layout["left"]["panels"]] == ["tree", "details"]
    # **`mesh`, `solve` and `results` joined the right slot on 2026-10-15** (Claude's panel order:
    # the build's list is what places them, and this used to be three panels).
    # **`properties` stays in the project's right slot and nothing joins it** (the owner's "remove
    # the right strip"): the panels this test used to see restored - `mesh`, `conditions`, `solve`,
    # `results`, `plots` - are hidden by the default now, so a layer that mentions one of them
    # decides where it goes rather than the restore filling the slot behind the user's back.
    assert [p["id"] for p in layout["right"]["panels"]] == ["properties"]
    assert not [f for f in result.findings if f.code == "workspace.duplicate"]


def test_a_panel_placed_twice_in_one_layer_stays_in_the_first_slot(
    store: WorkspaceStore, home: Path
) -> None:
    write(
        user_file(home),
        {
            "layout": {
                "left": {"panels": [{"id": "tree"}]},
                "right": {"panels": [{"id": "tree"}, {"id": "properties"}]},
            }
        },
    )
    result = store.effective(None)
    layout = result.document["layout"]
    # **`details` is the default left slot's second panel** (the layout decision 2026-10-18): the
    # override above places `properties`, which this document does not hold in `left`, so the
    # default arrangement stands.
    assert [p["id"] for p in layout["left"]["panels"]] == ["tree", "details"]
    # **`mesh`, `solve` and `results` joined the right slot on 2026-10-15** (Claude's panel order:
    # the build's list is what places them, and this used to be three panels).
    # **The user's right list stands** (the owner's "remove the right strip"): the panels this test
    # used to see restored beside `properties` are hidden by the default now, so the slot holds just
    # what the user placed. What the test is FOR - a duplicate staying in the first slot - is `tree`
    # appearing nowhere in `right`, which the empty-duplicate assertion below still covers.
    assert [p["id"] for p in layout["right"]["panels"]] == ["properties"]
    # **The user's right list replaces the default one**, so every right-slot built-in the user did
    # not place is restored - `properties` was placed, so it is not. Derived from `PANELS` rather
    # than a literal, because a literal here has to be edited whenever a panel is added and the
    # failure it gives says nothing about which one is new.
    # **A right-slot panel is not "unplaced" any more** (the owner's "remove the right strip"): the
    # default hides those, and a hidden panel is PLACED - in `layout.hidden` - so it makes no
    # restored
    # notice at all. The notices that remain are the left slot's.
    unplaced_right: list[str] = []
    # **The LEFT slot's own unplaced entries too** (2026-10-18): the document above places only
    # `tree` there, so `details` is restored beside it - the same rule as the right slot's, and a
    # count that ignored it would say `16` while the store reported `17`.
    unplaced_left = [p for p, slot in PANELS.items() if slot == "left" and p != "tree"]
    assert [(f.code, f.kind) for f in result.findings] == (
        [("workspace.duplicate", "notice")]
        + [("workspace.restored", "notice")] * (len(unplaced_right) + len(unplaced_left))
    )


def test_builtin_items_that_are_nowhere_come_back_and_hidden_ones_stay_hidden(
    store: WorkspaceStore, home: Path
) -> None:
    write(
        user_file(home),
        {
            "layout": {"left": {"panels": []}, "right": {"panels": []}, "hidden": ["properties"]},
            "islands": {"br": ["view"]},
            "topbar": {"items": [{"id": "run", "visible": True}]},
            "tree": {"groupOrder": ["runs", "geometry"]},
        },
    )
    result = store.effective(None)
    document = result.document
    # The default left slot holds the tree and the Details pane beside it (2026-10-18).
    assert document["layout"]["left"]["panels"] == [
        {"id": "tree", "order": 0},
        {"id": "details", "order": 1},
    ]
    # **`properties` stays hidden** - it is in `layout.hidden`, and a hidden built-in is a choice
    # the reader must not undo.
    #
    # **And the right slot stays EMPTY**: the panels this assertion used to see arrive are hidden by
    # the default arrangement now (the owner's "remove the right strip"), and a panel a layer does
    # not
    # mention is restored to the place the DEFAULT keeps it - which for all of them is `hidden`.
    # That
    # is the rule this test's own name describes, applied to panels whose default place has moved.
    assert document["layout"]["right"]["panels"] == []
    assert {"properties", "mesh", "conditions", "solve", "results", "plots"} <= set(
        document["layout"]["hidden"]
    )
    assert document["islands"]["br"] == ["view", "gizmo"]
    assert [item["id"] for item in document["topbar"]["items"]] == list(TOPBAR_ITEMS)
    groups = document["tree"]["groupOrder"]
    assert groups[:2] == ["runs", "geometry"]
    assert sorted(groups) == sorted(TREE_GROUPS)
    restored = [f for f in result.findings if f.code == "workspace.restored"]
    # **Counted from the document rather than from a remembered number.** A literal here has to be
    # edited every time a built-in panel is added, and the failure it gives (`17 == 14`) says
    # nothing
    # about which panel is new. `properties` is hidden so it is NOT restored; the top-bar items are
    # restored except the one the document already places.
    right_builtins = [p for p, slot in PANELS.items() if slot == "right" and p != "properties"]
    # **The left slot's own built-ins, counted rather than remembered as `1`.** It was `1` while the
    # tree was alone; the Details pane joined it on 2026-10-18, and a literal would have said `1`
    # while the document placed two - which is exactly the failure the rest of this test avoids.
    left_builtins = [p for p, slot in PANELS.items() if slot == "left"]
    # **Counted, not remembered** - and the islands are part of it, which a first version of this
    # forgot: `view` IS placed, so only `gizmo` comes back. Measured on this document:
    #   left 2 (tree, details) + right 5 + islands 1 (gizmo) + topbar 10 (11 built-ins less `run`,
    #   which the document already places) = 18
    assert len(restored) == len(left_builtins) + len(right_builtins) + 1 + len(TOPBAR_ITEMS) - 1


def test_chord_conflicts_are_reported_and_the_palette_keeps_a_chord(
    store: WorkspaceStore, home: Path
) -> None:
    write(user_file(home), {"shortcuts": {"view.fit": "O", "palette.open": ""}})
    result = store.effective(None)
    assert result.document["shortcuts"]["palette.open"] == "Ctrl+K"
    found = {(f.code, f.kind) for f in result.findings}
    assert ("workspace.required", "invalid") in found
    conflicts = [f for f in result.findings if f.code == "workspace.shortcut_conflict"]
    assert {f.path for f in conflicts} == {
        ("shortcuts", "view.fit"),
        ("shortcuts", "view.projection"),
    }


# ---------------------------------------------------------------------- files
def test_an_unreadable_file_is_reported_then_kept_aside_on_the_next_write(
    store: WorkspaceStore, home: Path
) -> None:
    user_file(home).parent.mkdir(parents=True)
    user_file(home).write_text("{not json", encoding="utf-8")
    result = store.effective(None)
    assert result.document == default_document()
    assert [(f.code, f.kind, f.scope) for f in result.findings] == [
        ("workspace.unreadable", "invalid", "user")
    ]
    reply = store.set("viewport.grid", False, "user", None)
    assert ("workspace.kept_aside", "") in codes(reply["warnings"])
    assert (home / "workspace.json.bak").read_text(encoding="utf-8") == "{not json"
    assert read(user_file(home))["viewport"] == {"grid": False}
    assert store.effective(None).findings == ()


def test_oversized_deep_and_non_object_files_are_refused(tmp_path: Path) -> None:
    big = tmp_path / "big.json"
    big.write_text(json.dumps({"x": "a" * MAX_DOCUMENT_BYTES}), encoding="utf-8")
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 40 + "]" * 40, encoding="utf-8")
    deep_object = tmp_path / "deep-object.json"
    deep_object.write_text('{"a":' * 40 + "1" + "}" * 40, encoding="utf-8")
    array = tmp_path / "array.json"
    array.write_text("[1, 2]", encoding="utf-8")
    nan = tmp_path / "nan.json"
    nan.write_text('{"x": NaN}', encoding="utf-8")
    for path in (big, deep, deep_object, array, nan):
        state = read_file(path, "user")
        assert (state.exists, state.readable, state.document) == (True, False, {}), path.name
        assert state.findings[0].code == "workspace.unreadable"


def test_a_file_outside_its_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / "workspace.json"
    write(outside, {"theme": {"name": "light"}})
    state = read_file(outside, "project", within=tmp_path / "demo.stokes")
    assert not state.readable
    assert "outside" in state.findings[0].message


def test_files_are_read_again_when_they_change(store: WorkspaceStore, home: Path) -> None:
    write(user_file(home), {"theme": {"name": "light"}})
    assert store.effective(None).document["theme"] == {"name": "light"}
    write(user_file(home), {"theme": {"name": "cupertino-dark"}})
    assert store.effective(None).document["theme"] == {"name": "cupertino-dark"}


def test_stokes_home_keeps_tests_away_from_the_real_home(store: WorkspaceStore, home: Path) -> None:
    real = Path.home() / ".stokes" / "workspace.json"
    before = real.stat().st_mtime_ns if real.exists() else None
    assert stokes_home() == home
    assert store.home == home
    store.set("viewport.grid", False, "user", None)
    store.save_preset("probe", None, None, None)
    assert user_file(home).is_file()
    assert (home / "presets" / "probe.json").is_file()
    after = real.stat().st_mtime_ns if real.exists() else None
    assert before == after
    assert WorkspaceStore(home=home / "other").home == home / "other"


# ---------------------------------------------------------------------- paths
def test_set_and_get_by_dotted_path(store: WorkspaceStore, home: Path) -> None:
    store.set("layout.left.panels.0.size", 280, "user", None)
    assert store.get("layout.left.panels.0.size", None)["value"] == 280
    assert store.get("layout.left.panels", None)["source"] == "user"
    # **Index 2 appends**: the slot holds the tree and the Details pane (2026-10-18), so replacing
    # index 1 would overwrite the pane rather than add a panel.
    store.set("layout.left.panels.2", {"id": "plots"}, "user", None)
    left = store.get("layout.left.panels", None)["value"]
    assert left == [
        {"id": "tree", "order": 0, "size": 280},
        {"id": "details", "order": 1},
        {"id": "plots", "order": 2},
    ]
    # **A panel is in exactly one place**, so placing `plots` in `left` removes it from the right
    # slot - which is what the default arrangement leaves: five built-ins, with the orders the build
    # gives them. `mesh`, `solve` and `results` joined on 2026-10-15.
    # **A dotted read still reaches an empty list** (the owner's "remove the right strip"): the path
    # exists and its value is what it is, which is the property this test is about - it used to name
    # the six panels the default placed there.
    assert store.get("layout.right.panels", None)["value"] == []
    reply = store.set("shortcuts.view.fit", "shift g", "user", None)
    assert (reply["path"], reply["value"]) == ('shortcuts["view.fit"]', "Shift+G")
    store.set("shortcuts.history.redo.1", "ctrl alt z", "user", None)
    assert store.get('shortcuts["history.redo"]', None)["value"] == ["Ctrl+Y", "Ctrl+Alt+Z"]
    assert store.get("shortcuts.history.redo.1", None)["value"] == "Ctrl+Alt+Z"
    store.set("theme", {"base": "light", "tokens": {"--accent": "#123456"}}, "user", None)
    store.set("theme.tokens.--radius", "4px", "user", None)
    got = store.get("theme.tokens", None)
    assert got["value"] == {"--accent": "#123456", "--radius": "4px"}
    assert got["source"] == "user"
    assert read(user_file(home))["shortcuts"] == {
        "view.fit": "Shift+G",
        "history.redo": ["Ctrl+Y", "Ctrl+Alt+Z"],
    }


@pytest.mark.parametrize(
    ("path", "value"),
    [
        # **Past the end of the slot.** `left` holds the tree and the Details pane (2026-10-18), so
        # index 2 is the first that does not exist; index 1 used to be, and is a real panel now.
        # Index 2 APPENDS (the slot has two panels), so the first invalid index is 3.
        ("layout.left.panels.3", {"id": "plots"}),
        ("layout.left.panels.2.size", 300),
        ("layout.left.colour", "red"),
        ("viewport.grid.size", 3),
        ("version", 2),
        ("nonsense", 1),
        ("shortcuts.Bad Key", "F"),
        ("tree.stages.Optimise", ["/runs"]),
        ("theme.tokens.--nope", "#ffffff"),
        ("layout[0]", 1),
    ],
)
def test_writes_to_paths_the_schema_does_not_know_are_refused(
    store: WorkspaceStore, home: Path, path: str, value: Any
) -> None:
    with pytest.raises(WorkspacePathError):
        store.set(path, value, "user", None)
    assert not user_file(home).exists()


def test_reads_of_missing_paths_are_refused(store: WorkspaceStore) -> None:
    for path in ("layout.left.colour", "layout.left.panels.4", "viewport.grid.x", "theme.tokens"):
        with pytest.raises(WorkspacePathError):
            store.get(path, None)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("layout.left.mode", "grid"),
        ("layout.left.size", 3),
        ("layout.left.panels", [{"id": "tree"}, {"id": "tree"}]),
        ("layout.left", {"size": 280, "glow": True}),
        ("shortcuts.palette.open", ""),
        ("shortcuts.view.fit", "Ctrl+Hyper+F"),
        ("theme.tokens.--accent", "#ff0000"),
        ("theme", {"base": "dark", "tokens": {"--accent": "teal"}}),
        ("viewport.background", "url(x)"),
        ("engines.default", "Open FOAM"),
    ],
)
def test_writes_are_strict(store: WorkspaceStore, home: Path, path: str, value: Any) -> None:
    with pytest.raises(WorkspaceInvalid) as caught:
        store.set(path, value, "user", None)
    assert caught.value.code == "workspace.invalid"
    assert caught.value.details["warnings"]
    assert not user_file(home).exists()


def test_values_must_be_json(store: WorkspaceStore) -> None:
    for value in ({1, 2}, float("nan"), {"a": {"b": object()}}):
        with pytest.raises(WorkspaceInvalid):
            store.set("viewport.grid", value, "user", None)
    nested: Any = 1
    for _ in range(40):
        nested = [nested]
    with pytest.raises(WorkspaceInvalid):
        store.set("viewport.grid", nested, "user", None)


def test_writes_store_canonical_values(store: WorkspaceStore, home: Path) -> None:
    store.set("viewport.background", "#ABC", "user", None)
    store.set("topbar.items", [{"id": "run"}, {"id": "logo", "visible": False}], "user", None)
    on_disk = read(user_file(home))
    assert on_disk["viewport"]["background"] == "#aabbcc"
    assert on_disk["topbar"]["items"] == [
        {"id": "run", "visible": True},
        {"id": "logo", "visible": False},
    ]


def test_set_says_when_the_project_overrides_the_value(
    store: WorkspaceStore, home: Path, project: Path
) -> None:
    write(project_file(project), {"viewport": {"grid": True}})
    reply = store.set("viewport.grid", False, "user", project)
    assert (reply["value"], reply["effective"], reply["source"]) == (False, True, "project")
    assert "project scope overrides" in reply["note"]


def test_project_scope_needs_a_project_folder(store: WorkspaceStore) -> None:
    with pytest.raises(ProjectFolderRequired) as caught:
        store.set("viewport.grid", False, "project", None)
    assert caught.value.code == "workspace.no_project_folder"
    with pytest.raises(WorkspaceInvalid):
        store.set("viewport.grid", False, "everyone", None)


# ---------------------------------------------------------------------- reset
def test_reset_works_per_scope(
    store: WorkspaceStore,
    home: Path,
    project: Path,
    events: list[tuple[str, dict[str, Any]]],
) -> None:
    store.set("viewport.grid", False, "user", project)
    store.set("theme", {"name": "light"}, "user", project)
    store.set("layout.left.panels.0.size", 260, "user", project)
    store.set("layout.left.size", 360, "project", project)
    events.clear()
    assert store.reset("project", None, project) == {
        "scope": "project",
        "path": None,
        "removed": True,
    }
    assert not project_file(project).exists()
    assert user_file(home).exists()
    assert store.reset("user", "viewport.grid", project)["removed"] is True
    assert "viewport" not in read(user_file(home))
    assert store.reset("user", "viewport.grid", project)["removed"] is False
    with pytest.raises(WorkspacePathError, match="is a list"):
        store.reset("user", "layout.left.panels.0", project)
    assert store.reset("user", "nothing.here", project)["removed"] is False
    assert store.reset("user", None, project)["removed"] is True
    assert store.reset("user", None, project)["removed"] is False
    assert store.effective(project).document == default_document()
    assert events == [
        ("workspace.changed", {"scope": "project", "path": None}),
        ("workspace.changed", {"scope": "user", "path": "viewport.grid"}),
        ("workspace.changed", {"scope": "user", "path": None}),
    ]


# ---------------------------------------------------------------------- export and import
def test_export_and_import_round_trip(store: WorkspaceStore, home: Path, tmp_path: Path) -> None:
    store.set("theme", {"base": "cupertino", "tokens": {"--accent": "#ff8800"}}, "user", None)
    store.set("shortcuts.view.fit", "G", "user", None)
    store.apply_preset("workflow", "user", None)
    write(user_file(home), {**read(user_file(home)), "plugins": {"probe": 1}})
    exported = store.export("user", None, None)
    user_before = read(user_file(home))
    store.reset("user", None, None)
    imported = store.import_text(exported["text"], "user", None)
    assert imported["sections"] == exported["sections"]
    assert read(user_file(home)) == user_before

    whole = store.export("effective", None, None)
    elsewhere = WorkspaceStore(home=tmp_path / "elsewhere")
    elsewhere.import_text(whole["text"], "user", None)
    assert elsewhere.effective(None).document == store.effective(None).document


def test_export_selected_sections(store: WorkspaceStore) -> None:
    store.set("theme", {"name": "light"}, "user", None)
    exported = store.export("effective", ["theme"], None)
    assert exported["sections"] == ["theme"]
    assert json.loads(exported["text"]) == {"version": 1, "theme": {"name": "light"}}
    with pytest.raises(WorkspaceInvalid):
        store.export("effective", ["colours"], None)
    with pytest.raises(WorkspaceInvalid):
        store.export("everything", None, None)


def test_import_replaces_only_the_sections_it_holds(store: WorkspaceStore, home: Path) -> None:
    store.apply_preset("classic", "user", None)
    reply = store.import_text(
        json.dumps({"version": 1, "theme": {"base": "dark", "tokens": {"--accent": "teal"}}}),
        "user",
        None,
    )
    assert reply["sections"] == ["theme"]
    assert ("workspace.invalid_value", "theme.tokens.--accent") in codes(reply["warnings"])
    on_disk = read(user_file(home))
    assert on_disk["theme"] == {"base": "dark", "tokens": {}}
    assert on_disk["layout"]["left"]["mode"] == "split"


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '{"version": 1}', '{"x": NaN}', ""])
def test_import_refuses_text_without_a_document(store: WorkspaceStore, text: str) -> None:
    with pytest.raises(WorkspaceInvalid, match="imported text"):
        store.import_text(text, "user", None)


# ---------------------------------------------------------------------- presets
def test_apply_preset_replaces_the_arrangement_and_keeps_the_rest(
    store: WorkspaceStore, home: Path, project: Path
) -> None:
    store.set("theme", {"name": "cupertino"}, "user", project)
    store.set("shortcuts.view.fit", "G", "user", project)
    reply = store.apply_preset("minimal", "user", project)
    assert reply["name"] == "minimal"
    assert reply["sections"] == ["layout", "islands", "topbar", "tree"]
    assert reply["overridden"] == []
    document = store.effective(project).document
    # **`minimal` hides the sparse set**: `tree` since it was written, and `mesh`, `solve` and
    # `results` since 2026-10-15 - a preset whose whole character is "few panels showing" hides the
    # three rather than placing them, which is its own idiom and keeps every built-in placed once.
    # **`details` hidden too** (2026-10-18): a preset whose character is "few panels showing"
    # answers a new built-in the way it already answers the tree.
    assert document["layout"]["hidden"] == ["tree", "details", "mesh", "solve", "results"]
    assert document["theme"] == {"name": "cupertino"}
    assert document["shortcuts"]["view.fit"] == "G"
    write(project_file(project), {"layout": {"left": {"size": 400}}})
    assert store.apply_preset("classic", "user", project)["overridden"] == ["layout"]
    assert store.apply_preset("ribbon", "project", project)["scope"] == "project"
    assert read(project_file(project))["topbar"]["stages"] == {"style": "ribbon"}


def test_saved_presets_round_trip(store: WorkspaceStore, home: Path, tmp_path: Path) -> None:
    store.set("theme", {"base": "dark", "tokens": {"--accent": "#00ff00"}}, "user", None)
    store.apply_preset("classic", "user", None)
    expected = store.effective(None).document
    saved = store.save_preset("lab-setup", "Lab setup", "Our lab's arrangement.", None)
    assert Path(saved["path"]) == home / "presets" / "lab-setup.json"
    listed = {p["name"]: p for p in store.presets()["presets"]}
    assert listed["lab-setup"]["source"] == "user"
    assert listed["lab-setup"]["title"] == "Lab setup"
    assert listed["lab-setup"]["description"] == "Our lab's arrangement."
    assert listed["classic"]["source"] == "shipped"
    store.reset("user", None, None)
    store.apply_preset("lab-setup", "user", None)
    assert store.effective(None).document == expected


@pytest.mark.parametrize("name", ["classic", "Lab", "../x", "", "a" * 65, "dot.ted"])
def test_preset_names_are_checked(store: WorkspaceStore, name: str) -> None:
    with pytest.raises(WorkspaceInvalid):
        store.save_preset(name, None, None, None)


def test_unknown_presets_and_odd_files(store: WorkspaceStore, home: Path) -> None:
    with pytest.raises(UnknownPreset) as caught:
        store.apply_preset("nope", "user", None)
    assert caught.value.code == "workspace.unknown_preset"
    folder = home / "presets"
    folder.mkdir(parents=True)
    (folder / "Bad Name.json").write_text("{}", encoding="utf-8")
    (folder / "classic.json").write_text("{}", encoding="utf-8")
    (folder / "broken.json").write_text("{oops", encoding="utf-8")
    listing = store.presets()
    names = [p["name"] for p in listing["presets"]]
    assert names == ["stokes", "classic", "ribbon", "workflow", "minimal", "broken"]
    assert len(listing["warnings"]) == 2
    broken = listing["presets"][-1]
    assert broken["sections"] == []
    assert broken["warnings"][0]["code"] == "workspace.unreadable"
    with pytest.raises(WorkspaceInvalid):
        store.apply_preset("broken", "user", None)


# ---------------------------------------------------------------------- the project
def test_save_as_carries_the_project_overrides(
    tmp_path: Path, project: Path, events: list[tuple[str, dict[str, Any]]]
) -> None:
    store = WorkspaceStore(
        notify=lambda event, data: events.append((event, data)), project_dir=project
    )
    store.set("layout.left.size", 360, "project", project)
    events.clear()
    copy = tmp_path / "copy.stokes"
    copy.mkdir()
    store.project_changed(copy, saved=True)
    assert read(project_file(copy)) == read(project_file(project))
    assert events == [("workspace.changed", {"scope": "project", "path": None})]
    events.clear()
    store.project_changed(None)
    assert events == [("workspace.changed", {"scope": "project", "path": None})]
    events.clear()
    other = tmp_path / "other.stokes"
    other.mkdir()
    store.project_changed(other)
    assert events == []
    assert not project_file(other).exists()


def test_an_EMPTY_OPEN_SLOT_is_collapsed_on_load(store: WorkspaceStore, home: Path) -> None:
    """Claude's 2026-10-19 1230, the owner's "flat blue close-up".

    An older build wrote `right: {collapsed: false, panels: []}`. `Layout` draws nothing for a
    slot with no panels, but the viewport MEASURED the dock's 320 px, so the free rectangle was
    320 px narrower than what the user could see - and the first fit framed the model into that
    phantom space.

    **Normalised on load, so no reader has to special-case it.** The document says the slot is
    open and holds nothing; after one read it says collapsed, and the notice says so rather than
    changing a document silently.
    """
    write(
        user_file(home),
        {
            "layout": {
                "right": {"mode": "tabs", "size": 320, "collapsed": False, "panels": []},
                "left": {"panels": [{"id": "tree"}]},
            }
        },
    )
    result = store.effective(None)
    assert result.document["layout"]["right"]["collapsed"] is True
    assert [f.code for f in result.findings if f.code == "workspace.empty_slot"] == [
        "workspace.empty_slot"
    ]
    # **A slot that HOLDS something is left alone**, or this rule would fold a dock the user opened.
    write(
        user_file(home),
        {"layout": {"right": {"collapsed": False, "panels": [{"id": "properties"}]}}},
    )
    assert store.effective(None).document["layout"]["right"]["collapsed"] is False
