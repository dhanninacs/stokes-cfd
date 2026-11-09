"""The workspace document: defaults, shipped presets, sanitising, themes, chords and paths."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from stokes.workspace import catalog
from stokes.workspace.defaults import ACTIONS, ISLANDS, PANELS, TOPBAR_ITEMS, default_document
from stokes.workspace.errors import WorkspacePathError
from stokes.workspace.keys import normalise_binding, normalise_chord
from stokes.workspace.merge import effective
from stokes.workspace.paths import format_path, parse
from stokes.workspace.rules import Finding
from stokes.workspace.schema import (
    ARRANGEMENT,
    CORNERS,
    SECTIONS,
    SLOTS,
    sanitise,
    validate_complete,
)

NOWHERE = Path("unused-home")
CONTRACT = Path(__file__).resolve().parent.parent / "docs" / "engineering" / "workspace-schema.md"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every workspace test runs against a private Stokes home, never the real one."""
    path = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(path))
    return path


def found(findings: Iterable[Finding]) -> set[tuple[str, str]]:
    return {(f.code, format_path(f.path)) for f in findings}


def clean_of(raw: dict[str, Any]) -> tuple[dict[str, Any], list[Finding]]:
    return sanitise({"version": 1, **raw}, "user")


# ---------------------------------------------------------------------- defaults and presets
def test_default_document_is_complete_and_valid() -> None:
    document = default_document()
    assert document["version"] == 1
    assert list(document)[1:] == list(SECTIONS)
    assert validate_complete(document) == []
    assert document["theme"] == {"name": "auto"}
    assert document["shortcuts"]["workspace.customise"] == "Ctrl+Shift+C"
    assert document["shortcuts"]["history.redo"] == ["Ctrl+Y", "Ctrl+Shift+Z"]
    assert document["shortcuts"]["stage.3"] == "3"
    assert document["viewport"]["lowDetail"] is False
    # `/monitors` was dropped from the skeleton on 2026-10-18 (nothing read it), so the stage
    # map no longer names it.
    assert document["tree"]["stages"]["Solve"] == ["/solver", "/runs"]
    for action in ACTIONS:
        assert all(normalise_chord(key) == key for key in action.keys), action.id
    assert default_document() is not default_document()


def test_validate_complete_reports_missing_keys() -> None:
    document = default_document()
    del document["viewport"]["grid"]
    assert ("workspace.invalid_value", "viewport") in found(validate_complete(document))


def test_viewport_low_detail_is_optional_for_older_workspace_files() -> None:
    document = default_document()
    del document["viewport"]["lowDetail"]
    clean, findings = clean_of(document)
    assert not any(f.code == "workspace.invalid_value" for f in findings)
    assert "lowDetail" not in clean["viewport"]
    assert validate_complete(clean) == []


@pytest.mark.parametrize("name", catalog.SHIPPED)
def test_every_shipped_preset_validates(name: str) -> None:
    preset = catalog.find(name, NOWHERE)
    assert preset.findings == ()
    assert preset.source == "shipped"
    assert list(preset.document) == list(ARRANGEMENT)
    assert preset.title
    assert preset.description
    assert validate_complete({**default_document(), **preset.document}) == []


@pytest.mark.parametrize("name", catalog.SHIPPED)
def test_every_preset_places_each_builtin_item_exactly_once(name: str) -> None:
    document = catalog.find(name, NOWHERE).document
    layout, islands = document["layout"], document["islands"]
    places = [p["id"] for slot in SLOTS for p in layout[slot]["panels"]] + layout["hidden"]
    assert sorted(places) == sorted(PANELS)
    corners = [i for corner in (*CORNERS, "hidden") for i in islands[corner]]
    assert sorted(corners) == sorted(ISLANDS)
    assert [item["id"] for item in document["topbar"]["items"]] == list(TOPBAR_ITEMS)


def test_the_stokes_preset_is_the_default_arrangement() -> None:
    defaults = default_document()
    assert catalog.find("stokes", NOWHERE).document == {s: defaults[s] for s in ARRANGEMENT}


def test_presets_differ_as_documented() -> None:
    presets = {name: catalog.find(name, NOWHERE).document for name in catalog.SHIPPED}

    def ids(preset: str, slot: str) -> list[str]:
        return [p["id"] for p in presets[preset]["layout"][slot]["panels"]]

    # **Every preset that PLACES a right-slot panel also places `mesh`, `solve` and `results`**
    # (2026-10-15). Adding a built-in panel changes every arrangement that lists one, because
    # `test_every_preset_places_each_builtin_item_exactly_once` requires each to be placed or
    # hidden -
    # and a preset that placed `plots` but not `results` would have the latter silently restored,
    # which is a worse description of the preset than saying where it goes.
    new_right = ["mesh", "solve", "results"]

    classic = presets["classic"]
    assert classic["layout"]["left"]["mode"] == "split"
    # **`details` joins the classic left dock** (the layout decision 2026-10-18): the pane is a
    # built-in now, so every preset has to place it.
    assert ids("classic", "left") == ["tree", "properties", "conditions", "details"]
    # `classic`'s right slot keeps its own three and gains the new ones.
    assert ids("classic", "right") == ["output", "design-table", "plots", *new_right]
    assert classic["topbar"]["stages"]["style"] == "chips"
    assert presets["ribbon"]["topbar"]["stages"]["style"] == "ribbon"
    assert (ids("ribbon", "left"), ids("ribbon", "right")) == (
        # `details` is placed in every preset: the schema wants each built-in exactly once, and a
        # preset that forgot it would be a document the server refuses.
        ["tree", "details"],
        ["properties", "mesh", "conditions", "solve", "results", "plots"],
    )
    workflow = presets["workflow"]
    assert workflow["topbar"]["stages"]["style"] == "hidden"
    assert workflow["tree"]["linkStage"] is False
    assert (ids("workflow", "left"), ids("workflow", "right")) == (
        ["tree", "details"],
        ["properties", "mesh", "conditions", "solve", "results", "plots"],
    )
    minimal = presets["minimal"]
    # **`minimal` HIDES them rather than placing them**: a preset whose whole character is "few
    # panels showing" answers a new panel by hiding it, which is what it already does with `tree`.
    # **`details` hidden with them** (the layout decision 2026-10-18): the pane is a built-in, and
    # a preset whose whole character is "few panels showing" answers it the way it answers the
    # tree - by hiding it rather than placing it somewhere the preset did not choose.
    assert minimal["layout"]["hidden"] == ["tree", "details", *new_right]
    assert ids("minimal", "float") == ["properties", "conditions"]
    assert ids("minimal", "bottom") == ["output", "design-table", "plots"]
    assert minimal["layout"]["bottom"]["collapsed"] is True
    shown = [item["id"] for item in minimal["topbar"]["items"] if item["visible"]]
    assert shown == ["logo", "project", "commands", "run", "window"]


# ---------------------------------------------------------------------- sanitising
def test_unknown_keys_are_kept_and_reported() -> None:
    clean, findings = clean_of(
        {"layout": {"left": {"size": 280, "glow": True}}, "plugins": {"probe": [1, 2]}}
    )
    assert clean["layout"]["left"] == {"size": 280, "glow": True}
    assert clean["plugins"] == {"probe": [1, 2]}
    assert found(findings) == {
        ("workspace.unknown_key", "layout.left.glow"),
        ("workspace.unknown_key", "plugins"),
    }
    assert {f.kind for f in findings} == {"unknown"}
    assert {f.scope for f in findings} == {"user"}


def test_invalid_values_are_dropped_with_warnings() -> None:
    clean, findings = clean_of(
        {
            "theme": {"name": "neon"},
            "layout": {
                "left": {
                    "mode": "grid",
                    "size": -5,
                    "collapsed": "yes",
                    "panels": [{"id": "tree"}, {"id": 7}, "properties"],
                }
            },
            "viewport": {"projection": "fisheye", "grid": False, "background": "#ABC"},
            "tree": {"defaultExpanded": 99, "hidden": ["runs", "runs", "/scenes"]},
            "shortcuts": {"view.fit": "Ctrl+Hyper+F", "Bad Id": "F", "view.projection": "p"},
            "engines": {"default": "Open FOAM", "runPresets": {"fast": {"cores": 4}, "9x": {}}},
        }
    )
    assert "theme" not in clean
    assert clean["layout"]["left"] == {"panels": [{"id": "tree", "order": 0}]}
    assert clean["viewport"] == {"grid": False, "background": "#aabbcc"}
    assert clean["tree"] == {"hidden": ["runs"]}
    assert clean["shortcuts"] == {"view.projection": "P"}
    assert clean["engines"] == {"runPresets": {"fast": {"cores": 4}}}
    codes = found(findings)
    for path in (
        "theme.name",
        "layout.left.mode",
        "layout.left.size",
        "layout.left.collapsed",
        "layout.left.panels.1.id",
        "layout.left.panels.2",
        "viewport.projection",
        "tree.defaultExpanded",
        "tree.hidden.2",
        'shortcuts["view.fit"]',
        'shortcuts["Bad Id"]',
        "engines.default",
        "engines.runPresets.9x",
    ):
        assert ("workspace.invalid_value", path) in codes, path
    assert ("workspace.duplicate", "tree.hidden.1") in codes


def test_list_entries_are_completed_from_their_position() -> None:
    clean, findings = clean_of(
        {
            "layout": {"float": {"panels": [{"id": "properties"}, {"id": "tree", "x": 300}]}},
            "topbar": {"items": [{"id": "run"}]},
        }
    )
    assert findings == []
    assert clean["layout"]["float"]["panels"] == [
        {"id": "properties", "order": 0, "anchor": "tl", "x": 24, "y": 24},
        {"id": "tree", "x": 300, "order": 1, "anchor": "tl", "y": 56},
    ]
    assert clean["topbar"]["items"] == [{"id": "run", "visible": True}]


def test_float_only_keys_are_unknown_in_docked_slots() -> None:
    clean, findings = clean_of({"layout": {"left": {"panels": [{"id": "tree", "x": 3}]}}})
    assert clean["layout"]["left"]["panels"] == [{"id": "tree", "x": 3, "order": 0}]
    assert found(findings) == {("workspace.unknown_key", "layout.left.panels.0.x")}


def test_version_handling() -> None:
    _, missing = sanitise({}, "user")
    assert [(f.code, f.kind) for f in missing] == [("workspace.version", "notice")]
    _, newer = sanitise({"version": 7}, "user")
    assert [(f.code, f.kind) for f in newer] == [("workspace.version", "notice")]
    _, wrong = sanitise({"version": "1"}, "user")
    assert [(f.code, f.kind) for f in wrong] == [("workspace.version", "invalid")]
    clean, not_object = sanitise([1, 2], "project")
    assert clean == {}
    assert [(f.kind, f.scope, f.path) for f in not_object] == [("invalid", "project", ())]


# ---------------------------------------------------------------------- themes
def test_builtin_and_custom_themes() -> None:
    for name in (
        "dark",
        "light",
        "cupertino",
        "cupertino-dark",
        "ubuntu",
        "ubuntu-dark",
        "auto",
        "cupertino-auto",
        "ubuntu-auto",
    ):
        clean, findings = clean_of({"theme": {"name": name}})
        assert (clean["theme"], findings) == ({"name": name}, [])
    clean, findings = clean_of(
        {
            "theme": {
                "base": "light",
                "title": "Paper",
                "tokens": {
                    "--accent": "#0A7",
                    "--radius": "6px",
                    "--font-sans": '"Inter Variable", sans-serif',
                    "--shadow": "0 1px 2px rgba(0, 0, 0, 0.2), inset 0 0 0 1px var(--panel-border)",
                },
            }
        }
    )
    assert findings == []
    assert clean["theme"]["title"] == "Paper"
    assert clean["theme"]["tokens"]["--accent"] == "#00aa77"
    assert clean_of({"theme": {"base": "dark"}})[0]["theme"] == {"base": "dark", "tokens": {}}


def test_custom_theme_rejects_bad_token_names_and_values() -> None:
    clean, findings = clean_of(
        {
            "theme": {
                "base": "dark",
                "tokens": {
                    "--acent": "#ff0000",
                    "--accent": "teal",
                    "--bg": "url(https://example.com/x.png)",
                    "--panel": "red; color: blue",
                    "--tree-width": "400px",
                    "--text": "#ffffff",
                },
            }
        }
    )
    assert clean["theme"] == {"base": "dark", "tokens": {"--text": "#ffffff"}}
    assert found(findings) == {
        ("workspace.invalid_value", f"theme.tokens.{name}")
        for name in ("--acent", "--accent", "--bg", "--panel", "--tree-width")
    }
    messages = {format_path(f.path): f.message for f in findings}
    assert "not a theme token" in messages["theme.tokens.--acent"]
    assert "#rrggbb" in messages["theme.tokens.--accent"]
    assert "run time" in messages["theme.tokens.--tree-width"]


@pytest.mark.parametrize(
    ("theme", "path", "kept"),
    [
        ({"name": "dark", "tokens": {"--accent": "#fff"}}, "theme.tokens", {"name": "dark"}),
        ({"name": "dark", "base": "light"}, "theme.name", {"base": "light", "tokens": {}}),
        ({"base": "solarized"}, "theme.base", None),
        ({"title": "Nameless"}, "theme", None),
        ("dark", "theme", None),
    ],
)
def test_theme_shape_errors(theme: Any, path: str, kept: dict[str, Any] | None) -> None:
    clean, findings = clean_of({"theme": theme})
    assert clean.get("theme") == kept
    assert ("workspace.invalid_value", path) in found(findings)


def test_cupertino_tokens_on_another_base_are_noted_not_dropped() -> None:
    clean, findings = clean_of({"theme": {"base": "dark", "tokens": {"--cup-field-bg": "#101010"}}})
    assert clean["theme"]["tokens"] == {"--cup-field-bg": "#101010"}
    assert [(f.code, f.kind) for f in findings] == [("workspace.no_effect", "notice")]
    _, quiet = clean_of({"theme": {"base": "cupertino", "tokens": {"--cup-field-bg": "#101010"}}})
    assert quiet == []


# ---------------------------------------------------------------------- chords
@pytest.mark.parametrize(
    ("text", "chord"),
    [
        ("ctrl+k", "Ctrl+K"),
        ("Ctrl Shift Z", "Ctrl+Shift+Z"),
        ("shift+ctrl+z", "Ctrl+Shift+Z"),
        ("cmd+,", "Ctrl+,"),
        ("Ctrl++", "Ctrl+Plus"),
        ("+", "Plus"),
        ("esc", "Escape"),
        ("del", "Delete"),
        ("f10", "F10"),
        ("Shift+F10", "Shift+F10"),
        ("1", "1"),
        ("option+up", "Alt+ArrowUp"),
        ("win+e", "Meta+E"),
        ("Ctrl+-", "Ctrl+-"),
        ("  ", ""),
    ],
)
def test_chords_normalise(text: str, chord: str) -> None:
    assert normalise_chord(text) == chord


@pytest.mark.parametrize(
    "text", ["Ctrl", "Ctrl+Ctrl+K", "Hyper+K", "Ctrl+é", "F25", "Ctrl+KK", "Ctrl+²"]
)
def test_bad_chords_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match=r"."):
        normalise_chord(text)


def test_bindings() -> None:
    assert normalise_binding(["ctrl+y", "Ctrl+Y", "ctrl shift z"]) == ["Ctrl+Y", "Ctrl+Shift+Z"]
    assert normalise_binding([]) == []
    assert normalise_binding("") == ""
    for bad in (["Ctrl+Y", ""], ["F1", "F2", "F3", "F4", "F5"], [3], 3, None):
        with pytest.raises(ValueError, match=r"."):
            normalise_binding(bad)


# ---------------------------------------------------------------------- paths
@pytest.mark.parametrize(
    ("text", "segments"),
    [
        ("layout.left.size", ("layout", "left", "size")),
        ("layout.left.panels.0.size", ("layout", "left", "panels", "0", "size")),
        ('shortcuts["view.fit"]', ("shortcuts", "view.fit")),
        ('shortcuts["history.redo"][1]', ("shortcuts", "history.redo", 1)),
        ("islands.br[1]", ("islands", "br", 1)),
        ("theme.tokens.--accent", ("theme", "tokens", "--accent")),
        ('["odd key"].x', ("odd key", "x")),
    ],
)
def test_paths_parse(text: str, segments: tuple[Any, ...]) -> None:
    assert parse(text) == segments


def test_canonical_paths_parse_back_to_the_same_segments() -> None:
    # A dotted index parses as a digit string; resolving it against the list makes it an index.
    for loc, parsed in (
        (("layout", "left", "panels", 0, "size"), ("layout", "left", "panels", "0", "size")),
        (("shortcuts", "view.fit"), ("shortcuts", "view.fit")),
        (("shortcuts", "history.redo", 1), ("shortcuts", "history.redo", "1")),
        (("theme", "tokens", "--accent"), ("theme", "tokens", "--accent")),
        (("tree", "stages", "12"), ("tree", "stages", "12")),
    ):
        assert parse(format_path(loc)) == parsed
    assert format_path(("tree", "stages", "12")) == 'tree.stages["12"]'
    assert format_path(("shortcuts", "view.fit")) == 'shortcuts["view.fit"]'
    assert format_path(("layout", "left", "panels", 0)) == "layout.left.panels.0"
    assert format_path(()) == ""


@pytest.mark.parametrize(
    "text", ["", " ", "a..b", ".a", "a.", "a[", "a[x]", 'a["b"', "a]b", "a[0]b", "x" * 600]
)
def test_bad_paths_are_refused(text: str) -> None:
    with pytest.raises(WorkspacePathError) as caught:
        parse(text)
    assert caught.value.code == "workspace.path"


# ---------------------------------------------------------------------- the contract document
def contract_examples() -> list[Any]:
    """The JSON blocks of the example section of docs/engineering/workspace-schema.md."""
    text = CONTRACT.read_text(encoding="utf-8").split("## Example: the default document", 1)[1]
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, re.DOTALL)]


def test_the_documented_default_is_the_default() -> None:
    assert contract_examples()[0] == default_document()


def test_the_documented_user_example_is_valid() -> None:
    example = contract_examples()[1]
    clean, findings = sanitise(example, "user")
    assert findings == []
    result = effective([("user", clean)])
    layout = result.document["layout"]
    # **The example's own placements stand, and what it does not mention stays HIDDEN** - the
    # default arrangement keeps the stage panels that way (the owner's "remove the right strip"), so
    # a
    # layer that says nothing about `mesh` must not put it back into the right slot. This assertion
    # used to expect exactly that restore, and would have re-created the rail's panels the moment a
    # user document did not mention one.
    assert [p["id"] for p in layout["right"]["panels"]] == ["properties", "output", "plots"]
    assert layout["bottom"]["panels"] == []
    unmentioned = [
        p
        for p, slot in PANELS.items()
        if slot == "right" and p not in ("properties", "output", "plots")
    ]
    assert set(unmentioned) | {"design-table"} == set(layout["hidden"]), layout["hidden"]
    # **One restored notice per unmentioned panel**, whichever place it went back to.
    assert [f.code for f in result.findings] == ["workspace.restored"] * len(unmentioned)
    # **The example moved off `G`** when `selection.grow` took it (package two): the schema
    # refuses a chord two actions share, so the example had to move rather than the rule relax.
    assert result.document["shortcuts"]["view.fit"] == "Ctrl+Shift+F"


def test_the_contract_document_names_every_action_and_command() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    absent = [action.id for action in ACTIONS if f"`{action.id}`" not in text]
    assert not absent, f"workspace-schema.md does not list {absent}"
    for command in ("get", "set", "reset", "presets", "apply_preset", "save_preset", "export"):
        assert f"`workspace.{command}`" in text
    assert "`workspace.import`" in text


def test_tree_layout_accepts_both_values_and_refuses_anything_else() -> None:
    """The outline key (G-305).

    **"outline" is the target and "groups" is today's tree**, kept so a user who arranged their own
    is not forced onto a new one by an upgrade. A value outside the two is **dropped, with a
    finding** rather than kept silently - the document works, and the layer says what it
    ignored.
    """
    from stokes.workspace.schema import sanitise

    for value in ("outline", "groups"):
        out, _findings = sanitise({"tree": {"layout": value}}, "project")
        assert out["tree"]["layout"] == value

    refused, findings = sanitise({"tree": {"layout": "nonsense"}}, "project")
    assert "layout" not in refused["tree"], "a bad value must be dropped, not stored"
    assert len(findings) == 2, "the refusal is reported, not silent"


def test_a_document_without_the_key_reads_as_the_outline() -> None:
    """**A new key, not a change of meaning**: a document saved before it existed has none, and the
    web reader treats absent as "outline" - which is what the target tree is."""
    from stokes.workspace.defaults import default_document

    assert default_document()["tree"]["layout"] == "outline"
