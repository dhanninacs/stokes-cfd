"""workspace.* through Session.execute: schemas, events, the undo stack, journals, project scope."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Journal, Session, default_registry, replay
from stokes.errors import NoProject, StokesError, ValidationError
from stokes.workspace import WorkspaceStore
from stokes.workspace.defaults import default_document
from stokes.workspace.errors import UnknownPreset, WorkspacePathError

COMMANDS = {
    "workspace.get": False,
    "workspace.set": True,
    "workspace.reset": True,
    "workspace.presets": False,
    "workspace.apply_preset": True,
    "workspace.save_preset": True,
    "workspace.export": False,
    "workspace.import": True,
}


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every workspace test runs against a private Stokes home, never the real one."""
    path = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(path))
    return path


def changes(session: Session) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    session.events.subscribe("workspace.changed", lambda event, data: seen.append(data))
    return seen


def test_commands_are_registered_with_their_schemas() -> None:
    schema = {c["name"]: c for c in default_registry().json_schema()}
    assert {name: schema[name]["mutates"] for name in COMMANDS} == COMMANDS
    assert all(schema[name]["requires_project"] is False for name in COMMANDS)
    assert schema["workspace.set"]["input_schema"]["required"] == ["path", "value"]
    assert set(schema["workspace.set"]["input_schema"]["properties"]) == {"path", "value", "scope"}
    assert schema["workspace.reset"]["input_schema"]["required"] == ["scope"]
    assert schema["workspace.import"]["input_schema"]["required"] == ["text"]
    assert (
        schema["workspace.export"]["input_schema"]["properties"]["scope"]["default"] == "effective"
    )
    assert all(schema[name]["description"] for name in COMMANDS)


def test_get_works_without_a_project() -> None:
    session = Session()
    got = session.execute("workspace.get")
    assert got["document"] == default_document()
    assert got["files"]["project"] is None
    assert got["files"]["user"]["exists"] is False
    assert got["warnings"] == []
    assert session.execute("workspace.get", path="shortcuts.palette.open")["value"] == "Ctrl+K"
    assert isinstance(session.extensions["workspace"], WorkspaceStore)


def test_project_scope_needs_an_OPEN_project(tmp_path: Path) -> None:
    """**Rewritten for the new contract** (claude-win2, reading 1). This asserted two refusals: no
    open project, and then an open project **with no folder** (`workspace.no_project_folder`).

    **The second refusal is unreachable now** - a project has a folder from `project.new` - so the
    test asserts the part that still means something: a project-scoped override needs a project
    and once there is one it is written to that project's own `workspace.json`. **The first refusal
    is kept**, because asking for project scope with no project is still a mistake in the call.
    """
    session = Session()
    with pytest.raises(NoProject):
        session.execute("workspace.set", path="viewport.grid", value=False, scope="project")
    # **A new project is enough** - no `project.save` first, which is the change: the folder it was
    # saved into is the one it was created in.
    session.execute("project.new", name="draft", path=str(tmp_path / "draft"))
    reply = session.execute("workspace.set", path="viewport.grid", value=False, scope="project")
    assert reply["source"] == "project"
    assert (tmp_path / "draft.stokes" / "workspace.json").is_file()


def test_every_write_emits_workspace_changed_and_none_is_undoable(session: Session) -> None:
    seen = changes(session)
    session.execute("workspace.set", path="viewport.grid", value=False)
    session.execute("workspace.set", path="layout.left.size", value=280, scope="project")
    session.execute("workspace.apply_preset", name="ribbon")
    session.execute("workspace.import", text='{"version": 1, "viewport": {"viewMode": "edges"}}')
    session.execute("workspace.reset", scope="project")
    session.execute("workspace.save_preset", name="mine")
    session.execute("workspace.get")
    session.execute("workspace.presets")
    session.execute("workspace.export")
    assert seen == [
        {"scope": "user", "path": "viewport.grid"},
        {"scope": "project", "path": "layout.left.size"},
        {"scope": "user", "path": None},
        {"scope": "user", "path": None},
        {"scope": "project", "path": None},
        {"scope": "presets", "path": "mine"},
    ]
    assert session.execute("history.state")["can_undo"] is False
    session.execute("history.undo")
    document = session.execute("workspace.get")["document"]
    # The import replaced the whole viewport section, so the earlier grid override is gone.
    assert document["viewport"]["grid"] is True
    assert document["viewport"]["viewMode"] == "edges"
    assert document["topbar"]["stages"] == {"style": "ribbon"}


def test_workspace_writes_leave_model_history_alone(velocity_parameter: Session) -> None:
    session = velocity_parameter
    before = session.execute("history.state")
    session.execute("workspace.set", path="theme", value={"name": "light"})
    assert session.execute("history.state") == before
    session.execute("history.undo")
    assert session.execute("workspace.get", path="theme")["value"] == {"name": "light"}


def test_journal_records_replay_into_a_fresh_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "first"))
    journal = tmp_path / "journal.jsonl"
    session = Session(journal_path=journal)
    session.execute(
        "workspace.set", path="theme", value={"base": "dark", "tokens": {"--accent": "#ff8800"}}
    )
    session.execute("workspace.set", path="shortcuts.view.fit", value="g")
    session.execute("workspace.apply_preset", name="classic")
    session.execute("workspace.reset", scope="user", path="tree")
    session.execute(
        "workspace.import", text=json.dumps({"version": 1, "viewport": {"grid": False}})
    )
    session.execute("workspace.get")
    session.execute("workspace.export")
    expected = session.execute("workspace.get")["document"]
    records = Journal.read(journal)
    assert [r["command"] for r in records] == [
        "workspace.set",
        "workspace.set",
        "workspace.apply_preset",
        "workspace.reset",
        "workspace.import",
    ]
    assert records[0]["params"]["scope"] == "user"
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "second"))
    fresh = Session()
    replay(fresh, records)
    assert fresh.execute("workspace.get")["document"] == expected
    assert (tmp_path / "second" / "workspace.json").is_file()


def test_save_as_and_close_follow_the_project(tmp_path: Path) -> None:
    session = Session()
    session.execute("project.new", name="demo", path=str(tmp_path / "a"))
    session.execute("workspace.set", path="layout.left.size", value=360, scope="project")
    seen = changes(session)
    session.execute("project.save", path=str(tmp_path / "b"))
    assert (tmp_path / "b.stokes" / "workspace.json").is_file()
    got = session.execute("workspace.get", path="layout.left.size")
    assert (got["value"], got["source"]) == (360, "project")
    session.execute("project.close")
    assert session.execute("workspace.get", path="layout.left.size")["source"] == "default"
    assert seen == [{"scope": "project", "path": None}, {"scope": "project", "path": None}]
    session.execute("project.open", path=str(tmp_path / "a"))
    assert session.execute("workspace.get", path="layout.left.size")["value"] == 360


def test_errors_carry_stable_codes() -> None:
    session = Session()
    with pytest.raises(WorkspacePathError) as path_error:
        session.execute("workspace.set", path="layout.left.colour", value="red")
    assert path_error.value.code == "workspace.path"
    with pytest.raises(UnknownPreset):
        session.execute("workspace.apply_preset", name="nope")
    with pytest.raises(ValidationError) as missing:
        session.execute("workspace.set", path="viewport.grid")
    assert missing.value.code == "validation"
    with pytest.raises(ValidationError) as bad_scope:
        session.execute("workspace.set", path="viewport.grid", value=False, scope="team")
    assert bad_scope.value.code == "workspace.invalid"
    exported = session.execute("workspace.export", sections=["theme", "viewport"])
    assert exported["sections"] == ["theme", "viewport"]
    assert json.loads(exported["text"])["viewport"]["grid"] is True


# --------------------------------------------------------------- preferences (T-176, user-only)
def test_a_project_may_not_carry_the_users_preferences(session: Session, tmp_path: Path) -> None:
    """**T-176 correction 1: preferences are the user's, not the project's.**

    A project file is shared. If it could carry a mouse scheme it would decide, silently, how the
    interface behaves for whoever opens it - a setting chosen by someone else, arriving in a
    file they were sent.

    **Reported at the VALUE, not the section.** The first version of this rule raised its finding at
    `("preferences",)`, which is an ANCESTOR of the path being written; `_check` keeps a
    finding only when `is_within(finding.path, root)`, so it was filtered out and **the write
    was accepted**.
    Measured: `preferences.mouse.scheme = "paraview"` at project scope wrote without complaint.
    """
    session.execute("project.new", path=str(tmp_path / "p.stokes"), name="p")
    with pytest.raises(ValidationError) as caught:
        session.execute(
            "workspace.set", path="preferences.mouse.scheme", value="paraview", scope="project"
        )
    assert "user's" in str(caught.value)

    # The same write is fine for the user, which is who owns it.
    reply = session.execute(
        "workspace.set", path="preferences.mouse.scheme", value="paraview", scope="user"
    )
    assert reply["value"] == "paraview"
    assert reply["source"] == "user"


def test_a_preferences_section_in_a_project_file_is_dropped(
    session: Session, tmp_path: Path
) -> None:
    """A project file on disk with a `preferences` section must not take effect.

    Dropped with `workspace.user_only` rather than refused: the rest of the document is still
    good, and the reader deserves to know what was ignored.
    """
    from stokes.workspace.schema import sanitise

    clean, findings = sanitise({"preferences": {"mouse": {"scheme": "blender"}}}, "project")
    assert "preferences" not in clean
    codes = [f.code for f in findings]
    assert "workspace.user_only" in codes, codes
    assert any("user's, not the project's" in f.message for f in findings)

    # The same document IS kept for the user, which is the whole point of the distinction.
    kept, _ = sanitise({"preferences": {"mouse": {"scheme": "blender"}}}, "user")
    assert kept["preferences"]["mouse"]["scheme"] == "blender"


def test_a_preset_never_carries_the_preferences(session: Session, tmp_path: Path) -> None:
    """**T-176 correction 2.** `save_preset` snapshots the EFFECTIVE document, which contains
    `preferences`, so without dropping it a preset would capture the saver's mouse scheme and
    impose it on whoever applied it.

    The round trip is the point: set the scheme, save a preset, set it back, apply the preset,
    and the scheme must be the one the user chose - not the one baked into the preset.
    """
    session.execute("project.new", path=str(tmp_path / "p.stokes"), name="p")
    session.execute("workspace.set", path="preferences.mouse.scheme", value="blender", scope="user")
    session.execute("workspace.save_preset", name="mine", title="Mine", description="d")

    found = sorted(tmp_path.rglob("mine.json"))
    assert found, "the preset file was not written"
    content = json.loads(found[0].read_text())
    assert "preferences" not in content, (
        f"the preset carried the user's settings: {sorted(content)}"
    )

    session.execute("workspace.set", path="preferences.mouse.scheme", value="cad", scope="user")
    session.execute("workspace.apply_preset", name="mine")
    document = session.execute("workspace.get")["document"]
    assert document["preferences"]["mouse"]["scheme"] == "cad", (
        "applying a preset changed the user's mouse scheme"
    )


def test_a_bad_mouse_scheme_is_refused_and_a_good_one_round_trips(session: Session) -> None:
    """**The false-refusal half.** The four schemes the front end actually implements must be
    accepted - measured from `web/src/app/navModes.test.ts`, which asserts the same list - and
    anything else refused."""
    session.execute("project.new", name="p")
    for scheme in ("cad", "paraview", "blender", "toggle"):
        reply = session.execute(
            "workspace.set", path="preferences.mouse.scheme", value=scheme, scope="user"
        )
        assert reply["value"] == scheme
    with pytest.raises(ValidationError) as caught:
        session.execute(
            "workspace.set", path="preferences.mouse.scheme", value="trackball", scope="user"
        )
    assert "must be one of" in str(caught.value)

    # And the import unit, the section's other consumer.
    for unit in ("mm", "cm", "m", "in", "ft"):
        session.execute("workspace.set", path="preferences.import.units", value=unit, scope="user")
    document = session.execute("workspace.get")["document"]
    assert document["preferences"]["import"]["units"] == "ft"
    with pytest.raises(ValidationError):
        session.execute(
            "workspace.set", path="preferences.import.units", value="furlong", scope="user"
        )


# --------------------------------------------------- the action defaults (T-176 §2)
def test_the_context_menu_actions_do_not_clash_with_the_tree(
    session: Session,
) -> None:
    """**T-176 §3: only two actions of the SAME CONTEXT conflict.**

    `tree.menu` (context `tree`) and `context.menu` (context `global`) share `Shift+F10` and
    `ContextMenu` **by design** - the tree consumes the chord while it has focus and the global
    action takes it otherwise, which is what a context is for. The rule reported every pair, so the
    DEFAULT document carried four warnings (measured) and a reader would learn to ignore them.

    A genuine same-context clash must still be reported, which is the second half.
    """
    from stokes.workspace.defaults import default_document
    from stokes.workspace.merge import effective

    clean = [
        f
        for f in effective([("user", default_document())]).findings
        if f.code == "workspace.shortcut_conflict"
    ]
    assert clean == [], f"the default document reports conflicts it does not have: {clean}"

    # A real clash: two GLOBAL actions on one chord.
    document = default_document()
    document["shortcuts"]["view.fit"] = "O"  # view.projection already holds O
    found = [
        f
        for f in effective([("user", document)]).findings
        if f.code == "workspace.shortcut_conflict"
    ]
    assert {f.path[-1] for f in found} == {"view.fit", "view.projection"}, found


def test_app_settings_and_display_settings_both_keep_their_chord(session: Session) -> None:
    """**The OWNER DECISION, not the plan.** The plan wanted Display settings stripped of `Ctrl+,`
    and a `workspace.migrated` notice emitted; the owner kept it and gave Settings
    `Ctrl+Shift+,`. Both bindings therefore exist, neither is a conflict, and no migration notice is
    emitted - which is asserted here so a later reader does not "fix" it back.
    """
    from stokes.workspace.defaults import ACTION_IDS, default_document
    from stokes.workspace.merge import effective

    assert ACTION_IDS["display.settings"].keys == ("Ctrl+,",)
    assert ACTION_IDS["app.settings"].keys == ("Ctrl+Shift+,",)
    shortcuts = default_document()["shortcuts"]
    assert shortcuts["display.settings"] == "Ctrl+,"
    assert shortcuts["app.settings"] == "Ctrl+Shift+,"

    # No migration notice: the decision removed the need for one.
    codes = {f.code for f in effective([("user", default_document())]).findings}
    assert "workspace.migrated" not in codes, codes


def test_every_context_menu_action_has_a_context_and_a_title(session: Session) -> None:
    """The new ids are what the context menu (T-172) installs handlers for, so each must carry the
    context that decides whether it is a clash and the label a menu row shows."""
    from stokes.workspace.defaults import ACTION_IDS

    for name in (
        "app.settings",
        "selection.clearPick",
        "context.menu",
        "view.fitSelection",
        "view.showAll",
        "view.from.iso",
        "selection.siblings",
        "selection.allFaces",
        "edit.rename",
        "edit.delete",
        "edit.copyName",
        "edit.copyPath",
    ):
        action = ACTION_IDS[name]
        assert action.context in ("global", "tree"), (name, action.context)
        assert action.label, f"{name} has no label for a menu row"


def test_the_stepper_preference_is_shown_or_hidden_and_nothing_else(session: Session) -> None:
    """`preferences.mesh.stepper` (ASK Q2, brief M-502): the value is validated SERVER-side, so a
    web-only spelling of it would be refused on write — which is the point of putting it in the
    schema rather than in the front end alone."""
    session.execute("workspace.set", path="preferences.mesh.stepper", value="hidden", scope="user")
    got = session.execute("workspace.get", path="preferences.mesh.stepper")
    assert got["value"] == "hidden"
    session.execute("workspace.set", path="preferences.mesh.stepper", value="shown", scope="user")
    assert session.execute("workspace.get", path="preferences.mesh.stepper")["value"] == "shown"
    with pytest.raises(StokesError, match="stepper"):
        session.execute(
            "workspace.set", path="preferences.mesh.stepper", value="collapsed", scope="user"
        )
    # And the shipped default is shown, so a user who never touches it sees the stepper.
    fresh = Session()
    fresh.execute("project.new", name="fresh")
    assert fresh.execute("workspace.get", path="preferences.mesh.stepper")["value"] == "shown"
